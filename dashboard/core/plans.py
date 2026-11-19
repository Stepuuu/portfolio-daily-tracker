"""Trade plans: the position size, stop and target written down before buying.

A plan never places, prepares or blocks an order, and never blocks a ledger
entry. The ledger records what happened; where a booked trade departs from
its plan, ``check_events`` reports the departure as a warning.

Storage
=======
Plans and their links to ledger events live in ``desk.sqlite3`` (schema
``plans``), not in the research journal of ``ledger.sqlite3``:

* activating a plan creates its alert rules and closing it disables them.
  The rules are in ``desk.sqlite3``, so the status change and the rules
  commit in one transaction, or neither does;
* a plan changes (edits, status) while the journal is an append-only list
  that ``GET /api/ledger/journal`` returns whole; the ledger file keeps only
  what the ledger itself writes, with its revision-checked confirmations;
* readers never create the desk store (``desk_store.ready``), so a GET in
  the read-only demo writes nothing.

The journal and its routes are unchanged.

A plan (``get``, ``list``, ``create`` ...)::

    id                    int, never reused
    ticker                EXCH:CODE as the ledger writes it (SHA, SHE, HKG, NASDAQ, NYSE)
    account               a ledger account name, or null: the plan covers the
                          ticker in every account
    direction             'buy' (long plans only)
    thesis, invalidation  why to buy; what would prove the idea wrong
    entry_low, entry_high planned buy price range, in ``currency`` (the trading
                          currency: USD or HKD for a B share, which the
                          ledger books in CNY); so are stop and target
    stop_price            for a long plan below the entry, and below the target
    target_price          above the entry
    planned_weight_pct    planned position as a % of the whole portfolio in CNY
                          (cash and funds included, as on the Today page), 0 < w <= 100
    planned_quantity      or the planned position in shares; at most one of the two
    max_loss_cny          derived, never entered: see ``max_loss``; null when unknown
    max_loss              {amount, currency, amount_cny, fx_rate, fx_status,
                           entry_basis, formula, reason}
    review_on             YYYY-MM-DD or null
    status, status_label  draft | active | closed | cancelled (草稿 启用中 已关闭 已取消)
    close_reason          why it was closed (required) or cancelled (optional)
    created_at, updated_at, activated_at, ended_at   ISO-8601 UTC, or null
    source                'manual', 'agent:chat' (a confirmed proposal) ...
    currency              the ticker's trading currency
    conflicts             ids of the other active plans for the same listing
                          in an overlapping scope ([] unless an older store
                          holds such duplicates; never for an inactive plan)
    version               changes with every write to the plan; ``update``
                          with ``expected_version`` refuses an edit made
                          against another version (409), so a stale form never
                          reverts a newer change

Numbers are decimal strings (``"10.5"``), null when not set. ``validate``
accepts numbers or strings and writes normalised strings. A string is a
plain decimal in ASCII digits (at most 32 characters, no exponent, no
full-width or other digits); a ``Decimal`` as it is (``/api/plans`` reads
every JSON number from its text that way, so nothing is rounded by a float
first); a float, from other callers, by its shortest form.
Each must be above 0 with at most 8 decimal places; prices at most
100000000 (what the quote service and alert thresholds accept), quantities
at most 1000000000000, weights at most 100. Checked on the exact value, so
nothing is rounded, underflowed or expanded before it is stored.

Status: ``create`` makes a draft. draft -> active (``activate``) -> closed
(``close``); draft or active -> cancelled (``cancel``). closed and cancelled
are final. At most one active plan covers a ticker in an account: two plans
for the same ticker and account, or one for every account (account null)
and one for a single account, are never active together.

Instrument identity: a plan keeps the ticker as it was entered, like the
ledger. Wherever a plan is matched to a ticker (one active plan per listing,
the plan of a ledger trade and the shares in its scope, its alert rules, the
list filter, the Today rows) the comparison is by ``listing_key``
(``market.search.identity_key``: ``instruments.quote_key`` plus the US
class-share spelling), so ``HKG:700``, ``HKG:0700`` and ``HKG:00700`` are one
listing, and so are ``NYSE:BRK.B`` and ``NYSE:BRK-B``. Plans stored before
this rule keep their spelling and are compared the same way when read;
nothing is migrated. Two active plans such a store may already hold for one
listing in overlapping scopes both stay active and unchanged: each names the
other in ``conflicts``, and a trade both cover is checked against the latest
with a ``plan_conflict`` warning (see ``check_events``).

Alerts
======
``activate`` creates, in the transaction that sets the status, alert rules
with ``source='plan:<id>'``: ``stop_loss`` at the stop, ``target_price`` at
the target and ``position_weight_max`` at the planned weight (only when a
planned weight is given), each with the plan's account. Editing an active
plan updates them in the edit's transaction (a cleared field disables its
rule). ``close`` and ``cancel`` disable them (``rules.disable_by_source``).
Alert rules are unique per ticker, kind, threshold and account: when an
identical rule from another source already exists (for example one the user
made by hand) no copy is created and ``alert_notes`` says which rule covers
it; a disabled rule left by an ended plan is deleted instead (its fired
events stay), so the next plan at the same price gets a working alert.
``activate`` on an active plan changes nothing but repairs its rules, so it
is safe to repeat.

Ledger links
============
``check_events(events, state, market, history=...)`` compares ledger events
with the plans; see its docstring for the deviation codes.
``backend/api/ledger.py`` adds the result to a proposal as ``plan_check``;
right before the ledger confirms, ``prepare_links`` stores the check the
links are to record (schema ``plan_link_drafts``), and after it
``record_links`` stores it in ``plan_links`` once per event (the event id is
the primary key, so confirming twice writes once). A repeated confirmation
that repairs an interrupted link write so records the plans as they were
when the trade was confirmed, not as edited since.
``deviations_for_date`` and ``links_for_plan`` read them back.

Confirmation never waits for quotes. A preview's check reads current quotes
and this process keeps them for the proposal in its ledger file
(``PREVIEW_QUOTES_KEPT`` proposals, ``PREVIEW_QUOTES_MAX_AGE`` seconds);
``record_links`` values weights with those quotes only, so a link keeps the
weight the preview showed. Without them (another process, a restart, an old
preview, the same proposal in a copy of the portfolio) the weight is
``weight_unknown`` with the reason, never fetched.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import date as Date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core import desk_store, fees, proposals
from core.alerts import rules
from core.alerts.engine import KIND_LABELS, POSITION_WEIGHT_MAX, STOP_LOSS, TARGET_PRICE
from core.ledger import MARKETS, Ledger, LedgerError, instrument, portfolio_directory, read_confirmed, replay, valid_date
from core.market.calendar import SHANGHAI, ZONES
from core.market.instruments import CN as CN_MARKET, EXCHANGES, InstrumentError, parse as parse_instrument
from core.market.quotes import MAX_PRICE, UNAVAILABLE as UNAVAILABLE_RATE
from core.market.search import identity_key

logger = logging.getLogger(__name__)

SCHEMA = 'plans'
MIGRATIONS = [[
    """CREATE TABLE trade_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        account TEXT,
        direction TEXT NOT NULL,
        thesis TEXT NOT NULL,
        invalidation TEXT NOT NULL,
        entry_low TEXT,
        entry_high TEXT,
        stop_price TEXT,
        target_price TEXT,
        planned_weight_pct TEXT,
        planned_quantity TEXT,
        review_on TEXT,
        status TEXT NOT NULL,
        close_reason TEXT,
        source TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        activated_at TEXT,
        ended_at TEXT)""",
    "CREATE INDEX trade_plans_by_ticker ON trade_plans(ticker, status)",
    # A backstop for the rule checked in code: one active plan per ticker and account.
    "CREATE UNIQUE INDEX trade_plans_one_active ON trade_plans(ticker, IFNULL(account, '')) WHERE status = 'active'",
    # One row per confirmed buy or sell, written once after the ledger confirmed it.
    """CREATE TABLE plan_links (
        event_id TEXT PRIMARY KEY,
        plan_id INTEGER,
        relation TEXT NOT NULL,
        deviations TEXT NOT NULL,
        created_at TEXT NOT NULL,
        proposal_id TEXT,
        event_date TEXT NOT NULL,
        kind TEXT NOT NULL,
        ticker TEXT NOT NULL,
        account TEXT,
        quantity TEXT,
        price TEXT)""",
    "CREATE INDEX plan_links_by_plan ON plan_links(plan_id)",
    "CREATE INDEX plan_links_by_date ON plan_links(event_date)",
]]
desk_store.register_schema(SCHEMA, MIGRATIONS)

DRAFT, ACTIVE, CLOSED, CANCELLED = 'draft', 'active', 'closed', 'cancelled'
STATUSES = (DRAFT, ACTIVE, CLOSED, CANCELLED)
ENDED = (CLOSED, CANCELLED)
STATUS_LABELS = {DRAFT: '草稿', ACTIVE: '启用中', CLOSED: '已关闭', CANCELLED: '已取消'}
BUY = 'buy'

FIELDS = ('ticker', 'account', 'direction', 'thesis', 'invalidation', 'entry_low', 'entry_high', 'stop_price',
          'target_price', 'planned_weight_pct', 'planned_quantity', 'review_on')
FIXED_WHEN_ACTIVE = ('ticker', 'account', 'direction')
NUMBER_LABELS = {'entry_low': '入场价下限', 'entry_high': '入场价上限', 'stop_price': '止损价',
                 'target_price': '目标价', 'planned_weight_pct': '计划仓位比例', 'planned_quantity': '计划股数'}
# alert kind -> the plan field that is its threshold
ALERT_FIELDS = ((STOP_LOSS, 'stop_price'), (TARGET_PRICE, 'target_price'), (POSITION_WEIGHT_MAX, 'planned_weight_pct'))

MAX_TEXT = 4000
MAX_REASON = 500
MAX_ACCOUNT = 240
MAX_DECIMALS = 8
MAX_NUMBER_TEXT = 32
MAX_QUANTITY = fees.MAX_QUANTITY            # 1e12 shares, as the fee estimate and the importer
# field -> largest value. Prices become alert thresholds and are compared with
# quotes, so they stop where the quote service does.
NUMBER_LIMITS = {'entry_low': MAX_PRICE, 'entry_high': MAX_PRICE, 'stop_price': MAX_PRICE,
                 'target_price': MAX_PRICE, 'planned_weight_pct': Decimal(100), 'planned_quantity': MAX_QUANTITY}
# Plain decimal notation in ASCII digits. ``Decimal`` also takes exponents,
# Unicode digits, underscores, NaN and Infinity; none of them is a plan number.
_PLAIN_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)')
_EXPONENT_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)[eE][+-]?[0-9]+')
MAX_ID = 2 ** 53
HUNDRED = Decimal(100)
CENT = Decimal('0.01')
_SOURCE = re.compile(r'[a-z][a-z0-9_.:\-]{0,63}')
TICKER_HINT = ('代码格式不正确。请使用“交易所:代码”，例如 SHA:600000、SHE:000001、HKG:00700、NASDAQ:AAPL'
               '（交易计划只用于股票，不用于指数）。')
NOT_AN_ORDER = '交易计划只用于记录和提醒，不会下单。'

# Ledger link relations and deviation codes (see ``check_events``).
ENTRY, ADD, REDUCE, EXIT, NONE = 'entry', 'add', 'reduce', 'exit', 'none'
WARNING, INFO = 'warning', 'info'
NO_PLAN = 'no_plan'
AFTER_INVALIDATION = 'after_invalidation'
OUTSIDE_ENTRY_RANGE = 'outside_entry_range'
BELOW_STOP_BUY = 'below_stop_buy'
OVER_PLANNED_QUANTITY = 'over_planned_quantity'
OVER_PLANNED_WEIGHT = 'over_planned_weight'
WEIGHT_UNKNOWN = 'weight_unknown'
SOLD_BELOW_STOP = 'sold_below_stop'
SOLD_BEFORE_TARGET = 'sold_before_target'
PLAN_CONFLICT = 'plan_conflict'
PRICE_UNKNOWN = 'price_unknown'
DEVIATION_CODES = (NO_PLAN, AFTER_INVALIDATION, OUTSIDE_ENTRY_RANGE, BELOW_STOP_BUY, PRICE_UNKNOWN,
                   OVER_PLANNED_QUANTITY, OVER_PLANNED_WEIGHT, WEIGHT_UNKNOWN, SOLD_BELOW_STOP, SOLD_BEFORE_TARGET,
                   PLAN_CONFLICT)
TRADE_KINDS = ('buy', 'sell')


class PlanError(ValueError):
    """The request cannot be carried out; the message is shown to the user (Chinese)."""


class PlanNotFound(PlanError):
    pass


class PlanConflict(PlanError):
    """The plan's status does not allow the request, or another active plan covers the ticker."""


