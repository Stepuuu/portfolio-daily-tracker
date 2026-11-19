"""Edge-triggered alert decisions. Pure: no I/O, no clock, no globals.

``evaluate(rule, state, quote, context)`` looks at one rule, its stored state
and the latest quote, and returns a ``Decision`` with the new state. The
caller (``core.alerts.monitor``) persists that state and, for a ``fire``,
the notification, in one transaction.

What each kind watches (``value``), when it fires and when it re-arms:

==================== ============================ ===================== ==========================
kind                 value                        fires when            re-arms when (hysteresis)
==================== ============================ ===================== ==========================
stop_loss            last price                   price <= threshold    price >= threshold x 1.005
target_price         last price                   price >= threshold    price <= threshold x 0.995
day_change_pct       (price - prev close) / prev  t < 0: value <= t     value >= t + 0.5
                     close x 100, in percent      t > 0: value >= t     value <= t - 0.5
position_weight_max  the valuation's weight_pct   value >= threshold    value <= threshold - 0.5
==================== ============================ ===================== ==========================

``day_change_pct`` takes a *signed* threshold: ``-5`` means "down 5 % or
more", ``5`` means "up 5 % or more". A rule watches one direction; watching
both takes two rules. Zero is not a valid threshold. Percentage rules use a
band of 0.5 percentage points, price rules 0.5 % of the threshold.

``position_weight_max`` is evaluated only on a verified valuation: the
portfolio totals' quality must be ``complete`` and the position's own row
neither estimated nor unavailable. Otherwise the rule is skipped with reason
``weight_estimated`` (or ``weight_unavailable``) and its state is left alone;
a stale or untraded price elsewhere in the portfolio would distort the
denominator (see ``weight_check``).

Firing is edge-triggered. An armed rule fires when its condition holds, then
it is disarmed. It re-arms when the value has recovered beyond the band, or
on the next trading day of its market (so a position still below its stop
at the next session is reported once more). A rule fires at most
``MAX_FIRES_PER_DAY`` times per trading day, and not again within
``cooldown_seconds`` of its last fire. A new rule starts armed, so a
condition that already holds at the first check in session fires once.

A quote is used only when it can be trusted. ``stale``, ``time_unknown`` and
``unavailable`` quotes, and no quote at all, give a ``skip`` decision with a
reason and leave the state untouched. Outside the session nothing is
evaluated. In session, a quote that still shows the previous close (no trade
yet, or suspended) is not a price for price rules. A ``closed`` quote in a
session that the calendar does not know about (the monitor's fail-open case)
is used only when its exchange time is recent.

State changes never go back in time. ``Context.observation`` (an
``Observation``) says what the decision looks at: the exchange time of every
input (the rule's quote; for a weight rule the quote of every holding and the
rate of every currency held) and, for a weight rule, the ledger revision. An
applied change stores its observation with the state. A decision that would
change the state is dropped (``skip``, reason ``stale_observation``, with a
message naming the input) when one of its inputs has an *earlier* exchange
time than the same input behind the last change: a source that fell back to
older data must not re-arm a rule that newer data fired. Equal times are not
older (a source with coarse timestamps reports new prices under one time),
and the ledger revision has no order (a restored backup lowers it): neither
holds a change back.

A verified close (a quote with status ``closed``) is one observation per
session, whatever time a source stamps on it: it is the input
``close:<listing>`` dated by its session (midnight of its exchange-local
date), so a close reported at 15:05 by one source and at 15:00 by another is
the same observation, and only the close of an *earlier* session is older.
An open-market quote is ``quote:<listing>`` at its exchange time; the two
names are never compared with each other. Listings are compared by
``core.market.search.identity_key`` (``HKG:700`` = ``HKG:00700``,
``NYSE:BRK-B`` = ``NYSE:BRK.B``).

A held-back change (``stale_observation``) is released at the first decision
in which no input is older than the same input behind the last change, which
happens when:

1. the input catches up (an equal or later exchange time; for a close, the
   same or a later session);
2. the input is no longer an input: the holding was sold, the currency's cash
   went to zero, the rule's quote is missing (then another skip applies);
3. the stored time lies more than ``FUTURE_TOLERANCE`` after ``now`` (it was
   written before the clock was put back);
4. the rule is edited (threshold, account) or enabled again, which forgets
   the stored observation (``core.alerts.rules.update``).

How long an input can stay behind: an open-market quote at most
``DELAYED_MAX_AGE`` (older, it is stale and the rule, or for a weight rule
the valuation, is skipped for that reason instead); a rate at most
``FX_DELAYED_MAX_AGE`` on weekdays; a close only while a source reports an
earlier session's close, without a time limit. The monitor announces a
weight rule held back for 30 minutes in a row, whatever the reasons
(``core.alerts.monitor``). Stale *cycles* (a monitor that read its data
before a newer cycle changed the rule, or whose data is no longer current)
are excluded by the monitor, not here. A decision that changes nothing is
reported as usual. ``Context.observed_at`` alone is read as the observation
of the rule's own quote.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Mapping, Optional, Tuple

from core.market.calendar import SHANGHAI, ZONES
from core.market.instruments import InstrumentError, parse
from core.market.search import identity_key
from core.market.quotes import (CLOSED, DELAYED, DELAYED_MAX_AGE, FUTURE_TOLERANCE, LIVE, PREV_CLOSE,
                                PREV_CLOSE_ONLY, STALE, TIME_UNKNOWN, UNAVAILABLE, LiveQuote)

STOP_LOSS, TARGET_PRICE = 'stop_loss', 'target_price'
DAY_CHANGE_PCT, POSITION_WEIGHT_MAX = 'day_change_pct', 'position_weight_max'
KINDS = (STOP_LOSS, TARGET_PRICE, DAY_CHANGE_PCT, POSITION_WEIGHT_MAX)
PRICE_KINDS = (STOP_LOSS, TARGET_PRICE)
PERCENT_KINDS = (DAY_CHANGE_PCT, POSITION_WEIGHT_MAX)
KIND_LABELS = {STOP_LOSS: '止损', TARGET_PRICE: '目标价', DAY_CHANGE_PCT: '涨跌幅', POSITION_WEIGHT_MAX: '仓位上限'}
# Importance, most important first: the order in which a cycle evaluates (and
# so notifies) its rules and in which a digest lists what was held back.
PRIORITY = (STOP_LOSS, POSITION_WEIGHT_MAX, TARGET_PRICE, DAY_CHANGE_PCT)


def priority(kind: str) -> int:
    return PRIORITY.index(kind) if kind in PRIORITY else len(PRIORITY)

MAX_FIRES_PER_DAY = 3
REARM_FRACTION = Decimal('0.005')   # price rules: 0.5 % of the threshold
REARM_POINTS = Decimal('0.5')       # percentage rules: 0.5 percentage points
HUNDRED = Decimal(100)

# Bounds of a threshold, checked before it is normalised or stored
# (``core.alerts.rules``). The largest magnitude is the largest price the
# quote service accepts; percentage kinds have their own, narrower ranges.
MAX_THRESHOLD_TEXT = 32
MAX_THRESHOLD = Decimal(100000000)
MAX_THRESHOLD_PLACES = 8
_PLAIN_LIMIT = Decimal('1E+16')     # ``plain`` switches to the short scientific form from here

# Decision actions.
FIRE, REARM, WAIT, NONE, SKIP = 'fire', 'rearm', 'wait', 'none', 'skip'
# Skip reasons of a weight rule whose weight may not be used.
WEIGHT_UNAVAILABLE, WEIGHT_ESTIMATED = 'weight_unavailable', 'weight_estimated'

# Skip reasons that mean "the market data could not be used", as opposed to
# a property of this one instrument. The monitor counts a cycle in which
# every rule hit one of these as a failed cycle.
QUOTE_FAILURES = frozenset({'no_quote', 'quote_unavailable', 'quote_stale', 'quote_time_unknown', 'quote_too_old'})


@dataclass(frozen=True)
class Rule:
    id: int
    ticker: str
    kind: str
    threshold: Decimal
    account: Optional[str] = None
    enabled: bool = True
    cooldown_seconds: int = 0
    note: Optional[str] = None
    source: str = 'manual'

    @property
    def market(self) -> Optional[str]:
        try:
            return parse(self.ticker).market
        except InstrumentError:
            return None


def listing(ticker: str) -> str:
    """What makes two tickers one listing (``identity_key``): HK zero padding and US class-share dot or dash."""
    return identity_key(ticker) or str(ticker)


def quote_input(ticker: str) -> str:
    """The name of an open-market quote among an observation's inputs: one per listing, whatever the spelling."""
    return f'quote:{listing(ticker)}'


