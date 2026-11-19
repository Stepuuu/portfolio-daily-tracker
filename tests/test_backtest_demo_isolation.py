"""Demo mode: its own backtest storage, demo tickers only, synthetic bars, no downloads."""
import sys
import time

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import backtest
from backtesting.data.loader import DataLoader
from backtesting.demo import DEMO_SOURCE, DemoLoader, demo_tickers, synthetic_bars
from backtesting.history import BacktestHistoryStore
from backtesting.reflection.llm_reflector import BacktestReflector
from core.demo_seed import write_fake_quotes
from core.market.runtime import set_market_runtime

DEMO_HOLDINGS = {"date": "2026-08-28", "groups": {
    "长期账户": {"positions": [{"name": "示例宽基", "ticker": "SHA:DEMO", "quantity": 1000, "cost_price": 50}]},
    "研究账户": {"positions": [{"name": "示例研究标的", "ticker": "NASDAQ:DEMO", "quantity": 20, "cost_price": 100}]},
}}


@pytest.fixture
def demo(tmp_path, monkeypatch):
    portfolio = tmp_path / "demo-portfolio"
    portfolio.mkdir()
    write_fake_quotes(portfolio, DEMO_HOLDINGS, {"SHA:DEMO": 61.2, "NASDAQ:DEMO": 118.9}, {"CNY": 1, "USD": 7.05})
    private = tmp_path / "private"
    monkeypatch.setenv("PORTFOLIO_DIR", str(portfolio))
    monkeypatch.setenv("TRACKER_BACKTEST_DIR", str(private))
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    monkeypatch.delenv("TRACKER_ALLOW_CUSTOM_STRATEGIES", raising=False)

    # The user's own history, written before the demo starts.
    BacktestHistoryStore().save_run(result_dict={"run_id": "private-run", "strategy_name": "Mine",
                                                 "primary_symbol": "600519"})

    def no_network(self, *args, **kwargs):
        raise AssertionError("the demo must never download")

    monkeypatch.setattr(DataLoader, "get_daily", no_network)
    monkeypatch.setattr(BacktestReflector, "_get_llm_provider",
                        lambda self: pytest.fail("the demo must not call an LLM") if self._use_llm else None)
    monkeypatch.setenv("TRACKER_DEMO_MODE", "1")
    set_market_runtime(None)
    backtest.SERVICE.registry.clear()
    yield {"portfolio": portfolio, "private": private}
    backtest.SERVICE.registry.clear()
    set_market_runtime(None)


@pytest.fixture
def client(demo):
    app = FastAPI()
    app.include_router(backtest.router)
    return TestClient(app)


