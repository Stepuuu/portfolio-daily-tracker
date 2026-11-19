"""HTTP contract of proposal review, including the read-only demo."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from backend.api import proposals as api
from core import notify, proposals


class Counter:
    """A fictional kind whose apply only counts, so a double apply is visible."""

    def __init__(self):
        self.applied = 0
        self.closed = False
        self.fail_with = None

    def validate(self, payload):
        if self.closed:
            raise proposals.ProposalError("计数器已关闭，不能再增加。")
        if not isinstance(payload.get("step"), int) or payload["step"] <= 0:
            raise proposals.ProposalError("步长必须是正整数。")
        return {"step": payload["step"]}

    def describe(self, payload):
        return f"计数器增加 {payload['step']}"

    def apply(self, payload, proposal):
        if self.fail_with is not None:
            raise self.fail_with
        self.applied += payload["step"]
        return {"total": self.applied}


@pytest.fixture
def counter(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    monkeypatch.setattr(proposals, "_kinds", {})
    kind = Counter()
    proposals.register_kind("counter", kind.validate, kind.apply, kind.describe)
    return kind


@pytest.fixture
def client(counter):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/proposals")
    return TestClient(app)


def propose(step=1):
    return proposals.propose("counter", {"step": step}, "agent:chat")


def test_pending_list_shows_payload_summary_and_source(client, counter):
    assert client.get("/api/proposals").json() == {"items": [], "pending": 0}
    first, second = propose(1), propose(2)
    body = client.get("/api/proposals", params={"status": "pending"}).json()
    assert body["pending"] == 2
    assert body["items"] == [second, first]
    assert body["items"][0]["summary"] == "计数器增加 2"
    assert body["items"][0]["payload"] == {"step": 2} and body["items"][0]["source"] == "agent:chat"
    assert client.get(f"/api/proposals/{first['id']}").json() == first
    assert counter.applied == 0, "reading never applies"


def test_confirm_applies_once_and_can_be_repeated(client, counter):
    proposal = propose(5)
    response = client.post(f"/api/proposals/{proposal['id']}/confirm")
    assert response.status_code == 200
    applied = response.json()
    assert applied["status"] == "applied" and applied["result"] == {"total": 5}
    again = client.post(f"/api/proposals/{proposal['id']}/confirm")
    assert again.status_code == 200 and again.json() == applied
    assert counter.applied == 5
    assert client.get("/api/proposals").json() == {"items": [], "pending": 0}
    assert client.get("/api/proposals", params={"status": "applied"}).json()["items"] == [applied]


def test_reject_then_confirm_is_a_conflict(client, counter):
    proposal = propose()
    response = client.delete(f"/api/proposals/{proposal['id']}")
    assert response.status_code == 200 and response.json()["status"] == "rejected"
    assert client.delete(f"/api/proposals/{proposal['id']}").status_code == 200
    conflict = client.post(f"/api/proposals/{proposal['id']}/confirm")
    assert conflict.status_code == 409 and "已被放弃" in conflict.json()["detail"]
    assert counter.applied == 0
    assert client.get("/api/proposals").json()["pending"] == 0


def test_applied_proposal_cannot_be_rejected(client, counter):
    proposal = propose()
    client.post(f"/api/proposals/{proposal['id']}/confirm")
    assert client.delete(f"/api/proposals/{proposal['id']}").status_code == 409
    assert client.get(f"/api/proposals/{proposal['id']}").json()["status"] == "applied"


@pytest.mark.parametrize("method, suffix", [("get", ""), ("post", "/confirm"), ("delete", "")])
def test_unknown_proposal_is_a_chinese_404(client, method, suffix):
    response = getattr(client, method)(f"/api/proposals/{'0' * 32}{suffix}")
    assert response.status_code == 404
    assert "方案不存在" in response.json()["detail"]


def test_payload_that_became_invalid_is_a_chinese_422_and_stays_pending(client, counter):
    proposal = propose()
    counter.closed = True
    response = client.post(f"/api/proposals/{proposal['id']}/confirm")
    assert response.status_code == 422
    assert response.json()["detail"] == "计数器已关闭，不能再增加。"
    assert counter.applied == 0
    [pending] = client.get("/api/proposals").json()["items"]
    assert pending["status"] == "pending" and pending["error"] == "计数器已关闭，不能再增加。"


def test_unloaded_kind_is_a_chinese_422_not_a_500(client, counter, monkeypatch):
    proposal = propose()
    monkeypatch.setattr(proposals, "_kinds", {})
    response = client.post(f"/api/proposals/{proposal['id']}/confirm")
    assert response.status_code == 422
    assert "未知的方案类型" in response.json()["detail"]
    assert client.get("/api/proposals").json()["pending"] == 1


def test_expired_proposal_is_a_conflict(client, counter, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(proposals, "_now", lambda: clock[0])
    proposal = proposals.propose("counter", {"step": 1}, "agent:chat", ttl_seconds=60)
    clock[0] += 61
    assert client.get("/api/proposals").json() == {"items": [], "pending": 0}
    response = client.post(f"/api/proposals/{proposal['id']}/confirm")
    assert response.status_code == 409 and "过期" in response.json()["detail"]
    assert counter.applied == 0
    assert client.get("/api/proposals", params={"status": "expired"}).json()["items"][0]["id"] == proposal["id"]


def test_refused_apply_is_a_422_with_the_kinds_message(client, counter):
    proposal = propose()
    counter.fail_with = proposals.ProposalError("计数器已满。")
    response = client.post(f"/api/proposals/{proposal['id']}/confirm")
    assert response.status_code == 422 and response.json()["detail"] == "计数器已满。"
    assert client.get("/api/proposals").json()["items"][0]["error"] == "计数器已满。"


def test_crashing_apply_reports_without_internals_and_stays_pending(client, counter):
    proposal = propose()
    counter.fail_with = RuntimeError("internal detail 76543.21")
    response = client.post(f"/api/proposals/{proposal['id']}/confirm")
    assert response.status_code == 500
    assert "76543.21" not in response.text and "未做任何修改" in response.json()["detail"]
    counter.fail_with = None
    assert client.post(f"/api/proposals/{proposal['id']}/confirm").json()["result"] == {"total": 1}


def test_unknown_status_filter_is_a_chinese_422(client):
    response = client.get("/api/proposals", params={"status": "everything"})
    assert response.status_code == 422 and "未知的方案状态" in response.json()["detail"]


def test_http_cannot_create_or_edit_a_proposal(client, counter):
    proposal = propose()
    assert client.post("/api/proposals", json={"kind": "counter", "payload": {"step": 9}}).status_code == 405
    assert client.put(f"/api/proposals/{proposal['id']}", json={"payload": {"step": 9}}).status_code == 405
    assert client.post(f"/api/proposals/{proposal['id']}/apply").status_code in (404, 405)
    assert client.get("/api/proposals").json()["items"] == [proposal]
    assert counter.applied == 0


def test_lock_contention_is_a_chinese_503(client, counter, tmp_path, monkeypatch):
    import sqlite3
    from core import desk_store
    proposal = propose()
    monkeypatch.setattr(desk_store, "BUSY_TIMEOUT_MS", 100)
    holder = sqlite3.connect(tmp_path / "desk.sqlite3", timeout=0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        for response in (client.post(f"/api/proposals/{proposal['id']}/confirm"),
                         client.delete(f"/api/proposals/{proposal['id']}")):
            assert response.status_code == 503 and "稍后" in response.json()["detail"]
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert counter.applied == 0


def test_store_newer_than_the_code_is_a_503(client, counter):
    from core import desk_store
    proposal = propose()
    desk_store.ensure_schema(proposals.SCHEMA, proposals.MIGRATIONS + [["CREATE TABLE later (id INTEGER PRIMARY KEY)"]])
    for response in (client.get("/api/proposals"), client.get(f"/api/proposals/{proposal['id']}"),
                     client.post(f"/api/proposals/{proposal['id']}/confirm"),
                     client.delete(f"/api/proposals/{proposal['id']}")):
        assert response.status_code == 503, response.text
    assert counter.applied == 0


def test_router_is_registered_as_a_feature():
    mounted = {prefix: router for router, prefix, _tags in features.routers()}
    assert mounted.get("/api/proposals") is api.router


def test_demo_reads_of_an_empty_portfolio_create_nothing(counter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from backend.main import app

    portfolio = tmp_path / "portfolio"
    portfolio.mkdir()
    monkeypatch.setenv("PORTFOLIO_DIR", str(portfolio))
    monkeypatch.setenv("TRACKER_DEMO_MODE", "1")
    client = TestClient(app, base_url="http://127.0.0.1")
    for url in ("/api/proposals", "/api/proposals?status=all", "/api/notifications",
                "/api/accounts/profiles", "/api/accounts/a/profile",
                "/api/accounts/a/fee-estimate?ticker=SHA:600519&side=buy&quantity=100&price=10&date=2026-11-16"):
        response = client.get(url)
        assert response.status_code == 200, url
    assert client.get(f"/api/proposals/{'0' * 32}").status_code == 404
    assert list(portfolio.iterdir()) == []


def test_demo_mode_reads_work_and_writes_are_rejected(counter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # importing the application creates data directories in the working directory
    from backend.main import app

    # No lifespan: no model or market services are started. The real application only
    # answers loopback Host headers, so the client must not use the default "testserver".
    client = TestClient(app, base_url="http://127.0.0.1")
    proposal = propose(3)
    seq = notify.publish("alert", "info", "Example", "Example body")

    monkeypatch.setenv("TRACKER_DEMO_MODE", "1")
    assert client.get("/api/proposals").json() == {"items": [proposal], "pending": 1}
    assert client.get(f"/api/proposals/{proposal['id']}").json() == proposal
    assert client.get("/api/notifications").json()["unread"] == 1
    for response in (client.post(f"/api/proposals/{proposal['id']}/confirm"),
                     client.delete(f"/api/proposals/{proposal['id']}"),
                     client.post(f"/api/notifications/{seq}/read"),
                     client.post("/api/notifications/read-all")):
        assert response.status_code == 403
        assert response.json() == {"detail": "Demo mode is read-only"}
    assert counter.applied == 0
    assert client.get("/api/proposals").json() == {"items": [proposal], "pending": 1}
    assert client.get("/api/notifications").json()["unread"] == 1

    # The same requests go through once demo mode is off, so the 403s came from the demo guard.
    monkeypatch.delenv("TRACKER_DEMO_MODE")
    assert client.post(f"/api/notifications/{seq}/read").json()["unread"] == 0
    assert client.post(f"/api/proposals/{proposal['id']}/confirm").json()["result"] == {"total": 3}
    assert counter.applied == 3
