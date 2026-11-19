"""Rows of a 交割单 / 对账单 → ledger events, with a status for every row.

Business types (the action column):

* 证券买入 / 担保品买入 / 港股通买入 → ``buy``; 证券卖出 / 担保品卖出 / 港股通卖出 → ``sell``.
  港股通 rows settled in CNY become ``fx`` (CNY→HKD) + an HKD ``buy``, or an HKD
  ``sell`` + ``fx`` (HKD→CNY). The CNY fee is converted at the settlement rate
  (from the file, or implied by the CNY amount) and shown on the row.
* 红利入账 / 股息入帐 → ``dividend`` (with ticker); 股息红利差异扣税 / 红利税补缴 → ``fee``.
* Currency: the 币种 column, a currency unit in a header (``发生金额(美元)``) and a
  symbol in a cell (``HK$100``) are all kept (``currency_sides``). A supported
  non-CNY currency is carried into the event; an unsupported (EUR ...), ambiguous
  (``$``) or conflicting one needs review. A cash row without any currency evidence is
  CNY only while nothing in the file names another currency (a mainland export); in a
  file that also shows HKD, USD or another currency it needs review.
* 银行转证券 → ``deposit``; 证券转银行 → ``withdrawal``.
* 利息归本 / 结息 → ``interest``.
* 逆回购 (codes 204xxx / 1318xx): the lending leg and the 购回 leg become one
  ``interest`` with the net cash, only when the pairing is unambiguous and the
  interest plausible (see ``_pair_repos``); any other leg needs review.
* 送股 / 红股入账 / 转增 → ``split``, ratio = (held + added) / held, computed when
  the rows are checked against the ledger (``reconcile.simulate``).
* 新股入帐 / 中签缴款 / 配股 / ETF 申赎 / 场外基金 / 融资融券 / 债转股 → needs review.
* 冲正 / 红冲 / 冲销 / 撤销 / 更正 (a correction of an earlier row) → needs review, never an
  ordinary trade; so does a buy with a negative quantity or incoming cash and a
  sell with outgoing cash.
* 申购配号 / 撤单 / 废单 / 指定交易 / 转托管 / 冻结 → skipped and listed.
* 北交所 and B 股 → unsupported and listed. Anything else → needs review with the raw text.

Guards. A row is bookable (``new``) only when every fact needed to book it is
positively established by the file: the listing, the direction, the quantity and
its unit, the price, the currency of every amount, the fee, the account identity,
and that it is not already in the ledger. Missing, blank, contradictory, ambiguous
or out-of-convention evidence makes the row 需处理 (or a blocker) with a reason that
names the field; it never falls back to a default (CNY, the upload's account, buy,
an absolute value, the first of two columns). ``GUARDS`` lists, for every path that
builds a ledger event, the check that establishes each of these facts (``CHECKS``),
or why it cannot apply (``n/a: ...``). ``tests/test_importer_guards.py`` runs a row
that violates each applicable cell through the real importer and fails when one is
booked, when a cell has no probe, or when a function builds events without being
listed here.

Direction (buy or sell, cash in or out) comes from the action column; two action
columns stating different business types are a column conflict (``direction``).
Row edits pass ``validate_edit`` when made and again when a stored session is read.

Numbers: the side comes from the action (the sign of the amount only when the
action column is empty). Fee = the fee columns by the overlap rules of
``fee_rule`` (佣金 vs 净佣金 vs 规费 vs 手续费), cross-checked with
``|发生金额| − 成交金额`` when both exist (columns within a cent of it: the fee is that
difference, so the cash is the stated one); else derived from them; else the row needs
review. A missing fee is never 0, and a reading the columns cannot settle without
a cash amount needs review.

Cash to the cent: the ledger books a trade's cash as quantity × price ± fee, computed in
Python's default decimal context (``LEDGER_ARITHMETIC``, 28 significant digits). The price
is 成交均价 when quantity × price is exactly 成交金额; otherwise (a rounded 成交均价, or
none) it is 成交金额 / 数量 with as few decimal places as make that product exactly 成交金额
in the ledger's arithmetic (``exact_price``: 9,605.00 / 300 is booked as a 27-place price,
never as 32.0166666667, which books 9,605.00000001). Money (fee, amounts) stays in the
file's cents.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context as DecimalContext, Decimal, localcontext
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from core.ledger import LedgerError, decimal_text, normalize, valid_date
from core.importers import ImportFailure, accounts, codes, dedupe
from core.importers.mapping import (CURRENCY_WORDS, OTHER_CURRENCIES, SUPPORTED_CURRENCIES, TEMPLATE_COLUMNS, Unit,
                                    apply_unit, column_units, header_matches, normalize_header)
from core.importers.values import (LIMITS, UnsettledPoint, bounded, cell_comma, cell_currency, clean, decimal, money,
                                   parse_date, parse_time, text)

STATUS_LABELS = {'new': '新', 'imported': '已导入', 'changed': '与已入账记录不同', 'possible_duplicate': '可能重复',
                 'review': '需处理', 'skipped': '跳过', 'unsupported': '不支持', 'merged': '已合并'}
BOOKABLE = ('new', 'possible_duplicate')

CATEGORY_LABELS = {
    'buy': '买入', 'sell': '卖出', 'dividend': '分红', 'tax': '红利税', 'deposit': '银行转证券',
    'withdrawal': '证券转银行', 'interest': '利息', 'fee': '费用', 'repo': '逆回购', 'split': '送转股',
    'ipo': '新股入账', 'ipo_payment': '中签缴款', 'rights': '配股', 'fund': '基金/ETF 申赎', 'margin': '融资融券',
    'conversion': '债转股', 'correction': '冲正 / 撤销', 'unknown': '未知业务', 'skip': '跳过', 'footer': '合计行',
    'template': '模板行', 'position': '持仓',
}
# A review row says what to fill in (``needs``: a row edit, "mapping" or "account"), or that it cannot be fixed on the
# import page, with this phrase and what to do instead (tests/test_importer_page_gaps.py).
NOT_HERE = '这一行无法在这里修改'
TEMPLATE_NOT_HERE = NOT_HERE + '（导入模板的行只能填写费用或设为跳过）。'
REVIEW_HINTS = {
    'ipo': '新股入账：请选择正式代码，按发行价记为买入并填写费用（通常为 0，需要手工填写）。'
           '如果中签缴款那一行已经记为买入，请把这一行设为跳过。',
    'ipo_payment': '中签缴款：资金已扣除，股份在“新股入账”那一行到账。建议在新股入账行记为买入，这一行设为跳过。',
    'rights': '配股：请核对配股价和数量，选择正式代码后记为买入。',
    'fund': 'ETF / 基金申购赎回：账本按买卖或基金合计记账，需要手工选择类型并核对金额。',
    'margin': '融资融券业务：请逐笔核对后手工处理（融资买入可记为买入并选择允许融资）。',
    'conversion': '债转股：请手工处理（卖出债券、买入股票）。',
}
EDIT_KINDS = ('buy', 'sell', 'dividend', 'fee', 'deposit', 'withdrawal', 'interest', 'skip')
# A row that corrects or reverses an earlier one. It is never turned into an ordinary trade or cash flow.
CORRECTION_WORDS = ('冲正', '红冲', '冲红', '冲销', '冲回', '撤销', '更正')
CORRECTION_HINT = ('这一行是冲正 / 撤销记录（“{action}”），用来更正之前的一笔，不能当作普通买卖或资金变动入账。'
                   '如果被更正的那一笔已经入账，请在账本里冲销它；如果它也在这个文件里，请把两行都设为跳过。')
CHINA_CURRENCIES = {'人民币': 'CNY', 'RMB': 'CNY', 'CNY': 'CNY', '人民币元': 'CNY', '港币': 'HKD', '港元': 'HKD',
                    'HKD': 'HKD', '美元': 'USD', 'USD': 'USD', '美金': 'USD'}
# Which side of a row a money column describes: the instrument's (price, turnover) or the settlement's.
TRADE_SIDE = ('price', 'turnover')
CASH_SIDE = ('cash_change', 'income', 'expense', 'cash_balance')
CONNECT_WORDS = ('港股通', '沪港通', '深港通')
CENT = Decimal('0.01')
MAX_QUANTITY = Decimal('1e12')
MAX_PRICE = Decimal('1e9')
UNCOMPUTABLE = '这一行的数字无法计算（数值过大、过小或小数位数过多）。请核对文件，或填写数量、价格和金额。'
HKD_CNY_BAND = (Decimal('0.70'), Decimal('0.98'))


@dataclass
class Row:
    row: int                                   # 1-based row number in the file / sheet
    cells: List[str]
    index: int = 0                             # position among data rows (file order)
    order: int = 0                             # chronological position
    status: str = 'new'
    category: str = 'unknown'
    action: str = ''
    date: Optional[str] = None
    time: Optional[str] = None
    code: str = ''
    name: str = ''
    ticker: Optional[str] = None
    currency: Optional[str] = None             # currency of the booked event
    cash_currency: str = 'CNY'                 # currency of the file's amount and balance columns
    quantity: Optional[Decimal] = None
    price: Optional[Decimal] = None
    turnover: Optional[Decimal] = None
    cash_change: Optional[Decimal] = None
    fee: Optional[Decimal] = None
    fee_source: Optional[str] = None           # columns | derived | edited
    price_source: Optional[str] = None         # columns | derived (成交金额 ÷ 数量) | edited
    cash_balance: Optional[Decimal] = None
    share_balance: Optional[Decimal] = None
    trade_id: str = ''
    serial: str = ''
    external_id: Optional[str] = None
    events: List[dict] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)   # why the row has its status
    notes: List[str] = field(default_factory=list)      # what was inferred
    choices: Dict[str, list] = field(default_factory=dict)
    needs: List[str] = field(default_factory=list)
    include: bool = True
    edits: dict = field(default_factory=dict)
    duplicate_of: Optional[dict] = None
    existing: Optional[dict] = None
    merged_into: Optional[int] = None
    pair_row: Optional[int] = None
    split_added: Optional[Decimal] = None
    account_ids: List[Tuple[str, str]] = field(default_factory=list)   # (dimension, value), see accounts
    other_account: bool = False                # refused by the account identity rules (accounts._reject)
    resolution: Optional[codes.Resolution] = None
    parse_errors: Dict[str, str] = field(default_factory=dict)
    edit_problems: Dict[str, str] = field(default_factory=dict)   # stored edits today's rules refuse
    raw: dict = field(default_factory=dict)
    # What rules version 2 derived differently for this row (a 10-place price, a fee from columns a cent away from the
    # cash): {'price': Decimal, 'fee': Decimal}; and the events it gave, for dedupe (an earlier booking is 已导入).
    earlier: dict = field(default_factory=dict)
    earlier_events: List[dict] = field(default_factory=list)

    def set(self, status: str, message: Optional[str] = None, *, needs=()):
        self.status = status
        if message:
            self.messages.append(message)
        for item in needs:
            if item not in self.needs:
                self.needs.append(item)
        if status not in BOOKABLE:
            self.include = False
            self.events = []

    def to_json(self) -> dict:
        return {
            'row': self.row, 'cells': self.cells, 'status': self.status, 'status_label': STATUS_LABELS[self.status],
            'include': self.include and self.status in BOOKABLE, 'can_include': self.status in BOOKABLE,
            'category': self.category, 'category_label': CATEGORY_LABELS.get(self.category, self.category),
            'action': self.action, 'date': self.date, 'time': self.time, 'code': self.code, 'name': self.name,
            'ticker': self.ticker, 'currency': self.currency, 'quantity': text(self.quantity), 'price': text(self.price),
            'turnover': text(self.turnover), 'cash_change': text(self.cash_change), 'fee': text(self.fee),
            'fee_source': self.fee_source, 'price_source': self.price_source, 'cash_balance': text(self.cash_balance),
            'share_balance': text(self.share_balance), 'external_id': self.external_id, 'events': self.events,
            'messages': self.messages, 'notes': self.notes, 'choices': self.choices, 'needs': self.needs,
            'edits': self.edits, 'duplicate_of': self.duplicate_of, 'existing': self.existing,
            'merged_into': self.merged_into, 'pair_row': self.pair_row, 'order': self.order,
            'account_identity': [{'dimension': d, 'label': accounts.LABELS[d], 'value': v} for d, v in self.account_ids],
        }


@dataclass
class Context:
    account: str
    source: str
    known_tickers: List[str] = field(default_factory=list)
    aliases: Dict[str, str] = field(default_factory=dict)
    short_code_market: Optional[str] = None
    numeric_columns: List[int] = field(default_factory=list)
    whitespace: bool = False
    account_choice: Optional[dict] = None     # accounts.choice / saved_choice, or None for the default
    accounts: Optional[dict] = None           # set by interpret: Session.accounts
    identities: Dict = field(default_factory=dict)   # the ledger account's identity memory (accounts.memory)
    comma: str = 'thousands'                  # how the file writes commas in numbers (values.comma_convention)
    currencies: set = field(default_factory=set)   # every currency the file's rows state (set by interpret)


# -- guards ------------------------------------------------------------------
# See "Guards" in the module docstring. ``builder`` is the function that calls the event constructor (``_event``,
# ``normalize`` or ``holdings.opening_event``); a check that cannot apply says why ("n/a: ...").
CHECKS = ('ticker', 'direction', 'quantity', 'price', 'currency', 'fee', 'account', 'duplicate', 'sign', 'bounds')
_ACCOUNT = 'accounts.decide (choice, account memory; no identity never passes a choice)'
_DUPLICATE = 'dedupe.compare (same id) / fuzzy (account, date, listing, kind, quantity or amount)'
GUARDS = {
    'trade': {
        'builder': 'transactions._trade',
        'ticker': 'codes.resolve (market column both ways), held_spelling',
        'direction': 'classify; column_conflicts(action): a blank action cannot hide one; _side_from_sign',
        'quantity': 'header units (手 only with a certain lot); column_conflicts compares in shares',
        'price': '_price: quantity × price = 成交金额, else review; never guessed',
        'currency': 'currency_sides (币种 column, header units, cell symbols); trade side = market',
        'fee': 'trade_fee (columns, cash cross-check, derived); number_problem: a buy or sell needs a fee',
        'account': _ACCOUNT, 'duplicate': _DUPLICATE,
        'sign': '_opposite_sign; price < 0; 成交金额 < 0 unless a buy whose cash goes out; fee < 0 needs cash',
        'bounds': 'MAX_QUANTITY, MAX_PRICE, number_problem'},
    'connect': {
        'builder': 'transactions._connect',
        'ticker': 'codes.resolve (in _trade)',
        'direction': 'classify; column_conflicts(action)',
        'quantity': 'header units: 手 of a Hong Kong share is never certain',
        'price': '_price (in _trade)',
        'currency': 'currency_sides (in _trade), CNY settlement only',
        'fee': 'fee_rule; no fee columns or an uncertain reading without a rate needs review',
        'account': _ACCOUNT, 'duplicate': _DUPLICATE,
        'sign': '_opposite_sign (in _trade); a stated rate must be positive (_rate); HKD_CNY_BAND',
        'bounds': 'MAX_PRICE (in _trade), number_problem'},
    'cash_flow': {
        'builder': 'transactions._cash_flow',
        'ticker': 'codes.resolve',
        'direction': 'classify; column_conflicts(action)',
        'quantity': 'n/a: a cash flow books no quantity',
        'price': 'n/a: a cash flow books no price',
        'currency': 'currency_sides, turnover side, A-share in CNY',
        'fee': 'any fee on the row needs review: the cash amount may or may not include it',
        'account': _ACCOUNT, 'duplicate': _DUPLICATE,
        'sign': 'amount sign vs business; 成交金额 must equal |发生金额|; a stated rate must be positive',
        'bounds': 'values.decimal, number_problem'},
    'repo': {
        'builder': 'transactions._book_repo',
        'ticker': 'n/a: an interest event has no ticker',
        'direction': 'repo_direction: 借出 / 融券回购 out, 购回 / 到期 in, must agree with the cash sign',
        'quantity': '_repo_window: both legs state the same quantity and principal, or they are not paired',
        'price': 'n/a: the interest is the net of the two cash amounts, not a price',
        'currency': '_repo_currency: CNY only',
        'fee': 'n/a: the net of the two cash amounts already includes the fees',
        'account': _ACCOUNT, 'duplicate': _DUPLICATE,
        'sign': 'lending < 0 < buy-back, net > 0; an amount edit is signed by the stated direction',
        'bounds': 'REPO_MAX_RATE, number_problem'},
    'split': {
        'builder': 'reconcile._split_event',
        'ticker': 'codes.resolve',
        'direction': 'classify; column_conflicts(action)',
        'quantity': 'the added quantity is stated (units as for trades); the ratio is exact or review',
        'price': 'n/a: a split books no price',
        'currency': 'n/a: a split moves no money (money on the row is the sign check)',
        'fee': 'a fee on a split row needs review (_build)',
        'account': _ACCOUNT, 'duplicate': 'dedupe.compare / fuzzy: the shares a booked split added',
        'sign': 'added quantity < 0; any cash, turnover or fee moving needs review',
        'bounds': 'number_problem (ratio)'},
    'template': {
        'builder': 'transactions.interpret_template',
        'ticker': '_template_instrument (check_ticker, held_spelling)',
        'direction': 'the kind column (normalize: a known kind)',
        'quantity': 'replay in reconcile.simulate: a trade without a quantity is refused',
        'price': 'replay in reconcile.simulate: a trade without a price is refused',
        'currency': '_template_currency (a stated currency; cell symbols agree), _template_instrument',
        'fee': 'number_problem: a buy or sell needs an explicit fee (the ledger would take 0)',
        'account': 'account column = this account', 'duplicate': _DUPLICATE,
        'sign': 'number_problem',
        'bounds': '_template_numbers (bounded), number_problem'},
    'opening': {
        'builder': 'session._next_batch',
        'ticker': 'codes.resolve, held_spelling',
        'direction': 'n/a: an opening states holdings, not a business with a direction',
        'quantity': 'holdings._position: a stated quantity, 手 only with a certain lot',
        'price': 'holdings._position: a stated cost (an HKD cost for Hong Kong)',
        'currency': 'holdings._stated',
        'fee': 'n/a: an opening has no fee',
        'account': _ACCOUNT,
        'duplicate': 'n/a: an opening is proposed only for an account without one (else a reconciliation table)',
        'sign': 'quantity / cost < 0',
        'bounds': 'LIMITS'},
}
# The one place every broker event passes through; not a path of its own.
EVENT_CONSTRUCTOR = 'transactions._event'


# -- row edits ---------------------------------------------------------------
# One validator for a PATCH and for the edits a stored session already holds (``checked_edits``), so an
# edit saved under older, looser rules makes its row 需处理 instead of failing the whole session.

# Which edits each kind of statement uses; any other is refused rather than silently ignored.
EDIT_FIELDS = {'holdings': ('include', 'kind', 'ticker', 'quantity', 'price'), 'template': ('include', 'kind', 'fee')}
_EDIT_NAMES = {'quantity': '数量', 'price': '价格', 'amount': '金额', 'fx_rate': '汇率', 'fee': '费用'}
EDIT_LABELS = {**_EDIT_NAMES, 'include': '是否入账', 'kind': '类型', 'ticker': '代码', 'currency': '币种', 'date': '日期',
               'allow_margin': '允许融资'}


def _positive(value, field_name, row, *, allow_zero=False) -> str:
    what = _EDIT_NAMES[field_name]
    if not isinstance(value, str):
        raise ImportFailure(f'第 {row} 行：{what}必须是数字字符串，例如 "5.00"。')
    try:
        number = decimal(value)
    except ValueError as exc:
        raise ImportFailure(f'第 {row} 行：{what} {exc}') from None
    if number is None or number < 0 or (number == 0 and not allow_zero):
        raise ImportFailure(f'第 {row} 行：{what}必须{"不小于" if allow_zero else "大于"} 0。')
    if number > LIMITS[field_name]:
        raise ImportFailure(f'第 {row} 行：{what}不能超过 {text(LIMITS[field_name])}。')
    return text(number)


def validate_edit(row: int, raw: dict, current: dict, statement: str = 'transactions') -> dict:
    """The row's edits after applying ``raw`` (a PATCH ``rows`` item) to ``current``; ImportFailure when invalid."""
    if not isinstance(raw, dict):
        raise ImportFailure(f'第 {row} 行：修改必须是一个对象。')
    if raw.get('reset'):
        current = {}
    edit = dict(current)
    allowed = EDIT_FIELDS.get(statement)
    for key, value in raw.items():
        if key in ('row', 'reset'):
            continue
        if value is None:
            edit.pop(key, None)
            continue
        if allowed is not None and key not in allowed:
            what = '持仓文件' if statement == 'holdings' else '导入模板'
            raise ImportFailure(f'第 {row} 行：{what}的行不能修改{EDIT_LABELS.get(key, "这一项")}'
                                f'（只能修改{"、".join(EDIT_LABELS[k] for k in allowed)}）。')
        if key in ('include', 'allow_margin'):
            if not isinstance(value, bool):
                raise ImportFailure(f'第 {row} 行：{EDIT_LABELS[key]}只能选择是或否。')
            edit[key] = value
        elif key == 'ticker':
            try:
                edit[key] = codes.check_ticker(value)
            except LedgerError as exc:
                raise ImportFailure(f'第 {row} 行：代码无效（{exc}）。') from None
        elif key == 'kind':
            kinds = EDIT_KINDS if allowed is None else ('skip',)
            if value not in kinds:
                raise ImportFailure(f'第 {row} 行：类型只能是{"、".join(CATEGORY_LABELS[k] for k in kinds)}。')
            edit[key] = value
        elif key in ('quantity', 'price', 'amount', 'fx_rate', 'fee'):
            edit[key] = _positive(value, key, row, allow_zero=key == 'fee')
        elif key == 'currency':
            if value not in SUPPORTED_CURRENCIES:
                raise ImportFailure(f'第 {row} 行：币种只能是 CNY、HKD 或 USD。')
            edit[key] = value
        elif key == 'date':
            try:
                edit[key] = valid_date(value)
            except LedgerError:
                raise ImportFailure(f'第 {row} 行：日期的格式应为 2026-11-12。') from None
        else:
            raise ImportFailure(f'第 {row} 行：不能修改这一项（{str(key)[:40]}）。')
    return edit


