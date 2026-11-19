"""One listing, several spellings: plans compare ``instruments.quote_key``, never the raw ticker text.

``HKG:700``, ``HKG:0700`` and ``HKG:00700`` are one Hong Kong listing. The
ledger and the plans keep the spelling they were given; every place that
matches a plan to a ticker (one active plan, the plan of a trade, the shares
in its scope, its alert rules, the list filter, the Today rows) compares the
canonical identity.
"""
import pytest

from core import desk_store, plans
from core.alerts import rules
from tests.fakes import build_ledger, opening, trade
from tests.plan_support import MONDAY, active, at, payload, pin

SHORT, LONG = 'HKG:700', 'HKG:00700'
DOT, DASH = 'NYSE:BRK.B', 'NYSE:BRK-B'      # one US class share: the same Yahoo and Eastmoney symbol
B_USD, B_HKD = 'SHA:900901', 'SHE:200012'  # B shares: trade in USD / HKD, the ledger books them in CNY
DAY = '2026-11-16'


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    pin(monkeypatch)
    return tmp_path


@pytest.fixture
def ledger(tmp_path):
    return build_ledger(tmp_path, [opening('Main', cash={'HKD': '1000000'}, date='2026-11-13')])


def buy(ticker, quantity='100', price='400', **fields):
    return trade('buy', ticker, quantity, price, currency='HKD', account=fields.pop('account', 'Main'),
                 date=fields.pop('date', DAY), **fields)


def check(ledger, *events):
    return plans.check_proposal(ledger.propose(list(events)))


def codes(item):
    return [deviation['code'] for deviation in item['deviations']]


# -- one active plan per listing ------------------------------------------------

def test_a_second_spelling_of_the_same_listing_cannot_be_activated_too():
    first = active(SHORT, account='Main', stop_price='300')
    second = plans.create(payload(LONG, account='Main', stop_price='350'))
    with pytest.raises(plans.PlanConflict) as caught:
        plans.activate(second['id'])
    assert f'#{first["id"]}' in str(caught.value)
    assert plans.get(second['id'])['status'] == 'draft'
    everywhere = plans.create(payload('HKG:0700'))       # every account against one account
    with pytest.raises(plans.PlanConflict, match=f'#{first["id"]}'):
        plans.activate(everywhere['id'])
    assert active('HKG:701', account='Main')['status'] == 'active', 'another listing is another plan'


def test_the_list_filter_finds_every_spelling():
    stored = plans.create(payload(LONG))
    assert [plan['id'] for plan in plans.list(ticker=SHORT)] == [stored['id']]
    assert plans.list(ticker='HKG:701') == []


def test_an_active_plan_stored_in_another_spelling_keeps_working():
    plan = active(SHORT, account='Main', stop_price='300')
    edited = plans.update(plan['id'], {'ticker': LONG, 'thesis': '新理由'})
    assert (edited['ticker'], edited['thesis']) == (SHORT, '新理由'), 'the same listing is not a ticker change'
    with pytest.raises(plans.PlanConflict, match='不能修改代码'):
        plans.update(plan['id'], {'ticker': 'HKG:701'})


# -- trades ---------------------------------------------------------------------

def test_a_trade_in_another_spelling_belongs_to_the_plan(ledger):
    plan = active(SHORT, account='Main', planned_quantity='150')
    [item] = check(ledger, buy(LONG))
    assert (item['plan_id'], item['relation'], codes(item)) == (plan['id'], 'entry', [])
    proposal = ledger.propose([buy(SHORT)])
    ledger.confirm(proposal['id'])
    # The shares in the plan's scope add up across spellings: 100 + 100 > 150.
    [more] = check(ledger, buy('HKG:0700'))
    assert (more['plan_id'], more['relation'], codes(more)) == (plan['id'], 'add', ['over_planned_quantity'])
    assert more['deviations'][0]['data']['quantity_after'] == '200'


def test_after_invalidation_sees_every_spelling(ledger, monkeypatch):
    pin(monkeypatch, at('2026-11-13'))
    ended = active(LONG)
    plans.close(ended['id'], '判断失效')
    [item] = check(ledger, buy(SHORT))
    assert (item['plan_id'], codes(item)) == (ended['id'], ['after_invalidation'])