def wait_run(client, run_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/backtest/runs/{run_id}").json()
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        time.sleep(0.05)
    raise AssertionError("the demo run did not finish")


def test_the_demo_lists_its_own_tickers(demo):
    assert demo_tickers() == {"SHA:DEMO": {"name": "示例宽基"}, "NASDAQ:DEMO": {"name": "示例研究标的"}}


def test_synthetic_bars_are_deterministic_per_ticker():
    whole = synthetic_bars("SHA:DEMO", "2024-01-01", "2024-12-31")
    part = synthetic_bars("SHA:DEMO", "2024-06-03", "2024-06-28")
    pd.testing.assert_frame_equal(whole.loc["2024-06-03":"2024-06-28"], part)
    other = synthetic_bars("NASDAQ:DEMO", "2024-06-03", "2024-06-28")
    assert not other["close"].equals(part["close"])
    assert float(whole["close"].pct_change().abs().max()) < 0.095        # inside the A-share 10% limit
    assert (whole["high"] >= whole[["open", "close"]].max(axis=1)).all()
    with pytest.raises(RuntimeError, match="演示模式没有"):
        DemoLoader().get_daily("IDX:000300", "2024-01-01", "2024-12-31")


def test_demo_history_is_isolated_from_the_users_own(client, demo):
    listing = client.get("/api/backtest/history").json()
    assert listing["runs"] == [] and listing["stats"]["total_runs"] == 0
    assert str(demo["portfolio"] / "backtest_demo") in listing["stats"]["db_path"]
    assert client.get("/api/backtest/history/private-run").status_code == 404
    assert client.get("/api/backtest/data/symbols").json() == {"symbols": []}


def test_the_demo_backtests_its_holdings_on_labelled_synthetic_bars(client, demo):
    started = client.post("/api/backtest/runs", json={"ticker": "SHA:DEMO", "strategy": "sma_cross"})
    assert started.status_code == 202, started.text

    done = wait_run(client, started.json()["run_id"])

    assert done["status"] == "completed", done["error"]
    result = done["result"]
    assert result["facts"]["source"] == DEMO_SOURCE and result["facts"]["demo"] is True
    assert result["facts"]["name"] == "示例宽基" and result["facts"]["currency"] == "CNY"
    codes = {c["code"]: c for c in result["caveats"]}
    assert "不是真实行情" in codes["demo_data"]["text"]
    assert result["benchmark"]["index"] is None and "演示模式没有指数行情" in codes["index_unavailable"]["text"]
    assert result["manifest"]["demo"] is True and result["manifest"]["coverage"][0]["source"] == DEMO_SOURCE
    # Saved nowhere: not to the user's history, and the demo keeps none of its own.
    assert done["saved_to_history"] is False
    assert client.get("/api/backtest/history").json()["runs"] == []
    assert not (demo["portfolio"] / "backtest_demo").exists()
    private = BacktestHistoryStore(str(demo["private"] / "backtest_history.db"))
    assert [run["run_id"] for run in private.list_runs()] == ["private-run"]
    assert not (demo["private"] / "backtesting.db").exists()

    us = wait_run(client, client.post("/api/backtest/runs", json={
        "ticker": "nasdaq:demo", "strategy": "sma_cross"}).json()["run_id"])
    assert us["status"] == "completed" and us["result"]["facts"]["currency"] == "USD"

    reflect = client.post(f"/api/backtest/runs/{result['run_id']}/reflect")
    assert reflect.status_code == 202
    task = backtest.SERVICE.registry.get(result["run_id"])
    assert task.reflection_done.wait(10) and task.reflection["result"]["source"] == "rules"


@pytest.mark.parametrize("ticker", ["600519", "SHA:600519", "AAPL", "IDX:000300"])
def test_the_demo_refuses_other_tickers(client, demo, ticker):
    response = client.post("/api/backtest/runs", json={"ticker": ticker, "strategy": "sma_cross"})
    assert response.status_code == 403
    assert "SHA:DEMO" in response.json()["detail"] and "NASDAQ:DEMO" in response.json()["detail"]
    defaults = client.get("/api/backtest/defaults", params={"ticker": ticker})
    assert defaults.status_code == 403


def test_demo_defaults(client, demo):
    body = client.get("/api/backtest/defaults", params={"ticker": "SHA:DEMO"}).json()
    assert body["demo"] is True and body["name"] == "示例宽基" and body["benchmark"] is None
    assert body["line_zh"] == "示例宽基 · SHA:DEMO · A股 · CNY · 每手 100 股"
    assert body["assumptions_zh"].endswith("前复权，不对比指数")


@pytest.mark.skipif(sys.version_info < (3, 10), reason="backend.main needs Python 3.10+")
def test_the_real_application_lets_the_demo_run_backtests_and_nothing_else(demo, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRACKER_LAB_DIR", str(tmp_path / "lab"))
    from backend.main import app

    client = TestClient(app, base_url="http://127.0.0.1")
    started = client.post("/api/backtest/runs", json={"ticker": "SHA:DEMO", "strategy": "sma_cross"})
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    done = wait_run(client, run_id)
    assert done["status"] == "completed"
    assert client.post(f"/api/backtest/runs/{run_id}/cancel").status_code == 409     # reached the route
    assert client.post(f"/api/backtest/runs/{run_id}/reflect").status_code == 202
    assert client.get(f"/api/backtest/runs/{run_id}/export").status_code == 200
    for path in ("/api/backtest/run-sync", "/api/backtest/run", f"/api/backtest/history/{run_id}/star",
                 f"/api/backtest/history/{run_id}/rerun", "/api/backtest/data/download", "/api/backtest/quick-test"):
        response = client.post(path, json={})
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode is read-only"}, path
    assert client.delete(f"/api/backtest/history/{run_id}").status_code == 403
    assert client.get("/api/backtest/history").json()["runs"] == []          # the demo saves nothing


@pytest.mark.parametrize("save", [None, True, False])
def test_a_demo_run_completes_in_memory(client, demo, save):
    # cr_r3b (A) service.py:1020,1036: no bar cache, no history database, nothing under the
    # demo's data directory - whether or not the request asks to save.
    body = {"ticker": "SHA:DEMO", "strategy": "sma_cross"}
    if save is not None:
        body["save"] = save
    started = client.post("/api/backtest/runs", json=body)
    assert started.status_code == 202, started.text

    done = wait_run(client, started.json()["run_id"])

    assert done["status"] == "completed", done["error"]
    assert done["saved_to_history"] is False and done["result"]["facts"]["source"] == DEMO_SOURCE
    reflect = client.post(f"/api/backtest/runs/{done['run_id']}/reflect")
    assert reflect.status_code == 202
    assert backtest.SERVICE.registry.get(done["run_id"]).reflection_done.wait(10)
    assert client.get(f"/api/backtest/runs/{done['run_id']}/export").status_code == 200
    # Reads of the demo's history and bar cache create nothing either.
    assert client.get("/api/backtest/history").json()["runs"] == []
    assert client.get("/api/backtest/history/lessons").json() == {"lessons": []}
    assert client.get("/api/backtest/data/symbols").json() == {"symbols": []}
    assert client.get("/api/backtest/data/cache").status_code == 200
    assert not (demo["portfolio"] / "backtest_demo").exists()
    assert not (demo["private"] / "backtesting.db").exists()