def close_input(ticker: str) -> str:
    """The name of a verified close among an observation's inputs (see the module docstring)."""
    return f'close:{listing(ticker)}'


def fx_input(currency: str) -> str:
    return f'fx:{currency}'


def quote_observed(ticker: str, quote: Optional[LiveQuote]) -> Optional[Tuple[str, datetime]]:
    """``(input name, time)`` of a quote in an observation, or ``None`` without an exchange time.

    A verified close (status ``closed``) is ``close:<listing>`` at midnight of
    its session's exchange-local date: two reports of one close are one
    observation whatever their timestamps.
    """
    if quote is None or quote.quote_time is None:
        return None
    if quote.status == CLOSED:
        try:
            zone = ZONES[parse(ticker).market]
        except (InstrumentError, KeyError):
            zone = SHANGHAI
        day = quote.quote_time.astimezone(zone).date()
        return close_input(ticker), datetime.combine(day, time(0), tzinfo=zone)
    return quote_input(ticker), quote.quote_time


@dataclass(frozen=True)
class Observation:
    """What a decision looked at (see the module docstring).

    ``inputs`` is ``((name, exchange time), ...)`` sorted by name, names from
    ``quote_input`` and ``fx_input``; ``ledger`` the ledger revision a weight
    was computed from (``None`` for the other kinds).
    """
    inputs: Tuple[Tuple[str, datetime], ...] = ()
    ledger: Optional[int] = None

    @classmethod
    def of(cls, times: Mapping[str, Optional[datetime]], ledger: Optional[int] = None) -> 'Observation':
        return cls(tuple(sorted((name, moment) for name, moment in times.items() if moment is not None)), ledger)

    @property
    def at(self) -> Optional[datetime]:
        """The latest exchange time among the inputs."""
        return max((moment for _name, moment in self.inputs), default=None)

    def behind(self, stored: 'Observation', now: datetime) -> Optional[Tuple[str, datetime, datetime]]:
        """``(name, this time, stored time)`` of an input older here than in ``stored``, else ``None``.

        A stored time more than ``FUTURE_TOLERANCE`` ahead of ``now`` is ignored.
        """
        theirs = dict(stored.inputs)
        for name, mine in self.inputs:
            other = theirs.get(name)
            if other is not None and mine < other <= now + FUTURE_TOLERANCE:
                return name, mine, other
        return None

    def to_json(self) -> dict:
        return {'inputs': {name: moment.isoformat() for name, moment in self.inputs}, 'ledger': self.ledger}

    @classmethod
    def from_json(cls, data) -> Optional['Observation']:
        """The stored form back, or ``None`` when it is not one (never raises)."""
        if not isinstance(data, Mapping) or not isinstance(data.get('inputs'), Mapping):
            return None
        times = {}
        for name, text in data['inputs'].items():
            try:
                moment = datetime.fromisoformat(text) if isinstance(name, str) and isinstance(text, str) else None
            except ValueError:
                moment = None
            if moment is not None and moment.tzinfo is not None:
                times[name] = moment
        ledger = data.get('ledger')
        return cls.of(times, ledger if isinstance(ledger, int) and not isinstance(ledger, bool) else None)


