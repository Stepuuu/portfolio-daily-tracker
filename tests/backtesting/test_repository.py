"""Bar repository and the adjusted-series cache (offline, hand-built frames)."""
import math
import re

import pandas as pd
import pytest

from backtesting.data.store import DataStore


def bars(start, end, scale=1.0, base=10.0):
    """Business-day bars with a gentle deterministic wave; `scale` re-anchors them."""
    dates = pd.bdate_range(start, end)
    # The wave follows the calendar, so two frames agree on the dates they share.
    close = [round(base * (1 + 0.02 * math.sin((d - pd.Timestamp("2024-01-01")).days / 3)) * scale, 2)
             for d in dates]
    df = pd.DataFrame({
        "open": close,
        "high": [round(c * 1.01, 2) for c in close],
        "low": [round(c * 0.99, 2) for c in close],
        "close": close,
        "volume": [100_000.0] * len(dates),
        "amount": [c * 100_000.0 for c in close],
    }, index=dates)
    df.index.name = "date"
    return df


def max_daily_move(df):
    return float(df["close"].pct_change().abs().max())


@pytest.fixture
def store(tmp_path):
    return DataStore(str(tmp_path / "bars.sqlite"))


# ------------------------------------------------------------------ #
#  Adjustment anchors (defect 2)
# ------------------------------------------------------------------ #

def test_redownload_with_a_moved_anchor_replaces_the_adjusted_series(store):
    # The first download is anchored before a 10送10; the second, after it, halves
    # every forward-adjusted price. Merging row by row would leave a -50% seam on
    # 2024-04-01, where the new rows start.
    first = bars("2024-01-01", "2024-06-28")
    second = bars("2024-04-01", "2024-09-30", scale=0.5)
    store.save_daily("SHA:600519", first, "qfq")
    store.record_download("SHA:600519", "qfq", "2024-01-01", "2024-06-30", fetched_at="2024-07-01T09:00:00")

    store.save_daily("SHA:600519", second, "qfq")
    store.record_download("SHA:600519", "qfq", "2024-04-01", "2024-09-30", fetched_at="2024-10-08T09:00:00")

    stored = store.load_daily("SHA:600519", "2024-01-01", "2024-09-30", "qfq")
    assert stored.index[0] == pd.Timestamp("2024-04-01")  # old rows outside the new download are gone
    assert stored["close"].tolist() == second["close"].tolist()
    assert max_daily_move(stored) < 0.05  # no seam
    assert store.last_save.replaced is True
    assert store.last_save.compared == len(bars("2024-04-01", "2024-06-28"))
    # Coverage is what the second download fetched, nothing more.
    assert [(s, e) for s, e, _ in store.get_coverage("SHA:600519", "qfq")] == [("2024-04-01", "2024-09-30")]
    assert store.is_data_available("SHA:600519", "2024-01-01", "2024-06-30", "qfq") is False


def test_backward_adjusted_series_is_checked_the_same_way(store):
    store.save_daily("X", bars("2024-01-01", "2024-03-29"), "hfq")
    store.save_daily("X", bars("2024-03-01", "2024-05-31", scale=0.5), "hfq")

    assert store.last_save.replaced is True
    assert store.load_daily("X", "2024-01-01", "2024-05-31", "hfq").index[0] == pd.Timestamp("2024-03-01")


def test_overlap_that_agrees_is_merged(store):
    store.save_daily("X", bars("2024-01-01", "2024-06-28"), "qfq")
    store.record_download("X", "qfq", "2024-01-01", "2024-06-30", fetched_at="2024-07-01T09:00:00")
    store.save_daily("X", bars("2024-04-01", "2024-09-30"), "qfq")
    store.record_download("X", "qfq", "2024-04-01", "2024-09-30", fetched_at="2024-10-08T09:00:00")

    stored = store.load_daily("X", "2024-01-01", "2024-09-30", "qfq")
    assert stored.index[0] == pd.Timestamp("2024-01-01")
    assert store.last_save.replaced is False
    assert [(s, e) for s, e, _ in store.get_coverage("X", "qfq")] == [("2024-01-01", "2024-09-30")]


