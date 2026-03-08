"""
市场数据模型定义
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class Market(Enum):
    """交易市场"""
    A_SHARE = "a_share"      # A股（沪深）
    HK_STOCK = "hk_stock"    # 港股
    US_STOCK = "us_stock"    # 美股


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


@dataclass
class Quote:
    """实时行情"""
    stock: Stock
    price: float             # 当前价
    open: float              # 开盘价
    high: float              # 最高价
    low: float               # 最低价
    prev_close: float        # 昨收价
    volume: int              # 成交量
    amount: float            # 成交额
    timestamp: datetime      # 更新时间

    @property
    def change(self) -> float:
        """涨跌额"""
        return self.price - self.prev_close

    @property
    def change_pct(self) -> float:
        """涨跌幅（百分比）"""
        if self.prev_close == 0:
            return 0.0
        return (self.price - self.prev_close) / self.prev_close * 100

    @property
    def amplitude(self) -> float:
        """振幅（百分比）"""
        if self.prev_close == 0:
            return 0.0
        return (self.high - self.low) / self.prev_close * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.stock.symbol,
            "name": self.stock.name,
            "price": self.price,
            "change_pct": f"{self.change_pct:+.2f}%",
            "volume": self.volume,
            "amount": self.amount,
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
    def market_value(self) -> float:
        """市值"""
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
    """投资组合"""
    positions: List[Position] = field(default_factory=list)
    cash: float = 0.0        # 现金

    @property
    def total_market_value(self) -> float:
        """总市值"""
        return sum(p.market_value for p in self.positions)

    @property
    def total_assets(self) -> float:
        """总资产"""
        return self.total_market_value + self.cash

    @property
    def total_profit(self) -> float:
        """总盈亏"""
        return sum(p.profit for p in self.positions)

    def to_summary(self) -> str:
        """生成持仓摘要"""
        lines = [
            f"【持仓概览】",
            f"总资产: ¥{self.total_assets:,.2f}",
            f"持仓市值: ¥{self.total_market_value:,.2f}",
            f"可用现金: ¥{self.cash:,.2f}",
            f"总盈亏: ¥{self.total_profit:+,.2f}",
            f"",
            f"【持仓明细】"
        ]

        for p in self.positions:
            lines.append(
                f"  {p.stock.name}({p.stock.symbol}): "
                f"{p.quantity}股 @ ¥{p.cost_price:.2f} → ¥{p.current_price:.2f} "
                f"| 盈亏: {p.profit_pct:+.2f}% (¥{p.profit:+,.2f})"
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
