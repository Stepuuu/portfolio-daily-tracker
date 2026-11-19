"""Release gate 4 (cr_g4d PART 1), the backtest routes the page needs: offline.

* DataTab 更新到今天 (POST /data/download): an index was saved under qfq while the
  不复权 series the comparison reads still ended Friday. The update refreshes every
  adjustment the app uses for the symbol and says per adjustment what it now holds.
"""
from datetime import datetime

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import backtest
from backtesting.data.loader import DataLoader
from backtesting.data.store import DataStore
from tests.backtesting.test_gate4_data import bars


@pytest.fixture
def bt_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_BACKTEST_DIR", str(tmp_path / "bt"))
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    # Wednesday 2026-11-18 at 18:00 Shanghai time: Wednesday has settled.
    monkeypatch.setattr(backtest, "_clock", lambda: datetime.fromisoformat("2026-11-18T18:00:00+08:00"))
    return tmp_path / "bt"


@pytest.fixture
def client(bt_dir):
    app = FastAPI()
    app.include_router(backtest.router)
    return TestClient(app)


class Provider:
    """Stands in for DataLoader.get_daily (an instance is not bound to the loader)."""

    def __init__(self, history, fail=()):
        self.history, self.fail, self.calls = history, set(fail), []

    def __call__(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, adjust))
        if adjust in self.fail:
            raise ConnectionError("secret provider detail")
        return self.history.loc[start_date:end_date].copy()


HISTORY = bars("2026-06-01", "2026-11-18")
FRIDAY = HISTORY.loc[:"2026-11-13"]


def test_updating_an_index_refreshes_the_series_the_comparison_reads(client, bt_dir, monkeypatch):
    store = DataStore(str(bt_dir / "backtesting.db"))
    store.save_daily("IDX:000300", FRIDAY, "")
    store.record_download("IDX:000300", "", "2026-06-01", "2026-11-13", fetched_at="2026-11-13T18:00:00")
    provider = Provider(HISTORY)
    monkeypatch.setattr(DataLoader, "get_daily", provider)

    answer = client.post("/api/backtest/data/download", json={
        "symbol": "IDX:000300", "start_date": "2026-06-01", "end_date": "2026-11-18", "adjust": "qfq",
        "all_adjustments": True})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert provider.calls == [("IDX:000300", "")]
    [entry] = body["adjustments"]
    assert entry["adjust"] == "" and entry["label"] == "不复权" and entry["ok"] is True
    assert entry["last"] == "2026-11-18" and entry["last_session"] == "2026-11-18" and entry["up_to_date"] is True
    assert entry["rows"] == len(HISTORY) and entry["error"] is None
    assert body["rows_saved"] == entry["rows_saved"]
    # Nothing is stored under an adjustment the comparison never reads.
    assert [item["adjust"] for listed in store.list_available(["IDX:000300"]) for item in listed["adjustments"]] == [""]


def test_updating_a_stock_refreshes_every_adjustment_it_holds(client, bt_dir, monkeypatch):
    store = DataStore(str(bt_dir / "backtesting.db"))
    for adjust in ("qfq", ""):
        store.save_daily("SHA:600519", FRIDAY, adjust)
        store.record_download("SHA:600519", adjust, "2026-06-01", "2026-11-13", fetched_at="2026-11-13T18:00:00")
    provider = Provider(HISTORY, fail={""})
    monkeypatch.setattr(DataLoader, "get_daily", provider)

    body = client.post("/api/backtest/data/download", json={
        "symbol": "SHA:600519", "start_date": "2026-06-01", "end_date": "2026-11-18", "adjust": "qfq",
        "all_adjustments": True}).json()
    assert provider.calls == [("SHA:600519", "qfq"), ("SHA:600519", "")]
    qfq, raw = body["adjustments"]
    assert qfq["adjust"] == "qfq" and qfq["ok"] is True and qfq["last"] == "2026-11-18" and qfq["up_to_date"] is True
    # The failed one still says what the cache holds, with a fixed reason.
    assert raw["adjust"] == "" and raw["ok"] is False and raw["rows_saved"] is None
    assert raw["last"] == "2026-11-13" and raw["up_to_date"] is False and raw["last_session"] == "2026-11-18"
    assert raw["error"].startswith("数据下载失败") and "secret provider detail" not in raw["error"]


def test_without_all_adjustments_only_the_one_asked_for_is_updated(client, bt_dir, monkeypatch):
    store = DataStore(str(bt_dir / "backtesting.db"))
    store.save_daily("SHA:600519", FRIDAY, "")
    provider = Provider(HISTORY)
    monkeypatch.setattr(DataLoader, "get_daily", provider)
    body = client.post("/api/backtest/data/download", json={
        "symbol": "SHA:600519", "start_date": "2026-06-01", "end_date": "2026-11-18"}).json()
    assert provider.calls == [("SHA:600519", "qfq")]
    assert body["rows_saved"] == len(HISTORY) and [entry["adjust"] for entry in body["adjustments"]] == ["qfq"]


def test_when_every_download_fails_the_route_refuses_as_before(client, bt_dir, monkeypatch):
    monkeypatch.setattr(DataLoader, "get_daily", Provider(HISTORY, fail={"qfq"}))
    answer = client.post("/api/backtest/data/download", json={
        "symbol": "SHA:600519", "start_date": "2026-06-01", "end_date": "2026-11-18", "all_adjustments": True})
    assert answer.status_code == 502 and "secret provider detail" not in answer.text
