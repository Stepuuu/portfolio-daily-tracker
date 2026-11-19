"""Technical indicators: hand-computed values, and no value before the history it needs.

An indicator over N price changes (RSI, ATR, MFI, the ADX moves) is undefined until N
real changes exist: the first bar has no previous close, so it has no change, and it
must not be counted as a zero change.
"""
import math

import numpy as np
import pandas as pd
import pytest

from backtesting.factors.technical import TechnicalFactors


def series(values):
    return pd.Series([float(v) for v in values], index=pd.bdate_range("2024-01-02", periods=len(values)))


def test_rsi_is_wilder_from_the_first_period_changes():
    # Closes 10, 11, 10, 12, 13, 12: changes +1, -1, +2, +1, -1. With period 3 the first
    # average is the mean of changes 1..3 (gain 1, loss 1/3) at bar 3; then Wilder's
    # smoothing avg = (avg * 2 + x) / 3.
    rsi = TechnicalFactors.rsi(series([10, 11, 10, 12, 13, 12]), 3).tolist()
    assert all(math.isnan(v) for v in rsi[:3])
    assert rsi[3] == pytest.approx(75.0)                          # RS 1 / (1/3) = 3
    assert rsi[4] == pytest.approx(100 - 100 / (1 + 1 / (2 / 9)))  # gain 1, loss 2/9
    assert rsi[5] == pytest.approx(100 - 100 / (1 + (2 / 3) / (13 / 27)))


def test_rsi_14_needs_fourteen_real_changes():
    rsi = TechnicalFactors.rsi(series(range(100, 81, -1)), 14)
    assert rsi.iloc[:14].isna().all()                             # bars 0..13 have 0..13 changes
    assert rsi.iloc[14] == 0.0


def test_rsi_of_a_series_without_losses_is_100_and_of_a_flat_one_undefined():
    assert (TechnicalFactors.rsi(series(range(1, 21)), 14).iloc[14:] == 100.0).all()
    assert TechnicalFactors.rsi(series([5.0] * 20), 14).isna().all()


def test_rsi_never_reads_a_later_bar():
    closes = series(np.linspace(10, 20, 40) + np.sin(np.arange(40)))
    full = TechnicalFactors.rsi(closes, 14)
    for end in (15, 22, 39):
        assert TechnicalFactors.rsi(closes.iloc[:end + 1], 14).iloc[-1] == pytest.approx(full.iloc[end])


# ------------------------------------------------------------------ #
#  The other indicators over price changes: the same rule
# ------------------------------------------------------------------ #

CLOSES = [10.0, 10.5, 10.2, 10.8, 11.0, 10.6, 10.9, 11.4, 11.1, 11.6, 11.3, 11.9]


def hlc(closes=CLOSES):
    close = series(closes)
    return close + 0.3, close - 0.3, close


def test_true_range_and_atr_need_a_previous_close():
    high, low, close = hlc()
    tr = TechnicalFactors.true_range(high, low, close)
    assert math.isnan(tr.iloc[0])                                 # no previous close, no true range
    assert tr.iloc[1] == pytest.approx(max(0.6, abs(10.8 - 10.0), abs(10.2 - 10.0)))
    atr = TechnicalFactors.atr(high, low, close, 3)
    assert atr.iloc[:3].isna().all()
    assert atr.iloc[3] == pytest.approx(tr.iloc[1:4].mean())


def test_adx_counts_only_real_directional_moves():
    # +DM/-DM need the previous bar, so DI(3) first exists on bar 3 and ADX(3), the
    # 3-bar mean of DX, on bar 5.
    high, low, close = hlc()
    adx = TechnicalFactors.adx(high, low, close, 3)
    assert adx.iloc[:5].isna().all()
    assert not math.isnan(adx.iloc[5])


def test_mfi_counts_only_real_flows_and_equal_typical_prices_count_for_neither():
    one_price = series([10, 11, 10, 12])
    volume = series([100, 200, 300, 400])
    mfi = TechnicalFactors.mfi(one_price, one_price, one_price, volume, 3)
    # Flows: +11*200, -10*300, +12*400 -> 100 * 7000 / 10000.
    assert mfi.iloc[:3].isna().all() and mfi.iloc[3] == pytest.approx(70.0)
    flat_then_up = series([10, 10, 11, 12])
    mfi = TechnicalFactors.mfi(flat_then_up, flat_then_up, flat_then_up, volume, 3)
    assert mfi.iloc[3] == pytest.approx(100.0)                    # no negative flow at all


# ------------------------------------------------------------------ #
#  ATR, ADX and MFI by their standard definitions (cr_r3b (A) technical.py:87,209)
# ------------------------------------------------------------------ #

