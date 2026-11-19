"""deviations_for_date and links_for_plan: what the post-market review reads about a day's trades."""
from datetime import date

import pytest

from core import plans
from core.market.runtime import set_market_runtime
from tests.fakes import book as confirm, build_ledger, opening, trade
from tests.plan_support import CN, OTHER, active, pin
from tests.test_alert_monitor import Market

LINK_KEYS = {'event_id', 'event_date', 'kind', 'ticker', 'account', 'quantity', 'price', 'plan_id', 'relation',
             'deviations', 'proposal_id', 'created_at', 'reversed'}


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    pin(monkeypatch)
    scripted = Market()
    scripted.price(CN, '10')
    set_market_runtime(scripted.runtime)
    yield tmp_path
    set_market_runtime(None)


@pytest.fixture
def ledger(tmp_path):
    return build_ledger(tmp_path, [opening('Main', cash={'CNY': '100000'}, date='2026-11-13')])


def book(ledger, *events):
    """Preview, confirm and link, as the ledger API does; returns the confirmed events."""
    proposal = ledger.propose(list(events))
    ledger.confirm(proposal['id'])
    assert plans.record_links(ledger.proposal(proposal['id'])) == len(
        [event for event in proposal['events'] if event['kind'] in ('buy', 'sell')])
    return proposal['events']


def test_without_a_store_there_is_nothing_and_nothing_is_created(desk):
    assert plans.deviations_for_date('2026-11-16') == []
    assert plans.links_for_plan(1) == []
    assert not (desk / 'desk.sqlite3').exists()


def test_the_day_s_trades_with_their_deviations(ledger):
    plan = active(stop_price='9', target_price='12', planned_quantity='150')
    [first] = book(ledger, trade('buy', CN, '100', '10', account='Main', date='2026-11-16'))
    [second, unplanned] = book(ledger, trade('buy', CN, '100', '10.2', account='Main', date='2026-11-17'),
                               trade('buy', OTHER, '10', '20', account='Main', date='2026-11-17'))
    [monday] = plans.deviations_for_date('2026-11-16')
    assert set(monday) == LINK_KEYS
    assert (monday['event_id'], monday['plan_id'], monday['relation'], monday['deviations']) == (
        first['id'], plan['id'], 'entry', [])
    assert (monday['kind'], monday['ticker'], monday['account'], monday['quantity'], monday['price']) == (
        'buy', CN, 'Main', '100', '10')
    assert monday['reversed'] is False
    tuesday = plans.deviations_for_date(date(2026, 11, 17))
    assert [(item['event_id'], item['plan_id'], item['relation'], [d['code'] for d in item['deviations']])
            for item in tuesday] == [(second['id'], plan['id'], 'add', ['over_planned_quantity']),
                                     (unplanned['id'], None, 'none', ['no_plan'])]
    assert plans.deviations_for_date('2026-11-18') == []
    assert [item['event_id'] for item in plans.links_for_plan(plan['id'])] == [first['id'], second['id']]


def test_a_reversed_trade_did_not_happen(ledger):
    plan = active()
    [bought] = book(ledger, trade('buy', CN, '100', '10', account='Main', date='2026-11-16'))
    confirm(ledger, [{'kind': 'reverse', 'date': '2026-11-16', 'target_id': bought['id']}])
    assert plans.deviations_for_date('2026-11-16') == []
    [link] = plans.links_for_plan(plan['id'])
    assert link['event_id'] == bought['id'] and link['reversed'] is True


def test_when_the_ledger_cannot_be_read_reversals_are_unknown(ledger, monkeypatch):
    import sqlite3
    plan = active()
    book(ledger, trade('buy', CN, '100', '10', account='Main', date='2026-11-16'))
    # Links read the ledger read-only (core.ledger.read_confirmed); a failed read leaves the reversals unknown.
    from core.ledger import ConfirmedLedger
    with monkeypatch.context() as patched:
        patched.setattr(ConfirmedLedger, 'history',
                        lambda self: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked')))
        [item] = plans.deviations_for_date('2026-11-16')
        assert item['reversed'] is None
        assert plans.links_for_plan(plan['id'])[0]['reversed'] is None
    ledger.path.write_bytes(b'not a database' * 512)       # a damaged file: unknown too, and left as it is
    assert plans.links_for_plan(plan['id'])[0]['reversed'] is None
    assert ledger.path.read_bytes() == b'not a database' * 512


def test_trades_booked_before_plans_were_used_have_no_link(ledger, desk):
    proposal = ledger.propose([trade('buy', CN, '100', '10', account='Main', date='2026-11-16')])
    ledger.confirm(proposal['id'])
    assert plans.record_links(ledger.proposal(proposal['id'])) == 0
    assert not (desk / 'desk.sqlite3').exists()
    active(OTHER)
    assert plans.deviations_for_date('2026-11-16') == []


@pytest.mark.parametrize('day', ['2026-13-01', '16/11/2026', None, 20261116])
def test_a_bad_date_is_refused(day):
    with pytest.raises(plans.PlanError, match='YYYY-MM-DD'):
        plans.deviations_for_date(day)
