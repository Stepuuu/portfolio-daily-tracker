"""/api/setup: the first-run checklist, the model key (write-only), and the legacy holdings migration.

Every value here is fictional. The model provider is a local HTTP server that answers like an
OpenAI-compatible API; the key sentinel must never appear in a response, a log record, the URL or an
exception message.
"""
from contextlib import contextmanager
import http.server
import json
import logging
import socket
import stat
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import legacy_migration, llm_setup, setup_checks
from core.ledger import Ledger, StaleProposal, normalize, opening_events
from tests.fakes import FakeClock, ScriptedSource, fake_runtime, opening, quote

KEY = 'sk-test-' + 'SENTINEL' + '0123456789abcdef'
BAD_KEY = 'sk-test-' + 'WRONGKEY' + '0123456789abcdef'


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fresh portfolio directory and configuration path, full mode, scripted quotes, no keys in the environment."""
    from config import settings
    from core.market.runtime import set_market_runtime
    portfolio = tmp_path / 'portfolio'
    portfolio.mkdir()
    monkeypatch.setenv('PORTFOLIO_DIR', str(portfolio))
    monkeypatch.setenv('TRACKER_CONFIG_FILE', str(tmp_path / 'config.json'))
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', 'fake')
    for name in ('TRACKER_DEMO_MODE', 'TRACKER_LAB_ONLY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'TRACKER_ALERTS_ENABLED',
                 'TRACKER_CLOSE_SNAPSHOT', 'TRACKER_PREMARKET', 'TRACKER_PREMARKET_TIME', 'TRACKER_POSTMARKET',
                 'TRACKER_POSTMARKET_TIME', 'TRACKER_ALLOWED_HOSTS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, '_config', None)
    monkeypatch.setattr(legacy_migration, 'portfolio_file', lambda: tmp_path / 'data' / 'portfolio.json')
    set_market_runtime(None)
    yield tmp_path
    set_market_runtime(None)
    settings._config = None


@pytest.fixture
def client(env):
    import backend.main as main
    # No lifespan (no chat service, no background tasks); the real app with its middleware and host check.
    return TestClient(main.app, base_url='http://127.0.0.1')


def files(root: Path):
    return sorted(str(path.relative_to(root)) for path in root.rglob('*'))


def items(response):
    assert response.status_code == 200, response.text
    return {entry['id']: entry for entry in response.json()['items']}


def confirm_opening(directory, account='主账户', **kwargs):
    ledger = Ledger(Path(directory) / 'ledger.sqlite3')
    ledger.confirm(ledger.propose([{**opening(account, **kwargs), 'date': '2026-11-14'}])['id'])
    return ledger


# -- registration and the checklist ------------------------------------------------------

def test_registered_once():
    from backend import features
    assert [prefix for _, prefix, _ in features.routers()].count('/api/setup') == 1


def test_fresh_directory_lists_what_to_do_and_creates_nothing(client, env):
    before = files(env)
    response = client.get('/api/setup/status')
    found = items(response)
    body = response.json()
    assert list(found) == ['opening', 'legacy', 'fees', 'quotes', 'alerts', 'routines', 'notifications', 'llm']
    for entry in body['items']:
        assert set(entry) == {'id', 'label', 'state', 'detail', 'action'}
        assert entry['state'] in {'done', 'todo', 'optional', 'unavailable'}
        assert entry['detail']
    assert found['opening']['state'] == 'todo' and found['opening']['action']['href'] == '/setup?step=account'
    assert found['legacy']['state'] == 'done' and '没有发现' in found['legacy']['detail']
    assert found['fees']['state'] == 'todo'
    assert found['quotes']['state'] == 'done' and 'TRACKER_QUOTE_SOURCE=fake' in found['quotes']['detail']
    # The fixture app runs no background tasks: the monitor and the scheduler are not running.
    assert found['alerts']['state'] == 'todo' and found['alerts']['action']['command'] == 'make start'
    assert found['routines']['state'] == 'todo'
    assert '收盘快照 15:40 开' in found['routines']['detail'] and '盘前简报 08:45 开' in found['routines']['detail']
    assert found['routines']['action']['command'].split('\n') == [
        'TRACKER_CLOSE_SNAPSHOT=1', 'TRACKER_PREMARKET=1', 'TRACKER_PREMARKET_TIME=08:45', 'TRACKER_POSTMARKET=1',
        'TRACKER_POSTMARKET_TIME=15:45']
    assert found['notifications']['state'] == 'optional'
    assert found['llm']['state'] == 'optional' and '记账、行情和提醒不需要' in found['llm']['detail']
    assert body['setup_complete'] is False and body['dismissed'] is False and body['ledger_ready'] is False
    # Reads create no file: no ledger, no desk store, no configuration file.
    assert files(env) == before


def test_routine_switches_and_times_follow_the_environment(client, monkeypatch):
    monkeypatch.setenv('TRACKER_PREMARKET', '0')
    monkeypatch.setenv('TRACKER_POSTMARKET_TIME', '16:05')
    monkeypatch.setenv('TRACKER_CLOSE_SNAPSHOT', '0')
    detail = items(client.get('/api/setup/status'))['routines']['detail']
    assert '收盘快照 15:40 关' in detail and '盘前简报 08:45 关' in detail and '盘后简报 16:05 开' in detail
    monkeypatch.setenv('TRACKER_ALERTS_ENABLED', '0')
    alerts = items(client.get('/api/setup/status'))['alerts']
    assert alerts['state'] == 'todo' and alerts['action']['command'] == 'TRACKER_ALERTS_ENABLED=1'


def test_opening_and_fee_items_turn_done_and_reads_do_not_grow_the_stores(client, env):
    directory = env / 'portfolio'
    confirm_opening(directory, cash={'CNY': '1000'}, contributed_cny='1000')
    found = items(client.get('/api/setup/status'))
    assert found['opening']['state'] == 'done' and '主账户' in found['opening']['detail']
    assert found['fees']['state'] == 'todo' and '主账户' in found['fees']['detail']
    assert client.put('/api/accounts/profile', params={'account': '主账户'},
                      json={'a_share': {'commission_rate': '0.0001', 'commission_min': '5'}}).status_code == 200
    stores = [directory / 'ledger.sqlite3', directory / 'desk.sqlite3']
    sizes = [(path.stat().st_size, path.stat().st_mtime_ns) for path in stores]
    listing = files(env)
    for _ in range(3):
        found = items(client.get('/api/setup/status'))
        client.get('/api/setup/llm')
    assert found['fees']['state'] == 'done'
    assert client.get('/api/setup/status').json()['ledger_ready'] is True
    assert [(path.stat().st_size, path.stat().st_mtime_ns) for path in stores] == sizes
    assert files(env) == listing


def test_a_ledger_that_cannot_be_read_is_unavailable_never_empty(client, env):
    directory = env / 'portfolio'
    write_holdings(directory)
    damaged = directory / 'ledger.sqlite3'
    damaged.write_bytes(b'not a ledger ' * 400)
    listing = files(env)
    found = items(client.get('/api/setup/status?quotes=0'))
    for name in ('opening', 'fees'):
        assert found[name]['state'] == 'unavailable' and found[name]['detail'].startswith('暂时读不到账本：')
    assert found['legacy']['state'] == 'unavailable' and '无法确认是否已迁移' in found['legacy']['detail']
    draft = client.get('/api/setup/legacy')
    assert draft.status_code == 422 and draft.json()['detail'].startswith('暂时读不到账本：')
    assert damaged.read_bytes() == b'not a ledger ' * 400 and files(env) == listing


class SlowSource(ScriptedSource):
    def fetch(self, tickers):
        time.sleep(1.5)
        return super().fetch(tickers)


def test_the_quote_probe_is_bounded_and_says_what_it_saw(client, monkeypatch):
    from core.market.runtime import set_market_runtime
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE')
    clock = FakeClock('2026-11-17 10:00')
    good = ScriptedSource({'SHA:600519': quote('SHA:600519', '1680', prev_close='1660', at=clock.now(), source='eastmoney')},
                          name='eastmoney')
    set_market_runtime(fake_runtime(clock, good))
    entry = items(client.get('/api/setup/status'))['quotes']
    assert entry['state'] == 'done' and '东方财富可用' in entry['detail'] and '10:00:00' in entry['detail']
    down = ScriptedSource(name='eastmoney')
    down.down = '东方财富接口暂时无法访问'
    set_market_runtime(fake_runtime(clock, down))
    entry = items(client.get('/api/setup/status'))['quotes']
    assert entry['state'] == 'todo' and '东方财富接口暂时无法访问' in entry['detail']
    monkeypatch.setattr(setup_checks, 'QUOTE_PROBE_SECONDS', 0.2)
    set_market_runtime(fake_runtime(clock, SlowSource(name='eastmoney')))
    started = time.monotonic()
    entry = items(client.get('/api/setup/status'))['quotes']
    assert time.monotonic() - started < 1.4
    assert entry['state'] == 'todo' and '0.2 秒内没有回应' in entry['detail']
    entry = items(client.get('/api/setup/status', params={'quotes': '0'}))['quotes']
    assert entry['state'] == 'unavailable'


def test_the_quote_probe_names_the_source_that_answered_and_the_order_it_asks_them_in(client, monkeypatch):
    from core.market.runtime import set_market_runtime
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE')
    clock = FakeClock('2026-11-17 10:00')
    eastmoney = ScriptedSource(name='eastmoney')
    eastmoney.down = '数据源连接失败'
    tencent = ScriptedSource({'SHA:600519': quote('SHA:600519', '1680', prev_close='1660', at=clock.now(),
                                                  source='tencent', regular_session=False)}, name='tencent')
    yahoo = ScriptedSource(name='yahoo')
    set_market_runtime(fake_runtime(clock, eastmoney, tencent, yahoo))
    entry = items(client.get('/api/setup/status'))['quotes']
    assert entry['state'] == 'done' and entry['detail'].startswith('腾讯可用：SHA:600519 的行情时间 10:00:00')
    assert '依次使用东方财富、腾讯、Yahoo' in entry['detail'] and yahoo.calls == []
    set_market_runtime(fake_runtime(clock, tencent))
    assert '只用腾讯' in items(client.get('/api/setup/status'))['quotes']['detail']
    # Both sources reached directly by default have their switch to the proxy in the hint.
    monkeypatch.setattr(setup_checks, 'QUOTE_PROBE_SECONDS', 0.2)
    set_market_runtime(fake_runtime(clock, SlowSource(name='eastmoney')))
    entry = items(client.get('/api/setup/status'))['quotes']
    assert entry['state'] == 'todo'
    assert entry['action']['command'].split('\n') == ['TRACKER_EASTMONEY_PROXY=1', 'TRACKER_TENCENT_PROXY=1']


def test_complete_and_dismiss_are_persisted(client):
    assert client.get('/api/setup/status').json()['setup_complete'] is False
    flags = client.post('/api/setup/dismiss').json()
    assert flags['dismissed'] is True and flags['setup_complete'] is False and flags['dismissed_at']
    flags = client.post('/api/setup/complete').json()
    assert flags['setup_complete'] is True and flags['dismissed'] is True
    body = client.get('/api/setup/status').json()
    assert body['setup_complete'] is True and body['dismissed'] is True


def test_a_page_of_another_site_cannot_write(client):
    response = client.post('/api/setup/complete', headers={'Origin': 'http://attacker.example'})
    assert response.status_code == 403
    assert client.post('/api/setup/llm', headers={'Origin': 'null'},
                       json={'preset': 'deepseek', 'model': 'deepseek-chat', 'api_key': KEY}).status_code == 403
    assert client.post('/api/setup/complete', headers={'Origin': 'http://127.0.0.1:3000'}).status_code == 200
    assert client.post('/api/setup/complete', headers={'Origin': 'http://localhost:5173'}).status_code == 200


def test_demo_mode_reads_and_refuses_every_write(client, env, monkeypatch):
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    before = files(env)
    assert client.get('/api/setup/status').status_code == 200
    assert client.get('/api/setup/llm').status_code == 200
    for path, body in (('/api/setup/complete', None), ('/api/setup/dismiss', None),
                       ('/api/setup/llm', {'preset': 'deepseek', 'model': 'deepseek-chat', 'api_key': KEY}),
                       ('/api/setup/llm/test', None), ('/api/setup/legacy/proposals', {})):
        assert client.post(path, json=body).status_code == 403, path
    assert files(env) == before
    # The routes refuse on their own too, not only through the application's middleware.
    from fastapi import FastAPI
    from backend.api import setup
    app = FastAPI()
    app.include_router(setup.router, prefix='/api/setup')
    bare = TestClient(app)
    for path in ('/api/setup/complete', '/api/setup/dismiss', '/api/setup/llm', '/api/setup/llm/test',
                 '/api/setup/legacy/proposals'):
        response = bare.post(path, json={'preset': 'deepseek', 'model': 'deepseek-chat', 'api_key': KEY})
        assert response.status_code == 403 and response.json()['detail'] == '演示模式只读：不能保存设置。', path
        assert KEY not in response.text
    assert files(env) == before
    found = items(client.get('/api/setup/status'))
    assert found['alerts']['state'] == 'unavailable' and found['routines']['state'] == 'unavailable'
    assert '演示模式' in found['quotes']['detail']


def test_lab_mode_offers_only_the_model(client, monkeypatch):
    monkeypatch.setenv('TRACKER_LAB_ONLY', '1')
    found = items(client.get('/api/setup/status'))
    assert found['llm']['state'] == 'optional'
    assert {entry['state'] for key, entry in found.items() if key != 'llm'} == {'unavailable'}
    assert client.get('/api/setup/legacy').status_code == 409


# -- the model key: write-only -----------------------------------------------------------------

class Provider:
    """An OpenAI-compatible chat endpoint on loopback. ``mode`` picks the answer."""

    def __init__(self):
        self.mode = 'ok'
        self.seen = []
        self.require_key = True     # False: a local server that answers without a key
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}')
                auth = self.headers.get('Authorization', '')
                outer.seen.append({'path': self.path, 'auth': auth, 'body': body, 'headers': dict(self.headers.items())})
                if outer.mode == 'slow':
                    time.sleep(3)
                if self.path != '/v1/chat/completions' or outer.mode == 'not_found':
                    return self.answer(404, {'error': {'message': f'model {body.get("model")} not found'}})
                if outer.mode == 'server':
                    return self.answer(500, {'error': {'message': f'internal error for key {auth}'}})
                if outer.mode == 'garbage':
                    return self.answer(200, None, b'<html>not an api</html>')
                if outer.require_key and auth != f'Bearer {KEY}':
                    # Real providers echo part of the key; the setup API must not pass it on.
                    return self.answer(401, {'error': {'message': f'Incorrect API key provided: {auth}'}})
                return self.answer(200, {'id': 'x', 'model': body.get('model'), 'usage': {},
                                         'choices': [{'index': 0, 'finish_reason': 'stop',
                                                      'message': {'role': 'assistant', 'content': 'pong'}}]})

            def answer(self, status, payload, raw=None):
                data = raw if raw is not None else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                # The bounded test gives up on mode 'slow' first; its closed connection is expected, not news.
                if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
                    super().handle_error(request, client_address)

        self.server = Server(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/v1'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def provider():
    server = Provider()
    yield server
    server.close()


@contextmanager
def no_key_anywhere(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    yield
    captured = capsys.readouterr()
    for record in caplog.records:
        assert KEY not in record.getMessage() and BAD_KEY not in record.getMessage(), record.name
        assert 'SENTINEL' not in record.getMessage() and 'WRONGKEY' not in record.getMessage(), record.name
    assert 'SENTINEL' not in captured.out + captured.err and 'WRONGKEY' not in captured.out + captured.err


def save(client, **body):
    return client.post('/api/setup/llm', json={'preset': 'compatible', 'model': 'local-model', **body})


def test_the_key_is_saved_privately_and_never_returned(client, env, provider, caplog, capsys):
    with no_key_anywhere(caplog, capsys):
        response = save(client, base_url=provider.url, api_key=KEY, supports_vision=True)
        assert response.status_code == 200, response.text
        assert KEY not in response.text and 'SENTINEL' not in response.text
        current = response.json()['current']
        assert current['preset'] == 'compatible' and current['model'] == 'local-model'
        assert current['key'] == {'configured': True, 'source': 'config', 'env': None}
        assert current['base_url'] == provider.url and current['supports_vision'] is True
        for path in ('/api/setup/llm', '/api/setup/status', '/api/settings/config'):
            text = client.get(path).text
            assert 'SENTINEL' not in text, path
        found = items(client.get('/api/setup/status'))['llm']
        assert found['state'] == 'done' and '密钥已保存' in found['detail']
        # Saving again without a key keeps the stored one.
        assert save(client, base_url=provider.url).status_code == 200
    # Stored where the chat service reads it, in a file only the owner can read.
    path = env / 'config.json'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    from config import Config
    config = Config(str(path))
    assert config.get('current_api_group') == 'openai_compatible'
    assert config.get_current_model() == 'local-model'
    assert config.get_current_api_group()['api_key'] == KEY
    assert config.get_current_api_group()['headers']['Authorization'] == f'Bearer {KEY}'


def test_invalid_requests_never_echo_the_key(client, env, caplog, capsys):
    with no_key_anywhere(caplog, capsys):
        bad = [
            {'preset': 'compatible', 'model': 'm', 'base_url': 'http://127.0.0.1:1/v1', 'api_key': KEY + ' tail'},
            {'preset': 'compatible', 'model': 'm', 'base_url': 'http://127.0.0.1:1/v1', 'api_key': KEY * 40},
            {'preset': 'compatible', 'model': 'bad model!', 'api_key': KEY, 'base_url': 'http://127.0.0.1:1/v1'},
            {'preset': 'nowhere', 'model': 'm', 'api_key': KEY},
            {'preset': 'compatible', 'model': 'm', 'api_key': KEY, 'extra': KEY},
            {'preset': 'deepseek', 'model': 'deepseek-chat', 'api_key': KEY, 'base_url': 'http://127.0.0.1:9/v1'},
            {'preset': 'compatible', 'model': 'm', 'api_key': 12345},
        ]
        for body in bad:
            response = client.post('/api/setup/llm', json=body)
            assert response.status_code == 422, body
            assert 'SENTINEL' not in response.text
        response = client.post('/api/setup/llm', content=f'{{"api_key": "{KEY}"', headers={'Content-Type': 'application/json'})
        assert response.status_code == 422 and 'SENTINEL' not in response.text
    assert not (env / 'config.json').exists()


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'ftp://127.0.0.1/v1', 'http://user:pw@127.0.0.1/v1',
                                 'http://127.0.0.1/v1?key=1', 'http://127.0.0.1/v1#x', 'gopher://x', '/v1',
                                 'http:///v1', 'javascript:alert(1)', 'http://127.0.0.1:99999/v1', 'http://a b/v1'])
def test_base_urls_follow_the_research_connection_rule(client, url):
    response = save(client, base_url=url, api_key=KEY)
    assert response.status_code == 422 and response.json()['detail'] == llm_setup.FAILURES['bad_url']
    if url in ('http://127.0.0.1:99999/v1', 'http://a b/v1'):
        return   # stricter than the research connections: a malformed port or a space is refused too
    from core.lab_models import Connection
    with pytest.raises(ValueError):
        Connection(id='x', name='x', provider='openai', model='m', base_url=url)


def test_loopback_and_remote_https_addresses_are_accepted(client, provider):
    local = save(client, base_url=provider.url)   # a local server needs no key
    assert local.status_code == 200 and local.json()['current']['key']['configured'] is False
    assert items(client.get('/api/setup/status'))['llm']['state'] == 'done'
    assert save(client, base_url='https://llm.example.test/v1/').status_code == 422   # a remote one does
    assert save(client, base_url='https://llm.example.test/v1/', api_key=KEY).json()['current']['base_url'] == \
        'https://llm.example.test/v1'


def test_a_hand_edited_unsafe_address_is_not_tested(client, env):
    (env / 'config.json').write_text(json.dumps({
        'current_api_group': 'g', 'llm': {'current_model': 'm'},
        'api_groups': {'g': {'name': 'g', 'base_url': 'file:///etc/passwd', 'api_key': KEY, 'models': []}}}))
    body = client.post('/api/setup/llm/test').json()
    assert body['ok'] is False and body['code'] == 'bad_url' and 'SENTINEL' not in json.dumps(body)


def test_the_connection_test_classifies_without_provider_text(client, env, provider, caplog, capsys):
    with no_key_anywhere(caplog, capsys):
        assert client.post('/api/setup/llm/test').json()['code'] == 'not_configured'
        assert save(client, base_url=provider.url, api_key=KEY).status_code == 200
        body = client.post('/api/setup/llm/test').json()
        assert body['ok'] is True and body['message'] == '连接成功：模型已回应。' and body['model'] == 'local-model'
        assert provider.seen[-1]['body']['max_tokens'] == 8 and provider.seen[-1]['auth'] == f'Bearer {KEY}'
        cases = {'not_found': 'not_found', 'server': 'upstream', 'garbage': 'parse'}
        for mode, code in cases.items():
            provider.mode = mode
            body = client.post('/api/setup/llm/test').json()
            assert body == {'ok': False, 'code': code, 'message': llm_setup.FAILURES[code], 'model': 'local-model',
                            'status': body.get('status')}, body
            assert 'not found' not in json.dumps(body) and 'internal error' not in json.dumps(body)
        provider.mode = 'ok'
        assert save(client, base_url=provider.url, api_key=BAD_KEY).status_code == 200
        body = client.post('/api/setup/llm/test').json()
        assert body['code'] == 'auth' and body['status'] == 401 and body['message'] == llm_setup.FAILURES['auth']
        assert 'Incorrect' not in json.dumps(body) and 'WRONGKEY' not in json.dumps(body)
        # Nothing listens on this port: a connection failure, in fixed words.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            closed = sock.getsockname()[1]
        assert save(client, base_url=f'http://127.0.0.1:{closed}/v1', api_key=KEY).status_code == 200
        body = client.post('/api/setup/llm/test').json()
        assert body['code'] == 'connection' and body['message'] == llm_setup.FAILURES['connection']
    assert any('model connection test failed: auth' in record.getMessage() for record in caplog.records)


def test_the_connection_test_is_bounded(env, provider, monkeypatch):
    import asyncio
    from config import settings
    llm_setup.save(llm_setup.parse_request({'preset': 'compatible', 'model': 'm', 'base_url': provider.url,
                                            'api_key': KEY}))
    settings._config = None
    provider.mode = 'slow'
    started = time.monotonic()
    body = asyncio.run(llm_setup.test_connection(timeout=0.5))
    assert body['code'] == 'timeout' and time.monotonic() - started < 2.6


def test_the_example_files_placeholder_key_is_no_key(client, env):
    # `make setup` copies config.example.json; its "sk-YOUR_..._HERE" values were never filled in.
    root = Path(__file__).resolve().parents[1]
    (env / 'config.json').write_text((root / 'dashboard/config.example.json').read_text(encoding='utf-8'), encoding='utf-8')
    found = items(client.get('/api/setup/status'))['llm']
    assert found['state'] == 'optional' and '还没有 API 密钥' in found['detail']
    assert client.get('/api/setup/llm').json()['current']['key']['configured'] is False
    assert client.post('/api/setup/llm/test').json()['code'] == 'no_key'
    assert client.post('/api/setup/llm', json={'preset': 'openai', 'model': 'gpt-4o'}).status_code == 422


def test_a_key_from_the_environment_counts_and_is_not_written(client, env, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', KEY)
    response = client.post('/api/setup/llm', json={'preset': 'openai', 'model': 'gpt-4o'})
    assert response.status_code == 200, response.text
    assert response.json()['current']['key'] == {'configured': True, 'source': 'env', 'env': 'OPENAI_API_KEY'}
    assert 'SENTINEL' not in (env / 'config.json').read_text()
    monkeypatch.delenv('OPENAI_API_KEY')
    from config import settings
    settings._config = None
    found = items(client.get('/api/setup/status'))['llm']
    assert found['state'] == 'optional' and '还没有 API 密钥' in found['detail']
    assert client.post('/api/setup/llm', json={'preset': 'openai', 'model': 'gpt-4o'}).status_code == 422


def chat_once():
    """The chat service's own request path: ``AgentService._init_llm`` from the saved settings, one chat."""
    import asyncio
    from backend.services.agent_service import AgentService
    from config import settings
    settings._config = None
    service = object.__new__(AgentService)
    service.config = settings.get_config()
    asyncio.run(service._init_llm())
    return asyncio.run(service.llm_provider.chat([{'role': 'user', 'content': 'ping'}], max_tokens=8))


