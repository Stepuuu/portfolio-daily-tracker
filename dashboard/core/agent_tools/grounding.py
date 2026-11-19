"""Find numbers in an answer that this turn's data does not contain.

Pure functions, no I/O. The agent service calls them once per finished
answer::

    from core.agent_tools import grounding

    missing = grounding.ungrounded_numbers(
        answer_text,                       # the assistant's final text
        tool_results,                      # every tool result of THIS turn (dicts/lists/str)
        context_numbers=[evidence],        # the context's value fields (LiveContext.evidence)
        user_text=user_message)            # numbers the user typed are theirs
    note = grounding.footnote(missing)             # "" when nothing is missing
    if note:
        answer_text += "\n\n" + note

The footnote is built by code, never by the model, so it cannot be argued away.

Evidence keeps its unit
-----------------------
Every number of the data is evidence of one unit class:

``money``     prices and amounts: a field named like price, close, open, high,
              low, cost, value, pnl, cash, amount, total, fee, rate, threshold,
              target, stop, ... or ``*_cny``; in text a number written with
              元/港元/美元/¥/$
``percent``   a field with ``pct``/``percent`` in its name (3.38 means 3.38%);
              in text a number written with %
``fraction``  a field with ``ratio``/``fraction`` in its name (0.0338 means 3.38%)
``quantity``  shares and volumes (``quantity``, ``volume``, ``shares``, ...); in
              text a number written with 股/手/份
``index``     a level without a currency: an oscillator (RSI, KDJ, ...) or index
              points; grounds only a bare number
``any``       a number whose unit is not known: another field name, or a bare
              number in system or user text

A producer's own declaration comes first:

* a ``units`` mapping in a result (``get_price_history``, ``get_indicators``,
  ``get_market_breadth``, ...) gives each field it names its class, for that
  result and everything inside it: a text starting with ``%`` is percent, one
  with ``价格`` or a currency code money, one starting with 股/份/手 quantity,
  a range such as ``0-100`` index, anything else (``只``, ...) no evidence;
* a record with ``value`` and ``unit`` (a brief's fact) gives ``value`` the
  class of its unit: a currency code is money, ``pct`` percent, ``points``
  index, ``count``/``date``/``state``/none no evidence; its ``text`` and
  ``label`` (the display of that value) are no evidence either.

Otherwise an indicator field takes its class from ``_INDICATORS``, an
explicit table of the indicators the tools compute (moving averages, ATR,
MACD, Bollinger bands: money; RSI, ADX, MFI, KDJ, Williams %R, CCI: index;
volume averages, OBV: quantity; ``close_vs_ma_N``, ``momentum_N``,
``hist_vol_N``: percent, as ``get_indicators`` computes them), and any other
``<name>_<period>`` field (``high_low_ratio``, ``boll_width``, a factor of
``backtesting/factors`` the table does not know) is no evidence: its scale is
not known. Every other field takes the class of its own name, else of the
nearest enclosing key that has one (``cash: {CNY: "100"}`` is money). A
``columns`` + ``rows`` table labels each cell by its column.

No evidence at all, in any result:

* fields named like a date or time (``data_date``, ``fetched_at``,
  ``quote_time``, ``published``, ``first``, ``last``, ...);
* metadata (``_is_metadata``): labels (``market`` holding a text; a
  ``market`` record, a plan's or alert rule's market view, is evidence), ids (``id``, ``*_id``, ``*_ids``), ``seq``,
  revisions and versions (``ledger_revision``, ``kind_version``), counts of
  rows and items (``count``, ``*_count``, ``n``, a number under ``rows``),
  paging (``page``, ``offset``, ``limit``), ``coverage``, ``ref``;
* a brief's model ``commentary``: a model's words about the facts, not facts;
* the tool's own words: the text under ``note``/``notes``, ``notice``,
  ``message``, ``error``, ``reason``, ``hint``, ``detail``, ``summary``,
  ``label``, ``title``, ``condition``, ``cite``, ... (a refusal such as
  ``{"error": "...42.50..."}`` proves nothing).

In every other text of the data, dates, times, URLs, version numbers,
exchange codes (``HKG:00700``), rule ids (``#12``), ordinals and list numbers
are masked first, and counts (``3天``, ``5个``) are no evidence. Inside
``untrusted_text`` (third-party text) only a number written with its unit is
evidence, of that unit: a bare figure in a headline grounds nothing.

Simple arithmetic is evidence too, and only this:

* the difference of two price fields of one record (money), whichever they
  are (``high`` - ``low``, ``close`` - ``prev_close``);
* the change in percent from an earlier price to a later one, divided by the
  earlier: from ``prev_close``/``previous_close``/``pre_close`` (or another
  name in ``_EARLIER``) or from a cost (``cost_price``, ``avg_cost``, ...) to
  ``close``/``price``/``current_price``/``last_price`` of the same record, and
  from a price field of one row to the same field of the next row of one
  instrument's dated series (rows put in date order, newest-first lists
  included). Nothing else is divided: no change in percent between
  ``high`` and ``low``, and none relative to the later price.

What is checked, and against what
---------------------------------
A percentage (``5.2%``) needs percent evidence (or a fraction times 100, or
an ``any`` number as written); an amount with a currency word or symbol
(``1850元``, ``¥12.5``, ``1.2亿元``) needs money or ``any``; a share count
needs quantity or ``any`` (``手`` also a quantity of 100 shares a lot) and is
checked when it has decimals or 万/亿 (``8269.1万股``) or when the answer
states a holding or a trade with it (持有, 持仓, 买入, 卖出, 加仓, 数量, 你有,
...: ``持有 300 股``), but not as a rate (``每10股``); a bare decimal
(``12.34``) or a bare 万/亿 figure needs any class, as written. Plain integers
are not checked (counts, periods, list numbers), nor are dates, times,
tickers and codes, ordinals, URLs, list numbering, version numbers
(``Python 3.10``, ``v2.1``, ``2.1版``), numbers glued to letters (``MA5``) and
counts such as ``3天`` or ``一手 100 股``. A number is grounded when some
evidence of an accepted class equals it after rounding or truncation to the
decimals the answer wrote, ignoring sign (the answer says "下跌", the data
says -5.2; ``+``, ``-``, ``−``, ``＋`` alike) and thousands separators;
full-width digits are digits.

A distance of an alert rule's or a trade plan's view (``distance``,
``distance_to_stop``, ``distance_to_target``: the gap in the rule's own unit,
percentage points for a weight rule, a price otherwise) is grounded whatever
unit word the answer writes after it (%, 个百分点, 元, ...). Every other field
keeps its class: a price or an amount grounds money and bare numbers only,
never a percentage or a share count.

No value is exempt: ``0.00`` and ``100%`` are grounded only when a zero or a 100
of an accepted class is in the data, like any other figure. A price of zero
(``现价 0.00 元``, ``股价为 ¥0``: 0 right after 现价, 最新价, 股价, 价格, 报价,
收盘价, 开盘价, 成交价 or 当前价) is grounded only by a price field that is zero
(``close``, ``price``, ...) or by the same price of zero in the user's own
message: a zero amount elsewhere (a fund of 0.00) is no price.

Image turns. Code cannot read an uploaded image, so the check of an image
turn is the same as any other, with the same evidence (the context's value
fields and what the user typed; an image turn calls no tools): a number the
model read from the image is in no data and is listed like any other. None
is exempted because it might come from the image, and none is listed only
because the turn had one. ``footnote(..., image=True)`` adds ``IMAGE_NOTE``,
which says exactly that: compare the listed numbers with the image, and treat
any that are not from it as unverified.

Known limits (see tests/test_grounding_cases.py): a figure computed in any
other way (a sum, a ratio of two amounts, a cross-currency conversion) is
flagged because it is in no result, and the footnote asks the reader to check
it, which is the right outcome for an unverifiable figure. Ratios in ``倍`` are
never checked, only the number that carries the unit in a range
(``1300-1500元``) is, the direction of a move is not compared with the sign, and
a rhetorical "100%" is flagged.
"""
from __future__ import annotations

