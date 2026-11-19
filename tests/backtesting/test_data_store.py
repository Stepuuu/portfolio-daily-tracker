import sqlite3

import pandas as pd

from backtesting.data.store import DataStore


def test_save_and_load_daily_round_trip(tmp_path):
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    dates = pd.date_range("2024-01-01", periods=3, freq="B")
    df = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "volume": [1000, 1100, 1200],
            "amount": [10_000, 11_000, 12_000],
        },
        index=dates,
    )
    df.index.name = "date"

    written = store.save_daily("TEST", df, adjust="qfq")
    loaded = store.load_daily("TEST", "2024-01-01", "2024-01-05", adjust="qfq")

    assert written == 3
    assert list(loaded["close"]) == [10.5, 11.5, 12.5]
    assert loaded.index.name == "date"


def make_bars(start, periods=None, end=None):
    dates = pd.date_range(start, end, periods=periods, freq="B")
    periods = len(dates)
    df = pd.DataFrame(
        {
            "open": [10.0] * periods,
            "high": [10.5] * periods,
            "low": [9.5] * periods,
            "close": [10.0] * periods,
            "volume": [1000.0] * periods,
            "amount": [10_000.0] * periods,
        },
        index=dates,
    )
    df.index.name = "date"
    return df


# Coverage is what was downloaded (or imported), recorded per request; the dates of
# the cached bars prove nothing about the days between them or after the last one.

def test_partial_cache_is_not_reported_as_covering_a_longer_range(tmp_path):
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    store.save_daily("TEST", make_bars("2023-06-01", 15), adjust="qfq")
    store.record_coverage("TEST", "qfq", "2023-06-01", "2023-06-21")

    assert store.is_data_available("TEST", "2020-01-01", "2024-01-01", "qfq") is False
    # Missing only the head or only the tail of the range is still a partial hit.
    assert store.is_data_available("TEST", "2023-06-01", "2024-01-01", "qfq") is False
    assert store.is_data_available("TEST", "2020-01-01", "2023-06-21", "qfq") is False
    assert store.is_data_available("TEST", "2023-06-01", "2023-06-21", "qfq") is True


def test_bars_without_a_recorded_download_are_not_coverage(tmp_path):
    # A legacy cache (or rows written by save_daily alone) is a miss until a download
    # or import records the interval it covers.
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    store.save_daily("TEST", make_bars("2024-01-01", end="2024-03-29"), adjust="qfq")

    assert store.is_data_available("TEST", "2024-01-01", "2024-03-29", "qfq") is False


def test_cached_bars_with_an_interior_hole_do_not_cover_the_year(tmp_path):
    # Twenty rows from two short downloads at both ends of the year: the first and
    # last bar are near the edges, but January 16 - December 17 was never fetched.
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    store.save_daily("TEST", make_bars("2024-01-02", end="2024-01-15"), adjust="qfq")
    store.save_daily("TEST", make_bars("2024-12-18", end="2024-12-31"), adjust="qfq")
    store.record_download("TEST", "qfq", "2024-01-02", "2024-01-15", fetched_at="2025-01-05T10:00:00")
    store.record_download("TEST", "qfq", "2024-12-18", "2024-12-31", fetched_at="2025-01-05T10:00:00")

    assert store.is_data_available("TEST", "2024-01-01", "2024-12-31", "qfq") is False
    assert store.is_data_available("TEST", "2024-01-02", "2024-12-31", "qfq") is False
    assert store.is_data_available("TEST", "2024-01-03", "2024-01-15", "qfq") is True


def test_cached_bars_missing_the_tail_do_not_cover_it(tmp_path):
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    store.save_daily("TEST", make_bars("2024-01-01", end="2024-03-22"), adjust="qfq")
    store.record_download("TEST", "qfq", "2024-01-01", "2024-03-22", fetched_at="2024-03-23T09:00:00")

    # March 25-29 are five business days that were never fetched.
    assert store.is_data_available("TEST", "2024-01-01", "2024-03-31", "qfq") is False
    assert store.is_data_available("TEST", "2024-01-01", "2024-03-22", "qfq") is True


def test_coverage_follows_the_requested_range_not_the_bar_dates(tmp_path):
    # A download for 2024-01-01..2024-03-31 covers those dates even though the first
    # bar is Tue 2024-01-02 and the last one Fri 2024-03-29 (holiday and weekend).
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    bars = make_bars("2024-01-02", 64)
    assert str(bars.index[-1].date()) == "2024-03-29"
    store.save_daily("TEST", bars, adjust="qfq")
    store.record_download("TEST", "qfq", "2024-01-01", "2024-03-31", fetched_at="2024-04-02T09:00:00")

    assert store.is_data_available("TEST", "2024-01-01", "2024-03-31", "qfq") is True
    assert store.is_data_available("TEST", "2024-02-01", "2024-02-29", "qfq") is True
    assert store.is_data_available("TEST", "2024-01-01", "2024-03-31", "hfq") is False


