"""
双均线趋势策略 (带 ATR 止损)

逻辑:
  - 短期均线首次上穿长期均线       → 开多
  - ATR 动态止损 (entry - K*ATR)  → 止损
  - 短期均线下穿长期均线            → 平仓
  
参考 vnpy CTA 策略写法
"""
import math
from backtesting.strategies.base import Strategy
from backtesting.factors.technical import TechnicalFactors


class DualMaStrategy(Strategy):
    """双均线 + ATR 止损策略"""

    name = "双均线ATR止损策略"

    parameters = {
        "short_period": 20,
        "long_period": 60,
        "atr_period": 14,
        "atr_multiplier": 2.0,   # 止损倍数
        "position_pct": 0.90,
    }

    def on_init(self):
        self._entry_price = None
        self._stop_price = None

    def on_bar(self):
        p = self.parameters
        long_p = p["long_period"]
        atr_p  = p["atr_period"]

        if self.data.current_index < long_p + atr_p:
            return

        short_ma = self.sma(p["short_period"])
        long_ma  = self.sma(long_p)

        # 计算 ATR
        window = self.data.get_window(atr_p + 5)
        atr_val = float(
            TechnicalFactors.atr(window["high"], window["low"], window["close"], atr_p).iloc[-1]
        ) if len(window) >= atr_p else float("nan")

        close = self.data.close()
        pos = self.get_position()
        holding = pos and pos.quantity > 0

        if math.isnan(short_ma) or math.isnan(long_ma) or math.isnan(atr_val):
            return

        if holding:
            # ATR 动态追踪止损
            new_stop = close - p["atr_multiplier"] * atr_val
            if self._stop_price:
                self._stop_price = max(self._stop_price, new_stop)
            else:
                self._stop_price = new_stop

            # 触发止损
            if close <= self._stop_price:
                self.close_position()
                self.log(f"ATR止损 stop={self._stop_price:.2f} close={close:.2f}")
                self._entry_price = None
                self._stop_price = None
                return

            # 均线死叉平仓
            if short_ma < long_ma:
                self.close_position()
                self.log(f"均线死叉平仓 short={short_ma:.2f} long={long_ma:.2f}")
                self._entry_price = None
                self._stop_price = None
        else:
            # 均线金叉开仓
            if short_ma > long_ma:
                self.buy(pct_cash=p["position_pct"])
                self._entry_price = close
                self._stop_price = close - p["atr_multiplier"] * atr_val
                self.log(
                    f"均线金叉买入 short={short_ma:.2f} long={long_ma:.2f} "
                    f"stop={self._stop_price:.2f}"
                )