import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

_MULT = {'': Decimal(1), '万': Decimal(10) ** 4, '亿': Decimal(10) ** 8, '万亿': Decimal(10) ** 12}
_CURRENCY_BEFORE = '¥￥$'
_NUMBER = re.compile(
    r'(?<![A-Za-z0-9_.])'
    r'(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\.\d)'
    r'(?P<mult>[ ]?(?:万亿|亿|万))?'
    r'(?P<tail>[ ]?(?:%|％|个百分点|百分点|港元|美元|人民币|元|块))?')
# shares: a quantity, never a price (手 is a lot)
_SHARE_UNITS = re.compile(r'\s?(股|手|份)')
# words before a share count that say the answer states a holding or a trade with it
_HOLDING = re.compile(r'持有|持仓|持股|拥有|仓位|买入|卖出|买了|卖了|加仓|减仓|建仓|清仓|补仓|数量|剩余|剩下|还剩|'
                      r'还有|共有|一共|合计|总共|你有|您有|我有|他有')
_CLAUSE_END = re.compile(r'[。！？!?；;，,\n]')
# a price word right before a number (and the currency symbol written with it)
_PRICE_BEFORE = re.compile(r'(?:现价|最新价|股价|价格|报价|收盘价|开盘价|成交价|当前价)\s*(?:为|是|在|约|报|：|:)?\s*'
                           r'(?:HK\$|US\$|[¥￥$])?\s*$')