def test_the_no_plan_hint_sees_every_spelling(ledger, monkeypatch):
    draft = plans.create(payload('HKG:0700'))
    [item] = check(ledger, buy(SHORT))
    assert f'计划 #{draft["id"]} 仍是草稿' in item['deviations'][0]['message']
    pin(monkeypatch, at('2026-11-17'))
    plans.activate(draft['id'])
    [early] = check(ledger, buy(SHORT, date='2026-11-16'))
    assert '于 2026-11-17 启用' in early['deviations'][0]['message']


# -- alert rules ----------------------------------------------------------------

def test_an_identical_rule_in_another_spelling_covers_the_plan_s_alert():
    manual = rules.create({'ticker': LONG, 'kind': 'stop_loss', 'threshold': '300'})
    plan = active(SHORT, stop_price='300', target_price='500')
    own = {rule['kind'] for rule in rules.list_rules(source=f'plan:{plan["id"]}')}
    assert own == {'target_price'}, 'no second stop alert for the same listing'
    [note] = plans.alert_notes(plans.get(plan['id']))
    assert f'#{manual["id"]}' in note


def test_an_ended_plan_s_rule_in_another_spelling_gives_way():
    first = active(LONG, stop_price='300')
    plans.close(first['id'], '已离场')
    second = active(SHORT, stop_price='300')
    assert [(rule['ticker'], rule['enabled']) for rule in rules.list_rules(source=f'plan:{second["id"]}')] == [
        (SHORT, True)]
    assert rules.list_rules(source=f'plan:{first["id"]}') == [], 'the ended plan\'s identical rule was removed'
    assert plans.alert_notes(plans.get(second['id'])) == []


# -- the Today rows ---------------------------------------------------------------

def test_the_market_view_finds_the_row_in_another_spelling():
    plan = plans.create(payload(SHORT, planned_quantity='150'))
    today = {'as_of': '2026-11-17T10:00:00+08:00', 'totals': {'total_cny': '100000', 'quality': 'complete'},
             'accounts': [{'name': 'Main', 'positions': [
                 {'ticker': LONG, 'quantity': '100', 'price': '400', 'market_value_cny': '36800.00', 'weight_pct': '36.80',
                  'estimated': False,
                  'quote': {'status': 'live'}}]}]}
    view = plans.market_view(plan, today)
    assert (view['position_quantity'], view['weight_pct'], view['price']) == ('100', '36.80', '400')
    assert view['quantity_vs_plan'] == '-50'


# -- US class shares: BRK.B and BRK-B are one listing ------------------------------

@pytest.fixture
def usd_ledger(tmp_path):
    return build_ledger(tmp_path, [opening('Main', cash={'USD': '1000000'}, date='2026-11-13')])


def us_buy(ticker, quantity='10', price='400', **fields):
    return trade('buy', ticker, quantity, price, currency='USD', account=fields.pop('account', 'Main'),
                 date=fields.pop('date', DAY), **fields)


def test_a_us_class_share_in_the_other_spelling_cannot_be_activated_too():
    first = active(DOT, account='Main', stop_price='300')
    second = plans.create(payload(DASH, account='Main', stop_price='350'))
    with pytest.raises(plans.PlanConflict, match=f'#{first["id"]}'):
        plans.activate(second['id'])
    assert plans.get(second['id'])['status'] == 'draft'
    assert [plan['id'] for plan in plans.list(ticker=DASH)] == [second['id'], first['id']]


def test_a_trade_in_the_other_us_spelling_belongs_to_the_plan(usd_ledger):
    plan = active(DOT, account='Main', planned_quantity='15')
    [item] = check(usd_ledger, us_buy(DASH, '20'))
    assert (item['plan_id'], item['relation'], codes(item)) == (plan['id'], 'entry', ['over_planned_quantity'])