def sent_headers(server):
    return {name.lower(): value for name, value in server.seen[-1]['headers'].items()}


def test_a_saved_key_never_goes_to_another_address(client, env, provider, caplog, capsys):
    # A DeepSeek key saved under 兼容接口, then the address changed with the key field left blank.
    other = Provider()     # another address (another port); it answers without a key and records what it got
    other.require_key = False
    try:
        with no_key_anywhere(caplog, capsys):
            assert save(client, base_url=provider.url, api_key=KEY).status_code == 200
            stored = (env / 'config.json').read_bytes()
            # A remote address needs its own key: refused, nothing written, nothing sent.
            response = save(client, base_url='https://openrouter.example.test/api/v1')
            assert response.status_code == 422, response.text
            assert response.json()['detail'] == llm_setup.FAILURES['new_address']
            assert (env / 'config.json').read_bytes() == stored
            # A local address may go without a key; the stored one does not go with it.
            response = save(client, base_url=other.url)
            assert response.status_code == 200 and response.json()['current']['key']['configured'] is False
            assert 'SENTINEL' not in (env / 'config.json').read_text()
            assert client.post('/api/setup/llm/test').json()['ok'] is True
            assert 'authorization' not in sent_headers(other) and 'SENTINEL' not in json.dumps(other.seen)
            chat_once()      # the chat service after the save
            assert len(other.seen) == 2 and 'SENTINEL' not in json.dumps(other.seen)
            # Typed again for the new address, it is used there.
            assert save(client, base_url=other.url, api_key=KEY).status_code == 200
            assert client.post('/api/setup/llm/test').json()['ok'] is True
            assert sent_headers(other)['authorization'] == f'Bearer {KEY}'
    finally:
        other.close()