# counts and durations: never a price, never evidence
_COUNT_UNITS = re.compile(r'\s?(?:倍|天|日|个|只|年|月|周|次|笔|岁|分钟|小时|秒|x\b|X\b|th\b|st\b|nd\b|rd\b|条|家|名|位|项|期|季|档)')
# dates, times and URLs
_DATE_MASKS = [
    re.compile(r'https?://\S+'),
    re.compile(r'\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*[日号]?'),
    re.compile(r'\d{4}\s*年\s*\d{1,2}\s*月'),
    re.compile(r'\d{1,2}\s*月\s*\d{1,2}\s*[日号]?'),
    re.compile(r'(?<![\d.])\d{1,2}/\d{1,2}(?![\d.])'),                       # 11/17 style
    re.compile(r'\d{1,2}:\d{2}(?::\d{2})?'),
    re.compile(r'(?<!\d)(?:19|20)\d{6}(?!\d)'),                            # 20261117
    re.compile(r'(?<!\d)\d{4}\s*年'),
    re.compile(r'\d{1,2}\s*月(?:份)?'),
]
# version numbers: Python 3.10, v2.1, 2.1版 (a product name, 版本 or v before the number)
_VERSION = re.compile(
    r'(?i)(?:\b(?:python|node(?:\.js)?|java|windows|macos|ios|android|linux|ubuntu|excel|office|chrome|firefox|'
    r'gpt|claude|gemini|llama|pandas|numpy|akshare|yfinance|pytest|django|react|vue|angular|version|ver)'
    r'[\s\-]*v?\d+(?:\.\d+){0,3}'
    r'|(?<![A-Za-z0-9])v\d+(?:\.\d+){1,3}'
    r'|版本[\s号]*v?\d+(?:\.\d+){0,3}'
    r'|(?<![\d.])\d+(?:\.\d+){1,3}\s*版(?:本)?)')
# masked in the answer AND in every text of the data
_MASKS = _DATE_MASKS + [
    _VERSION,
    re.compile(r'[A-Za-z]{2,6}:[A-Za-z0-9.\-]+'),                 # SHA:600519
    re.compile(r'#\s*\d+'),                                       # rule #12
    re.compile(r'第\s*\d+'),
    re.compile(r'(?m)^[ \t>*+-]*\d{1,3}(?:\.(?!\d)|[)、:：])'),            # list numbering at line start
    re.compile(r'[（(]\s*\d{1,2}\s*[）)]'),                       # (1)
    re.compile(r'(?<![\d.])0\d{3,}(?![\d.])'),                    # 00700, leading-zero codes
    re.compile(r'(?<![\d.¥￥$])\d{6}(?![\d.]|\s?[元%％万亿块])'),  # bare 6-digit exchange codes
]
_NUMERIC_TEXT = re.compile(r'\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*')

