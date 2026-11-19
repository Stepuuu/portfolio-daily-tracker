"""Release gate 4 (cr_g4d PART 1), a history saved by v3.2.1: offline.

* history/__init__.py:114 - opening History on a v3.2.1 database migrated it on
  GET, and a read-only copy answered 500.
* The page: the list showed a v3.2.1 run, but opening and exporting it answered
  404. They open as a legacy result (read-only translation, saying what is not
  available), and nothing is written.
"""
import hashlib
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import backtest
import backtesting.history as history_module

# The v3.2.1 schema, as that release created it (no ALTER TABLE columns).
V321_SCHEMA = """
CREATE TABLE backtest_runs (
    run_id TEXT PRIMARY KEY, run_date TEXT NOT NULL, strategy_name TEXT NOT NULL, strategy_class TEXT,
    strategy_params TEXT, symbol TEXT NOT NULL, start_date TEXT, end_date TEXT, initial_cash REAL,
    final_value REAL, total_return REAL, annualized_return REAL, benchmark_return REAL, alpha REAL,
    sharpe_ratio REAL, sortino_ratio REAL, calmar_ratio REAL, max_drawdown REAL, max_drawdown_duration INTEGER,
    volatility REAL, var_95 REAL, total_trades INTEGER, win_rate REAL, profit_factor REAL, avg_profit REAL,
    avg_loss REAL, max_single_profit REAL, max_single_loss REAL, commission_buy REAL, commission_sell REAL,
    slippage_pct REAL, reflection_text TEXT, equity_curve_json TEXT, summary_text TEXT, tags TEXT DEFAULT '[]',
    notes TEXT DEFAULT '', starred INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE backtest_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, trade_date TEXT, symbol TEXT, direction TEXT,
    quantity REAL, price REAL, commission REAL, pnl REAL);
CREATE TABLE backtest_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, lesson_type TEXT DEFAULT 'backtest',
    description TEXT, lesson TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
"""
CURVE = [{"date": "2024-01-02", "net_value": 1.0, "drawdown": 0.0},
         {"date": "2024-03-01", "net_value": 1.05, "drawdown": 0.0},
         {"date": "2024-06-28", "net_value": 0.98, "drawdown": -0.0667},
         {"date": "2024-12-31", "net_value": 1.12, "drawdown": 0.0}]


def write_v321(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(V321_SCHEMA)
        conn.execute(
            "INSERT INTO backtest_runs (run_id, run_date, strategy_name, strategy_class, strategy_params, symbol, "
            "start_date, end_date, initial_cash, final_value, total_return, annualized_return, sharpe_ratio, "
            "max_drawdown, total_trades, win_rate, profit_factor, commission_buy, commission_sell, slippage_pct, "
            "equity_curve_json, summary_text, reflection_text) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("old-run", "2025-01-05T10:00:00", "sma_cross", "SmaCrossStrategy", json.dumps({"fast_period": 10}),
             "600519", "2024-01-01", "2024-12-31", 1_000_000.0, 1_120_000.0, 0.12, 0.1215, 0.0, -0.0667, 2, 0.0,
             None, 0.0003, 0.0013, 0.0002, json.dumps(CURVE), "旧摘要", json.dumps({"lessons": ["别追高"]})))
        conn.executemany(
            "INSERT INTO backtest_trades (run_id, trade_date, symbol, direction, quantity, price, commission, pnl) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [("old-run", "2024-01-03 00:00:00", "600519", "buy", 500, 1650.0, 247.5, 0.0),
             ("old-run", "2024-12-31 00:00:00", "600519", "sell", 500, 1890.0, 1228.5, 118524.0)])
        conn.execute("INSERT INTO backtest_lessons (run_id, description, lesson) VALUES "
                     "('old-run', 'sma_cross @ 600519', '别追高')")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def columns(path):
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)")}


@pytest.fixture
def old_history(tmp_path, monkeypatch):
    folder = tmp_path / "bt"
    folder.mkdir()
    monkeypatch.setenv("TRACKER_BACKTEST_DIR", str(folder))
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    path = folder / "backtest_history.db"
    write_v321(path)
    backtest.SERVICE.registry.clear()
    yield path
    backtest.SERVICE.registry.clear()


@pytest.fixture
def client(old_history):
    app = FastAPI()
    app.include_router(backtest.router)
    return TestClient(app)