def test_a_key_saved_for_another_provider_is_not_reused(client, env):
    (env / 'config.json').write_text(json.dumps({
        'current_api_group': 'openai_compatible', 'llm': {'current_model': 'm'},
        'api_groups': {'openai_compatible': {'name': 'gw', 'base_url': 'https://gw.example.test/v1',
                                             'provider_type': 'claude', 'api_key': KEY, 'models': []}}}))
    response = save(client, base_url='https://gw.example.test/v1')
    assert response.status_code == 422 and response.json()['detail'] == llm_setup.FAILURES['new_address']
    assert save(client, base_url='https://gw.example.test/v1', api_key=KEY).status_code == 200


def test_a_local_server_without_a_key_gets_no_authorization_header(client, env, provider):
    # Fresh settings: the shipped 兼容接口 group carries the header template "Authorization: Bearer ".
    provider.require_key = False
    response = save(client, base_url=provider.url)
    assert response.status_code == 200 and response.json()['current']['local'] is True
    body = client.post('/api/setup/llm/test').json()
    assert body['ok'] is True, body
    assert 'authorization' not in sent_headers(provider)
    chat_once()      # the chat service sends the same request
    assert len(provider.seen) == 2 and 'authorization' not in sent_headers(provider)


def test_blank_credential_headers_are_filled_with_the_key_or_left_out(tmp_path):
    from config import Config
    path = tmp_path / 'config.json'
    group = {'base_url': 'http://127.0.0.1:1/v1', 'api_key': '', 'models': [],
             'headers': {'Authorization': 'Bearer ', 'x-api-key': '', 'Content-Type': 'application/json'}}
    path.write_text(json.dumps({'current_api_group': 'g', 'api_groups': {'g': group}}))
    assert Config(str(path)).get_current_api_group()['headers'] == {'Content-Type': 'application/json'}
    path.write_text(json.dumps({'current_api_group': 'g', 'api_groups': {'g': {**group, 'api_key': KEY}}}))
    assert Config(str(path)).get_current_api_group()['headers'] == {
        'Authorization': f'Bearer {KEY}', 'x-api-key': KEY, 'Content-Type': 'application/json'}
    path.write_text(json.dumps({'current_api_group': 'g', 'api_groups': {'g': {**group, 'headers': {'Authorization': ''}}}}))
    assert Config(str(path)).get_current_api_group()['headers'] is None