def activate_behind_the_rule(plan_id):
    """What an older version could store: a second active plan for one listing in another spelling."""
    with desk_store.transaction() as db:
        db.execute("UPDATE trade_plans SET status='active', activated_at=?, updated_at=? WHERE id=?",
                   (MONDAY, MONDAY, plan_id))


def test_active_duplicates_stored_earlier_are_kept_and_reported(usd_ledger):
    first = active(DOT, account='Main', stop_price='300')
    second = plans.create(payload(DASH, account='Main', stop_price='350'))
    activate_behind_the_rule(second['id'])
    other = active('NYSE:BRK.A', account='Main')
    listed = {plan['id']: plan for plan in plans.list()}
    assert (listed[first['id']]['conflicts'], listed[second['id']]['conflicts']) == ([second['id']], [first['id']])
    assert listed[other['id']]['conflicts'] == []
    assert plans.get(first['id'])['conflicts'] == [second['id']]
    # A trade both cover is compared with the latest one and says so; nothing is deleted or changed.
    [item] = check(usd_ledger, us_buy(DOT, price='320'))
    assert item['plan_id'] == second['id'] and codes(item) == ['below_stop_buy', 'plan_conflict']
    conflict = item['deviations'][1]
    assert conflict['level'] == 'warning' and f'#{first["id"]}' in conflict['message']
    assert conflict['data'] == {'plan_ids': [first['id'], second['id']]}
    assert [(plan['id'], plan['status']) for plan in plans.list(status='active')] == [
        (other['id'], 'active'), (second['id'], 'active'), (first['id'], 'active')]
    assert plans.activate(first['id'])['status'] == 'active', 'repairing its alerts still works'
    assert plans.list(status='draft') == [] and plans.get(second['id'])['status'] == 'active'


# -- B shares: the plan's prices are in the trading currency -------------------------

@pytest.fixture
def cny_ledger(tmp_path, monkeypatch):
    import core.ledger   # B shares as booked before the ledger refused them (core.ledger.bookable)
    monkeypatch.setattr(core.ledger, 'bookable', core.ledger.instrument)
    return build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000000'}, date='2026-11-13')])


def b_trade(kind, ticker, quantity='100', price='3.50'):
    """As the ledger books a B share: in CNY, whatever the trading currency."""
    return trade(kind, ticker, quantity, price, currency='CNY', account='Main', date=DAY)


@pytest.mark.parametrize('ticker, currency', [(B_USD, 'USD'), (B_HKD, 'HKD')])
def test_a_b_share_trade_booked_in_cny_is_not_compared_with_prices_in_its_trading_currency(cny_ledger, ticker,
                                                                                            currency):
    plan = active(ticker, account='Main', entry_low='0.45', entry_high='0.55', stop_price='0.4', target_price='0.7',
                  planned_quantity='150')
    [bought] = check(cny_ledger, b_trade('buy', ticker))    # CNY 3.50 is about USD 0.50: no range check without FX
    assert (bought['plan_id'], bought['relation'], codes(bought)) == (plan['id'], 'entry', ['price_unknown'])
    [deviation] = bought['deviations']
    assert deviation['level'] == 'info' and '不可用' in deviation['message']
    assert currency in deviation['message'] and 'CNY' in deviation['message']
    assert deviation['data'] == {'price': '3.5', 'booked_currency': 'CNY', 'plan_currency': currency}
    # Quantities do not depend on the currency and are still checked.
    [more] = check(cny_ledger, b_trade('buy', ticker, '200'))
    assert codes(more) == ['price_unknown', 'over_planned_quantity']
    confirmed = cny_ledger.propose([b_trade('buy', ticker)])
    cny_ledger.confirm(confirmed['id'])
    [sold] = check(cny_ledger, b_trade('sell', ticker, '50', '2.80'))
    assert (sold['relation'], codes(sold)) == ('reduce', ['price_unknown'])


def test_a_b_share_plan_s_loss_is_in_its_trading_currency():
    for ticker, currency in ((B_USD, 'USD'), (B_HKD, 'HKD')):
        loss = plans.create(payload(ticker, entry_high='10', stop_price='9', planned_quantity='100'))['max_loss']
        assert (loss['amount'], loss['currency']) == ('100.00', currency)
