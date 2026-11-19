"""Data-layer findings of the second engine review (5, 7, 14), offline with
hand-made frames and a fake loader."""
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backtesting.data.repository import BarRepository
from backtesting.data.store import DataStore

NOW = pd.Timestamp("2025-01-10 10:00")


def frame(days, closes, volume=100_000.0):
    index = pd.DatetimeIndex(pd.to_datetime(days), name="date")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": volume, "amount": [c * volume for c in closes]}, index=index)


class FakeLoader:
    """Serves the rows of `universe` inside the requested range; counts calls."""

    def __init__(self, universe):
        self.universe = universe
        self.calls = []

    def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, start_date, end_date, adjust))
        rows = self.universe.loc[start_date:end_date].copy()
        if rows.empty:
            raise ValueError("no rows")
        return rows


@pytest.fixture
def store(tmp_path):
    return DataStore(str(tmp_path / "bars.sqlite"))


def spans(store, symbol, adjust="qfq"):
    return [(start, end) for start, end, _ in store.get_coverage(symbol, adjust)]


# ------------------------------------------------------------------ #
#  Finding 5: coverage merges only after an anchor comparison
# ------------------------------------------------------------------ #

def test_a_bar_only_the_old_series_has_does_not_prove_the_overlap(store):
    # Reviewer's reproduction: Jan 2-3 at 10 cover Jan 1-3; a download for
    # Jan 3-5 returns Jan 4-5 at 5. Nothing was compared, yet the intervals
    # merged and a Jan 2-5 request was served as one complete series.
    store.save_daily("X", frame(["2024-01-02", "2024-01-03"], [10.0, 10.0]), "qfq")
    store.record_coverage("X", "qfq", "2024-01-01", "2024-01-03")
    store.save_daily("X", frame(["2024-01-04", "2024-01-05"], [5.0, 5.0]), "qfq")
    assert store.last_save.compared == 0
    store.record_coverage("X", "qfq", "2024-01-03", "2024-01-05")

    assert spans(store, "X") == [("2024-01-01", "2024-01-03"), ("2024-01-04", "2024-01-05")]
    assert store.last_record.unverified == ["2024-01-03"]
    assert store.is_data_available("X", "2024-01-02", "2024-01-05", "qfq") is False
    # The new download does not vouch for Jan 3: that row is from the older one.
    assert store.is_data_available("X", "2024-01-03", "2024-01-05", "qfq") is False
    assert store.is_data_available("X", "2024-01-04", "2024-01-05", "qfq") is True


def test_a_compared_shared_bar_still_merges(store):
    store.save_daily("X", frame(["2024-01-02", "2024-01-03"], [10.0, 10.0]), "qfq")
    store.record_coverage("X", "qfq", "2024-01-01", "2024-01-03")
    store.save_daily("X", frame(["2024-01-03", "2024-01-04", "2024-01-05"], [10.0, 10.0, 10.0]), "qfq")
    assert store.last_save.compared == 1 and not store.last_save.replaced
    store.record_coverage("X", "qfq", "2024-01-03", "2024-01-05")

    assert spans(store, "X") == [("2024-01-01", "2024-01-05")]
    assert store.last_record.unverified == []


def test_each_overlapping_interval_needs_its_own_comparison(store):
    store.save_daily("X", frame(pd.bdate_range("2024-01-01", "2024-01-10"), [10.0] * 8), "qfq")
    store.record_coverage("X", "qfq", "2024-01-01", "2024-01-10")
    store.save_daily("X", frame(pd.bdate_range("2024-01-15", "2024-01-19"), [10.0] * 5), "qfq")
    store.record_coverage("X", "qfq", "2024-01-15", "2024-01-20")
    # New frame Jan 8-12: compared with the first interval only; the request runs
    # to Jan 16, where the second interval's Jan 15-16 rows were not re-fetched.
    store.save_daily("X", frame(pd.bdate_range("2024-01-08", "2024-01-12"), [10.0] * 5), "qfq")
    store.record_coverage("X", "qfq", "2024-01-08", "2024-01-16")

    assert spans(store, "X") == [("2024-01-01", "2024-01-14"), ("2024-01-15", "2024-01-20")]
    assert store.last_record.unverified == ["2024-01-15", "2024-01-16"]


def test_coverage_without_any_bars_still_merges(store):
    store.record_coverage("X", "qfq", "2024-01-01", "2024-01-31")
    store.record_coverage("X", "qfq", "2024-01-15", "2024-02-29")
    assert spans(store, "X") == [("2024-01-01", "2024-02-29")]