def test_presets_say_which_key_the_environment_provides(client, env, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', KEY)
    body = client.get('/api/setup/llm').json()
    assert 'SENTINEL' not in json.dumps(body)
    keys = {preset['id']: preset['env_key'] for preset in body['presets']}
    assert keys == {'deepseek': None, 'openai': {'name': 'OPENAI_API_KEY', 'available': False},
                    'anthropic': {'name': 'ANTHROPIC_API_KEY', 'available': True}, 'compatible': None}
    response = client.post('/api/setup/llm', json={'preset': 'anthropic', 'model': 'claude-sonnet-4-20250514'})
    assert response.status_code == 200 and response.json()['current']['key']['source'] == 'env'


# -- legacy holdings --------------------------------------------------------------------------

HOLDINGS = {
    'date': '2026-11-14',
    'groups': {
        '进攻': {'cost_basis': 500000, 'cash': 50000, 'fund': 0, 'positions': [
            {'name': '示例A股', 'ticker': 'SHA:600000', 'quantity': 1000, 'cost_price': 10.5},
            {'name': '示例港股', 'ticker': 'HKG:0700', 'quantity': 100, 'cost_price': '三百五'},
            {'name': '已清仓', 'ticker': 'SHE:000001', 'quantity': 0, 'cost_price': 9},
        ]},
        '稳健': {'cost_basis': 800000, 'cash_balances': {'CNY': 100000, 'USD': 20}, 'cash': 100000, 'positions': [
            {'name': '示例ETF', 'ticker': 'SHA:510300', 'quantity': 5000, 'cost_price': 4.0},
        ]},
    },
}


def write_holdings(directory, day='2026-11-14', data=None):
    folder = Path(directory) / 'holdings'
    folder.mkdir(exist_ok=True)
    path = folder / f'{day}.json'
    path.write_text(json.dumps(data or HOLDINGS, ensure_ascii=False), encoding='utf-8')
    return path


def test_the_draft_reads_what_it_can_and_asks_for_the_rest(client, env):
    directory = env / 'portfolio'
    write_holdings(directory, '2026-11-13', {'groups': {'旧': {'cash': 1}}})
    path = write_holdings(directory)
    before = files(env)
    found = items(client.get('/api/setup/status'))['legacy']
    assert found['state'] == 'todo' and 'holdings/2026-11-14.json' in found['detail'] and '共 2 个日期' in found['detail']
    draft = client.get('/api/setup/legacy').json()
    assert files(env) == before   # the draft writes nothing either
    assert draft['source'] == 'holdings' and draft['file'] == 'holdings/2026-11-14.json' and draft['date'] == '2026-11-14'
    attack, steady = draft['accounts']
    assert attack['account'] == '进攻' and attack['exists'] is False
    assert attack['cash'] == [{'key': 'a0.cash.CNY', 'value': '50000', 'problem': None, 'note': None, 'currency': 'CNY'}]
    assert attack['contributed_cny']['value'] == '500000'
    rows = {row['name']: row for row in attack['positions']}
    assert set(rows) == {'示例A股', '示例港股'}
    assert rows['示例A股']['cost_price']['value'] == '10.5' and rows['示例A股']['quantity']['value'] == '1000'
    assert rows['示例港股']['cost_price']['value'] is None
    assert '成本价无法读取' in rows['示例港股']['cost_price']['problem'] and '三百五' in rows['示例港股']['cost_price']['problem']
    assert [(item['key'], item['kind']) for item in draft['needs_input']] == [('a0.p1.cost_price', 'cost')]
    assert draft['needs_input'][0]['row'] == 'a0.p1'
    assert draft['skipped'] == [{'account': '进攻', 'name': '已清仓', 'ticker': 'SHE:000001', 'reason': '数量为 0（已清仓），不迁移'}]
    assert [cash['currency'] for cash in steady['cash']] == ['CNY', 'USD']
    # An earlier date picks the earlier file, as the 流水 page's preview does.
    assert client.get('/api/setup/legacy', params={'date': '2026-11-13'}).json()['file'] == 'holdings/2026-11-13.json'
    assert client.get('/api/setup/legacy', params={'date': '2026-11-01'}).status_code == 422
    assert path.read_text(encoding='utf-8') == json.dumps(HOLDINGS, ensure_ascii=False)


def test_the_proposal_needs_every_unreadable_value_and_is_bound_to_the_file(client, env):
    directory = env / 'portfolio'
    path = write_holdings(directory)
    original = path.read_bytes()
    draft = client.get('/api/setup/legacy').json()
    request = {'source': 'holdings', 'date': draft['date'], 'token': draft['token']}
    response = client.post('/api/setup/legacy/proposals', json=request)
    assert response.status_code == 422 and '还有 1 项需要填写' in response.json()['detail'] and '示例港股 成本价' in response.json()['detail']
    for fills in ({'a0.p1.cost_price': 'abc'}, {'a0.p1.cost_price': '-1'}, {'a0.p0.cost_price': '1'}):
        assert client.post('/api/setup/legacy/proposals', json={**request, 'fills': fills}).status_code == 422, fills
    assert client.post('/api/setup/legacy/proposals', json={**request, 'skip': ['a0.p0']}).status_code == 422
    stale = client.post('/api/setup/legacy/proposals', json={**request, 'token': '0' * 64, 'fills': {'a0.p1.cost_price': '350'}})
    assert stale.status_code == 409
    response = client.post('/api/setup/legacy/proposals', json={**request, 'fills': {'a0.p1.cost_price': '350'}})
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert [event['account'] for event in proposal['events']] == ['进攻', '稳健']
    attack = proposal['events'][0]
    assert attack['source'] == 'legacy-opening' and attack['external_id'] == '2026-11-14:进攻'
    assert [(p['ticker'], p['quantity'], p['cost_price']) for p in attack['positions']] == [
        ('SHA:600000', '1000', '10.5'), ('HKG:0700', '100', '350')]
    assert proposal['events'][1]['cash_balances'] == {'CNY': '100000', 'USD': '20'}
    assert proposal['after']['accounts']['进攻']['contributed_cny'] == '500000'
    # Skipping the unreadable row instead is allowed, and replaces the pending preview (one per file).
    skipped = client.post('/api/setup/legacy/proposals', json={**request, 'skip': ['a0.p1']}).json()
    assert [p['ticker'] for p in skipped['events'][0]['positions']] == ['SHA:600000']
    ledger = Ledger(directory / 'ledger.sqlite3')
    assert [item['id'] for item in ledger.proposals()] == [skipped['id']]
    # The file changes after the preview: the proposal can no longer be confirmed.
    path.write_text(json.dumps({**HOLDINGS, 'note': 'edited'}), encoding='utf-8')
    with pytest.raises(StaleProposal):
        ledger.confirm(skipped['id'])
    path.write_bytes(original)
    draft = client.get('/api/setup/legacy').json()
    proposal = client.post('/api/setup/legacy/proposals', json={**request, 'token': draft['token'],
                                                                'fills': {'a0.p1.cost_price': '350'}}).json()
    assert client.post(f'/api/ledger/proposals/{proposal["id"]}/confirm').status_code == 200
    # Confirmed: the file is untouched, the checklist says migrated, a second migration finds nothing to do.
    assert path.read_bytes() == original
    found = items(client.get('/api/setup/status'))
    assert found['legacy']['state'] == 'done' and '已迁移' in found['legacy']['detail']
    assert found['opening']['state'] == 'done'
    again = client.get('/api/setup/legacy').json()
    assert all(account['exists'] for account in again['accounts']) and again['needs_input'] == []
    response = client.post('/api/setup/legacy/proposals', json={**request, 'token': again['token']})
    assert response.status_code == 422 and '都已在账本中' in response.json()['detail']


def test_a_readable_file_gives_exactly_the_ledger_pages_opening_events(client, env):
    directory = env / 'portfolio'
    data = json.loads(json.dumps(HOLDINGS))
    data['groups']['进攻']['positions'][1]['cost_price'] = 350
    write_holdings(directory, data=data)
    draft = client.get('/api/setup/legacy', params={'date': '2026-11-15'}).json()
    assert draft['needs_input'] == []
    ours = legacy_migration.propose('holdings', '2026-11-15', draft['token'])['events']
    theirs = [normalize(event) for event in opening_events(directory, '2026-11-15')]
    assert ours == theirs


@pytest.mark.parametrize('group, key, problem', [
    ({'cash': 1, 'positions': [{'ticker': 'SHA:600000', 'cost_price': 1}]}, 'a0.p0.quantity', '原文件没有数量'),
    ({'cash': 1, 'positions': [{'ticker': 'SHA:600000', 'quantity': 1}]}, 'a0.p0.cost_price', '原文件没有成本价'),
    ({'cash': 1, 'positions': [{'ticker': '600000', 'quantity': 1, 'cost_price': 1}]}, 'a0.p0.ticker', '代码无法识别'),
    ({'cash': 1, 'positions': [{'ticker': 'SHA:900901', 'quantity': 1, 'cost_price': 1}]}, 'a0.p0.ticker', 'B 股'),
    ({'cash': 1, 'positions': [{'ticker': 'SHA:600000', 'quantity': -5, 'cost_price': 1}]}, 'a0.p0.quantity', '必须大于 0'),
    ({'cash': 'lots'}, 'a0.cash.CNY', 'CNY 现金无法读取'),
    ({'cash': 5, 'cash_balances': {'CNY': 6}}, 'a0.cash.CNY', '不一致'),
    ({'cash': 1, 'fund': 'x'}, 'a0.fund_cny', '基金估值无法读取'),
])
def test_values_that_cannot_be_read_are_asked_for_not_guessed(client, env, group, key, problem):
    data = {'groups': {'账户': {'cost_basis': 1, **group}}}
    write_holdings(env / 'portfolio', data=data)
    draft = client.get('/api/setup/legacy').json()
    needed = {item['key']: item for item in draft['needs_input']}
    assert key in needed and problem in needed[key]['problem'], draft['needs_input']


def test_a_typed_ticker_is_checked_as_the_ledger_books_it():
    assert legacy_migration._answer('ticker', 'SHA:600000') == 'SHA:600000'
    with pytest.raises(legacy_migration.LegacyError, match='B 股'):
        legacy_migration._answer('ticker', 'SHA:900901')
    with pytest.raises(legacy_migration.LegacyError, match='代码无法识别'):
        legacy_migration._answer('ticker', '600000')


def test_without_cost_basis_the_net_contribution_is_asked_for(client, env):
    write_holdings(env / 'portfolio', data={'groups': {'账户': {'cash': 100}}})
    draft = client.get('/api/setup/legacy').json()
    assert [item['key'] for item in draft['needs_input']] == ['a0.contributed_cny']
    assert draft['accounts'][0]['cash'][0]['value'] == '100'


def test_the_legacy_portfolio_page_file(client, env):
    path = env / 'data' / 'portfolio.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'cash': 2000.5, 'updated_at': '2026-10-01T09:00:00', 'positions': [
        {'symbol': '600519', 'name': '示例白酒', 'market': 'a_share', 'quantity': 100, 'cost_price': 1500},
        {'symbol': '700', 'name': '示例港股', 'market': 'hk_stock', 'quantity': 200, 'cost_price': 380},
        {'symbol': 'EXAMPLE', 'name': '示例美股', 'market': 'us_stock', 'quantity': 1, 'cost_price': 100},
    ]}), encoding='utf-8')
    found = items(client.get('/api/setup/status'))['legacy']
    assert found['state'] == 'todo' and 'data/portfolio.json' in found['detail']
    # Without a date the opening is the day the file was last saved.
    assert client.get('/api/setup/legacy').json()['date'] == '2026-10-01'
    draft = client.get('/api/setup/legacy', params={'source': 'portfolio', 'date': '2026-11-14'}).json()
    assert draft['file_date'] == '2026-10-01'
    account = draft['accounts'][0]
    assert account['account'] == '主账户' and account['account_editable'] is True
    tickers = [row['ticker']['value'] for row in account['positions']]
    assert tickers == ['SHA:600519', 'HKG:00700', None]
    needed = {item['key']: item for item in draft['needs_input']}
    assert set(needed) == {'a0.contributed_cny', 'a0.p2.ticker'}
    assert needed['a0.p2.ticker']['choices'] == ['NASDAQ:EXAMPLE', 'NYSE:EXAMPLE']
    request = {'source': 'portfolio', 'date': '2026-11-14', 'token': draft['token'], 'account_name': '老账户',
               'fills': {'a0.contributed_cny': '200000', 'a0.p2.ticker': 'NYSE:EXAMPLE'}}
    proposal = client.post('/api/setup/legacy/proposals', json=request).json()
    event = proposal['events'][0]
    assert event['account'] == '老账户' and event['source'] == 'legacy-portfolio'
    assert event['cash_balances'] == {'CNY': '2000.5'} and event['contributed_cny'] == '200000'
    assert [p['ticker'] for p in event['positions']] == ['SHA:600519', 'HKG:00700', 'NYSE:EXAMPLE']
    assert json.loads(path.read_text(encoding='utf-8'))['cash'] == 2000.5


