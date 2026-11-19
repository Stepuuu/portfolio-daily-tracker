"""The watchlist API: saved directly for the person, only proposed by the agent. Fictional data."""
import asyncio
import json
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from core import agent_tools, proposals, watchlist
from core.market import search
from core.market.runtime import set_market_runtime
from tests.fakes import FakeClock, ScriptedSource, build_ledger, fake_runtime, fx_rate, opening, position, quote


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    clock = FakeClock('2026-11-17 10:00')
    now = clock.now()
    source = ScriptedSource({
        'SHE:000001': quote('SHE:000001', '12.5', prev_close='12', at=now - timedelta(seconds=3), name='示例银行'),
        'HKG:00700': quote('HKG:00700', '300', prev_close='310', at=now - timedelta(seconds=5), name='示例港股'),
    }, {'USD': fx_rate('USD', '7', at=now), 'HKD': fx_rate('HKD', '0.9', at=now)})
    set_market_runtime(fake_runtime(clock, source))
    search.set_symbol_index(search.SymbolIndex(fetchers={}))
    yield tmp_path
    set_market_runtime(None)
    search.set_symbol_index(None)


@pytest.fixture
def client():
    app = FastAPI()
    for router, prefix, tags in features.routers():
        app.include_router(router, prefix=prefix, tags=tags)
    return TestClient(app)


def test_empty_list_does_not_create_the_store(client, desk):
    response = client.get('/api/watchlist')
    assert response.status_code == 200
    # Changed in review round 3: ``holdings_reason`` was added (null unless the ledger could not be read).
    assert response.json() == {'items': [], 'count': 0, 'max_items': watchlist.MAX_ITEMS, 'as_of': None, 'source': None,
                               'holdings_reason': None}
    assert not (desk / 'desk.sqlite3').exists()


def test_add_list_with_quotes_update_and_delete(client, desk):
    created = client.post('/api/watchlist', json={'ticker': 'she:000001', 'note': '  观察\n回踩  '})
    assert created.status_code == 201, created.text
    item = created.json()
    assert item['ticker'] == 'SHE:000001' and item['note'] == '观察 回踩' and item['order'] == 0
    assert item['source'] == 'manual' and item['added_at'] and item['name'] is None
    assert client.post('/api/watchlist', json={'ticker': 'HKG:700'}).json()['ticker'] == 'HKG:00700'
    assert client.post('/api/watchlist', json={'ticker': 'IDX:000300'}).status_code == 201

    listing = client.get('/api/watchlist', params={'quotes': 1}).json()
    assert [entry['ticker'] for entry in listing['items']] == ['SHE:000001', 'HKG:00700', 'IDX:000300']
    assert [entry['order'] for entry in listing['items']] == [0, 1, 2]
    assert listing['count'] == 3 and listing['source'] == 'scripted' and listing['as_of'].endswith('+08:00')
    quotes = client.get('/api/quotes', params={'tickers': 'SHE:000001,HKG:00700,IDX:000300'}).json()['quotes']
    assert [entry['quote'] for entry in listing['items']] == quotes, 'the same quote objects as /api/quotes'
    assert listing['items'][0]['quote']['price'] == '12.5' and listing['items'][0]['quote']['change_pct'] == '4.17'
    assert listing['items'][2]['quote']['price'] is None, 'no scripted quote: unavailable, never 0'
    assert listing['items'][2]['quote']['quote']['status'] == 'unavailable'
    assert all(entry['quote'] is None for entry in client.get('/api/watchlist', params={'quotes': 0}).json()['items'])

    moved = client.patch('/api/watchlist/IDX:000300', json={'order': 0, 'note': '大盘'})
    assert moved.status_code == 200 and moved.json()['order'] == 0 and moved.json()['note'] == '大盘'
    assert [entry['ticker'] for entry in client.get('/api/watchlist?quotes=0').json()['items']] == \
        ['IDX:000300', 'SHE:000001', 'HKG:00700']
    cleared = client.patch('/api/watchlist/HKG:0700', json={'note': None, 'order': 99})
    assert cleared.json()['note'] is None and cleared.json()['order'] == 2, 'any spelling of the HK code finds it'

    removed = client.delete('/api/watchlist/SHE:000001')
    assert removed.status_code == 200 and removed.json()['deleted'] is True
    assert removed.json()['item']['ticker'] == 'SHE:000001'
    assert client.delete('/api/watchlist/SHE:000001').status_code == 404
    assert [(entry['ticker'], entry['order']) for entry in client.get('/api/watchlist?quotes=0').json()['items']] == \
        [('IDX:000300', 0), ('HKG:00700', 1)]


