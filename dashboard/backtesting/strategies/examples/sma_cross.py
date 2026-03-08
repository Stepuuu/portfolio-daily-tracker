"""
双均线金叉死叉策略 (经典 SMA CrossOver)
类似 backtrader 官方文档示例

逻辑:
  - 金叉 (快线上穿慢线) → 买入
  - 死叉 (快线下穿慢线) → 卖出清仓
"""
import math
from backtesting.strategies.base import Strategy


class SmaCrossStrategy(Strategy):
    """SMA 双均线金叉策略"""

    name = "SMA金叉策略"

    parameters = {
        "fast_period": 10,   # 快线周期
        "slow_period": 30,   # 慢线周期
        "position_pct": 0.95,  # 每次用仓位比例
    }

    def on_init(self):
        self.log(
            f"初始化: fast={self.parameters['fast_period']}, "
            f"slow={self.parameters['slow_period']}"
        )
        self._prev_fast = None
        self._prev_slow = None

    def on_bar(self):
        fast_period = self.parameters["fast_period"]
        slow_period = self.parameters["slow_period"]

        # 数据不足时跳过
        if self.data.current_index < slow_period:
            return

        fast = self.sma(fast_period)
        slow = self.sma(slow_period)

        if math.isnan(fast) or math.isnan(slow):
            return

        # 判断交叉
        if self._prev_fast is not None and self._prev_slow is not None:
            prev_diff = self._prev_fast - self._prev_slow
            curr_diff = fast - slow

            # 金叉: 从负变正
            if prev_diff < 0 and curr_diff >= 0:
                pos = self.get_position()
                if pos is None or pos.quantity == 0:
                    self.buy(pct_cash=self.parameters["position_pct"])
                    self.log(f"金叉买入 fast={fast:.2f} slow={slow:.2f}")

            # 死叉: 从正变负
            elif prev_diff > 0 and curr_diff <= 0:
                pos = self.get_position()
                if pos and pos.quantity > 0:
                    self.close_position()
                    self.log(f"死叉卖出 fast={fast:.2f} slow={slow:.2f}")

        self._prev_fast = fast
        self._prev_slow = slow