def test_an_empty_legacy_portfolio_file_is_not_a_migration(client, env):
    path = env / 'data' / 'portfolio.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'cash': 0, 'positions': []}), encoding='utf-8')
    assert items(client.get('/api/setup/status'))['legacy']['state'] == 'done'
    assert client.get('/api/setup/legacy').status_code == 404


def confirm(client, proposal):
    response = client.post(f'/api/ledger/proposals/{proposal["id"]}/confirm')
    assert response.status_code == 200, response.text


def test_every_legacy_account_not_yet_in_the_ledger_is_offered(client, env):
    # Account 进攻 was opened by hand; 稳健 from the same dated file is still to migrate.
    directory = env / 'portfolio'
    write_holdings(directory)
    confirm_opening(directory, account='进攻', cash={'CNY': '1'}, contributed_cny='1')
    found = items(client.get('/api/setup/status'))['legacy']
    assert found['state'] == 'optional' and '稳健' in found['detail'] and '进攻' not in found['detail']
    assert found['action']['href'] == '/setup?step=account&start=legacy'
    draft = client.get('/api/setup/legacy').json()
    assert {account['account']: account['exists'] for account in draft['accounts']} == {'进攻': True, '稳健': False}


def test_the_other_legacy_source_is_offered_after_one_is_migrated(client, env):
    write_holdings(env / 'portfolio', data={'groups': {'甲': {'cost_basis': 100, 'cash': 100}}})
    path = env / 'data' / 'portfolio.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'cash': 5, 'updated_at': '2026-11-10T15:00:00', 'positions': [
        {'symbol': '700', 'name': '示例港股', 'market': 'hk_stock', 'quantity': 100, 'cost_price': 300}]}), encoding='utf-8')
    draft = client.get('/api/setup/legacy', params={'source': 'holdings'}).json()
    confirm(client, client.post('/api/setup/legacy/proposals', json={
        'source': 'holdings', 'date': draft['date'], 'token': draft['token'], 'fills': {}, 'skip': []}).json())
    found = items(client.get('/api/setup/status'))['legacy']
    assert found['state'] == 'optional' and 'data/portfolio.json' in found['detail'], found
    assert found['action']['href'] == '/setup?step=account&start=legacy'
    draft = client.get('/api/setup/legacy').json()
    assert draft['source'] == 'portfolio'      # what is left is what the migration opens on
    confirm(client, client.post('/api/setup/legacy/proposals', json={
        'source': 'portfolio', 'date': draft['date'], 'token': draft['token'], 'account_name': '乙',
        'fills': {'a0.contributed_cny': '30005'}, 'skip': []}).json())
    found = items(client.get('/api/setup/status'))['legacy']
    assert found['state'] == 'done' and '甲' in found['detail'] and '乙' in found['detail']