@pytest.mark.parametrize('body, status, words', [
    ({'ticker': '000001'}, 422, '交易所:代码'),
    ({'ticker': 'SHA:000001'}, 422, '指数编号段'),
    ({'ticker': 'SHE:600001'}, 422, '上交所'),
    ({'ticker': 'SHA:60001'}, 422, '6 位数字'),
    ({'ticker': 'BSE:430047'}, 422, '交易所:代码'),
    ({'ticker': 'SHE:200001'}, 422, 'B 股'),
    ({'ticker': 'SHE:000001', 'note': 'x' * 201}, 422, '最多'),
    ({'ticker': 'SHE:000001', 'name': '自己填'}, 422, '不支持的字段'),
    ({}, 422, '请填写代码'),
])
def test_invalid_additions_are_refused(client, desk, body, status, words):
    response = client.post('/api/watchlist', json=body)
    assert response.status_code == status and words in response.json()['detail']
    assert watchlist.list_items() == []


def test_duplicates_and_missing_items(client, desk):
    assert client.post('/api/watchlist', json={'ticker': 'HKG:00700'}).status_code == 201
    duplicate = client.post('/api/watchlist', json={'ticker': 'HKG:700'})
    assert duplicate.status_code == 409 and '已在自选中' in duplicate.json()['detail']
    assert client.patch('/api/watchlist/SHE:000001', json={'note': 'x'}).status_code == 404
    assert client.patch('/api/watchlist/HKG:00700', json={'ticker': 'SHE:000001'}).status_code == 422
    assert client.patch('/api/watchlist/HKG:00700', json={'order': -1}).status_code == 422
    assert client.patch('/api/watchlist/HKG:00700', json={}).status_code == 422


def test_hong_kong_form_follows_the_ledger_and_holdings_are_marked(client, desk):
    build_ledger(desk, [opening('Main', cash={'HKD': '1000'}, positions=[position('HKG:0700', '100', '300', '示例港股')])])
    item = client.post('/api/watchlist', json={'ticker': 'HKG:00700'}).json()
    assert item['ticker'] == 'HKG:0700' and item['name'] == '示例港股'
    clock = FakeClock('2026-11-17 10:00')
    set_market_runtime(fake_runtime(clock, ScriptedSource(
        {'HKG:0700': quote('HKG:0700', '301', prev_close='310', at=clock.now() - timedelta(seconds=5))})))
    [listed] = client.get('/api/watchlist').json()['items']
    assert listed['in_holdings'] is True and listed['quote']['ticker'] == 'HKG:0700'
    assert listed['quote']['price'] == '301'


def test_list_is_bounded(client, desk, monkeypatch):
    monkeypatch.setattr(watchlist, 'MAX_ITEMS', 2)
    for ticker in ('SHE:000001', 'SHE:000002'):
        assert client.post('/api/watchlist', json={'ticker': ticker}).status_code == 201
    full = client.post('/api/watchlist', json={'ticker': 'SHE:000003'})
    assert full.status_code == 422 and '最多 2 个' in full.json()['detail']


def test_agent_proposal_changes_nothing_until_confirmed_and_applies_once(desk):
    proposal = proposals.propose(watchlist.PROPOSAL_KIND, {'ticker': 'HKG:700', 'note': '等回调'}, 'agent:chat')
    assert proposal['payload'] == {'ticker': 'HKG:00700', 'note': '等回调'}
    assert '把 HKG:00700 加入自选' in proposal['summary'] and '不会下单' in proposal['summary']
    assert watchlist.list_items() == []
    applied = proposals.confirm(proposal['id'])
    assert applied['status'] == 'applied' and applied['result'] == {'ticker': 'HKG:00700'}
    assert proposals.confirm(proposal['id'])['result'] == {'ticker': 'HKG:00700'}
    [item] = watchlist.list_items()
    assert item['source'] == 'agent:chat' and item['note'] == '等回调'
    with pytest.raises(proposals.InvalidProposal, match='已在自选中'):
        proposals.propose(watchlist.PROPOSAL_KIND, {'ticker': 'HKG:00700'}, 'agent:chat')


