"""The pure alert decision: edge-triggered, hysteresis, daily cap, and no decision on untrusted quotes."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from core.alerts import engine, messages
from core.alerts.engine import Context, Rule, RuleState
from core.market.quotes import PREV_CLOSE
from core.market.valuation import value_portfolio
from tests.fakes import quote, shanghai

TICKER = 'SHA:600001'
NOW = shanghai('2026-11-17 10:00')
TODAY, YESTERDAY = date(2026, 11, 17), date(2026, 11, 16)


def rule(kind=engine.STOP_LOSS, threshold='10', **fields):
    return Rule(id=1, ticker=fields.pop('ticker', TICKER), kind=kind, threshold=Decimal(threshold), **fields)


def live(price, prev_close='10', *, status='live', age=3, **fields):
    """A quote as ``QuoteService`` hands it out: already stamped with ``status``."""
    return replace(quote(TICKER, price, prev_close=prev_close, at=NOW - timedelta(seconds=age), name='示例甲', **fields),
                   status=status)


def context(**fields):
    values = {'now': NOW, 'session_open': True, 'trading_date': TODAY}
    values.update(fields)
    return Context(**values)


def walk(a_rule, prices, state=None, **context_fields):
    """Evaluate a price path; return the actions and the final state."""
    actions = []
    for price in prices:
        decision = engine.evaluate(a_rule, state, live(price), context(**context_fields))
        actions.append(decision.action)
        state = decision.state
    return actions, state


# -- crossing and re-arming ----------------------------------------------------

def test_stop_loss_fires_once_when_crossed_and_never_while_it_stays_below():
    actions, state = walk(rule(), ['10.5', '10.01', '10', '9.8', '9.2', '9.99', '10.02', '9.7'])
    assert actions == ['none', 'none', 'fire', 'wait', 'wait', 'wait', 'none', 'wait']
    assert state == RuleState(armed=False, last_fired_at=NOW, last_trading_date=TODAY, fire_count_today=1)


def test_the_first_fire_carries_its_number_day_and_value():
    decision = engine.evaluate(rule(), None, live('9.9'), context())
    assert decision.fired and decision.changed
    assert (decision.fire_number, decision.trading_date, decision.value) == (1, TODAY, Decimal('9.9'))


def test_stop_loss_rearms_only_after_a_half_percent_recovery():
    actions, state = walk(rule(), ['9.9', '10.04', '10.049'])
    assert actions == ['fire', 'none', 'none'] and not state.armed
    decision = engine.evaluate(rule(), state, live('10.05'), context())     # 10 x 1.005
    assert decision.action == 'rearm' and decision.state.armed
    again = engine.evaluate(rule(), decision.state, live('9.95'), context())
    assert again.fired and again.fire_number == 2


def test_target_price_mirrors_the_stop():
    target = rule(engine.TARGET_PRICE, '20')
    actions, state = walk(target, ['19.9', '20', '20.5', '19.95', '19.9', '20.1'])
    assert actions == ['none', 'fire', 'wait', 'none', 'rearm', 'fire']
    assert state.fire_count_today == 2


def test_next_trading_day_rearms_and_restarts_the_count():
    yesterday = RuleState(armed=False, last_fired_at=NOW - timedelta(days=1), last_trading_date=YESTERDAY,
                          fire_count_today=3)
    decision = engine.evaluate(rule(), yesterday, live('9.5'), context())
    assert decision.fired and decision.fire_number == 1 and decision.trading_date == TODAY
    assert decision.state.last_trading_date == TODAY and decision.state.fire_count_today == 1
    # Above the stop on the new day: nothing fires, but the new day is stored, re-armed.
    calm = engine.evaluate(rule(), yesterday, live('10.5'), context())
    assert calm.action == 'none' and calm.changed
    assert calm.state == RuleState(armed=True, last_fired_at=yesterday.last_fired_at, last_trading_date=TODAY,
                                   fire_count_today=0)


def test_at_most_three_fires_per_rule_per_day():
    actions, state = walk(rule(), ['9.9', '10.1', '9.9', '10.1', '9.9', '10.1', '9.9', '9.8'])
    assert actions == ['fire', 'rearm', 'fire', 'rearm', 'fire', 'rearm', 'wait', 'wait']
    assert state.fire_count_today == engine.MAX_FIRES_PER_DAY == 3
    capped = engine.evaluate(rule(), state, live('9.7'), context())
    assert capped.reason == 'daily_cap' and not capped.fired


def test_cooldown_holds_a_second_fire_until_it_has_passed():
    slow = rule(cooldown_seconds=300)
    fired = engine.evaluate(slow, None, live('9.9'), context())
    rearmed = engine.evaluate(slow, fired.state, live('10.2'), context(now=NOW + timedelta(seconds=60)))
    held = engine.evaluate(slow, rearmed.state, live('9.9'), context(now=NOW + timedelta(seconds=120)))
    assert (held.action, held.reason) == ('wait', 'cooldown') and held.state.armed
    later = engine.evaluate(slow, held.state, live('9.9'), context(now=NOW + timedelta(seconds=300)))
    assert later.fired and later.fire_number == 2


def test_a_last_fire_dated_after_the_clock_holds_the_cooldown_no_longer_than_it_lasts():
    slow = rule(cooldown_seconds=300)
    ahead = RuleState(armed=True, last_fired_at=NOW + timedelta(hours=1), last_trading_date=TODAY, fire_count_today=1)
    assert engine.evaluate(slow, ahead, live('9.9'), context()).fired, 'the clock went back an hour'
    near = replace(ahead, last_fired_at=NOW + timedelta(seconds=60))   # a clock a minute apart: still the cooldown
    assert engine.evaluate(slow, near, live('9.9'), context()).reason == 'cooldown'


def test_an_earlier_trading_date_than_the_stored_one_is_not_evaluated():
    stored = RuleState(armed=True, last_trading_date=TODAY)
    decision = engine.evaluate(rule(), stored, live('9'), context(trading_date=YESTERDAY))
    assert decision.action == 'skip' and decision.reason == 'date_regressed' and decision.state == stored


def test_a_change_from_an_observation_older_than_the_stored_one_is_ignored():
    """A source that fell back to older data can neither re-arm nor fire a rule; an equal time is not older."""
    earlier, later = NOW - timedelta(seconds=5), NOW + timedelta(seconds=5)
    fired = engine.evaluate(rule(), None, live('9.9'), context(observed_at=NOW))
    assert fired.fired and fired.state.observed_at == NOW
    stale = engine.evaluate(rule(), fired.state, live('10.1'), context(observed_at=earlier))
    assert (stale.action, stale.reason, stale.changed) == ('skip', 'stale_observation', False)
    assert stale.state == fired.state
    assert 'SHA:600001 的行情' in stale.message and '10:00:00' in stale.message
    # An observation that would change nothing is reported as usual.
    assert engine.evaluate(rule(), fired.state, live('9.8'), context(observed_at=earlier)).action == 'wait'
    # A coarse timestamp: a new price under the same exchange time is new information.
    assert engine.evaluate(rule(), fired.state, live('10.1'), context(observed_at=NOW)).action == 'rearm'
    newer = engine.evaluate(rule(), fired.state, live('10.1'), context(observed_at=later))
    assert newer.action == 'rearm' and newer.state.observed_at == later
    assert engine.evaluate(rule(), newer.state, live('9.9'), context(observed_at=later)).fired
    # A stored time more than two minutes ahead of the clock (it was put back) holds nothing back.
    ahead = replace(fired.state, observation=engine.Observation.of(
        {engine.quote_input(TICKER): NOW + timedelta(minutes=3)}))
    assert engine.evaluate(rule(), ahead, live('10.1'), context(observed_at=NOW)).action == 'rearm'


def test_a_weight_observation_orders_quotes_and_rates_but_not_the_ledger_revision():
    weight = rule(engine.POSITION_WEIGHT_MAX, '60')
    price, rate = engine.quote_input(TICKER), engine.fx_input('HKD')
    stored = RuleState(last_trading_date=TODAY, observation=engine.Observation.of({price: NOW, rate: NOW}, ledger=7))

    def at(rate_time, ledger):
        observation = engine.Observation.of({price: NOW, rate: rate_time}, ledger=ledger)
        return engine.evaluate(weight, stored, live('10'), context(weight_pct=Decimal('70'), observation=observation))

    assert at(NOW, 8).fired, 'a new ledger revision under the same quote and rate'
    assert at(NOW, 3).fired, 'a lower revision too: a restored backup is not older data'
    behind = at(NOW - timedelta(seconds=1), 8)
    assert behind.reason == 'stale_observation' and 'HKD 的汇率' in behind.message
    assert at(NOW + timedelta(seconds=1), 8).state.observation.ledger == 8


def test_the_stored_observation_survives_its_json_form():
    observation = engine.Observation.of({engine.quote_input('HKG:700'): NOW, engine.fx_input('HKD'): None}, ledger=4)
    assert observation.inputs == (('quote:HKG:00700', NOW),) and observation.at == NOW
    assert engine.Observation.from_json(observation.to_json()) == observation
    for broken in (None, [], {'inputs': 'x'}, {'inputs': {'quote:X': 'not a time'}, 'ledger': True}):
        assert engine.Observation.from_json(broken) in (None, engine.Observation())


def test_a_disabled_rule_is_skipped():
    decision = engine.evaluate(rule(enabled=False), None, live('9'), context())
    assert decision.action == 'skip' and decision.reason == 'disabled' and not decision.changed


# -- untrusted quotes ---------------------------------------------------------

@pytest.mark.parametrize('status, code', [('stale', 'quote_stale'), ('time_unknown', 'quote_time_unknown')])
def test_stale_and_time_unknown_quotes_never_fire(status, code):
    stored = RuleState(armed=True, last_trading_date=TODAY)
    decision = engine.evaluate(rule(), stored, live('5', status=status), context())
    assert (decision.action, decision.reason) == ('skip', code)
    assert decision.state == stored and not decision.changed and decision.message


def test_unavailable_or_missing_quotes_never_fire():
    stored = RuleState(armed=True, last_trading_date=TODAY)
    unavailable = quote(TICKER, None, reason='数据源中断')
    assert unavailable.status == 'unavailable'
    for given, code in ((unavailable, 'quote_unavailable'), (None, 'no_quote')):
        decision = engine.evaluate(rule(), stored, given, context())
        assert (decision.action, decision.reason, decision.state) == ('skip', code, stored)
    assert '数据源中断' in engine.evaluate(rule(), stored, unavailable, context()).message


def test_nothing_is_evaluated_outside_the_session():
    for status in ('closed', 'live'):
        decision = engine.evaluate(rule(), None, live('5', status=status), context(session_open=False))
        assert (decision.action, decision.reason) == ('skip', 'session_closed')


def test_a_quote_still_at_the_previous_close_is_not_a_price():
    untraded = replace(live('9', prev_close='9', status='closed'), price_basis=PREV_CLOSE)
    decision = engine.evaluate(rule(), None, untraded, context())
    assert (decision.action, decision.reason) == ('skip', 'no_trade_today')


def test_the_prev_close_status_means_no_trade_today_not_a_quote_failure():
    """QuoteService labels an untraded or suspended security 'prev_close'; that is no outage."""
    untraded = replace(live('9', prev_close='9', status='prev_close'), price_basis=PREV_CLOSE)
    decision = engine.evaluate(rule(), None, untraded, context())
    assert (decision.action, decision.reason) == ('skip', 'no_trade_today')
    assert decision.reason not in engine.QUOTE_FAILURES


def test_a_closed_label_in_a_fail_open_session_is_used_only_while_recent():
    recent = engine.evaluate(rule(), None, live('9.9', status='closed', age=60), context())
    assert recent.fired
    old = engine.evaluate(rule(), None, live('9.9', status='closed', age=30 * 60), context())
    assert (old.action, old.reason) == ('skip', 'quote_too_old')


def test_delayed_quotes_are_evaluated():
    assert engine.evaluate(rule(), None, live('9.9', status='delayed', age=300), context()).fired


# -- daily change -------------------------------------------------------------

def test_negative_day_change_threshold_watches_the_fall_only():
    fall = rule(engine.DAY_CHANGE_PCT, '-5')
    actions, state = walk(fall, ['10.6', '9.6', '9.5', '9.3', '9.54', '9.55', '9.4'])
    # -5 % at 9.5; re-arm at -4.5 % (9.55).
    assert actions == ['none', 'none', 'fire', 'wait', 'none', 'rearm', 'fire']
    first = engine.evaluate(fall, None, live('9.5'), context())
    assert first.value == Decimal('-5')


def test_positive_day_change_threshold_watches_the_rise_only():
    rise = rule(engine.DAY_CHANGE_PCT, '5')
    actions, _ = walk(rise, ['9.0', '10.4', '10.5', '10.7', '10.46', '10.45', '10.6'])
    assert actions == ['none', 'none', 'fire', 'wait', 'none', 'rearm', 'fire']


def test_day_change_without_a_previous_close_is_skipped():
    decision = engine.evaluate(rule(engine.DAY_CHANGE_PCT, '-5'), None, live('5', prev_close=None), context())
    assert (decision.action, decision.reason) == ('skip', 'no_prev_close')


# -- position weight ----------------------------------------------------------

def test_weight_rule_uses_the_given_weight_and_rearms_half_a_point_below():
    cap = rule(engine.POSITION_WEIGHT_MAX, '20')
    state = None
    actions = []
    for weight in ('19.9', '20', '25', '19.6', '19.5', '21'):
        decision = engine.evaluate(cap, state, live('10.5'), context(weight_pct=Decimal(weight)))
        actions.append(decision.action)
        state = decision.state
    assert actions == ['none', 'fire', 'wait', 'none', 'rearm', 'fire']


def test_weight_rule_is_skipped_when_the_weight_is_unavailable():
    decision = engine.evaluate(rule(engine.POSITION_WEIGHT_MAX, '20'), None, live('10.5'),
                               context(weight_pct=None, weight_reason='汇率不可用'))
    assert (decision.action, decision.reason) == ('skip', 'weight_unavailable') and '汇率不可用' in decision.message


def test_weight_rule_still_needs_a_trusted_quote_but_accepts_an_untraded_one():
    cap = rule(engine.POSITION_WEIGHT_MAX, '20')
    stale = engine.evaluate(cap, None, live('10.5', status='stale'), context(weight_pct=Decimal('30')))
    assert stale.reason == 'quote_stale'
    untraded = replace(live('10', status='closed'), price_basis=PREV_CLOSE)
    assert engine.evaluate(cap, None, untraded, context(weight_pct=Decimal('30'))).fired


def _valuation(prices, stale=()):
    state = {'accounts': {
        'Main': {'cash_balances': {'CNY': '1000'}, 'fund_cny': '0',
                 'positions': {TICKER: {'quantity': '100', 'cost_price': '8', 'currency': 'CNY', 'name': '示例甲'}}},
        'Other': {'cash_balances': {'CNY': '0'}, 'fund_cny': '0',
                  'positions': {TICKER: {'quantity': '100', 'cost_price': '8', 'currency': 'CNY', 'name': '示例甲'},
                                'SHA:600002': {'quantity': '50', 'cost_price': '20', 'currency': 'CNY'}}}}}
    quotes = {ticker: replace(quote(ticker, price, prev_close=price, at=NOW),
                              status='stale' if ticker in stale else 'live')
              for ticker, price in prices.items()}
    return value_portfolio(state, quotes, {}, None, [])


def test_a_weight_is_used_only_when_every_input_is_verified():
    cap = rule(engine.POSITION_WEIGHT_MAX, '20')
    elsewhere = _valuation({TICKER: '10', 'SHA:600002': '20'}, stale={'SHA:600002'})
    assert elsewhere.quality == 'estimated' and elsewhere.total_cny == 4000
    weight, code, reason = engine.weight_check(cap, elsewhere)
    assert (weight, code) == (None, 'weight_estimated') and 'SHA:600002' in reason
    own = engine.weight_check(cap, _valuation({TICKER: '10', 'SHA:600002': '20'}, stale={TICKER}))
    assert own[:2] == (None, 'weight_estimated') and TICKER in own[2]
    assert engine.weight_check(cap, _valuation({TICKER: '10', 'SHA:600002': '20'})) == (Decimal(50), None, None)
    decision = engine.evaluate(cap, None, live('10.5'),
                               context(weight_code='weight_estimated', weight_reason='SHA:600002：行情已过期'))
    assert (decision.action, decision.reason, decision.changed) == ('skip', 'weight_estimated', False)
    assert '未核实' in decision.message and 'SHA:600002' in decision.message


def test_weight_comes_from_the_valuation_summed_over_accounts_or_for_one_account():
    valuation = _valuation({TICKER: '10', 'SHA:600002': '20'})   # total 1000 + 1000 + 1000 + 1000 = 4000
    cap = rule(engine.POSITION_WEIGHT_MAX, '20')
    assert engine.weight_for(cap, valuation) == (Decimal(50), None)
    assert engine.weight_for(replace(cap, account='Main'), valuation) == (Decimal(25), None)
    assert engine.weight_for(replace(cap, ticker='SHA:600003'), valuation) == (Decimal(0), None)
    assert engine.weight_for(replace(cap, account='Missing'), valuation) == (None, '账本中没有这个账户')


def test_weight_is_unavailable_when_any_holding_cannot_be_valued():
    weight, reason = engine.weight_for(rule(engine.POSITION_WEIGHT_MAX, '20'), _valuation({TICKER: '10'}))
    assert weight is None and reason.startswith('组合总值不可用')
    assert engine.weight_for(rule(engine.POSITION_WEIGHT_MAX, '20'), None)[0] is None


# -- text built from numbers ----------------------------------------------------

@pytest.mark.parametrize('value', ['1e22', '1E+999999', '-1E+999999', '1E-999999', '123456789012345678901234567.123'])
def test_formatting_a_number_never_raises_and_stays_short(value):
    number = Decimal(value)
    for text in (messages.number(number, 8), messages.number(number), messages.percent(number),
                 engine.describe_condition(engine.STOP_LOSS, number),
                 engine.describe_condition(engine.DAY_CHANGE_PCT, number)):
        assert isinstance(text, str) and len(text) <= 60, text


def test_a_stored_threshold_out_of_range_is_skipped_not_evaluated():
    """A rule stored before thresholds were bounded can neither fire nor raise."""
    legacy = rule(threshold='1e22')
    decision = engine.evaluate(legacy, None, live('9'), context())
    assert (decision.action, decision.reason, decision.changed) == ('skip', 'invalid_threshold', False)
    assert '阈值' in decision.message


# -- distance -----------------------------------------------------------------

def test_distance_is_positive_until_the_condition_holds():
    assert engine.distance(rule(), Decimal('10.5'), live('10.5')) == (Decimal('0.5'), Decimal('0.5') / Decimal('10.5') * 100)
    assert engine.distance(rule(engine.TARGET_PRICE, '12'), Decimal('10'), live('10')) == (Decimal(2), Decimal(20))
    gap, pct = engine.distance(rule(), Decimal('9.5'), live('9.5'))
    assert gap < 0 and pct < 0
    # -5 % from a previous close of 10 triggers at 9.5: 3 points away, 0.3 / 9.8 of the price.
    fall = rule(engine.DAY_CHANGE_PCT, '-5')
    gap, pct = engine.distance(fall, engine.change_pct(live('9.8')), live('9.8'))
    assert gap == Decimal('3') and pct == Decimal('0.3') / Decimal('9.8') * 100
    assert engine.distance(rule(engine.POSITION_WEIGHT_MAX, '20'), Decimal('15'), live('10'))[1] is None
