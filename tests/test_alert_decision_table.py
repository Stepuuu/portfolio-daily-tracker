"""The decision table in the docstring of ``core.alerts.monitor``, one test per row."""
import asyncio
from datetime import timedelta

import pytest

from core import desk_store, notify
from core.alerts import monitor, rules, view
from core.market.runtime import set_market_runtime
from tests.fakes import build_ledger, opening, position
from tests.test_alert_monitor import (CN, OTHER, Interfering, Market, cycle, notes, pausing_monitor,
                                      stalling_monitor, stop_rule, untraded)


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_ALERTS_ENABLED', raising=False)
    monkeypatch.delenv('TRACKER_ALERT_INTERVAL_SECONDS', raising=False)
    monkeypatch.setattr(notify, '_deliverers', [])
    set_market_runtime(None)
    yield tmp_path
    set_market_runtime(None)


def skipped(market):
    """``{rule id: code}`` as ``GET /api/alerts/status`` lists them."""
    return {entry['rule_id']: entry['code'] for entry in monitor.status(market.runtime, wall=market.clock)['skipped_rules']}


def shown(market, rule_id, ledger=None):
    [item] = [item for item in view.with_market(rules.list_rules(), market.runtime, ledger) if item['id'] == rule_id]
    return item['market']


def test_row_1_another_monitor_holds_a_valid_lease():
    market = Market()
    stop_rule()
    market.price(CN, '9.9')
    cycle(market.monitor(owner='holder'))
    report = cycle(market.monitor(owner='other'))
    assert (report.status, report.actions, report.fetched) == ('standby', {}, [])
    assert len(notes()) == 1


def test_row_2_outside_the_session_nothing_is_evaluated():
    market = Market('2026-11-17 12:00')
    rule = stop_rule()
    market.price(CN, '9.9')
    report = cycle(market.monitor())
    assert (report.status, report.actions) == ('waiting', {}) and notes() == []
    assert shown(market, rule['id'])['skip_code'] == 'session_closed'


def test_row_3_a_failed_quote_request_evaluates_nothing():
    market = Market()
    stop_rule()
    market.source.down = '模拟数据源中断'
    report = cycle(market.monitor())
    assert report.failed and report.actions == {} and skipped(market) == {}


def test_row_4_a_cycle_overtaken_by_a_newer_one_writes_nothing():
    market = Market()
    stop_rule(cooldown_seconds=0)
    stalled, source = stalling_monitor(market, owner='same-process')
    other = market.monitor(owner='same-process')
    market.price(CN, '9.9')
    source.meanwhile = lambda: asyncio.run(other.run_cycle())     # the newer cycle fires
    report = cycle(stalled)
    assert (report.status, report.actions) == ('superseded', {})
    assert [item['data']['n'] for item in notes()] == [1]
    assert monitor.heartbeat()['applied_cycle'] > 0


def test_row_4_a_cycle_whose_data_is_out_of_date_writes_nothing():
    """Paused after its fetch until the next session date: nothing of it is written, not even the heartbeat."""
    market = Market('2026-11-17 14:59')
    rule = stop_rule()
    watcher, quotes = pausing_monitor(market)
    market.price(CN, '9.9')
    quotes.then = lambda: market.clock.set('2026-11-18 09:40')
    report = cycle(watcher)
    assert (report.status, report.actions) == ('stale_cycle', {}) and notes() == []
    assert rules.get(rule['id'])['state']['last_trading_date'] is None
    assert 'last_cycle_at' not in monitor.heartbeat()


@pytest.mark.parametrize('how, action', [('delete', 'deleted'), ('disable', 'disabled')])
def test_row_5_a_rule_deleted_or_disabled_since_the_cycle_began(how, action):
    holder = {}
    change = {'delete': lambda: rules.delete(holder['id']),
              'disable': lambda: rules.update(holder['id'], {'enabled': False})}[how]
    market = Market(source=Interfering(change))
    holder['id'] = stop_rule()['id']
    market.price(CN, '9.9')
    assert cycle(market.monitor()).actions == {holder['id']: action} and notes() == []