def _now() -> str:
    """The time stamp for ``*_at`` columns. Tests pin it."""
    return desk_store.now()


def _beijing(stamp: Optional[str]) -> str:
    """A stored ``*_at`` stamp (UTC) as people read it: "2026-11-19 03:42（北京时间）"; unreadable ones as they are."""
    try:
        moment = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
    except ValueError:
        moment = None
    if moment is None:
        return str(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f'{moment.astimezone(SHANGHAI):%Y-%m-%d %H:%M}（北京时间）'


def ensure() -> None:
    """For writers: create or upgrade the plan tables."""
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)


def available() -> bool:
    """For readers: whether the plan tables exist. Never creates or migrates the store."""
    return desk_store.ready(SCHEMA, MIGRATIONS)


def in_use() -> bool:
    """Whether at least one plan was ever saved. Never creates the store."""
    if not available():
        return False
    with desk_store.connect() as db:
        return db.execute("SELECT 1 FROM trade_plans LIMIT 1").fetchone() is not None


def alert_source(plan_id: int) -> str:
    return f'plan:{plan_id}'


def listing_key(ticker: str) -> str:
    """What makes two tickers one listing: ``identity_key`` (Hong Kong zero padding, ``BRK.B`` = ``BRK-B``)."""
    return identity_key(ticker) or ticker


def same_listing(first: str, second: str) -> bool:
    """Whether two tickers name one listing (``HKG:700`` and ``HKG:00700``, ``NYSE:BRK.B`` and ``NYSE:BRK-B``)."""
    return listing_key(first) == listing_key(second)


def _overlap(first: Optional[str], second: Optional[str]) -> bool:
    """Whether two plan scopes share an account (``None`` is every account)."""
    return first is None or second is None or first == second


# -- validation ---------------------------------------------------------------

def _fmt(number: Optional[Decimal]) -> Optional[str]:
    """A decimal string without exponent or trailing zeros: Decimal('10.50') -> '10.5'."""
    if number is None:
        return None
    text = format(number.normalize(), 'f')
    return '0' if text in ('-0', '') else text


def _money(number: Optional[Decimal]) -> Optional[str]:
    """Two decimals, half away from zero, as fees, valuation and the frontend's formatting round."""
    if number is None:
        return None
    rounded = number.quantize(CENT, rounding=ROUND_HALF_UP)
    return format(rounded if rounded != 0 else Decimal('0.00'), 'f')


def _decimal(value: Optional[str]) -> Optional[Decimal]:
    return Decimal(value) if value is not None else None


def _number(field: str, value) -> Optional[Decimal]:
    """The exact value of a plan number, or ``PlanError``; ``None`` when not set.

    Syntax, sign and size are checked before any arithmetic, and
    ``fees.bounded_decimal`` (the shared exact reader of user numbers)
    normalises without a rounding context, so what is stored is what was
    entered.
    """
    label = NUMBER_LABELS[field]
    not_a_number = f'{label}必须是数字，例如 "10.5"。'
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise PlanError(not_a_number)
    limit = NUMBER_LIMITS[field]
    out_of_range = ('计划仓位比例是占整个组合的百分比，必须大于 0 且不超过 100。' if field == 'planned_weight_pct'
                    else f'{label}超出支持的范围：不能超过 {format(limit, "f")}。')
    if isinstance(value, int):
        if value > limit:
            raise PlanError(out_of_range)   # before Decimal(): a huge int is refused, not converted
        number = Decimal(value)
    elif isinstance(value, float):
        # A JSON number: its shortest form is what was written ("1e-05" for 0.00001).
        number = Decimal(repr(value))       # 'nan' and 'inf' too: refused as not finite below
    elif isinstance(value, Decimal):
        number = value
    else:
        text = value.strip()
        if len(text) > MAX_NUMBER_TEXT:
            raise PlanError(f'{label}过长：最多 {MAX_NUMBER_TEXT} 个字符，例如 "10.5"。')
        if _EXPONENT_NUMBER.fullmatch(text):
            raise PlanError(f'{label}请直接写出数字，不支持科学计数法（例如 1e3），例如 "1000"。')
        if not _PLAIN_NUMBER.fullmatch(text):
            raise PlanError(not_a_number)
        number = Decimal(text)              # exact: plain ASCII digits, at most 32 characters
    if not number.is_finite():
        raise PlanError(not_a_number)
    if number <= 0:
        raise PlanError(f'{label}必须大于 0。')
    if number > limit:
        raise PlanError(out_of_range)
    try:
        return fees.bounded_decimal(number, label, limit, MAX_DECIMALS, allow_zero=False)
    except fees.FeeInputError as exc:
        raise PlanError(str(exc)) from None


