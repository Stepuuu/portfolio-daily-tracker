"""The history table must keep the risk metrics the engine computed."""
import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from backtesting.history import BacktestHistoryStore
from backtesting.strategies.base import Strategy


class BuyOnceStrategy(Strategy):
    name = "BuyOnce"

    def on_init(self):
        self.did_buy = False

    def on_bar(self):
        if not self.did_buy:
            self.buy(pct_cash=0.5)
            self.did_buy = True


def run_backtest():
    closes = [10.0, 10.4, 10.1, 10.6, 10.2, 10.9, 10.3, 10.8, 10.5, 11.0, 10.6, 11.2]
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
            "amount": [10_000_000] * len(closes),
        },
        index=dates,
    )
    df.index.name = "date"
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("TEST", "", "", df=df, warmup=1)
    engine.add_strategy(BuyOnceStrategy)
    return engine.run()


def test_result_dict_and_history_row_keep_risk_metrics(tmp_path):
    result = run_backtest()
    stats = result.stats
    assert stats.volatility > 0 and stats.var_95 < 0 and stats.max_drawdown_duration > 0

    payload = result.to_dict()
    assert payload["volatility"] == pytest.approx(stats.volatility, abs=1e-6)
    assert payload["var_95"] == pytest.approx(stats.var_95, abs=1e-6)
    assert payload["max_drawdown_duration"] == stats.max_drawdown_duration

    store = BacktestHistoryStore(str(tmp_path / "history.sqlite"))
    store.save_run(result_dict=payload)
    row = store.get_run(result.run_id)

    assert row["volatility"] == pytest.approx(stats.volatility, abs=1e-6)
    assert row["var_95"] == pytest.approx(stats.var_95, abs=1e-6)
    assert row["max_drawdown_duration"] == stats.max_drawdown_duration


# ------------------------------------------------------------------ #
#  Additive columns, the page's field names, idempotent migrations
# ------------------------------------------------------------------ #

OLD_SCHEMA = """
CREATE TABLE backtest_runs (
    run_id TEXT PRIMARY KEY, run_date TEXT NOT NULL, strategy_name TEXT NOT NULL, strategy_class TEXT,
    strategy_params TEXT, symbol TEXT NOT NULL, start_date TEXT, end_date TEXT, initial_cash REAL,
    final_value REAL, total_return REAL, annualized_return REAL, benchmark_return REAL, alpha REAL,
    sharpe_ratio REAL, sortino_ratio REAL, calmar_ratio REAL, max_drawdown REAL,
    max_drawdown_duration INTEGER, volatility REAL, var_95 REAL, total_trades INTEGER, win_rate REAL,
    profit_factor REAL, avg_profit REAL, avg_loss REAL, max_single_profit REAL, max_single_loss REAL,
    commission_buy REAL, commission_sell REAL, slippage_pct REAL, reflection_text TEXT,
    equity_curve_json TEXT, summary_text TEXT, tags TEXT DEFAULT '[]', notes TEXT DEFAULT '',
    starred INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE backtest_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, trade_date TEXT,
    symbol TEXT, direction TEXT, quantity REAL, price REAL, commission REAL, pnl REAL);
CREATE TABLE backtest_lessons (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    lesson_type TEXT DEFAULT 'backtest', description TEXT, lesson TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')));
INSERT INTO backtest_runs (run_id, run_date, strategy_name, symbol, total_return, starred, equity_curve_json,
    reflection_text)
VALUES ('old-run', '2024-03-01 10:00:00', 'SmaCross', '600519', 0.1, 1, '[{"date": "2024-01-02", "net_value": 1.0}]',
    '{"analysis": "old"}');
INSERT INTO backtest_lessons (run_id, lesson) VALUES ('old-run', 'an old lesson');
"""


