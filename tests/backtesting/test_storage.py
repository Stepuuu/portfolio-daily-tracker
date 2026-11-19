"""Both backtest databases follow TRACKER_BACKTEST_DIR; the demo always has its own."""
from pathlib import Path

from backtesting import storage
from backtesting.data.store import DataStore
from backtesting.history import BacktestHistoryStore


def test_default_location_is_unchanged(monkeypatch):
    monkeypatch.delenv("TRACKER_BACKTEST_DIR", raising=False)
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    dashboard = Path(storage.__file__).resolve().parents[1]
    assert storage.bars_db_path() == str(dashboard / "data" / "backtesting.db")
    assert storage.history_db_path() == str(dashboard / "data" / "backtest_history.db")


def test_both_databases_follow_the_environment_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    monkeypatch.setenv("TRACKER_BACKTEST_DIR", str(tmp_path / "bt"))

    assert DataStore().db_path == str(tmp_path / "bt" / "backtesting.db")
    assert BacktestHistoryStore().db_path == str(tmp_path / "bt" / "backtest_history.db")
    assert (tmp_path / "bt" / "backtesting.db").is_file()


def test_demo_mode_uses_a_directory_under_the_demo_portfolio_even_with_an_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_BACKTEST_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path / "demo-portfolio"))
    monkeypatch.setenv("TRACKER_DEMO_MODE", "1")

    expected = tmp_path / "demo-portfolio" / "backtest_demo"
    assert DataStore().db_path == str(expected / "backtesting.db")
    assert BacktestHistoryStore().db_path == str(expected / "backtest_history.db")
    assert not (tmp_path / "private").exists()
