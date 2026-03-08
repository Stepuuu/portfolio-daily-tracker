"""
策略基类
灵感: backtrader 的 Strategy + vnpy 的 CtaTemplate

策略生命周期:
  on_init()   → 初始化, 添加因子/参数
  on_start()  → 回测开始
  on_bar()    → 每个 bar 被调用 (主逻辑)
  on_order()  → 委托状态变化
  on_trade()  → 成交回调
  on_stop()   → 回测结束
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class Order:
    """委托单"""
    order_id: str
    symbol: str
    direction: str       # "buy" / "sell"
    order_type: str      # "market" / "limit" / "stop"
    quantity: float
    price: float         # 限价/触发价 (市价单填0)
    status: str = "pending"  # pending/filled/cancelled/rejected
    filled_qty: float = 0.0
    filled_price: float = 0.0
    commission: float = 0.0
    timestamp: Optional[Any] = None
    slippage: float = 0.0

    @property
    def is_buy(self) -> bool:
        return self.direction == "buy"

    @property
    def is_sell(self) -> bool:
        return self.direction == "sell"


@dataclass
class Trade:
    """成交记录"""
    trade_id: str
    order_id: str
    symbol: str
    direction: str
    quantity: float
    price: float
    commission: float
    timestamp: Any
    pnl: float = 0.0     # 本次成交带来的盈亏 (平仓时计算)


@dataclass
class Position:
    """持仓"""
    symbol: str
    quantity: float = 0.0       # 持仓数量 (正=多头)
    avg_cost: float = 0.0       # 平均成本价
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def market_value(self) -> float:
        return self.quantity * self.avg_cost

    def update_price(self, current_price: float):
        """更新浮盈"""
        self.unrealized_pnl = (current_price - self.avg_cost) * self.quantity


class Strategy(ABC):
    """
    策略抽象基类
    
    子类需实现 on_bar() 方法.
    通过 self.buy() / self.sell() / self.close_position() 下单.
    通过 self.data 访问当前价格数据.
    """

    # 策略名称 (子类可覆盖)
    name: str = "AbstractStrategy"

    # 参数字典 (可被外部优化器覆盖)
    parameters: Dict[str, Any] = {}

    def __init__(self):
        import copy
        self.parameters = copy.deepcopy(self.__class__.parameters)
        self._engine = None       # 由引擎注入
        self._data = None         # 当前 DataFeed
        self._all_data: Dict = {}  # 多标的 DataFeed 字典
        self._orders: List[Order] = []
        self._trades: List[Trade] = []
        self._log_messages: List[str] = []

    # ------------------------------------------------------------------ #
    #  引擎注入 (不直接调用)
    # ------------------------------------------------------------------ #

    def _set_engine(self, engine):
        self._engine = engine

    def _set_data(self, data_feed, all_feeds: Optional[Dict] = None):
        self._data = data_feed
        if all_feeds:
            self._all_data = all_feeds

    # ------------------------------------------------------------------ #
    #  生命周期钩子 (子类按需覆盖)
    # ------------------------------------------------------------------ #

    def on_init(self):
        """初始化, 可在此设置参数、预热期等"""
        pass

    def on_start(self):
        """回测开始"""
        pass

    @abstractmethod
    def on_bar(self):
        """
        每个 bar 被调用 (主逻辑实现这里).
        通过 self.data.close() 获取价格, self.buy() / self.sell() 下单.
        """
        pass

    def on_order(self, order: Order):
        """委托状态变化回调"""
        pass

    def on_trade(self, trade: Trade):
        """成交回调"""
        pass

    def on_stop(self):
        """回测结束"""
        pass

    # ------------------------------------------------------------------ #
    #  下单接口 (委托引擎执行)
    # ------------------------------------------------------------------ #

    def buy(
        self,
        symbol: Optional[str] = None,
        quantity: Optional[float] = None,
        price: float = 0.0,
        order_type: str = "market",
        pct_cash: Optional[float] = None,
    ) -> Optional[Order]:
        """
        买入.
        
        Args:
            symbol: 标的代码, None 则使用主 data 的 symbol
            quantity: 买入数量 (股/手)
            price: 限价单价格
            order_type: "market" 或 "limit"
            pct_cash: 使用可用资金的百分比 (0~1), 与 quantity 二选一
        Returns:
            Order 对象, 回测中会在下一根 bar 撮合
        """
        if self._engine is None:
            return None
        return self._engine.submit_order(
            strategy=self,
            symbol=symbol or self._data.symbol,
            direction="buy",
            quantity=quantity,
            price=price,
            order_type=order_type,
            pct_cash=pct_cash,
        )

    def sell(
        self,
        symbol: Optional[str] = None,
        quantity: Optional[float] = None,
        price: float = 0.0,
        order_type: str = "market",
    ) -> Optional[Order]:
        """卖出"""
        if self._engine is None:
            return None
        return self._engine.submit_order(
            strategy=self,
            symbol=symbol or self._data.symbol,
            direction="sell",
            quantity=quantity,
            price=price,
            order_type=order_type,
        )

    def close_position(self, symbol: Optional[str] = None) -> Optional[Order]:
        """清仓"""
        if self._engine is None:
            return None
        sym = symbol or self._data.symbol
        pos = self.get_position(sym)
        if pos and pos.quantity > 0:
            return self.sell(sym, quantity=pos.quantity)
        return None

    # ------------------------------------------------------------------ #
    #  信息查询接口
    # ------------------------------------------------------------------ #

    @property
    def data(self):
        """当前主 DataFeed"""
        return self._data

    def get_position(self, symbol: Optional[str] = None) -> Optional[Position]:
        """获取某标的持仓"""
        if self._engine is None:
            return None
        sym = symbol or (self._data.symbol if self._data else "")
        return self._engine.get_position(sym)

    @property
    def cash(self) -> float:
        """可用资金"""
        if self._engine is None:
            return 0.0
        return self._engine.broker.cash

    @property
    def portfolio_value(self) -> float:
        """总资产 (现金 + 持仓市值)"""
        if self._engine is None:
            return 0.0
        return self._engine.broker.total_value

    def log(self, msg: str):
        """策略日志"""
        timestamp = ""
        if self._data and self._data.current_bar:
            timestamp = str(self._data.current_bar.date)
        full_msg = f"[{self.name}] {timestamp} {msg}"
        self._log_messages.append(full_msg)
        logger.info(full_msg)

    # ------------------------------------------------------------------ #
    #  因子快捷方法
    # ------------------------------------------------------------------ #

    def sma(self, period: int, col: str = "close") -> float:
        """当前 SMA 值"""
        series = self._data.get_series(col, period + 1)
        if len(series) < period:
            return float("nan")
        return float(series.rolling(period).mean().iloc[-1])

    def ema(self, period: int, col: str = "close") -> float:
        """当前 EMA 值"""
        series = self._data.get_series(col, period * 3)
        if len(series) < period:
            return float("nan")
        return float(series.ewm(span=period, adjust=False).mean().iloc[-1])

    def rsi(self, period: int = 14) -> float:
        """当前 RSI 值"""
        from backtesting.factors.technical import TechnicalFactors
        series = self._data.get_series("close", period * 3)
        if len(series) < period:
            return float("nan")
        return float(TechnicalFactors.rsi(series, period).iloc[-1])
