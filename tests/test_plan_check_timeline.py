"""The plan check places each trade where the ledger does: by date and order, with reversals applied.

The shares held before and after a trade, and the portfolio a buy's weight
is measured in, are those of the ledger's own replay at that trade: every
confirmed event and every event of the proposal dated before it (or on the
same day and entered earlier), never counting a reversed event.
"""
import hashlib

import pytest

from core import plans
from core.ledger import encode
from core.market.runtime import set_market_runtime
from tests.fakes import build_ledger, fx_rate, opening, position, trade
from tests.plan_support import CN, OTHER, active, at, payload, pin
from tests.test_alert_monitor import Market

DAY = '2026-11-16'


@pytest.fixture(autouse=True)
def market(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    pin(monkeypatch, at('2026-11-12'))                     # plans are in force from Thursday 2026-11-12
    scripted = Market()
    scripted.price(CN, '10')
    set_market_runtime(scripted.runtime)
    yield scripted
    set_market_runtime(None)


@pytest.fixture
def ledger(tmp_path):
    return build_ledger(tmp_path, [opening('Main', cash={'CNY': '100000'}, date='2026-11-10')])


def buy(quantity='100', price='10', date=DAY, **fields):
    return {**trade('buy', CN, quantity, price, account='Main', date=date), **fields}


def sell(quantity='100', price='10', date=DAY, **fields):
    return {**trade('sell', CN, quantity, price, account='Main', date=date), **fields}


def event_id(external_id, source='manual'):
    """The id the ledger gives an event with this external id."""
    return hashlib.sha256(encode([source, external_id]).encode()).hexdigest()


def book(ledger, *events):
    proposal = ledger.propose(list(events))
    ledger.confirm(proposal['id'])
    return proposal['events']


def check(ledger, *events):
    return plans.check_proposal(ledger.propose(list(events)))


def summary(items):
    return [(item['index'], item['relation'], [deviation['code'] for deviation in item['deviations']])
            for item in items]


# -- quantities ---------------------------------------------------------------------

def test_a_purchase_replaced_in_the_same_proposal_is_not_counted_twice(ledger):
    active(account='Main', planned_quantity='150')
    [held] = book(ledger, buy())
    items = check(ledger, {'kind': 'reverse', 'date': DAY, 'target_id': held['id']}, buy())
    assert summary(items) == [(1, 'entry', [])], 'the ledger ends at 100 shares, within the plan'


def test_a_backfilled_purchase_counts_only_what_was_held_on_its_date(ledger):
    active(account='Main', planned_quantity='150')
    book(ledger, buy())                                    # Monday
    [item] = check(ledger, buy(date='2026-11-13'))         # the Friday before: nothing was held yet
    assert (item['relation'], item['deviations']) == ('entry', [])


def test_a_backdated_sale_exits_what_was_held_then(ledger):
    active(account='Main', planned_quantity='1000')
    book(ledger, buy(date='2026-11-13'))
    book(ledger, buy(date='2026-11-17'))
    [item] = check(ledger, sell(date='2026-11-14'))
    assert item['relation'] == 'exit'


def test_events_of_one_proposal_are_taken_in_date_order(ledger):
    active(account='Main', planned_quantity='150')
    items = check(ledger, buy(date='2026-11-16'), buy(date='2026-11-13'))
    assert summary(items) == [(0, 'add', ['over_planned_quantity']), (1, 'entry', [])]
    assert items[0]['deviations'][0]['data']['quantity_after'] == '200'


def test_a_trade_reversed_in_its_own_proposal_never_happened(ledger):
    plan = active(account='Main', planned_quantity='150')
    items = check(ledger, buy(external_id='b1'), {'kind': 'reverse', 'date': DAY, 'target_id': event_id('b1')},
                  buy())
    assert [(item['index'], item['plan_id'], item['relation'], item['deviations']) for item in items] == [
        (0, None, 'none', []), (2, plan['id'], 'entry', [])]


def test_the_stored_link_counts_the_ledger_as_it_was_at_confirmation(ledger):
    plan = active(account='Main', planned_quantity='150')
    proposal = ledger.propose([buy()])
    ledger.confirm(proposal['id'])                         # its links were not written (an interrupted request)
    book(ledger, buy(date='2026-11-13'))                   # a purchase entered later, dated earlier
    assert plans.record_links(ledger.proposal(proposal['id'])) == 1
    [link] = plans.links_for_plan(plan['id'])
    assert (link['relation'], link['deviations']) == ('entry', []), 'the same link as if written at once'


def test_a_confirmed_reversal_and_replacement_link_as_an_entry(ledger):
    plan = active(account='Main', planned_quantity='150')
    [held] = book(ledger, buy())
    proposal = ledger.propose([{'kind': 'reverse', 'date': DAY, 'target_id': held['id']}, buy()])
    ledger.confirm(proposal['id'])
    plans.record_links(ledger.proposal(proposal['id']))
    [link] = plans.links_for_plan(plan['id'])
    assert (link['event_id'], link['relation'], link['deviations']) == (proposal['events'][1]['id'], 'entry', [])


# -- weights: each buy against the portfolio right after it, at current quotes ---------
# Main holds 100 000 CNY; CN quotes at 10, so 600 shares weigh 6 %.

def weights(items):
    return [(item['index'], [(deviation['code'], deviation['data'].get('weight_pct'))
                             for deviation in item['deviations']]) for item in items]


def test_a_later_sale_does_not_erase_a_buy_over_its_planned_weight(ledger):
    active(account='Main', planned_weight_pct='5')
    items = check(ledger, buy('600'), sell('600'))
    assert weights(items) == [(0, [('over_planned_weight', '6.00')]), (1, [])]


def test_a_later_deposit_does_not_dilute_the_weight_right_after_the_buy(ledger):
    active(account='Main', planned_weight_pct='5')
    items = check(ledger, buy('600'), {'kind': 'deposit', 'date': DAY, 'account': 'Main', 'currency': 'CNY',
                                       'amount': '100000'})
    assert weights(items) == [(0, [('over_planned_weight', '6.00')])]


def test_each_buy_is_measured_right_after_itself(ledger):
    active(account='Main', planned_weight_pct='5')
    items = check(ledger, buy('300'), buy('300'))
    assert weights(items) == [(0, []), (1, [('over_planned_weight', '6.00')])]


def test_a_backfilled_buy_is_measured_in_the_portfolio_of_its_date(ledger):
    active(account='Main', planned_weight_pct='5')
    book(ledger, buy('600'))
    [item] = check(ledger, buy('100', date='2026-11-13'))  # 1 000 of 100 000 then; 7 % only counting Monday's
    assert item['deviations'] == []


def test_the_weight_states_its_price_basis(ledger):
    active(account='Main', planned_weight_pct='5')
    [item] = check(ledger, buy('600', price='12'))         # paid 7 200 in cash, valued at the quote: 6 000
    [deviation] = item['deviations']
    assert deviation['data'] == {'weight_pct': '6.07', 'planned_weight_pct': '5', 'estimated': False,
                                 'price_basis': 'current_quotes', 'quotes_as_of': '2026-11-17T10:00:00+08:00'}
    assert '按当前行情' in deviation['message']


# -- estimated: a weight is as good as every input of the portfolio total ---------------

def test_an_estimated_quote_of_another_holding_makes_the_weight_estimated(tmp_path, market):
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, positions=[position(OTHER, '50', '20')],
                                             date='2026-11-10')])
    market.price(OTHER, '20', age=30 * 60)                 # stale: valued, but not verified
    active(account='Main', planned_weight_pct='10')
    [item] = check(ledger, buy('100'))                     # a fresh 1 000 of 1 000 + a stale 1 000
    [deviation] = item['deviations']
    assert (deviation['code'], deviation['data']['weight_pct'], deviation['data']['estimated']) == (
        'over_planned_weight', '50.00', True)
    assert '估算' in deviation['message'] and OTHER in deviation['message']


