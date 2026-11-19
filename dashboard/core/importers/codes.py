"""Bare security code → ledger ticker (``EXCH:CODE``), currency and instrument kind.

Shared rule (mirrored by the instrument search in the frontend):

=============================  ===========================================
6-digit prefix                 result
=============================  ===========================================
60 68 50 51 52 53 56 58 11     SHA (stock / fund / ETF / bond)
00 30 15 16 18 12              SHE
204 / 1318                     逆回购, handled by the importer
4 8 92                         北交所: unsupported (the ledger has no BSE)
900 200 201                    B 股: unsupported
7                              申购 / 配售 code, not a listed security
≤5 digits                      HKG (see below)
letters                        US: NASDAQ or NYSE, chosen once, remembered
=============================  ===========================================

An exchange the file states (a suffix such as ``.N`` / ``.O`` / ``.SZ``, a qualified
``NYSE:X``, or the market column) wins over tickers remembered from the ledger or
an earlier choice; when they disagree the row needs review. So does a market column
that contradicts the code itself, in either direction: a 6-digit code on NASDAQ /
NYSE, a US symbol on 上海 / 深圳 / 港股, a numeric code on a US market. The market
column names the market first; a currency (港币) suggests 港股 only when the market
column says nothing.

B shares (``900xxx`` on Shanghai in USD, ``200xxx`` / ``201xxx`` on Shenzhen in HKD)
are one unsupported case whatever the spelling (bare, ``.SH`` / ``.SZ``, ``SHA:`` /
``SHE:``) and whatever the market or currency columns say: the ledger books SHA / SHE
in CNY only (``core.market.instruments``: ``ledger_currency_problem``).

A bare ``000001`` is ``SHE:000001``; indices never appear in trade files.

Short numeric codes are ambiguous in A-share broker files: a spreadsheet that
stored ``000001`` as a number shows ``1``. A short code is HK only when the row
says so (港股通 action, 港 market, HKD) or nothing in the file suggests A-shares;
with an A-share hint it is zero-padded; otherwise the row needs review with both
choices.

HK canonical form: if the ledger already uses the same listing in any spelling
(``HKG:0700`` vs ``HKG:00700``, compared by ``core.market.instruments.quote_key``) that
spelling is reused, otherwise 5 digits (``held_spelling``). Broker cells, template rows
and tickers the person types all go through it, so one position is never split into two
tickers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import unicodedata
from typing import Dict, Iterable, List, Optional

from core.ledger import MARKETS, LedgerError, instrument
from core.importers.values import clean
from core.market.instruments import quote_key

A_SHARE_PREFIXES = [
    ('60', 'SHA', 'stock'), ('68', 'SHA', 'stock'), ('50', 'SHA', 'fund'), ('51', 'SHA', 'etf'),
    # 52 / 53: newer Shanghai ETF ranges, seen in the exchange's ETF list in 2026.
    ('52', 'SHA', 'etf'), ('53', 'SHA', 'etf'),
    ('56', 'SHA', 'etf'), ('58', 'SHA', 'etf'), ('11', 'SHA', 'bond'),
    ('00', 'SHE', 'stock'), ('30', 'SHE', 'stock'), ('15', 'SHE', 'etf'), ('16', 'SHE', 'fund'),
    ('18', 'SHE', 'fund'), ('12', 'SHE', 'bond'),
]
REPO_PREFIXES = ('204', '1318')
BSE_PREFIXES = ('92', '4', '8')
B_SHARE_PREFIXES = ('900', '200', '201')

EXCHANGE_NAMES = {'SHA': '上交所', 'SHE': '深交所', 'HKG': '港股', 'NASDAQ': 'NASDAQ', 'NYSE': 'NYSE'}
MARKET_NAMES = {**EXCHANGE_NAMES, 'BSE': '北交所'}
US_EXCHANGES = ('NASDAQ', 'NYSE')
B_SHARE = ('B 股：账本暂不支持 B 股（沪市 B 股以美元、深市 B 股以港币交易，账本按人民币记录沪深证券）。')


@dataclass
class Resolution:
    status: str                       # ok | repo | unsupported | review
    code: str = ''                    # bare code after cleaning / padding
    ticker: Optional[str] = None
    currency: Optional[str] = None
    kind: Optional[str] = None        # stock | etf | fund | bond | repo | subscription | us
    message: Optional[str] = None     # why it is not ok (Chinese)
    note: Optional[str] = None        # what was inferred (Chinese)
    choices: List[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def bare(text) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', clean(text))).upper()


def market_hint(*, action: str = '', market: str = '', currency: str = '', shareholder: str = '') -> Optional[str]:
    """SHA / SHE / HKG / BSE / NASDAQ / NYSE from the row's other columns, or None. The action and market
    columns first; the currency (港币) and the shape of the 股东代码 only when they name no market."""
    text = unicodedata.normalize('NFKC', f'{action}|{market}').upper()
    if '港' in text or 'HK' in text or '香港' in text:
        return 'HKG'
    market = unicodedata.normalize('NFKC', market).upper()
    if 'NASDAQ' in market or '纳斯达克' in market:
        return 'NASDAQ'
    if 'NYSE' in market or '纽约证券交易所' in market or '纽交所' in market or '纽约证交所' in market:
        return 'NYSE'
    if any(word in market for word in ('北', 'BJ', '京')):
        return 'BSE'
    if any(word in market for word in ('深', 'SZ', 'SHE')):   # before SH: "SHENZHEN" contains "SH"
        return 'SHE'
    if any(word in market for word in ('沪', '上海', '上证', '上交', 'SH')):
        return 'SHA'
    if bare(currency) in {'HKD', '港币', '港元'}:
        return 'HKG'
    holder = bare(shareholder)
    if re.fullmatch(r'A\d{9}', holder):
        return 'SHA'
    if re.fullmatch(r'0\d{9}', holder):
        return 'SHE'
    return None


def held_spelling(ticker: str, known: Iterable[str] = ()) -> str:
    """The spelling of ``ticker``'s listing the ledger already uses (``HKG:700`` → ``HKG:00700`` when that is
    held), by the shared listing identity ``quote_key``; otherwise a Hong Kong code in 5 digits."""
    key = quote_key(ticker)
    for spelling in known:
        if quote_key(spelling) == key:
            return spelling
    return key if ticker.startswith('HKG:') else ticker


def hk_ticker(digits: str, known: Iterable[str] = ()) -> str:
    return held_spelling('HKG:' + str(int(digits)).zfill(5), known)


def _ok(code, ticker, kind, note=None) -> Resolution:
    return Resolution('ok', code=code, ticker=ticker, currency=MARKETS[ticker.split(':')[0]], kind=kind, note=note)


def _b_share(code: str) -> Optional[Resolution]:
    """The one answer for a B-share code, whatever the spelling and the other columns (module docstring)."""
    if re.fullmatch(r'\d{6}', code) and code.startswith(B_SHARE_PREFIXES):
        return Resolution('unsupported', code=code, kind='stock', message=B_SHARE)
    return None


def a_share(code: str, hint: Optional[str] = None, note: Optional[str] = None) -> Resolution:
    if code.startswith(REPO_PREFIXES):
        return Resolution('repo', code=code, currency='CNY', kind='repo', note='逆回购')
    if code.startswith(BSE_PREFIXES):
        return Resolution('unsupported', code=code, kind='stock', message='北交所证券：账本暂不支持北交所。')
    if _b_share(code):
        return _b_share(code)
    if code.startswith('7'):
        return Resolution('review', code=code, kind='subscription',
                          message=f'{code} 是申购或配售代码，不是上市证券代码。请选择对应的正式代码。')
    for prefix, exchange, kind in A_SHARE_PREFIXES:
        if code.startswith(prefix):
            if hint is not None and hint != exchange:   # the market column names another market
                return Resolution('review', code=code, kind=kind, choices=[f'{exchange}:{code}'],
                                  message=f'代码 {code} 属于{EXCHANGE_NAMES[exchange]}，但这一行的市场是'
                                          f'{MARKET_NAMES.get(hint, hint)}。请核对。')
            return _ok(code, f'{exchange}:{code}', kind, note)
    return Resolution('review', code=code, message=f'无法从代码 {code} 判断交易所。请选择正式代码。')


def _remembered_us(symbol: str, known: Iterable[str], aliases: Optional[Dict[str, str]]):
    """(ticker, where) the ledger or an earlier choice uses for a US symbol, or (None, None)."""
    candidates = [f'NASDAQ:{symbol}', f'NYSE:{symbol}']
    for ticker in known:
        if ticker in candidates:
            return ticker, '账本'
    remembered = (aliases or {}).get(f'US:{symbol}')
    if remembered in candidates:
        return remembered, '上次的选择'
    return None, None


def _explicit_conflict(symbol: str, explicit: str, remembered: str, where: str) -> Resolution:
    return Resolution('review', code=symbol, kind='us', choices=[explicit, remembered],
                      message=f'文件标明 {symbol} 在 {explicit.split(":")[0]} 交易，但{where}里是 {remembered}。'
                              '请核对后选择交易所。')


def us(symbol: str, known: Iterable[str] = (), aliases: Optional[Dict[str, str]] = None,
       exchange: Optional[str] = None) -> Resolution:
    """A US symbol. An exchange stated in the file (``exchange``) wins over the ledger and earlier
    choices; when they disagree the row needs review."""
    candidates = [f'NASDAQ:{symbol}', f'NYSE:{symbol}']
    try:
        for candidate in candidates:
            instrument(candidate)
    except LedgerError:
        return Resolution('review', code=symbol, message=f'无法识别的代码：{symbol}')
    remembered, where = _remembered_us(symbol, known, aliases)
    if exchange:
        explicit = f'{exchange}:{symbol}'
        if remembered and remembered != explicit:
            return _explicit_conflict(symbol, explicit, remembered, where)
        return _ok(symbol, explicit, 'us', '按文件标明的交易所')
    if remembered:
        return _ok(symbol, remembered, 'us', '沿用账本中已有的交易所' if where == '账本' else '按上次的选择')
    return Resolution('review', code=symbol, kind='us', choices=candidates,
                      message='美股代码需要选择交易所（NASDAQ 或 NYSE），选择后会记住。')


def _stated_exchange(raw: str, exchange: str, hint: Optional[str]) -> Optional[Resolution]:
    """Review when the row's market column (``hint``) names another exchange than a qualified / suffixed code."""
    if hint is None or hint == exchange:
        return None
    return Resolution('review', code=raw, message=f'代码写的是 {raw}（{MARKET_NAMES[exchange]}），但这一行的市场是'
                                                  f'{MARKET_NAMES.get(hint, hint)}。请核对后选择正式代码。')


