"""``create_demo --ledger``: every desk page has data, consistent with the scripted quotes, read-only.

The demo is fictional: invented tickers, names and prices. The seed writes through the features' own public
functions (``core/demo_seed.py``); these tests read the result through the HTTP API in demo mode.
"""
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from core import demo_seed, desk_store
from core.alerts import monitor
from core.market.calendar import CN, SHANGHAI, SessionClock, load_holidays
from core.market.runtime import set_market_runtime

# Fixed, so that the dates below read the same on every run. A Tuesday.
TODAY = date(2026, 11, 17)
needs_backend = pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')


def fingerprint(directory):
    """Relative path -> (size, sha256) of every file under ``directory``."""
    return {str(path.relative_to(directory)): (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(Path(directory).rglob('*')) if path.is_file()}


@pytest.fixture
def demo(tmp_path, monkeypatch):
    from create_demo import create_demo
    directory = tmp_path / 'portfolio'
    create_demo(directory, ledger=True, today=TODAY)
    monkeypatch.setenv('PORTFOLIO_DIR', str(directory))
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    monkeypatch.delenv('TRACKER_ALERTS_ENABLED', raising=False)
    set_market_runtime(None)
    yield directory
    set_market_runtime(None)


@pytest.fixture
def client(demo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # importing the application creates data directories in the working directory
    from fastapi.testclient import TestClient
    import backend.main as main
    # No lifespan: the real application only answers loopback Host headers.
    return TestClient(main.app, base_url='http://127.0.0.1')


# -- dates --------------------------------------------------------------------

@pytest.mark.parametrize('today', [date(2026, 11, 17), date(2026, 10, 8), date(2026, 1, 5), date(2027, 2, 15)])
def test_history_days_are_weekdays_before_today_that_the_holiday_table_does_not_close(today):
    days = demo_seed.history_days(today)
    assert sorted(days) == list(range(1, demo_seed.HISTORY_DAYS + 1))
    assert all(days[k] > days[k + 1] for k in range(1, demo_seed.HISTORY_DAYS))
    assert days[1] < today
    holidays = load_holidays()[CN]
    for day in days.values():
        assert day.weekday() < 5 and day not in holidays.get(day.year, frozenset())


def test_history_days_skip_the_national_day_closure_and_refuse_to_precede_the_last_snapshot():
    days = demo_seed.history_days(date(2026, 10, 8))
    assert [days[k].isoformat() for k in (1, 2, 3, 4)] == ['2026-09-30', '2026-09-29', '2026-09-28', '2026-09-24']
    # A history that would start on or before the last snapshot is refused with the reason; it is never
    # moved past the requested day (that dated events in the future and left Today empty).
    with pytest.raises(ValueError, match='2026-08-28'):
        demo_seed.history_days(date(2026, 8, 31), last_snapshot=date(2026, 8, 28))
    assert demo_seed.history_days(date(2026, 9, 18), last_snapshot=date(2026, 8, 28))[12] == date(2026, 9, 2)


@pytest.mark.parametrize('today', [date(2026, 1, 5), date(2026, 8, 31), date(2026, 9, 18), TODAY, date(2027, 2, 15)])
def test_the_demo_is_rebased_to_end_before_the_day_it_is_made_for(tmp_path, today):
    from create_demo import create_demo
    from core.ledger import Ledger
    directory = tmp_path / 'portfolio'
    assert create_demo(directory, ledger=True, today=today) == 20
    days = demo_seed.history_days(today)
    snapshots = sorted(path.stem for path in (directory / 'snapshots').glob('*.json'))
    holdings = sorted(path.stem for path in (directory / 'holdings').glob('*.json'))
    assert snapshots == holdings and len(snapshots) == 20
    # Four whole weeks, Monday to Friday, ending on the Friday before the seeded history starts.
    first, last = date.fromisoformat(snapshots[0]), date.fromisoformat(snapshots[-1])
    assert first.weekday() == 0 and last.weekday() == 4 and (last - first).days == 25
    assert last < days[12] and (days[12] - last).days <= 7
    # Nothing is dated in the future: the opening balance is the first snapshot day, every event after it
    # and before the day the demo is made for.
    events = Ledger(directory / 'ledger.sqlite3').history()
    assert [event['date'] for event in events if event['kind'] == 'opening'] == [first.isoformat()] * 2
    later = [event['date'] for event in events if event['kind'] != 'opening']
    assert later and min(later) > last.isoformat() and max(later) < today.isoformat()
    with sqlite3.connect(directory / 'desk.sqlite3') as db:
        stamps = [row[0] for row in db.execute('SELECT created_at FROM trade_plans')]
    assert stamps and max(stamp[:10] for stamp in stamps) < today.isoformat()


def test_the_demo_script_takes_the_day_to_make_it_for(tmp_path):
    import subprocess
    script = Path(__file__).resolve().parents[1] / 'engine/scripts/create_demo.py'
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    target = tmp_path / 'early'
    subprocess.run([sys.executable, str(script), '--output', str(target), '--ledger', '--today', '2026-01-05'],
                   check=True, env=env, cwd=tmp_path, capture_output=True)
    from core.ledger import Ledger
    dates = [event['date'] for event in Ledger(target / 'ledger.sqlite3').history()]
    assert dates and max(dates) < '2026-01-05'


def _shanghai_day(value):
    """The Beijing date of a stored date, ISO time (``Z`` or an offset) or POSIX timestamp."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, SHANGHAI).date()
    text = str(value)
    if len(text) == 10:
        return date.fromisoformat(text)
    return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(SHANGHAI).date()


@pytest.mark.parametrize('today', [date(2026, 1, 5), TODAY])
def test_nothing_the_demo_writes_is_dated_after_the_day_it_is_made_for(tmp_path, today):
    """Every row of the desk, the proposal included, and every ledger event: on or before ``today``.

    Only what records when something happened is checked: a plan's review date and a proposal's expiry
    are deadlines, and ``schema_versions`` is the store's own bookkeeping.
    """
    from create_demo import create_demo
    from core.ledger import Ledger
    directory = tmp_path / 'portfolio'
    create_demo(directory, ledger=True, today=today)
    late = []
    with sqlite3.connect(directory / 'desk.sqlite3') as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            if table == 'schema_versions' or table.startswith('sqlite_'):
                continue
            for column in [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]:
                if not (column.endswith(('_at', '_ts', '_date')) or column == 'date') or column.startswith('expires'):
                    continue
                for (value,) in db.execute(f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
                    if _shanghai_day(value) > today:
                        late.append(f'{table}.{column} = {value}')
    assert late == []
    assert max(event['date'] for event in Ledger(directory / 'ledger.sqlite3').history()) < today.isoformat()
    # The pending proposal is still there to confirm.
    with sqlite3.connect(directory / 'desk.sqlite3') as db:
        assert db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0] == 1


@pytest.mark.parametrize('today', [date.min, date(1, 1, 20), date(1, 2, 5), date(9999, 12, 25), date.max])
def test_a_day_without_room_for_the_demo_is_refused_with_the_reason_before_anything_is_written(tmp_path, today):
    from create_demo import create_demo
    target = tmp_path / 'demo'
    with pytest.raises(ValueError, match=f'cannot make the demo for {today.isoformat()}'):
        create_demo(target, ledger=True, today=today)
    assert not target.exists()


def test_the_demo_script_refuses_such_a_day_as_a_usage_error(tmp_path):
    import subprocess
    script = Path(__file__).resolve().parents[1] / 'engine/scripts/create_demo.py'
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    result = subprocess.run([sys.executable, str(script), '--output', str(tmp_path / 'early'), '--ledger', '--today', '0001-01-05'],
                            env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2, result.stderr
    assert 'cannot make the demo for 0001-01-05' in result.stderr
    assert 'Traceback' not in result.stderr and 'OverflowError' not in result.stderr
    assert not (tmp_path / 'early').exists()


def test_the_history_is_placed_relative_to_the_day_the_demo_is_made(demo):
    from core.ledger import Ledger
    ledger = Ledger(demo / 'ledger.sqlite3')
    history = [(event['date'], event['kind'], event.get('ticker'), event['account']) for event in ledger.history()]
    # The opening balance is the first of the demo's daily snapshots, which end on the Friday before the
    # history starts: D12 is Friday 2026-10-30, so the snapshots run Monday 2026-09-28 to Friday 2026-10-23
    # (four whole weeks, 20 weekdays; it was a fixed 2026-08-03 before the demo was rebased on its day).
    assert history[:2] == [('2026-09-28', 'opening', None, '长期账户'), ('2026-09-28', 'opening', None, '研究账户')]
    assert history[2:] == [
        ('2026-11-02', 'buy', 'SHE:DEMOZ', '长期账户'),      # D11
        ('2026-11-05', 'buy', 'SHA:DEMO', '长期账户'),       # D8
        ('2026-11-06', 'dividend', 'SHA:DEMO', '长期账户'),  # D7
        ('2026-11-10', 'sell', 'SHE:DEMOZ', '长期账户'),     # D5
        ('2026-11-11', 'fee', None, '长期账户'),             # D4
        ('2026-11-11', 'interest', None, '研究账户')]        # D4
    # Trades stay out of the last two sessions, which the Today page treats as today's.
    assert max(event['date'] for event in ledger.history()) <= demo_seed.history_days(TODAY)[4].isoformat()


# -- the files ----------------------------------------------------------------

def test_the_default_demo_has_no_desk_and_only_its_holdings_are_quoted(tmp_path):
    from create_demo import create_demo
    create_demo(tmp_path / 'plain')
    assert sorted(path.name for path in (tmp_path / 'plain').iterdir()) == [
        'config.json', 'demo.json', 'fake_quotes.json', 'holdings', 'snapshots']
    assert set(json.loads((tmp_path / 'plain/fake_quotes.json').read_text())['quotes']) == {'SHA:DEMO', 'NASDAQ:DEMO'}


def test_the_seeded_desk_is_one_plain_file_that_readers_leave_alone(demo):
    assert sorted(path.name for path in demo.iterdir()) == [
        'config.json', 'demo.json', 'desk.sqlite3', 'fake_quotes.json', 'holdings', 'ledger.sqlite3', 'snapshots']
    with sqlite3.connect(demo / 'desk.sqlite3') as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'   # no -wal / -shm to create
    before, names = fingerprint(demo), sorted(path.name for path in demo.iterdir())
    from core import notify, plans, proposals, watchlist
    from core.alerts import events, rules
    assert (len(watchlist.list_items()), len(rules.list_rules()), len(events.list_events()['items']),
            len(plans.list_plans()), notify.unread_count(), proposals.pending_count()) == (4, 6, 1, 3, 1, 1)
    assert fingerprint(demo) == before and sorted(path.name for path in demo.iterdir()) == names


def test_every_held_ticker_has_a_scripted_quote_and_the_new_ones_are_quoted_too(demo):
    from core.ledger import active_ledger
    script = json.loads((demo / 'fake_quotes.json').read_text())
    held = {ticker for account in active_ledger(demo).state()['accounts'].values() for ticker in account['positions']}
    assert held == {'SHA:DEMO', 'NASDAQ:DEMO'} and held <= set(script['quotes'])
    assert set(script['quotes']) == held | {'NASDAQ:DEMOW', 'NYSE:DEMOX', 'NYSE:DEMOV', 'SHE:DEMOZ'}
    assert 'NASDAQ:DEMOY' not in script['quotes']   # the watched ticker without a quote, on purpose
    assert all(quote['age_seconds'] == 3 for quote in script['quotes'].values()), 'times stay relative'
    assert 'now' not in script


# -- the pages ----------------------------------------------------------------

@needs_backend
def test_every_page_has_data_that_agrees_with_the_scripted_prices_and_nothing_changes(demo, client):
    before = fingerprint(demo)
    script = json.loads((demo / 'fake_quotes.json').read_text())['quotes']

    # Today: the ledger at the scripted prices. Hand arithmetic: see tests/test_today_api.py (124349.12).
    today = client.get('/api/today').json()
    rows = {row['ticker']: row for account in today['accounts'] for row in account['positions']}
    assert today['totals']['total_cny'] == '124349.12' and today['totals']['unavailable'] == []
    assert {name: row['price'] for name, row in rows.items()} == {'SHA:DEMO': '60', 'NASDAQ:DEMO': '117.5'}
    assert rows['SHA:DEMO']['quantity'] == '1200'
    # 1200 x 60 = 72000 of 124349.12
    assert rows['SHA:DEMO']['market_value_cny'] == '72000.00' and rows['SHA:DEMO']['weight_pct'] == '57.90'

    # Watchlist: three quoted tickers and one that deliberately has none.
    watch = client.get('/api/watchlist').json()
    assert [(item['ticker'], item['in_holdings']) for item in watch['items']] == [
        ('NASDAQ:DEMOW', False), ('NYSE:DEMOX', False), ('NASDAQ:DEMOY', False), ('NYSE:DEMOV', False)]
    assert all(item['note'] and item['name'] for item in watch['items'])
    for item in watch['items']:
        if item['ticker'] == 'NASDAQ:DEMOY':
            assert item['quote']['price'] is None and item['quote']['quote']['status'] == 'unavailable'
            assert 'fake_quotes.json' in item['quote']['quote']['reason']
        else:
            assert item['quote']['price'] == script[item['ticker']]['price']
            assert item['quote']['quote']['source'] == 'fake'

    # Alert rules: the thresholds against the quotes.
    listed = client.get('/api/alerts/rules').json()['items']
    by_id = {rule['id']: rule for rule in listed}
    assert [(rule['id'], rule['ticker'], rule['kind'], rule['threshold'], rule['source'], rule['enabled'])
            for rule in listed] == [
        (1, 'SHE:DEMOZ', 'stop_loss', '11.2', 'plan:1', False), (2, 'SHE:DEMOZ', 'target_price', '13.5', 'plan:1', False),
        (3, 'SHA:DEMO', 'stop_loss', '54', 'plan:2', True), (4, 'SHA:DEMO', 'target_price', '66', 'plan:2', True),
        (5, 'NASDAQ:DEMO', 'day_change_pct', '-4', 'manual', True),
        (6, 'SHA:DEMO', 'position_weight_max', '50', 'manual', True)]
    price = float(rows['SHA:DEMO']['price'])
    assert float(by_id[3]['threshold']) < price < float(by_id[4]['threshold'])       # armed: not reached
    assert by_id[3]['state']['armed'] and by_id[4]['state']['armed']
    assert (by_id[3]['market']['distance'], by_id[4]['market']['distance']) == ('6', '6')
    assert float(by_id[5]['market']['change_pct']) > float(by_id[5]['threshold']) and by_id[5]['state']['armed']
    assert by_id[6]['market']['weight_pct'] == rows['SHA:DEMO']['weight_pct']        # the rule sees what Today shows
    assert float(by_id[6]['market']['weight_pct']) >= float(by_id[6]['threshold'])   # reached, and fired
    assert by_id[6]['state']['armed'] is False and by_id[6]['state']['fire_count_today'] == 1
    assert by_id[6]['state']['last_trading_date'] == '2026-11-12'

    # The fired alert: the price (here: the share of the portfolio) really is beyond the threshold.
    [event] = client.get('/api/alerts/events').json()['items']
    assert (event['rule_id'], event['ticker'], event['kind'], event['delivery'], event['n']) == (
        6, 'SHA:DEMO', 'position_weight_max', 'notified', 1)
    assert event['price'] == script['SHA:DEMO']['price'] and event['prev_close'] == script['SHA:DEMO']['prev_close']
    assert float(event['value']) >= float(event['threshold']) and abs(float(event['value']) - 57.9) < 0.01
    assert (event['fired_at'], event['trading_date'], event['quote_status']) == (
        '2026-11-12T10:42:00+08:00', '2026-11-12', 'live')
    assert datetime.fromisoformat(event['fired_at']).astimezone(SHANGHAI).weekday() < 5

    # The bell: that same alert, unread.
    inbox = client.get('/api/notifications').json()
    [alert] = inbox['items']
    assert inbox['unread'] == 1 and alert['read_at'] is None and alert['kind'] == 'alert'
    assert alert['seq'] == event['notification_seq'] and alert['title'] == event['title']
    assert alert['data']['rule_id'] == 6 and alert['created_at'] == '2026-11-12T02:42:00+00:00'

    # Plans: one active, one draft on a watched ticker, one closed with its reason.
    items = {plan['id']: plan for plan in client.get('/api/plans').json()['items']}
    assert {pid: (plan['status'], plan['ticker']) for pid, plan in items.items()} == {
        1: ('closed', 'SHE:DEMOZ'), 2: ('active', 'SHA:DEMO'), 3: ('draft', 'NYSE:DEMOX')}
    assert items[1]['close_reason'] and items[3]['ticker'] in {item['ticker'] for item in watch['items']}
    active, draft, closed = (client.get(f'/api/plans/{pid}').json() for pid in (2, 3, 1))
    # The plan's market block is the Today row and the alert rules' numbers.
    market = active['market']
    assert (market['price'], market['position_quantity'], market['weight_pct']) == (
        rows['SHA:DEMO']['price'], rows['SHA:DEMO']['quantity'], rows['SHA:DEMO']['weight_pct'])
    assert market['quantity_vs_plan'] == '0' and active['planned_quantity'] == '1200'
    assert (market['distance_to_stop'], market['distance_to_target']) == (
        by_id[3]['market']['distance'], by_id[4]['market']['distance'])
    assert market['stop_reached'] is False and market['target_reached'] is False
    assert [(rule['id'], rule['enabled']) for rule in active['alerts']] == [(3, True), (4, True)]
    assert active['alert_notes'] == [] and all(rule['source'] == 'plan:2' for rule in active['alerts'])
    assert draft['alerts'] == [] and draft['market']['price'] == script['NYSE:DEMOX']['price']
    assert draft['market']['position_quantity'] == '0'
    assert float(draft['entry_low']) <= float(draft['market']['price']) <= float(draft['entry_high'])
    assert [rule['enabled'] for rule in closed['alerts']] == [False, False]
    assert closed['market']['position_quantity'] == '0' and closed['market']['target_reached'] is True
    # Plan links: the ledger events booked under each plan, with no deviation from it.
    assert [(link['kind'], link['relation'], link['ticker'], link['event_date'], link['deviations'])
            for link in active['links']] == [('buy', 'add', 'SHA:DEMO', '2026-11-05', [])]
    assert [(link['kind'], link['relation'], link['event_date'], link['deviations']) for link in closed['links']] == [
        ('buy', 'entry', '2026-11-02', []), ('sell', 'exit', '2026-11-10', [])]
    assert draft['links'] == []
    assert float(active['entry_low']) <= 58.8 <= float(active['entry_high'])        # the add was inside the plan's range

    # The pending proposal.
    proposals = client.get('/api/proposals').json()
    [proposal] = proposals['items']
    assert proposals['pending'] == 1 and (proposal['kind'], proposal['status'], proposal['source']) == (
        'watchlist_add', 'pending', 'agent:chat')
    assert proposal['payload']['ticker'] == 'NASDAQ:DEMOQ' and '加入自选' in proposal['summary']
    assert proposal['payload']['ticker'] not in {item['ticker'] for item in watch['items']}
    assert client.get(f"/api/proposals/{proposal['id']}").json()['id'] == proposal['id']

    # The ledger: the events after the opening, and the realised profit on the swing trade.
    ledger = client.get('/api/ledger').json()
    assert ledger['revision'] == 7 and ledger['pending'] == []
    long_account = ledger['accounts']['长期账户']
    # (500 x 13.6 - fee 5) - (500 x 12.3 + fee 5) = 6795 - 6155 = 640
    assert long_account['realized'] == {'CNY': '640'} and long_account['cash_balances']['CNY'] == '9223'
    assert long_account['positions']['SHA:DEMO']['quantity'] == '1200' and 'SHE:DEMOZ' not in long_account['positions']
    assert ledger['accounts']['研究账户']['cash_balances']['CNY'] == '10008.62'
    kinds = [event['kind'] for event in client.get('/api/ledger/events').json()['events']]
    assert kinds == ['opening', 'opening', 'buy', 'buy', 'dividend', 'sell', 'fee', 'interest']

    # Nothing above changed a byte, and creating the desk store was not needed for any of it.
    assert fingerprint(demo) == before


@needs_backend
def test_confirming_the_pending_proposal_is_refused_and_it_still_shows(demo, client):
    before = fingerprint(demo)
    [proposal] = client.get('/api/proposals').json()['items']
    for response in (client.post(f"/api/proposals/{proposal['id']}/confirm"),
                     client.delete(f"/api/proposals/{proposal['id']}"),
                     client.post('/api/watchlist', json={'ticker': 'NASDAQ:DEMOQ'}),
                     client.post('/api/notifications/read-all')):
        assert response.status_code == 403 and response.json() == {'detail': 'Demo mode is read-only'}
    [still] = client.get('/api/proposals').json()['items']
    assert still['id'] == proposal['id'] and still['status'] == 'pending'
    assert client.get('/api/notifications').json()['unread'] == 1
    assert fingerprint(demo) == before


# -- start-up and the monitor ---------------------------------------------------

def test_start_up_in_demo_mode_changes_nothing_and_the_alert_monitor_stays_off(demo):
    from fastapi import FastAPI
    from backend import features
    before = fingerprint(demo)

    async def start_up():
        app = FastAPI()
        async with features.desk_store_upgrade(app):
            async with monitor.background(app) as started:
                return started, app.state.alert_monitor

    assert asyncio.run(start_up()) == (None, None)
    assert fingerprint(demo) == before
    shown = monitor.status()
    assert shown['status'] == 'demo' and shown['running'] is False
    assert shown['active_status'] == 'not_started'     # it never ran: no heartbeat in the file


def test_outside_demo_mode_the_same_start_up_does_run_the_monitor(demo, tmp_path, monkeypatch):
    # The control for the test above: the guard is what keeps it off, not an empty rule list.
    from fastapi import FastAPI
    monkeypatch.delenv('TRACKER_DEMO_MODE')
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', 'fake')   # never the network
    copy = tmp_path / 'copy'
    copy.mkdir()
    for name in ('desk.sqlite3', 'ledger.sqlite3', 'fake_quotes.json'):
        (copy / name).write_bytes((demo / name).read_bytes())
    monkeypatch.setenv('PORTFOLIO_DIR', str(copy))

    async def start_up():
        app = FastAPI()
        async with monitor.background(app) as started:
            return started is not None and started.running

    assert asyncio.run(start_up()) is True
    assert monitor.heartbeat()['status'] == 'stopped'


def test_the_seed_refuses_to_fire_a_rule_the_engine_would_not_fire(demo):
    before = fingerprint(demo)
    with pytest.raises(RuntimeError, match='would not fire'):
        demo_seed._fire(demo, 3, datetime(2026, 11, 12, 10, 42, tzinfo=SHANGHAI))   # the plan's stop at 54, price 60
    assert fingerprint(demo) == before


def test_seeding_follows_the_directory_it_is_given_and_restores_the_environment(tmp_path, monkeypatch):
    from create_demo import create_demo
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path / 'elsewhere'))
    create_demo(tmp_path / 'one', ledger=True, today=TODAY)
    assert os.environ['PORTFOLIO_DIR'] == str(tmp_path / 'elsewhere')
    assert not (tmp_path / 'elsewhere').exists() and (tmp_path / 'one/desk.sqlite3').is_file()
    monkeypatch.delenv('PORTFOLIO_DIR')
    create_demo(tmp_path / 'two', ledger=True, today=date(2026, 10, 8))
    assert 'PORTFOLIO_DIR' not in os.environ
    # The second demo is a different day's: its fired alert is on that day's third trading day back.
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path / 'two'))
    from core.alerts import events
    [event] = events.list_events()['items']
    assert event['trading_date'] == '2026-09-28' and SessionClock().is_trading_day(CN, date(2026, 9, 28))


def test_opening_only_confirms_the_opening_balance_and_nothing_else(tmp_path):
    from create_demo import create_demo
    directory = tmp_path / 'portfolio'
    create_demo(directory, ledger=True, today=TODAY, history=False)
    assert not (directory / 'desk.sqlite3').exists()
    with sqlite3.connect(directory / 'ledger.sqlite3') as db:
        kinds = [json.loads(row[0])['kind'] for row in db.execute('SELECT payload FROM events ORDER BY sequence')]
        revision = db.execute('SELECT revision FROM ledger_meta').fetchone()[0]
    assert kinds and set(kinds) == {'opening'} and revision == 1
    quotes = json.loads((directory / 'fake_quotes.json').read_text(encoding='utf-8'))['quotes']
    assert sorted(quotes) == ['NASDAQ:DEMO', 'SHA:DEMO']


def test_a_year_without_exchange_calendars_still_seeds_and_only_leaves_out_the_fired_alert(tmp_path):
    # In 2029 no market has a holiday table, so the US close cannot be verified at the moment the
    # weight alert would fire and the engine holds the rule back; the demo must still be created.
    from create_demo import create_demo
    directory = tmp_path / 'portfolio'
    create_demo(directory, ledger=True, today=date(2029, 3, 14))
    with sqlite3.connect(directory / 'desk.sqlite3') as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        fired = db.execute('SELECT COUNT(*) FROM alert_events').fetchone()[0] if 'alert_events' in tables else 0
        rules_stored = db.execute('SELECT COUNT(*) FROM alert_rules').fetchone()[0]
        plans_stored = db.execute('SELECT COUNT(*) FROM trade_plans').fetchone()[0]
    assert fired == 0 and rules_stored == 6 and plans_stored == 3
