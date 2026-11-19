"""HTTP contract of the notification inbox, on synthetic notifications."""
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from backend.api import notifications
from core import desk_store, notify


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(notifications.router, prefix="/api/notifications")
    return TestClient(app)


def publish(title, **values):
    return notify.publish("alert", values.pop("level", "info"), title, "Example body", **values)


def test_empty_inbox(client):
    assert client.get("/api/notifications").json() == {"items": [], "unread": 0, "last_seq": 0}


def test_inbox_lists_newest_first_with_every_field(client):
    first = publish("First", level="warning", data={"ticker": "NASDAQ:EXAMPLE"})
    second = publish("Second")
    body = client.get("/api/notifications").json()
    assert body["unread"] == 2 and body["last_seq"] == second
    assert [item["seq"] for item in body["items"]] == [second, first]
    assert body["items"][1] == {"seq": first, "kind": "alert", "level": "warning", "title": "First",
                                "body": "Example body", "data": {"ticker": "NASDAQ:EXAMPLE"},
                                "created_at": body["items"][1]["created_at"], "read_at": None}


def test_polling_after_the_last_seq_returns_only_new_items(client):
    seqs = [publish(f"n{index}") for index in range(3)]
    last = client.get("/api/notifications").json()["last_seq"]
    assert client.get("/api/notifications", params={"after": last}).json() == {
        "items": [], "unread": 3, "last_seq": seqs[2]}
    new = publish("new")
    body = client.get("/api/notifications", params={"after": last}).json()
    assert [item["seq"] for item in body["items"]] == [new] and body["last_seq"] == new
    page = client.get("/api/notifications", params={"limit": 2}).json()
    assert [item["seq"] for item in page["items"]] == [new, seqs[2]]
    assert page["unread"] == 4, "unread counts the whole inbox, not the page"


def seqs_of(response):
    assert response.status_code == 200, response.text
    return [item["seq"] for item in response.json()["items"]]


def test_every_row_is_reachable_past_the_largest_page(client):
    stored = [publish(f"n{index}") for index in range(201)]
    first = client.get("/api/notifications", params={"limit": 200})
    assert seqs_of(first) == stored[:0:-1] and first.json()["last_seq"] == stored[-1]
    # Infinite scroll: older pages, newest first, below the smallest seq shown.
    assert seqs_of(client.get("/api/notifications", params={"before": stored[1]})) == [stored[0]]
    assert seqs_of(client.get("/api/notifications", params={"before": stored[0]})) == []
    # Polling from the start: oldest first, no gaps between pages.
    assert seqs_of(client.get("/api/notifications", params={"after": 0, "limit": 200})) == stored[:200]
    assert seqs_of(client.get("/api/notifications", params={"after": stored[199], "limit": 200})) == [stored[200]]


def test_a_poller_that_falls_behind_misses_nothing(client):
    publish("seen at the first load")
    cursor = client.get("/api/notifications").json()["last_seq"]
    arrived = [publish(f"while away {index}") for index in range(7)]
    collected = []
    for _ in range(10):  # the documented poller loop, with a bound so a wrong cursor fails instead of hanging
        page = seqs_of(client.get("/api/notifications", params={"after": cursor, "limit": 3}))
        collected += page
        if page:
            cursor = page[-1]
        if len(page) < 3:
            break
    assert collected == arrived


def test_scrolling_down_reaches_the_oldest_row(client):
    stored = [publish(f"n{index}") for index in range(8)]
    shown = seqs_of(client.get("/api/notifications", params={"limit": 3}))
    for _ in range(10):
        page = seqs_of(client.get("/api/notifications", params={"before": shown[-1], "limit": 3}))
        shown += page
        if len(page) < 3:
            break
    assert shown == stored[::-1]


def test_after_and_before_together_are_refused(client):
    response = client.get("/api/notifications", params={"after": 1, "before": 5})
    assert response.status_code == 422 and "before" in response.json()["detail"]


def test_mark_one_read(client):
    first, second = publish("First"), publish("Second")
    response = client.post(f"/api/notifications/{first}/read")
    assert response.status_code == 200
    assert response.json() == {"seq": first, "read": True, "unread": 1}
    assert client.post(f"/api/notifications/{first}/read").json()["unread"] == 1
    items = {item["seq"]: item for item in client.get("/api/notifications").json()["items"]}
    assert items[first]["read_at"] is not None and items[second]["read_at"] is None


def test_unknown_notification_is_a_chinese_404(client):
    publish("First")
    response = client.post("/api/notifications/999/read")
    assert response.status_code == 404
    assert "通知不存在" in response.json()["detail"]
    assert client.get("/api/notifications").json()["unread"] == 1


def test_mark_all_read(client):
    first, second, third = publish("a"), publish("b"), publish("c")
    assert client.post("/api/notifications/read-all", params={"up_to": second}).json() == {"marked": 2, "unread": 1}
    assert client.post("/api/notifications/read-all").json() == {"marked": 1, "unread": 0}
    assert all(item["read_at"] for item in client.get("/api/notifications").json()["items"])
    assert first < second < third


@pytest.mark.parametrize("request_line", [
    ("get", "/api/notifications?after=-1"), ("get", "/api/notifications?after=abc"),
    ("get", "/api/notifications?limit=0"), ("get", "/api/notifications?limit=100000"),
    ("get", "/api/notifications?after=99999999999999999999999"),
    ("get", "/api/notifications?before=0"), ("get", "/api/notifications?before=-1"),
    ("get", "/api/notifications?before=99999999999999999999999"),
    ("post", "/api/notifications/abc/read"), ("post", "/api/notifications/0/read"),
    ("post", "/api/notifications/99999999999999999999999/read"),
    ("post", "/api/notifications/read-all?up_to=99999999999999999999999"),
])
def test_out_of_range_input_is_a_client_error(client, request_line):
    method, url = request_line
    assert getattr(client, method)(url).status_code == 422


@pytest.fixture
def held_lock(tmp_path, monkeypatch):
    """Another process holds the store's write lock longer than the busy timeout."""
    monkeypatch.setattr(desk_store, "BUSY_TIMEOUT_MS", 100)

    def hold():
        holder = sqlite3.connect(tmp_path / "desk.sqlite3", timeout=0, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        holders.append(holder)

    holders = []
    yield hold
    for holder in holders:
        holder.execute("ROLLBACK")
        holder.close()


def test_lock_contention_is_a_chinese_503(client, held_lock):
    seq = publish("First")
    held_lock()
    for response in (client.post(f"/api/notifications/{seq}/read"), client.post("/api/notifications/read-all")):
        assert response.status_code == 503, response.text
        assert "稍后" in response.json()["detail"]
    assert client.get("/api/notifications").json()["unread"] == 1, "reading is not blocked by a writer"


def test_store_newer_than_the_code_is_a_503(client):
    publish("First")
    desk_store.ensure_schema(notify.SCHEMA, notify.MIGRATIONS + [["CREATE TABLE later (id INTEGER PRIMARY KEY)"]])
    for response in (client.get("/api/notifications"), client.post("/api/notifications/1/read"),
                     client.post("/api/notifications/read-all")):
        assert response.status_code == 503, response.text


def test_router_is_registered_as_a_feature():
    mounted = {prefix: router for router, prefix, _tags in features.routers()}
    assert mounted.get("/api/notifications") is notifications.router