def test_a_proposal_for_an_item_added_meanwhile_cannot_be_confirmed(desk):
    proposal = proposals.propose(watchlist.PROPOSAL_KIND, {'ticker': 'SHE:000001'}, 'agent:chat')
    watchlist.add('SHE:000001')
    with pytest.raises(proposals.InvalidProposal, match='已在自选中'):
        proposals.confirm(proposal['id'])
    assert len(watchlist.list_items()) == 1


def tools_by_name():
    return {tool.name: tool for tool in agent_tools.build_all(None)}


def test_agent_tools_read_the_watchlist_and_only_propose(desk):
    watchlist.add('SHE:000001', '观察')
    tools = tools_by_name()
    listing = asyncio.run(tools['get_watchlist'].function())
    assert listing['items'][0]['ticker'] == 'SHE:000001' and listing['items'][0]['quote']['price'] == '12.5'
    assert listing['as_of'] and '不可用' in listing['note']
    json.dumps(listing, ensure_ascii=False)
    result = asyncio.run(tools['propose_watchlist_add'].function(ticker='HKG:700', note='跟踪'))
    assert result['status'] == 'awaiting_user_confirmation' and result['proposal_id']
    assert [item['ticker'] for item in watchlist.list_items()] == ['SHE:000001'], 'nothing added by the tool'
    refused = asyncio.run(tools['propose_watchlist_add'].function(ticker='000001'))
    assert refused['status'] == 'not_proposed' and '交易所:代码' in refused['error']


def test_demo_mode_reads_work_writes_are_refused_and_nothing_is_created(tmp_path, monkeypatch):
    from create_demo import create_demo
    import backend.main as main
    create_demo(tmp_path / 'portfolio', ledger=True, today=date(2026, 11, 17))   # a fixed day, never the runner's
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path / 'portfolio'))
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    set_market_runtime(None)
    search.set_symbol_index(None)
    store = tmp_path / 'portfolio' / 'desk.sqlite3'
    existed = store.exists()
    try:
        client = TestClient(main.app, base_url='http://127.0.0.1')
        # Seeded by core/demo_seed.py (tests/test_demo_seed.py checks the rest of the desk).
        assert [item['ticker'] for item in client.get('/api/watchlist').json()['items']] == [
            'NASDAQ:DEMOW', 'NYSE:DEMOX', 'NASDAQ:DEMOY', 'NYSE:DEMOV']
        found = client.get('/api/instruments/search', params={'q': '示例宽基'}).json()
        assert found['results'][0]['ticker'] == 'SHA:DEMO' and found['results'][0]['in_holdings'] is True
        assert found['index_available'] is False and '演示模式' in found['index_reason']
        assert client.post('/api/watchlist', json={'ticker': 'SHE:000001'}).status_code == 403
        assert client.patch('/api/watchlist/SHE:000001', json={'note': 'x'}).status_code == 403
        assert client.delete('/api/watchlist/SHE:000001').status_code == 403
        assert client.post('/api/instruments/aliases', json={'ticker': 'NYSE:EXAMPLE', 'symbol': 'EXAMPLE'}).status_code == 403
        assert store.exists() == existed
    finally:
        set_market_runtime(None)
        search.set_symbol_index(None)


# -- review findings: one identity per US class share ---------------------------

def test_class_share_spellings_are_one_watchlist_item(client, desk):
    assert client.post('/api/watchlist', json={'ticker': 'nyse:brk.b'}).json()['ticker'] == 'NYSE:BRK.B'
    again = client.post('/api/watchlist', json={'ticker': 'NYSE:BRK-B'})
    assert again.status_code == 409 and 'NYSE:BRK.B' in again.json()['detail']
    assert [item['ticker'] for item in client.get('/api/watchlist', params={'quotes': 0}).json()['items']] == ['NYSE:BRK.B']
    assert client.post('/api/watchlist', json={'ticker': 'NASDAQ:BRK.B'}).status_code == 201, 'another exchange is another item'


