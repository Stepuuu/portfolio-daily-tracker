"""
RSI 均值回归策略

逻辑:
  - RSI 超卖 (< 30) + 收盘价在长期均线之上 → 买入
  - RSI 超买 (> 70) → 卖出清仓
  - 或跌破止损线 → 强制止损
"""
import math
from backtesting.strategies.base import Strategy


class RsiMeanReversionStrategy(Strategy):
    """RSI 均值回归策略"""

    name = "RSI均值回归策略"

    parameters = {
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "ma_filter_period": 60,   # 趋势过滤均线
        "stop_loss_pct": 0.05,    # 止损线5%
        "position_pct": 0.95,
    }

    def on_init(self):
        self._entry_price = None

    def on_bar(self):
        params = self.parameters
        ma_period = params["ma_filter_period"]

        if self.data.current_index < max(params["rsi_period"], ma_period):
            return

        rsi_val = self.rsi(params["rsi_period"])
        ma_val  = self.sma(ma_period)
        close   = self.data.close()

        if math.isnan(rsi_val) or math.isnan(ma_val):
            return

        pos = self.get_position()
        holding = pos and pos.quantity > 0

        if holding:
            # 止损检查
            if self._entry_price and close < self._entry_price * (1 - params["stop_loss_pct"]):
                self.close_position()
                self.log(f"触发止损 entry={self._entry_price:.2f} close={close:.2f}")
                self._entry_price = None
                return

            # RSI 超买平仓
            if rsi_val > params["rsi_overbought"]:
                self.close_position()
                self.log(f"RSI超买平仓 RSI={rsi_val:.1f}")
                self._entry_price = None
        else:
            # RSI 超卖 + 价格高于长期均线 (大趋势向上)
            if rsi_val < params["rsi_oversold"] and close > ma_val:
                self.buy(pct_cash=params["position_pct"])
                self._entry_price = close
                self.log(f"RSI超卖买入 RSI={rsi_val:.1f} close={close:.2f}")