def test_overlapping_and_adjacent_intervals_are_merged(tmp_path):
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    store.record_coverage("TEST", "qfq", "2024-01-01", "2024-01-31", fetched_at="2024-02-01T09:00:00")
    store.record_coverage("TEST", "qfq", "2024-02-01", "2024-02-29", fetched_at="2024-03-01T09:00:00")  # adjacent
    store.record_coverage("TEST", "qfq", "2024-01-15", "2024-03-10", fetched_at="2024-03-11T09:00:00")  # overlapping
    store.record_coverage("TEST", "qfq", "2024-03-12", "2024-03-20", fetched_at="2024-03-21T09:00:00")  # one-day gap
    store.record_coverage("TEST", "hfq", "2024-01-01", "2024-12-31", fetched_at="2025-01-02T09:00:00")

    assert [(start, end) for start, end, _ in store.get_coverage("TEST", "qfq")] == [
        ("2024-01-01", "2024-03-10"),
        ("2024-03-12", "2024-03-20"),
    ]
    assert store.get_coverage("TEST", "qfq")[0][2] == "2024-03-11T09:00:00"
    assert store.is_data_available("TEST", "2024-01-10", "2024-03-01", "qfq") is True
    # 2024-03-11 was never fetched.
    assert store.is_data_available("TEST", "2024-03-01", "2024-03-15", "qfq") is False


def test_download_coverage_ends_before_the_fetch_day(tmp_path):
    # The fetch day's bar may still be trading, so it is never recorded as covered;
    # a request ending today or later is judged against yesterday the same way.
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    now = pd.Timestamp("2026-03-18 10:30")
    store.record_download("TEST", "qfq", "2025-01-01", "2027-12-31", fetched_at=now.isoformat())

    assert [(s, e) for s, e, _ in store.get_coverage("TEST", "qfq")] == [("2025-01-01", "2026-03-17")]
    assert store.is_data_available("TEST", "2025-01-01", "2027-12-31", "qfq", now=now) is True
    assert store.is_data_available("TEST", "2025-01-01", "2026-03-18", "qfq", now=now) is True
    # The next day the previous fetch day is complete but was not downloaded after its close.
    assert store.is_data_available("TEST", "2025-01-01", "2026-03-18", "qfq",
                                   now=now + pd.Timedelta(days=1)) is False
    # A range that starts today has nothing complete yet: always a download.
    assert store.is_data_available("TEST", "2026-03-18", "2026-03-31", "qfq", now=now) is False


def test_range_ending_in_the_future_is_judged_against_today(tmp_path):
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    today = pd.Timestamp.now().normalize()
    bars = make_bars(today - pd.Timedelta(days=120), 400)
    bars = bars[bars.index <= today]
    store.save_daily("TEST", bars, adjust="qfq")
    start = str(bars.index[0].date())
    end = str((today + pd.Timedelta(days=365)).date())
    store.record_download("TEST", "qfq", start, end)

    assert store.is_data_available("TEST", start, end, "qfq") is True


def test_delete_symbol_forgets_its_coverage(tmp_path):
    store = DataStore(db_path=str(tmp_path / "backtesting.db"))
    store.save_daily("TEST", make_bars("2024-01-01", end="2024-01-31"), adjust="qfq")
    store.record_coverage("TEST", "qfq", "2024-01-01", "2024-01-31")

    store.delete_symbol("TEST")

    assert store.get_coverage("TEST", "qfq") == []
    assert store.is_data_available("TEST", "2024-01-01", "2024-01-31", "qfq") is False


LEGACY_SCHEMA = """
CREATE TABLE daily_bars (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    amount      REAL,
    pct_change  REAL,
    turnover    REAL,
    adjust      TEXT DEFAULT 'qfq',
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (symbol, date, adjust)
);
CREATE INDEX idx_symbol_date ON daily_bars(symbol, date);
INSERT INTO daily_bars (symbol, date, open, high, low, close, volume, amount, pct_change, turnover, adjust)
VALUES ('TEST', '2024-01-02', 10, 10.5, 9.5, 10, 1000, 10000, 0, 0, 'qfq'),
       ('TEST', '2024-01-03', 10, 10.5, 9.5, 10, 1000, 10000, 0, 0, 'qfq');
"""


def test_existing_cache_file_is_migrated_idempotently(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(LEGACY_SCHEMA)

    store = DataStore(db_path=str(path))
    # Old rows stay readable, but are not trusted as coverage.
    assert len(store.load_daily("TEST", "2024-01-01", "2024-01-31", "qfq")) == 2
    assert store.get_coverage("TEST", "qfq") == []
    assert store.is_data_available("TEST", "2024-01-02", "2024-01-03", "qfq") is False

    store.record_coverage("TEST", "qfq", "2024-01-02", "2024-01-03", fetched_at="2024-01-04T09:00:00")
    for _ in range(2):
        reopened = DataStore(db_path=str(path))
        assert reopened.get_coverage("TEST", "qfq") == [("2024-01-02", "2024-01-03", "2024-01-04T09:00:00")]
        assert reopened.is_data_available("TEST", "2024-01-02", "2024-01-03", "qfq") is True
        assert len(reopened.load_daily("TEST", "2024-01-01", "2024-01-31", "qfq")) == 2