def test_row_5_a_rule_edited_since_the_cycle_began_is_judged_as_edited():
    holder = {}
    market = Market(source=Interfering(None))
    holder['id'] = stop_rule(threshold='9')['id']
    watcher = market.monitor()
    market.price(CN, '9.8')
    cycle(watcher)
    market.tick()
    market.price(CN, '9.5')
    market.source.change = lambda: rules.update(holder['id'], {'threshold': '10'})
    assert cycle(watcher).actions == {holder['id']: 'fire'}


def test_row_6_a_threshold_out_of_range_is_skipped():
    market = Market()
    rule = stop_rule()
    with desk_store.transaction() as db:
        db.execute("UPDATE alert_rules SET threshold='10000000000000000000000' WHERE id=?", (rule['id'],))
    market.price(CN, '9')
    assert cycle(market.monitor()).actions == {rule['id']: 'skip'}
    assert skipped(market) == {rule['id']: 'invalid_threshold'}
    assert monitor.status(market.runtime, wall=market.clock)['errored_rule_ids'] == [rule['id']]


def test_row_7_an_unusable_quote_is_skipped_with_its_reason():
    market = Market()
    good, untraded_rule = stop_rule(), stop_rule(OTHER)
    market.price(CN, '10.5')
    untraded(market, OTHER)
    assert cycle(market.monitor()).actions == {good['id']: 'none', untraded_rule['id']: 'skip'}
    assert skipped(market) == {untraded_rule['id']: 'no_trade_today'}
    assert shown(market, untraded_rule['id'])['skip_code'] == 'no_trade_today'


def test_row_8_a_weight_on_an_unverified_valuation_is_skipped(desk):
    ledger = build_ledger(desk, [opening('Main', positions=[position(CN, '100', '8', '示例甲'),
                                                             position(OTHER, '100', '8', '示例乙')], date='2026-11-13')])
    market = Market()
    rule = rules.create({'ticker': CN, 'kind': 'position_weight_max', 'threshold': '70'})
    market.price(CN, '10')
    market.price(OTHER, '1', age=30 * 60)
    assert cycle(market.monitor()).actions == {rule['id']: 'skip'}
    assert skipped(market) == {rule['id']: 'weight_estimated'}
    assert shown(market, rule['id'], ledger)['skipped_since'] == '2026-11-17T10:00:00+08:00'


def test_row_9_a_session_date_earlier_than_the_stored_one_is_skipped():
    market = Market('2026-11-18 09:31')
    rule = stop_rule()
    market.price(CN, '9.9')
    watcher = market.monitor()
    cycle(watcher)
    market.clock.set('2026-11-17 10:00')                # the clock was put back a day
    market.price(CN, '9.8')
    assert cycle(watcher).actions == {rule['id']: 'skip'}
    assert skipped(market) == {rule['id']: 'date_regressed'}
    item = shown(market, rule['id'])
    assert item['checked_now'] is False and item['skip_code'] == 'date_regressed' and len(notes()) == 1


def test_row_10_a_result_that_changes_nothing_writes_nothing():
    market = Market()
    rule = stop_rule()
    market.price(CN, '10.5')
    watcher = market.monitor()
    assert cycle(watcher).actions == {rule['id']: 'none'}          # row 12: the new day is stored
    with desk_store.connect() as db:
        before = dict(db.execute('SELECT * FROM alert_state').fetchone())
    market.tick()
    assert cycle(watcher).actions == {rule['id']: 'none'}
    with desk_store.connect() as db:
        assert dict(db.execute('SELECT * FROM alert_state').fetchone()) == before
    assert skipped(market) == {}


def test_row_11_a_change_from_an_older_input_is_skipped():
    market = Market()
    rule = stop_rule(cooldown_seconds=0)
    watcher = market.monitor()
    market.price(CN, '9.9', age=0)
    cycle(watcher)
    market.clock.advance(seconds=5)
    market.price(CN, '10.1', age=25)
    assert cycle(watcher).actions == {rule['id']: 'skip'}
    assert skipped(market) == {rule['id']: 'stale_observation'}


def test_row_12_a_change_is_written_with_its_observation():
    market = Market()
    rule = stop_rule()
    market.price(CN, '9.9')
    assert cycle(market.monitor()).actions == {rule['id']: 'fire'}
    with desk_store.connect() as db:
        _rule, state = rules.load_for_update(db, rule['id'])
    assert state.observation.inputs == (('quote:SHA:600001', market.clock.now() - timedelta(seconds=3)),)
    assert not state.armed and len(notes()) == 1 and skipped(market) == {}