def test_unadjusted_coverage_has_no_anchor_to_check(store):
    store.save_daily("X", frame(["2024-01-02", "2024-01-03"], [10.0, 10.0]), "")
    store.record_coverage("X", "", "2024-01-01", "2024-01-03")
    store.save_daily("X", frame(["2024-01-04", "2024-01-05"], [5.0, 5.0]), "")
    store.record_coverage("X", "", "2024-01-03", "2024-01-05")
    assert spans(store, "X", "") == [("2024-01-01", "2024-01-05")]


def test_repository_reports_the_seam_instead_of_a_complete_spliced_series(store):
    store.save_daily("SHA:600519", frame(["2024-01-02", "2024-01-03"], [10.0, 10.0]), "qfq")
    store.record_coverage("SHA:600519", "qfq", "2024-01-01", "2024-01-03")
    loader = FakeLoader(frame(["2024-01-04", "2024-01-05"], [5.0, 5.0]))
    repo = BarRepository(store=store, loader=loader, now=lambda: NOW)

    bars, cov = repo.load("600519", "2024-01-03", "2024-01-05")
    assert bars["close"].tolist() == [10.0, 5.0, 5.0]
    assert cov.complete is False
    assert any("2024-01-03" in w and "锚点" in w for w in cov.warnings)

    bars, cov = repo.load("600519", "2024-01-02", "2024-01-05", allow_download=False)
    assert bars["close"].tolist() == [10.0, 10.0, 5.0, 5.0]
    assert cov.complete is False and cov.warnings


# ------------------------------------------------------------------ #
#  Finding 7: today's bar before the close is provisional
# ------------------------------------------------------------------ #

# Plain datetimes with zoneinfo: pandas' own time-zone handling is avoided in the
# code under test (it fails on systems with an unreadable UTC zone file).
SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai(when):
    return pd.Timestamp(when).to_pydatetime().replace(tzinfo=SHANGHAI)


class Clock:
    def __init__(self, when):
        self.now = shanghai(when)

    def __call__(self):
        return self.now


class ChangingLoader(FakeLoader):
    """The provider's view moves with the clock: today's close is 11 at 10:00 and 15 at 20:00."""

    def __init__(self, clock):
        super().__init__(None)
        self.clock = clock

    def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
        self.universe = frame(["2024-01-02", "2024-01-03"], [10.0, 11.0 if self.clock.now.hour < 15 else 15.0])
        return super().get_daily(symbol, start_date, end_date, adjust)


def test_todays_bar_fetched_before_the_close_is_not_final(store):
    # Reviewer's reproduction: at 10:00 on Jan 3 the provider returns [10, 11]
    # including today; the second request downloaded nothing and returned 11 as
    # complete, although the day's final close (known at 20:00) is 15.
    clock = Clock("2024-01-03 10:00")
    loader = ChangingLoader(clock)
    repo = BarRepository(store=store, loader=loader, now=clock)

    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03")
    assert bars["close"].tolist() == [10.0]
    assert cov.provisional_last_bar is True and cov.last == "2024-01-02"
    assert any("2024-01-03" in w and "收盘" in w for w in cov.warnings)
    assert store.load_daily("SHA:600519", "2024-01-01", "2024-01-31", "qfq")["close"].tolist() == [10.0]
    assert spans(store, "SHA:600519") == [("2024-01-02", "2024-01-02")]

    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03")
    assert bars["close"].tolist() == [10.0] and cov.provisional_last_bar is True
    assert len(loader.calls) == 1

    clock.now = shanghai("2024-01-03 20:00")
    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03")
    assert bars["close"].tolist() == [10.0, 15.0]
    assert cov.provisional_last_bar is False and cov.complete
    assert len(loader.calls) == 2
    bars, _ = repo.load("600519", "2024-01-02", "2024-01-03")
    assert bars["close"].tolist() == [10.0, 15.0] and len(loader.calls) == 2


def test_a_provisional_row_already_in_the_cache_is_left_out_on_read(store):
    # A cache written by an earlier version holds the 10:00 row of Jan 3 as if final.
    store.save_daily("SHA:600519", frame(["2024-01-02", "2024-01-03"], [10.0, 11.0]), "qfq")
    store.record_download("SHA:600519", "qfq", "2024-01-02", "2024-01-03",
                          fetched_at=shanghai("2024-01-03 10:00").isoformat())
    repo = BarRepository(store=store, loader=FakeLoader(frame(["2024-01-02"], [10.0])),
                         now=Clock("2024-01-03 11:00"))

    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03", allow_download=False)
    assert bars["close"].tolist() == [10.0]
    assert cov.provisional_last_bar is True and cov.complete