# evidence classes, and which of them an answer number of each unit accepts (with the scale applied to the data)
MONEY, PERCENT, FRACTION, QUANTITY, INDEX, ANY = 'money', 'percent', 'fraction', 'quantity', 'index', 'any'
SKIP = 'skip'          # a field that is no evidence at all
_ACCEPTS = {
    'percent': ((PERCENT, Decimal(1)), (ANY, Decimal(1)), (FRACTION, Decimal(100))),
    'money': ((MONEY, Decimal(1)), (ANY, Decimal(1))),
    'quantity': ((QUANTITY, Decimal(1)), (ANY, Decimal(1))),
    'lots': ((QUANTITY, Decimal(1)), (QUANTITY, Decimal('0.01')), (ANY, Decimal(1))),
    'bare': ((MONEY, Decimal(1)), (QUANTITY, Decimal(1)), (ANY, Decimal(1)), (PERCENT, Decimal(1)),
             (FRACTION, Decimal(1)), (INDEX, Decimal(1))),
}
_PERCENT_PARTS = frozenset({'pct', 'percent', 'percentage'})
_FRACTION_PARTS = frozenset({'ratio', 'fraction'})
_QUANTITY_PARTS = frozenset({'quantity', 'qty', 'volume', 'shares', 'lots', 'counts'})
_MONEY_PARTS = frozenset({'price', 'close', 'open', 'high', 'low', 'cost', 'value', 'pnl', 'cash', 'amount', 'cny',
                          'usd', 'hkd', 'total', 'fee', 'fees', 'threshold', 'target', 'stop', 'entry', 'rate',
                          'dividend', 'turnover', 'loss', 'profit', 'distance', 'balance', 'fund', 'nav', 'boll',
                          'cap', 'change', 'revenue', 'income'})
# the price family: differences between these are evidence
_PRICE_PARTS = frozenset({'price', 'close', 'open', 'high', 'low', 'cost'})
_NOT_PRICE_PARTS = frozenset({'pct', 'percent', 'value', 'pnl', 'amount', 'total', 'distance', 'volume', 'quantity',
                              'cny', 'reason', 'status', 'basis', 'source', 'kind'})
# the change in percent of a record: from an earlier price to a later one, divided by the earlier
_EARLIER = frozenset({'prev_close', 'previous_close', 'pre_close', 'preclose', 'prior_close', 'yesterday_close',
                      'cost', 'cost_price', 'avg_cost', 'average_cost', 'avg_price', 'average_price', 'unit_cost'})
_LATER = frozenset({'close', 'price', 'last', 'last_price', 'current_price', 'latest_price'})
# indicator fields by name, when their tool declares no unit (see "Evidence keeps its unit")
_INDICATORS = (
    (re.compile(r'(?:sma|ema|wma|ma|vwap|atr|macd|macd_signal|macd_hist|boll_upper|boll_mid|boll_lower)(?:_\d+)?'),
     MONEY),
    (re.compile(r'(?:rsi|adx|mfi|cci|kdj_[kdj]|williams_r)(?:_\d+)?'), INDEX),
    (re.compile(r'(?:volume_ma|obv)(?:_\d+)?'), QUANTITY),
    (re.compile(r'(?:close_vs_ma|momentum|hist_vol)(?:_\d+)?'), PERCENT),
    (re.compile(r'(?:high_low_ratio|boll_width|volume_ratio)(?:_\d+)?'), SKIP),
)
_INDICATOR_LIKE = re.compile(r'[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)*_\d+')