def test_an_estimated_rate_on_cash_makes_the_weight_estimated(tmp_path, market):
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000', 'HKD': '1000'}, date='2026-11-10')])
    market.source.rates['HKD'] = fx_rate('HKD', '0.9')     # no time: time_unknown, an estimate
    active(account='Main', planned_weight_pct='10')
    [item] = check(ledger, buy('100'))                     # 1 000 of 1 000 + 900
    [deviation] = item['deviations']
    assert (deviation['code'], deviation['data']['weight_pct'], deviation['data']['estimated']) == (
        'over_planned_weight', '52.63', True)
    assert 'HKD' in deviation['message']


def test_a_portfolio_total_that_is_unavailable_leaves_the_weight_unknown(tmp_path, market):
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, positions=[position(OTHER, '50', '20')],
                                             date='2026-11-10')])
    active(account='Main', planned_weight_pct='10')        # OTHER has no quote at all
    [item] = check(ledger, buy('100'))
    [deviation] = item['deviations']
    assert (deviation['code'], deviation['level']) == ('weight_unknown', 'info')
    assert '组合总值不可用' in deviation['message']


def test_the_plan_view_weight_is_estimated_when_the_total_is():
    plan = plans.create(payload(planned_weight_pct='10'))
    today = {'as_of': '2026-11-17T10:00:00+08:00',
             'totals': {'total_cny': '2000.00', 'quality': 'estimated', 'unavailable': [],
                        'estimated': [{'ticker': OTHER, 'status': 'stale', 'reason': '行情已超过 30 分钟未更新'}]},
             'accounts': [{'name': 'Main', 'positions': [
                 {'ticker': CN, 'quantity': '100', 'price': '10', 'market_value_cny': '1000.00', 'weight_pct': '50.00',
                  'estimated': False,
                  'estimated_reason': None, 'quote': {'status': 'live'}}]}]}
    view = plans.market_view(plan, today)
    assert (view['weight_pct'], view['weight_estimated']) == ('50.00', True)
    assert OTHER in view['weight_reason']