def test_numbers_in_the_file_are_read_exactly(client, env):
    folder = env / 'portfolio' / 'holdings'
    folder.mkdir()
    (folder / '2026-11-14.json').write_text(
        '{"groups": {"甲": {"cost_basis": 1000.12345678901234567890, "cash": 0.1, "fund": 2.000000000000000000001,'
        ' "positions": [{"ticker": "SHA:600000", "quantity": 100, "cost_price": 10.123456789012345678901}]}}}',
        encoding='utf-8')
    account = client.get('/api/setup/legacy').json()['accounts'][0]
    assert account['contributed_cny']['value'] == '1000.1234567890123456789'
    assert account['cash'][0]['value'] == '0.1' and account['fund_cny']['value'] == '2.000000000000000000001'
    assert account['positions'][0]['cost_price']['value'] == '10.123456789012345678901'
    (folder / '2026-11-14.json').write_text('{"groups": {"甲": {"cash": [1.5], "cost_basis": 1}}}', encoding='utf-8')
    draft = client.get('/api/setup/legacy').json()
    assert draft['needs_input'][0]['problem'] == 'CNY 现金无法读取：“[1.5]”'


# -- round 5: a migrated source is not migrated twice; the form's key rule is the API's -----------------

def ledger_accounts(env):
    state = Ledger(env / 'portfolio' / 'ledger.sqlite3').state()
    return {name: account['cash_balances'] for name, account in state['accounts'].items()}


