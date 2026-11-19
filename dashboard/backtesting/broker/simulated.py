"""
模拟撮合经纪商
灵感: backtrader 的 BackBroker + rqalpha 的 sys_simulation Mod

支持:
  - 市价单: 下一根 bar 的开盘价成交 (符合"昨信号今开盘执行"逻辑)
  - 限价单: 下一根 bar 检查是否满足条件
  - 交易成本: 佣金 (用户设置, 最低佣金只作用于佣金) + 按市场与日期的税费
    (印花税、过户费、港股交易费与征费、美股 SEC 费, 见 fees.py / fee_schedules.json)
  - 滑点模拟
  - 持仓管理
"""
import uuid
import logging
import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from backtesting.strategies.base import Order, Trade, Position
from backtesting.broker import market_rules
from backtesting.broker.fees import FeeBreakdown, compute_fees, default_schedule

logger = logging.getLogger(__name__)

EXIT_TYPES = ("stop", "target")
# Steps of SimulatedBroker._bar_step that fill at the open (the rest are inside the bar).
OPENING_STEPS = (1, 2, 3)
# Participation cap: all fills on one symbol in one bar together stay within this
# share of the bar's volume (limit_queue_fill_ratio near a price limit).
VOLUME_CAP = 0.10


def _day(value) -> Optional[str]:
    """YYYY-MM-DD of a bar date (Timestamp, date or string)."""
    if value is None or value == "":
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


@dataclass
class BrokerConfig:
    """经纪商配置

    commission_buy / commission_sell / min_commission are the broker commission
    only (the minimum applies to the commission, not to taxes). Stamp duty,
    transfer fee, exchange fees and levies come from the dated schedule in
    fee_schedules.json for the symbol's market and the trade date. None means the
    market default stated in that file (A-share/HK 0.025% with a minimum of 5, US 0).
    """
    commission_buy: Optional[float] = None    # 买入佣金率 (仅佣金)
    commission_sell: Optional[float] = None   # 卖出佣金率 (仅佣金, 不含印花税)
    min_commission: Optional[float] = None    # 每笔最低佣金 (仅佣金)
    slippage_pct: float = 0.0002       # 滑点 0.02%
    lot_size: Optional[int] = None     # automatic: A/HK 100, US 1; explicit overrides are preserved
    allow_short: bool = False          # 是否允许做空 (A股默认不允许)
    enforce_price_limits: bool = True  # 是否启用涨跌停限制
    block_one_word_limit: bool = True  # 一字涨跌停不允许成交
    limit_queue_fill_ratio: float = 0.03  # 涨跌停板时假设最多成交当日量的3%
    market: str = "auto"                 # auto / a_share / hk / us
    enforce_t_plus_one: bool = True     # only applies to A-share stocks (ETFs included)
    buy_expiry_bars: int = 1            # an unfilled buy is dropped after this many bars
    sell_retry_bars: int = 20           # an unfilled sell keeps trying for this many bars

    def __post_init__(self):
        if self.market not in {"auto", "a_share", "hk", "us"}:
            raise ValueError("Unsupported broker market")
        if self.allow_short:
            raise ValueError("Short selling is not implemented by this research broker")
        if self.lot_size is not None and (not isinstance(self.lot_size, int) or isinstance(self.lot_size, bool) or self.lot_size <= 0):
            raise ValueError("lot_size must be a positive integer")
        rates = [self.slippage_pct, self.limit_queue_fill_ratio]
        rates += [value for value in (self.commission_buy, self.commission_sell) if value is not None]
        for value in rates:
            if not math.isfinite(value) or not 0 <= value < 1:
                raise ValueError("Broker rates must be finite, nonnegative and below one")
        if self.min_commission is not None and (not math.isfinite(self.min_commission) or self.min_commission < 0):
            raise ValueError("Minimum commission must be finite and nonnegative")
        for value in (self.buy_expiry_bars, self.sell_retry_bars):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("Order lifetimes must be positive integers")

    def market_for(self, symbol: str) -> str:
        return market_rules.market_for(symbol, self.market)

    def lot_rule_for(self, symbol: str) -> market_rules.LotRule:
        """Board lot rule (STAR 200+1, BSE 100+1, A/HK 100, US 1) or the explicit lot_size."""
        return market_rules.lot_rule(symbol, self.market_for(symbol), self.lot_size)

    def lot_for(self, symbol: str) -> int:
        """Smallest buy quantity (one lot)."""
        return self.lot_rule_for(symbol).minimum

    def fees_for(self, symbol: str, side: str, quantity, price, when) -> FeeBreakdown:
        """Commission plus the dated taxes and levies of one fill."""
        rate = self.commission_buy if side == "buy" else self.commission_sell
        return compute_fees(symbol, side, quantity, price, when, commission_rate=rate,
                            min_commission=self.min_commission, market=self.market_for(symbol))


@dataclass
class TriggeredSell(Order):
    """The unfilled rest of a triggered stop, working as a market sell.

    A stop that triggered stays triggered: whatever it could not sell on the
    trigger bar (volume cap, T+1, limit-down board) becomes this order, which
    sells at the next open under the normal sell-retry rules. It is a separate
    order, so moving or cancelling the stop does not withdraw it; its fills are
    recorded with Trade.reason "stop".
    """
    triggered_by: str = ""        # order_id of the stop
    exit_reason: str = "stop"