@pytest.fixture
def read_only(monkeypatch):
    """Every connection the history store opens is read-only (a copy on a read-only disk)."""
    real = sqlite3.connect

    def connect(path, *args, **kwargs):
        return real(f"file:{path}?mode=ro", *args, uri=True, **kwargs)

    monkeypatch.setattr(history_module.sqlite3, "connect", connect)


READS = ["/api/backtest/history", "/api/backtest/history/old-run", "/api/backtest/history/lessons",
         "/api/backtest/history/best", "/api/backtest/runs/old-run", "/api/backtest/runs/old-run/export",
         "/api/backtest/runs/old-run/export?format=json"]


def test_reading_a_v321_history_never_migrates_it(client, old_history):
    before, schema = digest(old_history), columns(old_history)
    for url in READS:
        assert client.get(url).status_code == 200, url
    assert columns(old_history) == schema and "ticker" not in schema
    assert digest(old_history) == before


def test_a_read_only_copy_of_a_v321_history_can_be_read(client, old_history, read_only):
    for url in READS:
        answer = client.get(url)
        assert answer.status_code == 200, (url, answer.text)
    listed = client.get("/api/backtest/history").json()
    assert [run["run_id"] for run in listed["runs"]] == ["old-run"]
    assert listed["runs"][0]["ticker"] is None and listed["runs"][0]["primary_symbol"] == "600519"
    assert client.get("/api/backtest/history", params={"symbol": "600519"}).json()["runs"][0]["run_id"] == "old-run"


def test_a_v321_run_opens_as_a_legacy_result(client, old_history):
    snapshot = client.get("/api/backtest/runs/old-run").json()
    assert snapshot["status"] == "completed" and snapshot["kind"] == "saved"
    result = snapshot["result"]
    legacy = result["legacy"]
    assert legacy["saved_by"] and legacy["note_zh"]
    unavailable = {item["key"] for item in legacy["unavailable"]}
    assert {"manifest", "benchmark", "round_trips", "unfilled", "equity_daily"} <= unavailable
    assert all(item["label_zh"] for item in legacy["unavailable"])
    facts = result["facts"]
    assert facts["ticker"] == "SHA:600519" and facts["label"] == "600519" and facts["currency"] == "CNY"
    assert facts["total_return"] == 0.12 and facts["capital"] == 1_000_000.0 and facts["final_value"] == 1_120_000.0
    assert facts["max_drawdown"] == -0.0667 and facts["first_date"] == "2024-01-02" and facts["last_date"] == "2024-12-31"
    assert facts["same_stock_return"] is None and facts["index_return"] is None and facts["fills"] == 2
    # What v3.2.1 wrote as 0 when it had no value is not shown as a measured 0.
    assert result["metrics"]["sharpe_ratio"] is None and "sharpe_ratio" in result["metrics"]["undefined"]
    assert legacy["saved_metrics"]["sharpe_ratio"] == 0.0
    assert [row["date"] for row in result["equity"]] == [point["date"] for point in CURVE]
    assert result["equity"][-1]["total_value"] == pytest.approx(1_120_000.0)
    assert [(fill["date"], fill["direction"], fill["quantity"]) for fill in result["fills"]] == [
        ("2024-01-03", "buy", 500), ("2024-12-31", "sell", 500)]
    assert result["round_trips"] == [] and result["unfilled"] == []
    assert result["benchmark"] == {"same_stock": None, "index": None}
    codes = {c["code"]: c for c in result["caveats"]}
    assert codes["legacy_result"]["level"] == "warning"
    assert "旧版本" in result["headline_zh"] and "+12.0%" in result["headline_zh"]
    assert snapshot["reflection"]["status"] == "completed"
    # The run's own record still cannot be rerun: it has no manifest.
    refused = client.post("/api/backtest/history/old-run/rerun", json={})
    assert refused.status_code == 422 and "无法重跑" in refused.json()["detail"]


def test_a_v321_run_exports_with_its_legacy_mark(client, old_history):
    csv_text = client.get("/api/backtest/runs/old-run/export").text
    assert "[fills]" in csv_text and "2024-12-31" in csv_text and "legacy" in csv_text
    body = json.loads(client.get("/api/backtest/runs/old-run/export", params={"format": "json"}).text)
    assert body["legacy"]["saved_by"] and body["facts"]["total_return"] == 0.12
    fills = client.get("/api/backtest/runs/old-run/export", params={"part": "fills"}).text
    assert fills.splitlines()[0].lstrip("﻿").startswith("date,symbol,direction")


def test_a_run_that_does_not_exist_is_still_404(client):
    assert client.get("/api/backtest/runs/no-such-run").status_code == 404