def _words(value, required_message: str, limit: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(required_message)
    value = value.strip()
    if len(value) > limit:
        raise PlanError(f'{label}最多 {limit} 个字符。')
    return value


def _ticker(value) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise PlanError('请填写代码，例如 SHA:600000。')
    try:
        return instrument(value)
    except LedgerError:
        raise PlanError(TICKER_HINT) from None


def _account(value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PlanError('账户名称必须是文字。')
    value = value.strip()
    if len(value) > MAX_ACCOUNT:
        raise PlanError(f'账户名称最多 {MAX_ACCOUNT} 个字符。')
    return value or None


def _review_on(value) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return valid_date(value.strip() if isinstance(value, str) else value)
    except LedgerError:
        raise PlanError('复查日期必须是有效日期，格式为 YYYY-MM-DD。') from None


def _source(value) -> str:
    if not isinstance(value, str) or not _SOURCE.fullmatch(value):
        raise ValueError("plan source must be a short lowercase name such as 'manual' or 'agent:chat'")
    return value


def _unknown(payload: Mapping, allowed: Iterable[str]) -> None:
    extra = sorted(str(key) for key in payload if key not in allowed)
    if 'status' in extra:
        raise PlanError('计划状态不能直接修改，请使用“启用”“关闭”或“取消”。')
    if 'max_loss_cny' in extra or 'max_loss' in extra:
        raise PlanError('最大亏损由入场价、止损价和计划股数计算得出，不能直接填写。')
    if extra:
        raise PlanError('不支持的字段：' + '、'.join(extra) + '。')


def validate(payload: Any, *, raised_stop: bool = False) -> dict:
    """Check and normalise a plan's fields; returns every field of ``FIELDS``. Writes nothing.

    Accepts its own output unchanged, so it runs again when a proposal is confirmed.
    ``raised_stop`` lets the stop reach or pass the entry: an active plan that
    keeps its entry may move its stop up to breakeven or into profit (still
    below the target). A plan being planned, or given a new entry, keeps its
    stop below the entry.
    """
    if not isinstance(payload, Mapping):
        raise PlanError('交易计划必须是一个 JSON 对象。')
    _unknown(payload, FIELDS)
    ticker = _ticker(payload.get('ticker'))
    direction = payload.get('direction')
    if direction in (None, ''):
        direction = BUY
    if direction != BUY:
        raise PlanError('目前只支持做多（买入）计划，direction 只能是 buy。')
    numbers = {field: _number(field, payload.get(field)) for field in NUMBER_LABELS}
    low, high = numbers['entry_low'], numbers['entry_high']
    stop, target = numbers['stop_price'], numbers['target_price']
    if low is not None and high is not None and low > high:
        raise PlanError(f'入场价下限 {_fmt(low)} 不能高于上限 {_fmt(high)}。')
    lowest_entry = low if low is not None else high
    highest_entry = high if high is not None else low
    if not raised_stop and stop is not None and lowest_entry is not None and stop >= lowest_entry:
        raise PlanError(f'做多计划的止损价必须低于入场价：止损价 {_fmt(stop)}，入场价 {_fmt(lowest_entry)}。')
    if target is not None and highest_entry is not None and target <= highest_entry:
        raise PlanError(f'做多计划的目标价必须高于入场价：目标价 {_fmt(target)}，入场价 {_fmt(highest_entry)}。')
    if stop is not None and target is not None and stop >= target:
        raise PlanError(f'止损价 {_fmt(stop)} 必须低于目标价 {_fmt(target)}。')
    if numbers['planned_weight_pct'] is not None and numbers['planned_quantity'] is not None:
        raise PlanError('计划仓位请只填一项：占组合的比例（planned_weight_pct）或股数（planned_quantity）。')
    return {
        'ticker': ticker, 'account': _account(payload.get('account')), 'direction': direction,
        'thesis': _words(payload.get('thesis'), '请写下买入理由（thesis）。', MAX_TEXT, '买入理由'),
        'invalidation': _words(payload.get('invalidation'), '请写下什么情况说明判断错了（invalidation）。', MAX_TEXT,
                               '判断失效条件'),
        **{field: _fmt(number) for field, number in numbers.items()},
        'review_on': _review_on(payload.get('review_on')),
    }


# -- derived: maximum loss ----------------------------------------------------

def currency_of(ticker: str) -> str:
    """The ticker's trading currency: a B share's is USD or HKD, though the ledger books it in CNY."""
    try:
        return parse_instrument(ticker).currency
    except InstrumentError:
        return MARKETS[ticker.split(':')[0]]


def max_loss(plan: Mapping, fx_rate: Optional[Decimal] = None, fx_status: Optional[str] = None, *,
             fx_source: Optional[str] = None, fx_quote_time: Optional[str] = None,
             estimated_reason: Optional[str] = None) -> dict:
    """The loss if the whole planned quantity is bought at the top of the entry range and sold at the stop.

    ``(entry - stop) x planned quantity``, in the ticker's currency, and in CNY
    at ``fx_rate`` (CNY per unit) for HKD and USD. The entry is ``entry_high``,
    or ``entry_low`` when only that is given. Fees, slippage and gaps below the
    stop are not included. Needs the stop, an entry and ``planned_quantity``;
    otherwise every amount is null and ``reason`` says what is missing. A stop
    at or above the entry (an active plan's stop moved up) loses nothing: the
    amounts are 0, never negative, and ``locked_gain`` is (stop - entry) x
    quantity in the ticker's currency.

    A CNY amount converted at a rate carries the rate's ``fx_status``,
    ``fx_source`` and ``fx_quote_time``; when the rate is not verified
    (``valuation.rate_estimate``: stale, or of unknown time) the amount is
    ``estimated`` with ``estimated_reason``, as the Today page marks
    estimated figures, and the formula says so.
    """
    currency = currency_of(plan['ticker'])
    result = {'amount': None, 'currency': currency, 'amount_cny': None, 'fx_rate': None, 'fx_status': None,
              'fx_source': None, 'fx_quote_time': None, 'estimated': False, 'estimated_reason': None,
              'entry_basis': None, 'locked_gain': None, 'formula': None, 'reason': None}
    basis = 'entry_high' if plan.get('entry_high') is not None else 'entry_low' if plan.get('entry_low') else None
    missing = [label for label, value in (('入场价', basis), ('止损价', plan.get('stop_price')),
                                           ('计划股数', plan.get('planned_quantity'))) if value is None]
    if missing:
        result['reason'] = '需要' + '、'.join(missing) + '才能计算最大亏损' + (
            '（计划按仓位比例填写时不计算）' if plan.get('planned_quantity') is None
            and plan.get('planned_weight_pct') is not None else '')
        return result
    entry, stop, quantity = Decimal(plan[basis]), Decimal(plan['stop_price']), Decimal(plan['planned_quantity'])
    label = '入场价上限' if basis == 'entry_high' else '入场价下限'
    if stop >= entry:
        # An active plan's stop moved to breakeven or into profit: nothing is lost at the stop, never a negative
        # loss; the gain it locks in is said (bought at the top of the entry, the smallest such gain).
        with localcontext() as context:
            context.prec = 60
            gain = (stop - entry) * quantity
        result.update(amount=_money(Decimal(0)), amount_cny=_money(Decimal(0)), entry_basis=basis,
                      locked_gain=_money(gain),
                      formula=f'止损价 {_fmt(stop)} 不低于{label} {_fmt(entry)}：触及止损时不亏损，按计划股数 '
                              f'{_fmt(quantity)} 锁定盈利 ({_fmt(stop)} − {_fmt(entry)}) × {_fmt(quantity)} '
                              f'= {_money(gain)} {currency}。不含手续费、滑点和跳空低开。')
        return result
    with localcontext() as context:
        context.prec = 60
        native = (entry - stop) * quantity
        formula = (f'({label} {_fmt(entry)} − 止损价 {_fmt(stop)}) × 计划股数 {_fmt(quantity)} '
                   f'= {_money(native)} {currency}')
        result.update(amount=_money(native), entry_basis=basis)
        if currency == 'CNY':
            result['amount_cny'] = _money(native)
        elif fx_rate is not None:
            result.update(amount_cny=_money(native * fx_rate), fx_rate=_fmt(fx_rate), fx_status=fx_status,
                          fx_source=fx_source, fx_quote_time=fx_quote_time,
                          estimated=estimated_reason is not None, estimated_reason=estimated_reason)
            formula += f'；× 汇率 {_fmt(fx_rate)} = {_money(native * fx_rate)} CNY'
            if estimated_reason is not None:
                formula += f'（估算：{estimated_reason}）'
        else:
            result['reason'] = f'缺少 {currency} 兑人民币汇率，人民币金额不可用'
    result['formula'] = formula + '。不含手续费、滑点和跳空低开。'
    return result


def with_fx(items: Sequence[dict], runtime=None) -> List[dict]:
    """``items`` with ``max_loss`` converted to CNY at the runtime's current rates where needed.

    Asks for rates only when an HKD or USD plan has a computable loss; a rate
    that cannot be had leaves ``amount_cny`` null with a reason. The converted
    amount carries the rate's source, time and status, and is ``estimated``
    when the rate is not verified. Never raises.
    """
    needs = {plan['id'] for plan in items if plan['max_loss']['amount'] is not None
             and plan['max_loss']['amount_cny'] is None}
    if not needs:
        return [*items]
    try:
        if runtime is None:
            from core.market.runtime import get_market_runtime
            runtime = get_market_runtime()
        rates = runtime.quotes.fx()
    except Exception as exc:
        logger.warning('trade plan max loss could not read exchange rates: %s', type(exc).__name__)
        return [*items]
    from core.market.today import moment
    from core.market.valuation import rate_estimate
    result = []
    for plan in items:
        rate = rates.get(plan['currency'])
        usable = rate is not None and getattr(rate, 'rate', None) is not None and rate.rate > 0 \
            and getattr(rate, 'status', None) != UNAVAILABLE_RATE
        if plan['id'] in needs and usable:
            estimate = rate_estimate(rate)
            loss = max_loss(plan, rate.rate, rate.status, fx_source=rate.source,
                            fx_quote_time=moment(rate.quote_time),
                            estimated_reason=estimate[1] if estimate else None)
            plan = {**plan, 'max_loss': loss, 'max_loss_cny': loss['amount_cny']}
        result.append(plan)
    return result


# -- rows ---------------------------------------------------------------------

def _plan(row) -> dict:
    plan = {
        'id': row['id'], 'ticker': row['ticker'], 'account': row['account'], 'direction': row['direction'],
        'thesis': row['thesis'], 'invalidation': row['invalidation'],
        'entry_low': row['entry_low'], 'entry_high': row['entry_high'], 'stop_price': row['stop_price'],
        'target_price': row['target_price'], 'planned_weight_pct': row['planned_weight_pct'],
        'planned_quantity': row['planned_quantity'], 'review_on': row['review_on'],
        'status': row['status'], 'status_label': STATUS_LABELS.get(row['status'], row['status']),
        'close_reason': row['close_reason'], 'source': row['source'],
        'created_at': row['created_at'], 'updated_at': row['updated_at'],
        'activated_at': row['activated_at'], 'ended_at': row['ended_at'],
        'currency': currency_of(row['ticker']),
    }
    loss = max_loss(plan)
    plan['max_loss'] = loss
    plan['max_loss_cny'] = loss['amount_cny']
    plan['conflicts'] = []
    plan['version'] = _version(row)
    return plan


# Every stored column a write can change: the version changes with each of them.
VERSIONED = (*FIELDS, 'status', 'close_reason', 'activated_at', 'ended_at', 'updated_at')
_VERSION = re.compile(r'[0-9a-f]{16}')


def _version(row) -> str:
    return hashlib.sha256(json.dumps([row[key] for key in VERSIONED], ensure_ascii=False).encode()).hexdigest()[:16]


def _active_rows(db) -> list:
    return db.execute("SELECT id, ticker, account FROM trade_plans WHERE status='active' ORDER BY id").fetchall()


def _conflicts(plan: Mapping, active_rows) -> List[int]:
    """Ids of the other active plans for ``plan``'s listing in an overlapping scope; [] for an inactive plan."""
    if plan['status'] != ACTIVE:
        return []
    return [row['id'] for row in active_rows if row['id'] != plan['id'] and same_listing(row['ticker'], plan['ticker'])
            and _overlap(row['account'], plan['account'])]


def _row(db, plan_id) -> Optional[dict]:
    if isinstance(plan_id, bool) or not isinstance(plan_id, int) or not 0 < plan_id <= MAX_ID:
        return None
    row = db.execute("SELECT * FROM trade_plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        return None
    plan = _plan(row)
    plan['conflicts'] = _conflicts(plan, _active_rows(db))
    return plan


def _require(db, plan_id) -> dict:
    plan = _row(db, plan_id)
    if plan is None:
        raise PlanNotFound('交易计划不存在。请刷新列表。')
    return plan


def _scope(account: Optional[str]) -> str:
    return '全部账户' if account is None else f'账户“{account}”'


def _active_conflict(db, plan: Mapping) -> None:
    """Refuse when another active plan covers the same listing (any spelling) in an overlapping scope."""
    rows = db.execute("SELECT id, ticker, account FROM trade_plans WHERE status='active' AND id != ? ORDER BY id",
                      (plan['id'],)).fetchall()
    row = next((row for row in rows if same_listing(row['ticker'], plan['ticker'])
                and _overlap(row['account'], plan['account'])), None)
    if row is not None:
        raise PlanConflict(
            f'{plan["ticker"]} 已有启用中的交易计划 #{row["id"]}（{_scope(row["account"])}）。同一只股票在同一账户'
            f'只能有一个启用中的计划；请先关闭或取消计划 #{row["id"]}，再启用这个计划（{_scope(plan["account"])}）。')


# -- public functions ---------------------------------------------------------

def create(payload: Any, source: str = 'manual') -> dict:
    """Store a new plan as a draft and return it."""
    plan = validate(payload)
    source = _source(source)
    ensure()
    with desk_store.transaction() as db:
        stamp = _now()
        cursor = db.execute(
            "INSERT INTO trade_plans(ticker, account, direction, thesis, invalidation, entry_low, entry_high, "
            "stop_price, target_price, planned_weight_pct, planned_quantity, review_on, status, source, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (*(plan[field] for field in FIELDS), DRAFT, source, stamp, stamp))
        return _require(db, cursor.lastrowid)


def get(plan_id: int) -> dict:
    if not available():
        raise PlanNotFound('交易计划不存在。请刷新列表。')
    with desk_store.connect() as db:
        return _require(db, plan_id)


def list_plans(status: Optional[str] = None, ticker: Optional[str] = None) -> List[dict]:
    """Plans, newest first. ``status`` one of ``STATUSES`` (None, '' or 'all' for every one)."""
    where, parameters = [], []
    if status not in (None, '', 'all'):
        if status not in STATUSES:
            raise PlanError('未知的计划状态。可用：draft（草稿）、active（启用中）、closed（已关闭）、'
                            'cancelled（已取消）或 all。')
        where.append('status = ?')
        parameters.append(status)
    wanted = _ticker(ticker) if ticker not in (None, '') else None
    if not available():
        return []
    with desk_store.connect() as db:
        query = "SELECT * FROM trade_plans" + (' WHERE ' + ' AND '.join(where) if where else '') + " ORDER BY id DESC"
        rows = db.execute(query, parameters).fetchall()
        active_rows = _active_rows(db)
    found = [_plan(row) for row in rows if wanted is None or same_listing(row['ticker'], wanted)]
    for plan in found:
        plan['conflicts'] = _conflicts(plan, active_rows)
    return found


def update(plan_id: int, changes: Any, *, expected_version: Optional[str] = None) -> dict:
    """Change the fields of a draft, or the prices, sizes and texts of an active plan.

    An active plan keeps its ticker, account and direction (another spelling
    of the same listing is not a change, and the stored spelling stays); its
    alert rules follow the new stop, target and planned weight in the same
    transaction. Ended plans cannot change. With ``expected_version`` (the
    ``version`` the edit was made against) a plan changed since is refused
    with ``PlanConflict`` and nothing is saved.
    """
    if not isinstance(changes, Mapping):
        raise PlanError('修改内容必须是一个 JSON 对象。')
    _unknown(changes, FIELDS)
    if not changes:
        raise PlanError('没有要修改的内容。')
    if expected_version is not None and (not isinstance(expected_version, str)
                                         or not _VERSION.fullmatch(expected_version)):
        raise PlanError('if_version 必须是计划的 version（16 位十六进制）。')
    ensure()
    rules.ensure()
    with desk_store.transaction() as db:
        current = _require(db, plan_id)
        if expected_version is not None and current['version'] != expected_version:
            raise PlanConflict(f'计划已在别处修改（最近修改于 {_beijing(current["updated_at"])}），这次修改没有保存。'
                               '请重新打开计划，核对最新内容后再改。')
        if current['status'] in ENDED:
            raise PlanConflict(f'计划{STATUS_LABELS[current["status"]]}，不能再修改。需要时请新建计划。')
        # An active plan that keeps its entry may move its stop up to breakeven or into profit.
        running = current['status'] == ACTIVE
        merged = validate({**{field: current[field] for field in FIELDS}, **changes}, raised_stop=running)
        if running and (merged['entry_low'], merged['entry_high']) != (current['entry_low'], current['entry_high']):
            merged = validate(merged)                      # a new entry: the stop goes below it again
        if current['status'] == ACTIVE:
            if same_listing(merged['ticker'], current['ticker']):
                merged['ticker'] = current['ticker']
            if any(merged[field] != current[field] for field in FIXED_WHEN_ACTIVE):
                raise PlanConflict('启用中的计划不能修改代码、账户或方向；请取消这个计划后重新建立。')
        db.execute(
            "UPDATE trade_plans SET ticker=?, account=?, direction=?, thesis=?, invalidation=?, entry_low=?, "
            "entry_high=?, stop_price=?, target_price=?, planned_weight_pct=?, planned_quantity=?, review_on=?, "
            "updated_at=? WHERE id=?", (*(merged[field] for field in FIELDS), _now(), plan_id))
        plan = _require(db, plan_id)
        _sync_or_refuse(plan)
        return plan


def activate(plan_id: int) -> dict:
    """draft -> active, creating its alert rules in the same transaction.

    On an active plan nothing changes except that missing or outdated alert
    rules are repaired, so a repeated call is harmless.
    """
    ensure()
    rules.ensure()
    with desk_store.transaction() as db:
        plan = _require(db, plan_id)
        if plan['status'] in ENDED:
            raise PlanConflict(f'计划{STATUS_LABELS[plan["status"]]}，不能再启用。需要时请新建计划。')
        if plan['status'] == DRAFT:
            _active_conflict(db, plan)
            stamp = _now()
            db.execute("UPDATE trade_plans SET status='active', activated_at=?, updated_at=? WHERE id=?",
                       (stamp, stamp, plan_id))
            plan = _require(db, plan_id)
        _sync_or_refuse(plan)
        return plan


def close(plan_id: int, reason: Any) -> dict:
    """active -> closed with a reason; disables the plan's alert rules. Closing a closed plan changes nothing."""
    reason = _words(reason, '请写明关闭原因，例如“已到目标价卖出”或“跌破止损已离场”。', MAX_REASON, '关闭原因')
    return _end(plan_id, CLOSED, reason)


def cancel(plan_id: int, reason: Any = None) -> dict:
    """draft or active -> cancelled; disables the plan's alert rules. Cancelling twice changes nothing."""
    if reason is None or (isinstance(reason, str) and not reason.strip()):
        reason = None
    else:
        reason = _words(reason, '取消原因必须是文字。', MAX_REASON, '取消原因')
    return _end(plan_id, CANCELLED, reason)


def _end(plan_id: int, status: str, reason: Optional[str]) -> dict:
    ensure()
    rules.ensure()
    with desk_store.transaction() as db:
        plan = _require(db, plan_id)
        if plan['status'] == status:
            rules.disable_by_source(alert_source(plan_id))
            return plan
        if plan['status'] in ENDED:
            raise PlanConflict(f'计划{STATUS_LABELS[plan["status"]]}，不能再{"关闭" if status == CLOSED else "取消"}。')
        if status == CLOSED and plan['status'] == DRAFT:
            raise PlanConflict('草稿计划还没有启用，不能关闭；不再需要时请“取消”。')
        stamp = _now()
        db.execute("UPDATE trade_plans SET status=?, close_reason=?, ended_at=?, updated_at=? WHERE id=?",
                   (status, reason, stamp, stamp, plan_id))
        rules.disable_by_source(alert_source(plan_id))
        return _require(db, plan_id)


# -- alerts -------------------------------------------------------------------

def _same_rule(rule: Mapping, ticker: str, kind: str, threshold: str, account: Optional[str]) -> bool:
    return (same_listing(rule['ticker'], ticker) and rule['kind'] == kind and rule['account'] == account
            and Decimal(rule['threshold']) == Decimal(threshold))


def _rules_of_listing(ticker: str, source: str) -> List[dict]:
    """Alert rules on the listing of ``ticker`` in any spelling, except those of ``source``."""
    return [rule for rule in rules.list_rules() if rule['source'] != source and same_listing(rule['ticker'], ticker)]


def _sync_or_refuse(plan: Mapping) -> None:
    try:
        _sync_alerts(plan)
    except rules.AlertRuleError as exc:
        # Raised inside the caller's transaction: the plan change is undone with the rules.
        raise PlanError(f'未能为计划设置提醒，计划未修改：{exc}') from None


def _left_by_ended_plan(rule: Mapping) -> bool:
    """A disabled rule whose plan has ended (or no longer exists): history, not an alert."""
    match = re.fullmatch(r'plan:([0-9]+)', rule['source'] or '')
    if rule['enabled'] or match is None:
        return False
    with desk_store.connect() as db:
        row = db.execute("SELECT status FROM trade_plans WHERE id=?", (int(match.group(1)),)).fetchone()
    return row is None or row['status'] in ENDED


def _sync_alerts(plan: Mapping) -> None:
    """Make the rules of ``source='plan:<id>'`` match the plan. Call inside a desk transaction.

    Rules are unique per ticker, kind, threshold and account whatever their
    source, so a disabled rule left by an ended plan at the same threshold
    would block this plan's own rule; it is deleted (its fired events stay).
    An identical rule from anywhere else (the user's own) covers the alert and
    is left as it is. "Identical" compares the listing, not the spelling
    (``same_listing``).
    """
    source = alert_source(plan['id'])
    if plan['status'] != ACTIVE:
        rules.disable_by_source(source)
        return
    mine: Dict[str, List[dict]] = {}
    for rule in rules.list_rules(source=source):
        mine.setdefault(rule['kind'], []).append(rule)
    others = _rules_of_listing(plan['ticker'], source)
    for kind, field in ALERT_FIELDS:
        threshold = plan[field]
        own = mine.get(kind, [])
        keep = own[0] if own else None
        for extra in own[1:]:
            if extra['enabled']:
                rules.update(extra['id'], {'enabled': False})
        same = [rule for rule in others if threshold is not None
                and _same_rule(rule, plan['ticker'], kind, threshold, plan['account'])]
        for rule in same:
            if _left_by_ended_plan(rule):
                rules.delete(rule['id'])
                others.remove(rule)
        covered = any(rule in others for rule in same)
        if threshold is None or covered:
            if keep is not None and keep['enabled']:
                rules.update(keep['id'], {'enabled': False})
            continue
        if keep is None:
            rules.create({'ticker': plan['ticker'], 'kind': kind, 'threshold': threshold, 'account': plan['account'],
                          'note': f'交易计划 #{plan["id"]}'}, source=source)
        elif not (keep['enabled'] and _same_rule(keep, plan['ticker'], kind, threshold, plan['account'])):
            rules.update(keep['id'], {'threshold': threshold, 'account': plan['account'], 'enabled': True})


def alert_rules(plan_id: int) -> List[dict]:
    """The plan's own alert rules (``source='plan:<id>'``), as ``rules.list`` returns them."""
    return rules.list_rules(source=alert_source(plan_id))


def alert_notes(plan: Mapping) -> List[str]:
    """For an active plan, where an expected alert is not one of its own enabled rules: why (Chinese)."""
    if plan['status'] != ACTIVE:
        return []
    own = alert_rules(plan['id'])
    others = _rules_of_listing(plan['ticker'], alert_source(plan['id']))
    notes = []
    for kind, field in ALERT_FIELDS:
        threshold = plan[field]
        if threshold is None:
            continue
        label = KIND_LABELS[kind]
        if any(rule['enabled'] and _same_rule(rule, plan['ticker'], kind, threshold, plan['account']) for rule in own):
            continue
        covering = [rule for rule in others if _same_rule(rule, plan['ticker'], kind, threshold, plan['account'])]
        if covering:
            rule = covering[0]
            state = '已启用' if rule['enabled'] else '已停用，目前不会提醒'
            notes.append(f'{label}提醒使用已有的相同规则 #{rule["id"]}（{state}），没有重复创建。')
        else:
            notes.append(f'{label}提醒缺失或已停用。再次点击“启用”可以修复。')
    return notes


# -- current price and weight against the plan --------------------------------

def _first_reason(entries: Sequence[Mapping], ticker: Optional[str] = None) -> Optional[str]:
    for entry in entries or []:
        if entry.get('field') == 'day_pnl':
            continue
        if ticker is None or entry.get('ticker') == ticker:
            return entry.get('reason')
    return None


def _estimate_reason(entries: Sequence[Mapping]) -> Optional[str]:
    """The non-verified inputs of a total (``estimated[]`` of a ``Valuation`` or the Today totals), in words."""
    parts = []
    for entry in entries or []:
        subject = entry.get('ticker') or (f'{entry["currency"]} 汇率' if entry.get('currency') else None)
        parts.append(f'{subject}：{entry.get("reason")}' if subject else str(entry.get('reason')))
    if not parts:
        return None
    return '；'.join(parts[:3]) + (f' 等 {len(parts)} 项' if len(parts) > 3 else '')


def _weight_pct(values: Iterable[Decimal], total: Decimal) -> Decimal:
    """A listing's weight in % of the portfolio total: its CNY values summed, then divided once (exact).

    The plan's market block and the buy check both use it, on the same
    amounts, so they agree; the result is rounded once where it is shown.
    Adding per-account percentages that were already rounded is not the same
    number (3.33 + 3.33 for a 6.67% holding).
    """
    with localcontext() as context:
        context.prec = 60
        return sum(values, Decimal(0)) / total * HUNDRED


def _gap_pct(gap: Optional[Decimal], price: Optional[Decimal]) -> Optional[str]:
    return _money(gap / price * HUNDRED) if gap is not None and price else None


def market_unavailable(plan: Mapping, reason: str) -> dict:
    return {'name': None, 'price': None, 'prev_close': None, 'change_pct': None, 'quote': None, 'price_reason': reason,
            'price_estimated': False, 'price_estimated_reason': None, 'position_quantity': None, 'planned_quantity': plan['planned_quantity'], 'quantity_vs_plan': None,
            'weight_pct': None, 'weight_estimated': False, 'weight_reason': reason,
            'planned_weight_pct': plan['planned_weight_pct'], 'weight_vs_plan_pct': None, 'over_planned_weight': None,
            'stop_price': plan['stop_price'], 'distance_to_stop': None, 'distance_to_stop_pct': None,
            'stop_reached': None, 'target_price': plan['target_price'], 'distance_to_target': None,
            'distance_to_target_pct': None, 'target_reached': None, 'as_of': None}


def market_view(plan: Mapping, today: Mapping, quote: Optional[Mapping] = None) -> dict:
    """The plan against the current price and position, from a ``build_today`` payload.

    The rows are the Today rows of the plan's ticker in the plan's account
    (every account when it has none); they give the position and weight. The
    price is the listing's, whichever account holds it: a row of any account,
    else ``quote`` (a ``build_quotes`` item, also used for the ``name``) when
    no row holds the ticker or no row has a price for it (a held B share:
    Today cannot price it in CNY, the quote is in its trading currency, as
    the plan).
    ``price_estimated`` (with ``price_estimated_reason``) says the price is
    not verified, as Today marks the row: a stale quote, a New York close
    before it settles. ``name`` is the instrument's name, null when unknown. Weights are the Today
    weights (of the whole portfolio in CNY); a ticker that is not held weighs
    0 when the total is known. A weight above 0 is ``weight_estimated`` when
    any input of the total is (the Today ``totals.quality`` is 'estimated':
    this row, another holding or a currency rate), with the inputs in
    ``weight_reason``. Unknown values are null with ``*_reason``, never
    guessed. ``distance_to_stop`` = price - stop and
    ``distance_to_target`` = target - price, positive while not reached;
    their ``_pct`` is of the current price.
    """
    view = market_unavailable(plan, '没有行情')
    view['as_of'] = today.get('as_of')
    accounts = [*(today.get('accounts') or [])]
    scoped = [account for account in accounts if plan['account'] in (None, account.get('name'))]
    listing = [row for account in accounts for row in account.get('positions') or []
               if isinstance(row.get('ticker'), str) and same_listing(row['ticker'], plan['ticker'])]
    rows = [row for account in scoped for row in account.get('positions') or []
            if isinstance(row.get('ticker'), str) and same_listing(row['ticker'], plan['ticker'])]
    # The price is the listing's: the plan's own rows first, else another account's (a plan for an account
    # that does not hold the stock yet). A held B share has no Today price (the ledger books it in CNY, it
    # trades in USD or HKD); its quote, in the trading currency like the plan's prices, is then the price
    # (``with_market`` asks for it).
    priced = [row for row in [*rows, *listing] if row.get('price') is not None]
    source = (priced[0] if priced else quote if quote is not None else rows[0] if rows
              else listing[0] if listing else None)
    # The ledger's name for a holding (as the Today row shows it), else the quote's; null when neither has one.
    view['name'] = next((row['name'] for row in [*rows, *listing] if row.get('name') and row['name'] != row['ticker']),
                        None) or (quote or {}).get('name') or None
    if source is not None:
        view.update(price=source.get('price'), prev_close=source.get('prev_close'),
                    change_pct=source.get('change_pct'), quote=source.get('quote'))
        view['price_reason'] = None if source.get('price') is not None else (
            (source.get('quote') or {}).get('reason') or '行情不可用')
    if view['price'] is not None:
        # Whether the price itself is not verified (a stale quote, a New York close that has not settled), as
        # Today marks its row: a quote item says so itself; a Today row's quote is in the totals' estimated
        # inputs (its row flag also counts a currency rate, which the plan's own-currency price does not use).
        if source is quote:
            flagged = (quote.get('estimated_reason') or '行情未经核实') if quote.get('estimated') else None
        else:
            flagged = next((entry.get('reason') or '行情未经核实'
                            for entry in (today.get('totals') or {}).get('estimated') or []
                            if isinstance(entry.get('ticker'), str) and same_listing(entry['ticker'], plan['ticker'])),
                           None)
        view.update(price_estimated=flagged is not None, price_estimated_reason=flagged)

    if not accounts:
        weight_reason = today.get('notice') or '账本没有已确认的持仓'
    elif plan['account'] is not None and not scoped:
        weight_reason = f'账本中没有账户“{plan["account"]}”'
    else:
        weight_reason = None
        view['position_quantity'] = _fmt(sum((Decimal(row['quantity']) for row in rows), Decimal(0)))
    total = (today.get('totals') or {}).get('total_cny')
    weight: Optional[Decimal] = None
    if weight_reason is None:
        if total is None or Decimal(total) <= 0:
            cause = _first_reason((today.get('totals') or {}).get('unavailable') or [])
            weight_reason = '组合总值不可用' + (f'（{cause}）' if cause else '')
        elif any(row.get('weight_pct') is None or row.get('market_value_cny') is None for row in rows):
            row = next(row for row in rows if row.get('weight_pct') is None or row.get('market_value_cny') is None)
            weight_reason = (row.get('quote') or {}).get('reason') or '该持仓的市值不可用'
        else:
            weight = _weight_pct((Decimal(row['market_value_cny']) for row in rows), Decimal(total))
            totals = today.get('totals') or {}
            flagged = [row for row in rows if row.get('estimated')]
            estimated = weight != 0 and (bool(flagged) or totals.get('quality') == 'estimated')
            view['weight_estimated'] = estimated
            weight_reason = (_estimate_reason(totals.get('estimated'))
                             or next((row.get('estimated_reason') for row in flagged), None)) if estimated else None
    view['weight_pct'] = _money(weight)
    view['weight_reason'] = weight_reason

    planned_weight = _decimal(plan['planned_weight_pct'])
    if weight is not None and planned_weight is not None:
        view['weight_vs_plan_pct'] = _money(weight - planned_weight)
        view['over_planned_weight'] = weight > planned_weight
    if view['position_quantity'] is not None and plan['planned_quantity'] is not None:
        view['quantity_vs_plan'] = _fmt(Decimal(view['position_quantity']) - Decimal(plan['planned_quantity']))

    price = _decimal(view['price'])
    stop, target = _decimal(plan['stop_price']), _decimal(plan['target_price'])
    if price is not None and stop is not None:
        view.update(distance_to_stop=_fmt(price - stop), distance_to_stop_pct=_gap_pct(price - stop, price),
                    stop_reached=price <= stop)
    if price is not None and target is not None:
        view.update(distance_to_target=_fmt(target - price), distance_to_target_pct=_gap_pct(target - price, price),
                    target_reached=price >= target)
    return view


def _booked_elsewhere(ticker: str) -> bool:
    """Whether the ledger books ``ticker`` in another currency than it trades in (a B share)."""
    try:
        return parse_instrument(ticker).ledger_currency_problem is not None
    except InstrumentError:
        return False


def with_market(items: Sequence[dict], runtime, ledger) -> List[dict]:
    """``items`` with ``market`` (``market_view``) from one Today payload and one quote request.

    Quotes are asked for the plans' tickers the ledger does not hold, and for
    held B shares, which Today cannot price in the ledger's currency.

    Never raises: when the Today payload or the quotes cannot be had, every
    market value is null with the reason.
    """
    items = [*items]
    if not items:
        return []
    from core.market.today import MAX_QUOTE_TICKERS, build_quotes, build_today
    try:
        today = build_today(ledger, runtime)
    except Exception as exc:
        logger.warning('trade plan view could not build the Today payload: %s', type(exc).__name__)
        return [{**plan, 'market': market_unavailable(plan, '行情或账本暂时不可用')} for plan in items]
    held = {listing_key(row['ticker']) for account in today.get('accounts') or []
            for row in account.get('positions') or []}
    missing = [*dict.fromkeys(plan['ticker'] for plan in items
                              if listing_key(plan['ticker']) not in held or _booked_elsewhere(plan['ticker']))]
    quotes: Dict[str, dict] = {}
    try:
        for start in range(0, len(missing), MAX_QUOTE_TICKERS):
            for item in build_quotes(runtime, missing[start:start + MAX_QUOTE_TICKERS])['quotes']:
                quotes[item['ticker']] = item
    except Exception as exc:
        logger.warning('trade plan view could not read quotes: %s', type(exc).__name__)
    return [{**plan, 'market': market_view(plan, today, quotes.get(plan['ticker']))} for plan in items]


# -- ledger events against plans ----------------------------------------------

def _local_day(stamp: Optional[str], ticker: Optional[str] = None) -> Optional[str]:
    """The date of a stored UTC time stamp in the market of ``ticker`` (Shanghai without one).

    Trades are dated in their market's own calendar (a New York buy at 01:30
    Beijing is dated the day before), so a plan's activation and end are
    compared with a trade date in that market's local day too.
    """
    if not stamp:
        return None
    market = EXCHANGES.get(str(ticker or '').split(':')[0].upper(), (None, CN_MARKET))[1]
    return datetime.fromisoformat(stamp).astimezone(ZONES.get(market, SHANGHAI)).date().isoformat()


def _covers(plan: Mapping, ticker: str, account: Optional[str]) -> bool:
    return same_listing(plan['ticker'], ticker) and plan['account'] in (None, account)


def _in_force(plans: Sequence[Mapping], event: Mapping) -> List[Mapping]:
    """The plans that covered the event's ticker and account on the event's date, the one in force last.

    A plan is in force from the date it was activated. A closed or cancelled
    plan stops being in force for buys on its end date and for sells after
    it, so the sale that ends a plan, booked after closing it the same day,
    still belongs to it. Only one plan should be in force; more than one
    (duplicates an older store may hold) are ordered by activation, and the
    latest is the plan the trade is checked against.
    """
    day, buying = event['date'], event['kind'] == 'buy'
    found = []
    for plan in plans:
        if not _covers(plan, event['ticker'], event.get('account')) or plan['activated_at'] is None:
            continue
        if _local_day(plan['activated_at'], plan['ticker']) > day:
            continue
        end = _local_day(plan['ended_at'], plan['ticker'])
        if end is not None and (day >= end if buying else day > end):
            continue
        found.append(plan)
    return sorted(found, key=lambda plan: (plan['activated_at'], plan['id']))


def _ended_by(plans: Sequence[Mapping], event: Mapping) -> Optional[Mapping]:
    """The latest plan for the event's ticker and account closed or cancelled on or before its date."""
    ended = [plan for plan in plans if _covers(plan, event['ticker'], event.get('account'))
             and plan['status'] in ENDED and plan['ended_at']
             and _local_day(plan['ended_at'], plan['ticker']) <= event['date']]
    return max(ended, key=lambda plan: (plan['ended_at'], plan['id'])) if ended else None


def _no_plan_hint(plans: Sequence[Mapping], event: Mapping) -> str:
    covering = [plan for plan in plans if _covers(plan, event['ticker'], event.get('account'))]
    later = [plan for plan in covering
             if plan['activated_at'] and _local_day(plan['activated_at'], plan['ticker']) > event['date']]
    if later:
        plan = later[0]
        return f'计划 #{plan["id"]} 于 {_local_day(plan["activated_at"], plan["ticker"])} 启用，晚于这笔交易的日期 {event["date"]}。'
    drafts = [plan for plan in covering if plan['status'] == DRAFT]
    if drafts:
        return f'计划 #{drafts[0]["id"]} 仍是草稿，尚未启用。'
    return '建议买入前先写下交易计划（仓位、止损、目标），之后可以对照执行。'


def _deviation(code: str, level: str, message: str, **data) -> dict:
    return {'code': code, 'level': level, 'message': message,
            'data': {key: value for key, value in data.items() if value is not None}}


# -- the ledger at each trade: ``ledger.replay`` on the corrected timeline -----

_PROPOSED = '_plan_check_index'   # marks a proposal's own events on the timeline
_CARRY = 'plan-check-carry:'


def _timeline(history: Sequence[Mapping], events: Sequence[Mapping]) -> List[dict]:
    """The events ``ledger.replay`` applies, in its order.

    ``history`` (with ``_sequence``) comes first and the proposal's
    ``events`` follow it in their order, as ``Ledger.propose`` sequences
    them; everything is then sorted by date and sequence. A reversal anywhere
    (in the history or in the proposal) removes its target, and reversals
    themselves change nothing, exactly as in ``replay``: a reversed event is
    a mistake that never happened, whatever the reversal's own date.
    """
    last = max((event.get('_sequence') or 0 for event in history), default=0)
    proposed = [{**event, '_sequence': last + index + 1, _PROPOSED: index} for index, event in enumerate(events)]
    combined = [*history, *proposed]
    cancelled = {event.get('target_id') for event in combined if event.get('kind') == 'reverse'}
    ordered = sorted((event for event in combined
                      if event.get('kind') != 'reverse' and event.get('id') not in cancelled),
                     key=lambda event: (event['date'], event.get('_sequence') or 0))
    return ordered


def _carried(state: Optional[Mapping]) -> List[dict]:
    """``state`` as opening events, so that ``replay`` continues from it.

    Cash, positions (quantity, average cost, name), contributed capital and
    fund value carry over unchanged; realised profit does not, and nothing
    here reads it. The openings sort before every real event.
    """
    accounts = [*((state or {}).get('accounts') or {}).items()]
    return [{'id': f'{_CARRY}{index}', 'kind': 'opening', 'date': '', '_sequence': index - len(accounts),
             'account': name, 'cash_balances': dict(account.get('cash_balances') or {}),
             'positions': [{'ticker': ticker, 'name': position.get('name'), 'quantity': position['quantity'],
                            'cost_price': position['cost_price']}
                           for ticker, position in (account.get('positions') or {}).items()],
             'contributed_cny': account.get('contributed_cny', '0'), 'fund_cny': account.get('fund_cny', '0')}
            for index, (name, account) in enumerate(accounts)]


def _continued(state: Mapping, events: Sequence[Mapping]) -> Mapping:
    """The ledger state after ``events`` (in replay order, no reversals) applied to ``state``."""
    return replay([*_carried(state), *events]) if events else state


def _around(ordered: Sequence[Mapping], points: Iterable[int], start: Mapping) -> Dict[int, Tuple[Mapping, Mapping]]:
    """``{i: (state just before ordered[i], state just after)}`` for each ``i`` in ``points``.

    One replay per stretch between two points, so the cost grows with the
    events and the points, not with their product.
    """
    found: Dict[int, Tuple[Mapping, Mapping]] = {}
    state, done = start, 0
    for point in sorted(set(points)):
        before = _continued(state, ordered[done:point])
        after = _continued(before, ordered[point:point + 1])
        found[point] = (before, after)
        state, done = after, point + 1
    return found


def _held(state: Mapping, ticker: str, account: Optional[str]) -> Decimal:
    """Shares of ``ticker``'s listing (any spelling) in ``account``, or in every account when ``None``."""
    return sum((Decimal(position['quantity']) for name, value in (state.get('accounts') or {}).items()
                if account in (None, name)
                for held, position in (value.get('positions') or {}).items() if same_listing(held, ticker)),
               Decimal(0))


class MarketUnavailable(Exception):
    """No quotes to value a weight with; the message is the reason shown (Chinese)."""


# What a buy's weight is priced at (``data.price_basis`` of over_planned_weight).
PRICE_BASIS = 'current_quotes'
ESTIMATED_QUALITY = 'estimated'   # ``Valuation.quality`` / Today ``quality`` when an input is not verified


def _fetch_market(runtime, tickers: Sequence[str]) -> Tuple[Mapping, Mapping, Optional[str]]:
    """``(quotes, fx, as_of)``: the runtime's current quotes of ``tickers``, its rates, and when they were asked."""
    from core.market.today import moment
    quotes = runtime.quotes.get(tickers) if tickers else {}
    return quotes, runtime.quotes.fx(), moment(runtime.clock.now())


def _valued(state: Mapping, quotes: Mapping, fx: Mapping):
    """``state`` valued as the Today page values the ledger (``value_portfolio``)."""
    from core.market.valuation import value_portfolio
    with localcontext() as context:
        context.prec = 60
        return value_portfolio(state, quotes, fx, None, [])


def _valuations(states: Sequence[Mapping], market: Optional[Callable],
                reason: Optional[str]) -> List[Tuple[Any, Optional[str], Optional[str]]]:
    """``(Valuation | None, reason, quotes_as_of)`` per state, from one read of ``market``.

    ``market(tickers)`` returns ``(quotes, fx, as_of)`` for the tickers held
    in any of the states; it is called only when there is a state to value.
    """
    if not states:
        return []
    if market is None:
        return [(None, reason or '没有行情', None)] * len(states)
    tickers = [*dict.fromkeys(ticker for state in states for account in (state.get('accounts') or {}).values()
                              for ticker in account.get('positions') or {})]
    try:
        quotes, fx, as_of = market(tickers)
    except MarketUnavailable as exc:
        return [(None, str(exc), None)] * len(states)
    except Exception as exc:
        logger.warning('plan check could not read quotes: %s', type(exc).__name__)
        return [(None, '暂时无法取得行情', None)] * len(states)
    found = []
    for state in states:
        try:
            found.append((_valued(state, quotes, fx), None, as_of))
        except Exception as exc:
            logger.warning('plan check could not value the portfolio: %s', type(exc).__name__)
            found.append((None, '暂时无法计算组合市值', None))
    return found


def _valuation_weight(valuation, reason: Optional[str], ticker: str,
                      account: Optional[str]) -> Tuple[Optional[Decimal], bool, Optional[str]]:
    """``(weight %, estimated, reason)`` of a ticker's listing in a ``Valuation``; weight None when unknown.

    The weight divides by the portfolio total, so it is estimated when any
    input of that total is (``valuation.quality``): the position itself,
    another holding or a currency rate on cash. A weight of 0 is exact.
    """
    if valuation is None:
        return None, False, reason or '没有估值'
    if valuation.total_cny is None or valuation.total_cny <= 0:
        cause = _first_reason(valuation.unavailable)
        return None, False, '组合总值不可用' + (f'（{cause}）' if cause else '')
    accounts = [value for value in valuation.accounts if account in (None, value.name)]
    if account is not None and not accounts:
        return None, False, f'账本中没有账户“{account}”'
    values = []
    for value in accounts:
        for position in value.positions:
            if not same_listing(position.ticker, ticker):
                continue
            if position.weight_pct is None or position.market_value_cny is None:
                return None, False, _first_reason(valuation.unavailable, ticker) or '该持仓的市值不可用'
            values.append(position.market_value_cny)
    weight = _weight_pct(values, valuation.total_cny)
    estimated = weight != 0 and valuation.quality == ESTIMATED_QUALITY
    return weight, estimated, _estimate_reason(valuation.estimated) if estimated else None


PRICE_LABELS = {'entry_low': '入场区间', 'entry_high': '入场区间', 'stop_price': '止损价', 'target_price': '目标价'}


def _price_unknown(plan: Mapping, event: Mapping, fields: Sequence[str]) -> Optional[dict]:
    """The info deviation when the trade's price cannot be compared with the plan's ``fields``, or ``None``.

    A plan's prices are in the instrument's trading currency; the ledger books
    a trade in its market's currency. They differ for B shares (USD or HKD
    traded, CNY booked), and no verified rate of the trade is known, so the
    comparison is unavailable rather than made across currencies.
    """
    set_fields = [field for field in fields if plan.get(field) is not None]
    booked = event.get('currency') or MARKETS.get(str(event.get('ticker', '')).split(':')[0])
    wanted = plan.get('currency') or currency_of(plan['ticker'])
    if not set_fields or booked == wanted:
        return None
    labels = '、'.join(dict.fromkeys(PRICE_LABELS[field] for field in set_fields))
    price = _fmt(Decimal(event['price']))
    return _deviation(PRICE_UNKNOWN, INFO,
                      f'成交价 {price} 按 {booked} 记账，计划价格以 {plan["ticker"]} 的交易币种 {wanted} 计；没有核实的'
                      f'成交汇率，未对照计划的{labels}（不可用）。',
                      price=price, booked_currency=booked, plan_currency=wanted)


def _buy_deviations(plan: Mapping, event: Mapping, after: Decimal, weight: Optional[tuple]) -> List[dict]:
    """``weight``: ``(weight %, estimated, reason, quotes_as_of)`` right after the buy, for a weight plan."""
    price = Decimal(event['price'])
    low, high = _decimal(plan['entry_low']), _decimal(plan['entry_high'])
    stop = _decimal(plan['stop_price'])
    found = []
    unknown = _price_unknown(plan, event, ('entry_low', 'entry_high', 'stop_price'))
    if unknown is not None:
        found.append(unknown)
        low = high = stop = None
    if (low is not None and price < low) or (high is not None and price > high):
        if low is not None and high is not None:
            where = f'计划入场区间 {_fmt(low)}–{_fmt(high)}'
        else:
            where = f'计划入场价下限 {_fmt(low)}' if low is not None else f'计划入场价上限 {_fmt(high)}'
        side = '低于' if low is not None and price < low else '高于'
        found.append(_deviation(OUTSIDE_ENTRY_RANGE, WARNING, f'买入价 {_fmt(price)} {side}{where}。',
                                price=_fmt(price), entry_low=plan['entry_low'], entry_high=plan['entry_high']))
    if stop is not None and price <= stop:
        found.append(_deviation(BELOW_STOP_BUY, WARNING,
                                f'买入价 {_fmt(price)} 不高于计划止损价 {_fmt(stop)}：按计划此时应止损，而不是买入。',
                                price=_fmt(price), stop_price=plan['stop_price']))
    scope = _scope(plan['account'])
    if plan['planned_quantity'] is not None and after > Decimal(plan['planned_quantity']):
        found.append(_deviation(OVER_PLANNED_QUANTITY, WARNING,
                                f'买入后持有 {_fmt(after)} 股（{scope}），超过计划的 {plan["planned_quantity"]} 股。',
                                quantity_after=_fmt(after), planned_quantity=plan['planned_quantity']))
    if plan['planned_weight_pct'] is not None and weight is not None:
        value, estimated, reason, as_of = weight
        planned = Decimal(plan['planned_weight_pct'])
        if value is None:
            found.append(_deviation(WEIGHT_UNKNOWN, INFO,
                                    f'无法计算买入后的仓位比例（{reason}），未检查计划仓位 {_fmt(planned)}%。',
                                    planned_weight_pct=plan['planned_weight_pct'], reason=reason))
        elif value > planned:
            note = f'（按未经核实的行情估算：{reason}）' if estimated else ''
            found.append(_deviation(OVER_PLANNED_WEIGHT, WARNING,
                                    f'按当前行情，买入后该持仓约占组合 {_money(value)}%（{scope}），超过计划的 '
                                    f'{_fmt(planned)}%{note}。',
                                    weight_pct=_money(value), planned_weight_pct=plan['planned_weight_pct'],
                                    estimated=estimated, price_basis=PRICE_BASIS, quotes_as_of=as_of))
    return found


def _sell_deviations(plan: Mapping, event: Mapping) -> List[dict]:
    price = Decimal(event['price'])
    stop, target = _decimal(plan['stop_price']), _decimal(plan['target_price'])
    unknown = _price_unknown(plan, event, ('stop_price', 'target_price'))
    if unknown is not None:
        return [unknown]
    if stop is not None and price < stop:
        return [_deviation(SOLD_BELOW_STOP, INFO, f'卖出价 {_fmt(price)} 低于计划止损价 {_fmt(stop)}。',
                           price=_fmt(price), stop_price=plan['stop_price'])]
    if target is not None and price < target and (stop is None or price > stop):
        return [_deviation(SOLD_BEFORE_TARGET, INFO,
                           f'卖出价 {_fmt(price)} 低于计划目标价 {_fmt(target)}，在达到目标前卖出。',
                           price=_fmt(price), target_price=plan['target_price'])]
    return []


def _plans_for(tickers: Sequence[str]) -> List[dict]:
    """The stored plans of these tickers' listings, in any spelling, oldest first."""
    keys = {listing_key(ticker) for ticker in tickers}
    if not keys or not available():
        return []
    with desk_store.connect() as db:
        rows = db.execute("SELECT * FROM trade_plans ORDER BY id").fetchall()
    return [_plan(row) for row in rows if listing_key(row['ticker']) in keys]


def check_events(events: Sequence[Mapping], state: Optional[Mapping], market: Optional[Callable] = None, *,
                 plans: Optional[Sequence[Mapping]] = None, market_reason: Optional[str] = None,
                 history: Optional[Sequence[Mapping]] = None) -> List[dict]:
    """Compare a ledger proposal's buys and sells with the trade plans.

    events     the proposal's normalised events, in order (each has an ``id``)
    history    the ledger's confirmed events before the proposal
               (``Ledger.history()``, with ``_sequence``). The shares held
               just before and after a trade are those of ``ledger.replay``
               at that trade: every confirmed event and every event of the
               proposal dated before it, or on its date and entered before
               it, without the events a reversal (confirmed or proposed)
               removes. A trade a reversal in its own proposal removes never
               happened: plan_id null, relation none, no deviations.
    state      only without ``history``: the ledger state before the events
               (a proposal's ``before``), which the events then follow by
               date; reversals of events outside the proposal cannot be
               applied
    market     ``market(tickers) -> (quotes, fx, quotes_as_of)``: current
               quotes (``LiveQuote`` by ticker) and rates, called once and
               only when a buy falls under a plan with a planned weight;
               ``None`` (``market_reason`` says why) or an exception leaves
               such weights unknown (``MarketUnavailable``: its message is
               the reason)
    plans      the plans to compare with; default: the stored plans of the
               events' listings

    Raises ``LedgerError`` when the history and the events do not replay
    (a preview the ledger has moved on from).

    Returns one item per buy or sell, in the order of ``events``::

        {event_ref, index, kind, date, ticker, account, quantity, price,
         plan_id, relation, deviations: [{code, level, message, data}]}

    ``event_ref`` is the event id (the ledger keeps it on confirmation) and
    ``index`` its position in ``events``. ``plan_id`` is the plan in force
    (``_in_force``): activated on or before the trade date, not ended before
    it. ``relation``: entry (a buy when nothing was held in the plan's scope:
    its account, or every account), add (a buy on top of a holding), reduce
    (a sell that leaves shares), exit (a sell to zero), none (no plan in
    force). Deviation codes, ``level`` warning unless noted:

    no_plan              a buy with no plan in force and no plan for the
                         ticker ended on or before its date
    after_invalidation   a buy with no plan in force, on or after the date the
                         latest plan for the ticker was closed or cancelled
                         (``plan_id`` is that plan, relation none)
    outside_entry_range  a buy under a plan: price < entry_low or > entry_high
    below_stop_buy       a buy under a plan: price <= stop_price
    price_unknown        (info) a buy or sell under a plan whose prices are in
                         another currency than the ledger booked the trade in
                         (a B share: USD or HKD traded, CNY booked): without a
                         verified trade rate the range, stop and target are
                         not compared, instead of the four price codes;
                         ``data`` {price, booked_currency, plan_currency}
    over_planned_quantity a buy under a plan: quantity held in its scope after
                         the buy > planned_quantity
    over_planned_weight  a buy under a plan: the weight of the position in its
                         scope in the portfolio right after that buy (the
                         ledger at the buy, as above), > planned_weight_pct.
                         Valued like the Today page at current quotes, not at
                         trade prices: ``data.price_basis`` 'current_quotes',
                         ``data.quotes_as_of`` when they were read. A later
                         sale or deposit does not undo it. ``data.estimated``
                         when any quote or rate behind the portfolio total
                         is not verified (this position, another holding,
                         a rate on cash: the valuation's ``quality``), the
                         message then names them
    weight_unknown       (info) a buy under a plan with planned_weight_pct
                         whose weight cannot be computed (no quotes, as when
                         a confirmation has no quotes from its preview; the
                         total or the position value unknown); never guessed
    sold_below_stop      (info) a sell under a plan: price < stop_price
    sold_before_target   (info) a sell under a plan: price < target_price and
                         above the stop (a sale at the stop follows the plan)
    plan_conflict        a buy or sell more than one active plan covered (two
                         plans for one listing and scope, which only an older
                         store can hold): checked against the latest
                         activated, ``data.plan_ids`` lists them all

    ``data`` holds the compared numbers as decimal strings.
    """
    trades = [event for event in events if event.get('kind') in TRADE_KINDS and event.get('ticker')]
    if not trades:
        return []
    if plans is None:
        plans = _plans_for([event['ticker'] for event in trades])
    ordered = _timeline(history or [], events)
    place = {event[_PROPOSED]: position for position, event in enumerate(ordered) if _PROPOSED in event}
    covering = {index: _in_force(plans, event) for index, event in enumerate(events)
                if event.get('kind') in TRADE_KINDS and event.get('ticker') and index in place}
    in_force = {index: found[-1] if found else None for index, found in covering.items()}
    start = {'accounts': {}} if history is not None else (state or {'accounts': {}})
    states = _around(ordered, [place[index] for index, plan in in_force.items() if plan is not None], start)
    # Every buy under a weight plan is valued in the portfolio right after it.
    weighed = [index for index, plan in in_force.items()
               if plan is not None and plan['planned_weight_pct'] is not None and events[index]['kind'] == 'buy']
    weights = {}
    for index, (valuation, reason, as_of) in zip(
            weighed, _valuations([states[place[index]][1] for index in weighed], market, market_reason)):
        plan = in_force[index]
        weights[index] = (*_valuation_weight(valuation, reason, plan['ticker'], plan['account']), as_of)

    results = []
    for index, event in enumerate(events):
        if event.get('kind') not in TRADE_KINDS or not event.get('ticker'):
            continue
        item = {'event_ref': event.get('id'), 'index': index, 'kind': event['kind'], 'date': event['date'],
                'ticker': event['ticker'], 'account': event.get('account'), 'quantity': event.get('quantity'),
                'price': event.get('price'), 'plan_id': None, 'relation': NONE, 'deviations': []}
        results.append(item)
        if index not in place:                             # reversed in its own proposal: it never happened
            continue
        plan = in_force[index]
        deviations: List[dict] = []
        plan_id, relation = (plan['id'] if plan is not None else None), NONE
        if plan is None:
            if event['kind'] == 'buy':
                ended = _ended_by(plans, event)
                if ended is not None:
                    plan_id = ended['id']
                    reason = f'（{ended["close_reason"]}）' if ended['close_reason'] else ''
                    deviations.append(_deviation(
                        AFTER_INVALIDATION, WARNING,
                        f'计划 #{ended["id"]} 已于 {_local_day(ended["ended_at"], ended["ticker"])} '
                        f'{STATUS_LABELS[ended["status"]][1:]}{reason}，之后仍买入 {event["ticker"]}。',
                        plan_status=ended['status'], ended_on=_local_day(ended['ended_at'], ended['ticker']),
                        close_reason=ended['close_reason']))
                else:
                    deviations.append(_deviation(NO_PLAN, WARNING,
                                                 f'买入 {event["ticker"]} 时没有启用中的交易计划。'
                                                 + _no_plan_hint(plans, event)))
        else:
            state_before, state_after = states[place[index]]
            before = _held(state_before, event['ticker'], plan['account'])
            after = _held(state_after, event['ticker'], plan['account'])
            if event['kind'] == 'buy':
                relation = ENTRY if before <= 0 else ADD
                deviations = _buy_deviations(plan, event, after, weights.get(index))
            else:
                relation = EXIT if after <= 0 else REDUCE
                deviations = _sell_deviations(plan, event)
            if len(covering[index]) > 1:
                deviations.append(_conflict(covering[index]))
        item.update(plan_id=plan_id, relation=relation, deviations=deviations)
    return results


def _conflict(found: Sequence[Mapping]) -> dict:
    """The warning for a trade more than one plan covered (duplicates an older store may hold)."""
    latest, others = found[-1], found[:-1]
    names = '、'.join(f'#{plan["id"]}' for plan in others)
    return _deviation(PLAN_CONFLICT, WARNING,
                      f'计划 {names} 与计划 #{latest["id"]} 同时启用，都覆盖这笔交易；同一只股票在同一账户只能有一个'
                      f'启用中的计划。已按最近启用的计划 #{latest["id"]} 对照，请关闭或取消多余的计划。',
                      plan_ids=[plan['id'] for plan in found])


def _ledger_here():
    """The ledger of ``PORTFOLIO_DIR`` (where ``backend/api/ledger.py`` keeps it), or ``None`` without a file."""
    path = portfolio_directory() / 'ledger.sqlite3'
    return Ledger(path) if path.is_file() else None


def _history_at(ledger, revision: int) -> List[dict]:
    """The confirmed events of ledger revision ``revision``, as ``Ledger.history()`` lists them.

    A confirmation that adds events requires the ledger to be at the revision
    its proposal was previewed against (``proposals.revision``) and raises it
    by one, so the events of revision R are exactly those whose proposal was
    previewed below R. Raises ``LedgerError`` when an event's proposal is
    missing and the revision cannot be rebuilt.
    """
    with ledger.connect() as db:
        rows = db.execute("SELECT e.sequence, e.payload, p.revision FROM events e "
                          "LEFT JOIN proposals p ON p.id = e.proposal_id ORDER BY e.sequence").fetchall()
    if any(row['revision'] is None for row in rows):
        raise LedgerError('ledger events without their proposal: revision cannot be rebuilt')
    return [{**json.loads(row['payload']), '_sequence': row['sequence']} for row in rows if row['revision'] < revision]


def _history_before(ledger, proposal: Mapping) -> List[dict]:
    """The ledger's events before ``proposal``.

    For a confirmed proposal: the events confirmed before its own, so a
    link written later (a repair) is the check as it was at confirmation.
    For a pending one: the events of the revision it was previewed against
    (``_history_at``), which its saved ``before`` and ``after`` show; a
    preview reloaded after the ledger moved on is checked against the same
    ledger as its saved states (it can no longer be confirmed). Its own
    events are left out (none, unless an identical external id was
    confirmed since).
    """
    own = {event.get('id') for event in proposal.get('events') or []}
    if not proposal.get('receipt') and isinstance(proposal.get('revision'), int):
        return [event for event in _history_at(ledger, proposal['revision']) if event['id'] not in own]
    events = ledger.history()
    sequences = [event['_sequence'] for event in events if event['id'] in own]
    if proposal.get('receipt') and sequences:
        first = min(sequences)
        return [event for event in events if event['_sequence'] < first]
    return [event for event in events if event['id'] not in own]


# -- quotes kept from a preview for its confirmation ----------------------------

PREVIEW_QUOTES_MAX_AGE = 30 * 60   # seconds a preview's quotes still count as current
PREVIEW_QUOTES_KEPT = 32           # proposals whose preview quotes are kept
_clock = time.monotonic            # tests move it
# (ledger file, proposal id) -> (stamp, quotes, fx, as_of)
_preview_quotes: 'OrderedDict[Tuple[Optional[str], str], Tuple[float, dict, dict, Optional[str]]]' = OrderedDict()
_preview_lock = threading.Lock()


def _ledger_key(ledger) -> Optional[str]:
    """Which ledger a proposal belongs to: its file, resolved. A copy of the portfolio is another ledger."""
    path = getattr(ledger, 'path', None)
    if path is None:
        return None
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path).absolute())


def _keep_quotes(key: Tuple[Optional[str], str], quotes: Mapping, fx: Mapping, as_of: Optional[str]) -> None:
    with _preview_lock:
        _preview_quotes[key] = (_clock(), dict(quotes), dict(fx), as_of)
        _preview_quotes.move_to_end(key)
        while len(_preview_quotes) > PREVIEW_QUOTES_KEPT:
            _preview_quotes.popitem(last=False)


def _kept_market(key: Optional[Tuple[Optional[str], str]]) -> Callable:
    """A ``market`` that only answers with the quotes the preview of ``key`` read, never new ones.

    ``key`` is ``(ledger file, proposal id)``: the same proposal id in another
    ledger (a copied portfolio) was not previewed there and has no quotes.
    """
    def market(tickers):
        with _preview_lock:
            kept = _preview_quotes.get(key) if key is not None else None
        if kept is None:
            raise MarketUnavailable('确认入账时不等待行情，也没有这次预览时取得的行情')
        stamp, quotes, fx, as_of = kept
        if _clock() - stamp > PREVIEW_QUOTES_MAX_AGE:
            raise MarketUnavailable(f'确认入账时不等待行情，预览时取得的行情已超过 {PREVIEW_QUOTES_MAX_AGE // 60} 分钟')
        missing = [ticker for ticker in tickers if ticker not in quotes]
        if missing:
            raise MarketUnavailable('确认入账时不等待行情，预览时没有取得 ' + '、'.join(missing[:3])
                                    + (' 等' if len(missing) > 3 else '') + ' 的行情')
        return quotes, fx, as_of
    return market


def check_proposal(proposal: Mapping, runtime=None, *, ledger=None, fetch: bool = True) -> Optional[List[dict]]:
    """``check_events`` for a ledger proposal; ``None`` while no plan was ever saved.

    ``ledger`` is the ledger the proposal belongs to (default: the one in
    ``PORTFOLIO_DIR``); its history places the proposal's trades. With
    ``fetch`` (a preview), weights are at the runtime's current quotes,
    requested only when a buy falls under a plan with a planned weight, and
    the quotes are kept for the proposal's confirmation in that ledger. Without
    ``fetch`` (a confirmation) no quote is requested: weights use the quotes
    kept by a preview of this proposal in this ledger, or are ``weight_unknown``.
    """
    if not in_use():
        return None
    if ledger is None:
        ledger = _ledger_here()
    history = _history_before(ledger, proposal) if ledger is not None else None
    proposal_id = proposal.get('id')
    key = (_ledger_key(ledger), proposal_id) if isinstance(proposal_id, str) else None

    def market(tickers):
        current = runtime
        if current is None:
            from core.market.runtime import get_market_runtime
            current = get_market_runtime()
        quotes, fx, as_of = _fetch_market(current, tickers)
        if key is not None:
            _keep_quotes(key, quotes, fx, as_of)
        return quotes, fx, as_of

    return check_events([*(proposal.get('events') or [])], proposal.get('before'),
                        market if fetch else _kept_market(key), history=history)


def _trades(proposal: Mapping) -> List[Mapping]:
    return [event for event in proposal.get('events') or []
            if event.get('kind') in TRADE_KINDS and event.get('ticker') and event.get('id')]


# -- the check a confirmation records, stored right before the ledger confirms ---

DRAFTS = 'plan_link_drafts'
DRAFT_MIGRATIONS = [[
    # One row per pending proposal about to be confirmed in a ledger file: the
    # check_events items its links are to record. Removed once they are written.
    """CREATE TABLE plan_link_drafts (
        proposal_id TEXT NOT NULL,
        ledger TEXT NOT NULL,
        revision INTEGER NOT NULL,
        items TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (proposal_id, ledger))""",
]]
desk_store.register_schema(DRAFTS, DRAFT_MIGRATIONS)
DRAFT_DAYS = 30   # a stored check never used (the confirmation failed) is dropped after this


def _revision(ledger) -> Optional[int]:
    with ledger.connect() as db:
        row = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()
    return row[0] if row is not None else None


def prepare_links(proposal: Mapping, *, ledger) -> bool:
    """Right before ``ledger`` confirms the pending ``proposal``: store the check its links are to record.

    The plans as they are when the trade is confirmed decide its links. The
    ledger and the desk store are two files, so the links are written after
    the ledger committed; when that write is interrupted, the repeated
    confirmation (``record_links``) records this stored check, not the plans
    as edited since. Stores nothing for a confirmed proposal, for one the
    ledger can no longer confirm (it moved on since the preview), or while
    no plan was ever saved. Never asks for quotes (weights at the preview's
    kept quotes, as ``record_links``). Returns whether a check was stored.
    """
    if not isinstance(proposal, Mapping) or proposal.get('receipt') or not _trades(proposal):
        return False
    key = _ledger_key(ledger)
    if key is None or not in_use() or _revision(ledger) != proposal.get('revision'):
        return False
    items = check_proposal(proposal, ledger=ledger, fetch=False)
    if items is None:
        return False
    desk_store.ensure_schema(DRAFTS, DRAFT_MIGRATIONS)
    with desk_store.transaction() as db:
        stamp = _now()
        oldest = (datetime.fromisoformat(stamp) - timedelta(days=DRAFT_DAYS)).isoformat()
        db.execute("DELETE FROM plan_link_drafts WHERE created_at < ?", (oldest,))
        db.execute("INSERT OR REPLACE INTO plan_link_drafts(proposal_id, ledger, revision, items, created_at) "
                   "VALUES (?,?,?,?,?)", (proposal['id'], key, proposal['revision'], desk_store.encode(items), stamp))
    return True


def _stored_check(proposal: Mapping, key: Optional[str]) -> Optional[List[dict]]:
    """The check ``prepare_links`` stored for this proposal in this ledger at its revision, or ``None``."""
    if key is None or not desk_store.ready(DRAFTS, DRAFT_MIGRATIONS):
        return None
    with desk_store.connect() as db:
        row = db.execute("SELECT revision, items FROM plan_link_drafts WHERE proposal_id=? AND ledger=?",
                         (proposal.get('id'), key)).fetchone()
    if row is None or row['revision'] != proposal.get('revision'):
        return None
    return json.loads(row['items'])


def record_links(proposal: Mapping, *, ledger=None) -> int:
    """Store the plan links of a confirmed ledger proposal, once; returns how many rows were new.

    ``proposal`` is ``Ledger.proposal(id)`` after confirmation, ``ledger``
    the ledger it was confirmed in (default: the one in ``PORTFOLIO_DIR``).
    Events that already have a link are left alone (a second confirmation
    writes nothing, and a call after an interrupted one fills in what is
    missing). Nothing is written while no plan was ever saved.

    The links record the check ``prepare_links`` stored right before the
    ledger confirmed, when there is one: a repair after an interrupted write
    keeps the plans as they were at confirmation. Otherwise the check is made
    now. It runs in the confirm request, so it never asks for quotes: weights
    are valued at the quotes the proposal's preview read (``check_proposal``
    without ``fetch``), and are ``weight_unknown`` when there are none.
    """
    if not isinstance(proposal, Mapping) or not proposal.get('receipt'):
        return 0
    trades = _trades(proposal)
    if not trades or not in_use():
        return 0
    ids = [event['id'] for event in trades]
    with desk_store.connect() as db:
        linked = set()
        for start in range(0, len(ids), 500):
            part = ids[start:start + 500]
            linked |= {row[0] for row in db.execute(
                f"SELECT event_id FROM plan_links WHERE event_id IN ({','.join('?' * len(part))})", part)}
    if linked >= set(ids):
        return 0
    if ledger is None:
        ledger = _ledger_here()
    key = _ledger_key(ledger)
    stored = _stored_check(proposal, key)
    items = stored if stored is not None else (check_proposal(proposal, ledger=ledger, fetch=False) or [])
    check = {item['event_ref']: item for item in items}
    ensure()
    written = 0
    with desk_store.transaction() as db:
        stamp = _now()
        for event in trades:
            item = check.get(event['id'])
            if event['id'] in linked or item is None:
                continue
            written += db.execute(
                "INSERT OR IGNORE INTO plan_links(event_id, plan_id, relation, deviations, created_at, proposal_id, "
                "event_date, kind, ticker, account, quantity, price) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (event['id'], item['plan_id'], item['relation'], desk_store.encode(item['deviations']), stamp,
                 proposal.get('id'), event['date'], event['kind'], event['ticker'], event.get('account'),
                 event.get('quantity'), event.get('price'))).rowcount
        if stored is not None:
            db.execute("DELETE FROM plan_link_drafts WHERE proposal_id=? AND ledger=?", (proposal.get('id'), key))
    return written


# -- reading the ledger without writing to it --------------------------------------

LEDGER_READ_TIMEOUT = 5.0   # seconds a read waits for a commit in progress


def read_only_ledger(directory=None):
    """The confirmed ledger of ``directory`` (default ``PORTFOLIO_DIR``) for reading, or ``None``.

    ``core.ledger.read_confirmed``: ``None`` without a file, for a file
    without the ledger's tables (an empty file) or without a confirmed
    revision. The file is read read-only and never created, grown or changed,
    and no ``-wal`` / ``-shm`` file is left beside a WAL ledger, so a GET, also
    in the read-only demo, leaves it as it is. ``build_today`` and the plan
    views accept the result in place of a ``Ledger``. Raises
    ``core.ledger.LedgerUnavailable`` when the file cannot be read.
    """
    return read_confirmed(directory, timeout=LEDGER_READ_TIMEOUT)


def _reversed_ids(ledger) -> Optional[set]:
    """Ids of ledger events reversed since; ``None`` when the ledger cannot be read."""
    try:
        if ledger is None:
            ledger = read_only_ledger()
            if ledger is None:
                return set()
        return {event.get('target_id') for event in ledger.history() if event.get('kind') == 'reverse'}
    except Exception as exc:
        logger.warning('plan links could not read the ledger: %s', type(exc).__name__)
        return None


def _link(row, reversed_ids: Optional[set]) -> dict:
    return {'event_id': row['event_id'], 'event_date': row['event_date'], 'kind': row['kind'],
            'ticker': row['ticker'], 'account': row['account'], 'quantity': row['quantity'], 'price': row['price'],
            'plan_id': row['plan_id'], 'relation': row['relation'], 'deviations': json.loads(row['deviations']),
            'proposal_id': row['proposal_id'], 'created_at': row['created_at'],
            'reversed': None if reversed_ids is None else row['event_id'] in reversed_ids}


def links_for_plan(plan_id: int, ledger=None) -> List[dict]:
    """The confirmed ledger events linked to a plan, oldest first.

    Each is ``{event_id, event_date, kind, ticker, account, quantity, price,
    plan_id, relation, deviations, proposal_id, created_at, reversed}``;
    ``reversed`` is true when the ledger has reversed the event since, null
    when the ledger could not be read.
    """
    if isinstance(plan_id, bool) or not isinstance(plan_id, int) or not available():
        return []
    with desk_store.connect() as db:
        rows = db.execute("SELECT * FROM plan_links WHERE plan_id=? ORDER BY event_date, rowid", (plan_id,)).fetchall()
    if not rows:
        return []
    reversed_ids = _reversed_ids(ledger)
    return [_link(row, reversed_ids) for row in rows]


def deviations_for_date(day, ledger=None) -> List[dict]:
    """Every confirmed buy and sell dated ``day`` (YYYY-MM-DD or a ``date``) with its plan check.

    Items as in ``links_for_plan`` (``deviations`` may be empty: the trade
    followed its plan). Events the ledger has reversed since are left out.
    Only trades confirmed while plans were in use have a link. Reads only.
    """
    if isinstance(day, Date):
        day = day.isoformat()
    try:
        day = valid_date(day)
    except LedgerError:
        raise PlanError('日期格式应为 YYYY-MM-DD。') from None
    if not available():
        return []
    with desk_store.connect() as db:
        rows = db.execute("SELECT * FROM plan_links WHERE event_date=? ORDER BY rowid", (day,)).fetchall()
    if not rows:
        return []
    reversed_ids = _reversed_ids(ledger)
    return [link for link in (_link(row, reversed_ids) for row in rows) if not link['reversed']]


# -- the trade_plan proposal kind (the agent may only propose) ----------------

PROPOSAL_KIND = 'trade_plan'
# The summary is cut at ``proposals.MAX_SUMMARY`` characters. Long texts are
# shortened here instead, so every field and the closing sentences always fit;
# the full texts are in the proposal's payload.
SUMMARY_TEXT = 80
SUMMARY_ACCOUNT = 24


def _short(text: str, limit: int = SUMMARY_TEXT) -> str:
    return text if len(text) <= limit else text[:limit - 1] + '…'


def describe_proposal(payload: Mapping) -> str:
    """The summary a person reads before confirming: every field, built by code."""
    def value(field: str, unit: str = '') -> str:
        return f'{payload[field]}{unit}' if payload.get(field) is not None else '未填'

    if payload.get('planned_weight_pct') is not None:
        size = f'占组合 {payload["planned_weight_pct"]}%'
    elif payload.get('planned_quantity') is not None:
        size = f'{payload["planned_quantity"]} 股'
    else:
        size = '未填'
    loss = max_loss({**payload})
    loss_text = f'{loss["amount"]} {loss["currency"]}' if loss['amount'] is not None else '无法计算'
    account = payload.get('account')
    return (f'新建交易计划（草稿）：买入 {payload["ticker"]}，'
            f'{_scope(_short(account, SUMMARY_ACCOUNT) if account else None)}；'
            f'入场区间 {value("entry_low")}–{value("entry_high")}；止损价 {value("stop_price")}；'
            f'目标价 {value("target_price")}；计划仓位 {size}；最大亏损 {loss_text}；'
            f'复查日期 {value("review_on")}；买入理由：{_short(payload["thesis"])}；'
            f'判断失效条件：{_short(payload["invalidation"])}。'
            '确认后保存为草稿，需要你在计划页启用后才会生成提醒。' + NOT_AN_ORDER)


def _apply_proposal(payload: dict, proposal: dict) -> dict:
    plan = create(payload, source=proposal['source'])
    return {'plan_id': plan['id'], 'status': plan['status']}


def register_proposal_kind() -> None:
    proposals.register_kind(PROPOSAL_KIND, validate, _apply_proposal, describe_proposal, version='1')


register_proposal_kind()

# ``plans.list`` is the public name; bound last so nothing here shadows the built-in.
list = list_plans