@dataclass(frozen=True)
class RuleState:
    armed: bool = True
    last_fired_at: Optional[datetime] = None
    last_trading_date: Optional[date] = None   # the day ``fire_count_today`` counts
    fire_count_today: int = 0
    # The latest exchange time behind the last change of this state (kept for
    # stores of the previous release; ``observation`` says it all).
    observed_at: Optional[datetime] = None
    # The observation behind the last change of this state.
    observation: Optional[Observation] = None


@dataclass(frozen=True)
class Context:
    """What the caller knows besides the quote.

    now            the session clock's current time (aware)
    session_open   the rule's market is in continuous trading, by the calendar
                   or by the monitor's fail-open check
    trading_date   the market's session date; falls back to the quote's
                   exchange date when ``None``
    weight_pct     for ``position_weight_max``: the position's weight, or
                   ``None`` with ``weight_reason`` and ``weight_code``
                   (``weight_unavailable`` or ``weight_estimated``; see
                   ``weight_check``)
    observation    what the decision looks at (``Observation``); ``None``
                   leaves the ordering of state changes unchecked
    observed_at    shorthand for an observation of the rule's own quote at
                   this exchange time (used when ``observation`` is ``None``)
    """
    now: datetime
    session_open: bool
    trading_date: Optional[date] = None
    weight_pct: Optional[Decimal] = None
    weight_reason: Optional[str] = None
    observed_at: Optional[datetime] = None
    weight_code: Optional[str] = None
    observation: Optional[Observation] = None