def test_a_migrated_legacy_source_is_never_migrated_again(client, env):
    path = env / 'data' / 'portfolio.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'cash': 10000, 'updated_at': '2026-11-10T15:00:00', 'positions': []}), encoding='utf-8')
    draft = client.get('/api/setup/legacy').json()
    request = {'source': 'portfolio', 'date': draft['date'], 'token': draft['token'], 'account_name': '老账户',
               'fills': {'a0.contributed_cny': '10000'}, 'skip': []}
    confirm(client, client.post('/api/setup/legacy/proposals', json=request).json())
    assert ledger_accounts(env) == {'老账户': {'CNY': '10000'}}
    assert items(client.get('/api/setup/status'))['legacy']['state'] == 'done'
    again = client.get('/api/setup/legacy').json()
    assert [account['exists'] for account in again['accounts']] == [True] and again['needs_input'] == []
    assert [(item['source'], item['pending']) for item in again['sources']] == [('portfolio', False)]
    # The page asks again under the default account: refused, the ledger keeps one copy of the assets.
    for name in ('主账户', '老账户', None):
        body = {**request, 'token': again['token'], **({'account_name': name} if name else {})}
        response = client.post('/api/setup/legacy/proposals', json=body)
        assert response.status_code == 422 and response.json()['detail'] == legacy_migration.ALREADY_MIGRATED
    assert ledger_accounts(env) == {'老账户': {'CNY': '10000'}}
    # The dated holdings: an account already in the ledger is never booked again either.
    write_holdings(env / 'portfolio', data={'groups': {'甲': {'cost_basis': 100, 'cash': 100}}})
    draft = client.get('/api/setup/legacy', params={'source': 'holdings'}).json()
    request = {'source': 'holdings', 'date': draft['date'], 'token': draft['token'], 'fills': {}, 'skip': []}
    confirm(client, client.post('/api/setup/legacy/proposals', json=request).json())
    response = client.post('/api/setup/legacy/proposals', json=request)
    assert response.status_code == 422 and response.json()['detail'] == '没有可以迁移的账户：旧文件中的账户都已在账本中。'
    assert ledger_accounts(env) == {'老账户': {'CNY': '10000'}, '甲': {'CNY': '100'}}


