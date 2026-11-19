"""Engine data loading: findings 2 and 5 of the service review ("Backtest run
service and API"), offline with hand-made frames, a fake loader and an injected
clock (plain datetimes with a fixed UTC offset: no time-zone database needed)."""
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from backtesting.broker.simulated import BrokerConfig
from backtesting.data.repository import BAR_COLUMNS, bars_sha256
from backtesting.engine import BacktestEngine, MarketDataUnavailableError
from backtesting.service import BacktestService, TaskRegistry, parse_spec
from backtesting.strategies.base import Strategy

NO_FEES = BrokerConfig(slippage_pct=0.0, commission_buy=0.0, commission_sell=0.0, min_commission=0.0)
CHINA = timezone(timedelta(hours=8))
# Friday 2024-01-05, 10:00 in Shanghai: the session is open, today's bar is not final.
MORNING = datetime(2024, 1, 5, 10, 0, tzinfo=CHINA)


def flat(days, closes):
    index = pd.DatetimeIndex(pd.to_datetime(days), name="date")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": 1_000_000.0, "amount": [c * 1_000_000.0 for c in closes]}, index=index)


# The provider at 10:00 on Jan 5 already has an intraday bar for today (20, not final).
INTRADAY = flat(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"], [10.0, 10.0, 10.0, 20.0])


class Provider:
    def __init__(self, frame):
        self.frame, self.calls = frame, []

    def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, start_date, end_date, adjust))
        return self.frame.loc[start_date:end_date].copy()


class HoldFromTheStart(Strategy):
    name = "HoldFromTheStart"

    def on_init(self):
        self.sent = False

    def on_bar(self):
        if not self.sent:
            self.buy(quantity=100)
            self.sent = True


# ------------------------------------------------------------------ #
#  Finding 2: today's incomplete bar never enters the simulation
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("use_cache", [True, False], ids=["cache", "direct"])
def test_todays_intraday_bar_is_neither_simulated_nor_cached(monkeypatch, use_cache):
    # Reviewer's reproduction: clock Jan 5 10:00, prices Jan 2-5 [10, 10, 10, 20]: the
    # run ended Jan 5 and reported +100%; on completed bars it ends Jan 4 at 0%.
    engine = BacktestEngine(initial_cash=100_000, broker_config=NO_FEES, use_cache=use_cache,
                            now=lambda: MORNING)
    provider = Provider(INTRADAY)
    monkeypatch.setattr(engine._loader, "get_daily", provider.get_daily)

    engine.add_data("600000", "2024-01-02", "2024-01-05", warmup=0)
    engine.add_strategy(HoldFromTheStart)
    result = engine.run()

    assert len(provider.calls) == 1
    feed = engine._data_feeds["600000"]
    assert str(feed._df.index[-1].date()) == "2024-01-04"
    assert result.period["last_date"] == "2024-01-04"
    # Bought 100 at 10 on Jan 3, valued at the Jan 4 close of 10: only the statutory
    # fees are lost (no commission; transfer fee 0.01 each way, stamp duty 0.50 on the
    # end-of-data sale = 0.52), not the +1,000 of the provisional close of 20.
    assert result.fees_total == pytest.approx(0.52)
    assert result.to_dict()["final_value"] == pytest.approx(100_000.0 - 0.52)
    coverage = result.coverage["600000"]
    assert coverage.provisional_last_bar is True and coverage.last == "2024-01-04"
    assert coverage.requested_end == "2024-01-05"
    note = next(c for c in result.caveats if c["code"] == "provisional_bar")
    assert "2024-01-04" in note["text"]
    # Nothing provisional is written: the cache never holds Jan 5 (and the direct
    # path leaves the cache alone altogether).
    cached = engine._store.load_daily("SHA:600000", "2024-01-01", "2024-01-31", "qfq")
    assert [day.strftime("%Y-%m-%d") for day in cached.index] == (
        ["2024-01-02", "2024-01-03", "2024-01-04"] if use_cache else [])