@dataclass(frozen=True)
class Decision:
    action: str                    # FIRE, REARM, WAIT, NONE or SKIP
    reason: str                    # machine-readable code
    message: str                   # Chinese, for the UI
    state: RuleState               # the state to store (unchanged for SKIP)
    changed: bool = False          # ``state`` differs from the stored one
    value: Optional[Decimal] = None
    trading_date: Optional[date] = None
    fire_number: Optional[int] = None   # 1..MAX_FIRES_PER_DAY for FIRE

    @property
    def fired(self) -> bool:
        return self.action == FIRE


# -- numbers as text, without a context that could round or raise --------------

def _significant(number: Decimal) -> Tuple[int, Tuple[int, ...], int]:
    """``(sign, digits, exponent)`` of a finite ``number`` with trailing zeros removed."""
    sign, digits, exponent = number.as_tuple()
    kept = len(digits)
    while kept > 1 and digits[kept - 1] == 0:
        kept -= 1
    return sign, tuple(digits[:kept]), exponent + len(digits) - kept


def decimal_places(number: Decimal) -> int:
    """Digits after the point, trailing zeros not counted. Exact for any finite number."""
    sign, digits, exponent = _significant(number)
    return 0 if digits == (0,) else max(0, -exponent)


def threshold_supported(threshold: Decimal) -> bool:
    """Finite, at most ``MAX_THRESHOLD`` in magnitude and ``MAX_THRESHOLD_PLACES`` places."""
    return (isinstance(threshold, Decimal) and threshold.is_finite()
            and threshold.copy_abs() <= MAX_THRESHOLD and decimal_places(threshold) <= MAX_THRESHOLD_PLACES)


def scientific(number: Decimal) -> str:
    """A short ``1.234567E+22`` form for a number too large or too precise to print plainly."""
    if not number.is_finite():
        return str(number)
    sign, digits, _exponent = _significant(number)
    head = ''.join(str(digit) for digit in digits[:7])
    mantissa = head[0] + ('.' + head[1:] if len(head) > 1 else '')
    return f'{"-" if sign else ""}{mantissa}E{number.adjusted():+d}'