def _dec(text: str) -> Optional[Decimal]:
    try:
        value = Decimal(text.replace(',', '').strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _blank(text: str, patterns) -> str:
    for pattern in patterns:
        text = pattern.sub(lambda m: ' ' * len(m.group(0)), text)
    return text


def _mask(text: str) -> str:
    return _blank(text, _MASKS)


class _Token:
    __slots__ = ('display', 'value', 'decimals', 'mult', 'unit', 'checked')

    def __init__(self, display, value, decimals, mult, unit, checked):
        self.display, self.value, self.decimals = display, value, decimals
        self.mult, self.unit, self.checked = mult, unit, checked


def _tokens(text: str) -> List[_Token]:
    """The numbers of ``text`` after masking, each with the unit written with it.

    ``unit`` is ``percent``, ``money``, ``quantity`` (股/份), ``lots`` (手),
    ``count`` (天, 个, 倍, ...) or ``bare``; ``checked`` says whether an
    answer's number is checked. A share count is shown with its unit (``300股``).
    """
    masked = _mask(text)
    found = []
    for match in _NUMBER.finditer(masked):
        raw = match.group('num')
        value = _dec(raw)
        if value is None:
            continue
        mult = (match.group('mult') or '').strip()
        tail = (match.group('tail') or '').strip()
        start, end = match.span()
        symbol = start > 0 and (masked[start - 1] in _CURRENCY_BEFORE
                                or masked[max(start - 2, 0):start] in ('HK', 'US'))
        percent = tail in ('%', '％', '个百分点', '百分点')
        after = masked[end:end + 4]
        share = _SHARE_UNITS.match(after)
        display = text[start:end].replace(' ', '')
        if percent:
            unit = 'percent'
        elif tail or symbol:
            unit = 'money'
        elif share:
            unit = 'lots' if share.group(1) == '手' else 'quantity'
            display += share.group(1)
        elif _COUNT_UNITS.match(after):
            unit = 'count'
        else:
            unit = 'bare'
        decimal = '.' in raw
        if unit in ('money', 'bare') and value == 0 and _PRICE_BEFORE.search(masked[max(0, start - 16):start]):
            unit = 'zero_price'                 # grounded by a zero price field only (Evidence.prices)
        if unit == 'count':
            checked = False
        elif unit in ('quantity', 'lots'):
            checked = bool(mult) or decimal or _holding(masked, start)
        else:
            checked = unit in ('percent', 'money', 'zero_price') or decimal or bool(mult)
        found.append(_Token(display, value, len(raw.split('.')[1]) if decimal else 0, _MULT[mult], unit, checked))
    return found


def _holding(masked: str, start: int) -> bool:
    """Whether the share count at ``start`` is a holding or a trade the answer states (not a rate such as 每10股)."""
    before = masked[:start]
    if before.rstrip().endswith('每'):
        return False
    ends = [match.end() for match in _CLAUSE_END.finditer(before)]
    return bool(_HOLDING.search(before[ends[-1] if ends else 0:]))


# -- evidence ----------------------------------------------------------------------

# keys whose values are dates or times, never financial evidence
_DATE_KEY = re.compile(r'(?:^|_)(?:date|dates|time|stamp|fetched|published)(?:$|_)|_at$')
# keys whose whole value is metadata or boilerplate
_SKIP_KEYS = frozenset({'first', 'last', 'requested_start', 'requested_end', 'units', 'columns', 'coverage',
                        'count', 'total_available', 'bars_used', 'requested_days', 'ticker', 'source', 'provider',
                        'market', 'calendar', 'currency', 'adjust', 'scope', 'window', 'cached', 'truncated',
                        'page', 'paging', 'ref', 'refs', 'session',
                        'commentary'})          # a brief's model commentary: 模型点评，不是事实
# label keys: metadata when they hold a label ('market': 'a_share'), evidence when they hold a record
# (a plan's or an alert rule's ``market`` view: price, weight, distance)
_LABEL_KEYS = frozenset({'market'})
# metadata: ids, sequence numbers, revisions, versions, counts of items, paging
_META_KEY = re.compile(r'(?:^|_)(?:id|ids|uuid|seq|revision|version|count|counts|n|limit|offset|index|idx|rank|'
                       r'attempts|returned|matched|next_offset)$')
# the tool's own words: their text is no evidence (a dict or list of records under them still is)
_TOOL_TEXT_KEYS = frozenset({'note', 'notes', 'notice', 'notices', 'message', 'messages', 'error', 'errors', 'hint',
                             'hints', 'detail', 'details', 'reason', 'reasons', 'estimated_reason', 'cite',
                             'untrusted_note', 'adjust_note', 'description', 'summary', 'label', 'title', 'body',
                             'condition', 'kind_label', 'warning', 'warnings', 'next_step', 'status_text', 'review_url'})
UNTRUSTED_KEY = 'untrusted_text'


def _name(key: Any) -> str:
    return str(key).strip().lower()


def _is_metadata(key: Any, value: Any) -> bool:
    """A field that is no evidence whatever its name says otherwise: a date, metadata, or the tool's own text."""
    name = _name(key)
    if name in _LABEL_KEYS and isinstance(value, dict):
        return False
    if name in _SKIP_KEYS or _DATE_KEY.search(name):
        return True
    if _META_KEY.search(name) and not isinstance(value, (dict, list, tuple)):
        return True
    if name == 'rows' and not isinstance(value, (dict, list, tuple)):
        return True                                       # a count of rows
    return name in _TOOL_TEXT_KEYS and (isinstance(value, str) or (
        isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value)))


def _parts(key: Any) -> Set[str]:
    return set(re.split(r'[^a-z0-9]+', _name(key))) - {''}


def field_class(key: Any) -> Optional[str]:
    """The evidence class a field's name gives its numbers, or ``None`` (inherit the enclosing one)."""
    parts = _parts(key)
    if parts & _PERCENT_PARTS:
        return PERCENT
    if parts & _FRACTION_PARTS:
        return FRACTION
    if parts & _QUANTITY_PARTS:
        return QUANTITY
    if parts & _MONEY_PARTS:
        return MONEY
    return None


