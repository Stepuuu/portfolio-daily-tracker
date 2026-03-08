"""
回测引擎 - 核心调度器
灵感: backtrader 的 Cerebro + vnpy 的事件驱动引擎

Cerebro 式的调度:
  engine.add_data(symbol, df)
  engine.add_strategy(MyStrategy)
  result = engine.run()
  engine.plot()
"""
import uuid
import logging
from typing import Optional, Type, Dict, List, Any
from datetime import datetime
import pandas as pd

from .data.feed import DataFeed
from .data.loader import DataLoader
from .data.cleaner import DataCleaner
from .data.store import DataStore
from .strategies.base import Strategy, Order, Position
from .broker.simulated import SimulatedBroker, BrokerConfig
from .analyzer.stats import BacktestStats

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    回测引擎 (Cerebro)
    
    使用示例:
    --------
    engine = BacktestEngine(initial_cash=500_000)
    engine.add_data("600519", "2020-01-01", "2024-01-01")
    engine.add_strategy(SmaCrossStrategy, fast_period=10, slow_period=30)
    result = engine.run()
    print(result.summary())
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        broker_config: Optional[BrokerConfig] = None,
        use_cache: bool = True,
    ):
        self.initial_cash = initial_cash
        self.broker_config = broker_config or BrokerConfig()
        self.use_cache = use_cache

        self._strategy_class: Optional[Type[Strategy]] = None
        self._strategy_params: Dict[str, Any] = {}
        self._strategy: Optional[Strategy] = None

        self._data_feeds: Dict[str, DataFeed] = {}  # symbol -> DataFeed
        self._primary_symbol: Optional[str] = None

        self.broker = SimulatedBroker(initial_cash, self.broker_config)
        self._store = DataStore()
        self._loader = DataLoader(source="akshare")

        self._run_id = str(uuid.uuid4())[:8]
        self._run_date: Optional[str] = None

    # ------------------------------------------------------------------ #
    #  配置接口
    # ------------------------------------------------------------------ #

    def add_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        df: Optional[pd.DataFrame] = None,
        is_primary: bool = True,
        warmup: int = 60,
    ):
        """
        添加标的数据.
        
        Args:
            symbol:     股票代码
            start_date: 开始日期 "YYYY-MM-DD"
            end_date:   结束日期 "YYYY-MM-DD"
            adjust:     复权方式 "qfq"/"hfq"/""
            df:         直接传入 DataFrame (跳过数据下载)
            is_primary: 是否为主标的
            warmup:     预热期 bar 数
        """
        if df is None:
            # 先检查本地缓存
            if self.use_cache and self._store.is_data_available(
                symbol, start_date, end_date, adjust
            ):
                logger.info(f"[Engine] 从本地缓存加载 {symbol}")
                df = self._store.load_daily(symbol, start_date, end_date, adjust)
            else:
                logger.info(f"[Engine] 从 AKShare 下载 {symbol} {start_date}~{end_date}")
                df = self._loader.get_daily(symbol, start_date, end_date, adjust)
                # 清洗
                df = (
                    DataCleaner(df)
                    .fill_missing()
                    .clip_price()
                    .add_returns()
                    .result
                )
                # 存储到本地
                self._store.save_daily(symbol, df, adjust)

        feed = DataFeed(symbol, df, warmup=warmup)
        self._data_feeds[symbol] = feed

        if is_primary or self._primary_symbol is None:
            self._primary_symbol = symbol

        logger.info(f"[Engine] 添加数据: {symbol} ({len(df)} 个交易日)")

    def add_strategy(
        self,
        strategy_class: Type[Strategy],
        **params,
    ):
        """
        添加策略.
        
        Args:
            strategy_class: 策略类
            **params:       覆盖策略默认参数
        """
        self._strategy_class = strategy_class
        self._strategy_params = params

    # ------------------------------------------------------------------ #
    #  下单接口 (由策略调用)
    # ------------------------------------------------------------------ #

    def submit_order(
        self,
        strategy: Strategy,
        symbol: str,
        direction: str,
        quantity: Optional[float],
        price: float,
        order_type: str,
        pct_cash: Optional[float] = None,
    ) -> Optional[Order]:
        """处理策略下单请求"""

        feed = self._data_feeds.get(symbol)
        if feed is None or feed.current_bar is None:
            return None

        current_price = feed.current_bar.close

        # 计算数量
        if quantity is None and pct_cash is not None and direction == "buy":
            available = self.broker.cash * pct_cash
            cfg = self.broker.config
            max_qty = available / (current_price * (1 + cfg.commission_buy))
            quantity = (int(max_qty) // cfg.lot_size) * cfg.lot_size
            if quantity <= 0:
                logger.warning(f"[Engine] 资金不足以买入 {symbol}")
                return None
        elif quantity is None:
            # 卖出时, 默认全部持仓
            pos = self.broker.get_position(symbol)
            quantity = pos.quantity if pos else 0

        if quantity <= 0:
            return None

        order = Order(
            order_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            direction=direction,
            order_type=order_type,
            quantity=quantity,
            price=price,
            timestamp=feed.current_bar.date,
        )

        return self.broker.submit_order(order)

    def get_position(self, symbol: str) -> Optional[Position]:
        return self.broker.get_position(symbol)

    # ------------------------------------------------------------------ #
    #  运行引擎
    # ------------------------------------------------------------------ #

    def run(self) -> "BacktestResult":
        """
        执行回测.
        返回 BacktestResult 对象.
        """
        if self._strategy_class is None:
            raise ValueError("请先调用 add_strategy() 添加策略")
        if not self._data_feeds:
            raise ValueError("请先调用 add_data() 添加数据")

        self._run_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[Engine] 开始回测 run_id={self._run_id}")

        # 初始化策略
        self._strategy = self._strategy_class()
        self._strategy.parameters = {
            **self._strategy.parameters,
            **self._strategy_params,
        }
        self._strategy._set_engine(self)
        primary_feed = self._data_feeds[self._primary_symbol]
        self._strategy._set_data(primary_feed, self._data_feeds)
        self._strategy.on_init()
        self._strategy.on_start()

        # 重置所有 feed
        for feed in self._data_feeds.values():
            feed.reset()

        # 主循环 - 以主标的日历驱动
        bar_count = 0
        while primary_feed.advance():
            current_bar = primary_feed.current_bar
            if current_bar is None:
                continue

            # 同步其他标的 (如果有)
            date = current_bar.date
            for sym, feed in self._data_feeds.items():
                if sym != self._primary_symbol:
                    # 将辅助标的推进到不晚于主标的日期
                    while not feed.is_done:
                        next_bar_idx = feed._cursor + 1
                        if next_bar_idx < feed._len:
                            next_date = feed._df.index[next_bar_idx]
                            if next_date <= date:
                                feed.advance()
                            else:
                                break
                        else:
                            break

            # 处理上一 bar 的挂单 (昨信号今撮合)
            trades = self.broker.process_bar(self._primary_symbol, current_bar)
            for trade in trades:
                self._strategy.on_trade(trade)

            # 更新持仓市值
            self.broker.update_positions_price(
                self._primary_symbol, current_bar.close
            )

            # 记录净值
            self.broker.record_equity(
                date,
                {self._primary_symbol: current_bar.close},
            )

            # 调用策略 (跳过预热期)
            if not primary_feed.is_warmup:
                self._strategy.on_bar()

            bar_count += 1

        # 结束: 强制平仓 (可选)
        pos = self.broker.get_position(self._primary_symbol)
        if pos and pos.quantity > 0:
            last_bar = primary_feed.current_bar
            if last_bar:
                self.broker.process_bar(self._primary_symbol, last_bar)

        self._strategy.on_stop()
        logger.info(
            f"[Engine] 回测完成 共 {bar_count} bars, "
            f"成交 {len(self.broker.trades)} 笔"
        )

        return BacktestResult(
            run_id=self._run_id,
            run_date=self._run_date,
            strategy_name=self._strategy.name,
            strategy_params=self._strategy.parameters,
            primary_symbol=self._primary_symbol,
            broker=self.broker,
            strategy=self._strategy,
            data_feeds=self._data_feeds,
        )


class BacktestResult:
    """
    回测结果容器
    
    提供:
      - summary()   文字摘要
      - stats       BacktestStats 详细统计
      - trades      成交记录列表
      - equity_df   净值曲线 DataFrame
    """

    def __init__(
        self,
        run_id: str,
        run_date: str,
        strategy_name: str,
        strategy_params: Dict,
        primary_symbol: str,
        broker: SimulatedBroker,
        strategy: Strategy,
        data_feeds: Dict[str, DataFeed],
    ):
        self.run_id = run_id
        self.run_date = run_date
        self.strategy_name = strategy_name
        self.strategy_params = strategy_params
        self.primary_symbol = primary_symbol
        self._broker = broker
        self._strategy = strategy
        self._data_feeds = data_feeds

        # 延迟计算统计
        self._stats: Optional[BacktestStats] = None

    @property
    def stats(self) -> BacktestStats:
        if self._stats is None:
            # 提取基准价格（主标的收盘价序列 = buy-and-hold）
            benchmark_prices = None
            if self.primary_symbol in self._data_feeds:
                feed = self._data_feeds[self.primary_symbol]
                if hasattr(feed, '_df') and 'close' in feed._df.columns:
                    benchmark_prices = feed._df['close'].tolist()
            self._stats = BacktestStats(
                equity_curve=self._broker.equity_curve,
                trades=self._broker.trades,
                initial_cash=self._broker.initial_cash,
                benchmark_prices=benchmark_prices,
            )
        return self._stats

    @property
    def equity_df(self) -> pd.DataFrame:
        """净值曲线 DataFrame"""
        if not self._broker.equity_curve:
            return pd.DataFrame()
        df = pd.DataFrame(
            self._broker.equity_curve,
            columns=["date", "total_value", "cash", "market_value"],
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df["net_value"] = df["total_value"] / self._broker.initial_cash
        df["drawdown"] = df["net_value"] / df["net_value"].cummax() - 1
        return df

    @property
    def trades(self):
        return self._broker.trades

    def summary(self) -> str:
        """生成文字摘要"""
        s = self.stats
        lines = [
            f"{'='*60}",
            f"  回测结果摘要  [{self.run_date}]",
            f"{'='*60}",
            f"策略:     {self.strategy_name}",
            f"标的:     {self.primary_symbol}",
            f"参数:     {self.strategy_params}",
            f"{'─'*60}",
            f"初始资金: {self._broker.initial_cash:>15,.0f} 元",
            f"最终净值: {s.final_value:>15,.0f} 元",
            f"总收益率: {s.total_return:>14.2%}",
            f"年化收益: {s.annualized_return:>14.2%}",
            f"基准收益: {s.benchmark_return:>14.2%}  (持有不动)",
            f"超额收益: {s.alpha:>14.2%}",
            f"{'─'*60}",
            f"夏普比率: {s.sharpe_ratio:>14.4f}",
            f"索提诺率: {s.sortino_ratio:>14.4f}",
            f"卡玛比率: {s.calmar_ratio:>14.4f}",
            f"最大回撤: {s.max_drawdown:>14.2%}",
            f"{'─'*60}",
            f"交易次数: {s.total_trades:>14d}",
            f"胜率:     {s.win_rate:>14.2%}",
            f"盈亏比:   {s.profit_factor:>14.4f}",
            f"平均盈利: {s.avg_profit:>14.2f} 元",
            f"平均亏损: {s.avg_loss:>14.2f} 元",
            f"最大单笔盈利: {s.max_single_profit:>10.2f} 元",
            f"最大单笔亏损: {s.max_single_loss:>10.2f} 元",
            f"{'='*60}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        """转为字典 (用于 API 返回)"""
        s = self.stats
        return {
            "run_id": self.run_id,
            "run_date": self.run_date,
            "strategy_name": self.strategy_name,
            "strategy_params": self.strategy_params,
            "primary_symbol": self.primary_symbol,
            "initial_cash": self._broker.initial_cash,
            "final_value": round(s.final_value, 2),
            "total_return": round(s.total_return, 6),
            "annualized_return": round(s.annualized_return, 6),
            "benchmark_return": round(s.benchmark_return, 6),
            "alpha": round(s.alpha, 6),
            "sharpe_ratio": round(s.sharpe_ratio, 4),
            "sortino_ratio": round(s.sortino_ratio, 4),
            "calmar_ratio": round(s.calmar_ratio, 4),
            "max_drawdown": round(s.max_drawdown, 6),
            "total_trades": s.total_trades,
            "win_rate": round(s.win_rate, 4),
            "profit_factor": round(s.profit_factor, 4),
            "avg_profit": round(s.avg_profit, 2),
            "avg_loss": round(s.avg_loss, 2),
            "max_single_profit": round(s.max_single_profit, 2),
            "max_single_loss": round(s.max_single_loss, 2),
        }
