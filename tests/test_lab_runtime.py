"""Exercise real queue transitions and adversarial research boundaries offline."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time

import pytest

from core.lab_data import demo_records, import_csv, validate_records
from core.lab_mcp import handle
from core.lab_service import LabService
from core.lab_store import Conflict, LabStore, LostLease
from core.llm.base import LLMResponse


def test_raw_csv_quality_and_symbol_preservation():
    content = b"date,symbol,open,high,low,close,volume\n2024-01-02,000001,10,12,9,11,100\n"
    assert import_csv(content)[0]["symbol"] == "000001"
    for replacement in (b"inf", b"nan", b"-1"):
        with pytest.raises(ValueError):
            import_csv(content.replace(b",10,", b"," + replacement + b","))
    records = demo_records()[:4]
    with pytest.raises(ValueError, match="重复"):
        validate_records(records + records[:1])
    with pytest.raises(ValueError, match="递增"):
        validate_records(records[::-1])


def test_atomic_claim_idempotency_fencing_and_retry(tmp_path):
    store = LabStore(tmp_path)
    identifier = store.submit({"dataset_id": "example"}, "same")
    assert store.submit({"dataset_id": "example"}, "same") == identifier
    with pytest.raises(Conflict):
        store.submit({"dataset_id": "other"}, "same")
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda n: store.claim(str(n)), range(8)))
    assert sum(x is not None for x in claimed) == 1
    with store.connect() as db:
        db.execute("UPDATE runs SET lease_until=0 WHERE id=?", (identifier,))
    resumed = store.claim("new-owner")
    assert resumed["id"] == identifier
    with pytest.raises(LostLease):
        store.checkpoint(identifier, "old-owner", "computed", {}, "late output")
    store.checkpoint(identifier, "new-owner", "computed", {"frozen": True}, "saved")
    store.cancel(identifier)
    with pytest.raises(LostLease):
        store.finish(identifier, "new-owner", result={"late": True})
    retry = store.retry(identifier)
    assert retry != identifier
    assert store.get(retry)["request"]["parent_run_id"] == identifier
    assert store.get(identifier)["status"] == "cancelled"


def test_dataset_tampering_and_schedule_deduplication(tmp_path):
    store = LabStore(tmp_path)
    records = demo_records()[:2]
    info = store.add_dataset({"name": "demo"}, records)
    with store.connect() as db:
        db.execute("UPDATE datasets SET content='[]' WHERE id=?", (info["id"],))
    with pytest.raises(ValueError, match="integrity"):
        store.dataset(info["id"])
    schedule = store.schedule({"name": "repeat", "request": {"example": True}, "interval_seconds": 900, "max_runs": 1, "enabled": True})
    with store.connect() as db:
        db.execute("UPDATE schedules SET next_due=0 WHERE id=?", (schedule["id"],))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: store.enqueue_due(), range(4)))
    assert len(store.runs()) == 1
    assert store.schedules()[0]["enabled"] is False


def test_equal_prices_keep_distinct_data_provenance(tmp_path):
    store = LabStore(tmp_path)
    records = demo_records()[:2]
    synthetic = store.add_dataset({"name": "same", "source": "synthetic", "synthetic": True}, records)
    imported = store.add_dataset({"name": "same", "source": "csv", "synthetic": False}, records)
    assert synthetic["content_hash"] == imported["content_hash"]
    assert synthetic["id"] != imported["id"]
    assert store.dataset(imported["id"])[0]["synthetic"] is False


def test_resume_does_not_skip_validation_review(tmp_path):
    async def scenario():
        model = FakeResearchModel()
        service = LabService(tmp_path, provider_factory=lambda _: model)
        await service.start()
        try:
            dataset = service.store.datasets()[0]
            service.store.save_connection({"id": "fake", "provider": "openai", "model": "fixture"})
            run = service.submit({"mode": "agent", "objective": "Resume validation review", "dataset_id": dataset["id"],
                                  "template_id": "volatility", "connection_id": "fake", "max_experiments": 2})
            claimed = service.store.claim("interrupted-worker")
            assert claimed["id"] == run["id"]
            spec = {"template_id": "volatility", **claimed["request"]["params"]}
            cp = {"started": time.time(), "model_calls": 1, "max_model_calls": 4, "dataset_id": dataset["id"],
                  "plan": {"experiments": [{"template_id": "volatility", "params": {}}]},
                  "queue": [{"template_id": "volatility", "params": {}}],
                  "experiments": [{"id": "experiment_1", "status": "completed", "spec": spec,
                                   "result": {"metrics": {"validation": {"mae": 1}, "test": {"mae": 9}}, "warnings": []}}]}
            service.store.checkpoint(run["id"], "interrupted-worker", "evaluated", cp, "Computed before restart")
            service.store.release("interrupted-worker")
            final = await wait_complete(service, run["id"])
            assert final["status"] == "completed", final.get("error")
            assert [p["results"]["phase"] for p in model.payloads] == ["validation", "final"]
            assert final["progress"]["model_calls"] == 3
        finally:
            await service.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("bad", [None, True, -1, float("inf")])
def test_invalid_selection_metric_cannot_complete(tmp_path, bad):
    async def scenario():
        async def compute(records, spec):
            return {"metrics": {"validation": {"mae": bad}}}
        service = LabService(tmp_path, compute=compute)
        await service.start()
        try:
            run = service.submit({"objective": "Reject invalid metrics", "dataset_id": service.store.datasets()[0]["id"]})
            final = await wait_complete(service, run["id"])
            assert final["status"] == "failed"
            assert not final["progress"]["frozen"]
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_api_contract_accepts_registered_template_parameters(tmp_path):
    from backtesting.research import register_template
    identifier = "custom_" + tmp_path.name.replace("-", "_")[-20:]
    # Parameter validation depends on registry metadata, not a hard-coded UI model.
    register_template(identifier, {"name": "Custom bins", "description": "Fixture", "target": "custom_target",
                                   "parameters": {"bins": {"type": "integer", "minimum": 2, "maximum": 8}},
                                   "defaults": {"bins": 4}}, runner=lambda frames, spec: {})
    service = LabService(tmp_path)
    data = service.store.add_dataset({"name": "fixture", "synthetic": True}, demo_records())
    prepared = service.prepare_request({"objective": "Try a custom registered method", "dataset_id": data["id"], "template_id": identifier,
                                       "params": {"bins": 5}})
    assert prepared["params"] == {"bins": 5}
    with pytest.raises(ValueError):
        service.prepare_request({"objective": "Reject bad custom parameter", "dataset_id": data["id"], "template_id": identifier,
                                 "params": {"bins": 99}})


def test_mcp_uses_only_registered_routes():
    called = []
    def invoke(method, path, body):
        called.append((method, path))
        return []
    listed = handle({"id": 1, "method": "tools/list"}, invoke)
    assert any(x["name"] == "research_run" for x in listed["result"]["tools"])
    response = handle({"id": 2, "method": "tools/call", "params": {"name": "research_datasets", "arguments": {}}}, invoke)
    assert response["result"]["isError"] is False
    invalid = handle({"id": 3, "method": "tools/call", "params": {"name": "research_status", "arguments": {"run_id": "../../settings"}}}, invoke)
    assert "error" in invalid
    assert called == [("GET", "/datasets")]


class FakeResearchModel:
    def __init__(self):
        self.payloads = []

    async def chat(self, messages, **kwargs):
        data = json.loads(messages[-1]["content"])["input"]
        self.payloads.append(data)
        if "context" in data:
            output = {"question": "Does historical information predict future risk?",
                      "hypotheses": ["A baseline may be hard to improve."],
                      "experiments": [{"template_id": "volatility", "params": {"lookback": 20}}],
                      "limitations": ["Synthetic data cannot establish investment value."]}
        else:
            evidence = data["results"]["experiments"]
            output = {"conclusion": "Compare the supplied validation evidence.",
                      "evidence": [{"experiment_id": evidence[0]["id"], "observation": "One completed experiment."}],
                      "limitations": ["Small synthetic sample."], "revised_spec": None}
        return LLMResponse(content=json.dumps(output), model="offline-test", usage={}, finish_reason="stop")


async def wait_complete(service, identifier):
    for _ in range(200):
        run = service.public_run(identifier)
        if run["status"] in {"completed", "failed", "cancelled"}:
            return run
        await asyncio.sleep(.03)
    pytest.fail("Research did not finish within the bounded fixture")


def test_full_agent_flow_keeps_holdout_out_of_refinement(tmp_path):
    async def scenario():
        model = FakeResearchModel()
        async def compute(records, spec):
            return {"metrics": {"validation": {"mae": 1.0}, "test": {"mae": 987654.0}},
                    "equity": [], "manifest": {}, "warnings": [], "lessons": []}
        service = LabService(tmp_path, provider_factory=lambda _: model, compute=compute)
        await service.start()
        try:
            dataset = service.store.datasets()[0]
            service.store.save_connection({"id": "fake", "provider": "openai", "model": "fixture"})
            run = service.submit({"mode": "agent", "objective": "Study volatility prediction", "dataset_id": dataset["id"],
                                  "template_id": "volatility", "connection_id": "fake", "max_experiments": 2})
            final = await wait_complete(service, run["id"])
            assert final["status"] == "completed", final.get("error")
            assert final["result"]["execution"]["frozen"]
            assert len(model.payloads) == 3
            assert "987654" not in json.dumps(model.payloads[:-1])
            assert "987654" in json.dumps(model.payloads[-1])
            assert final["result"]["selected_experiment"] == "experiment_1"
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_cancel_running_compute_and_recover_checkpoint(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        async def compute(records, spec):
            entered.set()
            await asyncio.sleep(30)
        service = LabService(tmp_path, compute=compute)
        await service.start()
        try:
            run = service.submit({"objective": "Cancel a running experiment", "dataset_id": service.store.datasets()[0]["id"]})
            await asyncio.wait_for(entered.wait(), 3)
            assert service.health()["worker_alive"]
            service.store.cancel(run["id"])
            for _ in range(30):
                if service.active_run is None:
                    break
                await asyncio.sleep(.05)
            assert service.public_run(run["id"])["status"] == "cancelled"
            assert service.active_run is None
            assert service.public_run(run["id"])["result"] is None
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_real_manual_compute_in_separate_process(tmp_path):
    async def scenario():
        service = LabService(tmp_path)
        await service.start()
        try:
            run = service.submit({"objective": "Learn stock volatility prediction", "dataset_id": service.store.datasets()[0]["id"],
                                  "template_id": "volatility"})
            final = await wait_complete(service, run["id"])
            assert final["status"] == "completed", final.get("error")
            assert final["result"]["metrics"]["test"]["mae"] >= 0
            assert final["result"]["dataset"]["synthetic"] is True
            assert len(final["result"]["equity"]) > 10
        finally:
            await service.stop()
    asyncio.run(scenario())
