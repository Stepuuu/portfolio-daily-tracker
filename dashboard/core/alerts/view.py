"""Rules as the alert page and the agent see them: with the current price and the distance to fire.

``with_market(items, runtime, ledger)`` adds to every rule from
``rules.list()``::

    "market": {
      "price", "prev_close", "change_pct", "weight_pct",  decimal strings or null
      "value",              what the rule compares (price, change % or weight %)
      "distance",           value to threshold in the rule's unit, positive
                            while the condition does not hold; null when the
                            quote cannot be trusted (stale, time unknown, ...)
      "distance_pct",       how far the price must move to fire, in % of the
                            current price; null for weight rules
                            (both with two decimals, a price distance with
                            up to six; a value that is not zero but would
                            round to zero keeps two significant digits instead)
      "unit",               "price" or "pct_points"
      "condition_met",      the engine's own comparison of the value with the
                            threshold (``engine.condition_met``): true when the
                            condition holds, false when not, null with the
                            distance; never read it off the rounded distance
      "checked_now",        the monitor would evaluate this rule right now
      "skip_reason",        why not (Chinese), or null
      "skip_code",          the same as a machine-readable code (``session_closed``,
                            ``quote_stale``, ``weight_estimated``,
                            ``stale_observation`` ...), or null; ``demo``
                            (``DEMO_REASON``) for every rule in the read-only
                            demo, which never runs the monitor
      "skipped_since",      when the monitor began skipping the rule for this
                            reason (ISO time), or null when it does not
      "quote": {source, quote_time, fetched_at, status, reason}
    }

The reasons are computed here from fresh quotes, except the ones only the
monitor can tell (``monitor.MONITOR_SKIPS``: an observation older than the
one behind the last change, a session date that went back), which come from
its latest cycle (rows 9 and 11 of the decision table in ``core.alerts.monitor``).

and ``fires_left_today`` to its state. Quotes come from one batched request.

Weights value the confirmed ledger as of today, as the monitor and the Today
page do (``core.alerts.holdings``). By default the portfolio's ledger is read
read-only, and only when a weight rule is shown with quotes: listing rules
never opens the ledger for writing, never creates or grows it.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from core.market.quotes import CLOSED, DELAYED, LIVE, LiveQuote
from core.market.today import moment, money, plain
from core.market.valuation import value_portfolio

from . import engine, holdings, monitor, rules
from .engine import MAX_FIRES_PER_DAY, PERCENT_KINDS, POSITION_WEIGHT_MAX, Context

TRUSTED = (LIVE, DELAYED, CLOSED)
# The read-only demo never starts the monitor (core.alerts.monitor): no rule there is being checked.
DEMO_SKIP = 'demo'
DEMO_REASON = '演示模式不检查提醒'
# ``with_market(ledger=...)``: read the portfolio's own ledger, read-only, when a weight needs it.
PORTFOLIO_LEDGER = object()


def _quote(quote: Optional[LiveQuote]) -> Optional[dict]:
    if quote is None:
        return None
    return {'source': quote.source, 'quote_time': moment(quote.quote_time), 'fetched_at': moment(quote.fetched_at),
            'status': quote.status, 'reason': quote.reason}


def _empty(reason: str) -> dict:
    return {'price': None, 'prev_close': None, 'change_pct': None, 'weight_pct': None, 'value': None,
            'distance': None, 'distance_pct': None, 'unit': None, 'condition_met': None, 'checked_now': False,
            'skip_reason': reason, 'skip_code': 'quotes_not_requested', 'skipped_since': None, 'quote': None}


def _gap(value: Optional[Decimal], shown: Callable[[Optional[Decimal]], Optional[str]]) -> Optional[str]:
    """``shown(value)``; when that rounds a value that is not zero to zero, two significant digits instead
    (``0.0025``), so a distance never reads as 0 while the condition does not hold."""
    text = shown(value)
    if value is None or text is None or value == 0 or not value.is_finite() or Decimal(text) != 0:
        return text
    rounded = format(value.quantize(Decimal(1).scaleb(value.adjusted() - 1), rounding=ROUND_HALF_UP), 'f')
    return rounded.rstrip('0').rstrip('.') if '.' in rounded else rounded


def _fires_left(item: Mapping, clock) -> int:
    state = item['state']
    rule = rules.to_rule(item)
    if rule.market is None or state.get('last_trading_date') != clock.local_date(rule.market).isoformat():
        return MAX_FIRES_PER_DAY
    return max(0, MAX_FIRES_PER_DAY - int(state.get('fire_count_today') or 0))


def with_market(items: Sequence[dict], runtime, ledger=PORTFOLIO_LEDGER, *, quotes: bool = True) -> List[dict]:
    """``items`` with ``market`` and ``state.fires_left_today`` filled in (see the module docstring).

    ``ledger``: by default the portfolio's own ledger, read-only; a
    ``core.ledger.Ledger`` to value instead; ``None`` for no ledger (weights
    unavailable).
    """
    clock = runtime.clock
    result = [{**item, 'state': {**item['state'], 'fires_left_today': _fires_left(item, clock)}} for item in items]
    if not result:
        return result
    if not quotes:
        for item in result:
            item['market'] = _empty('未请求行情')
        return result

    beat = monitor.heartbeat()
    parsed = [rules.to_rule(item) for item in result]
    needs_weights = any(rule.kind == POSITION_WEIGHT_MAX for rule in parsed)
    state, ledger_problem = None, None
    if needs_weights:
        if ledger is None:
            ledger_problem = holdings.NO_HOLDINGS
        else:
            state, ledger_problem = holdings.confirmed_holdings(clock, None if ledger is PORTFOLIO_LEDGER else ledger)
    held = [ticker for account in (state or {}).get('accounts', {}).values() for ticker in account['positions']]
    fetched: Dict[str, LiveQuote] = runtime.quotes.get(list(dict.fromkeys([rule.ticker for rule in parsed] + held)))
    valuation = value_portfolio(state, fetched, runtime.quotes.fx(), None, []) if state is not None else None
    now = clock.now()
    recorded = monitor.recent_skips(beat, now)
    demo = monitor.demo_mode()

    for item, rule in zip(result, parsed):
        quote = fetched.get(rule.ticker)
        weight, weight_code, weight_reason = None, None, None
        if rule.kind == POSITION_WEIGHT_MAX:
            weight, weight_code, weight_reason = (engine.weight_check(rule, valuation) if valuation is not None
                                                  else (None, engine.WEIGHT_UNAVAILABLE, ledger_problem))
        open_now = monitor.session_open(clock, rule.market, beat)
        context = Context(now=now, session_open=open_now, trading_date=clock.local_date(rule.market),
                          weight_pct=weight, weight_reason=weight_reason, weight_code=weight_code)
        value, value_problem = engine.observed_value(rule, quote, context)
        broken = engine.rule_problem(rule)
        problem = broken or engine.quote_problem(rule, quote, context) or value_problem
        skipped = recorded.get(rule.id) if open_now else None
        if problem is None and skipped is not None and skipped.get('code') in monitor.MONITOR_SKIPS:
            problem = (skipped['code'], skipped.get('message') or '')
        if demo and broken is None:
            problem = (DEMO_SKIP, DEMO_REASON)        # whatever the quote says: nobody checks the rule here
        since = skipped.get('since') if skipped is not None and problem and skipped.get('code') == problem[0] else None
        trusted = quote is not None and quote.status in TRUSTED and quote.price is not None and broken is None
        gap, gap_pct = engine.distance(rule, value, quote) if trusted and value is not None else (None, None)
        percent_kind = rule.kind in PERCENT_KINDS
        item['market'] = {
            'price': plain(quote.price) if quote else None,
            'prev_close': plain(quote.prev_close) if quote else None,
            'change_pct': money(engine.change_pct(quote)),
            'weight_pct': money(weight) if rule.kind == POSITION_WEIGHT_MAX else None,
            'value': (money(value) if percent_kind else plain(value)) if value is not None else None,
            'distance': _gap(gap, money if percent_kind else plain),
            'distance_pct': _gap(gap_pct, money),
            'unit': 'pct_points' if percent_kind else 'price',
            'condition_met': engine.condition_met(rule, value) if gap is not None else None,
            'checked_now': problem is None,
            'skip_reason': problem[1] if problem else None,
            'skip_code': problem[0] if problem else None,
            'skipped_since': since,
            'quote': _quote(quote)}
    return result