def test_an_old_database_gains_the_new_columns_and_keeps_its_rows(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)

    for _ in range(2):                                   # opening twice is a no-op the second time
        store = BacktestHistoryStore(str(path))
    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)")]
    assert columns.count("manifest_json") == 1 and "data_sha256" in columns and "engine_version" in columns

    old = store.get_run("old-run")
    assert old["primary_symbol"] == "600519" and old["manifest"] is None and old["result"] is None
    assert old["equity_curve"] == [{"date": "2024-01-02", "net_value": 1.0}]
    assert old["reflection"] == {"analysis": "old"}
    [row] = store.list_runs()
    assert row["primary_symbol"] == "600519" and row["ticker"] is None
    assert store.get_stats()["starred_count"] == 1 == store.get_stats()["starred_runs"]
    [lesson] = store.get_all_lessons()
    assert lesson["primary_symbol"] == "600519" and lesson["run_date"] == "2024-03-01 10:00:00"


def test_a_saved_run_keeps_its_manifest_full_equity_and_canonical_ticker(tmp_path):
    store = BacktestHistoryStore(str(tmp_path / "history.sqlite"))
    equity = [{"date": str(day.date()), "net_value": 1.0 + i / 1000, "drawdown": 0.0}
              for i, day in enumerate(pd.bdate_range("2023-01-02", periods=750))]
    manifest = {"engine_version": "2.1.0", "data_sha256": "ab" * 32, "fee_schedule_version": "2026-11",
                "spec": {"ticker": "SHA:600519", "adjust": "qfq", "warmup": 60, "market": "CN",
                         "period": {"preset": "3y"}, "preset": {"id": "ma_trend", "params": {"period": 20}}}}
    store.save_run(
        result_dict={"run_id": "r1", "strategy_name": "均线趋势", "primary_symbol": "600519", "ticker": "SHA:600519",
                     "closed_trades": 3, "profit_factor": float("inf"), "caveats": [{"code": "in_sample"}]},
        equity_curve=equity, manifest=manifest,
        result={"headline_zh": "一句话", "facts": {"currency": "CNY", "source": "akshare.stock_zh_a_hist"},
                "metrics": {"profit_factor": float("nan")}},
        broker_config={"commission_buy": None, "min_commission": 5.0, "slippage_pct": 0.0005, "lot_size": None},
    )

    run = store.get_run("r1")
    assert len(run["equity_curve"]) == 750                     # every day, no thinning
    assert run["ticker"] == "SHA:600519" and run["adjust"] == "qfq" and run["warmup"] == 60
    assert run["data_sha256"] == "ab" * 32 and run["engine_version"] == "2.1.0"
    assert run["manifest"] == manifest and run["preset"] == "ma_trend" and run["period_preset"] == "3y"
    assert run["result"]["metrics"]["profit_factor"] is None   # stored as valid JSON
    assert run["fees"]["min_commission"] == 5.0 and run["fees"]["slippage_pct"] == 0.0005
    assert run["headline_zh"] == "一句话" and run["caveats"] == [{"code": "in_sample"}]
    # The canonical ticker finds the run saved under the bare code, and so does the bare code.
    assert [r["run_id"] for r in store.list_runs(symbol="SHA:600519")] == ["r1"]
    assert [r["run_id"] for r in store.list_runs(symbol="600519")] == ["r1"]


def test_a_later_reflection_replaces_the_lessons(tmp_path):
    store = BacktestHistoryStore(str(tmp_path / "history.sqlite"))
    store.save_run(result_dict={"run_id": "r1", "strategy_name": "S", "primary_symbol": "600519"},
                   reflection={"analysis": "first", "lessons": ["one"]})

    assert store.update_reflection("r1", {"analysis": "AI 解读", "lessons": ["two", "three"]}) is True
    assert store.update_reflection("missing", {"analysis": "x"}) is False

    run = store.get_run("r1")
    assert run["reflection"]["analysis"] == "AI 解读"
    assert [lesson["lesson"] for lesson in run["lessons"]] == ["two", "three"]
