"""Third review, part 2: the CSV upload (backend/api/backtest.py:520) follows the
provisional-bar rule, and importing the API writes nothing into the source tree."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backtesting.data.store as store_module
import backtesting.engine as engine_module
import backtesting.history as history_module
from backend.api import backtest
from backtesting.data.loader import DataLoader
from backtesting.strategies.base import Strategy

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[2]


class BuyOnce(Strategy):
    name = "BuyOnce"

    def on_init(self):
        self.done = False

    def on_bar(self):
        if not self.done:
            self.buy(pct_cash=0.5)
            self.done = True


def january(last_close):
    """Jan 2-31, 2024 at 10, the last row (Jan 31) at `last_close`."""
    days = pd.bdate_range("2024-01-02", "2024-01-31")
    closes = [10.0] * (len(days) - 1) + [last_close]
    return pd.DataFrame({"date": days, "open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": 1_000_000.0, "amount": [c * 1_000_000.0 for c in closes]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    bars_db, history_db = str(tmp_path / "bars.sqlite"), str(tmp_path / "history.sqlite")

    class TmpDataStore(store_module.DataStore):
        def __init__(self, db_path=None):
            super().__init__(bars_db)

    class TmpHistoryStore(history_module.BacktestHistoryStore):
        def __init__(self, db_path=None):
            super().__init__(history_db)

    def no_network(self, *args, **kwargs):
        raise ConnectionError("offline test")

    monkeypatch.setattr(store_module, "DataStore", TmpDataStore)
    monkeypatch.setattr(engine_module, "DataStore", TmpDataStore)
    monkeypatch.setattr(history_module, "BacktestHistoryStore", TmpHistoryStore)
    monkeypatch.setattr(DataLoader, "get_daily", no_network)
    monkeypatch.setattr(backtest, "_get_strategy_registry", lambda: {"buy_once": {"cls": BuyOnce, "builtin": True}})
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    backtest.SERVICE.registry.clear()
    app = FastAPI()
    app.include_router(backtest.router)
    return TestClient(app), TmpDataStore()


def upload(client, df, at, monkeypatch):
    """Upload `df` as a CSV with the store's clock at `at` (an upload has no fetch time:
    the moment it arrives decides which of its rows can be final)."""
    with monkeypatch.context() as patch:
        patch.setattr(store_module, "_current_time", lambda: at)
        return client.post("/api/backtest/data/import-csv",
                           files={"file": ("bars.csv", df.to_csv(index=False).encode("utf-8"), "text/csv")},
                           data={"symbol": "600000", "adjust": ""})


def run(client):
    return client.post("/api/backtest/run-sync", json={
        "symbol": "600000", "start_date": "2024-01-02", "end_date": "2024-01-31", "strategy": "buy_once",
        "warmup": 0, "with_reflection": False, "adjust": ""})


def test_a_morning_csv_does_not_import_todays_row_as_final(env, monkeypatch):
    # Reviewer's reproduction: a CSV uploaded at 10:00 on Jan 31 ended with today's
    # provisional close 11; after the close the run used it as Jan 31's final bar.
    client, store = env
    response = upload(client, january(11.0), datetime_at("2024-01-31 10:00"), monkeypatch)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows_imported"] == len(january(11.0)) - 1
    assert body["provisional_dates"] == ["2024-01-31"]
    assert [(s, e) for s, e, _ in store.get_coverage("600000", "")] == [("2024-01-02", "2024-01-30")]
    assert store.load_daily("600000", "2024-01-01", "2024-12-31", "")["close"].tolist()[-1] == 10.0

    # After the close (the real clock is long past it): Jan 31 is not in the cache as
    # final; the download fails offline, so the run says it used Jan 2-30 only.
    result = run(client)
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["period"]["last_date"] == "2024-01-30"
    assert payload["coverage"]["600000"]["offline"] is True


def test_a_csv_uploaded_after_the_close_keeps_todays_row(env, monkeypatch):
    client, store = env
    response = upload(client, january(15.0), datetime_at("2024-01-31 16:00"), monkeypatch)
    assert response.status_code == 200, response.text
    assert response.json()["rows_imported"] == len(january(15.0))
    assert response.json()["provisional_dates"] == []
    assert [(s, e) for s, e, _ in store.get_coverage("600000", "")] == [("2024-01-02", "2024-01-31")]


def datetime_at(text):
    return pd.Timestamp(text).to_pydatetime().replace(tzinfo=SHANGHAI)


# ------------------------------------------------------------------ #
#  Importing the API writes nothing into the source tree
# ------------------------------------------------------------------ #

PROBE = textwrap.dedent('''
    import os, pathlib, sys
    root = pathlib.Path(sys.argv[1]).resolve()
    attempts = []

    def guard(name):
        original = getattr(pathlib.Path, name)

        def call(self, *args, **kwargs):
            if pathlib.Path(os.path.abspath(self)).is_relative_to(root):
                attempts.append((name, str(self)))
                raise PermissionError("read-only checkout: " + str(self))
            return original(self, *args, **kwargs)
        setattr(pathlib.Path, name, call)

    for name in ("touch", "mkdir", "write_text", "write_bytes", "unlink", "rename", "replace", "chmod"):
        guard(name)
    import backend.api.backtest  # noqa: F401
    print("ATTEMPTS", attempts)
''')


@pytest.mark.parametrize("demo", ["0", "1"], ids=["normal", "demo"])
def test_importing_the_api_writes_nothing_into_the_source_tree(tmp_path, demo):
    # Reviewer: importing the API refreshed the timestamp of
    # backtesting/strategies/custom/__init__.py. In a read-only checkout (and in demo
    # mode) importing must not even try to write there.
    custom_init = ROOT / "dashboard" / "backtesting" / "strategies" / "custom" / "__init__.py"
    before = custom_init.stat().st_mtime_ns
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    environ = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TRACKER_DEMO_MODE": demo,
               "TRACKER_BACKTEST_DIR": str(tmp_path / "bt"),
               "PYTHONPATH": os.pathsep.join([str(ROOT / "dashboard"), os.environ.get("PYTHONPATH", "")])}
    done = subprocess.run([sys.executable, str(probe), str(ROOT), demo], cwd=str(tmp_path), env=environ,
                          capture_output=True, text=True, timeout=120)

    assert done.returncode == 0, done.stderr[-3000:]
    assert done.stdout.strip().splitlines()[-1] == "ATTEMPTS []"
    assert custom_init.stat().st_mtime_ns == before
