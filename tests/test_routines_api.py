"""HTTP contract of ``/api/routines``, with fake clocks and fake runs on a temporary desk store."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, time
import sqlite3
import time as wall

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from backend.api import routines as api
from core import notify
from core.market.calendar import SHANGHAI
from core.routines import close_snapshot, scheduler
from core.routines.scheduler import Routine, Scheduler
from tests.fakes import FakeClock

TUESDAY = "2026-11-17"


def due(clock):
    day = clock.local_date()
    if not clock.is_trading_day(day=day):
        return None
    return datetime.combine(day, time(15, 40), tzinfo=SHANGHAI)


class Script:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def __call__(self, trading_date):
        self.calls.append(trading_date)
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    for name in ("TRACKER_DEMO_MODE", "TRACKER_LAB_ONLY", "TRACKER_QUOTE_SOURCE", "TRACKER_CLOSE_SNAPSHOT"):
        monkeypatch.delenv(name, raising=False)
    yield
    assert scheduler.current() is None, "a test left a scheduler running"


def app_with(scheduler_factory=None):
    """The router alone; with a factory, a lifespan runs that scheduler like the backend does."""
    @asynccontextmanager
    async def lifespan(app):
        if scheduler_factory is None:
            yield
            return
        running = scheduler_factory()
        running.start()
        try:
            yield
        finally:
            await running.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(api.router, prefix="/api/routines")
    return app


def wait_for(client, predicate, path="/api/routines/runs?kind=close_snapshot", seconds=15):
    deadline = wall.monotonic() + seconds
    while True:
        body = client.get(path).json()
        if predicate(body):
            return body
        assert wall.monotonic() < deadline, body
        wall.sleep(0.05)


# ── Reading ──

def test_status_without_a_running_scheduler():
    body = TestClient(app_with()).get("/api/routines/status").json()
    assert body["scheduler"]["alive"] is False and body["scheduler"]["mode"] == "normal"
    assert "只在后端运行期间" in body["scheduler"]["note"]
    kinds = {item["kind"]: item for item in body["routines"]}
    item = kinds["close_snapshot"]
    assert item["title"] == "收盘快照" and item["next_due_at"].endswith("15:40:00+08:00")
    assert set(item) == {"kind", "title", "trading_date", "due_at", "next_due_at", "today", "last_success",
                         "outstanding"}
    assert item["outstanding"] == []


def test_status_in_research_only_mode_says_routines_do_not_run(monkeypatch):
    monkeypatch.setenv("TRACKER_LAB_ONLY", "1")
    body = TestClient(app_with()).get("/api/routines/status").json()
    assert body["scheduler"]["mode"] == "lab_only" and "研究模式" in body["scheduler"]["note"]


def test_reading_routines_on_an_empty_portfolio_creates_no_store(tmp_path):
    client = TestClient(app_with())
    status = client.get("/api/routines/status")
    assert status.status_code == 200 and status.json()["routines"]
    assert {item["kind"]: item for item in status.json()["routines"]}["close_snapshot"]["today"] is None
    assert client.get("/api/routines/runs").json() == {"items": []}
    assert client.get("/api/routines/runs", params={"kind": "close_snapshot"}).json() == {"items": []}
    assert scheduler.run_for("close_snapshot", TUESDAY) is None and scheduler.last_success("close_snapshot") is None
    assert not (tmp_path / "desk.sqlite3").exists()


def test_the_routine_schema_is_registered_for_the_start_up_upgrade(tmp_path):
    from core import desk_store
    desk_store.ensure_schema("example_other", [["CREATE TABLE example_other (id INTEGER)"]])   # the file exists
    assert desk_store.migrate_registered().get("routine_runs") == len(scheduler.MIGRATIONS)


def test_runs_lists_rows_newest_first_and_validates_its_query():
    clock = FakeClock()
    fake = Routine(close_snapshot.KIND, due, Script(scheduler.ok("示例完成")), close_snapshot.TITLE)

    async def two_days():
        for day in (TUESDAY, "2026-11-18"):
            clock.set(f"{day} 15:40")
            s = Scheduler([fake], now=clock)
            await s.tick()
            await s.settle()
    asyncio.run(two_days())

    client = TestClient(app_with())
    items = client.get("/api/routines/runs", params={"kind": "close_snapshot"}).json()["items"]
    assert [item["trading_date"] for item in items] == ["2026-11-18", TUESDAY]
    assert items[0]["status"] == "ok" and items[0]["status_text"] == "已完成" and items[0]["summary"] == "示例完成"
    assert "owner" not in items[0] and "lease_until" not in items[0]
    assert len(client.get("/api/routines/runs", params={"limit": 1}).json()["items"]) == 1
    assert client.get("/api/routines/runs", params={"kind": "no_such_kind"}).status_code == 404
    assert client.get("/api/routines/runs", params={"kind": "Bad Kind"}).status_code == 422
    assert client.get("/api/routines/runs", params={"limit": 0}).status_code == 422


def test_storage_errors_are_503_not_500(monkeypatch):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(scheduler, "status_report", broken)
    monkeypatch.setattr(scheduler, "runs", broken)
    client = TestClient(app_with(), raise_server_exceptions=False)
    for path in ("/api/routines/status", "/api/routines/runs"):
        response = client.get(path)
        assert response.status_code == 503 and "例行任务记录" in response.json()["detail"]


# ── Running now ──

def test_run_now_needs_the_background_scheduler():
    response = TestClient(app_with()).post("/api/routines/close_snapshot/run")
    assert response.status_code == 409 and "后台调度器未运行" in response.json()["detail"]
    assert TestClient(app_with()).post("/api/routines/no_such_kind/run").status_code == 404


def test_run_now_after_the_last_retry_runs_again_and_then_refuses():
    clock = FakeClock(f"{TUESDAY} 16:00")
    run = Script(scheduler.failed("示例失败", "example"), scheduler.ok("示例完成"))
    fake = Routine(close_snapshot.KIND, due, run, close_snapshot.TITLE, retry_delays=())

    with TestClient(app_with(lambda: Scheduler([fake], now=clock))) as client:
        # Started after 15:40: the start-up catch-up runs today at once, and it fails for good.
        wait_for(client, lambda body: body["items"] and body["items"][0]["status"] == "failed")
        status = client.get("/api/routines/status").json()
        assert status["scheduler"]["alive"] is True
        today = {item["kind"]: item for item in status["routines"]}["close_snapshot"]["today"]
        assert today["status"] == "failed" and today["next_attempt_at"] is None
        assert len(notify.list()) == 1

        response = client.post("/api/routines/close_snapshot/run")
        assert response.status_code == 202
        assert response.json()["run"]["status"] == "pending" and response.json()["accepted"] is True
        done = wait_for(client, lambda body: body["items"][0]["status"] == "ok")
        assert done["items"][0]["attempts"] == 2 and run.calls == [TUESDAY, TUESDAY]

        again = client.post("/api/routines/close_snapshot/run")
        assert again.status_code == 409 and "已经完成" in again.json()["detail"]
    assert len(notify.list()) == 1


def test_run_now_before_the_due_time_is_refused():
    clock = FakeClock(f"{TUESDAY} 10:00")
    fake = Routine(close_snapshot.KIND, due, Script(scheduler.ok()), close_snapshot.TITLE)
    with TestClient(app_with(lambda: Scheduler([fake], now=clock))) as client:
        wait_for(client, lambda body: body["scheduler"]["alive"], path="/api/routines/status")
        response = client.post("/api/routines/close_snapshot/run")
        assert response.status_code == 409 and "15:40" in response.json()["detail"]


# ── Registration and the real application ──

def test_router_and_background_task_are_registered_as_features():
    mounted = {prefix: router for router, prefix, _tags in features.routers()}
    assert mounted.get("/api/routines") is api.router
    assert scheduler.background in features.background_tasks()


def test_demo_background_never_starts_the_engine_and_writes_nothing(tmp_path, monkeypatch):
    """The registered background task in demo mode: no scheduler, no child process, no row, no file."""
    monkeypatch.setenv("TRACKER_DEMO_MODE", "1")
    monkeypatch.setattr(scheduler, "wall_clock", FakeClock(f"{TUESDAY} 16:00"))

    async def no_engine(*args, **kwargs):
        raise AssertionError("demo mode started the engine")

    monkeypatch.setattr(close_snapshot, "run_engine", no_engine)
    app = FastAPI(lifespan=features.background)
    app.include_router(api.router, prefix="/api/routines")
    with TestClient(app) as client:
        wall.sleep(0.3)
        assert client.get("/api/routines/runs").json() == {"items": []}
        status = client.get("/api/routines/status").json()
    assert status["scheduler"]["alive"] is False and status["scheduler"]["mode"] == "demo"
    assert status["scheduler"]["note"] == "演示模式：例行任务不运行，也不写入任何记录；页面显示的是演示数据。"
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_demo_mode_rejects_run_now_in_the_real_application(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # importing the application creates data directories in the working directory
    from backend.main import app

    # No lifespan: nothing is started. The real application only answers loopback Host headers.
    client = TestClient(app, base_url="http://127.0.0.1")
    monkeypatch.setenv("TRACKER_DEMO_MODE", "1")
    assert client.get("/api/routines/status").status_code == 200
    response = client.post("/api/routines/close_snapshot/run")
    assert response.status_code == 403 and response.json() == {"detail": "Demo mode is read-only"}
    monkeypatch.delenv("TRACKER_DEMO_MODE")
    assert client.post("/api/routines/close_snapshot/run").status_code == 409, "the 403 came from the demo guard"
