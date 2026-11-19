"""
市场数据模型定义
"""
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class Market(Enum):
    """交易市场"""
    A_SHARE = "a_share"      # A股（沪深）
    HK_STOCK = "hk_stock"    # 港股
    US_STOCK = "us_stock"    # 美股

    @property
    def currency(self) -> str:
        """Currency the market quotes and settles in (ISO 4217 code)."""
        return MARKET_CURRENCY[self.value]


# Prices, costs and market values of a position are in its market's currency.
MARKET_CURRENCY: Dict[str, str] = {
    "a_share": "CNY",
    "hk_stock": "HKD",
    "us_stock": "USD",
}

CURRENCY_SYMBOLS: Dict[str, str] = {"CNY": "¥", "HKD": "HK$", "USD": "$"}

# The legacy portfolio stores one cash figure with no currency. Every screen and the CLI have
# always shown it as ¥, so it is treated as CNY.
CASH_CURRENCY = "CNY"


def currency_symbol(currency: str) -> str:
    """Display prefix for a currency code; unknown codes are shown as the code itself."""
    return CURRENCY_SYMBOLS.get(currency, f"{currency} ")


class OrderSide(Enum):
    """交易方向"""
    BUY = "buy"
    SELL = "sell"


class PositionSide(Enum):
    """持仓方向"""
    LONG = "long"
    SHORT = "short"


@dataclass
class Stock:
    """股票基本信息"""
    symbol: str              # 股票代码
    name: str                # 股票名称
    market: Market           # 所属市场

    def __str__(self):
        return f"{self.name}({self.symbol})"


# What a quote provider sends for a value it does not have (Eastmoney's "-" before the open and for a
# suspended stock, AKShare's "-" or NaN, a field Yahoo leaves out): the value is missing, never 0.
MISSING_MARKERS = frozenset({'', '-', '--', '---', 'n/a', 'na', 'none', 'null', 'nan'})


def provider_number(value: Any) -> Optional[float]:
    """A provider's number, ``None`` when it is missing (``None``, a ``MISSING_MARKERS`` text, NaN, not a number)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().lower() in MISSING_MARKERS:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def provider_count(value: Any) -> Optional[int]:
    """A provider's volume as a whole number, ``None`` when it is missing (a real 0 stays 0)."""
    number = provider_number(value)
    return int(number) if number is not None else None


def provider_price(value: Any) -> Optional[float]:
    """A provider's price: ``None`` when it is missing or not positive (nothing trades at 0)."""
    number = provider_number(value)
    return number if number is not None and number > 0 else None


@dataclass
class Quote:
    """实时行情: a value the provider did not give is ``None`` (不可用), never 0."""
    stock: Stock
    price: Optional[float]           # 当前价
    open: Optional[float]            # 开盘价
    high: Optional[float]            # 最高价
    low: Optional[float]             # 最低价
    prev_close: Optional[float]      # 昨收价
    volume: Optional[int]            # 成交量
    amount: Optional[float]          # 成交额
    timestamp: datetime      # 更新时间
    turnover: Optional[float] = None
    pe: Optional[float] = None

    @property
    def change(self) -> Optional[float]:
        """涨跌额; ``None`` without a price or a previous close."""
        if self.price is None or not self.prev_close:
            return None
        return self.price - self.prev_close

    @property
    def change_pct(self) -> Optional[float]:
        """涨跌幅（百分比）; ``None`` without a price or a previous close."""
        if self.price is None or not self.prev_close:
            return None
        return (self.price - self.prev_close) / self.prev_close * 100

    @property
    def amplitude(self) -> Optional[float]:
        """振幅（百分比）; ``None`` without the high, the low or a previous close."""
        if self.high is None or self.low is None or not self.prev_close:
            return None
        return (self.high - self.low) / self.prev_close * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.stock.symbol,
            "name": self.stock.name,
            "price": self.price,
            "change_pct": f"{self.change_pct:+.2f}%" if self.change_pct is not None else None,
            "volume": self.volume,
            "amount": self.amount,
            "turnover": self.turnover,
            "pe": self.pe,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class Position:
    """持仓信息"""
    stock: Stock
    quantity: int            # 持仓数量
    available_qty: int       # 可卖数量（T+1 限制）
    cost_price: float        # 成本价
    current_price: float     # 当前价
    side: PositionSide = PositionSide.LONG

    @property
    def currency(self) -> str:
        """Currency of this position's prices and amounts (from its market)."""
        return self.stock.market.currency

    @property
    def market_value(self) -> float:
        """市值（以本持仓的币种计）"""
        return self.quantity * self.current_price

    @property
    def cost_value(self) -> float:
        """成本"""
        return self.quantity * self.cost_price

    @property
    def profit(self) -> float:
        """盈亏金额"""
        return self.market_value - self.cost_value

    @property
    def profit_pct(self) -> float:
        """盈亏比例（百分比）"""
        if self.cost_value == 0:
            return 0.0
        return (self.market_value - self.cost_value) / self.cost_value * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.stock.symbol,
            "name": self.stock.name,
            "market": self.stock.market.value,
            "currency": self.currency,
            "quantity": self.quantity,
            "available_qty": self.available_qty,
            "cost_price": self.cost_price,
            "current_price": self.current_price,
            "market_value": self.market_value,
            "profit": self.profit,
            "profit_pct": f"{self.profit_pct:+.2f}%"
        }


