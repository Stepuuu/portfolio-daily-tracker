"""Third review, part 2: one provisional-bar rule on every ingestion path
(data/loader.py:92, data/store.py, data/repository.py), offline with hand-made
frames, fake loaders and injected clocks. The CSV upload is in
test_review3_api.py, a DataFrame passed to the engine in test_review3_engine.py.

The rule: a bar dated today (exchange-local) is final only once that market's
close is final (core.market.calendar.SessionClock.close_final, plus the
publication margin); before that it is never simulated, stored or covered, and
a row stored while provisional is downloaded again (or dropped and marked
incomplete) once the session is final.
"""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backtesting.data.loader import DataLoader
from backtesting.data.repository import BarRepository
from backtesting.data.store import DataStore

SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")


def shanghai(when):
    return pd.Timestamp(when).to_pydatetime().replace(tzinfo=SHANGHAI)


class Clock:
    def __init__(self, when):
        self.now = shanghai(when)

    def __call__(self):
        return self.now


def frame(days, closes, volume=100_000.0):
    index = pd.DatetimeIndex(pd.to_datetime(days), name="date")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": volume, "amount": [c * volume for c in closes]}, index=index)


class FakeLoader:
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


def closes(store, symbol, adjust="qfq"):
    return store.load_daily(symbol, "2024-01-01", "2024-12-31", adjust)["close"].tolist()


# ------------------------------------------------------------------ #
#  When a day's bar is final: the session calendar
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("market,moment,final", [
    ("CN", datetime(2024, 1, 3, 14, 59, tzinfo=SHANGHAI), "2024-01-02"),
    ("CN", datetime(2024, 1, 3, 15, 31, tzinfo=SHANGHAI), "2024-01-03"),
    # New York closes at 13:00 the day after Thanksgiving (the shipped holiday table).
    ("US", datetime(2026, 11, 27, 13, 5, tzinfo=NEW_YORK), "2026-11-26"),
    ("US", datetime(2026, 11, 27, 14, 0, tzinfo=NEW_YORK), "2026-11-27"),
    # A Hong Kong half day: continuous trading to 12:00, the closing auction to about 12:10.
    ("HK", datetime(2026, 12, 24, 12, 5, tzinfo=HONG_KONG), "2026-12-23"),
    ("HK", datetime(2026, 12, 24, 13, 0, tzinfo=HONG_KONG), "2026-12-24"),
    # A full Hong Kong day: the closing auction runs to about 16:10.
    ("HK", datetime(2026, 12, 23, 16, 20, tzinfo=HONG_KONG), "2026-12-22"),
])
def test_a_bar_is_final_after_the_close_of_the_session_calendar(market, moment, final):
    assert DataStore.last_complete_day(moment, market) == pd.Timestamp(final)


# ------------------------------------------------------------------ #
#  The store: never writes a provisional row or covers its day
# ------------------------------------------------------------------ #

def test_the_store_never_writes_a_provisional_row_or_covers_its_day(store):
    morning = shanghai("2024-01-03 10:00")
    store.save_daily("SHA:600519", frame(["2024-01-02", "2024-01-03"], [10.0, 11.0]), "qfq", fetched_at=morning)
    assert store.last_save.provisional == ("2024-01-03",)
    assert closes(store, "SHA:600519") == [10.0]

    store.record_coverage("SHA:600519", "qfq", "2024-01-02", "2024-01-03", fetched_at=morning.isoformat())
    assert spans(store, "SHA:600519") == [("2024-01-02", "2024-01-02")]


# ------------------------------------------------------------------ #
#  A retained loader object
# ------------------------------------------------------------------ #

def test_a_retained_loader_does_not_serve_its_morning_frame_after_the_close(store, monkeypatch):
    # Reviewer's reproduction: the loader kept the frame it fetched at 10:00 (today's
    # close 11) and handed it back after the close; the repository then stored and
    # certified 11 although the day's final close is 15.
    clock = Clock("2024-01-03 10:00")
    fetches = []

    def provider(symbol, start_date, end_date, adjust):
        fetches.append(clock.now)
        today = 11.0 if clock.now.hour < 15 else 15.0
        return frame(["2024-01-02", "2024-01-03"], [10.0, today]).reset_index()

    loader = DataLoader(now=clock)
    monkeypatch.setattr(loader, "_fetch_routed", provider)
    repo = BarRepository(store=store, loader=loader, now=clock)

    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03")
    assert bars["close"].tolist() == [10.0] and cov.provisional_last_bar is True

    clock.now = shanghai("2024-01-03 20:00")
    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03")
    assert bars["close"].tolist() == [10.0, 15.0]
    assert cov.complete is True and cov.provisional_last_bar is False
    assert closes(store, "SHA:600519") == [10.0, 15.0]
    assert len(fetches) == 2


