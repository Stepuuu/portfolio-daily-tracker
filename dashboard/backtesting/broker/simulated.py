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
import re
import math
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
    lot_size: Optional[int] = None     # automatic: A/HK 100, US 1; explicit overrides are preserved
    allow_short: bool = False          # 是否允许做空 (A股默认不允许)
    enforce_price_limits: bool = True  # 是否启用涨跌停限制
    block_one_word_limit: bool = True  # 一字涨跌停不允许成交
    limit_queue_fill_ratio: float = 0.03  # 涨跌停板时假设最多成交当日量的3%
    market: str = "auto"                 # auto / a_share / hk / us
    enforce_t_plus_one: bool = True     # only applies to A-share stocks

    def __post_init__(self):
        if self.market not in {"auto", "a_share", "hk", "us"}:
            raise ValueError("Unsupported broker market")
        if self.allow_short:
            raise ValueError("Short selling is not implemented by this research broker")
        if self.lot_size is not None and (not isinstance(self.lot_size, int) or isinstance(self.lot_size, bool) or self.lot_size <= 0):
            raise ValueError("lot_size must be a positive integer")
        for value in (self.commission_buy, self.commission_sell, self.slippage_pct, self.limit_queue_fill_ratio):
            if not math.isfinite(value) or not 0 <= value < 1:
                raise ValueError("Broker rates must be finite, nonnegative and below one")
        if not math.isfinite(self.min_commission) or self.min_commission < 0:
            raise ValueError("Minimum commission must be finite and nonnegative")

    def market_for(self, symbol: str) -> str:
        if self.market != "auto":
            return self.market
        value = symbol.upper()
        if value.startswith(("HKG:", "HK:")) or value.endswith(".HK") or (value.isdigit() and len(value) == 5):
            return "hk"
        if value.startswith(("NASDAQ:", "NYSE:", "AMEX:", "ARCA:", "BATS:", "US:")) or value.endswith(".US"):
            return "us"
        if value.startswith(("SHA:", "SHE:", "BJ:", "ETF:", "LOF:", "REIT:", "IDX:")) or value.endswith((".SS", ".SZ", ".SH")) or (value.isdigit() and len(value) == 6):
            return "a_share"
        return "us"

    def lot_for(self, symbol: str) -> int:
        if self.lot_size is not None:
            return self.lot_size
        return 1 if self.market_for(symbol) == "us" else 100


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
        if not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("Initial cash must be finite and positive")

        self._positions: Dict[str, Position] = {}
        self._pending_orders: List[Order] = []
        self._filled_orders: List[Order] = []
        self._trades: List[Trade] = []
        self._last_prices: Dict[str, float] = {}   # 最新市价 (用于计算真实市值)
        self._buy_today: Dict[str, str] = {}        # symbol -> date, T+1 限制
        self._bought_quantity: Dict[str, float] = {}

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
        if (order.direction not in {"buy", "sell"} or order.order_type not in {"market", "limit"}
                or not math.isfinite(order.quantity) or order.quantity <= 0
                or not math.isfinite(order.price) or order.price < 0
                or (order.order_type == "limit" and order.price <= 0)):
            order.status = "rejected"
            return order
        self._pending_orders.append(order)
        logger.debug(f"[Broker] 提交委托: {order}")
        return order

    def process_bar(self, symbol: str, bar) -> Tuple[List[Trade], List[Order]]:
        """
        处理当前 bar 的挂单撮合.
        
        Args:
            symbol: 当前处理的标的
            bar:    当前 Bar 对象
        Returns:
            (本 bar 成交的 Trade 列表, 状态发生变化的订单列表)
        """
        new_trades = []
        changed_orders = []
        remaining = []

        for order in self._pending_orders:
            if order.symbol != symbol:
                remaining.append(order)
                continue

            trade, queue_blocked = self._try_fill(order, bar)
            if trade:
                new_trades.append(trade)
                self._trades.append(trade)
                order.status = "filled" if trade.quantity == order.quantity else "partial_cancelled"
                order.filled_qty = trade.quantity
                order.filled_price = trade.price
                order.commission = trade.commission
                self._filled_orders.append(order)
                changed_orders.append(order)
            else:
                if queue_blocked:
                    if order.status != "queued":
                        order.status = "queued"
                        changed_orders.append(order)
                    remaining.append(order)
                    continue
                if order.status in {"rejected", "cancelled"}:
                    changed_orders.append(order)
                    continue
                # 限价单还未成交, 保留
                if order.order_type == "limit":
                    remaining.append(order)
                else:
                    # 市价单当 bar 未成交则取消
                    order.status = "cancelled"
                    logger.warning(f"[Broker] 市价单无法成交, 取消: {order}")
                    changed_orders.append(order)

        self._pending_orders = remaining
        return new_trades, changed_orders

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
        row = (date, total, self.cash, mkt_value)
        if self._equity_curve and self._equity_curve[-1][0] == date:
            self._equity_curve[-1] = row
        else:
            self._equity_curve.append(row)

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def force_close(self, symbol: str, price: float, timestamp) -> Optional[Trade]:
        """在回测结束时按指定价格强制平仓。"""
        pos = self._positions.get(symbol)
        if pos is None or pos.quantity <= 0:
            return None

        qty = pos.quantity
        proceed = price * qty
        commission = max(proceed * self.config.commission_sell, self.config.min_commission)
        pnl = (price - pos.avg_cost) * qty - commission

        self.cash += proceed - commission
        self._last_prices[symbol] = price

        trade = Trade(
            trade_id=str(uuid.uuid4())[:8],
            order_id="FORCE_CLOSE",
            symbol=symbol,
            direction="sell",
            quantity=qty,
            price=price,
            commission=commission,
            timestamp=timestamp,
            pnl=pnl,
        )
        self._trades.append(trade)
        del self._positions[symbol]
        return trade

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _limit_pct_for_symbol(self, symbol: str) -> float:
        digits = "".join(re.findall(r"\d", symbol))
        if digits.startswith(("300", "301", "688", "689")):
            return 0.20
        if digits.startswith(("4", "8")):
            return 0.30
        return 0.10

    def _limit_prices(self, symbol: str, bar) -> tuple[Optional[float], Optional[float]]:
        prev_close = getattr(bar, "prev_close", 0) or 0
        if prev_close <= 0:
            return None, None
        pct = self._limit_pct_for_symbol(symbol)
        return prev_close * (1 + pct), prev_close * (1 - pct)

    def _is_one_word_limit(self, order: Order, bar, limit_up: Optional[float], limit_down: Optional[float]) -> bool:
        eps = 1e-6
        if order.is_buy and limit_up is not None:
            return bar.open >= limit_up - eps and bar.low >= limit_up - eps
        if order.is_sell and limit_down is not None:
            return bar.open <= limit_down + eps and bar.high <= limit_down + eps
        return False

    def _try_fill(self, order: Order, bar) -> tuple[Optional[Trade], bool]:
        """尝试撮合"""
        cfg = self.config
        date_str = str(bar.date)[:10] if bar.date else ""
        if not all(math.isfinite(float(getattr(bar, field, float("nan")))) and float(getattr(bar, field, 0)) > 0
                   for field in ("open", "high", "low", "close", "volume")):
            return None, True
        a_share = cfg.market_for(order.symbol) == "a_share"
        lot_size = cfg.lot_for(order.symbol)
        available_qty = None
        limit_up, limit_down = (None, None)
        if cfg.enforce_price_limits and a_share:
            limit_up, limit_down = self._limit_prices(order.symbol, bar)

        # --- T+1 限制: 当日买入的股票不能当日卖出 ---
        if order.is_sell and cfg.enforce_t_plus_one and a_share:
            buy_date = self._buy_today.get(order.symbol)
            if buy_date and buy_date == date_str:
                position = self._positions.get(order.symbol)
                available_qty = max(0.0, (position.quantity if position else 0.0) - self._bought_quantity.get(order.symbol, 0.0))
                if available_qty <= 0:
                    logger.debug(f"[Broker] T+1限制: {order.symbol} 今日买入不可卖出")
                    return None, True

        if cfg.enforce_price_limits and cfg.block_one_word_limit and self._is_one_word_limit(order, bar, limit_up, limit_down):
            logger.debug(f"[Broker] 一字涨跌停无法成交: {order.symbol} {order.direction}")
            return None, True

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
                return None, False
        else:
            return None, False

        # 成交量限制: 不超过当bar成交量的10% (防止不切实际的大单)
        if hasattr(bar, 'volume') and bar.volume > 0:
            max_fill = bar.volume * 0.10
            # 若处于涨跌停附近，进一步假设只有少量排队成交机会
            eps = 1e-6
            near_buy_limit = order.is_buy and limit_up is not None and (fill_price >= limit_up - eps or bar.high >= limit_up - eps)
            near_sell_limit = order.is_sell and limit_down is not None and (fill_price <= limit_down + eps or bar.low <= limit_down + eps)
            if near_buy_limit or near_sell_limit:
                max_fill = min(max_fill, bar.volume * cfg.limit_queue_fill_ratio)
            if order.quantity > max_fill:
                logger.debug(f"[Broker] 成交量限制: 委托{order.quantity}股 > bar量10%={max_fill:.0f}股")
                max_fill = int(max_fill // lot_size) * lot_size
                if max_fill <= 0:
                    return None, True

        # 计算数量 (如果用 pct_cash 模式, 由 engine 预先计算 quantity)
        qty = min(order.quantity, max_fill)
        if order.is_buy:
            qty = int(qty // lot_size) * lot_size
        if available_qty is not None:
            qty = min(qty, available_qty)
        if qty <= 0:
            return None, True

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
                affordable = (affordable // lot_size) * lot_size
                if affordable <= 0:
                    logger.warning(f"[Broker] 资金不足, 取消买单: cash={self.cash:.0f}")
                    order.status = "rejected"
                    return None, False
                qty = affordable
                cost = fill_price * qty
                commission = max(cost * cfg.commission_buy, cfg.min_commission)
                total_cost = cost + commission

            self.cash -= total_cost

            # 记录T+1: 今天买入, 今天不能卖
            previous_bought = self._bought_quantity.get(order.symbol, 0.0) if self._buy_today.get(order.symbol) == date_str else 0.0
            self._buy_today[order.symbol] = date_str
            self._bought_quantity[order.symbol] = previous_bought + qty

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
                return None, False

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
            return None, False

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
        return trade, False