def _indicator_class(key: Any, container: bool = False) -> Optional[str]:
    """The class of an indicator field (``_INDICATORS``), ``SKIP`` for another ``<name>_<period>`` field
    holding a number, else ``None``."""
    name = _name(key)
    for pattern, unit in _INDICATORS:
        if pattern.fullmatch(name):
            return unit
    return SKIP if not container and _INDICATOR_LIKE.fullmatch(name) else None


def _class(key: Any, inherited: str, declared: Mapping[str, str], value: Any = None) -> str:
    """A field's class: the producer's declared unit, else the indicator table, else its name, else inherited."""
    name = _name(key)
    if name in declared:
        return declared[name]
    return (_indicator_class(name, isinstance(value, (dict, list, tuple, set, frozenset)))
            or field_class(name) or inherited)


def _declared_unit(text: Any) -> str:
    """The class of a unit a producer declares in a ``units`` mapping (``'%（…）'``, ``'价格…'``, ``'股'``, ``'0-100'``)."""
    if not isinstance(text, str):
        return SKIP
    text = text.strip()
    if text.startswith(('%', '％')):
        return PERCENT
    if '价格' in text or re.match(r'[A-Z]{3}(?![A-Za-z])', text):
        return MONEY
    if text.startswith(('股', '份', '手')):
        return QUANTITY
    if re.match(r'(?:可超出\s*)?-?\d+\s*[-~～]\s*-?\d+', text):
        return INDEX
    return SKIP