def plain(number: Decimal) -> str:
    """``number`` without exponent or trailing zeros (``9.5``, ``100``); ``scientific`` beyond the bounds.

    Never raises and never grows past a few dozen characters.
    """
    if not number.is_finite():
        return str(number)
    if number.copy_abs() >= _PLAIN_LIMIT or decimal_places(number) > 2 * MAX_THRESHOLD_PLACES:
        return scientific(number)
    text = format(Decimal(_significant(number)), 'f')
    return '0' if text in ('-0', '') else text


def rule_problem(rule: Rule) -> Optional[Tuple[str, str]]:
    """``(code, message)`` when the rule itself cannot be evaluated (a threshold stored before it was bounded)."""
    if not threshold_supported(rule.threshold):
        return 'invalid_threshold', ('阈值超出支持的范围（绝对值不超过 1 亿、最多 8 位小数），这条规则不会被检查；'
                                     '请修改阈值或删除这条规则')
    return None


# -- the value a rule watches -------------------------------------------------

def change_pct(quote: Optional[LiveQuote]) -> Optional[Decimal]:
    if quote is None or quote.price is None or quote.prev_close is None or quote.prev_close <= 0:
        return None
    return (quote.price - quote.prev_close) / quote.prev_close * HUNDRED


def quote_problem(rule: Rule, quote: Optional[LiveQuote], context: Context) -> Optional[Tuple[str, str]]:
    """``(code, message)`` when the quote may not be used for ``rule`` now, else ``None``."""
    if not context.session_open:
        return 'session_closed', '不在交易时段，不检查提醒'
    if quote is None:
        return 'no_quote', '没有行情'
    if quote.status == UNAVAILABLE or quote.price is None:
        return 'quote_unavailable', f'行情不可用：{quote.reason or "没有有效价格"}'
    if quote.status == STALE:
        return 'quote_stale', f'行情已过期：{quote.reason or "长时间未更新"}'
    if quote.status == TIME_UNKNOWN:
        return 'quote_time_unknown', f'行情缺少可信的交易所时间：{quote.reason or "无法判断时效"}'
    if quote.status == PREV_CLOSE_ONLY or quote.price_basis == PREV_CLOSE:
        # Untraded this session (or suspended): no price yet, but no outage either.
        if rule.kind != POSITION_WEIGHT_MAX:
            return 'no_trade_today', '本交易日尚无成交（价格为昨收），不据此提醒'
        return None
    if quote.status not in (LIVE, DELAYED, CLOSED):
        return 'quote_unavailable', '行情状态未知'
    if quote.status == CLOSED:
        # In session the service labels a quote ``closed`` only when the
        # calendar disagrees with the exchange; then its own time decides.
        if quote.quote_time is None or context.now - quote.quote_time > DELAYED_MAX_AGE:
            return 'quote_too_old', '日历显示休市，且行情的交易所时间不是最近的'
    return None


def observed_value(rule: Rule, quote: Optional[LiveQuote], context: Context) -> Tuple[Optional[Decimal], Optional[Tuple[str, str]]]:
    """The number the rule compares with its threshold, or why there is none."""
    if rule.kind in PRICE_KINDS:
        return quote.price if quote is not None else None, None
    if rule.kind == DAY_CHANGE_PCT:
        value = change_pct(quote)
        return value, None if value is not None else ('no_prev_close', '缺少昨收价，无法计算涨跌幅')
    if rule.kind == POSITION_WEIGHT_MAX:
        if context.weight_pct is None:
            if context.weight_code == WEIGHT_ESTIMATED:
                return None, (WEIGHT_ESTIMATED, f'组合估值含未核实的价格或汇率，暂不按仓位提醒：{context.weight_reason}')
            return None, (WEIGHT_UNAVAILABLE, f'仓位权重不可用：{context.weight_reason or "估值不完整"}')
        return context.weight_pct, None
    return None, ('unknown_kind', '未知的提醒类型')


def condition_met(rule: Rule, value: Decimal) -> bool:
    if rule.kind not in KINDS:
        return False
    return value <= rule.threshold if _downward(rule) else value >= rule.threshold