def test_class_share_is_stored_in_one_spelling_and_found_through_either(client, desk):
    assert client.post('/api/watchlist', json={'ticker': 'NYSE:BRK-B'}).json()['ticker'] == 'NYSE:BRK.B'
    assert client.patch('/api/watchlist/NYSE:BRK-B', json={'note': '观察'}).json()['note'] == '观察'
    assert client.patch('/api/watchlist/nyse:brk.b', json={'note': '再看'}).json()['note'] == '再看'
    deleted = client.delete('/api/watchlist/NYSE:BRK-B')
    assert deleted.status_code == 200 and deleted.json()['item']['ticker'] == 'NYSE:BRK.B'
    assert client.get('/api/watchlist', params={'quotes': 0}).json()['count'] == 0


def test_class_share_follows_the_spelling_the_ledger_already_uses(client, desk):
    build_ledger(desk, [opening('Main', cash={'USD': '10'}, positions=[position('NYSE:BRK-B', '1', '10')])])
    item = client.post('/api/watchlist', json={'ticker': 'NYSE:BRK.B'}).json()
    assert item['ticker'] == 'NYSE:BRK-B'
    assert client.get('/api/watchlist', params={'quotes': 0}).json()['items'][0]['in_holdings'] is True


def test_a_proposal_for_the_other_spelling_is_a_duplicate(client, desk):
    watchlist.add('NYSE:BRK.B')
    with pytest.raises(proposals.InvalidProposal, match='已在自选中'):
        proposals.propose(watchlist.PROPOSAL_KIND, {'ticker': 'NYSE:BRK-B'}, 'agent:chat')


def test_search_marks_either_spelling_as_watched(client, desk):
    watchlist.add('NYSE:BRK.B')
    rows = client.get('/api/instruments/search', params={'q': 'NYSE:BRK-B'}).json()['results']
    assert [(row['ticker'], row['in_watchlist']) for row in rows] == [('NYSE:BRK.B', True)]


# -- review round 3: a ledger writer never holds up the list -----------------------

def hold_ledger(path, mode, seconds):
    """Another connection keeps ``BEGIN <mode>`` on the ledger for ``seconds``; returns the releasing thread."""
    import sqlite3
    import threading
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute(f'BEGIN {mode}')

    def release():
        holder.execute('ROLLBACK')
        holder.close()
    timer = threading.Timer(seconds, release)
    timer.start()
    return timer


@pytest.mark.parametrize('mode', ['IMMEDIATE', 'EXCLUSIVE'])
def test_a_ledger_writer_never_holds_up_the_list(client, desk, mode):
    import time
    build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, positions=[position('SHE:000001', '100', '10')])])
    watchlist.add('SHE:000001')
    timer = hold_ledger(desk / 'ledger.sqlite3', mode, 3.1)
    try:
        started = time.monotonic()
        response = client.get('/api/watchlist', params={'quotes': 0})
        assert time.monotonic() - started < 1.6, 'no GET waits behind a ledger writer for more than about a second'
    finally:
        timer.join(10)
    assert response.status_code == 200
    payload = response.json()
    [item] = payload['items']
    if mode == 'IMMEDIATE':
        # A writer's open transaction does not stop a reader: the committed holdings are read.
        assert item['in_holdings'] is True and payload['holdings_reason'] is None
    else:
        # Shut out: holdings are unknown, said so, never "not held".
        assert item['in_holdings'] is None and '持仓' in payload['holdings_reason']


def test_reading_the_list_never_initialises_the_ledger(client, desk):
    import sqlite3
    (desk / 'ledger.sqlite3').touch()
    assert client.get('/api/watchlist', params={'quotes': 0}).status_code == 200
    assert (desk / 'ledger.sqlite3').stat().st_size == 0
    sqlite3.connect(desk / 'ledger.sqlite3').close()
