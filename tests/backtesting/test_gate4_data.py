"""Release gate 4 (cr_g4d PART 1), the bar repository: offline, with fake
providers and injected clocks.

* data/repository.py:438 - a download made soon after the close can miss the
  day's bar (the provider publishes it later), yet the whole requested interval
  is recorded. A later run must not take that cache as complete: a series whose
  last bar is older than the last settled session is downloaded again, or the
  result says the data ends early.
* data/repository.py:404 - a window cached with a short warm-up must not cut a
  longer warm-up short when the provider has the history: the cached run and
  a cold run give the same bars.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from backtesting.data.repository import BarRepository
from backtesting.data.store import DataStore

SHANGHAI = ZoneInfo("Asia/Shanghai")


class Clock:
    def __init__(self, when):
        self.now = datetime.fromisoformat(when).replace(tzinfo=SHANGHAI)

    def set(self, when):
        self.now = datetime.fromisoformat(when).replace(tzinfo=SHANGHAI)

    def __call__(self):
        return self.now


def bars(start, end):
    days = pd.bdate_range(start, end, name="date")
    close = 10.0 + np.sin(np.arange(len(days)) / 7.0) + np.arange(len(days)) * 0.01
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1_000_000.0, "amount": close * 1_000_000.0}, index=days).round(4)


class LaggingProvider:
    """Publishes a day's bar `lag` after 15:00 Shanghai time (the close)."""

    def __init__(self, history, clock, lag_hours=2.0, fail=False):
        self.history, self.clock, self.lag, self.fail = history, clock, pd.Timedelta(hours=lag_hours), fail
        self.calls = []

    def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, start_date, end_date, adjust))
        if self.fail:
            raise ConnectionError("provider down")
        now = pd.Timestamp(self.clock().astimezone(SHANGHAI).replace(tzinfo=None))
        published = [day for day in self.history.index if day + pd.Timedelta(hours=15) + self.lag <= now]
        rows = self.history.loc[published].loc[start_date:end_date].copy()
        if rows.empty:
            raise ValueError("no rows")
        return rows


@pytest.fixture
def store(tmp_path):
    return DataStore(str(tmp_path / "bars.sqlite"))


HISTORY = bars("2025-06-02", "2026-11-16")      # Monday 2026-11-16 is the last session


# ------------------------------------------------------------------ #
#  repository.py:438 - a cache written before the provider published the day
# ------------------------------------------------------------------ #

def test_a_cache_missing_the_last_settled_session_is_downloaded_again(store):
    clock = Clock("2026-11-16T15:45:00")
    provider = LaggingProvider(HISTORY, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)

    early, first = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    # At 15:45 the provider has Friday's bar only: the data ends a session early.
    assert first.last == "2026-11-13" and first.last_session == "2026-11-16"
    assert first.ends_early is True and first.complete is False
    assert any("2026-11-16" in text for text in first.warnings)

    clock.set("2026-11-16T18:00:00")
    late, second = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    # The cached series ends before Monday, which has settled: it is downloaded again.
    assert len(provider.calls) == 2
    assert second.last == "2026-11-16" and second.ends_early is False and second.complete is True
    assert late.index[-1] == pd.Timestamp("2026-11-16")


def test_the_same_run_cold_and_cached_gives_the_same_bars(store, tmp_path):
    clock = Clock("2026-11-16T15:45:00")
    repo = BarRepository(store=store, loader=LaggingProvider(HISTORY, clock), now=clock)
    repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    clock.set("2026-11-16T18:00:00")
    cached, cached_cov = repo.load("SHA:600519", "2026-09-01", "2026-11-16")

    cold_repo = BarRepository(store=DataStore(str(tmp_path / "cold.sqlite")),
                              loader=LaggingProvider(HISTORY, clock), now=clock)
    cold, cold_cov = cold_repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    pd.testing.assert_frame_equal(cached, cold)
    assert cached_cov.sha256 == cold_cov.sha256 and cached_cov.complete is cold_cov.complete is True


def test_without_a_download_a_short_cache_says_the_data_ends_early(store):
    clock = Clock("2026-11-16T15:45:00")
    repo = BarRepository(store=store, loader=LaggingProvider(HISTORY, clock), now=clock)
    repo.load("SHA:600519", "2026-09-01", "2026-11-16")

    clock.set("2026-11-16T18:00:00")
    frame, coverage = repo.load("SHA:600519", "2026-09-01", "2026-11-16", allow_download=False)
    assert frame.index[-1] == pd.Timestamp("2026-11-13")
    assert coverage.ends_early is True and coverage.complete is False and coverage.last_session == "2026-11-16"


def test_a_failed_refresh_keeps_the_cached_bars_and_says_they_end_early(store):
    clock = Clock("2026-11-16T15:45:00")
    provider = LaggingProvider(HISTORY, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)
    repo.load("SHA:600519", "2026-09-01", "2026-11-16")

    clock.set("2026-11-16T18:00:00")
    provider.fail = True
    frame, coverage = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    assert len(provider.calls) == 2
    # Not an offline run: the cache holds the window; it is only a session short.
    assert coverage.offline is False and coverage.cached is True
    assert coverage.ends_early is True and coverage.complete is False
    assert frame.index[-1] == pd.Timestamp("2026-11-13")
    assert any("下载失败" in text for text in coverage.warnings)