def _checked_a_share(raw: str, code: str, exchange: str) -> Resolution:
    """``a_share`` for a code whose exchange the file states; a prefix of the other exchange needs review."""
    result = a_share(code, exchange)
    if result.status == 'review' and result.choices:
        result.message = f'代码写的是 {raw}，但 {code} 属于{EXCHANGE_NAMES[result.choices[0].split(":")[0]]}。请核对后选择正式代码。'
    return result


def resolve(text, *, hint: Optional[str] = None, numeric_cell: bool = False, file_has_a_codes: bool = False,
            short_code_market: Optional[str] = None, known: Iterable[str] = (),
            aliases: Optional[Dict[str, str]] = None) -> Resolution:
    """Resolve one code. ``hint`` comes from ``market_hint`` for the same row."""
    known = list(known)
    raw = bare(text)
    if not raw:
        return Resolution('review', message='缺少证券代码。')
    qualified = re.fullmatch(r'(SHA|SHE|HKG|NASDAQ|NYSE):(.+)', raw)
    if qualified:
        try:
            ticker = instrument(raw)
        except LedgerError:
            return Resolution('review', code=raw, message=f'无法识别的代码：{raw}')
        exchange, code = ticker.split(':', 1)
        if exchange in ('SHA', 'SHE'):
            # The same rules as a bare code: 6 digits whose prefix belongs to this exchange.
            if not re.fullmatch(r'\d{6}', code):
                return Resolution('review', code=raw, message=f'{raw}：A 股代码必须是 6 位数字。请核对或选择正式代码。')
            return _b_share(code) or _stated_exchange(raw, exchange, hint) or _checked_a_share(raw, code, exchange)
        if exchange == 'HKG':
            if not re.fullmatch(r'\d{1,5}', code):
                return Resolution('review', code=raw, message=f'{raw}：港股代码是 1 到 5 位数字。请核对或选择正式代码。')
            return _stated_exchange(raw, exchange, hint) or _ok(code, hk_ticker(code, known), 'stock')
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', code):
            return Resolution('review', code=raw, message=f'{raw}：美股代码以字母开头。请核对或选择正式代码。')
        remembered, where = _remembered_us(code, known, aliases)
        if hint in US_EXCHANGES and hint != exchange:
            return _explicit_conflict(code, ticker, f'{hint}:{code}', '交易市场列')
        if hint is not None and hint not in US_EXCHANGES:
            return _stated_exchange(raw, exchange, hint)
        if remembered and remembered != ticker:
            return _explicit_conflict(code, ticker, remembered, where)
        return _ok(code, ticker, 'us')
    suffixed = (re.fullmatch(r'(\d{1,6})\.(SH|SS|SZ|HK|BJ)', raw)
                or re.fullmatch(r'(SH|SZ|HK|BJ)(\d{1,6})', raw))
    if suffixed:
        first, second = suffixed.groups()
        digits, market = (first, second) if first.isdigit() else (second, first)
        if market == 'BJ':
            return Resolution('unsupported', code=digits, message='北交所证券：账本暂不支持北交所。')
        exchange = {'SH': 'SHA', 'SS': 'SHA', 'SZ': 'SHE', 'HK': 'HKG'}[market]
        if market != 'HK' and _b_share(digits.zfill(6)):
            return _b_share(digits.zfill(6))
        conflict = _stated_exchange(raw, exchange, hint)
        if conflict:
            return conflict
        if market == 'HK':
            if len(digits) > 5:
                return Resolution('review', code=raw, message=f'{raw}：港股代码是 1 到 5 位数字。请核对或选择正式代码。')
            return _ok(digits, hk_ticker(digits, known), 'stock')
        return _checked_a_share(raw, digits.zfill(6), exchange)
    us_suffixed = re.fullmatch(r'([A-Z][A-Z0-9.\-]{0,14})\.(US|O|OQ|N)', raw)
    us_hint = hint if hint in US_EXCHANGES else None
    if (us_suffixed or re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', raw)) and hint is not None and hint not in US_EXCHANGES:
        return Resolution('review', code=raw, kind='us', message=f'代码 {raw} 是美股代码，但这一行的市场是'
                                                                f'{MARKET_NAMES.get(hint, hint)}。请核对后选择正式代码。')
    if us_suffixed:
        symbol, suffix = us_suffixed.groups()
        stated = {'O': 'NASDAQ', 'OQ': 'NASDAQ', 'N': 'NYSE'}.get(suffix)
        if stated and us_hint and stated != us_hint:
            return _explicit_conflict(symbol, f'{stated}:{symbol}', f'{us_hint}:{symbol}', '交易市场列')
        return us(symbol, known, aliases, stated or us_hint)
    if raw.isdigit():
        if hint in US_EXCHANGES:
            return Resolution('review', code=raw, message=f'代码 {raw} 是数字代码，但这一行的市场是 {hint}。请核对后选择正式代码。')
        if len(raw) == 6:
            if _b_share(raw):
                return _b_share(raw)
            if hint == 'HKG':
                return Resolution('review', code=raw, message=f'代码 {raw} 是 6 位，但这一行看起来是港股。请核对。')
            return a_share(raw, hint)
        if len(raw) > 6:
            return Resolution('review', code=raw, message=f'代码 {raw} 的位数不对。')
        padded = raw.zfill(6)
        if hint == 'HKG' or short_code_market == 'HKG':
            return _ok(raw, hk_ticker(raw, known), 'stock')
        if hint in ('SHA', 'SHE', 'BSE') and short_code_market != 'A' and len(raw) == 5 and raw.startswith('0') \
                and not numeric_cell:
            # Written with its leading zeros, a 5-digit code is a Hong Kong one; an A-share code that lost zeros
            # in a number cell would have lost all of them.
            mainland = a_share(padded)
            return Resolution('review', code=raw,
                              choices=[hk_ticker(raw, known)] + ([mainland.ticker] if mainland.status == 'ok' else []),
                              message=f'代码 {raw} 是港股代码的写法（5 位，带前导零），但这一行的市场是'
                                      f'{MARKET_NAMES.get(hint, hint)}。请核对后选择正式代码。')
        if hint in ('SHA', 'SHE', 'BSE') or short_code_market == 'A':
            return a_share(padded, hint, note=f'代码补足前导零为 {padded}')
        if numeric_cell or file_has_a_codes:
            choices = [hk_ticker(raw, known)]
            padded_result = a_share(padded)
            if padded_result.status == 'ok':
                choices.insert(0, padded_result.ticker)
            return Resolution('review', code=raw, choices=choices,
                              message=f'代码只有 {len(raw)} 位：可能是丢了前导零的 A 股代码 {padded}，也可能是港股。请选择。')
        return _ok(raw, hk_ticker(raw, known), 'stock', '5 位以内的数字代码按港股处理')
    if re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', raw):
        return us(raw, known, aliases, us_hint)
    return Resolution('review', code=raw, message=f'无法识别的代码：{clean(text)}')


def lot_size(ticker: Optional[str]) -> Optional[int]:
    """Shares in one 手 when that is certain: 100 for A-share stocks and ETFs. None for
    bonds, funds, 港股 (the lot differs per stock) and US shares: a quantity in 手 then needs review."""
    if not ticker or ':' not in ticker:
        return None
    exchange, code = ticker.split(':', 1)
    if exchange not in ('SHA', 'SHE') or not re.fullmatch(r'\d{6}', code):
        return None
    result = a_share(code, exchange)
    return 100 if result.status == 'ok' and result.kind in ('stock', 'etf') else None


def whole_units(ticker: Optional[str]) -> bool:
    """Whether the listing trades in whole shares / 张 / 份 only (every SHA, SHE and HKG listing; US shares can
    be fractional): a fractional quantity of one is a misread number, never a holding."""
    return bool(ticker) and ticker.split(':', 1)[0] in ('SHA', 'SHE', 'HKG')


def check_ticker(text) -> str:
    """A ticker the person typed or chose: must be a valid ledger ticker. Whoever books it uses the ledger's
    spelling of the listing (``held_spelling``)."""
    ticker = instrument(text)
    exchange, code = ticker.split(':', 1)
    if exchange in ('SHA', 'SHE'):
        if not re.fullmatch(r'\d{6}', code):
            raise LedgerError('A 股代码必须是 6 位数字')
        result = a_share(code, exchange)
        if result.status != 'ok':
            raise LedgerError(result.message or '无法使用这个代码')
    if exchange == 'HKG' and not re.fullmatch(r'\d{1,5}', code):
        raise LedgerError('港股代码是 1 到 5 位数字')
    if exchange in ('NASDAQ', 'NYSE') and not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', code):
        raise LedgerError('美股代码以字母开头')
    return ticker