def rearm_level(rule: Rule) -> Decimal:
    """The value beyond which a fired rule is armed again."""
    if rule.kind == STOP_LOSS:
        return rule.threshold * (1 + REARM_FRACTION)
    if rule.kind == TARGET_PRICE:
        return rule.threshold * (1 - REARM_FRACTION)
    if rule.kind == DAY_CHANGE_PCT and rule.threshold < 0:
        return rule.threshold + REARM_POINTS
    return rule.threshold - REARM_POINTS


def recovered(rule: Rule, value: Decimal) -> bool:
    level = rearm_level(rule)
    return value >= level if _downward(rule) else value <= level


def trading_date_of(rule: Rule, quote: Optional[LiveQuote], context: Context) -> Optional[date]:
    if context.trading_date is not None:
        return context.trading_date
    if quote is None or quote.quote_time is None:
        return None
    market = rule.market
    if market is None:
        return None
    return quote.quote_time.astimezone(ZONES[market]).date()


# -- the decision -------------------------------------------------------------

def _decision(action, reason, message, old: RuleState, new: RuleState, **fields) -> Decision:
    return Decision(action, reason, message, new, changed=new != old, **fields)


STALE_OBSERVATION = 'stale_observation'


def _input_label(name: str) -> str:
    kind, _, subject = name.partition(':')
    return {'fx': f'{subject} 的汇率', 'close': f'{subject} 的收盘价'}.get(kind, f'{subject} 的行情')


def stale_observation_message(name: str, stored: datetime) -> str:
    """Why a change was held back; names the input and the stored time only, so it stays the same while it lasts."""
    if name.startswith('close:'):
        return (f'{_input_label(name)}属于更早的交易日（上次更新这条规则时用的是 {stored:%Y-%m-%d} 的收盘价），'
                '可能换了数据源；追上之前不据此更新这条规则')
    return (f'{_input_label(name)}时间早于上次更新这条规则时所用的（{stored.astimezone(SHANGHAI):%H:%M:%S}），'
            '可能换了数据源；追上之前不据此更新这条规则（最长到这份数据因过旧不再使用为止）')


def _stored_observation(rule: Rule, old: RuleState) -> Optional[Observation]:
    if old.observation is not None:
        return old.observation
    if old.observed_at is not None and rule.kind != POSITION_WEIGHT_MAX:
        # The previous release stored the rule's own quote time only.
        return Observation.of({quote_input(rule.ticker): old.observed_at})
    return None


def evaluate(rule: Rule, state: Optional[RuleState], quote: Optional[LiveQuote], context: Context) -> Decision:
    """Decide what ``rule`` does with ``quote``. See the module docstring."""
    old = state or RuleState()
    decision = _decide(rule, old, quote, context)
    if not decision.changed:
        return decision
    seen = context.observation
    if seen is None and context.observed_at is not None:
        seen = Observation.of({quote_input(rule.ticker): context.observed_at})
    if seen is None:
        return decision
    stored = _stored_observation(rule, old)
    behind = seen.behind(stored, context.now) if stored is not None else None
    if behind is not None:
        name, _mine, theirs = behind
        return Decision(SKIP, STALE_OBSERVATION, stale_observation_message(name, theirs), old, changed=False,
                        value=decision.value, trading_date=decision.trading_date)
    return replace(decision, state=replace(decision.state, observation=seen, observed_at=seen.at))