def checked_edits(row: int, stored, statement: str) -> Tuple[dict, Dict[str, str]]:
    """(the stored edits of one row that today's rules accept, {field: why the others are refused})."""
    if not isinstance(stored, dict):
        return {}, {'edits': f'第 {row} 行：保存的修改格式不对'}
    valid, problems = {}, {}
    for key, value in stored.items():
        try:
            valid.update(validate_edit(row, {key: value}, {}, statement))
        except ImportFailure as exc:
            problems[key] = str(exc)
    return valid, problems


def refuse_stored_edits(row: 'Row') -> bool:
    """Make a row whose stored edits were refused 需处理 (its own values are not used instead); True if so."""
    if not row.edit_problems:
        return False
    row.set('review', '之前保存的修改无法使用：' + '；'.join(row.edit_problems.values()) +
            '。请重新填写这几项，或撤销这一行的修改。', needs=list(row.edit_problems))
    return True


# -- reading cells -----------------------------------------------------------

_DECIMAL_FIELDS = ('quantity', 'price', 'turnover', 'cash_change', 'income', 'expense', 'cash_balance',
                   'share_balance', 'fx_rate')
QUANTITY_FIELDS = ('quantity', 'share_balance')
# How exact a value converted from a header unit (万元, 万股) must stay.
RESOLUTION = {'quantity': Decimal(1), 'share_balance': Decimal(1), 'price': Decimal('0.001'), 'fx_rate': Decimal('1e-6')}


def _unit_value(number, name: str, index: Optional[int], header: List[str], units: Dict[int, Unit], lots: set):
    """Apply the column's header unit to ``number``; a lot (手) is only recorded in ``lots``."""
    unit = units.get(index) if index is not None else None
    if unit is None:
        return number
    kind = 'quantity' if name in QUANTITY_FIELDS else 'rate' if name == 'fx_rate' else 'money'
    text_header = header[index] if index < len(header) else ''
    if unit.lot:
        if kind != 'quantity' or unit.scale != 1:
            raise ValueError(f'表头“{text_header}”的单位“{unit.text}”无法用于这一列')
        lots.add(name)
        return number
    return apply_unit(number, unit, kind, text_header, RESOLUTION.get(name, CENT))


def _cell(cells, mapping, field_name) -> str:
    index = mapping.get(field_name)
    if index is None or index >= len(cells):
        return ''
    return clean(cells[index])


def read(row: Row, header: List[str], mapping: dict, units: Optional[Dict[int, Unit]] = None,
         comma: str = 'thousands', native=frozenset()) -> dict:
    """Parse the mapped cells; unreadable cells are recorded in ``row.parse_errors``. ``native``: the columns of
    this row that held native workbook numbers (read exactly, whatever ``comma`` says of the text cells).

    ``values['evidence']`` keeps every currency the row states: the 币种 column, units
    in the headers (``发生金额(美元)``) and symbols in the cells (``HK$100``), per side."""
    cells = row.cells
    units = units if units is not None else column_units(header, mapping)
    values = {}
    for name in ('action', 'code', 'name', 'market', 'shareholder', 'fund_account', 'currency', 'trade_id', 'serial',
                 'contract'):
        values[name] = _cell(cells, mapping, name)
    row.account_ids = accounts.identity(values['fund_account'], values['shareholder'])
    for name, parser in (('date', parse_date), ('time', parse_time)):
        try:
            values[name] = parser(_cell(cells, mapping, name))
        except ValueError as exc:
            values[name] = None
            row.parse_errors[name] = str(exc)
    if values.get('time') is None and 'time' not in row.parse_errors and mapping.get('date') is not None:
        raw_date = _cell(cells, mapping, 'date')
        if ' ' in raw_date or 'T' in raw_date:
            try:
                values['time'] = parse_time(raw_date.replace('T', ' ').split(' ', 1)[1])
            except (ValueError, IndexError):
                pass
    lots: set = set()
    unsettled = []
    for name in _DECIMAL_FIELDS:
        try:
            values[name] = _unit_value(decimal(_cell(cells, mapping, name), cell_comma(comma, native, mapping.get(name))),
                                       name, mapping.get(name), header, units, lots)
        except ValueError as exc:
            values[name] = None
            row.parse_errors[name] = str(exc)
            if isinstance(exc, UnsettledPoint) and name in _SETTLED_BY_TURNOVER:
                unsettled.append(name)
    if unsettled:
        lots |= _settle_points(row, values, unsettled, lambda name, found: _unit_value(
            decimal(_cell(cells, mapping, name), cell_comma(comma, native, mapping.get(name)), grouping_point=True),
            name, mapping.get(name), header, units, found))
    values['lots'] = lots
    if values['cash_change'] is None and (values['income'] is not None or values['expense'] is not None):
        values['cash_change'] = (values['income'] or Decimal(0)) - abs(values['expense'] or Decimal(0))
    fees = []
    for index in mapping.get('fees', []):
        if index >= len(cells) or not clean(cells[index]):
            continue
        try:
            number = _unit_value(decimal(cells[index], cell_comma(comma, native, index)), 'fees', index, header, units,
                                 set())
        except ValueError as exc:
            row.parse_errors['fees'] = f'{header[index] if index < len(header) else "费用"}：{exc}'
            continue
        if number is not None:
            fees.append((normalize_header(header[index]) if index < len(header) else '', abs(number)))
            if number < 0:
                values['negative_fee'] = f'{header[index] if index < len(header) else "费用"} {text(number)}'
    values['fees'] = fees
    values.setdefault('negative_fee', None)
    values['fee_columns'] = bool(mapping.get('fees'))
    values['evidence'] = _evidence(cells, header, mapping, units, values['currency'])
    row.raw = {header[i] if i < len(header) else str(i): cells[i] for i in range(len(cells))}
    return values


