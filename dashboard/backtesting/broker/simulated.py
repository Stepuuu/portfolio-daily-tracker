"""
模拟撮合经纪商
灵感: backtrader 的 BackBroker + rqalpha 的 sys_simulation Mod

支持:
  - 市价单: 下一根 bar 的开盘价成交 (符合"昨信号今开盘执行"逻辑)
  - 限价单: 下一根 bar 检查是否满足条件
  - 交易成本: 印花税 + 佣金 (A股默认)
  - 滑点模拟
  - 持仓管理
"""
import uuid
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from backtesting.strategies.base import Order, Trade, Position

logger = logging.getLogger(__name__)


@dataclass
class BrokerConfig:
    """经纪商配置"""
    # A股交易成本 (默认)
    commission_buy: float = 0.0003     # 买入佣金 0.03%
    commission_sell: float = 0.0013    # 卖出佣金 + 印花税 0.13%
    min_commission: float = 5.0        # 最低佣金 5元
    slippage_pct: float = 0.0002       # 滑点 0.02%
    lot_size: int = 100                # A股最小单位 100股
    allow_short: bool = False          # 是否允许做空 (A股默认不允许)


class SimulatedBroker:
    """
    模拟撮合经纪商
    
    持有账户状态, 处理委托, 模拟成交.
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        config: Optional[BrokerConfig] = None,
    ):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.config = config or BrokerConfig()

        self._positions: Dict[str, Position] = {}
        self._pending_orders: List[Order] = []
        self._filled_orders: List[Order] = []
        self._trades: List[Trade] = []
        self._last_prices: Dict[str, float] = {}   # 最新市价 (用于计算真实市值)
        self._buy_today: Dict[str, str] = {}        # symbol -> date, T+1 限制

        # 净值曲线记录
        self._equity_curve: List[Tuple] = []  # [(date, total_value)]

    # ------------------------------------------------------------------ #
    #  公开属性
    # ------------------------------------------------------------------ #

    @property
    def total_value(self) -> float:
        """总资产 = 现金 + 持仓市值 (按最新市价)"""
        mkt_value = 0.0
        for sym, pos in self._positions.items():
            price = self._last_prices.get(sym, pos.avg_cost)
            mkt_value += pos.quantity * price
        return self.cash + mkt_value

    @property
    def positions(self) -> Dict[str, Position]:
        return self._positions

    @property
    def trades(self) -> List[Trade]:
        return self._trades

    @property
    def equity_curve(self) -> List[Tuple]:
        return self._equity_curve

    # ------------------------------------------------------------------ #
    #  委托处理
    # ------------------------------------------------------------------ #

    def submit_order(self, order: Order) -> Order:
        """提交委托"""
        self._pending_orders.append(order)
        logger.debug(f"[Broker] 提交委托: {order}")
        return order

    def process_bar(self, symbol: str, bar) -> List[Trade]:
        """
        处理当前 bar 的挂单撮合.
        
        Args:
            symbol: 当前处理的标的
            bar:    当前 Bar 对象
        Returns:
            本 bar 成交的 Trade 列表
        """
        new_trades = []
        remaining = []

        for order in self._pending_orders:
            if order.symbol != symbol:
                remaining.append(order)
                continue

            trade = self._try_fill(order, bar)
            if trade:
                new_trades.append(trade)
                self._trades.append(trade)
                order.status = "filled"
                order.filled_qty = order.quantity
                order.filled_price = trade.price
                order.commission = trade.commission
                self._filled_orders.append(order)
            else:
                # 限价单还未成交, 保留
                if order.order_type == "limit":
                    remaining.append(order)
                else:
                    # 市价单当 bar 未成交则取消
                    order.status = "cancelled"
                    logger.warning(f"[Broker] 市价单无法成交, 取消: {order}")

        self._pending_orders = remaining
        return new_trades

    def update_positions_price(self, symbol: str, current_price: float):
        """更新持仓的当前市值"""
        self._last_prices[symbol] = current_price
        if symbol in self._positions:
            self._positions[symbol].update_price(current_price)

    def record_equity(self, date, market_prices: Dict[str, float]):
        """记录当日净值 (使用收盘价)"""
        self._last_prices.update(market_prices)
        mkt_value = sum(
            pos.quantity * market_prices.get(pos.symbol, pos.avg_cost)
            for pos in self._positions.values()
        )
        total = self.cash + mkt_value
        self._equity_curve.append((date, total, self.cash, mkt_value))

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _try_fill(self, order: Order, bar) -> Optional[Trade]:
        """尝试撮合"""
        cfg = self.config
        date_str = str(bar.date)[:10] if bar.date else ""

        # --- T+1 限制: 当日买入的股票不能当日卖出 ---
        if order.is_sell and not cfg.allow_short:
            buy_date = self._buy_today.get(order.symbol)
            if buy_date and buy_date == date_str:
                logger.debug(f"[Broker] T+1限制: {order.symbol} 今日买入不可卖出")
                return None

        if order.order_type == "market":
            # 市价单: 以当 bar 开盘价 + 滑点成交
            fill_price = bar.open * (1 + cfg.slippage_pct * (1 if order.is_buy else -1))

        elif order.order_type == "limit":
            # 限价单: 检查能否成交
            if order.is_buy and bar.low <= order.price:
                fill_price = min(order.price, bar.open)
            elif order.is_sell and bar.high >= order.price:
                fill_price = max(order.price, bar.open)
            else:
                return None
        else:
            return None

        # 成交量限制: 不超过当bar成交量的10% (防止不切实际的大单)
        if hasattr(bar, 'volume') and bar.volume > 0:
            max_fill = bar.volume * 0.10
            if order.quantity > max_fill:
                logger.debug(f"[Broker] 成交量限制: 委托{order.quantity}股 > bar量10%={max_fill:.0f}股")
                order.quantity = int(max_fill // cfg.lot_size) * cfg.lot_size
                if order.quantity <= 0:
                    order.status = "rejected"
                    return None

        # 计算数量 (如果用 pct_cash 模式, 由 engine 预先计算 quantity)
        qty = order.quantity

        # 资金/持仓检查
        if order.is_buy:
            cost = fill_price * qty
            commission = max(cost * cfg.commission_buy, cfg.min_commission)
            total_cost = cost + commission
            if total_cost > self.cash:
                # 按可用资金调整数量
                affordable = int(
                    (self.cash - cfg.min_commission) / (fill_price * (1 + cfg.commission_buy))
                )
                affordable = (affordable // cfg.lot_size) * cfg.lot_size
                if affordable <= 0:
                    logger.warning(f"[Broker] 资金不足, 取消买单: cash={self.cash:.0f}")
                    order.status = "rejected"
                    return None
                qty = affordable
                cost = fill_price * qty
                commission = max(cost * cfg.commission_buy, cfg.min_commission)
                total_cost = cost + commission

            self.cash -= total_cost

            # 记录T+1: 今天买入, 今天不能卖
            self._buy_today[order.symbol] = date_str

            # 更新持仓
            pos = self._positions.get(order.symbol)
            if pos is None:
                self._positions[order.symbol] = Position(
                    symbol=order.symbol,
                    quantity=qty,
                    avg_cost=(total_cost / qty) if qty > 0 else 0, # 加入买入佣金
                )
            else:
                total_qty = pos.quantity + qty
                pos.avg_cost = (pos.quantity * pos.avg_cost + total_cost) / total_qty
                pos.quantity = total_qty

        elif order.is_sell:
            pos = self._positions.get(order.symbol)
            if pos is None or pos.quantity < qty:
                qty = pos.quantity if pos else 0
            if qty <= 0:
                logger.warning(f"[Broker] 无持仓可卖: {order.symbol}")
                order.status = "rejected"
                return None

            proceed = fill_price * qty
            commission = max(proceed * cfg.commission_sell, cfg.min_commission)
            net = proceed - commission

            # 计算盈亏 (avg_cost已包含买入佣金，此处commission为卖出佣金)
            pnl = (fill_price - pos.avg_cost) * qty - commission

            self.cash += net

            pos.quantity -= qty
            pos.realized_pnl += pnl
            if pos.quantity == 0:
                del self._positions[order.symbol]

        else:
            return None

        trade = Trade(
            trade_id=str(uuid.uuid4())[:8],
            order_id=order.order_id,
            symbol=order.symbol,
            direction=order.direction,
            quantity=qty,
            price=fill_price,
            commission=max(
                fill_price * qty * (cfg.commission_buy if order.is_buy else cfg.commission_sell),
                cfg.min_commission,
            ),
            timestamp=bar.date,
            pnl=pnl if order.is_sell else 0.0,
        )

        logger.debug(
            f"[Broker] 成交: {order.symbol} {order.direction} {qty}@{fill_price:.2f}"
        )
        return trade