@dataclass
class Portfolio:
    """投资组合

    Positions can be in different currencies (CNY, HKD, USD) and this model has no exchange
    rates, so amounts are only ever added up within one currency: see ``totals_by_currency``.

    The single-number totals (``total_market_value``, ``total_assets``, ``total_profit``) are
    kept for existing callers. They are numbers only while the whole portfolio is in one
    currency (``currency`` says which). When it holds more than one, they are ``None`` and
    ``mixed_currency`` is True. ``None`` was chosen over "the CNY part only" because a partial
    figure labelled "total" would still be read as the whole portfolio, while a missing one
    cannot be misread.
    """
    positions: List[Position] = field(default_factory=list)
    cash: float = 0.0        # 现金（CNY，见 CASH_CURRENCY）

    @property
    def currencies(self) -> List[str]:
        """Currencies present, CNY first. Cash counts as CNY when it is non-zero."""
        present = {p.currency for p in self.positions}
        if self.cash or not present:
            present.add(CASH_CURRENCY)
        known = [c for c in MARKET_CURRENCY.values() if c in present]
        return known + sorted(present - set(known))

    @property
    def mixed_currency(self) -> bool:
        """True when amounts in more than one currency are held."""
        return len(self.currencies) > 1

    @property
    def currency(self) -> Optional[str]:
        """Currency of the single-number totals; None when the portfolio is mixed."""
        currencies = self.currencies
        return currencies[0] if len(currencies) == 1 else None

    @property
    def totals_by_currency(self) -> Dict[str, Dict[str, Any]]:
        """Totals per currency, never converted or added across currencies.

        ``{currency: {positions, market_value, cost_value, profit, profit_pct, cash,
        total_assets}}``. ``profit_pct`` is profit over cost in percent, 0.0 when there is no
        cost. Cash appears only under CNY.
        """
        totals: Dict[str, Dict[str, Any]] = {}
        for currency in self.currencies:
            held = [p for p in self.positions if p.currency == currency]
            market_value = sum(p.market_value for p in held)
            cost_value = sum(p.cost_value for p in held)
            cash = self.cash if currency == CASH_CURRENCY else 0.0
            totals[currency] = {
                "positions": len(held),
                "market_value": market_value,
                "cost_value": cost_value,
                "profit": market_value - cost_value,
                "profit_pct": (market_value - cost_value) / cost_value * 100 if cost_value else 0.0,
                "cash": cash,
                "total_assets": market_value + cash,
            }
        return totals

    def _single_total(self, key: str) -> Optional[float]:
        currency = self.currency
        return None if currency is None else self.totals_by_currency[currency][key]

    @property
    def total_market_value(self) -> Optional[float]:
        """总市值；多币种时为 None（见类说明）"""
        return self._single_total("market_value")

    @property
    def total_assets(self) -> Optional[float]:
        """总资产；多币种时为 None（见类说明）"""
        return self._single_total("total_assets")

    @property
    def total_profit(self) -> Optional[float]:
        """总盈亏；多币种时为 None（见类说明）"""
        return self._single_total("profit")

    def to_summary(self) -> str:
        """生成持仓摘要（金额带各自币种符号，多币种时按币种分列）"""
        totals = self.totals_by_currency
        if list(totals) == [CASH_CURRENCY]:
            cny = totals[CASH_CURRENCY]
            lines = [
                f"【持仓概览】",
                f"总资产: ¥{cny['total_assets']:,.2f}",
                f"持仓市值: ¥{cny['market_value']:,.2f}",
                f"可用现金: ¥{cny['cash']:,.2f}",
                f"总盈亏: ¥{cny['profit']:+,.2f}",
            ]
        else:
            lines = ["【持仓概览】（按币种分列，未换汇，不同币种的金额不能相加）"]
            for currency, row in totals.items():
                sym = currency_symbol(currency)
                parts = [
                    f"持仓市值 {sym}{row['market_value']:,.2f}",
                    f"盈亏 {sym}{row['profit']:+,.2f} ({row['profit_pct']:+.2f}%)",
                ]
                if currency == CASH_CURRENCY:
                    parts.append(f"可用现金 {sym}{row['cash']:,.2f}")
                    parts.append(f"总资产 {sym}{row['total_assets']:,.2f}")
                lines.append(f"{currency}: " + " | ".join(parts))

        lines += [f"", f"【持仓明细】"]

        for p in self.positions:
            sym = currency_symbol(p.currency)
            lines.append(
                f"  {p.stock.name}({p.stock.symbol}): "
                f"{p.quantity}股 @ {sym}{p.cost_price:.2f} → {sym}{p.current_price:.2f} "
                f"| 盈亏: {p.profit_pct:+.2f}% ({sym}{p.profit:+,.2f})"
            )

        if not self.positions:
            lines.append("  (空仓)")

        return "\n".join(lines)


@dataclass
class TradeOrder:
    """交易订单"""
    stock: Stock
    side: OrderSide
    quantity: int
    price: Optional[float] = None  # None 表示市价单
    order_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "pending"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.stock.symbol,
            "name": self.stock.name,
            "side": self.side.value,
            "quantity": self.quantity,
            "price": self.price,
            "status": self.status,
            "created_at": self.created_at.isoformat()
        }