_SETTLED_BY_TURNOVER = ('quantity', 'price', 'turnover')


def _settle_points(row: Row, values: dict, unsettled: List[str], grouped) -> set:
    """A lone point of a decimal-comma file (``values.UnsettledPoint``) in the quantity, price or 成交金额 is read as
    the file's grouping (1.000 = 1000) when, read so, quantity × price is 成交金额 to the cent: the row's own
    arithmetic settles it. Otherwise the cell stays unreadable. ``grouped(name, lots)``: the grouped reading.
    Returns the fields so read in 手 (``lots``)."""
    found, lots = {}, set()
    try:
        for name in unsettled:
            found[name] = grouped(name, lots)
    except ValueError:
        return set()
    quantity, price, turnover = (found.get(name, values[name]) for name in _SETTLED_BY_TURNOVER)
    if quantity is None or price is None or turnover is None or \
            abs(abs(quantity) * abs(price) - abs(turnover)) >= CENT / 2:
        return set()
    for name, value in found.items():
        values[name] = value
        row.parse_errors.pop(name, None)
    return lots


def _evidence(cells, header, mapping: dict, units: Dict[int, Unit], column_text: str) -> Dict[str, Dict[str, str]]:
    """{'cash': {currency: where it is stated}, 'trade': {...}}; also 'fee' (what the fee cells and headers state)
    and 'settlement' (the cash side without them: a 港股通 fee may be stated in HKD beside a CNY settlement)."""
    evidence: Dict[str, Dict[str, str]] = {'cash': {}, 'trade': {}, 'fee': {}}
    stated = _currency(column_text)
    if stated:
        evidence['cash'].setdefault(stated, f'币种列“{column_text[:20]}”')
    sides = [(name, 'trade' if name in TRADE_SIDE else 'cash') for name in TRADE_SIDE + CASH_SIDE]
    indices = [(mapping.get(name), side) for name, side in sides] + [(i, 'fee') for i in mapping.get('fees') or []]
    for index, side in indices:
        if index is None:
            continue
        unit = units.get(index)
        if unit is not None and unit.currency:
            evidence[side].setdefault(unit.currency, f'表头“{header[index]}”')
        if index < len(cells):
            symbol = cell_currency(cells[index])
            if symbol:
                evidence[side].setdefault(symbol, f'金额“{clean(cells[index])[:30]}”')
    evidence['settlement'] = dict(evidence['cash'])
    for code, where in evidence['fee'].items():
        evidence['cash'].setdefault(code, where)
    return evidence


