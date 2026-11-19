"""Market rules the simulated broker needs, chosen from the instrument (and the date).

Pure functions, no I/O. Every rule is derived from the canonical instrument,
never from the symbol text as typed: ``listing()`` resolves the symbol once
through ``data.symbols.resolve()`` (cached per symbol text), so SH600519,
SH:600519, SSE:600519, 600519.SH and SHA:600519 get the same lots, limits,
T+1 and fees, and so do 0700, 0700.HK, HKEX:00700 and HKG:00700.

A-share forms that resolve() does not route yet keep their board rules from
the text: BSE codes (4xxxxx, 8xxxxx, 920xxx, BJ:, .BJ), LOFs, listed REITs and
closed-end funds (LOF:, REIT:, SSE 50x/52x, SZSE 16x/18x), and US symbols
written with another US exchange prefix (ARCA:, BATS:). Any other symbol that
does not resolve raises ``SymbolError``: there is no fallback to US rules. An
explicitly configured market (BrokerConfig.market) still wins over the symbol.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from backtesting.data import symbols as _symbols
from backtesting.data.symbols import SymbolError

A_SHARE, HK, US = "a_share", "hk", "us"
STOCK, FUND, INDEX = "stock", "fund", "index"

_US_PREFIXES = ("NASDAQ:", "NYSE:", "AMEX:", "ARCA:", "BATS:", "US:")
_A_PREFIXES = ("SHA:", "SHE:", "BJ:", "ETF:", "LOF:", "REIT:", "IDX:")
_A_SUFFIXES = (".SS", ".SZ", ".SH", ".BJ")
_EXCHANGES = {"SHA": "SSE", "SHE": "SZSE", "HKG": "HKEX", "US": "US"}
_MARKETS = {"CN": A_SHARE, "HK": HK, "US": US}
_KINDS = {_symbols.ETF: FUND, _symbols.INDEX: INDEX}


@dataclass(frozen=True)
class Listing:
    """The instrument behind a symbol, as far as the trading rules need it."""
    ticker: str      # canonical ticker (SHA:600519), or the upper-cased text of a form resolve() does not route
    market: str      # a_share / hk / us
    exchange: str    # SSE / SZSE / BSE / HKEX / US; "" when not known
    code: str        # 600519 / 00700 / AAPL / 000300
    kind: str        # stock / fund / index


def listing(symbol) -> Listing:
    """The canonical instrument of `symbol` (see the module docstring).

    Raises SymbolError when the symbol cannot be resolved to a market.
    """
    if isinstance(symbol, _symbols.Symbol):
        symbol = symbol.ticker
    return _listing(str(symbol).strip())


@lru_cache(maxsize=4096)
def _listing(text: str) -> Listing:
    try:
        sym = _symbols.resolve(text)
    except SymbolError as exc:
        found = _unrouted_listing(text)
        if found is None:
            raise SymbolError(
                f"无法确定 {text!r} 属于哪个市场, 不能套用整手、涨跌停、T+1 和费用规则 ({exc})") from None
        return found
    return Listing(sym.ticker, _MARKETS[sym.market], _EXCHANGES.get(sym.exchange, ""), sym.code,
                   _KINDS.get(sym.kind, STOCK))


def _unrouted_listing(text: str) -> Optional[Listing]:
    """Listing of an A-share BSE/fund form or a US exchange prefix that resolve() rejects, else None."""
    value = text.upper()
    if value.startswith(_US_PREFIXES):
        code = value.split(":", 1)[1].strip()
        return Listing(value, US, "US", code, STOCK) if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", code) else None
    if not re.fullmatch(r"(?:(?:SHA|SHE|BJ|LOF|REIT):)?\d{6}(?:\.(?:SS|SH|SZ|BJ))?", value):
        return None
    if value.startswith(("SHA:", "SHE:")) and value.endswith(_A_SUFFIXES):
        return None
    code, exchange = _digits(value), _text_exchange(value)
    if exchange == "BSE" or (exchange == "" and (code.startswith(("4", "8")) or code.startswith("920"))):
        return Listing(value, A_SHARE, "BSE", code, STOCK)
    if _text_is_fund(value, code, exchange):
        return Listing(value, A_SHARE, exchange, code, FUND)
    return None                     # index ranges, B shares, codes on the wrong exchange: not guessed


def _info(symbol, market: str = "auto") -> Listing:
    """listing(symbol); with an explicit market, an unresolvable symbol is taken as a stock of that market."""
    if market in ("auto", "", None):
        return listing(symbol)
    try:
        return listing(symbol)
    except SymbolError:
        text = str(symbol).strip().upper()
        return Listing(text, market, "", _digits(text), STOCK)


def listing_key(symbol, market: str = "auto") -> str:
    """One key per listed instrument, whatever form the symbol was written in:
    600000, SHA:600000 and 600000.SH all give SHA:600000 (the canonical ticker; the
    upper-cased text for a form resolve() does not route)."""
    return _info(symbol, market).ticker


def market_for(symbol: str, configured: str = "auto") -> str:
    """a_share / hk / us. An explicit configured market wins over the symbol."""
    if configured != "auto":
        return configured
    return listing(symbol).market


def _digits(symbol: str) -> str:
    return "".join(re.findall(r"\d", str(symbol)))


def _text_exchange(value: str) -> str:
    """SSE / SZSE / BSE when the text names one, else ''."""
    if value.startswith("SHA:") or value.endswith((".SS", ".SH")):
        return "SSE"
    if value.startswith("SHE:") or value.endswith(".SZ"):
        return "SZSE"
    if value.startswith("BJ:") or value.endswith(".BJ"):
        return "BSE"
    return ""


def _text_is_fund(value: str, code: str, exchange: str) -> bool:
    """Exchange-traded fund code (ETF, LOF, listed REIT, closed-end fund) on SSE/SZSE.

    - symbols written with the ETF:, LOF: or REIT: prefixes;
    - SSE codes starting with 5 (50x closed-end funds and LOFs, 501/502 LOFs,
      508 public REITs, 51x/52x/56x ETFs, 588/589 STAR-market ETFs), unless the
      symbol names another exchange (SHE:, .SZ, BJ:);
    - SZSE codes starting with 15, 16 or 18 (159 ETFs, 16x LOFs, 180 public
      REITs, 184 closed-end funds), unless the symbol names SSE (SHA:, .SS, .SH),
      where 15x/16x/18x are bond codes.
    """
    if value.startswith(("ETF:", "LOF:", "REIT:")):
        return True
    if len(code) != 6:
        return False
    if exchange not in ("SZSE", "BSE") and code.startswith("5"):
        return True
    if exchange not in ("SSE", "BSE") and code.startswith(("15", "16", "18")):
        return True
    return False


def _exchange(symbol, market: str = "auto") -> str:
    """SSE / SZSE / BSE of an A-share instrument, else ''."""
    exchange = _info(symbol, market).exchange
    return exchange if exchange in ("SSE", "SZSE", "BSE") else ""


def is_a_share_fund(symbol: str, market: str = "auto") -> bool:
    """Exchange-traded fund (ETF, LOF, listed REIT, closed-end fund) on SSE/SZSE."""
    info = _info(symbol, market)
    return info.market == A_SHARE and info.kind == FUND


def instrument_kind(symbol: str, market: str = "") -> str:
    """stock / fund / index. Indices of every market are told apart; among
    tradable instruments only A-share funds are (HK and US ETFs resolve as
    stocks, so HK ETF stamp-duty relief is not modelled)."""
    info = _info(symbol, market or "auto")
    if info.kind == INDEX:
        return INDEX
    market = market or info.market
    if market != A_SHARE:
        return STOCK
    return FUND if info.kind == FUND else STOCK


def price_tick(symbol: str, market: str = "auto") -> Decimal:
    """Quote tick of an SSE/SZSE instrument: 0.001 for funds, 0.01 otherwise.

    Fund rules as in _text_is_fund (per the SSE and SZSE fund trading rules).
    Everything else, including all stocks and BSE codes, uses 0.01. Not covered:
    bonds, convertible bonds and repos (their 0.001 tick and different limit rules
    are not modelled) and B shares (not accepted).
    """
    return Decimal("0.001") if is_a_share_fund(symbol, market) else Decimal("0.01")


# ------------------------------------------------------------------ #
#  A-share boards
# ------------------------------------------------------------------ #

MAIN, CHINEXT, STAR, BSE = "main", "chinext", "star", "bse"


def a_share_board(symbol: str, market: str = "auto") -> str:
    """main / chinext / star / bse for an A-share stock code.

    STAR: SSE 688xxx/689xxx. ChiNext: SZSE 300xxx/301xxx. BSE: 4xxxxx, 8xxxxx
    (codes moved from the NEEQ select tier) and 920xxx (new BSE codes), or any
    symbol written BJ: / .BJ. Everything else is treated as a main-board code.
    """
    info = _info(symbol, market)
    digits = info.code if info.code.isdigit() else _digits(info.ticker)
    exchange = info.exchange if info.exchange in ("SSE", "SZSE", "BSE") else ""
    if exchange == "BSE":
        return BSE
    if len(digits) != 6:
        return MAIN
    if exchange == "" and (digits.startswith(("4", "8")) or digits.startswith("920")):
        return BSE
    if exchange in ("", "SSE") and digits.startswith(("688", "689")):
        return STAR
    if exchange in ("", "SZSE") and digits.startswith(("300", "301")):
        return CHINEXT
    return MAIN


# ------------------------------------------------------------------ #
#  Lots
# ------------------------------------------------------------------ #

_EPS = 1e-9


@dataclass(frozen=True)
class LotRule:
    """Buy quantities: `minimum`, then steps of `step` shares.

    Sell quantities (valid_sell) follow the exchange rule that a balance smaller
    than the trading unit is sold in one order (SSE/SZSE trading rules: 余额不足
    100 股的部分应当一次性申报卖出; STAR 200, BSE 100; from public rules, verify
    against the official text):

    - board lot (minimum == step: A-share main board, ChiNext, funds, HK's
      assumed lot, an explicit lot_size): whole lots; or the entire odd
      remainder (position mod lot), alone or together with whole lots; or the
      whole position. Selling part of the odd remainder is not allowed.
    - minimum then single shares (STAR 200+1, BSE 100+1): at least `minimum`
      shares in steps of `step`; a balance below `minimum` only as the whole
      position.
    - US (1+1): any whole number of shares.

    Never a fraction of a share, except the whole position. Not modelled: the
    HK odd-lot market (an odd lot is sold with the rest or alone, like A-shares).
    """
    minimum: int
    step: int
    assumed: bool = False
    note: str = ""

    def round_buy(self, quantity) -> int:
        wanted = int(math.floor(float(quantity) + _EPS))
        if wanted < self.minimum:
            return 0
        return self.minimum + (wanted - self.minimum) // self.step * self.step

    def _odd(self, position) -> int:
        """The part of `position` that only sells in one order with the rest of it."""
        held = int(math.floor(float(position) + _EPS))
        if self.minimum == self.step:
            return held % self.step
        return held if held < self.minimum else 0

    def valid_sell(self, quantity, position) -> bool:
        """Is selling `quantity` out of `position` allowed (see the class docstring)?"""
        q, held = float(quantity), float(position)
        if not q > _EPS or q > held + _EPS:
            return False
        if abs(q - held) <= _EPS:
            return True                                   # the whole position
        if abs(q - round(q)) > _EPS:
            return False                                  # no fractional shares
        whole = int(round(q))
        if whole >= self.minimum and (whole - self.minimum) % self.step == 0:
            return True                                   # whole trading units
        odd = self._odd(position)
        if self.minimum == self.step and odd and whole >= odd:
            return (whole - odd) % self.step == 0         # the entire odd remainder (+ whole lots)
        return False

    def round_sell(self, quantity, position):
        """Largest valid sell of at most `quantity` out of `position` (0 if none)."""
        if float(quantity) >= float(position) - _EPS:
            return position
        candidates = [self.round_buy(quantity)]
        odd = self._odd(position)
        if self.minimum == self.step and odd and float(quantity) + _EPS >= odd:
            candidates.append(odd + int(math.floor((float(quantity) - odd) / self.step + _EPS)) * self.step)
        valid = [q for q in candidates if q > 0 and self.valid_sell(q, position)]
        return max(valid, default=0)

    def partial_sell(self, cap, position, wanted):
        """Largest fill of at most `cap` for a valid sell of `wanted` out of
        `position` that leaves a valid rest (wanted - fill out of position - fill)."""
        if float(cap) >= float(wanted) - _EPS:
            return wanted
        candidates = {self.round_sell(cap, position), self.round_buy(cap)}
        if self.minimum > self.step:
            candidates.add(self.round_buy(min(float(cap), float(wanted) - self.minimum)))
        fits = [q for q in candidates
                if q > 0 and self.valid_sell(q, position) and self.valid_sell(float(wanted) - q, float(position) - q)]
        return max(fits, default=0)


HK_LOT_NOTE = "港股每手股数因股票而异（100 至数千股），默认按 100 股，请按实际每手股数设置"


def lot_rule(symbol: str, market: str = "auto", override: Optional[int] = None) -> LotRule:
    """Lot rule of a symbol. An explicit override is one fixed lot size.

    - A-share main board, ChiNext, funds: 100 shares (SSE/SZSE trading rules).
    - STAR (688/689): at least 200 shares, then 1-share steps (SSE STAR-market
      trading rules, 2019).
    - BSE: at least 100 shares, then 1-share steps (BSE trading rules, 2021).
    - HK: board lots differ per stock; 100 is assumed and flagged.
    - US: single shares.
    """
    if override is not None:
        return LotRule(int(override), int(override))
    market = market_for(symbol, market)
    if market == US:
        return LotRule(1, 1)
    if market == HK:
        return LotRule(100, 100, assumed=True, note=HK_LOT_NOTE)
    if instrument_kind(symbol, market) == STOCK:
        board = a_share_board(symbol, market)
        if board == STAR:
            return LotRule(200, 1)
        if board == BSE:
            return LotRule(100, 1)
    return LotRule(100, 100)


# ------------------------------------------------------------------ #
#  Daily price limits
# ------------------------------------------------------------------ #

# (effective from, daily limit as a fraction of the previous close), per board.
# From public rules, verify against the official notices:
#   main board / funds: SSE and SZSE 10% daily limit since 1996-12-16;
#   ChiNext: 10% from its 2009-10-30 opening, 20% from 2020-08-24 (ChiNext
#     registration reform, SZSE ChiNext trading rules revised 2020);
#   STAR: 20% from the 2019-07-22 opening (SSE STAR-market trading rules);
#   BSE: 30% (BSE trading rules, opening 2021-11-15; the NEEQ select tier the
#     8xxxxx/4xxxxx codes came from had 30% from 2020-07-27).
# Not modelled (stated in the result's assumptions):
#   - ST / *ST status (5% on the main board): unknown per date, so one-price
#     boards are detected by pattern instead (st_pattern_applies);
#   - limit-free days: the first days after a listing and some resumption days;
#   - fund-level exceptions, e.g. ETFs whose index is all STAR/ChiNext stocks (20%).
LIMIT_TABLE = {
    MAIN: [(date(1996, 12, 16), 0.10)],
    FUND: [(date(1996, 12, 16), 0.10)],
    CHINEXT: [(date(2009, 10, 30), 0.10), (date(2020, 8, 24), 0.20)],
    STAR: [(date(2019, 7, 22), 0.20)],
    BSE: [(date(2021, 11, 15), 0.30)],
}

# One-price bars (open = high = low = close) moving at least this much are
# treated as limit boards on 10% boards, where an ST stock's limit is 5%.
ONE_PRICE_LIMIT_MOVE = 0.048


def _as_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def limit_board(symbol: str, market: str = "auto") -> Optional[str]:
    """Key of LIMIT_TABLE for the symbol, or None when no daily limit applies."""
    market = market_for(symbol, market)
    if market != A_SHARE:
        return None
    kind = instrument_kind(symbol, market)
    if kind == INDEX:
        return None
    if kind == FUND:
        return FUND
    return a_share_board(symbol, market)


def price_limit_pct(symbol: str, when=None, market: str = "auto") -> Optional[float]:
    """Daily limit in force on `when` (the latest rule for an unknown date;
    the earliest rule before a board's first row). None: no limit."""
    board = limit_board(symbol, market)
    if board is None:
        return None
    rows = LIMIT_TABLE[board]
    day = _as_date(when)
    if day is None:
        return rows[-1][1]
    pct = rows[0][1]
    for start, value in rows:
        if day >= start:
            pct = value
    return pct


def st_pattern_applies(symbol: str, when=None, market: str = "auto") -> bool:
    """True for stocks on a 10% board, where an ST stock would have a 5% limit."""
    board = limit_board(symbol, market)
    return board in (MAIN, CHINEXT) and price_limit_pct(symbol, when, market) == 0.10