def _declared(units: Any, inherited: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(units, dict):
        return inherited
    declared = dict(inherited)
    for names, text in units.items():
        for name in str(names).split('/'):
            if name.strip():
                declared[_name(name)] = _declared_unit(text)
    return declared


# a fact's own unit (``core/routines/briefs.py``): currency code, pct, points; anything else proves nothing
_FACT_UNITS = {'pct': PERCENT, '%': PERCENT, 'percent': PERCENT, 'points': INDEX}


def _fact_class(unit: Any) -> str:
    if isinstance(unit, str) and re.fullmatch(r'[A-Z]{3}', unit):
        return MONEY
    return _FACT_UNITS.get(unit, SKIP) if isinstance(unit, str) else SKIP


def _price_field(key: Any, unit: str) -> bool:
    parts = _parts(key)
    return (unit in (MONEY, ANY) and _indicator_class(key) is None
            and bool(parts & _PRICE_PARTS) and not parts & _NOT_PRICE_PARTS)


class Evidence:
    """Numbers by unit class (absolute values)."""

    def __init__(self):
        self.sets: Dict[str, Set[Decimal]] = {MONEY: set(), PERCENT: set(), FRACTION: set(), QUANTITY: set(),
                                              INDEX: set(), ANY: set()}
        self.prices: Set[Decimal] = set()        # the values of price fields, for a price claim of zero

    def price(self, value: Decimal) -> None:
        if value.is_finite():
            self.prices.add(abs(value))

    def add(self, unit: str, value: Decimal) -> None:
        if unit in self.sets and value.is_finite():
            self.sets[unit].add(abs(value))

    def difference(self, a: Decimal, b: Decimal) -> None:
        """The difference of two prices (money)."""
        self.add(MONEY, a - b)

    def change(self, earlier: Decimal, later: Decimal) -> None:
        """The change in percent from ``earlier`` to ``later``, relative to ``earlier``."""
        if earlier:
            self.add(PERCENT, (later - earlier) / earlier * 100)

    def matches(self, token: _Token) -> bool:
        unit_size = Decimal(1).scaleb(-token.decimals)
        candidates = ([(self.prices, Decimal(1))] if token.unit == 'zero_price'
                      else [(self.sets[unit], factor) for unit, factor in _ACCEPTS.get(token.unit, ())])
        for values, factor in candidates:
            for data in values:
                scaled = data * factor / token.mult
                if abs(scaled - token.value) >= unit_size * 2:
                    continue
                if (scaled.quantize(unit_size, ROUND_HALF_UP) == token.value
                        or scaled.quantize(unit_size, ROUND_DOWN) == token.value):
                    return True
        return False


_TEXT_CLASS = {'percent': PERCENT, 'money': MONEY, 'quantity': QUANTITY, 'lots': QUANTITY}


def _text(text: str, pool: Evidence, trusted: bool, user: bool = False) -> None:
    """The numbers of a free text, each of the unit written with it; see the module docstring.
    ``user``: the user's own message, whose price of zero grounds its restatement."""
    for token in _tokens(text):
        if user and token.unit == 'zero_price':
            pool.price(token.value)
            pool.add(MONEY, token.value)
            continue
        unit = _TEXT_CLASS.get(token.unit)
        if unit is None:
            if token.unit != 'bare' or not trusted:
                continue            # counts never; a bare number in third-party text neither
            unit = ANY
        pool.add(unit, token.value * token.mult)


def _number(value: Any) -> Optional[Decimal]:
    """``value`` as a number when it is one (a numeric string included), else ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    if isinstance(value, str) and _NUMERIC_TEXT.fullmatch(value):
        return _dec(value)
    return None


def _prices(record: Sequence[Tuple[Any, Any]], unit: str, declared: Mapping[str, str]) -> Dict[str, Decimal]:
    """{field name: number} of the price fields of a record."""
    found: Dict[str, Decimal] = {}
    for key, item in record:
        if _is_metadata(key, item) or not _price_field(key, _class(key, unit, declared, item)):
            continue
        number = _number(item)
        if number is not None:
            found[_name(key)] = number
    return found


def _derive_record(pool: Evidence, record: Sequence[Tuple[Any, Any]], unit: str = ANY,
                   declared: Mapping[str, str] = {}) -> None:
    """Differences of any two prices of one record; the change in percent of the ``_EARLIER`` -> ``_LATER`` pairs."""
    prices = _prices(record, unit, declared)
    values = list(prices.values())
    for i, first in enumerate(values):
        for second in values[i + 1:]:
            pool.difference(first, second)
    for earlier, base in prices.items():
        if earlier in _EARLIER:
            for later, current in prices.items():
                if later in _LATER:
                    pool.change(base, current)


def _row_date(row: Sequence[Tuple[Any, Any]]) -> Optional[str]:
    for key, item in row:
        if _DATE_KEY.search(_name(key)):
            return item if isinstance(item, str) and item.strip() else None
    return None


def _series(rows: Sequence[Sequence[Tuple[Any, Any]]]) -> Optional[List[Sequence[Tuple[Any, Any]]]]:
    """``rows`` in date order when they are one instrument over time (every row dated, distinct dates,
    at most one ticker or symbol), else ``None``."""
    instruments, dated = set(), []
    for row in rows:
        day = _row_date(row)
        if day is None:
            return None
        instruments.update(str(item) for key, item in row if _name(key) in ('ticker', 'symbol'))
        dated.append((day, row))
    days = [day for day, _ in dated]
    if len(rows) < 2 or len(instruments) > 1 or len(set(days)) != len(days):
        return None
    return [row for _, row in sorted(dated, key=lambda pair: pair[0])]


def _derive_series(pool: Evidence, rows: Sequence[Sequence[Tuple[Any, Any]]], unit: str = ANY,
                   declared: Mapping[str, str] = {}) -> None:
    """One instrument's dated series: the same price field in a row and the next (in date order),
    their difference and the change in percent relative to the earlier row."""
    ordered = _series(rows)
    if ordered is None:
        return
    previous: Dict[str, Decimal] = {}
    for row in ordered:
        current = _prices(row, unit, declared)
        for name, number in current.items():
            if name in previous:
                pool.difference(number, previous[name])
                pool.change(previous[name], number)
        previous = current


def _weak(key: Any, declared: Mapping[str, str], value: Any) -> bool:
    """Whether a field is a rule's or plan's distance (see the module docstring): a gap in the rule's own unit,
    so that its number grounds the same number with any unit word."""
    name = _name(key)
    return (name not in declared and not isinstance(value, (dict, list, tuple, set, frozenset))
            and 'distance' in _parts(name) and field_class(name) == MONEY)


def _price_value(pool: Evidence, key: Any, value: Any, unit: str) -> None:
    """A price field's number, for a price claim of zero (see the module docstring)."""
    number = _number(value) if _price_field(key, unit) else None
    if number is not None:
        pool.price(number)


def _walk(value: Any, pool: Evidence, unit: str = ANY, depth: int = 0, declared: Mapping[str, str] = {},
          weak: bool = False) -> None:
    if depth > 12 or value is None or isinstance(value, bool) or unit == SKIP:
        return
    number = _number(value)
    if number is not None:
        pool.add(unit, number)
        if weak:
            pool.add(ANY, number)        # a distance in the rule's unit: the number itself is the evidence
        return
    if isinstance(value, str):
        _text(value, pool, trusted=True)
        return
    if isinstance(value, dict):
        declared = _declared(value.get('units'), declared)
        if 'unit' in value and 'value' in value:          # a fact with its own unit: only the value, of that unit
            _walk(value['value'], pool, _fact_class(value['unit']), depth + 1, declared, weak=False)
            for key, item in value.items():
                if key not in ('value', 'unit', 'text', 'label') and key != UNTRUSTED_KEY and not _is_metadata(key, item):
                    _walk(item, pool, _class(key, unit, declared, item), depth + 1, declared,
                          _weak(key, declared, item))
            return
        columns = value.get('columns')
        rows = value.get('rows')
        table = (isinstance(columns, list) and isinstance(rows, list)
                 and all(isinstance(row, (list, tuple)) and len(row) == len(columns) for row in rows))
        for key, item in value.items():
            if key == UNTRUSTED_KEY:
                if isinstance(item, str):
                    _text(item, pool, trusted=False)
                continue
            if _is_metadata(key, item) or (table and key == 'rows'):
                continue
            _price_value(pool, key, item, _class(key, unit, declared, item))
            _walk(item, pool, _class(key, unit, declared, item), depth + 1, declared, _weak(key, declared, item))
        _derive_record(pool, list(value.items()), unit, declared)
        if table:
            for row in rows:
                cells = [(column, cell) for column, cell in zip(columns, row) if not _is_metadata(column, cell)]
                for column, cell in cells:
                    _price_value(pool, column, cell, _class(column, unit, declared, cell))
                    _walk(cell, pool, _class(column, unit, declared, cell), depth + 2, declared,
                          _weak(column, declared, cell))
                _derive_record(pool, cells, unit, declared)
            _derive_series(pool, [list(zip(columns, row)) for row in rows], unit, declared)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        for item in items:
            _walk(item, pool, unit, depth + 1, declared)
        records = [list(item.items()) for item in items if isinstance(item, dict)]
        if len(records) == len(items):
            _derive_series(pool, records, unit, declared)


def evidence(tool_results: Any = (), context_numbers: Iterable[Any] = (), user_text: str = '') -> Evidence:
    """Everything this turn's data proves, by unit class; see the module docstring."""
    pool = Evidence()
    _walk(list(tool_results) if isinstance(tool_results, (list, tuple)) else tool_results, pool)
    _walk(list(context_numbers) if not isinstance(context_numbers, (str, bytes)) else [context_numbers], pool)
    if user_text:
        _text(user_text, pool, trusted=True, user=True)
    return pool


def ungrounded_numbers(answer_text: str, tool_results: Sequence[Any] = (),
                       context_numbers: Iterable[Any] = (), *, user_text: str = '') -> List[str]:
    """The checked numbers of ``answer_text`` found in no result, context or user text.

    Returns them as written in the answer (``'12.34'``, ``'5.2%'``), first
    occurrence order, no duplicates. ``context_numbers`` takes numbers, value
    records (dicts, as ``LiveContext.evidence``) or text; ``user_text`` is what
    the user typed. An empty ``tool_results`` is valid: then only the context
    and the user's text count.
    """
    if not isinstance(answer_text, str) or not answer_text.strip():
        return []
    pool = evidence(tool_results, context_numbers, user_text)
    missing: List[str] = []
    for token in _tokens(answer_text):
        if token.checked and token.display not in missing and not pool.matches(token):
            missing.append(token.display)
    return missing


IMAGE_NOTE = ('本轮附有图片，程序读不到图片里的数字：上面的数字如果读自你上传的图片，请对照原图核对；'
              '不是读自图片的，请当作未经核实的数字。')


def footnote(ungrounded: Sequence[str], limit: int = 8, *, image: bool = False) -> str:
    """A short Chinese note listing the numbers to check; ``''`` when there are none.

    ``image``: the turn had an uploaded image; the note then adds ``IMAGE_NOTE``
    on its own line (see "Image turns" above).
    """
    if not ungrounded:
        return ''
    shown = '、'.join(str(item) for item in ungrounded[:limit])
    more = f' 等{len(ungrounded)}项' if len(ungrounded) > limit else ''
    note = f'以下数字未在本轮数据中找到，请核对：{shown}{more}'
    return f'{note}\n{IMAGE_NOTE}' if image else note
