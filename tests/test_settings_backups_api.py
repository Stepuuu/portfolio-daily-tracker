"""What 设置 reads and the legacy writes it no longer calls. Fictional data only.

The v3.2 portfolio's writes (``POST /api/settings/cash``, ``/api/portfolio/*``) answer 409 with the reason
once a ledger is active; the holiday table's years and the close snapshot's switch are readable; ``GET /api/desk/backup.sqlite3`` is one
consistent copy of ``desk.sqlite3`` (also in the demo, never creating the store); a rejected lesson or profile
proposal can be deleted, nothing else, and the list can be narrowed to those kinds.
"""
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from backend.api import desk as desk_api
from backend.api import portfolio as portfolio_api
from backend.api import proposals as proposals_api
from backend.api import routines as routines_api
from backend.api import settings as settings_api
from core import desk_store, lessons, notify, proposals, watchlist
from core.ledger import Ledger
from core.market.calendar import load_holidays
from core.market.runtime import set_market_runtime
from tests.fakes import FakeClock, fake_runtime


@pytest.fixture
def directory(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    return tmp_path


@pytest.fixture
def client(directory):
    app = FastAPI()
    app.include_router(settings_api.router, prefix='/api/settings')
    app.include_router(portfolio_api.router, prefix='/api/portfolio')
    app.include_router(desk_api.router, prefix='/api/desk')
    app.include_router(proposals_api.router, prefix='/api/proposals')
    app.include_router(routines_api.router, prefix='/api/routines')
    return TestClient(app)


def activate_ledger(directory):
    ledger = Ledger(directory / 'ledger.sqlite3')
    ledger.confirm(ledger.propose([{'kind': 'opening', 'date': '2026-11-16', 'account': 'Example',
                                    'cash_balances': {'CNY': '1000'}, 'contributed_cny': '1000'}])['id'])


# -- the legacy portfolio writes -----------------------------------------------------------------

def test_with_an_active_ledger_every_legacy_write_is_a_409_with_the_reason(client, directory):
    activate_ledger(directory)
    cash = client.post('/api/settings/cash', json={'cash': 100})
    assert cash.status_code == 409 and cash.json()['detail'] == settings_api.LEDGER_ACTIVE_CASH
    assert '入金' in cash.json()['detail']
    for response in (client.post('/api/portfolio/add', json={'symbol': '600001', 'name': '示例', 'quantity': 100, 'cost_price': 1}),
                     client.put('/api/portfolio/600001', json={'quantity': 1}),
                     client.delete('/api/portfolio/600001')):
        assert response.status_code == 409 and '账本已启用' in response.json()['detail']


def test_without_a_ledger_the_cash_route_still_needs_the_assistant_service(client, directory, monkeypatch):
    # No 500 and no write: the guard comes first, then the service the old route always needed.
    monkeypatch.chdir(directory)  # the service lookup imports the application, which creates data directories
    assert client.post('/api/settings/cash', json={'cash': 100}).status_code == 503
    assert not (directory / 'ledger.sqlite3').exists()


# -- the calendar's coverage ---------------------------------------------------------------------

def test_the_calendar_names_the_years_of_each_markets_holiday_table(client):
    set_market_runtime(fake_runtime(FakeClock('2026-11-17 10:32')))
    try:
        body = client.get('/api/settings/calendar').json()
    finally:
        set_market_runtime(None)
    table = load_holidays()
    assert body['today'] == '2026-11-17'
    assert set(body['markets']) == {'CN', 'HK', 'US'}
    for market, entry in body['markets'].items():
        assert entry['years'] == sorted(table.get(market, {})), market
        assert entry['label']
    assert body['markets']['CN']['label'] == 'A 股'


def test_an_unreadable_calendar_is_a_chinese_503(client, monkeypatch):
    import core.market.runtime as runtime

    def broken():
        raise RuntimeError('boom')
    monkeypatch.setattr(runtime, 'get_market_runtime', broken)
    response = client.get('/api/settings/calendar')
    assert response.status_code == 503 and '交易日历' in response.json()['detail']


@pytest.mark.parametrize('value, enabled', [(None, True), ('0', False), ('off', False), ('1', True)])
def test_the_routines_status_says_whether_the_close_snapshot_is_on(client, monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv('TRACKER_CLOSE_SNAPSHOT', raising=False)
    else:
        monkeypatch.setenv('TRACKER_CLOSE_SNAPSHOT', value)
    settings = client.get('/api/routines/status').json()['settings']
    assert settings['close_snapshot'] == {'enabled': enabled, 'time': '15:40', 'env': {'enabled': 'TRACKER_CLOSE_SNAPSHOT'}}
    assert settings['premarket']['time'] and settings['postmarket']['time']


# -- the desk store backup -----------------------------------------------------------------------

def test_the_desk_backup_is_registered_as_a_feature():
    mounted = {prefix: router for router, prefix, _tags in features.routers()}
    assert mounted.get('/api/desk') is desk_api.router


def test_without_a_desk_store_the_backup_is_a_404_and_creates_nothing(client, directory):
    response = client.get('/api/desk/backup.sqlite3')
    assert response.status_code == 404 and response.json()['detail'] == desk_api.NO_DESK_STORE
    assert list(directory.iterdir()) == []


def test_the_backup_is_a_whole_sqlite_file_with_the_desk_tables(client, directory, tmp_path_factory):
    watchlist.add('SHA:600519', name='示例宽基')
    notify.publish('alert', 'info', '示例', '示例正文')
    response = client.get('/api/desk/backup.sqlite3')
    assert response.status_code == 200
    assert response.headers['content-disposition'] == 'attachment; filename="desk-backup.sqlite3"'
    assert response.content.startswith(b'SQLite format 3\x00')
    copy = tmp_path_factory.mktemp('restore') / 'desk.sqlite3'
    copy.write_bytes(response.content)
    with sqlite3.connect(copy) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'   # one file, no -wal needed
        assert [row[0] for row in db.execute('SELECT ticker FROM watchlist')] == ['SHA:600519']
        assert db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0] == 1
    assert {path.name for path in directory.iterdir()} <= {'desk.sqlite3', 'desk.sqlite3-wal', 'desk.sqlite3-shm'}


def test_the_backup_holds_the_last_committed_state_while_a_write_is_open(client, directory, tmp_path_factory):
    watchlist.add('SHA:600519', name='示例宽基')
    writer = sqlite3.connect(directory / 'desk.sqlite3', isolation_level=None)
    writer.execute('BEGIN IMMEDIATE')
    writer.execute("INSERT INTO watchlist(ticker, name, note, sort_order, source, added_at, updated_at) "
                   "VALUES ('SHA:600036', '示例银行', NULL, 2, 'manual', '2026-11-17T00:00:00+00:00', '2026-11-17T00:00:00+00:00')")
    try:
        response = client.get('/api/desk/backup.sqlite3')
    finally:
        writer.execute('ROLLBACK')
        writer.close()
    assert response.status_code == 200
    copy = tmp_path_factory.mktemp('restore') / 'desk.sqlite3'
    copy.write_bytes(response.content)
    with sqlite3.connect(copy) as db:
        assert [row[0] for row in db.execute('SELECT ticker FROM watchlist')] == ['SHA:600519']


def test_the_demo_can_download_its_desk_backup(directory, monkeypatch):
    monkeypatch.chdir(directory)
    from backend.main import app

    watchlist.add('SHA:600519', name='示例宽基')
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    response = TestClient(app, base_url='http://127.0.0.1').get('/api/desk/backup.sqlite3')
    assert response.status_code == 200 and response.content.startswith(b'SQLite format 3\x00')


# -- forgetting a rejected lesson or profile proposal --------------------------------------------

@pytest.fixture
def memory(directory):
    from core.memory import MemoryManager
    manager = MemoryManager(data_file=str(directory / 'memory.json'))
    lessons.use_memory_manager(lambda: manager)
    yield manager
    lessons.use_memory_manager(None)


LESSON = {'description': '追涨停买入后亏损', 'lesson': '不追涨停', 'type': 'failure', 'date': '2026-11-16'}


def test_a_rejected_lesson_can_be_forgotten_and_proposed_again(client, memory):
    first = lessons.propose(LESSON)
    client.delete(f"/api/proposals/{first['id']}")
    with pytest.raises(proposals.ProposalError, match='放弃过'):
        lessons.propose(LESSON)
    listed = client.get('/api/proposals', params={'status': 'rejected', 'kind': ['lesson', 'profile']}).json()
    assert [item['id'] for item in listed['items']] == [first['id']]
    # A second 放弃 changes nothing: only the explicit route forgets the decision.
    assert client.delete(f"/api/proposals/{first['id']}").json()['status'] == 'rejected'
    forgotten = client.delete(f"/api/proposals/{first['id']}/decision")
    assert forgotten.status_code == 200
    assert forgotten.json() == {'forgotten': True, 'proposal': {**listed['items'][0]}}
    assert client.get(f"/api/proposals/{first['id']}").status_code == 404
    again = lessons.propose(LESSON)
    assert again['id'] != first['id'] and again['status'] == 'pending'


def test_a_rejected_profile_change_can_be_forgotten(client, memory):
    proposal = lessons.propose_profile('risk_tolerance', 'aggressive')
    proposals.reject(proposal['id'])
    with pytest.raises(proposals.ProposalError):
        lessons.propose_profile('risk_tolerance', 'aggressive')
    assert client.delete(f"/api/proposals/{proposal['id']}/decision").status_code == 200
    assert lessons.propose_profile('risk_tolerance', 'aggressive')['status'] == 'pending'


def test_only_rejected_memory_proposals_can_be_forgotten(client, memory, monkeypatch):
    monkeypatch.setattr(proposals, '_kinds', dict(proposals._kinds))
    pending = lessons.propose(LESSON)
    response = client.delete(f"/api/proposals/{pending['id']}/decision")
    assert response.status_code == 409 and response.json()['detail'] == proposals.FORGET_ONLY_REJECTED
    client.post(f"/api/proposals/{pending['id']}/confirm")
    response = client.delete(f"/api/proposals/{pending['id']}/decision")
    assert response.status_code == 409 and response.json()['detail'] == proposals.FORGET_ONLY_REJECTED
    assert client.get(f"/api/proposals/{pending['id']}").json()['status'] == 'applied'

    proposals.register_kind('example_note', lambda payload: payload, lambda payload, proposal: None,
                            lambda payload: '示例方案')
    other = proposals.propose('example_note', {'text': '示例'}, 'agent:chat')
    proposals.reject(other['id'])
    response = client.delete(f"/api/proposals/{other['id']}/decision")
    assert response.status_code == 409 and response.json()['detail'] == proposals.FORGET_ONLY_MEMORY
    assert client.get(f"/api/proposals/{other['id']}").json()['status'] == 'rejected'
    assert client.delete(f"/api/proposals/{'0' * 32}/decision").status_code == 404


def test_the_kind_filter_keeps_only_those_kinds(client, memory):
    lesson = lessons.propose(LESSON)
    profile = lessons.propose_profile('trading_style', 'value')
    assert [item['id'] for item in client.get('/api/proposals', params={'kind': 'profile'}).json()['items']] == [profile['id']]
    both = client.get('/api/proposals', params={'kind': ['lesson', 'profile']}).json()
    assert {item['id'] for item in both['items']} == {lesson['id'], profile['id']} and both['pending'] == 2
    assert proposals.list('pending', kinds=[]) == []


def test_the_demo_cannot_forget(client, memory, monkeypatch):
    proposal = lessons.propose(LESSON)
    proposals.reject(proposal['id'])
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    with pytest.raises(proposals.ReadOnlyDemo):
        proposals.forget(proposal['id'])
    assert client.delete(f"/api/proposals/{proposal['id']}/decision").status_code == 422
    monkeypatch.delenv('TRACKER_DEMO_MODE')
    assert proposals.get(proposal['id'])['status'] == 'rejected'


def test_forgetting_deletes_the_proposals_recorded_effects(client, memory, directory):
    proposal = lessons.propose(LESSON)
    proposals.reject(proposal['id'])
    with desk_store.side_database(proposals.EFFECTS_FILE) as db:
        db.execute(proposals._EFFECTS_TABLE)
        db.execute("INSERT INTO effects VALUES (?, 'memory_lesson', 'null', '2026-11-16T00:00:00+00:00')", (proposal['id'],))
    assert client.delete(f"/api/proposals/{proposal['id']}/decision").status_code == 200
    with desk_store.side_database(proposals.EFFECTS_FILE) as db:
        assert db.execute('SELECT COUNT(*) FROM effects WHERE proposal_id=?', (proposal['id'],)).fetchone()[0] == 0
