"""
技术指标因子
灵感: qlib 的 Alpha158 因子集 + backtrader 内置 122 个指标

包含：
  趋势类: MA, EMA, MACD, ADX
  震荡类: RSI, KDJ, Stochastic, Williams %R
  波动类: ATR, Bollinger Bands, 历史波动率
  量价类: OBV, VWAP, Volume MA
  形态类: 涨跌幅、价差比
"""
import numpy as np
import pandas as pd
from typing import Optional, Tuple


class TechnicalFactors:
    """
    技术指标计算工具集 (静态方法, 无状态)
    
    所有方法接受 pd.Series 或 pd.DataFrame, 返回 pd.Series.
    支持直接作为 DataFeed 的因子添加.
    """

    # ================================================================ #
    #  趋势指标
    # ================================================================ #

    @staticmethod
    def sma(close: pd.Series, period: int) -> pd.Series:
        """简单移动平均"""
        return close.rolling(period).mean()

    @staticmethod
    def ema(close: pd.Series, period: int) -> pd.Series:
        """指数移动平均"""
        return close.ewm(span=period, adjust=False).mean()

    @staticmethod
    def macd(
        close: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD 指标.
        返回: (macd_line, signal_line, histogram)
        """
        ema_fast = TechnicalFactors.ema(close, fast)
        ema_slow = TechnicalFactors.ema(close, slow)
        macd_line = ema_fast - ema_slow
        signal_line = TechnicalFactors.ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def dma(close: pd.Series, short: int = 10, long: int = 50) -> pd.Series:
        """价格短长均线差: SMA(short) - SMA(long)"""
        return TechnicalFactors.sma(close, short) - TechnicalFactors.sma(close, long)

    @staticmethod
    def adx(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """平均趋向指数 (ADX), Wilder (1978) 的定义 - 衡量趋势强度.

        For each bar t >= 1 (the first bar has no previous bar, so no move: not a zero
        move): up = high_t - high_{t-1}, down = low_{t-1} - low_t; +DM = up when up > down
        and up > 0, else 0; -DM = down when down > up and down > 0, else 0; TR is the true
        range. TR, +DM and -DM are smoothed with Wilder's average (``wilder``: the first
        value is the mean of the first N, then avg = (avg * (N - 1) + x) / N), so the
        directional indicators first exist on bar N:

            +DI = 100 * smoothed +DM / ATR,  -DI = 100 * smoothed -DM / ATR
            DX = 100 * |+DI - -DI| / (+DI + -DI)
            ADX = Wilder's average of DX (the mean of the first N DX values, on bar 2N - 1)

        DI is undefined (NaN) when ATR is 0 and DX when +DI + -DI is 0 (no directional
        move at all); an undefined value starts the average that follows it again, like
        a missing price. Bar t uses bars <= t only.
        """
        period = TechnicalFactors._period(period, "ADX")
        tr = TechnicalFactors.true_range(high, low, close)
        up_move = high.diff()
        down_move = -low.diff()
        moved = up_move.notna() & down_move.notna()
        pos_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
                           index=close.index).where(moved)
        neg_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
                           index=close.index).where(moved)

        atr = TechnicalFactors.wilder(tr, period)
        safe_atr = atr.where(atr > 0)
        pos_di = 100 * TechnicalFactors.wilder(pos_dm, period) / safe_atr
        neg_di = 100 * TechnicalFactors.wilder(neg_dm, period) / safe_atr
        total = pos_di + neg_di
        dx = 100 * (pos_di - neg_di).abs() / total.where(total > 0)
        return TechnicalFactors.wilder(dx, period)

    @staticmethod
    def wilder(values: pd.Series, period: int) -> pd.Series:
        """Wilder's smoothing (an EMA with alpha 1/N, seeded with a simple mean).

        The first value is the mean of the first `period` values, on the bar that
        completes them; after that avg_t = (avg_{t-1} * (period - 1) + x_t) / period.
        NaN before that. A missing value (NaN) breaks the series: the average starts
        again and needs `period` new values. Bar t uses values <= t only.
        """
        period = TechnicalFactors._period(period, "Wilder")
        data = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
        out = np.full(len(data), np.nan)
        average, count = 0.0, 0
        for i, value in enumerate(data):
            if not np.isfinite(value):
                average, count = 0.0, 0
                continue
            count += 1
            if count < period:
                average += value
                continue
            if count == period:
                average = (average + value) / period
            else:
                average = (average * (period - 1) + value) / period
            out[i] = average
        return pd.Series(out, index=values.index)

    @staticmethod
    def _period(period, what: str) -> int:
        if isinstance(period, bool) or int(period) != period or period < 1:
            raise ValueError(f"{what} period must be a positive integer, got {period!r}")
        return int(period)

    # ================================================================ #
    #  震荡指标
    # ================================================================ #

    @staticmethod
    def rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """相对强弱指数 (RSI), Wilder (1978) 的定义.

        change_t = close_t - close_{t-1}; the first bar has no previous close, so it has
        no change (it is not a zero change). gain_t = max(change_t, 0), loss_t =
        max(-change_t, 0).

        The first averages are the simple means of the first `period` changes, on the
        bar that completes them (bar `period`, counting from 0). After that, Wilder's
        smoothing: avg_t = (avg_{t-1} * (period - 1) + x_t) / period.

        RSI_t = 100 - 100 / (1 + avg_gain_t / avg_loss_t); 100 when avg_loss_t is 0 and
        avg_gain_t > 0; NaN (undefined) before bar `period` and when both averages are
        0 (no movement at all). A missing close breaks the series: the averages start
        again and need `period` new changes. Bar t uses bars <= t only.
        """
        if isinstance(period, bool) or int(period) != period or period < 1:
            raise ValueError(f"RSI period must be a positive integer, got {period!r}")
        period = int(period)
        values = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
        out = np.full(len(values), np.nan)
        avg_gain = avg_loss = 0.0
        count = 0
        for i in range(1, len(values)):
            change = values[i] - values[i - 1]
            if not np.isfinite(change):
                avg_gain = avg_loss = 0.0
                count = 0
                continue
            gain, loss = (change, 0.0) if change > 0 else (0.0, -change)
            count += 1
            if count < period:
                avg_gain += gain
                avg_loss += loss
                continue
            if count == period:
                avg_gain = (avg_gain + gain) / period
                avg_loss = (avg_loss + loss) / period
            else:
                avg_gain = (avg_gain * (period - 1) + gain) / period
                avg_loss = (avg_loss * (period - 1) + loss) / period
            if avg_loss > 0:
                out[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
            elif avg_gain > 0:
                out[i] = 100.0
        return pd.Series(out, index=close.index)

    @staticmethod
    def kdj(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 9,
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        KDJ 随机指标.
        返回: (K, D, J)
        """
        low_min = low.rolling(period).min()
        high_max = high.rolling(period).max()
        rsv = 100 * (close - low_min) / (high_max - low_min + 1e-8)

        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        j = 3 * k - 2 * d
        return k, d, j

    @staticmethod
    def williams_r(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """威廉指标 %R"""
        highest_high = high.rolling(period).max()
        lowest_low = low.rolling(period).min()
        wr = -100 * (highest_high - close) / (highest_high - lowest_low + 1e-8)
        return wr

    # ================================================================ #
    #  波动指标
    # ================================================================ #

    @staticmethod
    def true_range(
        high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.Series:
        """真实波幅 (TR): max(high - low, |high - prev_close|, |low - prev_close|).

        The first bar has no previous close, so its true range is undefined (NaN), not
        high - low: ATR(N) averages N true ranges that all have a previous close.
        """
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        ], axis=1).max(axis=1, skipna=False)
        return tr

    @staticmethod
    def atr(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """平均真实波幅 (ATR), Wilder (1978) 的定义.

        Wilder's average of the true range (``wilder``): the first value is the simple
        mean of the first N true ranges, on bar N (the first bar has no previous close
        and no true range); after that ATR_t = (ATR_{t-1} * (N - 1) + TR_t) / N.
        """
        tr = TechnicalFactors.true_range(high, low, close)
        return TechnicalFactors.wilder(tr, period)

    @staticmethod
    def bollinger_bands(
        close: pd.Series,
        period: int = 20,
        num_std: float = 2.0,
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        布林带.
        返回: (upper, middle, lower)
        """
        middle = TechnicalFactors.sma(close, period)
        std = close.rolling(period).std()
        upper = middle + num_std * std
        lower = middle - num_std * std
        return upper, middle, lower

    @staticmethod
    def hist_vol(close: pd.Series, period: int = 20, annualize: bool = True) -> pd.Series:
        """
        历史波动率.
        annualize: 是否年化 (乘以 sqrt(252))
        """
        log_ret = np.log(close / close.shift(1))
        vol = log_ret.rolling(period).std()
        if annualize:
            vol = vol * np.sqrt(252)
        return vol

    # ================================================================ #
    #  量价指标
    # ================================================================ #

    @staticmethod
    def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """能量潮 (OBV)"""
        direction = np.sign(close.diff())
        obv = (direction * volume).cumsum()
        return obv

    @staticmethod
    def vwap(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        volume: pd.Series,
        period: Optional[int] = None,
    ) -> pd.Series:
        """
        成交量加权平均价 (VWAP).
        period=None 则为全周期累计 VWAP.
        """
        typical = (high + low + close) / 3
        if period:
            tp_vol = (typical * volume).rolling(period).sum()
            vol_sum = volume.rolling(period).sum()
        else:
            tp_vol = (typical * volume).cumsum()
            vol_sum = volume.cumsum()
        return tp_vol / vol_sum.replace(0, float("nan"))

    @staticmethod
    def volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
        """成交量移动均线"""
        return volume.rolling(period).mean()

    @staticmethod
    def mfi(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        volume: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """资金流量指标 (MFI)

        typical = (high + low + close) / 3, flow = typical * volume. A bar's flow is
        positive when its typical price is above the previous bar's, negative when below,
        and neither when equal; the first bar has no previous typical price and no flow.
        MFI(N) = 100 * positive / (positive + negative), the rolling sums of positive and
        negative money flow over the last N bars (= 100 - 100 / (1 + positive / negative)):
        first on bar N, 100 without negative flow, NaN when both sums are 0.
        """
        typical = (high + low + close) / 3
        money_flow = typical * volume
        previous = typical.shift(1)
        known = previous.notna() & typical.notna()
        pos_flow = money_flow.where(typical > previous, 0.0).where(known)
        neg_flow = money_flow.where(typical < previous, 0.0).where(known)
        pos_sum = pos_flow.rolling(period).sum()
        total = pos_sum + neg_flow.rolling(period).sum()
        return 100 * pos_sum / total.replace(0, float("nan"))

    # ================================================================ #
    #  价格特征 (Alpha158 风格)
    # ================================================================ #

    @staticmethod
    def price_momentum(close: pd.Series, period: int = 20) -> pd.Series:
        """动量: close / close[N] - 1"""
        return close / close.shift(period) - 1

    @staticmethod
    def high_low_ratio(high: pd.Series, low: pd.Series, period: int = 20) -> pd.Series:
        """高低价比: (MAX(high, N) - MIN(low, N)) / close"""
        return (high.rolling(period).max() - low.rolling(period).min())

    @staticmethod
    def close_vs_ma(close: pd.Series, period: int = 20) -> pd.Series:
        """收盘价相对均线偏离率"""
        ma = TechnicalFactors.sma(close, period)
        return close / ma - 1

    @staticmethod
    def compute_all(df: pd.DataFrame) -> pd.DataFrame:
        """
        一键计算常用技术因子并追加到 DataFrame.
        适合快速构建因子面板.
        """
        result = df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # 趋势
        for period in [5, 10, 20, 60]:
            result[f"sma_{period}"] = TechnicalFactors.sma(close, period)
            result[f"ema_{period}"] = TechnicalFactors.ema(close, period)
            result[f"close_vs_ma_{period}"] = TechnicalFactors.close_vs_ma(close, period)
            result[f"momentum_{period}"] = TechnicalFactors.price_momentum(close, period)

        # MACD
        macd, signal, hist = TechnicalFactors.macd(close)
        result["macd"] = macd
        result["macd_signal"] = signal
        result["macd_hist"] = hist

        # 震荡
        result["rsi_6"] = TechnicalFactors.rsi(close, 6)
        result["rsi_14"] = TechnicalFactors.rsi(close, 14)
        k, d, j = TechnicalFactors.kdj(high, low, close)
        result["kdj_k"] = k
        result["kdj_d"] = d
        result["kdj_j"] = j

        # 波动
        result["atr_14"] = TechnicalFactors.atr(high, low, close)
        result["hist_vol_20"] = TechnicalFactors.hist_vol(close, 20)
        upper, mid, lower = TechnicalFactors.bollinger_bands(close)
        result["boll_upper"] = upper
        result["boll_mid"] = mid
        result["boll_lower"] = lower
        result["boll_width"] = (upper - lower) / mid

        # 量价
        result["vwap_20"] = TechnicalFactors.vwap(high, low, close, volume, 20)
        result["volume_ma_20"] = TechnicalFactors.volume_ma(volume, 20)
        result["volume_ratio"] = volume / result["volume_ma_20"]
        result["obv"] = TechnicalFactors.obv(close, volume)

        return result