def test_differences_within_rounding_do_not_replace(store):
    first = bars("2024-01-01", "2024-03-29")
    nudged = first.loc["2024-02-01":].copy()
    nudged[["open", "high", "low", "close"]] += 0.004  # under half a cent
    store.save_daily("X", first, "qfq")
    store.save_daily("X", nudged, "qfq")

    assert store.last_save.replaced is False
    assert store.load_daily("X", "2024-01-01", "2024-03-29", "qfq").index[0] == pd.Timestamp("2024-01-01")


def test_one_cent_anchor_move_on_a_low_price_is_detected(store):
    # A 1% dividend on a 1.00 qfq price moves it by one cent: a real 1% seam.
    first = bars("2024-01-01", "2024-03-29", base=1.0)
    store.save_daily("X", first, "qfq")
    store.save_daily("X", bars("2024-02-01", "2024-04-30", base=1.0, scale=0.99), "qfq")

    assert store.last_save.replaced is True


def test_unadjusted_rows_are_corrected_in_place_never_replaced(store):
    # Raw prices have no anchor; a disagreeing re-download is a provider correction.
    store.save_daily("X", bars("2024-01-01", "2024-06-28"), "")
    store.save_daily("X", bars("2024-04-01", "2024-09-30", scale=0.5), "")

    stored = store.load_daily("X", "2024-01-01", "2024-09-30", "")
    assert stored.index[0] == pd.Timestamp("2024-01-01")
    assert store.last_save.replaced is False


def test_replacement_only_touches_that_symbol_and_adjustment(store):
    store.save_daily("X", bars("2024-01-01", "2024-06-28"), "qfq")
    store.save_daily("X", bars("2024-01-01", "2024-06-28"), "hfq")
    store.save_daily("Y", bars("2024-01-01", "2024-06-28"), "qfq")
    store.record_coverage("X", "hfq", "2024-01-01", "2024-06-30")

    store.save_daily("X", bars("2024-04-01", "2024-09-30", scale=0.5), "qfq")

    assert len(store.load_daily("X", "2024-01-01", "2024-06-30", "hfq")) == len(bars("2024-01-01", "2024-06-28"))
    assert len(store.load_daily("Y", "2024-01-01", "2024-06-30", "qfq")) == len(bars("2024-01-01", "2024-06-28"))
    assert store.get_coverage("X", "hfq") != []


def test_adjacent_adjusted_downloads_without_a_shared_bar_stay_separate(store):
    # Nothing in common to compare: merging the intervals would let one request
    # span both anchors. Kept apart, such a request downloads again and the
    # overlap check runs on the new frame.
    store.save_daily("X", bars("2024-01-01", "2024-06-28"), "qfq")
    store.record_download("X", "qfq", "2024-01-01", "2024-06-30", fetched_at="2024-07-01T09:00:00")
    store.save_daily("X", bars("2024-07-01", "2024-09-30", scale=0.5), "qfq")
    store.record_download("X", "qfq", "2024-07-01", "2024-09-30", fetched_at="2024-10-08T09:00:00")

    assert [(s, e) for s, e, _ in store.get_coverage("X", "qfq")] == [
        ("2024-01-01", "2024-06-30"), ("2024-07-01", "2024-09-30")]
    assert store.is_data_available("X", "2024-01-01", "2024-09-30", "qfq") is False

    store.save_daily("X", bars("2024-01-01", "2024-09-30", scale=0.5), "qfq")
    store.record_download("X", "qfq", "2024-01-01", "2024-09-30", fetched_at="2024-10-09T09:00:00")

    assert [(s, e) for s, e, _ in store.get_coverage("X", "qfq")] == [("2024-01-01", "2024-09-30")]
    assert max_daily_move(store.load_daily("X", "2024-01-01", "2024-09-30", "qfq")) < 0.05


