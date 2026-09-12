"""Keep all backtest tests isolated from a user's actual market cache."""
import pytest


@pytest.fixture(autouse=True)
def isolated_engine_cache(tmp_path, monkeypatch):
    from backtesting import engine
    from backtesting.data.store import DataStore
    monkeypatch.setattr(engine, "DataStore", lambda: DataStore(str(tmp_path / "bars.sqlite")))
