import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import portfolio_tracker as tracker


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "PORTFOLIO_DIR", str(tmp_path))
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "holdings").mkdir()
    (tmp_path / "config.json").write_text('{"private_token": "sentinel"}')
    app = FastAPI()
    app.include_router(tracker.router, prefix="/api/tracker")
    return TestClient(app)


@pytest.mark.parametrize("route", ["snapshot", "holdings", "group/Account"])
@pytest.mark.parametrize("day", ["../config", "../../config", "/tmp/config", "2026-02-30", "2026-9-9"])
def test_invalid_dates_cannot_read_arbitrary_json(client, route, day):
    response = client.get(f"/api/tracker/{route}", params={"date": day})
    assert response.status_code == 422
    assert "sentinel" not in response.text


def test_dates_ignore_templates_and_latest_ignores_example(client, tmp_path):
    (tmp_path / "snapshots/example.json").write_text('{}')
    (tmp_path / "snapshots/2026-09-09.json").write_text('{"date":"2026-09-09","groups":{}}')
    assert client.get('/api/tracker/dates').json()["dates"] == ["2026-09-09"]
    assert client.get('/api/tracker/snapshot').json()["date"] == "2026-09-09"


def test_synthetic_snapshot_reports_actual_positions_date(client, tmp_path):
    (tmp_path / "snapshots/2026-09-07.json").write_text(json.dumps({"date": "2026-09-07", "groups": {}}))
    (tmp_path / "history.csv").write_text(
        'date,total_value,total_cost,total_profit,return_pct,daily_change,daily_change_pct,max_drawdown_pct\n'
        '2026-09-09,110,100,10,10,10,10,0\n'
        '2026-09-07,100,100,0,0,0,0,0\n'
    )
    response = client.get('/api/tracker/snapshot', params={"date": "2026-09-09"})
    assert response.status_code == 200
    data = response.json()
    assert data["synthetic"] is True
    assert data["positions_as_of"] == "2026-09-07"
    assert data["summary"]["prev_date"] == "2026-09-07"
    assert client.get('/api/tracker/snapshot').json()["date"] == "2026-09-09"
