"""Offline tests for the classic backtest API (synthetic data, tmp storage only)."""
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backtesting.data.store as store_module
import backtesting.engine as engine_module
import backtesting.history as history_module
from backend.api import backtest
from backtesting.data.loader import DataLoader
from backtesting.reflection.llm_reflector import BacktestReflector
from backtesting.strategies.base import Strategy


class BuyOnceStrategy(Strategy):
    """Buys once with half of the cash on the first tradable bar."""

    name = "BuyOnce"
    parameters = {"position_pct": 0.5}

    def on_init(self):
        self.did_buy = False

    def on_bar(self):
        if not self.did_buy:
            self.buy(pct_cash=self.parameters["position_pct"])
            self.did_buy = True


def make_bars(start="2024-01-01", periods=30, price=10.0, step=0.1):
    dates = pd.date_range(start, periods=periods, freq="B")
    close = [round(price + step * i, 2) for i in range(periods)]
    df = pd.DataFrame(
        {
            "open": close,
            "high": [round(c * 1.01, 2) for c in close],
            "low": [round(c * 0.99, 2) for c in close],
            "close": close,
            "volume": [1_000_000.0] * periods,
            "amount": [10_000_000.0] * periods,
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def cache_bars(env, symbol, bars, adjust="qfq"):
    """Cache bars the way a completed download of exactly their date range does."""
    env.bars.save_daily(symbol, bars, adjust)
    env.bars.record_coverage(symbol, adjust, str(bars.index[0].date()), str(bars.index[-1].date()))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point every store the router touches at tmp_path and forbid network downloads."""
    bars_db = str(tmp_path / "bars.sqlite")
    history_db = str(tmp_path / "history.sqlite")

    class TmpDataStore(store_module.DataStore):
        def __init__(self, db_path=None):
            super().__init__(bars_db)

    class TmpHistoryStore(history_module.BacktestHistoryStore):
        def __init__(self, db_path=None):
            super().__init__(history_db)

    def no_network(self, symbol, start_date, end_date, adjust="qfq"):
        raise AssertionError(f"unexpected market data download for {symbol}")

    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    (custom_dir / "__init__.py").touch()

    monkeypatch.setattr(store_module, "DataStore", TmpDataStore)
    monkeypatch.setattr(engine_module, "DataStore", TmpDataStore)
    monkeypatch.setattr(history_module, "BacktestHistoryStore", TmpHistoryStore)
    monkeypatch.setattr(DataLoader, "get_daily", no_network)
    # Never read the real LLM configuration (or call a real API) from tests.
    monkeypatch.setattr(BacktestReflector, "_get_llm_provider", lambda self: None)
    monkeypatch.setattr(backtest, "CUSTOM_STRATEGY_DIR", custom_dir)
    monkeypatch.delenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", raising=False)
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    # Runs live in the service's bounded registry (it replaced _running_tasks/_completed_results).
    backtest.SERVICE.registry.clear()

    class Env:
        pass

    result = Env()
    result.tmp_path = tmp_path
    result.custom_dir = custom_dir
    result.bars = TmpDataStore()
    result.history = TmpHistoryStore()
    return result


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(backtest.router)
    return TestClient(app)


def use_buy_once_strategy(monkeypatch):
    monkeypatch.setattr(
        backtest, "_get_strategy_registry",
        lambda: {"buy_once": {"cls": BuyOnceStrategy, "builtin": True}},
    )


def run_payload(symbol, bars, **overrides):
    payload = {
        "symbol": symbol,
        "start_date": str(bars.index[0].date()),
        "end_date": str(bars.index[-1].date()),
        "strategy": "buy_once",
        "warmup": 1,
        "with_reflection": False,
    }
    payload.update(overrides)
    return payload


def save_history_run(history, run_id="run-a", total_trades=8, lessons=("keep position sizes small",)):
    history.save_run(
        result_dict={
            "run_id": run_id,
            "run_date": "2024-03-01 10:00:00",
            "strategy_name": "BuyOnce",
            "primary_symbol": "TEST",
            "total_trades": total_trades,
            "sharpe_ratio": 1.25,
        },
        reflection={"analysis": "synthetic", "lessons": list(lessons), "improvements": []},
    )


# ------------------------------------------------------------------ #
#  5a: fixed /history/* paths must not be shadowed by /history/{run_id}
# ------------------------------------------------------------------ #

def test_history_best_is_not_shadowed_by_run_id_route(client, env):
    save_history_run(env.history, run_id="run-a", total_trades=8)

    response = client.get("/api/backtest/history/best")

    assert response.status_code == 200
    assert [row["run_id"] for row in response.json()["best"]] == ["run-a"]


def test_history_lessons_is_not_shadowed_by_run_id_route(client, env):
    save_history_run(env.history, run_id="run-a", lessons=("keep position sizes small",))

    response = client.get("/api/backtest/history/lessons", params={"limit": 50})

    assert response.status_code == 200
    assert [row["lesson"] for row in response.json()["lessons"]] == ["keep position sizes small"]


def test_history_detail_still_resolves_real_run_ids(client, env):
    save_history_run(env.history, run_id="run-a")

    assert client.get("/api/backtest/history/run-a").json()["run_id"] == "run-a"
    assert client.get("/api/backtest/history/missing").status_code == 404


# ------------------------------------------------------------------ #
#  5c: reflection must not be presented as an LLM analysis when no LLM ran
# ------------------------------------------------------------------ #

def wait_for_reflection(run_id, timeout=10):
    task = backtest.SERVICE.registry.get(run_id)
    assert task is not None and task.reflection_done.wait(timeout), "the reflection did not finish"
    return task.reflection


# Reflection no longer runs inside the request (plan 1c): /run-sync returns the
# result at once with reflection_status "pending", and the reflection lands in
# the run (GET /result, GET /runs/{id}) and in its history row when it is done.
def test_reflection_without_llm_is_reported_as_rule_based(client, env, monkeypatch):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)
    progress_seen, payloads = [], []
    original_reflect = BacktestReflector.reflect_facts

    async def spy_reflect(self, payload):
        payloads.append(payload)
        progress_seen.extend(task.legacy_status()["progress"] for task in backtest.SERVICE.registry.values())
        return await original_reflect(self, payload)

    monkeypatch.setattr(BacktestReflector, "reflect_facts", spy_reflect)

    response = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, with_reflection=True))

    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    assert response.json()["reflection_status"] in ("pending", "completed")
    reflection = wait_for_reflection(run_id)
    assert reflection["status"] == "completed"
    assert reflection["result"]["source"] == "rules"
    assert "未使用 LLM" in reflection["result"]["analysis"]
    assert progress_seen and all("LLM" not in text for text in progress_seen)
    # It works from the run's facts and sees the round trips it comments on.
    assert payloads[0]["run_id"] == run_id and len(payloads[0]["round_trips"]) == 1
    assert client.get(f"/api/backtest/result/{run_id}").json()["reflection"]["source"] == "rules"
    assert client.get(f"/api/backtest/history/{run_id}").json()["reflection"]["source"] == "rules"


FAKE_KEY = "sk-FAKE-REVIEW-KEY"


class LeakyProvider:
    async def chat(self, messages, **kwargs):
        raise RuntimeError(f"Illegal header value b'Bearer {FAKE_KEY}\\n'")


async def leaky_reflect(self, result):
    raise RuntimeError(f"Illegal header value b'Bearer {FAKE_KEY}\\n'")


@pytest.mark.parametrize("leak", ["provider", "reflector"])
def test_provider_error_text_stays_out_of_logs_response_and_history(client, env, monkeypatch, caplog, leak):
    import logging

    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)
    if leak == "provider":
        monkeypatch.setattr(BacktestReflector, "_get_llm_provider", lambda self: LeakyProvider())
    else:
        monkeypatch.setattr(BacktestReflector, "reflect_facts", leaky_reflect)

    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, with_reflection=True))
        assert response.status_code == 200, response.text
        reflection = wait_for_reflection(response.json()["run_id"])

    assert FAKE_KEY not in response.text
    assert FAKE_KEY not in caplog.text
    run_id = response.json()["run_id"]
    for path in (f"/api/backtest/history/{run_id}", f"/api/backtest/runs/{run_id}", f"/api/backtest/result/{run_id}"):
        saved = client.get(path)
        assert saved.status_code == 200
        assert FAKE_KEY not in saved.text
    if leak == "provider":
        assert reflection["result"]["source"] == "rules"
    else:
        assert reflection["status"] == "failed" and FAKE_KEY not in reflection["message"]


# ------------------------------------------------------------------ #
#  5d: imported CSV bars must be usable by the engine and validated first
# ------------------------------------------------------------------ #

def import_csv(client, df, symbol="600000", **form):
    content = df.reset_index().to_csv(index=False).encode("utf-8")
    return client.post(
        "/api/backtest/data/import-csv",
        files={"file": ("bars.csv", content, "text/csv")},
        data={"symbol": symbol, **form},
    )


# "none" is what the web page sends; omitting the field uses the default.
@pytest.mark.parametrize("form", [{"adjust": "none"}, {}, {"adjust": ""}])
def test_imported_unadjusted_csv_is_used_by_the_backtest(client, env, monkeypatch, form):
    bars = make_bars()
    use_buy_once_strategy(monkeypatch)

    imported = import_csv(client, bars, **form)
    assert imported.status_code == 200, imported.text
    assert imported.json()["rows_imported"] == len(bars)
    assert imported.json()["adjust"] == ""

    # The fixture fails the run if the engine tries to download instead of using the import.
    run = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, adjust=""))
    assert run.status_code == 200, run.text
    assert run.json()["total_trades"] >= 1


def test_imported_csv_keeps_an_explicit_adjustment(client, env, monkeypatch):
    bars = make_bars()
    use_buy_once_strategy(monkeypatch)

    assert import_csv(client, bars, adjust="hfq").json()["adjust"] == "hfq"
    run = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, adjust="hfq"))
    assert run.status_code == 200, run.text


def test_csv_import_records_the_interval_it_spans(client, env, monkeypatch):
    bars = make_bars()
    use_buy_once_strategy(monkeypatch)

    assert import_csv(client, bars, adjust="").status_code == 200

    span = (str(bars.index[0].date()), str(bars.index[-1].date()))
    assert [(start, end) for start, end, _ in env.bars.get_coverage("600000", "")] == [span]
    # A longer range is not covered by the import. Offline, the run now uses the
    # imported fragment and says so (plan section 7: partial cache plus a caveat,
    # the actual dates, nothing extrapolated) instead of failing with a 500.
    longer = run_payload("600000", bars, adjust="", end_date="2024-12-31")
    run = client.post("/api/backtest/run-sync", json=longer)
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["period"]["last_date"] == span[1]
    assert body["coverage"]["600000"]["offline"] is True
    caveats = {c["code"]: c for c in body["caveats"]}
    assert caveats["offline_cache"]["level"] == "warning"
    assert f"{span[0]}~{span[1]}" in caveats["offline_cache"]["text"]
    assert f"{span[0]}~2024-12-31" in caveats["offline_cache"]["text"]
    assert "coverage_short" in caveats


def test_data_download_records_the_requested_interval(client, env, monkeypatch):
    listed_late = make_bars(start="2024-03-01", periods=30)
    use_buy_once_strategy(monkeypatch)
    monkeypatch.setattr(DataLoader, "get_daily", lambda self, *args, **kwargs: listed_late.copy())

    response = client.post("/api/backtest/data/download", json={
        "symbol": "600000", "start_date": "2024-01-01", "end_date": "2024-04-30", "adjust": "qfq"})
    assert response.status_code == 200, response.text
    assert [(s, e) for s, e, _ in env.bars.get_coverage("600000", "qfq")] == [("2024-01-01", "2024-04-30")]

    def no_network(self, *args, **kwargs):
        raise AssertionError("the downloaded range is covered")

    monkeypatch.setattr(DataLoader, "get_daily", no_network)
    run = client.post("/api/backtest/run-sync", json=run_payload(
        "600000", listed_late, start_date="2024-01-01", end_date="2024-04-30"))
    assert run.status_code == 200, run.text


def test_csv_import_rejects_unknown_adjustment(client, env):
    response = import_csv(client, make_bars(), adjust="bogus")

    assert response.status_code == 400
    assert env.bars.get_available_symbols() == []


def broken_ohlc(df):
    df = df.copy()
    df.iloc[3, df.columns.get_loc("high")] = df["low"].iloc[3] - 1.0
    return df


def set_value(column, value_of_row):
    def corrupt(df):
        df = df.copy()
        df.iloc[3, df.columns.get_loc(column)] = value_of_row(df.iloc[3])
        return df
    return corrupt


# A file in descending date order is no longer rejected (it is sorted, see below);
# the "unsorted" case moved to test_csv_import_sorts_a_newest_first_file.
@pytest.mark.parametrize("corrupt", [
    broken_ohlc,                                           # high < low
    lambda df: pd.concat([df, df.iloc[[5]]]),              # duplicated date
    set_value("close", lambda row: 0.0),                   # non-positive price
    set_value("low", lambda row: -1.0),
    set_value("close", lambda row: row["high"] + 0.5),     # close above high
    set_value("close", lambda row: row["low"] - 0.5),      # close below low
], ids=["ohlc", "duplicate", "zero-close", "negative-low", "close-above-high", "close-below-low"])
def test_csv_import_validates_bars_before_saving(client, env, corrupt):
    response = import_csv(client, corrupt(make_bars()))

    assert response.status_code == 400
    assert env.bars.get_available_symbols() == []


def test_csv_import_sorts_a_newest_first_file(client, env, monkeypatch):
    # Many exports list the newest day first; with unique dates the order carries
    # no information (the store returns bars by date), so the file is sorted.
    bars = make_bars()
    use_buy_once_strategy(monkeypatch)

    response = import_csv(client, bars.iloc[::-1])

    assert response.status_code == 200, response.text
    assert response.json()["sorted_by_date"] is True
    assert response.json()["rows_imported"] == len(bars)
    stored = env.bars.load_daily("600000", "2024-01-01", "2024-12-31", "")
    assert list(stored["close"]) == list(bars["close"])
    run = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, adjust=""))
    assert run.status_code == 200, run.text


@pytest.mark.parametrize("header,row,duplicate", [
    ("date,open,open,high,low,close,volume", "2024-01-02,10,1000,11,9,10,1000", "open"),
    ("date,open,high,low,close,volume,volume", "2024-01-02,10,11,9,10,1000,5", "volume"),
    ("date,open,high,low,close, close,volume", "2024-01-02,10,11,9,10,10.5,1000", "close"),
])
def test_csv_import_rejects_duplicate_column_names(client, env, header, row, duplicate):
    # pandas would rename the second column to "open.1" and import the first one silently.
    content = f"{header}\n{row}\n".encode("utf-8")

    response = client.post("/api/backtest/data/import-csv",
                           files={"file": ("bars.csv", content, "text/csv")},
                           data={"symbol": "600000", "adjust": ""})

    assert response.status_code == 400
    assert "重复" in response.json()["detail"] and duplicate in response.json()["detail"]
    assert env.bars.get_available_symbols() == []


def with_suspensions(df, rows):
    """Turn `rows` into suspension days: zero volume, flat at the last traded close."""
    df = df.copy()
    for i in rows:
        price = df["close"].iloc[i - 1]
        for column in ("open", "high", "low", "close"):
            df.iloc[i, df.columns.get_loc(column)] = price
        df.iloc[i, df.columns.get_loc("volume")] = 0.0
        df.iloc[i, df.columns.get_loc("amount")] = 0.0
    return df


def test_csv_import_accepts_a_history_with_suspension_days(client, env, monkeypatch):
    # Nine of 30 bars (30%) are suspension days, above the 25% zero-volume limit.
    bars = with_suspensions(make_bars(), [3, 4, 5, 10, 11, 12, 20, 21, 22])
    use_buy_once_strategy(monkeypatch)

    response = import_csv(client, bars)

    assert response.status_code == 200, response.text
    assert response.json()["rows_imported"] == 30
    run = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, adjust=""))
    assert run.status_code == 200, run.text
    assert run.json()["data_quality"]["600000"]["metrics"]["suspended_bar_count"] == 9


def preview(client, **params):
    response = client.get("/api/backtest/data/preview/600000", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_without_an_adjustment_shows_the_one_that_has_data(client, env):
    # The data page asks for a preview without naming an adjustment; an unadjusted
    # CSV import used to preview as empty because the default was qfq.
    bars = make_bars()
    assert import_csv(client, bars, adjust="").status_code == 200

    body = preview(client, limit=5)
    assert body["adjust"] == ""
    assert body["available_adjustments"] == [""]
    assert body["total_rows"] == len(bars)
    assert len(body["data"]) == 5

    cache_bars(env, "600000", bars, adjust="qfq")
    assert preview(client)["adjust"] == "qfq"          # qfq first when it has data
    assert preview(client)["available_adjustments"] == ["qfq", ""]

    explicit = preview(client, adjust="hfq")           # an explicit choice is kept
    assert explicit["adjust"] == "hfq" and explicit["total_rows"] == 0 and explicit["data"] == []
    assert preview(client, adjust="none")["adjust"] == ""


# ------------------------------------------------------------------ #
#  5e: a custom strategy file registers the class it defines, not what it imports
# ------------------------------------------------------------------ #

TUNED_STRATEGY = '''
from backtesting.strategies.examples import SmaCrossStrategy


class MyTunedCross(SmaCrossStrategy):
    """Tuned variant of the built-in cross."""

    name = "MyTunedCross"
'''


def test_custom_strategy_resolves_to_the_class_defined_in_its_file(client, env, monkeypatch):
    monkeypatch.setenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", "1")
    (env.custom_dir / "my_tuned.py").write_text(TUNED_STRATEGY, encoding="utf-8")

    assert backtest._get_strategy_class("custom:my_tuned").__name__ == "MyTunedCross"
    listing = client.get("/api/backtest/strategies").json()["strategies"]
    assert [s["class"] for s in listing if s["id"] == "custom:my_tuned"] == ["MyTunedCross"]


def test_upload_reports_only_classes_defined_in_the_file(client, env, monkeypatch):
    monkeypatch.setenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", "1")

    uploaded = client.post("/api/backtest/strategies/upload",
                           json={"filename": "my_tuned", "code": TUNED_STRATEGY})
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["strategy_classes"] == ["MyTunedCross"]

    # A file that only imports a built-in strategy defines nothing of its own.
    only_import = client.post(
        "/api/backtest/strategies/upload",
        json={"filename": "only_import", "code": "from backtesting.strategies.examples import SmaCrossStrategy\n"},
    )
    assert only_import.status_code == 400
    assert not (env.custom_dir / "only_import.py").exists()


def test_custom_strategy_source_is_the_text_of_its_file(client, env, monkeypatch):
    # inspect.getsource raises TypeError for classes of a module loaded from a file
    # path (it is not in sys.modules), which made this endpoint a 500.
    monkeypatch.setenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", "1")
    (env.custom_dir / "my_tuned.py").write_text(TUNED_STRATEGY, encoding="utf-8")

    response = client.get("/api/backtest/strategies/custom:my_tuned/source")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_code"] == TUNED_STRATEGY
    assert body["class_name"] == "MyTunedCross"
    assert body["builtin"] is False


def test_custom_strategy_source_never_reads_outside_the_strategy_folder(client, env, monkeypatch):
    monkeypatch.setenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", "1")
    outside = env.tmp_path / "outside.py"
    outside.write_text(TUNED_STRATEGY.replace("MyTunedCross", "Outside"), encoding="utf-8")
    (env.custom_dir / "linked.py").symlink_to(outside)

    for strategy_id in ("custom:..%2Foutside", "custom:outside", "custom:linked", "custom:__init__"):
        response = client.get(f"/api/backtest/strategies/{strategy_id}/source")
        assert response.status_code == 404, (strategy_id, response.status_code)
        assert "Outside" not in response.text

    monkeypatch.delenv("TRACKER_ALLOW_CUSTOM_STRATEGIES")
    (env.custom_dir / "my_tuned.py").write_text(TUNED_STRATEGY, encoding="utf-8")
    assert client.get("/api/backtest/strategies/custom:my_tuned/source").status_code == 404


def test_builtin_strategy_source_is_still_served(client, env):
    response = client.get("/api/backtest/strategies/sma_cross/source")

    assert response.status_code == 200
    assert "class SmaCrossStrategy" in response.json()["source_code"]


TWO_STRATEGIES = '''
from backtesting.strategies.base import Strategy


class Alpha(Strategy):
    name = "Alpha"

    def on_bar(self):
        pass


class Beta(Strategy):
    name = "Beta"

    def on_init(self):
        self.did_buy = False

    def on_bar(self):
        if not self.did_buy:
            self.buy(pct_cash=0.5)
            self.did_buy = True
'''


def test_every_strategy_class_in_a_file_gets_its_own_id(client, env, monkeypatch):
    monkeypatch.setenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", "1")
    (env.custom_dir / "pair.py").write_text(TWO_STRATEGIES, encoding="utf-8")
    (env.custom_dir / "my_tuned.py").write_text(TUNED_STRATEGY, encoding="utf-8")

    listing = client.get("/api/backtest/strategies").json()["strategies"]
    custom = sorted((s["id"], s["class"]) for s in listing if s["id"].startswith("custom:"))

    # A single-class file keeps its custom:<file> id.
    assert custom == [("custom:my_tuned", "MyTunedCross"), ("custom:pair:Alpha", "Alpha"),
                      ("custom:pair:Beta", "Beta")]
    assert backtest._get_strategy_class("custom:pair:Alpha").__name__ == "Alpha"
    assert backtest._get_strategy_class("custom:pair:Beta").__name__ == "Beta"
    # Ids were matched case-insensitively before; keep that working.
    assert backtest._get_strategy_class("CUSTOM:PAIR:BETA").__name__ == "Beta"
    with pytest.raises(ValueError):
        backtest._get_strategy_class("custom:pair")
    source = client.get("/api/backtest/strategies/custom:pair:Beta/source")
    assert source.status_code == 200 and source.json()["source_code"] == TWO_STRATEGIES

    bars = make_bars()
    cache_bars(env, "600000", bars)
    run = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, strategy="custom:pair:Beta"))
    assert run.status_code == 200, run.text
    assert run.json()["strategy_class"] == "Beta"


def test_upload_of_a_multi_class_file_reports_every_id(client, env, monkeypatch):
    monkeypatch.setenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", "1")

    uploaded = client.post("/api/backtest/strategies/upload", json={"filename": "pair", "code": TWO_STRATEGIES})

    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["strategy_classes"] == ["Alpha", "Beta"]
    assert uploaded.json()["strategy_ids"] == ["custom:pair:Alpha", "custom:pair:Beta"]
    assert uploaded.json()["strategy_id"] in uploaded.json()["strategy_ids"]

    single = client.post("/api/backtest/strategies/upload", json={"filename": "my_tuned", "code": TUNED_STRATEGY})
    assert single.json()["strategy_id"] == "custom:my_tuned"
    assert single.json()["strategy_ids"] == ["custom:my_tuned"]



# ------------------------------------------------------------------ #
#  5f: TRACKER_ALLOW_CUSTOM_STRATEGIES gates loading and deleting, not just upload
# ------------------------------------------------------------------ #

PLANTED_STRATEGY = '''
from pathlib import Path
from backtesting.strategies.base import Strategy

Path(__file__).with_suffix(".executed").write_text("ran")


class Planted(Strategy):
    name = "Planted"

    def on_bar(self):
        pass
'''


def plant_strategy(env):
    path = env.custom_dir / "planted.py"
    path.write_text(PLANTED_STRATEGY, encoding="utf-8")
    return path, env.custom_dir / "planted.executed"


def test_custom_strategy_files_are_not_executed_when_the_switch_is_off(client, env):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    _, marker = plant_strategy(env)

    listing = client.get("/api/backtest/strategies")
    assert listing.status_code == 200
    assert [s["id"] for s in listing.json()["strategies"] if s["id"].startswith("custom:")] == []
    assert client.get("/api/backtest/strategies/custom:planted/source").status_code == 404
    run = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, strategy="custom:planted"))
    assert run.status_code == 422          # an unknown strategy is a user error (was a 500)
    assert "custom:planted" in run.json()["detail"]
    assert not marker.exists()


def test_custom_strategy_files_load_when_the_switch_is_on(client, env, monkeypatch):
    monkeypatch.setenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", "1")
    _, marker = plant_strategy(env)

    listing = client.get("/api/backtest/strategies").json()["strategies"]

    assert [s["class"] for s in listing if s["id"] == "custom:planted"] == ["Planted"]
    assert marker.exists()


def test_delete_custom_strategy_is_refused_when_the_switch_is_off(client, env):
    path, _ = plant_strategy(env)

    response = client.delete("/api/backtest/strategies/custom/planted.py")

    assert response.status_code == 403
    assert path.exists()


def test_delete_custom_strategy_removes_only_user_strategy_files(client, env, monkeypatch):
    monkeypatch.setenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", "1")
    path, _ = plant_strategy(env)
    outside = env.tmp_path / "outside.py"
    outside.write_text("# not a strategy\n", encoding="utf-8")
    notes = env.custom_dir / "notes.txt"
    notes.write_text("keep", encoding="utf-8")

    for name in ("__init__", "__init__.py", "_private.py", "notes.txt", "..%2Foutside.py", "..%2Foutside", "%2E%2E"):
        response = client.delete(f"/api/backtest/strategies/custom/{name}")
        assert response.status_code in (404, 405, 422), (name, response.status_code)
    assert client.delete("/api/backtest/strategies/custom/__init__").status_code == 422
    assert (env.custom_dir / "__init__.py").exists()
    assert outside.exists()
    assert notes.exists()
    assert path.exists()

    assert client.delete("/api/backtest/strategies/custom/planted").status_code == 200
    assert not path.exists()
    assert client.delete("/api/backtest/strategies/custom/planted").status_code == 404


# ------------------------------------------------------------------ #
#  5h: lot size follows the market unless the request sets one
# ------------------------------------------------------------------ #

def buy_quantities(run_json):
    return [t["quantity"] for t in run_json["trade_records"] if t["direction"] == "buy"]


def test_us_symbol_trades_single_shares_by_default(client, env, monkeypatch):
    bars = make_bars(price=150.0, step=0.5)
    cache_bars(env, "AAPL", bars)
    use_buy_once_strategy(monkeypatch)

    run = client.post("/api/backtest/run-sync", json=run_payload("AAPL", bars, initial_cash=10_000))

    assert run.status_code == 200, run.text
    # Half of 10,000 buys 33 shares at about 150; a 100-share lot would buy nothing.
    assert buy_quantities(run.json()) == [33]


def test_a_share_symbol_keeps_100_share_lots_by_default(client, env, monkeypatch):
    bars = make_bars(price=10.0, step=0.1)
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)

    run = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, initial_cash=10_000))

    assert run.status_code == 200, run.text
    assert buy_quantities(run.json()) == [400]


def test_explicit_lot_size_overrides_the_market_default(client, env, monkeypatch):
    bars = make_bars(price=150.0, step=0.5)
    cache_bars(env, "AAPL", bars)
    use_buy_once_strategy(monkeypatch)

    run = client.post("/api/backtest/run-sync",
                      json=run_payload("AAPL", bars, initial_cash=10_000, lot_size=10))

    assert run.status_code == 200, run.text
    assert buy_quantities(run.json()) == [30]


def test_null_lot_size_picks_the_market_default_like_an_omitted_one(client, env, monkeypatch):
    # The page sends lot_size: null for its "自动" choice.
    bars = make_bars(price=150.0, step=0.5)
    cache_bars(env, "AAPL", bars)
    use_buy_once_strategy(monkeypatch)

    explicit_null = client.post("/api/backtest/run-sync",
                                json=run_payload("AAPL", bars, initial_cash=10_000, lot_size=None))
    omitted = client.post("/api/backtest/run-sync", json=run_payload("AAPL", bars, initial_cash=10_000))

    assert explicit_null.status_code == omitted.status_code == 200
    assert buy_quantities(explicit_null.json()) == buy_quantities(omitted.json()) == [33]


@pytest.mark.parametrize("lot_size", [0, -1, -100])
def test_non_positive_lot_size_is_a_validation_error(client, env, monkeypatch, lot_size):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)

    response = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, lot_size=lot_size))

    assert response.status_code == 422
    assert "lot_size" in response.text
    assert client.get("/api/backtest/history").json()["runs"] == []


# ------------------------------------------------------------------ #
#  5j: warmup must be nonnegative
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("warmup", [-1, -60])
def test_negative_warmup_is_rejected_by_the_api(client, env, monkeypatch, warmup):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)

    response = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, warmup=warmup))

    assert response.status_code == 422
    assert "warmup" in response.text
    assert client.get("/api/backtest/history").json()["runs"] == []


# ------------------------------------------------------------------ #
#  5k: risk metrics reach the history table
# ------------------------------------------------------------------ #

def test_saved_run_keeps_volatility_var_and_drawdown_duration(client, env, monkeypatch):
    bars = make_bars()
    closes = [10.0 + (0.4 if i % 2 else 0.0) + 0.05 * i for i in range(len(bars))]
    bars["open"] = bars["close"] = closes
    bars["high"] = [c * 1.01 for c in closes]
    bars["low"] = [c * 0.99 for c in closes]
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)

    run = client.post("/api/backtest/run-sync", json=run_payload("600000", bars))
    assert run.status_code == 200, run.text
    assert run.json()["saved_to_history"] is True

    saved = client.get(f"/api/backtest/history/{run.json()['run_id']}").json()
    assert saved["volatility"] > 0
    assert saved["var_95"] < 0
    assert saved["max_drawdown_duration"] > 0
    assert saved["volatility"] == run.json()["volatility"]


# ------------------------------------------------------------------ #
#  Fees follow the dated market schedule; the result explains itself
# ------------------------------------------------------------------ #

def trades_of(run_json, direction):
    return [t for t in run_json["trade_records"] if t["direction"] == direction]


def test_default_commission_follows_the_market(client, env, monkeypatch):
    # Omitted commission fields mean the market default: a US buy pays nothing
    # (it used to pay 0.03% with a 5 minimum).
    bars = make_bars(price=150.0, step=0.5)
    cache_bars(env, "AAPL", bars)
    use_buy_once_strategy(monkeypatch)

    run = client.post("/api/backtest/run-sync", json=run_payload("AAPL", bars, initial_cash=10_000))

    assert run.status_code == 200, run.text
    assert [t["commission"] for t in trades_of(run.json(), "buy")] == [0.0]


def test_commission_fields_set_the_commission_part_only(client, env, monkeypatch):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)

    # fee_basis commission_only: the fields are the commission (without it a legacy sell rate
    # includes the stamp duty, as in v3.2; see test_an_old_clients_inclusive_sell_rate_...).
    run = client.post("/api/backtest/run-sync", json=run_payload(
        "600000", bars, initial_cash=100_000, commission_buy=0.0003, commission_sell=0.0003, min_commission=5.0,
        fee_basis="commission_only"))

    assert run.status_code == 200, run.text
    (sale,) = trades_of(run.json(), "sell")
    # 4,900 shares valued at the last close 12.90 (turnover 63,210): commission 18.96,
    # stamp duty 0.05% = 31.61, transfer fee 0.001% = 0.63.
    assert sale["quantity"] == 4900 and sale["price"] == 12.9
    assert sale["commission"] == pytest.approx(51.20)


def test_run_reports_caveats_round_trips_and_the_end_mark(client, env, monkeypatch):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)

    body = client.post("/api/backtest/run-sync", json=run_payload("600000", bars)).json()

    codes = {c["code"] for c in body["caveats"]}
    assert {"few_trades", "end_mark", "warmup_short"} <= codes
    assert all(c["level"] in ("info", "warning") and c["text"] for c in body["caveats"])
    assert body["total_trades"] == 1 and body["closed_trades"] == 0
    assert body["round_trips"][0]["end_mark"] is True
    assert body["end_mark"]["positions"][0]["symbol"] == "600000"
    assert body["unfilled"] == []
    assert body["warmup"] == {"bars": 1, "available": 0, "requested_start": str(bars.index[0].date()),
                              "first_date": str(bars.index[1].date())}
    assert body["sharpe_ratio"] is None                      # 28 daily returns
    assert body["saved_to_history"] is True


# ================================================================== #
#  快速回测 routes (WP3): start -> poll -> result, cancel, export, rerun,
#  defaults, presets, history aliases, reflection off by default, no 500s
# ================================================================== #

import json
import math
import re
import sys
import threading
import time
import types
from datetime import date

CHINESE = re.compile(r"[一-鿿]")

OLD_RESULT_KEYS = {
    "run_id", "run_date", "strategy_name", "strategy_params", "primary_symbol", "initial_cash", "final_value",
    "total_return", "annualized_return", "benchmark_return", "alpha", "sharpe_ratio", "sortino_ratio",
    "calmar_ratio", "max_drawdown", "max_drawdown_duration", "volatility", "var_95", "total_trades",
    "closed_trades", "winning_trades", "losing_trades", "breakeven_trades", "win_rate", "payoff_ratio",
    "profit_factor", "avg_profit", "avg_loss", "max_single_profit", "max_single_loss", "avg_holding_days",
    "period", "round_trips", "end_mark", "data_quality", "caveats", "unfilled", "warmup", "assumptions",
    "summary", "start_date", "end_date", "strategy_class", "equity_curve", "trade_records", "reflection",
    "saved_to_history",
}


class TrendStrategy(Strategy):
    """Holds while the close is above its moving average."""
    name = "Trend"
    parameters = {"period": 10}

    def on_bar(self):
        average = self.sma(int(self.parameters["period"]))
        if math.isnan(average):
            return
        if not self.get_position() and self.data.close() > average:
            self.buy(pct_cash=0.9)
        elif self.get_position() and self.data.close() < average:
            self.close_position()


def wavy_bars(start="2023-01-02", periods=500, base=20.0):
    bars = make_bars(start=start, periods=periods, price=base, step=0.0)
    closes = [round(base * (1 + 0.2 * math.sin(i / 15.0)), 2) for i in range(periods)]
    bars["open"] = bars["close"] = closes
    bars["high"] = [round(c * 1.01, 2) for c in closes]
    bars["low"] = [round(c * 0.99, 2) for c in closes]
    return bars


def use_strategies(monkeypatch, **extra):
    registry = {"buy_once": {"cls": BuyOnceStrategy, "builtin": True},
                "trend": {"cls": TrendStrategy, "builtin": True}}
    registry.update({key: {"cls": cls, "builtin": True} for key, cls in extra.items()})
    monkeypatch.setattr(backtest, "_get_strategy_registry", lambda: registry)


def spec_for(bars, **overrides):
    body = {"ticker": "600000", "strategy": "trend", "period": "custom",
            "start_date": str(bars.index[60].date()), "end_date": str(bars.index[-1].date()),
            "benchmark": "none"}
    body.update(overrides)
    return body


def wait_run(client, run_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/backtest/runs/{run_id}").json()
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish")


def start_and_wait(client, body):
    started = client.post("/api/backtest/runs", json=body)
    assert started.status_code == 202, started.text
    assert started.json()["poll"] == f"/api/backtest/runs/{started.json()['run_id']}"
    return wait_run(client, started.json()["run_id"])


def test_start_poll_result(client, env, monkeypatch):
    bars = wavy_bars()
    cache_bars(env, "SHA:600000", bars)
    index = wavy_bars(base=3500.0)
    cache_bars(env, "IDX:000300", index, adjust="")
    use_strategies(monkeypatch)

    done = start_and_wait(client, spec_for(bars, benchmark="auto"))

    assert done["status"] == "completed" and done["stage"] == "done" and done["error"] is None
    assert [s["label"] for s in done["stages"]] == ["准备数据", "模拟交易", "统计"]
    result = done["result"]
    assert result["facts"]["ticker"] == "SHA:600000" and result["facts"]["currency"] == "CNY"
    assert "同期一直持有" in result["headline_zh"] and "沪深300" in result["headline_zh"]
    assert result["benchmark"]["index"]["ticker"] == "IDX:000300"
    assert len(result["equity"]) == len(bars) - 60
    assert result["manifest"]["spec"]["strategy"] == {"kind": "strategy", "id": "trend", "name": "Trend",
                                                      "class": "TrendStrategy", "params": {}}
    assert done["saved_to_history"] is True
    saved = client.get(f"/api/backtest/history/{result['run_id']}").json()
    assert saved["ticker"] == "SHA:600000" and saved["result"]["headline_zh"] == result["headline_zh"]
    assert len(saved["equity_curve"]) == len(result["equity"])        # full daily curve, not ~300 points
    assert client.get("/api/backtest/runs/unknown-run").status_code == 404


def test_cancel_a_running_backtest(client, env, monkeypatch):
    bars = wavy_bars(periods=600)
    cache_bars(env, "SHA:600000", bars)
    running = threading.Event()

    class Slow(TrendStrategy):
        def on_bar(self):
            running.set()
            time.sleep(0.005)
            super().on_bar()

    use_strategies(monkeypatch, slow=Slow)
    started = client.post("/api/backtest/runs", json=spec_for(bars, strategy="slow"))
    run_id = started.json()["run_id"]
    assert running.wait(10)

    cancelled = client.post(f"/api/backtest/runs/{run_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["cancel_requested"] is True

    done = wait_run(client, run_id)
    assert done["status"] == "cancelled" and done["result"] is None
    again = client.post(f"/api/backtest/runs/{run_id}/cancel")
    assert again.status_code == 409 and CHINESE.search(again.json()["detail"])
    assert client.get("/api/backtest/history").json()["runs"] == []
    assert client.post("/api/backtest/runs/missing/cancel").status_code == 404


def test_export_csv_and_json(client, env, monkeypatch):
    bars = wavy_bars()
    cache_bars(env, "SHA:600000", bars)
    use_strategies(monkeypatch)
    result = start_and_wait(client, spec_for(bars))["result"]
    run_id = result["run_id"]

    csv_all = client.get(f"/api/backtest/runs/{run_id}/export", params={"format": "csv"})
    assert csv_all.status_code == 200 and csv_all.headers["content-type"].startswith("text/csv")
    assert f'filename="backtest-{run_id}.csv"' in csv_all.headers["content-disposition"]
    text = csv_all.content.decode("utf-8-sig")
    assert text.startswith("[manifest]\nkey,value\n")
    for title in ("[fills]", "[round_trips]", "[unfilled]", "[equity]"):
        assert f"\n{title}\n" in text
    assert f"data_sha256,{result['manifest']['data_sha256']}" in text

    fills = client.get(f"/api/backtest/runs/{run_id}/export", params={"format": "csv", "part": "fills"})
    lines = fills.content.decode("utf-8-sig").splitlines()
    assert lines[0] == ("date,symbol,direction,quantity,price,commission,pnl,reason,fees,"
                        "end_mark,blocked,blocked_quantity,blocked_zh")
    assert len(lines) == 1 + len(result["fills"])
    equity = client.get(f"/api/backtest/runs/{run_id}/export", params={"part": "equity"})
    assert equity.content.decode("utf-8-sig").splitlines()[0] == \
        "date,total_value,cash,market_value,net_value,drawdown,same_stock,index"

    as_json = client.get(f"/api/backtest/runs/{run_id}/export", params={"format": "json"}).json()
    assert as_json["manifest"] == result["manifest"] and as_json["fills"] == result["fills"]
    assert client.get(f"/api/backtest/runs/{run_id}/export", params={"format": "xlsx"}).status_code == 422
    assert client.get(f"/api/backtest/runs/{run_id}/export", params={"part": "secrets"}).status_code == 422

    # A saved run can still be exported after the in-memory registry forgot it.
    backtest.SERVICE.registry.clear()
    assert client.get(f"/api/backtest/runs/{run_id}/export", params={"part": "fills"}).content == fills.content


def test_rerun_on_the_same_data_and_on_changed_data(client, env, monkeypatch):
    bars = wavy_bars()
    cache_bars(env, "SHA:600000", bars)
    use_strategies(monkeypatch)
    first = start_and_wait(client, spec_for(bars))["result"]

    same = client.post(f"/api/backtest/history/{first['run_id']}/rerun")
    assert same.status_code == 202 and same.json()["rerun_of"] == first["run_id"]
    same_result = wait_run(client, same.json()["run_id"])["result"]
    assert same_result["metrics"] == first["metrics"]
    assert same_result["rerun"]["same_data"] is True and same_result["rerun"]["metrics_identical"] is True
    assert "data_changed" not in [c["code"] for c in same_result["caveats"]]

    restated = bars.copy()
    restated[["open", "high", "low", "close"]] = (restated[["open", "high", "low", "close"]] * 0.97).round(2)
    cache_bars(env, "SHA:600000", restated)
    changed = client.post(f"/api/backtest/history/{first['run_id']}/rerun", json={"save": True})
    changed_result = wait_run(client, changed.json()["run_id"])["result"]
    caveat = next(c for c in changed_result["caveats"] if c["code"] == "data_changed")
    assert caveat["level"] == "warning"
    assert changed_result["rerun"]["same_data"] is False
    assert client.get(f"/api/backtest/history/{changed_result['run_id']}").json()["rerun_of"] == first["run_id"]

    assert client.post("/api/backtest/history/nope/rerun").status_code == 404
    assert client.post(f"/api/backtest/history/{first['run_id']}/rerun", json={"save": "yes"}).status_code == 422


def test_legacy_rows_cannot_be_rerun(client, env):
    save_history_run(env.history, run_id="run-a")
    response = client.post("/api/backtest/history/run-a/rerun")
    assert response.status_code == 422 and "旧版本" in response.json()["detail"]


@pytest.mark.parametrize("ticker,line", [
    ("600519", "SHA:600519 · A股 · CNY · 每手 100 股"),
    ("0700.HK", "HKG:00700 · 港股 · HKD · 每手按 100 股（请核对）"),
    ("NASDAQ:AAPL", "US:AAPL · 美股 · USD · 按 1 股交易"),
    ("510300", "SHA:510300 · A股 · CNY · 每手 100 股"),
])
def test_defaults_per_market(client, env, ticker, line):
    response = client.get("/api/backtest/defaults", params={"ticker": ticker})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["line_zh"] == line
    assert body["capital"] == 100_000 and body["slippage_pct"] == 0.0005 and body["period"] == "3y"
    assert "滑点 0.05%，次日开盘成交，前复权，对比" in body["assumptions_zh"]


def test_defaults_report_local_data_and_etf_taxes(client, env):
    cache_bars(env, "600519", make_bars(), adjust="")
    body = client.get("/api/backtest/defaults", params={"ticker": "SHA:600519"}).json()
    assert body["local_data"] == {"none": {"first": "2024-01-01", "last": "2024-02-09", "rows": 30}}
    etf = client.get("/api/backtest/defaults", params={"ticker": "ETF:510300"}).json()
    assert etf["taxes"] == []                       # ETFs pay neither stamp duty nor transfer fee


@pytest.mark.parametrize("params,status", [
    ({}, 422), ({"ticker": ""}, 422), ({"ticker": "XYZ.QQ"}, 422), ({"ticker": "IDX:000300"}, 422),
    ({"ticker": "000001.SH"}, 422), ({"ticker": "830799"}, 422),
])
def test_defaults_errors_are_chinese_4xx(client, env, params, status):
    response = client.get("/api/backtest/defaults", params=params)
    assert response.status_code == status and CHINESE.search(response.json()["detail"])


def fake_presets_module(calls):
    module = types.ModuleType("backtesting.presets")
    module.describe = lambda: [{"id": "trend", "name": "均线趋势", "summary": "站上均线买入",
                                "params": [{"key": "period", "label": "均线天数", "unit": "日", "default": 10,
                                            "min": 2, "max": 250, "step": 1}]}]

    def build(preset_id, params):
        calls.append((preset_id, params))
        if preset_id != "trend":
            raise ValueError(f"没有这个方法: {preset_id!r}")
        period = params.get("period", 10)
        if not isinstance(period, int) or not 2 <= period <= 250:
            raise ValueError("「均线天数」应在 2 到 250 之间")
        return TrendStrategy, {"period": period}

    module.build = build
    return module


def test_presets_route_with_a_presets_module(client, env, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "backtesting.presets", fake_presets_module(calls))
    bars = wavy_bars()
    cache_bars(env, "SHA:600000", bars)

    listing = client.get("/api/backtest/presets").json()
    assert listing["available"] is True and [p["id"] for p in listing["presets"]] == ["trend"]

    body = {k: v for k, v in spec_for(bars).items() if k != "strategy"}
    done = start_and_wait(client, {**body, "preset": "trend", "params": {"period": 12}})
    assert done["status"] == "completed"
    assert done["result"]["facts"]["strategy"]["name"] == "均线趋势"
    assert done["result"]["manifest"]["spec"]["preset"] == {"id": "trend", "params": {"period": 12}}
    assert client.get("/api/backtest/history").json()["runs"][0]["strategy_name"] == "均线趋势"
    bad = client.post("/api/backtest/runs", json={**body, "preset": "trend", "params": {"period": 1}})
    assert bad.status_code == 422 and "均线天数" in bad.json()["detail"]


def test_presets_route_without_a_presets_module(client, env, monkeypatch):
    monkeypatch.setitem(sys.modules, "backtesting.presets", None)

    listing = client.get("/api/backtest/presets")
    assert listing.status_code == 200
    assert listing.json()["available"] is False and listing.json()["presets"] == []
    assert CHINESE.search(listing.json()["detail"])
    run = client.post("/api/backtest/runs", json={"ticker": "600000", "preset": "trend"})
    assert run.status_code == 503 and CHINESE.search(run.json()["detail"])


def test_the_history_tab_field_names_are_served(client, env, monkeypatch):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)
    run = client.post("/api/backtest/run-sync", json=run_payload("600000", bars)).json()
    client.post(f"/api/backtest/history/{run['run_id']}/star")
    env.history.update_reflection(run["run_id"], {"analysis": "ok", "lessons": ["a lesson"]})

    listing = client.get("/api/backtest/history").json()
    assert listing["runs"][0]["primary_symbol"] == "600000" and listing["runs"][0]["ticker"] == "SHA:600000"
    assert listing["stats"]["starred_count"] == 1
    detail = client.get(f"/api/backtest/history/{run['run_id']}").json()
    assert detail["primary_symbol"] == "600000"
    assert detail["equity_curve"] == run["equity_curve"] and detail["reflection"]["analysis"] == "ok"
    [lesson] = client.get("/api/backtest/history/lessons").json()["lessons"]
    assert lesson["primary_symbol"] == "600000" and lesson["run_date"] == run["run_date"]
    compared = client.post("/api/backtest/history/compare", json={"run_ids": [run["run_id"]]}).json()
    assert compared["comparison"][0]["primary_symbol"] == "600000"


def test_legacy_result_keys_are_unchanged_and_new_ones_are_added(client, env, monkeypatch):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)

    body = client.post("/api/backtest/run-sync", json=run_payload("600000", bars)).json()

    assert OLD_RESULT_KEYS <= set(body)
    assert {"ticker", "headline_zh", "benchmark", "manifest", "coverage", "reflection_status"} <= set(body)
    assert body["primary_symbol"] == "600000" and body["ticker"] == "SHA:600000"
    # The old keys, plus what each row is (an end-of-data valuation is not a sale).
    assert set(body["trade_records"][0]) == {"date", "symbol", "direction", "quantity", "price", "pnl",
                                             "commission", "reason", "reason_zh", "end_mark", "blocked",
                                             "blocked_quantity", "blocked_zh"}
    assert set(body["equity_curve"][0]) == {"date", "net_value", "drawdown"}
    assert len(body["equity_curve"]) == len(bars) - 1         # every day after the warm-up bar
    assert body["benchmark"]["index"] is None                  # legacy runs compare with no index by default
    assert body["start_date"] == str(bars.index[0].date())

    # The async route shares the registry: submitted -> status -> result.
    submitted = client.post("/api/backtest/run", json=run_payload("600000", bars)).json()
    assert submitted["status"] == "submitted"
    task = backtest.SERVICE.registry.get(submitted["run_id"])
    assert task.done_event.wait(20)
    status = client.get(f"/api/backtest/status/{submitted['run_id']}").json()
    assert status["status"] == "completed" and status["has_result"] is True and status["progress"] == "完成"
    result = client.get(f"/api/backtest/result/{submitted['run_id']}").json()
    assert OLD_RESULT_KEYS <= set(result) and result["total_return"] == body["total_return"]


def test_reflection_is_off_by_default_and_runs_on_request(client, env, monkeypatch):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)
    calls = []
    original = BacktestReflector.reflect_facts

    async def counting(self, payload):
        calls.append(payload["run_id"])
        return await original(self, payload)

    monkeypatch.setattr(BacktestReflector, "reflect_facts", counting)
    payload = run_payload("600000", bars)
    del payload["with_reflection"]

    body = client.post("/api/backtest/run-sync", json=payload).json()
    assert body["reflection"] is None and body["reflection_status"] == "none"
    snapshot = client.get(f"/api/backtest/runs/{body['run_id']}").json()
    assert snapshot["reflection"]["status"] == "none"
    time.sleep(0.1)
    assert calls == []

    started = client.post(f"/api/backtest/runs/{body['run_id']}/reflect")
    assert started.status_code == 202 and started.json()["reflection"]["status"] in ("pending", "completed")
    reflection = wait_for_reflection(body["run_id"])
    assert reflection["status"] == "completed" and reflection["result"]["title"] == "AI 解读"
    assert calls == [body["run_id"]]
    assert client.get(f"/api/backtest/history/{body['run_id']}").json()["reflection"]["source"] == "rules"
    assert client.post("/api/backtest/runs/missing/reflect").status_code == 404


# ------------------------------------------------------------------ #
#  No 500 from user input
# ------------------------------------------------------------------ #

def new_route_bodies(bars):
    good = spec_for(bars)
    return [
        None, [], "text", {}, {"ticker": "600000"},
        {**good, "ticker": ""}, {**good, "ticker": "XYZ.QQ"}, {**good, "ticker": "IDX:000300"},
        {**good, "ticker": 600000}, {**good, "strategy": "nope"}, {**good, "strategy": None},
        {**good, "preset": "x"}, {**good, "params": {"period": "ten"}}, {**good, "params": {"x": 1}},
        {**good, "params": []}, {**good, "period": "2y"}, {**good, "period": "3y"},
        {**good, "start_date": "2024-02-30"}, {**good, "start_date": "20240101"},
        {**good, "start_date": good["end_date"]}, {**good, "start_date": "1980-01-01"},
        {**good, "capital": 0}, {**good, "capital": -5}, {**good, "capital": "lots"}, {**good, "capital": 1e20},
        {**good, "lot_size": 0}, {**good, "lot_size": -100}, {**good, "lot_size": 2.5},
        {**good, "commission_rate": 0.5}, {**good, "commission_buy": -0.001}, {**good, "min_commission": -1},
        {**good, "slippage_pct": 1}, {**good, "adjust": "forward"}, {**good, "benchmark": "SPY"},
        {**good, "warmup": -1}, {**good, "warmup": 10_000}, {**good, "allow_unadjusted": "maybe"},
        {**good, "tags": "a,b"}, {**good, "unknown_field": 1}, {**good, "notes": "x" * 5000},
    ]


def legacy_bodies(bars):
    good = run_payload("600000", bars)
    return [
        {**good, "lot_size": 0}, {**good, "lot_size": -1}, {**good, "warmup": -1},
        {**good, "symbol": ""}, {**good, "symbol": "XYZ.QQ"}, {**good, "symbol": "IDX:000300"},
        {**good, "strategy": "nope"}, {**good, "start_date": "2024-02-30"}, {**good, "end_date": "soon"},
        {**good, "start_date": good["end_date"], "end_date": good["start_date"]},
        {**good, "adjust": "forward"}, {**good, "initial_cash": 0}, {**good, "initial_cash": -1},
        {**good, "commission_buy": 2}, {**good, "commission_sell": -0.1}, {**good, "min_commission": -5},
        {**good, "slippage_pct": 5}, {**good, "params": {"position_pct": "half"}},
        {**good, "benchmark": "SPY"}, {**good, "symbol": "601999"},
    ]


def test_user_input_never_gives_a_500(client, env, monkeypatch):
    bars = wavy_bars()
    cache_bars(env, "600000", bars)
    use_strategies(monkeypatch)

    for body in new_route_bodies(bars):
        response = client.post("/api/backtest/runs", json=body)
        assert 400 <= response.status_code < 500, (body, response.status_code, response.text)
        detail = response.json()["detail"]
        assert isinstance(detail, str) and CHINESE.search(detail), (body, detail)
    bad_json = client.post("/api/backtest/runs", content=b"{not json", headers={"content-type": "application/json"})
    assert bad_json.status_code == 422 and CHINESE.search(bad_json.json()["detail"])

    for body in legacy_bodies(bars):
        response = client.post("/api/backtest/run-sync", json=body)
        assert 400 <= response.status_code < 500, (body, response.status_code, response.text)
        assert response.status_code == 422 or CHINESE.search(str(response.json()["detail"]))
    assert client.get("/api/backtest/history").json()["runs"] == []

    gbk = client.post("/api/backtest/data/import-csv", data={"symbol": "600000"},
                      files={"file": ("bars.csv", "日期,开盘,最高\n2024-01-02,10,11\n".encode("gbk"), "text/csv")})
    assert gbk.status_code == 400 and "UTF-8" in gbk.json()["detail"]
    download = client.post("/api/backtest/data/download", json={
        "symbol": "XYZ.QQ", "start_date": "2024-01-01", "end_date": "2024-02-01"})
    assert download.status_code == 422


def test_a_period_preset_counts_back_from_today(client, env, monkeypatch):
    bars = wavy_bars(start="2023-01-02", periods=420)          # to 2024-08-09
    cache_bars(env, "SHA:600000", bars)
    use_strategies(monkeypatch)
    monkeypatch.setattr(backtest.SERVICE, "today", lambda: date(2024, 8, 12))

    done = start_and_wait(client, {"ticker": "SHA:600000", "strategy": "trend", "period": "1y",
                                   "benchmark": "none"})

    facts = done["result"]["facts"]
    assert facts["period"]["requested_start"] == "2023-08-12" and facts["period"]["requested_end"] == "2024-08-12"
    assert facts["first_date"] == "2023-08-14" and facts["last_date"] == "2024-08-09"
    assert done["result"]["manifest"]["spec"]["period"]["end"] == "2024-08-09"


# ------------------------------------------------------------------ #
#  Preset dates and malformed values: never a 5xx (finding 12)
# ------------------------------------------------------------------ #

def preset_body(bars, preset="stop_target", params=None, **overrides):
    body = {"ticker": "600000", "preset": preset, "params": params or {}, "period": "custom",
            "start_date": "2023-06-01", "end_date": str(bars.index[-1].date()), "benchmark": "none"}
    body.update(overrides)
    return body


@pytest.mark.parametrize("entry", ["2024-1-2", "2024-01-02T10:00:00"])
def test_a_preset_date_is_used_as_the_preset_normalised_it(client, env, entry):
    bars = wavy_bars()
    cache_bars(env, "SHA:600000", bars)

    done = start_and_wait(client, preset_body(bars, params={"entry_date": entry}))

    assert done["status"] == "completed", done["error"]
    result = done["result"]
    assert result["manifest"]["spec"]["strategy_kwargs"]["spec"]["entry_date"] == "2024-01-02"
    assert result["fills"][0]["date"] == "2024-01-03"          # the 01-02 signal fills at the next open


MALFORMED = [
    None, "", " ", "x", "today", "1/2/2024", "2024-1-2", "2024-01-02T10:00:00", "2024-02-30", "0001-01-01",
    "9999-12-31", "１２", "\u0000", -1, 0, 1.5, 10 ** 30, 1e308, -1e308, float("nan"), float("inf"),
    True, False, [], [1], {}, {"a": 1}, "x" * 3000,
]
RUNSPEC_FIELDS = ["ticker", "preset", "strategy", "params", "period", "start_date", "end_date", "capital",
                  "commission_rate", "commission_buy", "commission_sell", "min_commission", "slippage_pct",
                  "lot_size", "adjust", "allow_unadjusted", "benchmark", "warmup", "tags", "notes", "save"]


def post_raw(client, body):
    # json.dumps writes NaN / Infinity, which Python's json.loads on the server accepts.
    return client.post("/api/backtest/runs", content=json.dumps(body),
                       headers={"content-type": "application/json"})


def assert_not_a_server_error(client, body):
    response = post_raw(client, body)
    assert response.status_code < 500, (body, response.status_code, response.text[:300])
    if response.status_code == 202:
        wait_run(client, response.json()["run_id"])            # keep the registry free
        return
    payload = response.json()
    assert isinstance(payload["detail"], str) and CHINESE.search(payload["detail"]), (body, payload)
    assert isinstance(payload.get("errors"), list), (body, payload)


def test_every_runspec_field_with_a_malformed_value_is_a_chinese_4xx(client, env, monkeypatch):
    bars = wavy_bars()
    cache_bars(env, "SHA:600000", bars)
    use_strategies(monkeypatch)
    good = spec_for(bars)
    for field in RUNSPEC_FIELDS:
        for value in MALFORMED:
            assert_not_a_server_error(client, {**good, field: value})


def test_every_preset_field_with_a_malformed_value_is_a_chinese_4xx(client, env):
    from backtesting import presets
    bars = wavy_bars()
    cache_bars(env, "SHA:600000", bars)
    for preset in presets.describe():
        for param in preset["params"]:
            for value in MALFORMED:
                assert_not_a_server_error(client, preset_body(bars, preset["id"], {param["key"]: value}))


# ------------------------------------------------------------------ #
#  Reruns keep the strategy (finding 1)
# ------------------------------------------------------------------ #

BUYING = """
from backtesting.strategies.base import Strategy


class Mine(Strategy):
    name = "Mine"
    parameters = {"fraction": 0.5}

    def on_bar(self):
        if not self.get_position() and not self.has_pending_order():
            self.buy(pct_cash=self.parameters["fraction"])
"""
NO_OP = BUYING.replace("        if not self.get_position() and not self.has_pending_order():\n"
                       "            self.buy(pct_cash=self.parameters[\"fraction\"])\n", "        pass\n")


def test_a_custom_strategy_rerun_reports_a_changed_file(client, env, monkeypatch):
    monkeypatch.setenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", "1")
    bars = wavy_bars()
    cache_bars(env, "SHA:600000", bars)
    assert client.post("/api/backtest/strategies/upload", json={"filename": "mine", "code": BUYING}).status_code == 200
    first = start_and_wait(client, spec_for(bars, strategy="custom:mine"))["result"]
    definition = first["manifest"]["spec"]["strategy_definition"]
    assert definition["source"] == BUYING and first["facts"]["fills"] >= 1

    assert NO_OP != BUYING
    assert client.post("/api/backtest/strategies/upload", json={"filename": "mine", "code": NO_OP}).status_code == 200
    rerun = client.post(f"/api/backtest/history/{first['run_id']}/rerun")
    again = wait_run(client, rerun.json()["run_id"])["result"]

    assert again["facts"]["fills"] == 0
    caveat = next(c for c in again["caveats"] if c["code"] == "strategy_changed")
    assert caveat["level"] == "warning" and again["rerun"]["same_strategy"] is False


# ------------------------------------------------------------------ #
#  Legacy fee fields (finding 6)
# ------------------------------------------------------------------ #

class BuyThenSell(Strategy):
    """Buys 1,000 shares on 2024-01-02's close (fills 01-03), sells on 01-04's (fills 01-05)."""
    name = "BuyThenSell"

    def on_bar(self):
        day = pd.Timestamp(self.data.current_bar.date).strftime("%Y-%m-%d")
        if day == "2024-01-02":
            self.buy(quantity=1000)
        elif day == "2024-01-04":
            self.close_position()


def sale_of_1000_at_100(client, env, monkeypatch, **fees):
    bars = make_bars(start="2024-01-02", periods=8, price=100.0, step=0.0)
    cache_bars(env, "600000", bars)
    monkeypatch.setattr(backtest, "_get_strategy_registry",
                        lambda: {"buy_then_sell": {"cls": BuyThenSell, "builtin": True}})
    response = client.post("/api/backtest/run-sync", json=run_payload(
        "600000", bars, strategy="buy_then_sell", initial_cash=200_000, slippage_pct=0.0, warmup=0, **fees))
    assert response.status_code == 200, response.text
    body = response.json()
    (sale,) = [t for t in body["trade_records"] if t["direction"] == "sell" and t["date"].startswith("2024-01-05")]
    return sale, body


def test_a_commission_only_request_is_charged_as_sent(client, env, monkeypatch):
    sale, body = sale_of_1000_at_100(client, env, monkeypatch, commission_buy=0.0003, commission_sell=0.0013,
                                     min_commission=5.0, fee_basis="commission_only")
    assert sale["commission"] == pytest.approx(181.0)               # 130 + 50 + 1, as asked
    assert "legacy_fees_translated" not in [c["code"] for c in body["caveats"]]


def test_a_legacy_request_without_commissions_uses_the_market_default(client, env, monkeypatch):
    sale, body = sale_of_1000_at_100(client, env, monkeypatch)
    assert sale["commission"] == pytest.approx(25.0 + 50.0 + 1.0)   # default 0.025%
    assert "legacy_fees_translated" not in [c["code"] for c in body["caveats"]]


class BuyFirstSellThird(Strategy):
    """Buys 1,000 shares on the first bar's close (fills on the second), sells on the third's (fills on the fourth)."""
    name = "BuyFirstSellThird"

    def on_bar(self):
        if self.data.current_index == 0:
            self.buy(quantity=1000)
        elif self.data.current_index == 2:
            self.close_position()


def round_trip_of_1000_at_100(client, env, monkeypatch, symbol, cache_key, start, **fees):
    bars = make_bars(start=start, periods=6, price=100.0, step=0.0)
    cache_bars(env, cache_key, bars)
    monkeypatch.setattr(backtest, "_get_strategy_registry",
                        lambda: {"buy_first_sell_third": {"cls": BuyFirstSellThird, "builtin": True}})
    response = client.post("/api/backtest/run-sync", json=run_payload(
        symbol, bars, strategy="buy_first_sell_third", initial_cash=300_000, slippage_pct=0.0, warmup=0, **fees))
    assert response.status_code == 200, response.text
    body = response.json()
    (buy,) = [t for t in body["trade_records"] if t["direction"] == "buy"]
    (sale,) = [t for t in body["trade_records"] if t["direction"] == "sell"]
    return buy, sale, body


# cr_r3b (A) service.py:1623: an old client's inclusive sell rate is charged exactly as v3.2 charged
# it (the rate as sent, no stamp duty on top) plus only the fees v3.2 did not know, which the
# dated schedule lists separately. 1,000 shares at 100 (100,000) with buy 0.03% / sell 0.13%:
#   CN 2024-01-05  sell 130 + transfer 1 = 131; buy 30 + transfer 1 = 31
#   HK 2024-01-05  sell 130 + trading fee 5.65 + SFC 2.70 + AFRC 0.15 = 138.50;
#                  buy 30 + stamp 100 + 8.50 = 138.50
#   HK 2022-06     (stamp duty alone was 0.13%) sell 130 + trading fee 5.00 + SFC 2.70 + AFRC 0.15
#                  = 137.85, not 30 + 130 + 7.85; buy 30 + stamp 130 + 7.85 = 167.85
#   US 2024-01-05  sell 130 + SEC 0.80 = 130.80; buy 30
@pytest.mark.parametrize("symbol,cache_key,start,buy_total,sell_total,translated", [
    ("600000", "600000", "2024-01-02", 31.0, 131.0, True),
    ("00700", "HKG:00700", "2024-01-02", 138.5, 138.5, True),
    ("00700", "HKG:00700", "2022-06-06", 167.85, 137.85, True),
    ("AAPL", "US:AAPL", "2024-01-02", 30.0, 130.8, False),
])
def test_an_old_clients_inclusive_sell_rate_is_charged_as_v3_2_charged_it(
        client, env, monkeypatch, symbol, cache_key, start, buy_total, sell_total, translated):
    buy, sale, body = round_trip_of_1000_at_100(client, env, monkeypatch, symbol, cache_key, start,
                                                commission_buy=0.0003, commission_sell=0.0013, min_commission=5.0)

    assert buy["commission"] == pytest.approx(buy_total, abs=0.001)
    assert sale["commission"] == pytest.approx(sell_total, abs=0.001)
    codes = {c["code"]: c for c in body["caveats"]}
    legacy = body["manifest"]["spec"]["legacy_fees"]
    if translated:
        assert legacy["basis"] == "legacy_inclusive" and legacy["commission_sell_sent"] == 0.0013
        assert legacy["commission_sell_used"] == 0.0013 and legacy["included_in_sell_rate"] == ["stamp_duty"]
        text = codes["legacy_fees_translated"]["text"]
        assert "0.13%" in text and "印花税" in text and "fee_basis" in text
    else:                                       # no stamp duty to fold in: the rate means the same
        assert legacy is None and "legacy_fees_translated" not in codes


def test_polling_a_runs_reading_can_leave_out_the_result(client, env, monkeypatch):
    bars = make_bars()
    cache_bars(env, "600000", bars)
    use_buy_once_strategy(monkeypatch)
    body = client.post("/api/backtest/run-sync", json=run_payload("600000", bars, with_reflection=True)).json()
    assert body["reflection_status"] in ("pending", "completed")

    light = client.get(f"/api/backtest/runs/{body['run_id']}", params={"include_result": "false"}).json()

    assert light["result"] is None and light["status"] == "completed"
    assert light["reflection"]["status"] in ("pending", "completed")
    full = client.get(f"/api/backtest/runs/{body['run_id']}").json()
    assert full["result"]["run_id"] == body["run_id"]
    wait_for_reflection(body["run_id"])