def _decide(rule: Rule, old: RuleState, quote: Optional[LiveQuote], context: Context) -> Decision:
    if not rule.enabled:
        return _decision(SKIP, 'disabled', '规则已停用', old, old)
    problem = rule_problem(rule) or quote_problem(rule, quote, context)
    if problem is None:
        value, problem = observed_value(rule, quote, context)
    if problem is not None:
        return _decision(SKIP, problem[0], problem[1], old, old)

    day = trading_date_of(rule, quote, context)
    if day is None:
        return _decision(SKIP, 'quote_time_unknown', '无法确定行情所属交易日', old, old)
    if old.last_trading_date is not None and day < old.last_trading_date:
        # Time went backwards (clock change or a source's old data); counting
        # fires for an earlier day again could repeat a notification.
        return _decision(SKIP, 'date_regressed', '行情日期早于上次检查的交易日，本次不检查', old, old)
    current = old
    if old.last_trading_date != day:
        # A new trading day: the daily count starts again and the rule re-arms.
        current = replace(old, armed=True, last_trading_date=day, fire_count_today=0)

    fields = {'value': value, 'trading_date': day}
    if condition_met(rule, value):
        if not current.armed:
            return _decision(WAIT, 'already_fired', '已提醒过，等待价格回到阈值以内后重新启用', old, current, **fields)
        if current.fire_count_today >= MAX_FIRES_PER_DAY:
            return _decision(WAIT, 'daily_cap', f'今日已提醒 {MAX_FIRES_PER_DAY} 次，达到每日上限', old, current, **fields)
        # A last fire more than FUTURE_TOLERANCE after now was recorded before the
        # clock went back: it does not hold the cooldown for the size of the step.
        if (rule.cooldown_seconds and current.last_fired_at is not None
                and -FUTURE_TOLERANCE <= context.now - current.last_fired_at < timedelta(seconds=rule.cooldown_seconds)):
            return _decision(WAIT, 'cooldown', '距上次提醒未满冷却时间', old, current, **fields)
        number = current.fire_count_today + 1
        fired = replace(current, armed=False, last_fired_at=context.now, fire_count_today=number)
        return _decision(FIRE, 'crossed', '条件成立，发出提醒', old, fired, fire_number=number, **fields)

    if not current.armed:
        if recovered(rule, value):
            return _decision(REARM, 'recovered', '已回到阈值以内，重新启用', old, replace(current, armed=True), **fields)
        return _decision(NONE, 'in_band', '刚回到阈值附近，尚未重新启用', old, current, **fields)
    return _decision(NONE, 'not_met', '条件未成立', old, current, **fields)


# -- position weights ---------------------------------------------------------

def weight_check(rule: Rule, valuation) -> Tuple[Optional[Decimal], Optional[str], Optional[str]]:
    """``(weight, None, None)``, or ``(None, code, reason)`` when the weight may not be used.

    The weight comes from a ``core.market.valuation.Valuation``. With an
    account, the weight of that account's position; without one, the ticker's
    weight summed over every account. Holdings are matched by listing
    (``listing``: ``HKG:700`` = ``HKG:00700``, ``NYSE:BRK-B`` = ``NYSE:BRK.B``),
    whatever spelling the rule or the ledger uses. Weights are shares of the whole
    portfolio in CNY, cash and funds included, exactly as the Today page shows
    them. A ticker that is not held weighs 0 when the total is known.

    A weight is used only when every input of it is verified: the code is
    ``weight_unavailable`` when a figure is missing, ``weight_estimated`` when
    the position's own row is estimated or the totals' quality is not
    ``complete`` (a stale, untraded or unverified price or currency rate
    anywhere in the portfolio changes the denominator).
    """
    if valuation is None:
        return None, WEIGHT_UNAVAILABLE, '账本没有已确认的持仓'
    if valuation.total_cny is None or valuation.total_cny <= 0:
        reasons = [entry.get('reason') for entry in valuation.unavailable if entry.get('field') != 'day_pnl']
        return None, WEIGHT_UNAVAILABLE, '组合总值不可用' + (f'（{reasons[0]}）' if reasons else '')
    accounts = [account for account in valuation.accounts if rule.account in (None, account.name)]
    if rule.account is not None and not accounts:
        return None, WEIGHT_UNAVAILABLE, '账本中没有这个账户'
    total = Decimal(0)
    wanted = listing(rule.ticker)   # HKG:700 and HKG:00700, NYSE:BRK-B and NYSE:BRK.B are one listing
    for account in accounts:
        for position in account.positions:
            if listing(position.ticker) != wanted:
                continue
            if position.weight_pct is None:
                reason = position.quote.reason if position.quote is not None and position.quote.reason else '该持仓的市值不可用'
                return None, WEIGHT_UNAVAILABLE, reason
            if position.estimated:
                return None, WEIGHT_ESTIMATED, f'{position.ticker}：{position.estimated_reason or "估值未经核实"}'
            total += position.weight_pct
    if valuation.quality != 'complete':
        entry = valuation.estimated[0] if valuation.estimated else {}
        subject = entry.get('ticker') or (f'汇率 {entry["currency"]}' if entry.get('currency') else '部分持仓')
        more = f'等 {len(valuation.estimated)} 项' if len(valuation.estimated) > 1 else ''
        return None, WEIGHT_ESTIMATED, f'{subject}{more}：{entry.get("reason") or "未经核实"}'
    return total, None, None