def test_adjacent_unadjusted_downloads_still_merge(store):
    store.save_daily("X", bars("2024-01-01", "2024-06-28"), "")
    store.record_download("X", "", "2024-01-01", "2024-06-30", fetched_at="2024-07-01T09:00:00")
    store.save_daily("X", bars("2024-07-01", "2024-09-30"), "")
    store.record_download("X", "", "2024-07-01", "2024-09-30", fetched_at="2024-10-08T09:00:00")

    assert [(s, e) for s, e, _ in store.get_coverage("X", "")] == [("2024-01-01", "2024-09-30")]


# ------------------------------------------------------------------ #
#  BarRepository
# ------------------------------------------------------------------ #

from backtesting.data.repository import (  # noqa: E402
    BarRepository, BarsUnavailableError, Coverage, bars_sha256, union_calendar,
)

NOW = pd.Timestamp("2025-01-10 10:00")


class FakeLoader:
    """Stands in for DataLoader: serves slices of `universe`, counts calls."""

    def __init__(self, universe=None, error=None):
        self.universe = universe
        self.error = error
        self.calls = []

    def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, start_date, end_date, adjust))
        if self.error is not None:
            raise self.error
        frame = self.universe.loc[start_date:end_date].copy()
        if frame.empty:
            raise ValueError("no rows")
        return frame


def repo_with(store, loader):
    return BarRepository(store=store, loader=loader, now=lambda: NOW)


def test_download_once_then_serve_from_the_cache(store):
    loader = FakeLoader(bars("2023-01-02", "2024-12-31"))
    repo = repo_with(store, loader)

    frame, cov = repo.load("600519", "2024-01-01", "2024-12-31")
    again, cov2 = repo.load("SHA:600519", "2024-01-01", "2024-12-31")

    assert loader.calls == [("SHA:600519", "2024-01-01", "2024-12-31", "qfq")]
    assert isinstance(cov, Coverage)
    assert (cov.ticker, cov.adjust, cov.requested_start, cov.requested_end) == (
        "SHA:600519", "qfq", "2024-01-01", "2024-12-31")
    assert (cov.first, cov.last, cov.rows) == ("2024-01-01", "2024-12-31", len(frame))
    assert cov.complete and not cov.offline and not cov.cached
    assert cov.source == "akshare.stock_zh_a_hist" and cov.fetched_at == "2025-01-10T10:00:00"
    assert cov2.complete and cov2.cached and not cov2.offline
    assert cov2.source == "akshare.stock_zh_a_hist" and cov2.fetched_at == cov.fetched_at
    pd.testing.assert_frame_equal(frame, again)
    # Written once, under the canonical key.
    assert store.get_available_symbols() == ["SHA:600519"]


def test_listed_after_the_start_is_not_downloaded_again(store):
    loader = FakeLoader(bars("2024-06-03", "2024-12-31"))
    repo = repo_with(store, loader)

    for _ in range(3):
        frame, cov = repo.load("SHE:000001", "2024-01-01", "2024-12-31")

    assert len(loader.calls) == 1
    assert cov.first == "2024-06-03" and cov.complete


def test_offline_with_a_partial_cache_returns_what_exists(store):
    repo_with(store, FakeLoader(bars("2024-01-01", "2024-06-28"))).load("600519", "2024-01-01", "2024-06-30")
    offline = FakeLoader(error=ConnectionError("network unreachable"))

    frame, cov = repo_with(store, offline).load("600519", "2024-01-01", "2024-12-31")

    assert len(offline.calls) == 1
    assert cov.offline and not cov.complete and cov.cached
    assert (cov.first, cov.last) == ("2024-01-01", "2024-06-28")
    assert len(frame) == cov.rows == len(bars("2024-01-01", "2024-06-28"))
    assert any("ConnectionError" in w for w in cov.warnings)
    assert any("2024-01-01~2024-06-28" in w for w in cov.warnings)


def test_download_not_allowed_uses_the_cache_without_calling_the_loader(store):
    repo_with(store, FakeLoader(bars("2024-01-01", "2024-06-28"))).load("600519", "2024-01-01", "2024-06-30")
    loader = FakeLoader(bars("2024-01-01", "2024-12-31"))
    repo = repo_with(store, loader)

    _, partial = repo.load("600519", "2024-01-01", "2024-12-31", allow_download=False)
    _, full = repo.load("600519", "2024-02-01", "2024-05-31", allow_download=False)

    assert loader.calls == []
    assert partial.offline and not partial.complete
    # Covered by the cache: nothing was missing, so the result is complete.
    assert full.complete and not full.offline