def test_the_loader_keeps_no_frame_that_had_a_provisional_bar(monkeypatch):
    clock = Clock("2024-01-03 10:00")
    fetches = []

    def provider(symbol, start_date, end_date, adjust):
        fetches.append(clock.now)
        return frame(["2024-01-02", "2024-01-03"], [10.0, 11.0]).reset_index()

    loader = DataLoader(now=clock)
    monkeypatch.setattr(loader, "_fetch_routed", provider)
    first = loader.get_daily("600519", "2024-01-02", "2024-01-03")
    assert first.attrs["fetched_at"].startswith("2024-01-03T10:00")
    loader.get_daily("600519", "2024-01-02", "2024-01-03")
    assert len(fetches) == 2                      # the morning frame was not kept

    clock.now = shanghai("2024-01-03 20:00")
    loader.get_daily("600519", "2024-01-02", "2024-01-03")
    again = loader.get_daily("600519", "2024-01-02", "2024-01-03")
    assert len(fetches) == 3                      # a final frame is kept ...
    assert again.attrs["fetched_at"].startswith("2024-01-03T20:00")    # ... with its fetch time


def test_the_repository_judges_a_frame_by_the_time_it_was_fetched(store):
    class Retaining:
        """Hands back the frame it fetched at 10:00, stamped with that time."""

        def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
            out = frame(["2024-01-02", "2024-01-03"], [10.0, 11.0])
            out.attrs["fetched_at"] = shanghai("2024-01-03 10:00").isoformat()
            return out

    repo = BarRepository(store=store, loader=Retaining(), now=Clock("2024-01-03 20:00"))
    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03")

    assert bars["close"].tolist() == [10.0]
    assert closes(store, "SHA:600519") == [10.0]
    assert spans(store, "SHA:600519") == [("2024-01-02", "2024-01-02")]
    assert cov.complete is False and cov.dropped_provisional == ["2024-01-03"]
    assert cov.fetched_at.startswith("2024-01-03T10:00")


# ------------------------------------------------------------------ #
#  A row stored while provisional (an earlier version, an earlier import)
# ------------------------------------------------------------------ #

def old_morning_cache(store):
    """What an earlier version (or a CSV imported at 10:00) left: Jan 3's row written
    at 10:00 on Jan 3 with the morning close 11, and coverage through Jan 3."""
    store.save_daily("SHA:600519", frame(["2024-01-02", "2024-01-03"], [10.0, 11.0]), "qfq")
    written = shanghai("2024-01-03 10:00").astimezone().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE daily_bars SET created_at=? WHERE date='2024-01-03'", (written,))
        conn.execute("INSERT INTO bar_coverage (symbol, adjust, start_date, end_date, fetched_at) "
                     "VALUES ('SHA:600519', 'qfq', '2024-01-02', '2024-01-03', '2024-01-03T10:00:00')")


def test_a_row_stored_while_provisional_is_downloaded_again_after_the_close(store):
    old_morning_cache(store)
    loader = FakeLoader(frame(["2024-01-02", "2024-01-03"], [10.0, 15.0]))
    repo = BarRepository(store=store, loader=loader, now=Clock("2024-01-03 20:00"))

    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03")
    assert bars["close"].tolist() == [10.0, 15.0]
    assert cov.complete is True and cov.cached is False and len(loader.calls) == 1
    # The morning row is not an anchor to compare the new download with.
    assert store.last_save.replaced is False
    assert closes(store, "SHA:600519") == [10.0, 15.0]

    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03")
    assert bars["close"].tolist() == [10.0, 15.0] and cov.cached is True and len(loader.calls) == 1


def test_a_row_stored_while_provisional_is_dropped_offline_and_marked_incomplete(store):
    old_morning_cache(store)
    repo = BarRepository(store=store, loader=FakeLoader(frame(["2024-01-02"], [10.0])),
                         now=Clock("2024-01-03 20:00"))

    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03", allow_download=False)
    assert bars["close"].tolist() == [10.0]
    assert cov.complete is False and cov.dropped_provisional == ["2024-01-03"]
    assert any("2024-01-03" in w and "收盘前" in w for w in cov.warnings)


def test_a_provisional_row_the_new_download_lacks_is_removed(store):
    # The provider no longer has Jan 3 at all: the morning row is not kept as if final.
    old_morning_cache(store)
    loader = FakeLoader(frame(["2024-01-02"], [10.0]))
    repo = BarRepository(store=store, loader=loader, now=Clock("2024-01-03 20:00"))

    bars, cov = repo.load("600519", "2024-01-02", "2024-01-03")
    assert bars["close"].tolist() == [10.0]
    assert closes(store, "SHA:600519") == [10.0]


def test_a_record_with_nothing_final_does_not_leave_an_earlier_report(store):
    # The repository reads last_record after record_download for the unverified rows of
    # its download; a call that records nothing (the whole range still provisional)
    # must not leave the previous call's report, of another symbol, in its place.
    store.save_daily("SHA:600000", frame(["2024-01-02", "2024-01-03"], [10.0, 10.0]), "qfq")
    store.record_coverage("SHA:600000", "qfq", "2024-01-01", "2024-01-03")
    store.save_daily("SHA:600000", frame(["2024-01-04"], [5.0]), "qfq")
    store.record_coverage("SHA:600000", "qfq", "2024-01-03", "2024-01-04")
    assert store.last_record.unverified == ["2024-01-03"]

    store.record_download("SHA:600519", "qfq", "2024-01-03", "2024-01-03",
                          fetched_at=shanghai("2024-01-03 10:00").isoformat())
    assert store.last_record.symbol == "SHA:600519" and store.last_record.unverified == []