def weight_for(rule: Rule, valuation) -> Tuple[Optional[Decimal], Optional[str]]:
    """``(weight, None)`` or ``(None, reason)``; see ``weight_check``."""
    weight, _code, reason = weight_check(rule, valuation)
    return weight, reason


# -- distance to the threshold, for display ------------------------------------

def trigger_price(rule: Rule, quote: Optional[LiveQuote]) -> Optional[Decimal]:
    """The price at which the rule fires; ``None`` for weight rules."""
    if rule.kind in PRICE_KINDS:
        return rule.threshold
    if rule.kind == DAY_CHANGE_PCT and quote is not None and quote.prev_close is not None:
        return quote.prev_close * (1 + rule.threshold / HUNDRED)
    return None


def distance(rule: Rule, value: Optional[Decimal], quote: Optional[LiveQuote]) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """``(distance, distance_pct)``; positive while the condition does not hold.

    ``distance`` is in the rule's own unit: price for price rules, percentage
    points for percentage rules. ``distance_pct`` is how far the price has to
    move to reach the trigger, in percent of the current price (``None`` for
    weight rules, whose trigger depends on every other holding too).
    """
    if value is None:
        return None, None
    downward = _downward(rule)
    gap = value - rule.threshold if downward else rule.threshold - value
    pct = None
    price = quote.price if quote is not None else None
    trigger = trigger_price(rule, quote)
    if price is not None and price > 0 and trigger is not None:
        move = price - trigger if downward else trigger - price
        pct = move / price * HUNDRED
    return gap, pct


def _downward(rule: Rule) -> bool:
    """True for rules that fire when the value falls to the threshold."""
    return rule.kind == STOP_LOSS or (rule.kind == DAY_CHANGE_PCT and rule.threshold < 0)


def describe_condition(kind: str, threshold: Decimal) -> str:
    """"价格跌到 9.5 或以下" and the like: the rule in words. Never raises."""
    text = plain(threshold) if isinstance(threshold, Decimal) else str(threshold)[:40]
    if kind == STOP_LOSS:
        return f'价格跌到 {text} 或以下'
    if kind == TARGET_PRICE:
        return f'价格涨到 {text} 或以上'
    if kind == DAY_CHANGE_PCT and isinstance(threshold, Decimal):
        magnitude = plain(threshold.copy_abs())
        return f'当日跌幅达到 {magnitude}%' if threshold < 0 else f'当日涨幅达到 {magnitude}%'
    if kind == POSITION_WEIGHT_MAX:
        return f'仓位占组合达到 {text}% 或以上'
    return text


def as_mapping(state: RuleState) -> Mapping:
    return {'armed': state.armed,
            'last_fired_at': state.last_fired_at.isoformat(timespec='seconds') if state.last_fired_at else None,
            'last_trading_date': state.last_trading_date.isoformat() if state.last_trading_date else None,
            'fire_count_today': state.fire_count_today}