def test_offline_with_nothing_cached_raises_a_clear_error(store):
    with pytest.raises(BarsUnavailableError) as excinfo:
        repo_with(store, FakeLoader(error=ConnectionError("down"))).load("600519", "2024-01-01", "2024-12-31")
    message = str(excinfo.value)
    assert "SHA:600519" in message and "ConnectionError" in message and "本地" in message


@pytest.mark.parametrize("legacy", ["600519", "600519.SH", "sh600519"])
def test_legacy_store_keys_are_read_as_aliases(store, legacy):
    store.save_daily(legacy, bars("2024-01-01", "2024-12-31"), "qfq")
    store.record_coverage(legacy, "qfq", "2024-01-01", "2024-12-31")
    loader = FakeLoader(error=AssertionError("cache covers the range"))

    frame, cov = repo_with(store, loader).load("SHA:600519", "2024-01-01", "2024-12-31")

    assert loader.calls == []
    assert cov.store_key == legacy and cov.ticker == "SHA:600519" and cov.complete
    assert len(frame) == len(bars("2024-01-01", "2024-12-31"))


def test_legacy_rows_without_coverage_are_an_offline_fallback(store):
    store.save_daily("0700.HK", bars("2024-01-01", "2024-06-28"), "qfq")

    frame, cov = repo_with(store, FakeLoader(error=ConnectionError("down"))).load(
        "HKG:00700", "2024-01-01", "2024-06-28")

    assert cov.store_key == "0700.HK" and cov.offline and not cov.complete
    assert len(frame) == len(bars("2024-01-01", "2024-06-28"))


def test_canonical_key_wins_over_an_alias(store):
    store.save_daily("AAPL", bars("2024-01-01", "2024-12-31", scale=2.0), "qfq")
    store.record_coverage("AAPL", "qfq", "2024-01-01", "2024-12-31")
    store.save_daily("US:AAPL", bars("2024-01-01", "2024-12-31"), "qfq")
    store.record_coverage("US:AAPL", "qfq", "2024-01-01", "2024-12-31")

    frame, cov = repo_with(store, FakeLoader(error=AssertionError())).load("NASDAQ:AAPL", "2024-03-01", "2024-03-29")

    assert cov.store_key == "US:AAPL"
    assert frame["close"].tolist() == bars("2024-03-01", "2024-03-29")["close"].tolist()


def test_anchor_drift_through_the_repository_leaves_no_seam(store):
    repo_with(store, FakeLoader(bars("2024-01-01", "2024-06-28"))).load("600519", "2024-01-01", "2024-06-30")
    reanchored = FakeLoader(bars("2024-01-01", "2024-12-31", scale=0.5))

    frame, cov = repo_with(store, reanchored).load("600519", "2024-04-01", "2024-09-30")

    assert max_daily_move(frame) < 0.05
    assert any("锚点" in w for w in cov.warnings)
    assert [(s, e) for s, e, _ in store.get_coverage("SHA:600519", "qfq")] == [("2024-04-01", "2024-09-30")]
    # The old first half is gone, so asking for it downloads again.
    repo_with(store, reanchored).load("600519", "2024-01-01", "2024-06-30")
    assert len(reanchored.calls) == 2


def test_warmup_bars_are_loaded_before_the_start(store):
    universe = bars("2023-01-02", "2024-12-31")
    loader = FakeLoader(universe)
    repo = repo_with(store, loader)

    frame, cov = repo.load("600519", "2024-01-01", "2024-03-29", warmup_bars=60)

    before = frame[frame.index < pd.Timestamp("2024-01-01")]
    assert len(before) == 60 and cov.warmup_rows == 60 and cov.warmup_bars == 60
    assert before.index[-1] == pd.Timestamp("2023-12-29")
    assert cov.first == str(before.index[0].date())
    assert cov.requested_start == "2024-01-01" and cov.complete
    assert pd.Timestamp(loader.calls[0][1]) < before.index[0]
    # The extended range is recorded, so the same run is a cache hit next time.
    repo.load("600519", "2024-01-01", "2024-03-29", warmup_bars=60)
    assert len(loader.calls) == 1


