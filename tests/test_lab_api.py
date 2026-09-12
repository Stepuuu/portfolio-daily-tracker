"""Exercise research HTTP boundaries without models, accounts or market traffic."""
import asyncio

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest

from backend.api.lab import router
from backend.main import validated_request_error
from core.lab_data import demo_records, metadata
from core.lab_fetch import provider_symbol
from core.lab_service import LabService


def client_for(service):
    app = FastAPI()
    app.state.lab_service = service
    app.include_router(router)
    app.add_exception_handler(RequestValidationError, validated_request_error)
    return TestClient(app)


def test_api_idempotency_cancellation_export_and_input_privacy(tmp_path):
    service = LabService(tmp_path)
    records = demo_records()
    data = service.store.add_dataset(metadata(records, "Fixture", "synthetic", True), records)
    with client_for(service) as client:
        payload = {"dataset_id": data["id"], "client_request_id": "retry-http-1"}
        first = client.post("/api/lab/runs", json=payload)
        assert first.status_code == 202
        identifier = first.json()["id"]
        assert client.post("/api/lab/runs", json=payload).json()["id"] == identifier
        assert client.post("/api/lab/runs", json={**payload, "template_id": "volatility"}).status_code == 409
        assert client.get(f"/api/lab/runs/{identifier}/result").status_code == 409
        cancelled = client.post(f"/api/lab/runs/{identifier}/cancel").json()
        assert cancelled["status"] == "cancelled"
        assert client.post(f"/api/lab/runs/{identifier}/retry").json()["id"] != identifier
        export = client.get(f"/api/lab/runs/{identifier}/export")
        assert "checkpoint" not in export.json()
        assert "export_notice" in export.json()
        sentinel = "SYNTHETIC_CREDENTIAL_DO_NOT_REFLECT"
        rejected = client.put("/api/lab/connections", json={"id": "example", "name": "Example",
            "provider": "openai", "model": "example", "api_key": sentinel,
            "base_url": f"https://example.invalid/?token={sentinel}"})
        assert rejected.status_code == 422
        assert sentinel not in rejected.text
        assert service.store.connections() == []


def test_demo_allows_only_synthetic_manual_work(tmp_path):
    service = LabService(tmp_path, demo=True)
    records = demo_records()
    demo = service.store.add_dataset(metadata(records, "Demo", "synthetic", True), records)
    imported = service.store.add_dataset(metadata(records, "Imported", "csv", False), records)
    with client_for(service) as client:
        assert client.post("/api/lab/runs", json={"dataset_id": demo["id"]}).status_code == 202
        assert client.post("/api/lab/runs", json={"dataset_id": imported["id"]}).status_code == 422
        assert client.post("/api/lab/runs", json={"dataset_id": demo["id"], "mode": "agent", "connection_id": "fake"}).status_code == 422
        assert client.put("/api/lab/connections", json={"id": "fake", "name": "Fake", "provider": "codex_cli"}).status_code == 403
        assert client.post("/api/lab/datasets/import", files={"file": ("example.csv", b"none")}).status_code == 403


@pytest.mark.parametrize("original,expected", [
    ("SHA:600000", "600000"), ("SHE:000001", "000001"), ("HKG:700", "0700.HK"),
    ("NASDAQ:EXAMPLE", "EXAMPLE"), ("EXAMPLE.US", "EXAMPLE"), ("700.HK", "0700.HK"),
])
def test_exchange_codes_map_without_exposing_portfolio(original, expected):
    assert provider_symbol(original) == expected


def test_demo_isolates_inherited_storage_and_rejects_legacy_queue(tmp_path, monkeypatch):
    monkeypatch.setenv('TRACKER_LAB_DIR', str(tmp_path))
    private = LabService()
    records = demo_records()
    data = private.store.add_dataset(metadata(records, 'Private fixture', 'csv'), records)
    private.store.save_connection({'id': 'private-connection', 'provider': 'openai', 'model': 'fixture'})
    original = private.store.submit({'mode': 'agent', 'dataset_id': data['id']})
    demo = LabService(demo=True)
    assert demo.store.path != private.store.path
    with client_for(demo) as client:
        for route in ['datasets', 'runs', 'connections']:
            assert client.get('/api/lab/' + route).json() == []
    async def scenario():
        await demo.start()
        try:
            dataset = demo.store.datasets()[0]
            queued = demo.store.submit({'mode': 'agent', 'dataset_id': dataset['id'],
                                       'timeout_seconds': 30, 'max_experiments': 1})
            for _ in range(80):
                run = demo.public_run(queued)
                if run['status'] == 'failed':
                    break
                await asyncio.sleep(.03)
            assert run['status'] == 'failed'
            assert run['progress']['model_calls'] == 0
            assert private.store.get(original)['status'] == 'queued'
        finally:
            await demo.stop()
    asyncio.run(scenario())
