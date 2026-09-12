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
