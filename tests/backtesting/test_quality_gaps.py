"""Corporate-action gap scan for unadjusted (不复权) series."""
import math

import pandas as pd
import pytest

from backtesting.data.quality import scan_corporate_action_gaps, validate_daily_bars
from backtesting.data.store import DataStore
from backtesting.data.repository import BarRepository


def series(start="2024-05-01", end="2024-07-31", base=20.0, moves=None):
    """Business-day bars; `moves` maps a date to (factor applied from that day on,
    open/high/low/close multipliers of that day relative to its own close)."""
    dates = pd.bdate_range(start, end)
    factor = 1.0
    rows = []
    for day in dates:
        intraday = (1.0, 1.01, 0.99)
        if moves and day.strftime("%Y-%m-%d") in moves:
            step = moves[day.strftime("%Y-%m-%d")]
            factor *= step[0]
            if len(step) > 1:
                intraday = step[1]
        close = round(base * factor * (1 + 0.01 * math.sin(len(rows) / 2)), 2)
        o, h, l = (round(close * m, 2) for m in intraday)
        rows.append((o, max(h, o, close), min(l, o, close), close, 100_000.0))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=dates)
    df.index.name = "date"
    return df


def dates(events):
    return [event.date for event in events]


def test_a_two_for_one_split_in_raw_prices_is_flagged_with_its_date():
    raw = series(moves={"2024-06-03": (0.5,)})

    events = scan_corporate_action_gaps(raw, symbol="SHA:600519")

    assert dates(events) == ["2024-06-03"]
    assert events[0].ratio == pytest.approx(0.5, abs=0.03)
    assert events[0].change < -0.45


def test_validation_of_raw_bars_reports_the_suspected_date():
    report = validate_daily_bars(series(moves={"2024-06-03": (0.5,)}), symbol="SHA:600519", adjust="")

    assert report.ok  # a caveat, not an error: the caller decides
    assert report.metrics["suspected_corporate_actions"] == ["2024-06-03"]
    assert any("2024-06-03" in w and "corporate action" in w for w in report.warnings)


def test_adjusted_series_are_not_scanned():
    report = validate_daily_bars(series(moves={"2024-06-03": (0.5,)}), symbol="SHA:600519", adjust="qfq")

    assert "suspected_corporate_actions" not in report.metrics
    assert not report.warnings


def test_limit_moves_on_the_main_board_are_not_flagged():
    raw = series(moves={"2024-06-03": (0.90, (1.0, 1.0, 1.0)), "2024-06-04": (0.90, (1.0, 1.0, 1.0)),
                        "2024-07-01": (1.10,)})

    assert scan_corporate_action_gaps(raw, symbol="SHA:600519") == []


def test_a_bad_bar_that_reverts_is_not_flagged():
    raw = series(moves={"2024-06-03": (0.5,), "2024-06-04": (2.0,)})

    assert scan_corporate_action_gaps(raw, symbol="SHA:600519") == []


def test_a_crash_that_recovers_inside_the_bar_is_not_flagged():
    # Opens -20% and closes -16%, but trades as high as -5%: the whole bar did not
    # move to a new level, so it is a market move, not a re-basing.
    raw = series(moves={"2024-06-03": (0.84, (0.95, 1.13, 0.95))})

    assert scan_corporate_action_gaps(raw, symbol="SHA:600519") == []


def test_chinext_thresholds_follow_the_2020_limit_change():
    limit_down = {"2024-06-03": (0.80, (1.0, 1.0, 1.0))}
    assert scan_corporate_action_gaps(series(moves=limit_down), symbol="SHE:300750") == []
    halved = {"2024-06-03": (0.5,)}
    assert dates(scan_corporate_action_gaps(series(moves=halved), symbol="SHE:300750")) == ["2024-06-03"]
    # Before 2020-08-24 ChiNext had a 10% limit, so a 10送2 (-16.7%) cannot be a market move.
    bonus = series("2019-05-01", "2019-07-31", moves={"2019-06-03": (1 / 1.2, (1.0, 1.0, 0.99))})
    assert dates(scan_corporate_action_gaps(bonus, symbol="SHE:300750")) == ["2019-06-03"]
    # After it, the same ratio is inside the 20% limit and is missed (a documented false negative).
    assert scan_corporate_action_gaps(series(moves={"2024-06-03": (1 / 1.2,)}), symbol="SHE:300750") == []


def test_markets_without_limits_need_a_larger_move():
    crash = series(moves={"2024-06-03": (0.75,)})
    assert scan_corporate_action_gaps(crash, symbol="HKG:00700") == []
    split = series(moves={"2024-06-03": (0.5,)})
    assert dates(scan_corporate_action_gaps(split, symbol="HKG:00700")) == ["2024-06-03"]


def test_a_reverse_split_is_flagged():
    raw = series(base=1.0, moves={"2024-06-03": (10.0,)})

    events = scan_corporate_action_gaps(raw, symbol="AAPL")

    assert dates(events) == ["2024-06-03"] and events[0].ratio == pytest.approx(10.0, rel=0.05)


def test_a_gap_on_the_last_bar_is_flagged_without_confirmation():
    raw = series(end="2024-06-03", moves={"2024-06-03": (0.5,)})

    assert dates(scan_corporate_action_gaps(raw, symbol="SHA:600519")) == ["2024-06-03"]


def test_unknown_symbols_use_the_strictest_threshold():
    raw = series(moves={"2024-06-03": (0.80,)})

    assert dates(scan_corporate_action_gaps(raw)) == ["2024-06-03"]
    assert dates(scan_corporate_action_gaps(raw, symbol="not a symbol!")) == ["2024-06-03"]


def test_repository_reports_suspected_dates_for_unadjusted_loads(tmp_path):
    class Loader:
        def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
            return series(moves={"2024-06-03": (0.5,)}).assign(amount=1.0)

    repo = BarRepository(store=DataStore(str(tmp_path / "bars.sqlite")), loader=Loader(),
                         now=lambda: pd.Timestamp("2025-01-10"))

    _, raw = repo.load("600519", "2024-05-01", "2024-07-31", adjust="")
    _, adjusted = repo.load("600519", "2024-05-01", "2024-07-31", adjust="qfq")

    assert raw.suspected_corporate_actions == ["2024-06-03"]
    assert any("2024-06-03" in w and "除权" in w for w in raw.warnings)
    assert adjusted.suspected_corporate_actions == []


def test_engine_result_carries_the_suspected_date_in_its_quality_report():
    from backtesting.engine import BacktestEngine
    from tests.backtesting.test_engine import BuyOnceStrategy

    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("SHA:600519", "", "", adjust="", df=series(moves={"2024-06-03": (0.5,)}), warmup=1)
    engine.add_strategy(BuyOnceStrategy)

    result = engine.run()

    report = result.data_quality_reports["SHA:600519"]
    assert report.metrics["suspected_corporate_actions"] == ["2024-06-03"]