@dataclass
class _LimitState:
    """Limit-up / limit-down tests for one bar (see SimulatedBroker.set_price_basis)."""
    pct: float
    prev: float
    up_price: Optional[float] = None       # raw basis: exchange-rounded limit prices
    down_price: Optional[float] = None
    tolerance: Optional[float] = None      # adjusted basis: ratio tolerance
    pattern_up: bool = False               # one-price board >= 4.8% (ST status unknown)
    pattern_down: bool = False

    def at_up(self, price: float) -> bool:
        if self.tolerance is None:
            return self.up_price is not None and price >= self.up_price - 1e-6
        return price / self.prev - 1 >= self.pct - self.tolerance - 1e-12

    def at_down(self, price: float) -> bool:
        if self.tolerance is None:
            return self.down_price is not None and price <= self.down_price + 1e-6
        return price / self.prev - 1 <= -(self.pct - self.tolerance) + 1e-12

    def one_word_up(self, bar) -> bool:
        return self.pattern_up or (self.at_up(bar.open) and self.at_up(bar.low))

    def within(self, price: float, bar, buy: bool) -> float:
        """``price`` kept inside the board: slippage never fills a buy above limit up or a sell below limit
        down. Raw prices: the exchange's limit price. Adjusted prices: a price at the board is capped at the
        bar's high (low), the board's own price in that series; nothing traded beyond it."""
        if buy:
            if self.tolerance is None:
                return min(price, self.up_price) if self.up_price is not None else price
            return min(price, float(bar.high)) if self.at_up(price) else price
        if self.tolerance is None:
            return max(price, self.down_price) if self.down_price is not None else price
        return max(price, float(bar.low)) if self.at_down(price) else price

    def one_word_down(self, bar) -> bool:
        return self.pattern_down or (self.at_down(bar.open) and self.at_down(bar.high))


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
        self._fee_assumed: List[Tuple[str, str]] = []
        self._price_basis: Dict[str, str] = {}      # symbol -> raw / adjusted (see set_price_basis)
        # Signals that did not (fully) fill: [{symbol, direction, reason, outcome, ...}]
        self._unfilled: List[dict] = []
        self._entries: Dict[int, dict] = {}         # id(working order) -> its unfilled entry
        self._volume_taken: Dict[str, Tuple[str, float]] = {}   # listing -> (bar day, shares filled)
        self._spawned: List[Order] = []

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
        """提交委托

        market / limit: buy or sell. stop / target: sells that trigger inside a bar
        (see _fill_price); follow_position=True protects the whole position.
        Raises SymbolError when the symbol's market cannot be determined (with
        market="auto"): its lots, limits, T+1 and fees would be unknown.
        """
        self.config.market_for(order.symbol)
        exit_order = order.order_type in EXIT_TYPES
        valid_quantity = math.isfinite(order.quantity) and (
            order.quantity > 0 or (exit_order and order.follow_position and order.quantity == 0))
        if (order.direction not in {"buy", "sell"} or order.order_type not in {"market", "limit", *EXIT_TYPES}
                or not valid_quantity
                or not math.isfinite(order.price) or order.price < 0
                or (order.order_type != "market" and order.price <= 0)
                or (exit_order and order.direction != "sell")):
            order.status = "rejected"
            return order
        self._pending_orders.append(order)
        logger.debug(f"[Broker] 提交委托: {order}")
        return order

    def pending_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Orders still working (queued buys/sells), oldest first."""
        return [order for order in self._pending_orders if symbol is None or order.symbol == symbol]

    def cancel_order(self, order: Order) -> bool:
        """Withdraw a working order. Returns False if it is no longer pending."""
        if order not in self._pending_orders:
            return False
        self._pending_orders.remove(order)
        order.status = "cancelled"
        order.reason = order.reason or "cancelled"
        self._finish_entry(order, "cancelled")
        return True

    def process_bar(self, symbol: str, bar) -> Tuple[List[Trade], List[Order]]:
        """
        处理当前 bar 的挂单撮合 (one symbol: ``process_bars({symbol: bar})``).

        Life-cycle (BrokerConfig.buy_expiry_bars / sell_retry_bars): a buy that does
        not fill expires after 1 bar; a sell keeps retrying for up to 20 bars. Every
        bar an order fails to (fully) fill is recorded in `unfilled` with its reason.

        Args:
            symbol: 当前处理的标的
            bar:    当前 Bar 对象
        Returns:
            (本 bar 成交的 Trade 列表, 状态发生变化的订单列表)
        """
        return self.process_bars({symbol: bar})[symbol]

    def process_bars(self, bars: Dict[str, object]) -> Dict[str, Tuple[List[Trade], List[Order]]]:
        """Match the working orders of every symbol in `bars` against its bar of one day.

        One session, two phases, each across all the symbols: first everything that
        fills at the open (steps 1-3 of the table below), then everything inside the
        bar (steps 4-6, and the orders the bar does not reach). Within a phase orders
        go by step, then in the order they were submitted, whatever symbol they are
        for. So the result does not depend on the order of `bars` (the order the data
        was added), and a sale inside the bar can never pay for a purchase at that
        morning's open: a buy at the open has the cash there was at the open plus the
        proceeds of sales that filled at the open before it.

        Returns {symbol: (trades, orders whose state changed)} for every symbol in `bars`.
        """
        results: Dict[str, Tuple[List[Trade], List[Order]]] = {symbol: ([], []) for symbol in bars}
        working = set()
        closed = set()                       # symbols a sale on this bar sold out
        self._spawned: List[Order] = []      # rests of stops triggered on this bar
        sequence = {id(order): index for index, order in enumerate(self._pending_orders)}
        candidates = [order for order in self._pending_orders if order.symbol in bars]
        steps = {id(order): self._bar_step(order, bars[order.symbol]) for order in candidates}

        at_open = [order for order in candidates if steps[id(order)] in OPENING_STEPS]
        inside = [order for order in candidates if steps[id(order)] not in OPENING_STEPS]
        for batch in (at_open, inside):
            for order in sorted(batch, key=lambda o: (steps[id(o)], sequence[id(o)])):
                symbol, bar = order.symbol, bars[order.symbol]
                new_trades, changed_orders = results[symbol]
                if symbol in closed and order.is_sell and order.order_type not in EXIT_TYPES \
                        and symbol not in self._positions:
                    # A stop (or another sell) earlier in the bar sold the position out.
                    order.status = "cancelled"
                    order.reason = "position_closed"
                    self._finish_entry(order, "cancelled")
                    changed_orders.append(order)
                    continue
                if order.order_type in EXIT_TYPES:
                    keep = self._process_exit(order, bar, new_trades, changed_orders)
                else:
                    keep = self._process_order(order, bar, new_trades, changed_orders)
                if keep:
                    working.add(id(order))
                if symbol not in self._positions and any(t.direction == "sell" for t in new_trades):
                    closed.add(symbol)

        self._pending_orders = [o for o in self._pending_orders if o.symbol not in bars or id(o) in working]
        self._pending_orders += self._spawned
        for symbol in bars:
            if symbol in closed and symbol not in self._positions:
                results[symbol][1].extend(self._cancel_exits(symbol, "position_closed"))
        return results

    # Order of events inside one daily bar. OHLC does not show the path, so when
    # two orders could both fill and their order is unknowable, the outcome worse
    # for the holder is assumed (the stop).
    #
    #   step  when            orders                                      fill price
    #   1     at the open     market buys and sells                       open -/+ slippage, never beyond the board
    #   2     at the open     stops at or above the open (gap through)    open - slippage
    #   3     at the open     sell limits and targets at or below the     open
    #                         open; buy limits at or above it
    #   4     inside the bar  stops below the open that the low reaches   stop - slippage
    #   5     inside the bar  sell limits and targets above the open      limit / target
    #                         that the high reaches
    #   6     inside the bar  buy limits below the open the low reaches   limit
    #   7                     anything else (not reached: waits / ages)
    #
    # Within a step orders go in the order they were submitted. A stop and a sell
    # limit both touched inside the bar: the stop first (step 4 before 5). Once a
    # sell empties the position, the symbol's other sells are cancelled
    # (position_closed). There are no buy stops: exit orders are sells only.
    # Steps 1-3 of every symbol of the session come before step 4 of any symbol
    # (process_bars).
    @staticmethod
    def _bar_step(order: Order, bar) -> int:
        try:
            open_ = float(bar.open)
        except (AttributeError, TypeError, ValueError):
            return 7
        kind = order.order_type
        if kind == "market":
            return 1
        if kind == "stop":
            return 2 if open_ <= order.price else 4
        if kind == "target" or (kind == "limit" and order.is_sell):
            return 3 if open_ >= order.price else 5
        if kind == "limit" and order.is_buy:
            return 3 if open_ <= order.price else 6
        return 7

    def _process_exit(self, order: Order, bar, new_trades: List[Trade], changed: List[Order]) -> bool:
        """Evaluate a stop or target on one bar. Returns True while it keeps working."""
        if order.symbol not in self._positions:
            return True                       # nothing to protect (yet)
        try:
            triggered = self._fill_price(order, bar) is not None
        except (TypeError, ValueError):
            triggered = False
        if not triggered:
            return True                       # not touched on this bar
        trade, reason = self._try_fill(order, bar)
        if trade is None:
            if reason == "suspended":
                return True
            if order.status == "rejected":
                order.reason = reason or "rejected"
                entry = self._entry(order, bar.date, order.reason, self._wanted(order))
                entry["outcome"] = "rejected"
                self._entries.pop(id(order), None)
                changed.append(order)
                return False
            if order.order_type == "stop":
                # Triggered but blocked (T+1, limit-down board, volume): it stays triggered.
                self._carry_stop(order, bar, reason or "not_filled", changed)
                return False
            # A target touched but blocked keeps working as a limit at its price.
            order.reason = reason or "not_filled"
            self._entry(order, bar.date, order.reason, self._wanted(order))
            if order.status != "queued":
                order.status = "queued"
                changed.append(order)
            return True
        new_trades.append(trade)
        self._trades.append(trade)
        done_before = order.filled_qty
        order.filled_qty = done_before + trade.quantity
        order.filled_price = (order.filled_price * done_before + trade.price * trade.quantity) / order.filled_qty
        order.commission += trade.commission
        changed.append(order)
        finished = order.symbol not in self._positions or (
            not order.follow_position and order.quantity - order.filled_qty <= 1e-9)
        if finished:
            order.status = "filled"
            self._filled_orders.append(order)
            entry = self._entries.pop(id(order), None)
            if entry is not None:
                entry.update(outcome="filled_late", filled_date=_day(bar.date),
                             filled_price=round(order.filled_price, 4), filled_quantity=order.filled_qty)
            return False
        if order.order_type == "stop":
            self._carry_stop(order, bar, reason or "partial", changed)
            return False
        order.status = "partially_filled"
        order.reason = reason or "partial"
        self._entry(order, bar.date, order.reason, self._wanted(order))
        return True

    def _carry_stop(self, order: Order, bar, reason: str, changed: List[Order]) -> None:
        """A stop triggered on `bar` that did not sell in full: the rest becomes a
        TriggeredSell (market) from the next bar on. Its unfilled-log entry is the
        stop's, with carried_by / triggered_date, and follows the market sell to
        its outcome (filled_late, expired, open_at_end)."""
        held = self._positions.get(order.symbol)
        quantity = min(self._wanted(order), held.quantity if held else 0.0)
        order.status = "triggered"
        order.reason = reason
        if order not in changed:
            changed.append(order)
        if quantity <= 1e-9:
            self._finish_entry(order, "filled_late")
            return
        rest = TriggeredSell(
            order_id=f"{order.order_id}-rest", symbol=order.symbol, direction="sell", order_type="market",
            quantity=quantity, price=0.0, timestamp=bar.date, status="queued", reason=reason,
            triggered_by=order.order_id,
        )
        entry = self._entry(order, bar.date, reason, quantity)
        self._entries.pop(id(order), None)
        entry.update(carried_by=rest.order_id, triggered_date=_day(bar.date), filled_at_trigger=order.filled_qty)
        self._entries[id(rest)] = entry
        self._spawned.append(rest)
        changed.append(rest)
        logger.info(f"[Broker] 止损已触发未全部成交: {order.symbol} 剩余 {quantity} 转为市价卖单 原因={reason}")

    def _cancel_exits(self, symbol: str, reason: str) -> List[Order]:
        """Stops and targets of a closed position (one cancels the other)."""
        cancelled = []
        for order in [o for o in self._pending_orders if o.symbol == symbol and o.order_type in EXIT_TYPES]:
            order.reason = reason
            self._pending_orders.remove(order)
            order.status = "cancelled"
            self._finish_entry(order, "cancelled")
            cancelled.append(order)
        return cancelled

    def _wanted(self, order: Order):
        """Quantity still to fill (the whole position for follow_position exits)."""
        if order.follow_position:
            held = self._positions.get(order.symbol)
            return held.quantity if held else 0
        return order.quantity - order.filled_qty

    def process_missing_bar(self, symbol: str, date) -> List[Order]:
        """A calendar day without a bar for `symbol`: it is suspended, nothing fills."""
        changed: List[Order] = []
        remaining = []
        for order in self._pending_orders:
            if order.symbol != symbol or order.order_type in EXIT_TYPES:
                remaining.append(order)     # stops and targets do not age
            elif self._wait(order, date, "suspended", changed):
                remaining.append(order)
        self._pending_orders = remaining
        return changed

    def close_pending(self, date) -> List[Order]:
        """End of data: orders still working are logged as open_at_end and dropped."""
        changed = list(self._pending_orders)
        for order in changed:
            order.status = "cancelled"
            if order.order_type in EXIT_TYPES and id(order) not in self._entries:
                order.reason = "end_of_data"        # never triggered: not a missed signal
                continue
            entry = self._entry(order, date, order.reason or "end_of_data", self._wanted(order))
            entry["outcome"] = "open_at_end"
            self._entries.pop(id(order), None)
            order.reason = order.reason or "end_of_data"
        self._pending_orders = []
        return changed

    def _process_order(self, order: Order, bar, new_trades: List[Trade], changed: List[Order]) -> bool:
        """Try one order on one bar. Returns True while the order keeps working."""
        trade, reason = self._try_fill(order, bar)
        if trade is None:
            if order.status == "rejected":
                order.reason = reason or "rejected"
                entry = self._entry(order, bar.date, order.reason, order.quantity - order.filled_qty)
                entry["outcome"] = "rejected"
                self._entries.pop(id(order), None)
                changed.append(order)
                return False
            return self._wait(order, bar.date, reason or "not_filled", changed)

        new_trades.append(trade)
        self._trades.append(trade)
        done_before = order.filled_qty
        order.filled_qty = done_before + trade.quantity
        order.filled_price = (order.filled_price * done_before + trade.price * trade.quantity) / order.filled_qty
        order.commission += trade.commission
        changed.append(order)
        left = order.quantity - order.filled_qty
        if left <= 1e-9 or (order.is_sell and order.symbol not in self._positions):
            order.status = "filled"
            self._filled_orders.append(order)
            entry = self._entries.pop(id(order), None)
            if entry is not None:
                entry.update(outcome="filled_late", filled_date=_day(bar.date),
                             filled_price=round(order.filled_price, 4), filled_quantity=order.filled_qty)
            return False
        if order.is_buy:
            # The rest of a buy is not carried to the next bar.
            order.status = "partial_cancelled"
            order.reason = reason or "partial"
            self._filled_orders.append(order)
            entry = self._entry(order, bar.date, order.reason, left)
            entry.update(outcome="partial", filled_quantity=order.filled_qty)
            self._entries.pop(id(order), None)
            return False
        # The rest of a sell keeps working.
        order.status = "partially_filled"
        return self._wait(order, bar.date, reason or "partial", changed, already_changed=True)

    def _wait(self, order: Order, date, reason: str, changed: List[Order], already_changed: bool = False) -> bool:
        """Count one bar without a (full) fill; expire the order when its time is up."""
        order.bars_waited += 1
        order.reason = reason
        entry = self._entry(order, date, reason, order.quantity - order.filled_qty)
        limit = self.config.buy_expiry_bars if order.is_buy else self.config.sell_retry_bars
        if order.bars_waited >= limit:
            order.status = "expired"
            entry["outcome"] = "expired"
            self._entries.pop(id(order), None)
            if not already_changed:
                changed.append(order)
            logger.info(f"[Broker] 委托过期: {order.symbol} {order.direction} 原因={reason} 等待 {order.bars_waited} 根K线")
            return False
        if order.status not in ("queued", "partially_filled"):
            order.status = "queued"
            if not already_changed:
                changed.append(order)
        return True

    def _entry(self, order: Order, date, reason: str, quantity) -> dict:
        """The unfilled-log entry of a working order (created at its first miss)."""
        entry = self._entries.get(id(order))
        if entry is None:
            entry = self.log_unfilled(
                symbol=order.symbol, direction=order.direction, order_type=order.order_type,
                quantity=quantity, reason=reason, signal_date=order.timestamp, date=date,
                order_id=order.order_id, outcome="pending", reasons=[reason], bars_waited=order.bars_waited,
            )
            self._entries[id(order)] = entry
        else:
            entry["quantity"] = quantity
            entry["bars_waited"] = order.bars_waited
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
        return entry

    def _finish_entry(self, order: Order, outcome: str) -> None:
        entry = self._entries.pop(id(order), None)
        if entry is not None:
            entry["outcome"] = outcome

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

    def sell_block_reason(self, symbol: str, bar) -> Optional[str]:
        """Why a sell could not trade on `bar` at all: suspended / limit_down / None."""
        try:
            valid = all(math.isfinite(float(getattr(bar, name))) and float(getattr(bar, name)) > 0
                        for name in ("open", "high", "low", "close", "volume"))
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            return "suspended"
        if self.config.enforce_price_limits and self.config.market_for(symbol) == "a_share":
            limits = self._limit_state(symbol, bar)
            if limits is not None and limits.one_word_down(bar):
                return "limit_down"
        return None

    def t_plus_one_locked(self, symbol: str, when) -> float:
        """Shares of `symbol` bought on `when` that A-share T+1 keeps from being sold that day."""
        if not (self.config.enforce_t_plus_one and self.config.market_for(symbol) == "a_share"):
            return 0.0
        if self._buy_today.get(symbol) != _day(when):
            return 0.0
        held = self._positions.get(symbol)
        return min(self._bought_quantity.get(symbol, 0.0), held.quantity if held else 0.0)

    @property
    def unfilled(self) -> List[dict]:
        """Every signal that did not fill in full, with the reason (see log_unfilled)."""
        return [dict(entry) for entry in self._unfilled]

    def log_unfilled(self, *, symbol: str, direction: str, order_type: str, quantity, reason: str,
                     signal_date, outcome: str, date=None, order_id: Optional[str] = None, **extra) -> dict:
        """Record a signal that did not fill.

        reason:  lot_unaffordable / insufficient_cash / limit_up / limit_down /
                 suspended / t_plus_one / volume_cap / limit_queue / ...
        outcome: not_submitted (never became an order), expired, partial
                 (remainder cancelled), filled_late, open_at_end, rejected.
        """
        entry = {
            "symbol": symbol,
            "direction": direction,
            "order_type": order_type,
            "order_id": order_id,
            "signal_date": _day(signal_date),
            "date": _day(date if date is not None else signal_date),
            "quantity": quantity,
            "reason": reason,
            "outcome": outcome,
            **extra,
        }
        self._unfilled.append(entry)
        return entry

    def force_close(self, symbol: str, price: float, timestamp) -> Optional[Trade]:
        """在回测结束时按指定价格强制平仓。"""
        pos = self._positions.get(symbol)
        if pos is None or pos.quantity <= 0:
            return None

        qty = pos.quantity
        proceed = price * qty
        fees = self._fees(symbol, "sell", qty, price, timestamp)
        commission = fees.total
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
            fees=fees.parts(),
            reason="end_mark",
        )
        self._trades.append(trade)
        del self._positions[symbol]
        return trade

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _fees(self, symbol: str, side: str, qty, price, when) -> FeeBreakdown:
        fees = self.config.fees_for(symbol, side, qty, price, when)
        if fees.assumed:
            key = (fees.market, str(when)[:10])
            if key not in self._fee_assumed:
                self._fee_assumed.append(key)
        return fees

    def affordable_quantity(self, symbol: str, price: float, cash: float, when) -> int:
        """Largest buy quantity allowed by the lot rule whose cost plus fees fits in `cash`."""
        rule = self.config.lot_rule_for(symbol)
        if price <= 0 or cash <= 0:
            return 0
        qty = rule.round_buy(cash / price)
        while qty > 0:
            over = price * qty + self._peek_fees(symbol, "buy", qty, price, when) - cash
            if over <= 1e-9:
                break
            # Step down by the overshoot first, then step by step.
            qty = rule.round_buy(qty - max(rule.step, int(over / price) // rule.step * rule.step))
        return max(qty, 0)

    def lot_cost(self, symbol: str, price: float, when) -> float:
        """Cost of the smallest allowed buy, fees included."""
        qty = self.config.lot_for(symbol)
        return price * qty + self._peek_fees(symbol, "buy", qty, price, when)

    def _peek_fees(self, symbol: str, side: str, qty, price, when) -> float:
        """Fees of a candidate fill without recording schedule assumptions."""
        return self.config.fees_for(symbol, side, qty, price, when).total

    @property
    def fee_assumptions(self) -> List[Tuple[str, str]]:
        """(market, trade date) of fills charged at a schedule row dated after the trade."""
        return list(self._fee_assumed)

    def _limit_pct_for_symbol(self, symbol: str, when=None) -> Optional[float]:
        """Daily limit of the symbol's board on `when` (market_rules.LIMIT_TABLE)."""
        return market_rules.price_limit_pct(symbol, when, self.config.market_for(symbol))

    def set_price_basis(self, symbol: str, basis: str) -> None:
        """How limit prices are detected for `symbol`.

        raw:      the series is the traded price; limit = previous close x (1 +/- pct)
                  rounded half-up to the tick, as the exchange computes it.
        adjusted: qfq/hfq prices. Rounding the adjusted previous close to the tick
                  breaks at low adjusted prices, so the move is compared as a ratio
                  on the same series with a tolerance of 1.5 ticks relative to the
                  previous close (the series rounds both prices, and the exchange
                  rounded the real limit price).
        """
        if basis not in ("raw", "adjusted"):
            raise ValueError("price basis must be raw or adjusted")
        # The engine calls this when a symbol's data is added: an unresolvable
        # symbol (SymbolError) stops the run before any order.
        self.config.market_for(symbol)
        self._price_basis[symbol] = basis

    def _price_tick_for_symbol(self, symbol: str) -> Decimal:
        """Quote tick: 0.001 for SSE/SZSE funds, 0.01 otherwise (see market_rules.price_tick)."""
        return market_rules.price_tick(symbol, self.config.market)

    def _limit_prices(self, symbol: str, bar) -> tuple[Optional[float], Optional[float]]:
        prev_close = getattr(bar, "prev_close", 0) or 0
        if prev_close <= 0:
            return None, None
        # 交易所规则: 涨跌停价 = 前收 × (1 ± 幅度), 四舍五入到最小价格变动单位
        # (股票 0.01 元, 基金 0.001 元, 见 _price_tick_for_symbol).
        # 用 Decimal 计算, 避免 11.055 这类值在浮点下被舍到错误的一侧.
        limit_pct = self._limit_pct_for_symbol(symbol, getattr(bar, "date", None))
        if limit_pct is None:
            return None, None
        prev = Decimal(str(prev_close))
        pct = Decimal(str(limit_pct))
        tick = self._price_tick_for_symbol(symbol)
        limit_up = (prev * (1 + pct)).quantize(tick, rounding=ROUND_HALF_UP)
        limit_down = (prev * (1 - pct)).quantize(tick, rounding=ROUND_HALF_UP)
        return float(limit_up), float(limit_down)

    def _limit_state(self, symbol: str, bar) -> Optional["_LimitState"]:
        """Limit detection for one bar of an A-share symbol, or None without a limit."""
        prev_close = float(getattr(bar, "prev_close", 0) or 0)
        if not math.isfinite(prev_close) or prev_close <= 0:
            return None
        pct = self._limit_pct_for_symbol(symbol, getattr(bar, "date", None))
        if pct is None:
            return None
        state = _LimitState(pct=pct, prev=prev_close)
        if self._price_basis.get(symbol, "raw") == "raw":
            state.up_price, state.down_price = self._limit_prices(symbol, bar)
        else:
            state.tolerance = 1.5 * float(self._price_tick_for_symbol(symbol)) / prev_close
        if market_rules.st_pattern_applies(symbol, getattr(bar, "date", None), self.config.market_for(symbol)):
            one_price = (bar.high - bar.low) <= 1e-9 * max(abs(bar.high), 1.0)
            move = bar.close / prev_close - 1
            state.pattern_up = one_price and move >= market_rules.ONE_PRICE_LIMIT_MOVE
            state.pattern_down = one_price and move <= -market_rules.ONE_PRICE_LIMIT_MOVE
        return state

    def _try_fill(self, order: Order, bar) -> Tuple[Optional[Trade], Optional[str]]:
        """Try to fill what is left of `order` on `bar`.

        Returns (trade, reason): reason says why nothing filled (trade None) or why
        only part of the order filled (trade set), e.g. suspended, limit_up,
        limit_down, t_plus_one, volume_cap, limit_queue, insufficient_cash.
        """
        cfg = self.config
        date_str = _day(bar.date) or ""
        if not all(math.isfinite(float(getattr(bar, field, float("nan")))) and float(getattr(bar, field, 0)) > 0
                   for field in ("open", "high", "low", "close", "volume")):
            return None, "suspended"
        a_share = cfg.market_for(order.symbol) == "a_share"
        rule = cfg.lot_rule_for(order.symbol)
        wanted = self._wanted(order)
        limited_by: Optional[str] = None
        available_qty = None
        limits = self._limit_state(order.symbol, bar) if cfg.enforce_price_limits and a_share else None

        target = wanted
        if order.is_sell:
            held = self._positions.get(order.symbol)
            if held is None or held.quantity <= 0:
                logger.warning(f"[Broker] 无持仓可卖: {order.symbol}")
                order.status = "rejected"
                return None, "no_position"
            # Every sell follows the lot rule (LotRule.valid_sell): whole lots, the
            # entire odd remainder or the whole position, never a fraction of a share.
            target = min(wanted, held.quantity)
            if not rule.valid_sell(target, held.quantity):
                logger.warning(f"[Broker] 卖出数量不符合整手规则: {order.symbol} {target} / 持仓 {held.quantity}")
                order.status = "rejected"
                return None, "odd_lot"

        # --- T+1 限制: 当日买入的股票不能当日卖出 ---
        if order.is_sell and cfg.enforce_t_plus_one and a_share:
            buy_date = self._buy_today.get(order.symbol)
            if buy_date and buy_date == date_str:
                position = self._positions.get(order.symbol)
                available_qty = max(0.0, (position.quantity if position else 0.0) - self._bought_quantity.get(order.symbol, 0.0))
                if available_qty <= 0:
                    logger.debug(f"[Broker] T+1限制: {order.symbol} 今日买入不可卖出")
                    return None, "t_plus_one"

        if limits is not None and cfg.block_one_word_limit and (
                (order.is_buy and limits.one_word_up(bar)) or (order.is_sell and limits.one_word_down(bar))):
            logger.debug(f"[Broker] 一字涨跌停无法成交: {order.symbol} {order.direction}")
            return None, "limit_up" if order.is_buy else "limit_down"

        fill_price = self._fill_price(order, bar)
        if fill_price is None:
            return None, "price_not_reached"
        if limits is not None:
            # A fill at the board is a fill at the limit price (and queued: limit_queue below), not beyond it.
            fill_price = limits.within(fill_price, bar, order.is_buy)

        # 成交量限制: 本 bar 该标的所有成交合计不超过成交量的 10% (防止不切实际的大单).
        # One budget per listing and bar, shared by every order on the instrument
        # whatever symbol text it was sent under (600000, SHA:600000, 600000.SH),
        # used up in the order the bar's events are processed (see _bar_step).
        cap = bar.volume * VOLUME_CAP
        cap_reason = "volume_cap"
        # 若处于涨跌停附近，进一步假设只有少量排队成交机会 (同样是整根 bar 合计).
        # A fill at the open happens before the rest of the bar: only its own price
        # counts. Inside the bar the path is unknown, so a bar that touched the
        # limit counts as near it.
        at_open = self._bar_step(order, bar) in OPENING_STEPS
        near_buy_limit = order.is_buy and limits is not None and (
            limits.at_up(fill_price) or (not at_open and limits.at_up(bar.high)))
        near_sell_limit = order.is_sell and limits is not None and (
            limits.at_down(fill_price) or (not at_open and limits.at_down(bar.low)))
        if (near_buy_limit or near_sell_limit) and bar.volume * cfg.limit_queue_fill_ratio < cap:
            cap = bar.volume * cfg.limit_queue_fill_ratio
            cap_reason = "limit_queue"
        max_fill = max(cap - self._volume_used(order.symbol, bar), 0.0)
        if order.is_sell:
            # A partial fill (volume cap, T+1) must leave a rest that is itself a valid sell.
            limit_qty = target
            if max_fill < limit_qty - 1e-9:
                logger.debug(f"[Broker] 成交量限制: 委托{target}股 > 可成交{max_fill:.0f}股")
                limit_qty, limited_by = max_fill, cap_reason
            if available_qty is not None and available_qty < limit_qty:
                limit_qty, limited_by = available_qty, "t_plus_one"
            held_qty = self._positions[order.symbol].quantity
            qty = target if limit_qty >= target - 1e-9 else rule.partial_sell(limit_qty, held_qty, target)
            if qty <= 0:
                return None, limited_by or "below_min_lot"
        else:
            if wanted > max_fill:
                logger.debug(f"[Broker] 成交量限制: 委托{wanted}股 > 可成交{max_fill:.0f}股")
                max_fill = rule.round_buy(max_fill)
                if max_fill <= 0:
                    return None, cap_reason
                limited_by = cap_reason

            # 计算数量 (如果用 pct_cash 模式, 由 engine 预先计算 quantity)
            qty = min(wanted, max_fill)
            rounded = rule.round_buy(qty)
            if rounded < qty and limited_by is None:
                limited_by = "lot_rounding"
            qty = rounded
            if qty <= 0:
                return None, "below_min_lot"

        if order.is_buy:
            cost = fill_price * qty
            if cost + self._peek_fees(order.symbol, "buy", qty, fill_price, bar.date) > self.cash:
                # 按可用资金调整数量 (含全部费用)
                affordable = min(qty, self.affordable_quantity(order.symbol, fill_price, self.cash, bar.date))
                if affordable <= 0:
                    logger.warning(f"[Broker] 资金不足, 买单未成交: cash={self.cash:.0f}")
                    return None, "insufficient_cash"
                qty = affordable
                limited_by = "insufficient_cash"

        trade = self._execute(order, qty, fill_price, bar.date)
        self._volume_taken[self._listing_key(order.symbol)] = (
            date_str, self._volume_used(order.symbol, bar) + trade.quantity)
        return trade, limited_by

    def _listing_key(self, symbol: str) -> str:
        """The listed instrument behind `symbol` (market_rules.listing_key)."""
        return market_rules.listing_key(symbol, self.config.market)

    def _volume_used(self, symbol: str, bar) -> float:
        """Shares already filled on `symbol`'s listing in this bar (the participation budget used)."""
        taken = self._volume_taken.get(self._listing_key(symbol))
        return taken[1] if taken is not None and taken[0] == (_day(bar.date) or "") else 0.0

    def _fill_price(self, order: Order, bar) -> Optional[float]:
        """Fill price of a market or limit order on `bar`, None if a limit is not reached."""
        cfg = self.config
        if order.order_type == "market":
            # 市价单: 以当 bar 开盘价 + 滑点成交
            return bar.open * (1 + cfg.slippage_pct * (1 if order.is_buy else -1))
        if order.order_type == "limit":
            # 限价单: 检查能否成交
            if order.is_buy and bar.low <= order.price:
                return min(order.price, bar.open)
            if order.is_sell and bar.high >= order.price:
                return max(order.price, bar.open)
        if order.order_type == "stop" and bar.low <= order.price:
            # 止损: 盘中触及按止损价, 跳空低开按开盘价; 触发后按市价卖出, 计滑点
            return min(bar.open, order.price) * (1 - cfg.slippage_pct)
        if order.order_type == "target" and bar.high >= order.price:
            # 止盈 (限价卖出): 盘中触及按目标价, 跳空高开按开盘价
            return max(bar.open, order.price)
        return None

    def _execute(self, order: Order, qty, fill_price: float, when) -> Trade:
        """Book a fill of `qty` at `fill_price`: cash, position, T+1 record, fees."""
        reason = order.order_type if order.order_type in EXIT_TYPES else getattr(order, "exit_reason", "signal")
        date_str = _day(when) or ""
        pnl = 0.0
        if order.is_buy:
            fees = self._fees(order.symbol, "buy", qty, fill_price, when)
            commission = fees.total
            total_cost = fill_price * qty + commission
            self.cash -= total_cost

            # 记录T+1: 今天买入, 今天不能卖
            previous_bought = self._bought_quantity.get(order.symbol, 0.0) if self._buy_today.get(order.symbol) == date_str else 0.0
            self._buy_today[order.symbol] = date_str
            self._bought_quantity[order.symbol] = previous_bought + qty

            # 更新持仓 (平均成本含买入费用)
            pos = self._positions.get(order.symbol)
            if pos is None:
                self._positions[order.symbol] = Position(symbol=order.symbol, quantity=qty, avg_cost=total_cost / qty)
            else:
                total_qty = pos.quantity + qty
                pos.avg_cost = (pos.quantity * pos.avg_cost + total_cost) / total_qty
                pos.quantity = total_qty
        else:
            pos = self._positions[order.symbol]
            qty = min(qty, pos.quantity)
            fees = self._fees(order.symbol, "sell", qty, fill_price, when)
            commission = fees.total
            # 计算盈亏 (avg_cost已包含买入费用，此处commission为卖出费用)
            pnl = (fill_price - pos.avg_cost) * qty - commission
            self.cash += fill_price * qty - commission
            pos.quantity -= qty
            pos.realized_pnl += pnl
            if pos.quantity <= 1e-9:
                del self._positions[order.symbol]

        logger.debug(f"[Broker] 成交: {order.symbol} {order.direction} {qty}@{fill_price:.2f}")
        return Trade(
            trade_id=str(uuid.uuid4())[:8],
            order_id=order.order_id,
            symbol=order.symbol,
            direction=order.direction,
            quantity=qty,
            price=fill_price,
            commission=commission,
            timestamp=when,
            pnl=pnl,
            fees=fees.parts(),
            reason=reason,
        )