def test_a_download_made_a_day_after_the_session_is_trusted(store):
    # Suspended on Monday: a download made two days later is all the provider will ever have.
    suspended = HISTORY.drop(pd.Timestamp("2026-11-16"))
    clock = Clock("2026-11-18T10:00:00")
    provider = LaggingProvider(suspended, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)

    _, first = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    _, second = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    assert len(provider.calls) == 1
    assert first.ends_early is False and first.complete is True
    assert second.ends_early is False and second.complete is True and second.cached is True


def test_an_old_window_ending_on_a_holiday_the_table_does_not_list_is_complete(store):
    # 2025-01-01 is a holiday the shipped table (A shares: 2026) does not list: by weekday it
    # is a session, but the download was made long after it, so no bar is late.
    clock = Clock("2026-11-18T10:00:00")
    history = bars("2024-01-02", "2024-12-31")
    provider = LaggingProvider(history, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)
    repo.load("SHA:600519", "2024-06-03", "2025-01-01")
    _, coverage = repo.load("SHA:600519", "2024-06-03", "2025-01-01")
    assert len(provider.calls) == 1
    assert coverage.ends_early is False and coverage.complete is True


def test_a_run_on_bars_that_end_early_says_so(tmp_path):
    from backtesting.engine import BacktestEngine
    from backtesting.strategies.base import Strategy

    class Hold(Strategy):
        name = "Hold"

        def on_bar(self):
            if not self.get_position():
                self.buy(pct_cash=0.5)

    clock = Clock("2026-11-16T15:45:00")
    engine = BacktestEngine(initial_cash=100_000, loader=LaggingProvider(HISTORY, clock), now=clock)
    engine.add_data("SHA:600519", "2026-09-01", "2026-11-16", warmup=0)
    engine.add_strategy(Hold)
    result = engine.run()
    found = [c for c in result.caveats if c["code"] == "data_ends_early"]
    assert len(found) == 1 and found[0]["level"] == "warning"
    assert "2026-11-13" in found[0]["text"] and "2026-11-16" in found[0]["text"]


# ------------------------------------------------------------------ #
#  repository.py:404 - a longer warm-up over a window cached with a short one
# ------------------------------------------------------------------ #

LONG_HISTORY = bars("2023-01-02", "2026-06-30")


def test_a_longer_warmup_fetches_the_earlier_bars_it_needs(store, tmp_path):
    clock = Clock("2026-11-18T10:00:00")
    provider = LaggingProvider(LONG_HISTORY, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)
    repo.load("SHA:600519", "2025-07-01", "2026-06-30", warmup_bars=20)          # an MA20 run
    cached, cached_cov = repo.load("SHA:600519", "2025-07-01", "2026-06-30", warmup_bars=200)   # MA200

    cold_repo = BarRepository(store=DataStore(str(tmp_path / "cold.sqlite")),
                              loader=LaggingProvider(LONG_HISTORY, clock), now=clock)
    cold, cold_cov = cold_repo.load("SHA:600519", "2025-07-01", "2026-06-30", warmup_bars=200)

    assert cached_cov.warmup_rows == cold_cov.warmup_rows == 200
    pd.testing.assert_frame_equal(cached, cold)
    assert cached_cov.sha256 == cold_cov.sha256
    assert cached_cov.complete is cold_cov.complete is True
    assert not any("预热期只取到" in text for text in cached_cov.warnings)

    # Once fetched, the longer warm-up is a cache hit.
    calls = len(provider.calls)
    _, again = repo.load("SHA:600519", "2025-07-01", "2026-06-30", warmup_bars=200)
    assert len(provider.calls) == calls and again.cached is True and again.warmup_rows == 200


def test_a_cached_window_with_enough_warmup_is_not_downloaded_again(store):
    clock = Clock("2026-11-18T10:00:00")
    provider = LaggingProvider(LONG_HISTORY, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)
    repo.load("SHA:600519", "2025-07-01", "2026-06-30", warmup_bars=200)
    _, coverage = repo.load("SHA:600519", "2025-07-01", "2026-06-30", warmup_bars=20)
    assert len(provider.calls) == 1 and coverage.warmup_rows == 20 and coverage.complete is True


def test_a_failed_download_for_the_warmup_falls_back_to_the_cached_window(store):
    clock = Clock("2026-11-18T10:00:00")
    provider = LaggingProvider(LONG_HISTORY, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)
    repo.load("SHA:600519", "2025-07-01", "2026-06-30", warmup_bars=20)
    provider.fail = True
    frame, coverage = repo.load("SHA:600519", "2025-07-01", "2026-06-30", warmup_bars=200)
    assert len(provider.calls) == 2
    assert coverage.offline is False and coverage.complete is False and coverage.warmup_rows < 200
    assert frame.index[-1] == pd.Timestamp("2026-06-30")
    assert any("下载失败" in text for text in coverage.warnings)


def test_a_demo_run_after_the_close_does_not_end_early(store):
    # The demo's synthetic source serves bars up to yesterday (its tickers have no market the
    # bar cache knows, so a bar of today would count as provisional there): its series is
    # complete, and no demo run after the close may say that it ends early.
    from backtesting.demo import DemoLoader, demo_symbol
    clock = Clock("2026-11-17T18:00:00")
    naive = lambda: pd.Timestamp("2026-11-17 18:00")             # noqa: E731 (the demo loader's own clock)
    _, coverage = BarRepository(store=store, loader=DemoLoader(now=naive), now=clock).load(
        demo_symbol("SHA:DEMO"), "2026-09-01", "2026-11-17")
    assert coverage.last == "2026-11-16" and coverage.ends_early is False and coverage.complete is True
    assert not coverage.dropped_provisional
