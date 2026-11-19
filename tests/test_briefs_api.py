"""``/api/briefs`` on the real application, and generation through ``POST /api/routines/{kind}/run``.

Scripted quotes (``fake_quotes.json``) drive the clock. The real application only answers loopback Host
headers, hence ``base_url``. Fictional data only.
"""
import asyncio
from contextlib import asynccontextmanager
import time as wall

import pytest
from fastapi.testclient import TestClient

from core.market.runtime import set_market_runtime
from core.routines import briefs, load_kinds, scheduler
from core.routines.scheduler import Scheduler
from tests.brief_support import runtime_for, write_script
from tests.fakes import build_ledger, opening, position

TUESDAY = '2026-11-17'
A = 'SHA:600001'
QUOTES = {A: {'name': '示例一号', 'price': '10.5', 'prev_close': '10.2', 'quote_time': '2026-11-16T15:00:00+08:00'}}


@pytest.fixture
def portfolio(tmp_path, monkeypatch):
    directory = tmp_path / 'portfolio'
    directory.mkdir()
    monkeypatch.setenv('PORTFOLIO_DIR', str(directory))
    monkeypatch.chdir(tmp_path)   # importing the application creates data directories in the working directory
    for name in ('TRACKER_DEMO_MODE', 'TRACKER_LAB_ONLY', 'TRACKER_QUOTE_SOURCE', 'TRACKER_PREMARKET',
                 'TRACKER_PREMARKET_TIME', 'TRACKER_POSTMARKET', 'TRACKER_POSTMARKET_TIME', briefs.COMMENTARY_ENV):
        monkeypatch.delenv(name, raising=False)
    build_ledger(directory, [opening('Main', cash={'CNY': '1000'}, positions=[position(A, '100', '10', '示例一号')],
                                     date='2026-11-02')])
    write_script(directory, '2026-11-17T16:00:00+08:00', QUOTES)
    runtime = runtime_for(directory)
    set_market_runtime(runtime)
    yield directory, runtime
    set_market_runtime(None)
    assert scheduler.current() is None, 'a test left a scheduler running'


@pytest.fixture
def app():
    from backend.main import app
    return app


def test_reading_on_an_empty_store_says_what_is_missing_and_creates_nothing(portfolio, app):
    directory, _ = portfolio
    client = TestClient(app, base_url='http://127.0.0.1')
    body = client.get('/api/briefs').json()
    assert (body['date'], body['current_trading_date'], body['previous_date'], body['next_date']) == (
        TUESDAY, TUESDAY, '2026-11-16', None)
    assert body['mode'] == 'normal' and body['scheduler_alive'] is False and body['latest_date'] is None
    pre = body['items']['premarket']
    assert (pre['state'], pre['state_text'], pre['brief'], pre['run']) == ('due', '尚未生成', None, None)
    assert pre['reason'] == '后台调度器未运行：例行任务只在后端运行期间自动执行。'
    assert (pre['time'], pre['due_at'], pre['can_run']) == ('08:45', '2026-11-17T08:45:00+08:00', False)
    one = client.get('/api/briefs/postmarket', params={'date': '2026-11-16'}).json()
    assert (one['kind'], one['trading_date'], one['state'], one['next_date']) == ('postmarket', '2026-11-16', 'missing', TUESDAY)
    assert client.get('/api/briefs', params={'date': '2026-11-21'}).json()['items']['premarket']['state'] == 'not_trading_day'
    assert client.get('/api/briefs', params={'date': '2026-1-1'}).status_code == 422
    bad = client.get('/api/briefs', params={'date': '2026-02-30'})
    assert bad.status_code == 422 and bad.json() == {'detail': '日期不存在。'}
    assert client.get('/api/briefs/other').status_code == 422
    assert not (directory / 'desk.sqlite3').exists()


def test_storage_trouble_is_503_not_500(portfolio, app, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('disk')
    monkeypatch.setattr(briefs, 'load', broken)
    response = TestClient(app, base_url='http://127.0.0.1').get('/api/briefs')
    assert response.status_code == 503 and response.json() == {'detail': '暂时无法读取简报，请稍后重试。'}


def test_the_routine_status_lists_both_briefs_and_their_settings(portfolio, app):
    body = TestClient(app, base_url='http://127.0.0.1').get('/api/routines/status').json()
    kinds = {item['kind']: item for item in body['routines']}
    assert {'close_snapshot', 'premarket', 'postmarket'} <= set(kinds)
    assert kinds['premarket']['title'] == '盘前简报' and kinds['postmarket']['title'] == '盘后简报'
    assert body['settings']['premarket']['time'] == '08:45' and body['settings']['postmarket']['time'] == '15:45'
    assert body['settings']['commentary'] == {'enabled': False, 'env': {'enabled': 'TRACKER_BRIEF_COMMENTARY'},
                                              'key': {'enabled': 'routines.commentary.enabled'}}


def test_generate_through_the_scheduler_then_read_it(portfolio, app, monkeypatch):
    directory, runtime = portfolio
    write_script(directory, '2026-11-17T08:00:00+08:00', QUOTES)

    @asynccontextmanager
    async def lifespan(application):
        load_kinds()
        running = Scheduler([routine for routine in scheduler.registered() if routine.kind in briefs.KINDS],
                            now=runtime.clock.now, interval=3600)
        running.start()
        for _ in range(500):   # the first tick makes it alive; the requests below need that
            if running.alive():
                break
            await asyncio.sleep(0.01)
        try:
            yield
        finally:
            await running.stop()

    monkeypatch.setattr(app.router, 'lifespan_context', lifespan)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        early = client.post('/api/routines/premarket/run')
        assert early.status_code == 409 and early.json()['detail'] == '还没到盘前简报的运行时间（08:45 之后）。'
        before = client.get('/api/briefs').json()['items']['premarket']
        assert (before['state'], before['reason'], before['run_refusal']) == ('scheduled', '08:45 自动生成。', '08:45 之后才能生成。')
        write_script(directory, '2026-11-17T09:00:00+08:00', QUOTES)
        assert client.get('/api/briefs').json()['items']['premarket']['can_run'] is True
        accepted = client.post('/api/routines/premarket/run')
        assert accepted.status_code == 202 and accepted.json()['run']['status'] == 'pending'
        deadline = wall.monotonic() + 15
        while True:
            item = client.get('/api/briefs').json()['items']['premarket']
            if item['state'] == 'ok':
                break
            assert item['state'] in ('pending', 'running') and wall.monotonic() < deadline, item
            wall.sleep(0.05)
        assert item['brief']['generated_at'] == '2026-11-17T09:00:00+08:00' and item['run']['summary'].startswith('已生成')
        assert (item['can_run'], item['run_refusal']) == (False, '今天的简报已经生成。')
        again = client.post('/api/routines/premarket/run')
        assert again.status_code == 409 and again.json()['detail'] == '今天的盘前简报已经完成，无需再次运行。'
        single = client.get('/api/briefs/premarket').json()
        assert single['brief']['sections'][0]['id'] == 'overnight' and single['latest_date'] == TUESDAY