# The review's example: N = 2 and high = low = close = 10, 11, 10, 13, 12 (unit volume).
#   true range (from bar 1): 1, 1, 3, 1
#   ATR: seed on bar 2 = mean(1, 1) = 1; Wilder: (1 * 1 + 3) / 2 = 2, (2 * 1 + 1) / 2 = 1.5
#   +DM: 1, 0, 3, 0   -DM: 0, 1, 0, 1
#   smoothed +DM: 0.5, (0.5 + 3) / 2 = 1.75, (1.75 + 0) / 2 = 0.875
#   smoothed -DM: 0.5, (0.5 + 0) / 2 = 0.25, (0.25 + 1) / 2 = 0.625
#   DI+ / DI-: bar 2 50 / 50 -> DX 0; bar 3 87.5 / 12.5 -> DX 75; bar 4 58.33 / 41.67 -> DX 16.67
#   ADX: seed on bar 3 = mean(0, 75) = 37.5; bar 4 (37.5 + 16.67) / 2 = 27.083
#   MFI: flows +11, -10, +13, -12; last bar 100 * 13 / (13 + 12) = 52
REVIEW = [10, 11, 10, 13, 12]


def test_atr_is_wilders_average_seeded_with_the_mean_of_the_first_n_true_ranges():
    one = series(REVIEW)
    atr = TechnicalFactors.atr(one, one, one, 2).tolist()
    assert math.isnan(atr[0]) and math.isnan(atr[1])
    assert atr[2:] == pytest.approx([1.0, 2.0, 1.5])


def test_adx_is_wilders_average_of_dx_seeded_with_the_mean_of_the_first_n():
    one = series(REVIEW)
    adx = TechnicalFactors.adx(one, one, one, 2).tolist()
    assert all(math.isnan(v) for v in adx[:3])                     # first DX on bar 2, first ADX on bar 3
    assert adx[3] == pytest.approx(37.5)
    assert adx[4] == pytest.approx((37.5 + 100 / 6) / 2)


def test_mfi_sums_positive_and_negative_money_flow_over_n_bars():
    one = series(REVIEW)
    mfi = TechnicalFactors.mfi(one, one, one, series([1] * 5), 2).tolist()
    assert math.isnan(mfi[0]) and math.isnan(mfi[1])
    assert mfi[2:] == pytest.approx([100 * 11 / 21, 100 * 13 / 23, 52.0])


# A second example, N = 3, computed by hand:
#   high  12 13 12 14 15 13 14      low 10 11 10 12 12 11 12      close 11 12 11 13 14 12 13
#   volume 100 200 100 300 200 100 200
#   true range (bars 1..6): 2, 2, 3, 3, 3, 2
#   ATR(3): bar 3 (2 + 2 + 3) / 3 = 7/3; bar 4 (7/3 * 2 + 3) / 3 = 23/9; bar 5 (23/9 * 2 + 3) / 3 = 73/27;
#           bar 6 (73/27 * 2 + 2) / 3 = 200/81
#   +DM (bars 1..6): 1, 0, 2, 1, 0, 1    -DM: 0, 1, 0, 0, 1, 0   (bar 4: up 1 > down 0)
#   smoothed +DM: bar 3 1, bar 4 1, bar 5 2/3, bar 6 7/9
#   smoothed -DM: bar 3 1/3, bar 4 2/9, bar 5 13/27, bar 6 26/81
#   DI+ / DI- / DX: bar 3 300/7, 100/7, 50; bar 4 900/23, 200/23, 700/11;
#                   bar 5 1800/73, 1300/73, 500/31; bar 6 31.5, 13, 3700/89
#   ADX(3): bar 5 (50 + 700/11 + 500/31) / 3 = 14750/341; bar 6 (14750/341 * 2 + 3700/89) / 3
#   typical price: 11, 12, 11, 13, 41/3, 12, 13; money flow (bars 1..6):
#     +2400, -1100, +3900, +8200/3, -1200, +2600
#   MFI(3): bar 3 6300 / 7400; bar 4 (19900/3) / (23200/3); bar 5 (19900/3) / (23500/3);
#           bar 6 (16000/3) / (19600/3)   (times 100)
HIGH = [12, 13, 12, 14, 15, 13, 14]
LOW = [10, 11, 10, 12, 12, 11, 12]
CLOSE = [11, 12, 11, 13, 14, 12, 13]
VOLUME = [100, 200, 100, 300, 200, 100, 200]


def test_atr_adx_and_mfi_on_a_second_hand_computed_example():
    high, low, close, volume = series(HIGH), series(LOW), series(CLOSE), series(VOLUME)

    atr = TechnicalFactors.atr(high, low, close, 3).tolist()
    assert all(math.isnan(v) for v in atr[:3])
    assert atr[3:] == pytest.approx([7 / 3, 23 / 9, 73 / 27, 200 / 81])

    adx = TechnicalFactors.adx(high, low, close, 3).tolist()
    assert all(math.isnan(v) for v in adx[:5])
    first = 14750 / 341
    assert adx[5:] == pytest.approx([first, (first * 2 + 3700 / 89) / 3])

    mfi = TechnicalFactors.mfi(high, low, close, volume, 3).tolist()
    assert all(math.isnan(v) for v in mfi[:3])
    assert mfi[3:] == pytest.approx([100 * 6300 / 7400, 100 * 19900 / 23200, 100 * 19900 / 23500,
                                     100 * 16000 / 19600])