def test_warmup_is_best_effort_and_reported_when_short(store):
    loader = FakeLoader(bars("2023-12-01", "2024-12-31"))  # listed in December

    frame, cov = repo_with(store, loader).load("600519", "2024-01-01", "2024-03-29", warmup_bars=60)

    assert cov.warmup_rows == len(bars("2023-12-01", "2023-12-31")) < 60
    assert any("预热" in w for w in cov.warnings)
    assert len(loader.calls) == 1  # the data starts well after the extended start: no retry


def test_hash_is_stable_across_download_and_cache_and_sensitive_to_prices(store):
    loader = FakeLoader(bars("2024-01-01", "2024-12-31"))
    repo = repo_with(store, loader)
    first, cov1 = repo.load("600519", "2024-01-01", "2024-12-31")
    second, cov2 = repo.load("600519", "2024-01-01", "2024-12-31")

    assert cov1.sha256 == cov2.sha256 == bars_sha256(first) == bars_sha256(second)
    assert re.fullmatch(r"[0-9a-f]{64}", cov1.sha256)
    # Column and row order do not matter; a price does.
    shuffled = first[list(reversed(first.columns))].iloc[::-1]
    assert bars_sha256(shuffled) == cov1.sha256
    changed = first.copy()
    changed.iloc[5, changed.columns.get_loc("close")] += 0.01
    assert bars_sha256(changed) != cov1.sha256


def test_hash_covers_every_column_a_strategy_or_the_broker_can_read():
    base = bars("2024-01-01", "2024-02-29").assign(pct_change=0.5, turnover=2.0)
    for column in ("open", "high", "low", "close", "volume", "amount", "pct_change", "turnover"):
        changed = base.copy()
        changed.iloc[3, changed.columns.get_loc(column)] += 1
        assert bars_sha256(changed) != bars_sha256(base), column
    # A column passed in with the frame is handed to the strategy too.
    assert bars_sha256(base.assign(signal=1.0)) != bars_sha256(base)
    assert bars_sha256(base.drop(columns="turnover")) != bars_sha256(base)


def test_hash_is_the_documented_canonical_text():
    import hashlib
    frame = pd.DataFrame({"turnover": [0.1], "close": [1.5], "open": [1.0], "high": [2.0], "low": [-0.0],
                          "volume": [100], "amount": [150.0], "pct_change": [float("nan")], "note": ["a,b"]},
                         index=pd.DatetimeIndex(["2024-01-02"], name="date"))
    text = ("bars-v2\n"
            "date,open,high,low,close,volume,amount,pct_change,turnover,note\n"
            '2024-01-02,1.0,2.0,0.0,1.5,100.0,150.0,nan,0.1,"a,b"\n')
    assert bars_sha256(frame) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_index_is_loaded_without_an_adjustment(store):
    loader = FakeLoader(bars("2024-01-01", "2024-12-31", base=3500.0))

    _, cov = repo_with(store, loader).load("IDX:000300", "2024-01-01", "2024-12-31", adjust="qfq")

    assert loader.calls[0][3] == "" and cov.adjust == ""
    assert cov.source == "akshare.index_zh_a_hist"


@pytest.mark.parametrize("kwargs", [
    dict(start="2024-12-31", end="2024-01-01"),
    dict(adjust="forward"),
    dict(warmup_bars=-1),
    dict(warmup_bars=True),
])
def test_invalid_requests_are_rejected(store, kwargs):
    args = dict(ticker="600519", start="2024-01-01", end="2024-12-31")
    args.update(kwargs)
    with pytest.raises(ValueError):
        repo_with(store, FakeLoader(bars("2024-01-01", "2024-12-31"))).load(**args)