def currency_sides(row: Row, v: dict, fees_apart: bool = False) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(settlement currency, instrument-side currency, problem) from the row's evidence.

    A currency the person chose (``edits.currency``) decides the settlement side. A bare ``$`` is the dollar the
    same side names (币种 USD and ``-$191``), and on the instrument side also the listing's own dollar (``$190`` of
    NASDAQ:AAPL is USD, of a HKG listing HKD). Unsupported, ambiguous ($ with nothing that says which) or conflicting
    evidence is a problem: the row needs review."""
    evidence = v.get('evidence') or {'cash': {}, 'trade': {}}
    out, problems = [], []
    market = codes.MARKETS.get(row.ticker.split(':')[0]) if row.ticker else None
    for side in ('cash', 'trade'):
        found = dict(evidence.get('settlement', evidence['cash']) if side == 'cash' and fees_apart else evidence[side])
        if side == 'cash' and row.edits.get('currency'):
            out.append(row.edits['currency'])
            continue
        if '$' in found:
            others = {code for code in found if code != '$'}
            if others & {'USD', 'HKD'} and len(others) == 1:
                found.pop('$')
            elif not others and side == 'trade' and market in ('USD', 'HKD'):
                found = {market: found['$']}   # the listing's price is in its own market's dollar
            elif not others:
                problems.append(f'{found["$"]}带“$”符号，无法确定是美元还是港币')
                out.append(None)
                continue
        unsupported = [code for code in found if code not in SUPPORTED_CURRENCIES and code != '$']
        if unsupported:
            problems.append('；'.join(f'{found[code]}的币种 {code} 不受支持（账本只支持人民币、港币和美元）'
                                     for code in unsupported))
            out.append(None)
            continue
        if len(found) > 1:
            problems.append('币种不一致：' + '，'.join(f'{where}是 {code}' for code, where in found.items()))
            out.append(None)
            continue
        out.append(next(iter(found), None))
    return out[0], out[1], '；'.join(problems) + '。请核对币种。' if problems else None


# -- classification ----------------------------------------------------------

def _norm(action: str) -> str:
    return unicodedata.normalize('NFKC', action).replace(' ', '').upper()


def classify(action: str, resolution: Optional[codes.Resolution], cash: Optional[Decimal]) -> Tuple[str, str]:
    """(category, label for skipped rows)."""
    a = _norm(action)
    if not a:
        return ('repo', '') if resolution is not None and resolution.status == 'repo' else ('blank', '')
    if '指定' in a or a == '撤指':
        return 'skip', '指定交易'
    if any(word in a for word in CORRECTION_WORDS):
        return 'correction', ''
    if any(word in a for word in ('撤单', '废单', '已撤', '部撤')):
        return 'skip', '撤单 / 废单'
    if '配号' in a:
        return 'skip', '申购配号'
    if '托管' in a:
        return 'skip', '转托管'
    if '冻结' in a or '解冻' in a:
        return 'skip', '资金冻结 / 解冻'
    if '中签通知' in a:
        return 'skip', '中签通知'
    if resolution is not None and resolution.status == 'repo' or any(w in a for w in ('逆回购', '回购', '购回', '拆出')):
        return 'repo', ''
    if any(word in a for word in ('融资', '融券', '还款', '还券')):
        return 'margin', ''
    if '新股' in a and ('入' in a or '到账' in a or '上市' in a):
        return 'ipo', ''
    if '中签' in a or ('缴款' in a and '配股' not in a):
        return 'ipo_payment', ''
    if '配股' in a or '配售' in a:
        return 'rights', ''
    if any(word in a for word in ('申购', '赎回', '认购', '场外')):
        if '申购' in a and 'ETF' not in a and '基金' not in a and not cash:
            return 'skip', '新股申购（未扣款）'
        return 'fund', ''
    if '债转股' in a or ('转股' in a and '转增' not in a):
        return 'conversion', ''
    if '税' in a:
        return 'tax', ''
    if any(word in a for word in ('送股', '红股', '转增', '送转', '股票股利')):
        return 'split', ''
    if any(word in a for word in ('红利', '股息', '派息', '分红')):
        return 'dividend', ''
    if any(word in a for word in ('银行转证券', '银证转入', '银行转存', '资金转入', '转入资金', '银行转入', '存入')):
        return 'deposit', ''
    if any(word in a for word in ('证券转银行', '银证转出', '银行转取', '资金转出', '转出资金', '转出到银行', '取出')):
        return 'withdrawal', ''
    if '利息' in a or '结息' in a:
        return 'interest', ''
    if '基金' in a:
        return 'fund', ''
    if '买' in a or a == 'B':
        return 'buy', ''
    if '卖' in a or a == 'S':
        return 'sell', ''
    return 'unknown', ''


def direction(action: str) -> Optional[str]:
    """The business type an action cell states (``证券买入`` and ``买入`` → buy), None when it states none.
    Used to compare two action columns (``mapping.column_conflicts``)."""
    category, _label = classify(clean(action), None, None)
    return None if category in ('blank', 'unknown') else category


# -- fees ----------------------------------------------------------------------

REGULATORY_TOTAL = ('交易规费', '规费')
REGULATORY_PARTS = ('经手费', '证管费')
_SPECIAL_FEES = {'佣金', '净佣金', '手续费', *REGULATORY_TOTAL, *REGULATORY_PARTS}


def fee_rule(parts: List[Tuple[str, Decimal]]) -> Tuple[List[Tuple[Decimal, str]], Optional[str]]:
    """``(candidates, problem)`` for the fee columns of one row.

    ``candidates`` are the possible fee totals, the rule's own reading first,
    each with a description. ``problem`` is set when the columns alone cannot
    tell which reading is right; then only the cash amount (|发生金额| − 成交金额)
    can choose one, otherwise the row needs review. Overlap rules (column names
    after ``normalize_header``; 规费 below is 交易规费 / 规费, or 经手费 + 证管费)::

        columns present                     counted                              certain?
        ----------------------------------  -----------------------------------  -----------------------
        交易规费 and 经手费 / 证管费         规费 once (= 经手费 + 证管费)          only if the two agree
        佣金 + 净佣金                        佣金 (gross; includes 佣金 − 净佣金)  yes
        佣金 + 净佣金 + 规费                 佣金 when 佣金 − 净佣金 = 规费;        yes; otherwise no
                                            佣金 + 规费 when 佣金 = 净佣金
        佣金 + 规费 (no 净佣金)              佣金 + 规费, or 佣金 alone             no: 佣金 may include it
        净佣金 + 规费 (no 佣金)              净佣金 + 规费                          yes
        净佣金 alone                         净佣金                                no: 规费 is missing
        手续费 + 佣金 / 净佣金               all, or 手续费 alone, or without it    no: one may total the other
        手续费 + others                      all; 手续费 alone only via cash       yes
        the same fee column twice           both, or one                          no
        every other column (印花税, 过户费,  added                                 yes
        其他费, 交易费, 结算费 ...)
    """
    if not parts:
        return [], None
    names = [name for name, _value in parts]
    problems: List[str] = []
    duplicates = sorted({name for name in names if names.count(name) > 1})
    sets = [parts]
    if duplicates:
        problems.append(f'费用列“{"、".join(duplicates)}”出现了不止一次，可能重复计算')
        first = {}
        for name, value in parts:
            first.setdefault(name, value)
        sets.append(list(first.items()))
    candidates: List[Tuple[Decimal, str]] = []
    certain = not problems
    for index, chosen in enumerate(sets):
        readings, sure = _fee_readings(chosen, problems if index == 0 else [])
        certain = certain and sure
        for total, description in readings:
            candidates.append((total, '费用列合计' if not candidates else description))
    unique, seen = [], set()
    for total, description in candidates:
        if total not in seen:
            seen.add(total)
            unique.append((total, description))
    return unique, None if certain else '；'.join(dict.fromkeys(problems))


def _fee_readings(parts, problems: List[str]) -> Tuple[List[Tuple[Decimal, str]], bool]:
    def total(wanted):
        return sum((value for name, value in parts if name in wanted), Decimal(0))

    def present(*wanted):
        return any(name in wanted for name, _value in parts)
    names = {name for name, _value in parts}
    others = total(names - _SPECIAL_FEES)
    gross = total({'佣金'}) if present('佣金') else None
    net = total({'净佣金'}) if present('净佣金') else None
    handling = total({'手续费'}) if present('手续费') else None
    reg_total = total(set(REGULATORY_TOTAL)) if present(*REGULATORY_TOTAL) else None
    reg_parts = total(set(REGULATORY_PARTS)) if present(*REGULATORY_PARTS) else None
    certain = True
    if reg_total is not None and reg_parts is not None and reg_total != reg_parts:
        problems.append(f'交易规费 {money(reg_total)} 不等于经手费和证管费之和 {money(reg_parts)}')
        regulatory = [reg_parts, reg_total, reg_parts + reg_total]
        certain = False
    else:
        regulatory = [reg_parts if reg_parts is not None else reg_total]
    commission: List[Tuple[Decimal, str]] = []
    for reg in regulatory:
        if gross is not None and net is not None:
            included = gross - net
            if reg is None and included >= 0 or reg is not None and included == reg:
                commission.append((gross, '佣金（已含规费）'))
            elif reg is not None and included == 0:
                commission.append((gross + reg, '佣金 + 规费'))
            else:
                problems.append(f'佣金 {money(gross)}、净佣金 {money(net)}' + (f'和规费 {money(reg)}' if reg is not None else '')
                                + ' 对不上')
                certain = False
                commission.append((gross, '佣金（已含规费）'))
                if reg is not None:
                    commission += [(gross + reg, '佣金 + 规费'), (net + reg, '净佣金 + 规费')]
        elif gross is not None:
            if reg is None:
                commission.append((gross, '佣金'))
            else:
                problems.append('文件同时有“佣金”和规费列，但没有“净佣金”：无法判断佣金是否已含规费')
                certain = False
                commission += [(gross + reg, '佣金 + 规费'), (gross, '佣金（已含规费）')]
        elif net is not None:
            if reg is None:
                problems.append('只有“净佣金”（不含规费），文件里没有规费列')
                certain = False
            commission.append((net + (reg or 0), '净佣金 + 规费'))
        else:
            commission.append((reg or Decimal(0), '规费'))
    readings: List[Tuple[Decimal, str]] = []
    if handling is not None and (gross is not None or net is not None):
        problems.append('“手续费”和“佣金”同时出现，可能是同一笔费用的合计与明细')
        certain = False
        readings += [(handling + value + others, description) for value, description in commission]
        readings.append((handling, '手续费列（已含其他费用）'))
        readings += [(value + others, description + '，不计手续费') for value, description in commission]
    elif handling is not None:
        readings += [(handling + value + others, description) for value, description in commission]
        if len(parts) > 1:
            readings.append((handling, '手续费列（已含其他费用）'))
    else:
        readings += [(value + others, description) for value, description in commission]
    return readings, certain


def fee_candidates(parts: List[Tuple[str, Decimal]]) -> List[Tuple[Decimal, str]]:
    """Possible fee totals, the rule-based one first (see ``fee_rule``)."""
    return fee_rule(parts)[0]


def trade_fee(fees, has_fee_columns, cash, turnover, side, negative: Optional[str] = None,
              earlier: Optional[dict] = None) -> Tuple[Optional[Decimal], Optional[str], Optional[str], Optional[str]]:
    """(fee, source, note, problem). ``problem`` set means the row needs review. ``earlier`` receives the fee rules
    version 2 gave when it differs (the columns' total a cent away from the cash).

    ``negative``: a fee cell was negative. Its absolute value is used only when the cash amount confirms the
    total (some exports sign fees as money out); otherwise it could be a refund, and the row needs review."""
    derived = None
    if cash is not None and turnover is not None:
        derived = abs(cash) - turnover if side == 'buy' else turnover - abs(cash)
    candidates, uncertain = fee_rule(fees)
    if candidates and negative and derived is None:
        return None, None, None, (f'费用列是负数（{negative}），无法判断是费用还是返还，文件里也没有发生金额可以核对。'
                                  '请填写这一笔的费用。')
    if candidates:
        if derived is not None:
            for total, description in candidates:
                if total == derived:
                    note = None if description == '费用列合计' else f'费用取{description}，与发生金额一致'
                    return total, 'columns', note, None
            for total, description in candidates:
                if abs(total - derived) <= CENT and derived >= 0:
                    # The cash is the broker's: the fee is what it leaves, and the columns only confirm it.
                    if earlier is not None:
                        earlier['fee'] = total
                    return derived, 'derived', (f'费用列（{description}）是 {money(total)}，按 |发生金额| − 成交金额 是 '
                                                f'{text(derived)}，相差不到 1 分：费用记 {text(derived)}，使现金与券商一致'), None
            return None, None, None, (f'费用列合计 {money(candidates[0][0])} 与按发生金额推算的 {money(derived)} 不一致。'
                                      '请核对费用列，或直接填写这一笔的费用。')
        if uncertain:
            return None, None, None, (f'{uncertain}；文件里没有发生金额可以核对。'
                                      '请填写这一笔的费用，或在列对应里只保留不重复的费用列。')
        return candidates[0][0], 'columns', None, None
    if derived is not None:
        if derived < 0:
            return None, None, None, f'按发生金额推算的费用是负数（{money(derived)}），请核对金额列或填写费用。'
        if derived > max(turnover * Decimal('0.01'), Decimal(50)):
            return None, None, None, f'按发生金额推算的费用 {money(derived)} 过高，可能对错了金额列。请核对或填写费用。'
        return derived, 'derived', '费用按 |发生金额| − 成交金额 推算', None
    if has_fee_columns:
        return None, None, None, '这一行的费用列都是空的，也无法用发生金额推算。请填写费用（确实没有费用请填 0）。'
    return None, None, None, '文件里没有费用列，也无法用发生金额和成交金额推算费用。请填写费用（确实没有费用请填 0）。'


# ``core.ledger.replay`` computes quantity × price ± fee in Python's default decimal context.
LEDGER_ARITHMETIC = DecimalContext(prec=28, rounding=ROUND_HALF_EVEN)
MAX_PRICE_PLACES = 60


def ledger_product(quantity: Decimal, price: Decimal) -> Decimal:
    """quantity × price as the ledger computes a trade's cash."""
    with localcontext(LEDGER_ARITHMETIC):
        return quantity * price


def exact_price(turnover: Decimal, quantity: Decimal) -> Tuple[Optional[Decimal], int]:
    """(price, decimal places): the price with the fewest places whose ledger product with ``quantity`` is exactly
    ``turnover``; (None, 0) when none has at most ``MAX_PRICE_PLACES`` places."""
    with localcontext() as context:
        context.prec = 120
        exact = turnover / quantity
        for places in range(MAX_PRICE_PLACES + 1):
            price = exact.quantize(Decimal(1).scaleb(-places))
            if price > 0 and ledger_product(quantity, price) == turnover:
                return price, places
    return None, 0


def _derived_price(row: Row, quantity: Decimal, turnover: Decimal, stated: Optional[Decimal]) -> Optional[Decimal]:
    price, places = exact_price(turnover, quantity)
    if price is None:
        row.set('review', f'成交金额 {money(turnover)} ÷ 数量 {text(quantity)} 除不尽，账本无法用一个价格精确记出这笔成交金额。'
                          '请填写价格（现金将按 数量 × 价格 记）。', needs=['price'])
        return None
    with localcontext() as context:   # what rules version 2 booked: the quotient rounded to 10 places
        context.prec = 60
        earlier = (turnover / quantity).quantize(Decimal('1e-10'))
    if earlier != price:
        row.earlier['price'] = earlier
    rounded = f'文件的成交价格 {text(stated)} 是四舍五入后的价格，' if stated is not None else '文件没有成交价格，'
    if places <= 10:
        row.notes.append(rounded + f'成交均价按 成交金额 ÷ 数量 = {text(price)} 记，使现金与券商一致')
    else:
        row.notes.append(rounded + f'成交金额 ÷ 数量 除不尽：成交均价记为 {places} 位小数，使 数量 × 价格 恰好等于成交金额 '
                                   f'{money(turnover)}，现金与券商一致到分')
    return price


def _price(row: Row, quantity: Decimal, price: Optional[Decimal], turnover: Optional[Decimal]) -> Optional[Decimal]:
    """The booked price (see "Cash to the cent"); sets a review status and returns None when it cannot be exact."""
    if price is not None and price <= 0:
        price = None
    if turnover is not None and turnover <= 0:
        turnover = None
    if price is not None and turnover is not None:
        exponent = price.as_tuple().exponent
        half_unit = Decimal(5).scaleb(min(exponent if isinstance(exponent, int) else 0, -2) - 1)
        tolerance = quantity * half_unit + CENT
        if abs(quantity * price - turnover) > tolerance:
            row.set('review', f'数量 × 价格 = {money(quantity * price)}，与成交金额 {money(turnover)} 不符'
                              '（可能是“手”和“股/张”的单位不同，或对错了列）。请核对。', needs=['quantity', 'price'])
            return None
        if ledger_product(quantity, price) == turnover:
            return price
        return _derived_price(row, quantity, turnover, price)
    if price is not None:
        return price
    if turnover is not None:
        return _derived_price(row, quantity, turnover, None)
    row.set('review', '缺少成交价格和成交金额。', needs=['price'])
    return None


# -- building events -----------------------------------------------------------

def _currency_word(word: str) -> Optional[str]:
    if word in CHINA_CURRENCIES:
        return CHINA_CURRENCIES[word]
    lower = word.lower()
    if lower in CURRENCY_WORDS and CURRENCY_WORDS[lower] != '$':
        return CURRENCY_WORDS[lower]
    return OTHER_CURRENCIES.get(lower)


def _currency(text_value: str) -> Optional[str]:
    """CNY / HKD / USD for a 币种 cell, another code (or the text) for an unsupported one, None when empty.

    ``人民币(RMB)`` or ``港币 HKD`` name one currency twice; a cell naming two different
    currencies, or one that is not known, is returned as its text (unsupported)."""
    if not text_value or not clean(text_value):
        return None
    word = codes.bare(text_value)
    found = _currency_word(word)
    if found:
        return found
    parts = [part for part in re.split(r'[()（）\[\]【】/、,，;；|]+|\s+', unicodedata.normalize('NFKC', clean(text_value)))
             if part]
    named = {_currency_word(codes.bare(part)) for part in parts}
    if len(named) == 1 and None not in named:
        return named.pop()
    return word[:20]


EVENT_NUMBERS = {'quantity': '数量', 'price': '价格', 'fee': '费用', 'amount': '金额', 'received_amount': '到账金额',
                 'fx_rate': '汇率', 'ratio': '比例'}
# The row edit that sets an event number (a split's ratio comes from its 到账数量; a 换汇 amount from the trade).
NUMBER_NEEDS = {'received_amount': 'amount', 'ratio': 'quantity'}
# ``core.ledger``'s refusals in the page's words: a row message never shows the ledger's English.
LEDGER_REASONS = (
    ('excessive decimal precision', '数字的小数位数超出了账本能记录的范围'),
    ('within supported bounds', '数字超出了账本能记录的范围'),
    ('An explicit numeric amount is required', '缺少数字'),
    ('Invalid numeric amount', '数字无法读取'),
    ('invalid sign', '数字的正负号不对'),
    ('Date must be', '日期无效'),
    ('Invalid calendar date', '日期无效'),
    ('Currency must be', '币种只能是人民币、港币或美元'),
    ('exchange-qualified ticker', '证券代码无效'),
    ('maximum 240 characters', '有一项文字为空或超过 240 个字'),
    ('Unsupported event kind', '账本不支持这种记录'),
    ('Event is too large', '这条记录太长'),
    ("does not match the ticker's market", '成交币种与证券所在的市场不符'),
    ('Insufficient native cash', '现金不足'),
    ('Cannot sell more shares than held', '卖出超过了持仓'),
    ('Split requires an existing position', '送转股需要已有持仓'),
    ('Create an opening balance', '账户还没有期初余额'),
    ('Account already exists', '账户已经有期初余额'),
    ('Duplicate opening position', '期初中同一代码出现了两次'),
    ('two different currencies', '换汇需要两种不同的币种'),
    ('Aggregate fund', '基金合计只能用人民币'),
    ('Insufficient CNY cash for fund purchase', '人民币现金不足，无法申购'),
    ('Fund redemption exceeds', '赎回金额超过了账本记录的基金市值'),
    ('Transfer requires', '转账需要另一个已有的账户'),
    ('Reversal', '冲销的目标记录无效'),
)


def ledger_reason(exc: Exception) -> str:
    """Why the ledger refused an event, in Chinese (its own Chinese messages, such as the B share refusal, as they are)."""
    message = str(exc)
    if re.search(r'[\u4e00-\u9fff]', message):
        return message.rstrip('。')
    return next((chinese for needle, chinese in LEDGER_REASONS if needle in message), '账本拒绝了这条记录')
# Kinds whose fee the ledger would take as 0 when it is missing (``core.ledger.replay``): the fee must be explicit.
FEE_KINDS = ('buy', 'sell', 'fund_buy', 'fund_sell')


def number_check(fields: dict) -> Optional[Tuple[str, str]]:
    """(why the numbers of an event cannot be booked, the field): beyond ``values.LIMITS``, or not positive (a fee:
    negative). Every event of a broker or template row passes here (``_event``, ``interpret_template``)."""
    if fields.get('kind') in FEE_KINDS and fields.get('fee') is None:
        return '买卖缺少费用：账本会把没有费用的买卖记为费用 0。请填写费用（确实没有费用请填 0）。', 'fee'
    for name, label in EVENT_NUMBERS.items():
        if fields.get(name) is None:
            continue
        number = Decimal(str(fields[name]))
        if abs(number) > LIMITS[name]:
            return f'{label} {text(number)} 超出可处理的范围（最大 {text(LIMITS[name])}）。请核对。', name
        zero = name == 'fee' or (name == 'amount' and fields.get('kind') == 'fund_value')   # a fund total may be 0
        if number < 0 or (number == 0 and not zero):
            return f'{label}必须{"不小于" if zero else "大于"} 0（这一行是 {text(number)}）。请核对。', name
    return None


def number_problem(fields: dict) -> Optional[str]:
    found = number_check(fields)
    return found[0] if found else None


def _event(row: Row, ctx: Context, suffix: str, **fields) -> Optional[dict]:
    raw = {'date': row.date, 'account': ctx.account, 'source': ctx.source,
           'external_id': (row.external_id or '') + suffix, **{k: v for k, v in fields.items() if v is not None}}
    for key in EVENT_NUMBERS:
        if isinstance(raw.get(key), Decimal):
            raw[key] = text(raw[key])
    found = number_check(raw)
    if found:
        row.set('review', found[0], needs=[NUMBER_NEEDS.get(found[1], found[1])])
        return None
    if row.action and 'note' not in raw:
        raw['note'] = row.action[:240]
    if row.name and raw.get('ticker') and 'name' not in raw:
        raw['name'] = row.name[:240]
    try:
        return normalize(raw)
    except LedgerError as exc:
        row.set('review', f'账本无法接受这一行：{ledger_reason(exc)}。{NOT_HERE}：请核对文件，或把它设为跳过。')
        return None


def _needs_ticker(row: Row) -> bool:
    res = row.resolution
    if row.ticker:
        return False
    if res is None or not row.code:
        row.set('review', '缺少证券代码。', needs=['ticker'])
    elif res.status == 'unsupported':
        row.set('unsupported', res.message)
    else:
        if res.choices:
            row.choices['ticker'] = res.choices
        row.set('review', res.message or '请选择正式代码。', needs=['ticker'])
    return True


def _opposite_sign(row: Row, v: dict, side: str, overridden: bool) -> Optional[str]:
    """Why the file's signs contradict the action (a correction in disguise), or None."""
    if side == 'buy' and v['quantity'] is not None and v['quantity'] < 0 and _edit_decimal(row, 'quantity') is None:
        return (f'“{row.action}”的成交数量是负数（{text(v["quantity"])}）：买入的股份应当增加，这一行可能是冲正或撤销。'
                '请核对；确认是买入时请填写正数数量。')
    cash = row.cash_change
    if not overridden and cash and (cash > 0) == (side == 'buy'):
        return (f'发生金额 {money(cash)} 的方向与“{row.action}”相反（买入是资金减少，卖出是资金增加），'
                '这一行可能是冲正或撤销。请核对。')
    return None


def _trade(row: Row, v: dict, ctx: Context, side: str, overridden: bool):
    if _needs_ticker(row):
        return
    edit = row.edits
    edited_amount = _edit_decimal(row, 'amount')
    if edited_amount is not None:   # the person's |发生金额| for this trade
        row.cash_change = -edited_amount if side == 'buy' else edited_amount
    problem = _opposite_sign(row, v, side, overridden)
    if problem:
        row.set('review', problem, needs=['kind', 'quantity'])
        return
    quantity = _edit_decimal(row, 'quantity')
    if quantity is None:
        quantity = abs(v['quantity']) if v['quantity'] is not None else None
        if quantity and 'quantity' in v['lots']:
            quantity = _in_shares(row, quantity, '成交数量')
            if quantity is None:
                return
    if not quantity:
        row.set('review', '缺少成交数量。', needs=['quantity'])
        return
    if quantity > MAX_QUANTITY:
        row.set('review', f'成交数量 {text(quantity)} 超出可处理的范围。', needs=['quantity'])
        return
    if codes.whole_units(row.ticker) and quantity != quantity.to_integral_value():
        row.set('review', f'成交数量 {text(quantity)} 不是整数：{row.ticker} 以整股（张、份）成交，这个数字可能读错了'
                          '（单位或小数点）。请核对。', needs=['quantity'])
        return
    row.quantity = quantity
    edited_price = _edit_decimal(row, 'price')
    turnover = v['turnover']
    if turnover is not None and turnover < 0:
        if edited_price is not None:
            turnover = None   # the price the person gave replaces the turnover whose sign is unclear
        elif not (side == 'buy' and row.cash_change is not None and row.cash_change < 0):
            # 成交金额 is a magnitude; signed like cash it is negative only on a buy whose cash also goes out.
            row.set('review', f'成交金额是负数（{text(turnover)}），与“{row.action}”的方向对不上（卖出的资金应当增加），'
                              '也没有发生金额能说明它是按资金流向记的符号。请核对，或填写价格。', needs=['price'])
            return
        else:
            turnover = abs(turnover)
    row.turnover = turnover
    if edited_price is None and v['price'] is not None and v['price'] < 0:
        row.set('review', f'成交价格是负数（{text(v["price"])}）：价格不可能为负，这一行可能是冲正或对错了列。请核对，'
                          '确认后填写价格。', needs=['price'])
        return
    price = edited_price if edited_price is not None else v['price']
    price = _price(row, quantity, price, None if edited_price is not None else turnover)
    if price is None:
        if row.status == 'new':
            row.set('review', '缺少成交价格。', needs=['price'])
        return
    if price > MAX_PRICE:
        row.set('review', f'成交价格 {text(price)} 超出可处理的范围（数量的单位可能不对）。', needs=['quantity', 'price'])
        return
    row.price = price
    # The file's own price, or one worked out as 成交金额 ÷ 数量 because the file's was rounded or missing.
    row.price_source = 'edited' if edited_price is not None else 'columns' if price == v['price'] else 'derived'
    exchange = row.ticker.split(':')[0]
    stated, trade_side, problem = currency_sides(row, v)
    connect_words = any(w in f"{row.action}{v['market']}" for w in CONNECT_WORDS)
    if exchange == 'HKG':
        # 港股通: the settlement is CNY and the fee may be stated in HKD; its currency is the fee's (``_connect``).
        apart = currency_sides(row, v, fees_apart=True)
        if not apart[2] and (apart[0] == 'CNY' or apart[0] is None and connect_words):
            stated, trade_side, problem = apart
    if problem:
        row.set('review', problem, needs=['currency'])
        return
    connect = exchange == 'HKG' and (stated == 'CNY' or connect_words)
    if exchange == 'HKG' and not connect and stated is None:
        row.set('review', '港股成交，但无法确定结算币种：如是港股通（人民币结算），请确认文件包含币种或“港股通”字样；'
                          '如是港币账户，请在币种列标明港币。', needs=['currency'])
        return
    if trade_side is not None and trade_side != row.currency:
        row.set('review', f'{row.ticker} 以 {row.currency} 交易，但成交价格或成交金额标明的币种是 {trade_side}。'
                          f'如果代码选错了，请重新选择代码；否则{NOT_HERE}，请核对文件，或把它设为跳过。', needs=['ticker'])
        return
    if connect and stated not in (None, 'CNY'):
        row.set('review', f'港股通以人民币结算，但这一行的币种是 {stated}。请核对。', needs=['currency'])
        return
    if not connect and stated is not None and stated != row.currency:
        row.set('review', f'{row.ticker} 以 {row.currency} 交易，但这一行的币种是 {stated}。请核对代码，或填写这一行的币种。',
                needs=['ticker', 'currency'])
        return
    cash = row.cash_change
    edited_fee = _edit_decimal(row, 'fee')
    if connect:
        _connect(row, v, ctx, side, quantity, price, edited_fee)
        return
    if edited_fee is not None:
        fee, source = edited_fee, 'edited'
    else:
        if overridden:
            cash = None  # the cash column of a 新股入账 / 配股 row does not describe this trade
        fee, source, note, problem = trade_fee(v['fees'], v['fee_columns'], cash,
                                               turnover or ledger_product(quantity, price), side, v.get('negative_fee'),
                                               row.earlier)
        if problem:
            row.set('review', problem, needs=['fee'])
            return
        if note:
            row.notes.append(note)
    row.fee, row.fee_source = fee, source
    event = _event(row, ctx, '', kind=side, ticker=row.ticker, currency=row.currency, quantity=quantity, price=price,
                   fee=fee, allow_margin=True if edit.get('allow_margin') else None)
    if event:
        row.events = [event]
        if row.earlier:
            row.earlier_events = [{**event, **{k: decimal_text(v) for k, v in row.earlier.items()}}]


def _in_shares(row: Row, lots: Decimal, what: str) -> Optional[Decimal]:
    """A quantity in 手 converted with the instrument's lot; review when the lot is not certain."""
    lot = codes.lot_size(row.ticker)
    if lot is None:
        row.set('review', f'{what}的单位是“手”，但无法确定 {row.ticker or row.code} 每手多少股 / 张'
                          '（只有 A 股股票和 ETF 确定是 100；债券、基金、港股、美股各不相同）。请填写以股或张为单位的数量。',
                needs=['quantity'])
        return None
    row.notes.append(f'{what} {text(lots)} 手 × {lot} = {text(lots * lot)} 股')
    return lots * lot


def _fee_currency(v: dict) -> Tuple[Optional[str], Optional[str]]:
    """(the currency the fee cells and headers state: HKD, CNY or None, problem) for a 港股通 row."""
    found = {('HKD' if code == '$' else code) for code in (v.get('evidence') or {}).get('fee', {})}
    if not found:
        return None, None
    if len(found) == 1 and found <= {'HKD', 'CNY'}:
        return found.pop(), None
    where = '、'.join(f'{w}' for w in (v.get('evidence') or {}).get('fee', {}).values())
    return None, f'港股通的费用列标明的币种无法使用（{where}）：只能是人民币或港币。请填写这一笔的人民币费用。'


def _connect(row: Row, v: dict, ctx: Context, side: str, quantity: Decimal, price: Decimal, edited_fee):
    """港股通: the cash column is CNY, prices and turnover are HKD."""
    cash = row.cash_change
    if cash is None or cash == 0:
        row.set('review', '港股通成交缺少人民币清算金额（发生金额），无法换算。请在列对应里选择发生金额列。', needs=['mapping'])
        return
    hkd_turnover = ledger_product(quantity, price)   # exactly 成交金额 (see "Cash to the cent")
    rate, problem = _rate(row, v)
    if problem:
        row.set('review', problem, needs=['fx_rate'])
        return
    fee_hkd = None   # the fee as the file states it in HKD, when it does
    if edited_fee is not None:
        fee_cny, source = edited_fee, 'edited'   # the CNY fee the page asked for: the fee cells no longer matter
    else:
        fee_unit, problem = _fee_currency(v)
        if problem:
            row.set('review', problem, needs=['fee'])
            return
        candidates, uncertain = fee_rule(v['fees'])
        if not candidates:
            row.set('review', '港股通成交缺少费用列，无法用人民币金额推算费用。请填写人民币费用。', needs=['fee'])
            return
        if (uncertain or v.get('negative_fee')) and rate is None:
            row.set('review', f'{uncertain or "费用列是负数（" + v["negative_fee"] + "）"}，没有结算汇率可以核对。'
                              '请填写这一笔的人民币费用。', needs=['fee'])
            return
        fee_cny, source = candidates[0][0], 'columns'
        sign = 1 if side == 'buy' else -1
        if rate is not None:
            # The CNY settlement is the truth: the fee in CNY is what it leaves beside the converted principal. A fee
            # column confirms it as CNY, or as HKD at the rate; a fee stated in HKD is never taken for CNY.
            derived = sign * (abs(cash) - hkd_turnover * rate)
            as_cny = next((total for total, _d in candidates if fee_unit != 'HKD'
                           and abs(hkd_turnover * rate + sign * total - abs(cash)) <= Decimal('0.05')), None)
            as_hkd = next((total for total, _d in candidates if fee_unit != 'CNY'
                           and abs(total * rate - derived) <= Decimal('0.05')), None)
            if as_cny is not None:
                fee_cny = as_cny
            elif as_hkd is not None and derived >= 0:
                fee_cny, fee_hkd, source = derived, as_hkd, 'derived'
            elif fee_unit == 'HKD':
                fee_cny = candidates[0][0] * rate   # reported below as not matching the settlement
        elif fee_unit == 'HKD':
            # Without a rate: the settlement converts principal and fee at one rate.
            fee_hkd = candidates[0][0]
            rate = abs(cash) / (hkd_turnover + sign * fee_hkd)
            fee_cny, source = (fee_hkd * rate).quantize(CENT, rounding=ROUND_HALF_UP), 'derived'
    implied = (abs(cash) - fee_cny if side == 'buy' else abs(cash) + fee_cny) / hkd_turnover
    if rate is not None:
        expected = hkd_turnover * rate + (fee_cny if side == 'buy' else -fee_cny)
        if abs(expected - abs(cash)) > Decimal('0.05'):
            row.set('review', f'港股通：成交金额 HK${money(hkd_turnover)} × 汇率 {rate} ± 费用 ¥{money(fee_cny)} = '
                              f'¥{money(expected)}，与清算金额 ¥{money(abs(cash))} 不符。请核对汇率和费用。',
                    needs=['fx_rate', 'fee'])
            return
    else:
        rate = implied
    # 港股通 opened in 2014; HKD/CNY has stayed within about 0.78-0.93 since. Outside the band the
    # amount is probably not CNY (an HKD amount gives about 1.0), so the row is not booked.
    if not HKD_CNY_BAND[0] < rate < HKD_CNY_BAND[1]:
        row.set('review', f'换算出的港币兑人民币汇率是 {text(rate.quantize(Decimal("1e-6")))}，不在合理范围内。'
                          '请核对发生金额是否为人民币清算金额：填写人民币清算金额，或在币种里选择它实际的币种。',
                needs=['amount', 'currency'])
        return
    if fee_hkd is None:
        fee_hkd = (fee_cny / rate).quantize(CENT, rounding=ROUND_HALF_UP)
    row.fee, row.fee_source = fee_cny, source
    row.currency, row.cash_currency = 'HKD', 'CNY'
    rate_text = text(rate.quantize(Decimal('1e-6')) if rate.as_tuple().exponent < -6 else rate)
    row.notes.append(f'港股通：人民币清算 ¥{money(abs(cash))}；费用 ¥{money(fee_cny)} 按汇率 {rate_text} 折合 HK${money(fee_hkd)}，'
                     '拆成换汇和港币成交两笔')
    margin = True if row.edits.get('allow_margin') else None
    if side == 'buy':
        fx = _event(row, ctx, ':fx', kind='fx', currency='CNY', amount=abs(cash), to_currency='HKD',
                    received_amount=hkd_turnover + fee_hkd)
        trade = _event(row, ctx, ':trade', kind='buy', ticker=row.ticker, currency='HKD', quantity=quantity,
                       price=price, fee=fee_hkd, allow_margin=margin)
        events = [fx, trade]
    else:
        if hkd_turnover - fee_hkd <= 0:
            row.set('review', '港股通卖出金额不足以支付费用，请核对费用。', needs=['fee'])
            return
        trade = _event(row, ctx, ':trade', kind='sell', ticker=row.ticker, currency='HKD', quantity=quantity,
                       price=price, fee=fee_hkd)
        fx = _event(row, ctx, ':fx', kind='fx', currency='HKD', amount=hkd_turnover - fee_hkd, to_currency='CNY',
                    received_amount=abs(cash))
        events = [trade, fx]
    if all(events):
        row.events = events
        if 'price' in row.earlier:   # rules version 2 converted quantity × the 10-place price
            old_turnover = quantity * row.earlier['price']
            changed = {'price': decimal_text(row.earlier['price'])}
            money_field = 'received_amount' if side == 'buy' else 'amount'
            old_money = decimal_text(old_turnover + fee_hkd if side == 'buy' else old_turnover - fee_hkd)
            row.earlier_events = [{**e, **(changed if e['kind'] in ('buy', 'sell') else {money_field: old_money})}
                                  for e in events]


_CASH_SIGN = {'dividend': 1, 'interest': 1, 'deposit': 1, 'tax': -1, 'fee': -1, 'withdrawal': -1}
_CASH_KIND = {'dividend': 'dividend', 'interest': 'interest', 'deposit': 'deposit', 'tax': 'fee', 'fee': 'fee',
              'withdrawal': 'withdrawal'}


def _cash_flow(row: Row, v: dict, ctx: Context, category: str):
    edited = _edit_decimal(row, 'amount')
    value = edited if edited is not None else row.cash_change
    fees = sum((amount for _name, amount in v['fees']), Decimal(0))
    # Income stated three ways (成交金额 gross, the fee or tax withheld, 发生金额 posted): when gross − withheld is the
    # posted amount, the posted amount is the credit, without any edit.
    netted = bool(fees and edited is None and category in ('dividend', 'interest') and v['turnover'] and value
                  and abs(v['turnover']) - fees == abs(value))
    if fees and edited is None and _edit_decimal(row, 'fee') is None and not netted:
        row.set('review', f'{CATEGORY_LABELS[category]}这一行有费用 {money(fees)}：无法判断发生金额是否已扣除它。'
                          '请核对后填写实际到账的金额，或设为跳过。', needs=['amount'])
        return
    if netted:
        row.notes.append(f'{CATEGORY_LABELS[category]} {money(abs(v["turnover"]))} − 扣除 {money(fees)} = 到账 '
                         f'{money(abs(value))}：按到账金额记')
    if (edited is None and not netted and value is not None and v['turnover'] and abs(v['turnover']) != abs(value)):
        row.set('review', f'成交金额 {money(v["turnover"])} 与发生金额 {money(value)} 不一致，无法确定{CATEGORY_LABELS[category]}'
                          '的金额。请核对后填写金额。', needs=['amount'])
        return
    from_turnover = False
    if value is None and category == 'dividend' and v['turnover'] is not None:
        value, from_turnover = v['turnover'], True
        row.notes.append('分红金额取自成交金额列')
    if value is None or value == 0:
        row.set('review', '缺少发生金额。', needs=['amount'])
        return
    sign = _CASH_SIGN[category]
    if edited is None and (value > 0) != (sign > 0):
        row.set('review', f'金额 {money(value)} 的正负与业务“{row.action}”的方向不一致。请核对。', needs=['amount'])
        return
    value = abs(value)
    ticker = None
    if category in ('dividend', 'tax') and row.code:
        if row.ticker is None and _needs_ticker(row):
            return
        ticker = row.ticker
    stated, trade_side, problem = currency_sides(row, v)
    if problem:
        row.set('review', problem, needs=['currency'])
        return
    if from_turnover and not row.edits.get('currency'):
        # The amount is the turnover column's: its currency evidence is the trade side's (and the 币种 column's).
        if trade_side and stated and trade_side != stated:
            row.set('review', f'币种不一致：成交金额是 {trade_side}，这一行的币种或发生金额是 {stated}。请核对币种。',
                    needs=['currency'])
            return
        stated = trade_side or stated
    if stated is None and not ticker and ctx.currencies - {'CNY'}:
        others = '、'.join(sorted(ctx.currencies - {'CNY'}))
        row.set('review', f'这个文件里有以 {others} 标明的金额，这一行没有标明币种，不能当作人民币。请核对币种。',
                needs=['currency'])
        return
    unit = stated or 'CNY'
    if stated is None and ticker and not ticker.startswith(('SHA:', 'SHE:')):
        connect = ticker.startswith('HKG:') and any(w in f"{row.action}{v['market']}" for w in CONNECT_WORDS)
        if not connect:
            row.set('review', f'{ticker} 的{CATEGORY_LABELS[category]}没有标明币种。请核对币种。', needs=['currency'])
            return
    if ticker and ticker.startswith(('SHA:', 'SHE:')) and unit != 'CNY':
        row.set('review', f'{ticker} 是 A 股，{CATEGORY_LABELS[category]}以人民币结算，但这一行的币种是 {unit}。请核对币种。',
                needs=['currency'])
        return
    row.currency = unit
    rate = None
    if category in ('deposit', 'withdrawal') and unit != 'CNY':
        rate, problem = _rate(row, v)
        if problem:
            row.set('review', problem, needs=['fx_rate'])
            return
        if rate is None:
            row.set('review', f'{unit} 入金 / 出金需要汇率（每 1 {unit} 折合人民币）才能计入累计净投入。', needs=['fx_rate'])
            return
    event = _event(row, ctx, '', kind=_CASH_KIND[category], currency=unit, amount=value, ticker=ticker, fx_rate=rate)
    if event:
        row.events = [event]


def _rate(row: Row, v: dict) -> Tuple[Optional[Decimal], Optional[str]]:
    """(the rate to use: the person's edit or the file's, problem). A rate the file states that is not positive is
    a problem, never a missing rate to be replaced by an implied one."""
    edited = _edit_decimal(row, 'fx_rate')
    if edited:
        return edited, None
    if v['fx_rate'] is not None and v['fx_rate'] <= 0:
        return None, f'汇率是 {text(v["fx_rate"])}，不是正数：文件里的汇率无法使用。请核对，或填写汇率。'
    return v['fx_rate'], None


def _edit_decimal(row: Row, name: str) -> Optional[Decimal]:
    value = row.edits.get(name)
    if value is None:
        return None
    number = decimal(value)  # already validated when the edit was saved
    return abs(number) if number is not None else None


# -- the whole file ------------------------------------------------------------

def _resolve(row: Row, v: dict, ctx: Context, file_has_a_codes: bool, mapping: dict):
    hint = codes.market_hint(action=row.action, market=v['market'], currency=v['currency'], shareholder=v['shareholder'])
    code_column = mapping.get('code')
    row.resolution = codes.resolve(row.code, hint=hint, numeric_cell=code_column in ctx.numeric_columns,
                                   file_has_a_codes=file_has_a_codes, short_code_market=ctx.short_code_market,
                                   known=ctx.known_tickers, aliases=ctx.aliases) if row.code else None
    edited = row.edits.get('ticker')
    if edited:
        row.ticker = codes.held_spelling(edited, ctx.known_tickers)   # HKG:700 books into a held HKG:00700
        row.currency = codes.MARKETS[edited.split(':')[0]]
        row.notes.append('代码由你选择')
    elif row.resolution is not None and row.resolution.status == 'ok':
        row.ticker, row.currency = row.resolution.ticker, row.resolution.currency
        if row.resolution.note:
            row.notes.append(row.resolution.note)


def _is_footer(cells: List[str]) -> bool:
    first = re.sub(r'\s+', '', unicodedata.normalize('NFKC', next((clean(c) for c in cells if clean(c)), '')))
    return first.startswith(('合计', '总计', '小计', '本页合计', '本页小计', '汇总', '累计')) or first in ('共计',)


_TITLE_DATE = re.compile(r'(?<!\d)(\d{4})(?:[-/.年](\d{1,2})[-/.月](\d{1,2})日?|(\d{2})(\d{2}))(?!\d)')
_PERIOD_WORDS = ('至', '到', '~', '起始', '终止', '开始', '结束', '截止')


def statement_period(title_rows) -> Optional[Tuple[str, str]]:
    """(first, last) date of the period the title lines state (``2026-11-01 至 2026-11-14``,
    ``起始日期:20261101 终止日期:20261114``), or None when they state none."""
    from datetime import date
    text = unicodedata.normalize('NFKC', ' '.join(clean(cell) for row in title_rows for cell in row if clean(cell)))
    if not any(word in text for word in _PERIOD_WORDS):
        return None
    found = []
    for match in _TITLE_DATE.finditer(text):
        year, month, day = match.group(1), match.group(2) or match.group(4), match.group(3) or match.group(5)
        try:
            found.append(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            continue
    found = sorted(day for day in set(found) if '1990' <= day[:4] <= '2100')
    return (found[0], found[-1]) if len(found) >= 2 else None


def interpret(table, mapping: dict, ctx: Context, edits: Dict[str, dict]) -> List[Row]:
    header = table.header
    rows: List[Row] = []
    values: Dict[int, dict] = {}
    header_norm = [normalize_header(h) for h in header]
    units = column_units(header, mapping)
    second_table = None   # the row number of a header with other columns: a second table, read with another mapping
    for index, (number, cells) in enumerate(table.data()):
        valid, problems = checked_edits(number, edits.get(str(number), {}), 'transactions')
        row = Row(row=number, cells=list(cells), index=index, edits=valid, edit_problems=problems)
        names = [normalize_header(c) for c in cells]
        while names and not names[-1]:
            names.pop()
        if names == header_norm[:len(names)] and len(names) >= min(3, len(header_norm)):
            continue  # a repeated header (multi-page export)
        if header_matches(cells) >= 3 and not any(ch.isdigit() for c in cells for ch in clean(c)):
            second_table = second_table or number   # another table's header: its rows cannot use this mapping
            continue
        rows.append(row)
        if _is_footer(cells):
            row.category = 'footer'
            row.set('skipped', '合计 / 汇总行')
            continue
        if second_table is not None:
            row.set('review', f'第 {second_table} 行起是另一个表格（表头的列与第 {table.header_index + 1} 行不同），'
                              f'不能按这里的列对应读取。{NOT_HERE}：请把它另存为单独的文件后导入，或设为跳过。')
            continue
        extra = [clean(c) for c in cells[len(header):] if clean(c)]
        if extra and table.kind in ('text', 'html'):
            row.set('review', f'这一行比表头多出 {len(cells) - len(header)} 列（“{extra[0][:20]}”）：可能是数字里的逗号或'
                              f'分隔符把单元格拆开了，各列的值已经错位。{NOT_HERE}：请核对文件（数字加引号，或另存为制表符'
                              '分隔）后重新导入，或设为跳过。')
            continue
        v = read(row, header, mapping, units, ctx.comma, table.native(row.row))
        values[row.index] = v
        ctx.currencies.update(code for side in v['evidence'].values() for code in side)
        row.action, row.code, row.name = v['action'], v['code'], v['name']
        row.date, row.time = v['date'], v['time']
        row.cash_change, row.cash_balance, row.share_balance = v['cash_change'], v['cash_balance'], v['share_balance']
        row.quantity, row.price, row.turnover = v['quantity'], v['price'], v['turnover']
        row.trade_id, row.serial = v['trade_id'], v['serial']
        cash_side = currency_sides(row, v)[0]
        stated = [code for code in v['evidence']['cash'] if code.isalpha()]
        # The balance column's currency for the reconciliation; an unsupported one is shown as it is.
        row.cash_currency = cash_side or (stated[0] if len(stated) == 1 else 'CNY')
        if cash_side and cash_side != 'CNY':
            row.currency = cash_side
        if ctx.whitespace and len(cells) != len(header):
            row.set('review', f'这一行有 {len(cells)} 列，表头有 {len(header)} 列（空格分隔的文本里名称可能含空格）。'
                              f'{NOT_HERE}：请另存为制表符或 CSV 后重新导入，或设为跳过。')
    file_has_a_codes = any(len(codes.bare(r.code)) == 6 and codes.bare(r.code).isdigit() for r in rows)
    period = statement_period(table.title_rows)
    ctx.accounts = accounts.decide(rows, ctx.account_choice, ctx.account, ctx.identities)
    # Identity comes from the file's own values; a date the person filled in only completes a missing one,
    # and such rows are numbered after every row dated in the file (see dedupe.assign_ids).
    filled = []
    for row in rows:
        if row.status == 'new' and not row.date and row.edits.get('date'):
            row.date = row.edits['date']
            filled.append(row)
    dedupe.assign_ids(rows, filled)
    for row in rows:
        if row.status != 'new':
            continue
        v = values[row.index]
        if refuse_stored_edits(row):
            continue
        for key in row.edits:   # a valid edit replaces the unreadable cell of its field
            for name in EDIT_REPAIRS.get(key, ()):
                row.parse_errors.pop(name, None)
        if row.edits.get('date'):
            row.date = row.edits['date']
        blocking = {k: m for k, m in row.parse_errors.items() if k not in _INFORMATIONAL}
        for name, message in row.parse_errors.items():
            if name in _INFORMATIONAL:
                row.notes.append(f'{_FIELD_NAMES.get(name, name)}无法读取（{message}），已忽略')
        if blocking:
            # The edits that stand in for the unreadable cells (EDIT_REPAIRS); a cell no edit replaces cannot be fixed
            # here.
            needs = [edit for edit, cells in EDIT_REPAIRS.items() if set(cells) & set(blocking)]
            unrepaired = [k for k in blocking if not any(k in cells for cells in EDIT_REPAIRS.values())]
            row.set('review', '；'.join(f'{_FIELD_NAMES.get(k, k)}：{m}' for k, m in blocking.items()) +
                    (f'。{NOT_HERE}：请核对文件后重新导入，或把它设为跳过。' if unrepaired or not needs else ''), needs=needs)
            continue
        if not row.date:
            row.set('review', '缺少成交日期。', needs=['date'])
            continue
        if period and not period[0] <= row.date <= period[1] and not row.edits.get('date'):
            row.set('review', f'日期 {row.date} 不在文件标题标明的期间（{period[0]} 至 {period[1]}）内：可能是另一份文件的行，'
                              '或日期读错了。请核对后填写日期，或设为跳过。', needs=['date'])
            continue
        try:
            _resolve(row, v, ctx, file_has_a_codes, mapping)
            _build(row, v, ctx)
        except ArithmeticError:  # decimal.InvalidOperation, Overflow: never a 500, never a guess
            row.set('review', UNCOMPUTABLE, needs=['quantity', 'price', 'amount'])
        if 'share_balance' in v['lots'] and row.share_balance is not None:
            lot = codes.lot_size(row.ticker)
            row.share_balance = row.share_balance * lot if lot else None
            if not lot:
                row.notes.append('股份余额的单位是“手”，无法确定每手股数，未用于对账')
    _chronological(rows)
    _pair_repos(rows, values, ctx)
    return rows


# The cells a row edit stands in for: an unreadable one no longer blocks the row once the edit is made.
# A price edit makes 成交金额 unnecessary for the price; an amount edit is the row's cash amount (for a
# trade: |发生金额|, its sign from the side; for a dividend also instead of 成交金额).
EDIT_REPAIRS = {'date': ('date',), 'quantity': ('quantity',), 'price': ('price', 'turnover'), 'fee': ('fees',),
                'amount': ('cash_change', 'income', 'expense', 'turnover'), 'fx_rate': ('fx_rate',)}
_FIELD_NAMES = {'date': '日期', 'time': '时间', 'quantity': '数量', 'price': '价格', 'turnover': '成交金额',
                'cash_change': '发生金额', 'income': '收入金额', 'expense': '支出金额', 'cash_balance': '资金余额',
                'share_balance': '股份余额', 'fx_rate': '汇率', 'fees': '费用'}
_INFORMATIONAL = {'time', 'cash_balance', 'share_balance'}  # used for ordering and reconciliation only
_TICKER_CATEGORIES = {'buy', 'sell', 'dividend', 'tax', 'split', 'ipo', 'ipo_payment', 'rights', 'fund', 'margin',
                      'conversion', 'unknown', 'blank'}


def _build(row: Row, v: dict, ctx: Context):
    kind = row.edits.get('kind')
    category, skip_label = classify(row.action, row.resolution, row.cash_change)
    row.category = category
    if kind == 'skip':
        row.category = 'skip'
        row.set('skipped', '你选择了跳过这一行')
        return
    if kind:
        category = {'fee': 'fee'}.get(kind, kind)
        row.category = category
    overridden = bool(kind)
    if category == 'skip':
        row.set('skipped', skip_label)
        return
    if (category in _TICKER_CATEGORIES and row.resolution is not None and row.resolution.status == 'unsupported'
            and not row.edits.get('ticker')):
        row.set('unsupported', row.resolution.message)
        return
    if category == 'blank':
        side = _side_from_sign(row, v)
        if side is None:
            row.set('review', '缺少业务类型（买入 / 卖出等），也无法从金额正负判断。请选择类型。', needs=['kind'])
            return
        row.category = category = side
        row.notes.append('没有业务类型，按金额正负判断为' + CATEGORY_LABELS[side])
    if category == 'repo':
        return  # paired in _pair_repos
    if category == 'correction':
        row.set('review', CORRECTION_HINT.format(action=row.action[:40]), needs=['kind'])
        return
    if category in ('buy', 'sell'):
        _trade(row, v, ctx, category, overridden)
    elif category in _CASH_SIGN:
        _cash_flow(row, v, ctx, category)
    elif category == 'split':
        if _needs_ticker(row):
            return
        if v['quantity'] is not None and v['quantity'] < 0 and _edit_decimal(row, 'quantity') is None:
            row.set('review', f'送转股的到账数量是负数（{text(v["quantity"])}），这一行可能是冲正。请核对。', needs=['quantity'])
            return
        moved = [f'{label} {money(amount)}' for label, amount in
                 (('发生金额', row.cash_change), ('成交金额', v['turnover']),
                  ('费用', sum((a for _n, a in v['fees']), Decimal(0)))) if amount]
        if moved and _edit_decimal(row, 'quantity') is None:
            row.set('review', f'送转股这一行有资金变动（{"、".join(moved)}）：送股和转增不会有资金变动，这一行可能同时含有'
                              '分红或费用。请核对，分开记录。', needs=['kind', 'amount'])
            return
        added = _edit_decimal(row, 'quantity')
        if added is None and v['quantity']:
            added = abs(v['quantity'])
            if 'quantity' in v['lots']:
                added = _in_shares(row, added, '到账数量')
                if added is None:
                    return
        if not added:
            row.set('review', '送转股缺少到账数量。', needs=['quantity'])
            return
        if codes.whole_units(row.ticker) and added != added.to_integral_value():
            row.set('review', f'送转股的到账数量 {text(added)} 不是整数：{row.ticker} 以整股计（零碎股按现金发放）。请核对。',
                    needs=['quantity'])
            return
        row.quantity = row.split_added = added  # the ratio is set when the rows are checked against the ledger
    elif category in REVIEW_HINTS:
        if row.resolution is not None and row.resolution.choices:
            row.choices['ticker'] = row.resolution.choices
        row.set('review', REVIEW_HINTS[category], needs=['kind', 'ticker', 'price', 'fee'])
    else:
        row.set('review', f'无法识别的业务“{row.action}”。请选择类型，或设为跳过。', needs=['kind'])


def _side_from_sign(row: Row, v: dict) -> Optional[str]:
    if not row.code or v['quantity'] is None:
        return None
    if v['quantity'] < 0:
        return 'sell'
    if row.cash_change is not None and row.cash_change != 0:
        return 'buy' if row.cash_change < 0 else 'sell'
    return None


def _balance_fit(rows: List[Row]) -> Tuple[int, int]:
    """(rows whose 资金余额 is the previous row's plus their 发生金额, rows where it is not), in this order. Only the
    selected account's rows: another account's 资金余额 is another chain."""
    last: Dict[str, Decimal] = {}
    fits = misses = 0
    for row in rows:
        if row.cash_balance is None or row.category == 'footer' or row.other_account:
            continue
        if row.cash_currency in last and row.cash_change is not None:
            if last[row.cash_currency] + row.cash_change == row.cash_balance:
                fits += 1
            else:
                misses += 1
        last[row.cash_currency] = row.cash_balance
    return fits, misses


def _cash_in(row: Row) -> bool:
    if row.cash_change is not None:
        return row.cash_change > 0
    return row.category in ('sell', 'dividend', 'interest', 'deposit')


def _moves(row: Row) -> bool:
    """A trade of the selected account: the one kind of row whose order within an untimed day decides what the
    account can afford."""
    return row.category in ('buy', 'sell') and not row.other_account


def _day_order(day: List[Row]) -> List[Row]:
    """A day's rows without times and without 资金余额 to order them: among the account's trades, a sale goes before
    the buys it pays for, except a sale of a listing that an earlier trade of the day buys, which keeps its place
    after it. Every other row keeps its place in the file."""
    first, rest = [], []
    for row in filter(_moves, day):
        if _cash_in(row) and not any(other.ticker and other.ticker == row.ticker for other in rest):
            first.append(row)
        else:
            rest.append(row)
    trades = iter(first + rest)
    return [next(trades) if _moves(row) else row for row in day]


# Rows other than trades that change a share count: a sale moved across one of them may sell shares not yet held.
_SHARE_COUNT = ('split', 'ipo', 'rights', 'fund', 'margin', 'conversion')


def _chronological(rows: List[Row]):
    """``row.order``: the file's order made chronological. Dates decide whether the file lists the newest row first;
    when they do not (one day), the selected account's 资金余额 chain does. Within a day, rows without times keep that
    order when their balances confirm it; without balances, the account's sales go before its buys (``_day_order``),
    so a day the account can afford is never refused for the order of its trades. A day with a split (or another
    row that changes a share count) of a listing the day's trades trade keeps the file's order."""
    dated = [r.date for r in rows if r.date]
    if len(dated) > 1 and dated[0] != dated[-1]:
        descending = dated[0] > dated[-1]
    else:
        descending = _balance_fit(list(reversed(rows)))[0] > _balance_fit(rows)[0]
    key_rows = list(reversed(rows)) if descending else list(rows)
    for position, row in enumerate(key_rows):
        row.order = position
    ordered = sorted(rows, key=lambda r: (r.date or '9999-99-99', r.time or '', r.order))
    days: Dict[str, List[Row]] = {}
    for row in ordered:
        if row.date:
            days.setdefault(row.date, []).append(row)
    for day in days.values():
        if len(day) > 1 and not any(r.time for r in day) and _balance_fit(day) == (0, 0) and \
                not any(r.category in _SHARE_COUNT and not r.other_account and r.ticker and
                        any(_moves(t) and dedupe.same_listing(t.ticker, r.ticker) for t in day) for r in day):
            reordered = iter(_day_order(day))
            ordered = [next(reordered) if row.date == day[0].date else row for row in ordered]
    for position, row in enumerate(ordered):
        row.order = position
    rows.sort(key=lambda r: r.index)


# 逆回购 terms in calendar days by code; the buy-back comes on or after lending date + term
# (later when the maturity falls on a holiday). Other 204xxx / 1318xx codes: term unknown.
REPO_TERMS = {'204001': 1, '204002': 2, '204003': 3, '204004': 4, '204007': 7, '204014': 14, '204028': 28,
              '204091': 91, '204182': 182, '131810': 1, '131811': 2, '131800': 3, '131809': 4, '131801': 7,
              '131802': 14, '131803': 28, '131805': 91, '131806': 182}
REPO_SLACK_DAYS = 12                 # holidays (春节, 国庆) and a buy-back recorded a day late
REPO_MAX_DAYS = 200
REPO_MAX_RATE = Decimal('0.5')       # sanity bound: 50 % a year, far above any normal 逆回购 rate
REPO_MAX_FEE_RATE = Decimal('0.0003')  # the largest 逆回购 fee is 0.03 % of the principal


def _days(first: str, second: str) -> int:
    from datetime import date
    return (date.fromisoformat(second) - date.fromisoformat(first)).days


def _repo_principal(lend: Row) -> Decimal:
    return lend.turnover if lend.turnover else abs(lend.cash_change)


def _repo_window(lend: Row, back: Row) -> Optional[int]:
    """Days between the legs when ``back`` can be the buy-back of ``lend`` by code, quantity and date."""
    if codes.bare(lend.code) != codes.bare(back.code) or not lend.date or not back.date:
        return None
    if lend.quantity is not None and back.quantity is not None and abs(lend.quantity) != abs(back.quantity):
        return None
    if lend.turnover and back.turnover and lend.turnover != back.turnover:
        return None   # 成交金额 is the principal on both legs
    days = _days(lend.date, back.date)
    term = REPO_TERMS.get(codes.bare(lend.code))
    if term is not None and not term <= days <= term + REPO_SLACK_DAYS:
        return None
    if term is None and not (0 <= days <= REPO_MAX_DAYS) or days == 0 and back.order < lend.order:
        return None
    return days


def _repo_bounds(lend: Row, days: int) -> Tuple[Decimal, Decimal]:
    """(lowest, highest) plausible net = buy-back + lending cash for this principal and term."""
    principal = _repo_principal(lend)
    highest = principal * REPO_MAX_RATE * max(days, 1) / 365 + CENT
    lowest = -(principal * REPO_MAX_FEE_RATE + CENT)
    return lowest, highest


def _pair_repos(rows: List[Row], values: Dict[int, dict], ctx: Context):
    """Pair each buy-back with its lending leg only when the match is unambiguous.

    A lending leg (cash out) and a buy-back (cash in) can belong together when the
    code is the same, the quantities and principals (成交金额) agree where the file
    has them, the buy-back is not earlier than the repo's term allows, and the net
    (buy-back + lending cash) lies between minus the largest fee and the interest
    of ``REPO_MAX_RATE`` a year for the days in between. Legs connected by such
    possibilities are paired only when the group is one lending and one buy-back,
    or when every lending leg in it is identical (a rolled repo: any order gives
    the same interest) and each buy-back finds one in date order. Every leg of
    any other group needs review: the interest is never guessed.
    """
    legs = sorted((r for r in rows if r.status == 'new' and r.category == 'repo'), key=lambda r: r.order)
    lends, backs = [], []
    for row in legs:
        problem = _repo_currency(row, values[row.index]) or _repo_cash(row)
        if problem:
            row.set('review', problem, needs=['amount' if '金额' in problem else 'currency'])
        elif row.cash_change < 0:
            lends.append(row)
        else:
            backs.append(row)
    edges: Dict[int, List[Row]] = {}
    implausible: Dict[int, Decimal] = {}
    for back in backs:
        edges[back.row] = []
        for lend in lends:
            days = _repo_window(lend, back)
            if days is None:
                continue
            lowest, highest = _repo_bounds(lend, days)
            net = back.cash_change + lend.cash_change
            if lowest <= net <= highest:
                edges[back.row].append(lend)
            else:
                implausible.setdefault(back.row, net)
    parent = {row.row: row.row for row in lends + backs}

    def root(number):
        while parent[number] != number:
            parent[number] = number = parent[parent[number]]
        return number
    for back in backs:
        for lend in edges[back.row]:
            parent[root(lend.row)] = root(back.row)
    groups: Dict[int, Tuple[List[Row], List[Row]]] = {}
    for row in lends + backs:
        group = groups.setdefault(root(row.row), ([], []))
        (group[0] if row.cash_change < 0 else group[1]).append(row)
    for group_lends, group_backs in groups.values():
        if not group_backs:
            for lend in group_lends:
                lend.set('review', '逆回购借出后没有找到到期购回的记录（可能在下一期的文件里）。这笔不入账；'
                                   '请在导入包含购回记录的文件后再核对。', needs=['kind'])
            continue
        if not group_lends:
            for back in group_backs:
                if back.row in implausible:
                    back.set('review', f'与这一行代码相同的逆回购借出记录，按金额算出的净收益 {money(implausible[back.row])} '
                                       '不在合理范围内（相对本金和天数过高或为负），无法确认是同一笔。请核对后手工记为利息，或跳过。',
                             needs=['kind', 'amount'])
                else:
                    back.set('review', '没有找到对应的逆回购借出记录（可能在上一期的文件里）。请核对后手工记为利息，或跳过。',
                             needs=['kind', 'amount'])
            continue
        pairs = _repo_matching(group_lends, group_backs, edges)
        if pairs is None:
            numbers = '、'.join(str(r.row) for r in sorted(group_lends + group_backs, key=lambda r: r.row))
            for row in group_lends + group_backs:
                row.set('review', f'逆回购配对不唯一：第 {numbers} 行的借出和购回金额可以有不止一种对应方式，'
                                  '算出的利息不同。请核对后手工记为利息，或跳过。', needs=['kind', 'amount'])
            continue
        used = {id(lend) for lend, _back in pairs}
        for lend in group_lends:
            if id(lend) not in used:
                lend.set('review', '逆回购借出后没有找到到期购回的记录（可能在下一期的文件里）。这笔不入账；'
                                   '请在导入包含购回记录的文件后再核对。', needs=['kind'])
        for lend, back in pairs:
            _book_repo(lend, back, ctx)


REPO_BACK_WORDS = ('购回', '到期', '返还')
REPO_LEND_WORDS = ('借出', '拆出', '融券回购')


def repo_direction(action: str) -> Optional[str]:
    """'lend' (cash out) or 'back' (the buy-back, cash in) when the action states it, else None."""
    a = _norm(action)
    if any(word in a for word in REPO_BACK_WORDS):
        return 'back'
    if any(word in a for word in REPO_LEND_WORDS):
        return 'lend'
    return None


def _repo_cash(row: Row) -> Optional[str]:
    """Set the leg's signed cash (a corrected |amount| stands in for the cell); why it cannot be used, or None.
    The sign must agree with the direction the action states (借出 / 融券回购 out, 购回 / 到期 in)."""
    stated = repo_direction(row.action)
    edited = _edit_decimal(row, 'amount')
    if edited is not None:
        if stated is None and not row.cash_change:
            return f'逆回购“{row.action}”没有写明是借出还是购回，修改后的金额无法确定方向。请在类型里选择，或修改原文件。'
        lend = stated == 'lend' if stated else row.cash_change < 0
        row.cash_change = -edited if lend else edited
    if row.cash_change is None or row.cash_change == 0:
        return '逆回购这一行缺少发生金额，无法计算利息。'
    if stated is not None and (row.cash_change < 0) != (stated == 'lend'):
        return (f'“{row.action}”是逆回购{"借出" if stated == "lend" else "购回"}，但发生金额 {money(row.cash_change)} 的方向相反'
                '（借出是资金减少，购回是资金增加）。这一行不参与配对，请核对金额。')
    return None


def _repo_currency(row: Row, v: dict) -> Optional[str]:
    """Why a 逆回购 leg cannot be booked as CNY interest, from its currency evidence; None when it can."""
    cash, trade, problem = currency_sides(row, v)
    if problem:
        return problem
    other = next((code for code in (cash, trade) if code not in (None, 'CNY')), None)
    if other:
        return f'逆回购以人民币结算，但这一行的币种是 {other}。请核对币种。'
    return None


def _repo_matching(lends: List[Row], backs: List[Row], edges: Dict[int, List[Row]]):
    """The pairs of one connected group, or None when the pairing (and so the interest) is ambiguous."""
    if len(lends) == 1 and len(backs) == 1:
        return [(lends[0], backs[0])]
    signature = {(lend.cash_change, lend.quantity, lend.turnover) for lend in lends}
    if len(signature) != 1 or len(backs) > len(lends):
        return None
    pairs, used = [], set()
    for back in sorted(backs, key=lambda r: r.order):
        lend = next((l for l in sorted(edges[back.row], key=lambda r: r.order) if id(l) not in used), None)
        if lend is None:
            return None
        used.add(id(lend))
        pairs.append((lend, back))
    return pairs


def _book_repo(match: Row, row: Row, ctx: Context):
    net = row.cash_change + match.cash_change
    match.pair_row, row.pair_row = row.row, match.row
    if net <= 0:
        row.set('review', f'逆回购净收益不是正数（{money(net)}），请核对两行的发生金额。', needs=['amount'])
        match.set('review', f'与第 {row.row} 行配对的逆回购净收益不是正数，请核对两行的发生金额。', needs=['amount'])
        return
    match.merged_into = row.row
    match.set('merged', f'逆回购借出，与第 {row.row} 行的购回合并为利息 {money(net)}')
    match.include = False
    row.notes.append(f'逆回购：第 {match.row} 行借出 {money(match.cash_change)}，本行购回 {money(row.cash_change)}，'
                     f'净收益 {money(net)} 记为利息')
    label = row.name or row.code
    event = _event(row, ctx, ':repo', kind='interest', currency='CNY', amount=net, note=f'逆回购 {label} 净收益'[:240])
    if event:
        row.events = [event]


# -- the system's own CSV template ---------------------------------------------

def _template_instrument(raw: dict, known=()) -> Optional[str]:
    """The checks of broker rows for a template row's ticker and currency: a valid code of its exchange, a trade
    in the market's currency, an A-share dividend or fee in CNY. Rewrites ``raw['ticker']`` as the ledger spells
    that listing (``codes.held_spelling``)."""
    if not raw.get('ticker'):
        return None
    try:
        ticker = raw['ticker'] = codes.held_spelling(codes.check_ticker(raw['ticker']), known)
    except LedgerError as exc:
        return f'代码 {raw["ticker"][:30]} 无效（{exc}）。请修改文件后重新导入。'
    market = codes.MARKETS[ticker.split(':')[0]]
    unit, kind = raw.get('currency'), raw.get('kind')
    if kind in ('buy', 'sell') and unit and unit != market:
        return f'{ticker} 以 {market} 交易，但这一行的币种是 {unit}。请修改文件后重新导入。'
    if kind in ('dividend', 'fee') and ticker.startswith(('SHA:', 'SHE:')) and unit and unit != 'CNY':
        return f'{ticker} 是 A 股，分红和费用以人民币结算，但这一行的币种是 {unit}。请修改文件后重新导入。'
    return None


TEMPLATE_MONEY = {'price': 'currency', 'fee': 'currency', 'amount': 'currency', 'received_amount': 'to_currency'}
TEMPLATE_UNPRICED = ('split', 'reverse', 'opening')


def _template_currency(raw: dict) -> Optional[str]:
    """A template row states its currency, and a symbol in a money cell (US$100) must name the same one."""
    if raw.get('kind') not in TEMPLATE_UNPRICED and not raw.get('currency'):
        return '模板行缺少币种：金额的币种不能默认为人民币。请修改文件后重新导入。'
    for name, column in TEMPLATE_MONEY.items():
        symbol = cell_currency(raw.get(name))
        expected = raw.get(column)
        if symbol is None or symbol == '$' and expected in ('USD', 'HKD'):
            continue
        if symbol != expected:
            return (f'{name}“{clean(raw.get(name))[:30]}”标明的币种是 {"$（美元还是港币？）" if symbol == "$" else symbol}，'
                    f'但这一行的{column}是 {expected or "空"}。请修改文件后重新导入。')
    return None


def _template_numbers(raw: dict, comma: str = 'thousands', native=frozenset()) -> Optional[str]:
    """Read the template's numbers with the parser of broker rows and edits (``values.bounded``): no
    scientific notation, at most 10 decimal places, within ``LIMITS``. Rewrites ``raw`` with exact text.
    ``native``: the fields whose cells held native workbook numbers."""
    for name, label in EVENT_NUMBERS.items():
        if name not in raw:
            continue
        try:
            number = bounded(raw[name], name, 'thousands' if name in native else comma)
        except ValueError as exc:
            return f'{name}（{label}）：{exc}'
        if number is None:
            del raw[name]
        else:
            raw[name] = text(number)
    return None


def interpret_template(table, mapping: dict, ctx: Context, edits: Dict[str, dict]) -> List[Row]:
    header = table.header
    rows: List[Row] = []
    columns = {name: mapping.get(name) for name in TEMPLATE_COLUMNS}
    index = columns.get('external_id')
    seen = Counter(clean(cells[index]) for _number, cells in table.data() if index is not None and index < len(cells)
                   and clean(cells[index]))
    for index, (number, cells) in enumerate(table.data()):
        valid, problems = checked_edits(number, edits.get(str(number), {}), 'template')
        row = Row(row=number, cells=list(cells), index=index, edits=valid, edit_problems=problems, category='template')
        rows.append(row)
        raw = {name: clean(cells[i]) for name, i in columns.items() if i is not None and i < len(cells) and clean(cells[i])}
        row.raw = {header[i] if i < len(header) else str(i): cells[i] for i in range(len(cells))}
        row.action, row.date = raw.get('kind', ''), raw.get('date')
        row.ticker, row.currency = raw.get('ticker'), raw.get('currency')
        row.external_id = raw.get('external_id')
        if refuse_stored_edits(row):
            continue
        if row.edits.get('kind') == 'skip':
            row.set('skipped', '你选择了跳过这一行')
            continue
        if not row.external_id:
            row.set('review', '模板行缺少编号（external_id 列）：没有编号就无法识别重复导入。请修改文件后重新导入。'
                    + TEMPLATE_NOT_HERE)
            continue
        if seen[row.external_id] > 1:
            row.set('review', f'编号（external_id）“{row.external_id[:60]}” 在文件里出现了 {seen[row.external_id]} 次：'
                              '同一个编号只能对应一条记录。请修改文件后重新导入，或把多余的行设为跳过。' + TEMPLATE_NOT_HERE)
            continue
        if raw.get('kind') in ('opening', 'reverse'):
            row.set('review', '期初和冲销请在账本里用各自的预览记录，不能通过导入。' + TEMPLATE_NOT_HERE)
            continue
        if raw.get('account', ctx.account) != ctx.account:
            row.set('review', f'这一行的账户是“{raw.get("account")}”，与所选账户“{ctx.account}”不同。请修改文件，'
                              '或在导入时选择那个账户。' + TEMPLATE_NOT_HERE)
            continue
        raw['account'] = ctx.account
        raw['source'] = ctx.source
        if row.edits.get('fee') is not None:
            raw['fee'] = row.edits['fee']
        native = {name for name, i in columns.items() if i is not None and i in table.native(number)}
        problem = _template_currency(raw) or _template_numbers(raw, ctx.comma, native)
        found = None if problem else number_check(raw)
        if found and found[1] == 'fee' and raw.get('fee') is None:
            row.set('review', found[0], needs=['fee'])   # the one number a template row can be given here
            continue
        problem = problem or (found and found[0]) or _template_instrument(raw, ctx.known_tickers)
        if problem:
            row.set('review', problem + TEMPLATE_NOT_HERE)
            continue
        try:
            event = normalize(raw)
        except LedgerError as exc:
            row.set('review', f'账本无法接受这一行：{ledger_reason(exc)}。' + TEMPLATE_NOT_HERE)
            continue
        row.date, row.ticker = event['date'], event.get('ticker')
        row.events = [event]
        for name in ('quantity', 'price', 'fee'):
            if name in event:
                setattr(row, name, Decimal(event[name]))
        if 'amount' in event:
            row.cash_change = Decimal(event['amount'])
    _chronological(rows)
    return rows