def test_a_reversed_migration_can_be_redone_once(client, env):
    # 40868ac: reversing a mistaken portfolio.json opening and migrating again under the corrected name restores
    # one ¥10,000 account. A migration that stands is still refused a second time (round 5).
    path = env / 'data' / 'portfolio.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'cash': 10000, 'updated_at': '2026-11-10T15:00:00', 'positions': []}), encoding='utf-8')
    draft = client.get('/api/setup/legacy').json()
    request = {'source': 'portfolio', 'date': draft['date'], 'token': draft['token'], 'account_name': '主帐户',
               'fills': {'a0.contributed_cny': '10000'}, 'skip': []}
    confirm(client, client.post('/api/setup/legacy/proposals', json=request).json())
    assert ledger_accounts(env) == {'主帐户': {'CNY': '10000'}}
    opening_id = next(event['id'] for event in Ledger(env / 'portfolio' / 'ledger.sqlite3').history()
                      if event['source'] == 'legacy-portfolio')
    reversal = client.post('/api/ledger/proposals', json={'events': [
        {'kind': 'reverse', 'date': draft['date'], 'target_id': opening_id}]})
    assert reversal.status_code == 200, reversal.text
    confirm(client, reversal.json())
    assert ledger_accounts(env) == {}
    # Reversed: offered again, and booked once more under the corrected name.
    assert items(client.get('/api/setup/status'))['legacy']['state'] == 'todo'
    again = client.get('/api/setup/legacy').json()
    assert [account['exists'] for account in again['accounts']] == [False]
    assert [(item['source'], item['pending']) for item in again['sources']] == [('portfolio', True)]
    response = client.post('/api/setup/legacy/proposals', json={**request, 'token': again['token'], 'account_name': '主账户'})
    assert response.status_code == 200, response.text
    confirm(client, response.json())
    assert ledger_accounts(env) == {'主账户': {'CNY': '10000'}}
    assert items(client.get('/api/setup/status'))['legacy']['state'] == 'done'
    # A third attempt: the corrected migration stands, so it is refused and the assets stay ¥10,000.
    third = client.get('/api/setup/legacy').json()
    for name in ('主账户', '另一个账户'):
        response = client.post('/api/setup/legacy/proposals', json={**request, 'token': third['token'], 'account_name': name})
        assert response.status_code == 422 and response.json()['detail'] == legacy_migration.ALREADY_MIGRATED
    assert ledger_accounts(env) == {'主账户': {'CNY': '10000'}}


# One saved key (兼容接口 at DeepSeek's address) and OPENAI_API_KEY in the server environment; whether saving
# each address needs a typed key. dashboard/frontend/tests/setup.test.mjs checks the form against the same table.
KEYLESS_TABLE = [
    ('https://api.deepseek.com/v1', None),
    ('https://API.DeepSeek.com:443/v2', None),
    ('https://api.deepseek.com:8443/v1', 'new_address'),
    ('http://api.deepseek.com/v1', 'new_address'),
    ('https://openrouter.example.test/api/v1', 'new_address'),
    ('https://api.openai.com/v1', None),
    ('http://api.openai.com:8080/v1', None),
    ('http://127.0.0.1:8000/v1', None),
    ('http://localhost:11434/v1', None),
]


def test_the_form_is_told_exactly_which_addresses_need_a_typed_key(client, env, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', BAD_KEY)
    assert save(client, base_url='https://api.deepseek.com/v1', api_key=KEY).status_code == 200
    presets = {preset['id']: preset for preset in client.get('/api/setup/llm').json()['presets']}
    assert 'SENTINEL' not in json.dumps(presets) and 'WRONGKEY' not in json.dumps(presets)
    assert presets['compatible']['keyless'] == {
        'origins': ['https://api.deepseek.com:443'], 'env': [{'host': 'api.openai.com', 'name': 'OPENAI_API_KEY'}],
        'loopback': True, 'saved_key': True}
    assert presets['deepseek']['keyless'] == {
        'origins': [], 'env': [{'host': 'api.openai.com', 'name': 'OPENAI_API_KEY'}], 'loopback': True, 'saved_key': False}
    stored = (env / 'config.json').read_bytes()
    for url, needed in KEYLESS_TABLE:
        response = save(client, base_url=url)
        if needed:
            assert response.status_code == 422 and response.json()['detail'] == llm_setup.FAILURES[needed], url
        else:
            assert response.status_code == 200, (url, response.text)
        (env / 'config.json').write_bytes(stored)
        from config import settings
        settings._config = None


def test_the_official_openai_address_under_the_compatible_preset_takes_the_environment_key(client, env, monkeypatch):
    # Baseline (and the API at every revision): saved without typing a key, and the request carries the environment's.
    monkeypatch.setenv('OPENAI_API_KEY', KEY)
    response = save(client, base_url='https://api.openai.com/v1')
    assert response.status_code == 200 and response.json()['current']['key'] == {
        'configured': True, 'source': 'env', 'env': 'OPENAI_API_KEY'}
    assert 'SENTINEL' not in (env / 'config.json').read_text()