def test_a_service_run_and_its_manifest_end_at_the_last_complete_bar(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_BACKTEST_DIR", str(tmp_path / "bt"))
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    provider = Provider(INTRADAY)
    service = BacktestService(strategy_resolver=lambda name: HoldFromTheStart,
                              loader_factory=lambda plan: provider,
                              today=lambda: date(2024, 1, 5), registry=TaskRegistry())
    # The service's day clock means "after the close"; this run happens at 10:00.
    monkeypatch.setattr(service, "_now_fn", lambda: (lambda: MORNING))

    plan = service.plan(parse_spec({"ticker": "600000", "strategy": "hold", "period": "custom",
                                    "start_date": "2024-01-02", "warmup": 0, "benchmark": "none",
                                    "save": False}))
    assert plan.end == "2024-01-05"                     # asked for today
    run_result, legacy = service.execute(plan)

    assert run_result["facts"]["last_date"] == "2024-01-04"
    assert run_result["facts"]["period"]["requested_end"] == "2024-01-05"
    assert [row["date"] for row in run_result["equity"]][-1] == "2024-01-04"
    manifest = run_result["manifest"]
    assert manifest["spec"]["period"]["end"] == "2024-01-04"
    assert manifest["coverage"][0]["last"] == "2024-01-04"
    assert manifest["coverage"][0]["provisional_last_bar"] is True
    # Bought 100 at 10 on Jan 3, valued at 10 on Jan 4: only the fees are lost.
    assert -0.001 < run_result["metrics"]["total_return"] <= 0.0


# ------------------------------------------------------------------ #
#  Finding 5: one stored series per load, described by its own flags
# ------------------------------------------------------------------ #

def loaded(feed):
    """The bars the repository handed to the engine: the frame the coverage hash describes.

    The feed's frame is those bars plus the feature columns the engine derives from them
    (BacktestEngine.with_features: prev_close, returns, ma_*, rsi_14, ...). bars_sha256 hashes
    every column of the frame it is given, and the coverage hash leaves the features out on
    purpose: they are a function of the bars and of ENGINE_VERSION (see bars_sha256). So compare
    like with like: drop exactly the columns with_features adds. It never changes a column the
    bars already have, so any other column the repository handed over stays in the comparison.
    """
    bars = feed._df[[column for column in feed._df.columns if column in BAR_COLUMNS]]
    derived = set(BacktestEngine.with_features(bars).columns) - set(bars.columns)
    return feed._df.drop(columns=sorted(derived))


def offline(*args, **kwargs):
    raise ConnectionError("network unreachable")


def two_aliases(legacy_end):
    """Legacy key 600000 at 10 covered Jan 2..legacy_end; canonical SHA:600000 at 5
    (another adjustment anchor) covered only Jan 2-10."""
    engine = BacktestEngine(initial_cash=100_000, broker_config=NO_FEES)
    days = pd.bdate_range("2024-01-02", legacy_end)
    legacy = flat(days, [10.0] * len(days))
    engine._store.save_daily("600000", legacy, "qfq")
    engine._store.record_coverage("600000", "qfq", "2024-01-02", legacy_end)
    early = pd.bdate_range("2024-01-02", "2024-01-10")
    engine._store.save_daily("SHA:600000", flat(early, [5.0] * len(early)), "qfq")
    engine._store.record_coverage("SHA:600000", "qfq", "2024-01-02", "2024-01-10")
    return engine, legacy


@pytest.mark.parametrize("allow_partial", [False, True])
def test_the_alias_that_covers_the_window_is_the_series_that_runs(monkeypatch, allow_partial):
    # Reviewer's reproduction: the legacy alias covered Jan 2-31, so the engine took the
    # window as cached and cleared `offline`, but the repository's warm-up-extended
    # request picked the canonical Jan 2-10 fragment: allow_partial=False still ran,
    # on a third of the window, reported as complete and online.
    engine, legacy = two_aliases("2024-01-31")
    monkeypatch.setattr(engine._loader, "get_daily", offline)

    engine.add_data("600000", "2024-01-02", "2024-01-31", warmup=2, allow_partial=allow_partial)

    feed = engine._data_feeds["600000"]
    coverage = engine.coverage["600000"]
    assert feed._df["close"].tolist() == [10.0] * len(legacy)          # only the legacy series
    assert coverage.store_key == "600000" and coverage.offline is False
    assert (coverage.first, coverage.last, coverage.rows) == ("2024-01-02", "2024-01-31", len(legacy))
    assert coverage.sha256 == bars_sha256(loaded(feed))
    # No bar before Jan 2 in that series: the warm-up is short, and says so.
    assert coverage.warmup_rows == 0 and engine._warmup_info["600000"]["available"] == 0


def test_without_an_alias_that_covers_the_window_the_partial_flags_hold(monkeypatch):
    # Neither alias covers Jan 2-31: the series used is partial and offline, so a run
    # that does not allow partial data is refused, and an allowed one says so.
    engine, _ = two_aliases("2024-01-19")
    monkeypatch.setattr(engine._loader, "get_daily", offline)
    with pytest.raises(MarketDataUnavailableError):
        engine.add_data("600000", "2024-01-02", "2024-01-31", warmup=2)

    engine, _ = two_aliases("2024-01-19")
    monkeypatch.setattr(engine._loader, "get_daily", offline)
    engine.add_data("600000", "2024-01-02", "2024-01-31", warmup=2, allow_partial=True)
    coverage = engine.coverage["600000"]
    feed = engine._data_feeds["600000"]
    assert coverage.offline is True and coverage.complete is False
    assert coverage.rows == len(feed._df) and coverage.sha256 == bars_sha256(loaded(feed))
    assert coverage.store_key in ("600000", "SHA:600000")
    expected = 10.0 if coverage.store_key == "600000" else 5.0
    assert set(feed._df["close"]) == {expected}
