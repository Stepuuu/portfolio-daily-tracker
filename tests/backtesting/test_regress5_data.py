"""Round 5 (cr_v5c B2): four submissions on one evening for a suspended stock made
four provider calls (baseline 40868ac: one). The round-4 rule downloads a series that
ends before the last settled session again on every run; a recent check of that series
(a download made after the session settled, within RECHECK_INTERVAL) is reused instead,
and the result still says the data ends early.

Both ways: the round-4 scenario (a download at 15:45, before the provider published
Monday; the 18:00 run downloads again and gets it) still passes.
"""
import pandas as pd
import pytest

from backtesting.data.repository import BarRepository
from backtesting.data.store import DataStore
from tests.backtesting.test_gate4_data import HISTORY, Clock, LaggingProvider


@pytest.fixture
def store(tmp_path):
    return DataStore(str(tmp_path / "bars.sqlite"))


SUSPENDED = HISTORY.drop(pd.Timestamp("2026-11-16"))        # no bar on Monday 2026-11-16


def test_one_evening_of_submissions_for_a_suspended_stock_asks_the_provider_once(store):
    clock = Clock("2026-11-16T18:00:00")
    provider = LaggingProvider(SUSPENDED, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)
    for when in ("18:00", "18:05", "18:20", "18:40"):
        clock.set(f"2026-11-16T{when}:00")
        frame, coverage = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
        assert frame.index[-1] == pd.Timestamp("2026-11-13")
        # Still said, every time: the data ends before the last settled session.
        assert coverage.ends_early is True and coverage.complete is False
        assert coverage.last_session == "2026-11-16"
    assert len(provider.calls) == 1                            # 40868ac: 1; round 4: 4


def test_the_check_is_reused_for_a_bounded_time_only(store):
    clock = Clock("2026-11-16T18:00:00")
    provider = LaggingProvider(SUSPENDED, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)
    repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    clock.set("2026-11-16T19:30:00")                          # more than an hour after the check
    _, coverage = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    assert len(provider.calls) == 2 and coverage.ends_early is True


def test_the_round_four_scenario_still_downloads_again_after_the_provider_published(store):
    clock = Clock("2026-11-16T15:45:00")
    provider = LaggingProvider(HISTORY, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)
    _, early = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    assert early.ends_early is True
    clock.set("2026-11-16T18:00:00")
    _, late = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    assert len(provider.calls) == 2 and late.last == "2026-11-16" and late.complete is True


def test_a_complete_cache_is_still_read_without_a_call(store):
    clock = Clock("2026-11-16T18:00:00")
    provider = LaggingProvider(HISTORY, clock)
    repo = BarRepository(store=store, loader=provider, now=clock)
    repo.load("SHA:600519", "2026-09-01", "2026-11-16")
    for when in ("18:05", "21:00"):
        clock.set(f"2026-11-16T{when}:00")
        _, coverage = repo.load("SHA:600519", "2026-09-01", "2026-11-16")
        assert coverage.cached is True and coverage.complete is True and coverage.ends_early is False
    assert len(provider.calls) == 1