def test_adjust_none_is_an_alias_for_unadjusted(store):
    loader = FakeLoader(bars("2024-01-01", "2024-12-31"))
    _, cov = repo_with(store, loader).load("600519", "2024-01-01", "2024-12-31", adjust="none")
    assert cov.adjust == "" and loader.calls[0][3] == ""


def test_coverage_serialises_to_plain_data(store):
    _, cov = repo_with(store, FakeLoader(bars("2024-01-01", "2024-12-31"))).load("600519", "2024-01-01", "2024-12-31")
    data = cov.to_dict()
    for field in ("ticker", "adjust", "source", "requested_start", "requested_end", "first", "last",
                  "rows", "complete", "offline", "fetched_at", "sha256"):
        assert field in data
    assert data["warnings"] == []


def test_union_calendar_keeps_days_the_primary_symbol_missed():
    primary = bars("2024-01-01", "2024-01-12").drop(pd.Timestamp("2024-01-05"))
    auxiliary = bars("2024-01-03", "2024-01-16")

    calendar = union_calendar({"A": primary, "B": auxiliary})

    assert pd.Timestamp("2024-01-05") in calendar
    assert calendar.is_monotonic_increasing and calendar.is_unique
    assert (calendar[0], calendar[-1]) == (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-16"))


# ------------------------------------------------------------------ #
#  Imported data is discoverable per adjustment
# ------------------------------------------------------------------ #

def import_raw(store, symbol="600519", start="2024-01-01", end="2024-12-31"):
    """What the page's CSV import does: save as 不复权 and record the file's span."""
    raw = bars(start, end)
    store.save_daily(symbol, raw, "")
    store.record_coverage(symbol, "", start, end)
    return raw


def test_list_available_reports_the_adjustments_that_have_data(store):
    import_raw(store)
    repo_with(store, FakeLoader(bars("2024-01-01", "2024-12-31"))).load("600519", "2024-01-01", "2024-06-30")

    listed = {item["symbol"]: item for item in store.list_available()}

    assert set(listed) == {"600519", "SHA:600519"}
    (raw,) = listed["600519"]["adjustments"]
    assert (raw["adjust"], raw["first"], raw["last"], raw["sources"]) == ("", "2024-01-01", "2024-12-31", ["import"])
    assert raw["rows"] == len(bars("2024-01-01", "2024-12-31"))
    assert raw["coverage"] == [["2024-01-01", "2024-12-31"]]
    (qfq,) = listed["SHA:600519"]["adjustments"]
    assert (qfq["adjust"], qfq["sources"]) == ("qfq", ["akshare.stock_zh_a_hist"])


def test_available_merges_legacy_keys_per_adjustment(store):
    import_raw(store)
    repo = repo_with(store, FakeLoader(bars("2024-01-01", "2024-12-31")))
    repo.load("SHA:600519", "2024-01-01", "2024-06-30")

    available = repo.available("600519.SH")

    assert set(available) == {"", "qfq"}
    assert available[""]["store_key"] == "600519" and available[""]["sources"] == ["import"]
    assert available["qfq"]["store_key"] == "SHA:600519"
    assert repo.available("SHE:000001") == {}


def test_import_under_another_adjustment_is_not_ignored_silently(store):
    import_raw(store)
    loader = FakeLoader(bars("2024-01-01", "2024-12-31"))
    repo = repo_with(store, loader)

    _, raw = repo.load("SHA:600519", "2024-01-01", "2024-12-31", adjust="")
    assert loader.calls == [] and raw.source == "import" and raw.store_key == "600519"

    _, qfq = repo.load("SHA:600519", "2024-01-01", "2024-12-31", adjust="qfq")
    assert len(loader.calls) == 1
    assert any("不复权" in w and "import" in w for w in qfq.warnings)


def test_offline_error_points_to_data_under_another_adjustment(store):
    import_raw(store)
    with pytest.raises(BarsUnavailableError, match="不复权"):
        repo_with(store, FakeLoader(error=ConnectionError("down"))).load("600519", "2024-01-01", "2024-12-31")