@pytest.mark.parametrize("market_now,settled", [
    ("2024-01-03 14:59", False),
    ("2024-01-03 15:31", True),
])
def test_the_close_is_judged_in_the_exchange_time_zone(market_now, settled):
    now = shanghai(market_now)
    day = DataStore.last_complete_day(now, market="CN")
    assert day == (pd.Timestamp("2024-01-03") if settled else pd.Timestamp("2024-01-02"))
    # The same instant seen from New York: Jan 3 02:00/02:31 there, US Jan 2 is final.
    assert DataStore.last_complete_day(now, market="US") == pd.Timestamp("2024-01-02")
    # A US session still trading at 14:00 New York time (03:00 next day in Shanghai).
    trading = pd.Timestamp("2024-01-03 14:00").to_pydatetime().replace(tzinfo=ZoneInfo("America/New_York"))
    assert DataStore.last_complete_day(trading.astimezone(SHANGHAI), market="US") == pd.Timestamp("2024-01-02")


# ------------------------------------------------------------------ #
#  Finding 14: unadjusted prices always carry the dividend caveat
# ------------------------------------------------------------------ #

from backtesting.analyzer.caveats import build_caveats  # noqa: E402
from backtesting.broker.simulated import BrokerConfig  # noqa: E402
from backtesting.data.quality import validate_daily_bars  # noqa: E402

DIVIDEND = frame(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"], [10.0, 10.0, 9.5, 9.5])


def test_unadjusted_bars_always_carry_the_corporate_action_warning():
    # Reviewer's reproduction: a 0.50 dividend in raw closes 10,10,9.5,9.5 is a
    # 5% loss in the backtest, and the quality report had no warning at all.
    report = validate_daily_bars(DIVIDEND, symbol="SHA:600519", adjust="")
    assert report.metrics["suspected_corporate_actions"] == []          # too small for the gap scan
    assert report.metrics["corporate_actions_accounted"] is False
    note = next(w for w in report.warnings if "dividends" in w)
    assert "not accounted" in note and "no suspected" in note
    assert not validate_daily_bars(DIVIDEND, symbol="SHA:600519", adjust="qfq").warnings


def test_repository_raw_load_warns_even_when_the_scan_finds_nothing(store):
    repo = BarRepository(store=store, loader=FakeLoader(DIVIDEND), now=lambda: NOW)
    _, raw = repo.load("600519", "2024-01-02", "2024-01-05", adjust="")
    assert raw.suspected_corporate_actions == []
    assert any("分红" in w and "低估" in w for w in raw.warnings)
    _, index = repo.load("IDX:000300", "2024-01-02", "2024-01-05")
    assert any("价格指数" in w for w in index.warnings)


def test_unadjusted_runs_get_a_warning_caveat():
    def caveats(adjustments):
        return build_caveats(unfilled=[], symbols=list(adjustments), config=BrokerConfig(),
                             adjustments=adjustments)

    (item,) = [c for c in caveats({"SHA:600519": ""}) if c["code"] == "unadjusted_prices"]
    assert item["level"] == "warning"
    assert "分红" in item["text"] and "没有发现" in item["text"]
    assert not [c for c in caveats({"SHA:600519": "qfq"}) if c["code"] == "unadjusted_prices"]


def test_engine_quality_report_carries_the_warning_for_the_reviewers_run():
    from backtesting.engine import BacktestEngine
    from backtesting.strategies.base import Strategy

    class Buy100(Strategy):
        def on_bar(self):
            if self.data.current_index == self.data.warmup:
                self.buy(quantity=100)

    engine = BacktestEngine(initial_cash=10_000, broker_config=BrokerConfig(
        slippage_pct=0.0, commission_buy=0.0, commission_sell=0.0, min_commission=0.0), use_cache=False)
    engine.add_data("600000", "", "", adjust="", df=DIVIDEND, warmup=0)
    engine.add_strategy(Buy100)
    result = engine.run()
    payload = result.to_dict()

    assert payload["final_value"] == pytest.approx(10_000 - 50, abs=1.0)   # the dividend shows as a loss
    assert any("dividends" in w for w in payload["data_quality"]["600000"]["warnings"])
