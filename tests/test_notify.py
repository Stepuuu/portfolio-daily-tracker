"""Notifications are stored once, in order, and delivery can never break publishing."""
from datetime import datetime, timedelta
import logging
import threading
import time

import pytest

from core import desk_store, notify


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    monkeypatch.setattr(notify, "_deliverers", [])
    yield tmp_path
    assert notify.flush(5), "a delivery was still running when the test ended"


def publish(title="Example", **values):
    return notify.publish(values.pop("kind", "alert"), values.pop("level", "info"), title,
                          values.pop("body", "Example body"), **values)


def test_publish_stores_every_field():
    seq = publish("Stop reached", level="warning", data={"ticker": "NASDAQ:EXAMPLE", "rule_id": 7})
    [item] = notify.list()
    assert item == {"seq": seq, "kind": "alert", "level": "warning", "title": "Stop reached",
                    "body": "Example body", "data": {"ticker": "NASDAQ:EXAMPLE", "rule_id": 7},
                    "created_at": item["created_at"], "read_at": None}
    assert item["created_at"].endswith("+00:00")


def test_same_dedupe_key_is_stored_once():
    first = publish("First", dedupe_key="alert:7:2026-08-03:1")
    assert first is not None
    assert publish("Second, same event", dedupe_key="alert:7:2026-08-03:1") is None
    assert [item["title"] for item in notify.list()] == ["First"]
    assert publish("Other event", dedupe_key="alert:7:2026-08-03:2") is not None
    assert notify.unread_count() == 2


def test_notifications_without_a_key_are_never_merged():
    assert publish("Same text") != publish("Same text")
    assert len(notify.list()) == 2


def test_seq_grows_and_list_is_newest_first():
    seqs = [publish(f"n{index}") for index in range(5)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 5
    assert [item["seq"] for item in notify.list()] == seqs[::-1]
    # after_seq continues a poll: oldest first, so a page that is cut off leaves no gap.
    assert [item["seq"] for item in notify.list(after_seq=seqs[2])] == [seqs[3], seqs[4]]
    assert [item["seq"] for item in notify.list(limit=2)] == [seqs[4], seqs[3]]
    assert notify.list(after_seq=seqs[4]) == []
    assert notify.last_seq() == seqs[4]


def test_after_and_before_cursors_reach_every_row():
    seqs = [publish(f"n{index}") for index in range(7)]
    assert [item["seq"] for item in notify.list(after_seq=0, limit=3)] == seqs[:3]
    assert [item["seq"] for item in notify.list(after_seq=seqs[2], limit=3)] == seqs[3:6]
    assert [item["seq"] for item in notify.list(before_seq=seqs[4], limit=3)] == [seqs[3], seqs[2], seqs[1]]
    assert [item["seq"] for item in notify.list(before_seq=seqs[1])] == [seqs[0]]
    assert notify.list(before_seq=seqs[0]) == []
    assert notify.inbox(before_seq=seqs[1])["items"] == notify.list(before_seq=seqs[1])
    with pytest.raises(ValueError):
        notify.list(after_seq=1, before_seq=5)


def test_duplicate_does_not_consume_a_visible_seq():
    first = publish(dedupe_key="k")
    publish(dedupe_key="k")
    assert notify.last_seq() == first
    assert publish() > first


def test_read_state(monkeypatch):
    first, second, third = publish("a"), publish("b"), publish("c")
    assert notify.unread_count() == 3
    monkeypatch.setattr(desk_store, "now", lambda: "2026-08-03T01:00:00+00:00")
    assert notify.mark_read(second) is True
    assert notify.unread_count() == 2
    read_at = {item["seq"]: item["read_at"] for item in notify.list()}
    assert read_at == {first: None, second: "2026-08-03T01:00:00+00:00", third: None}
    monkeypatch.setattr(desk_store, "now", lambda: "2026-08-03T02:00:00+00:00")
    assert notify.mark_read(second) is True, "marking twice is harmless"
    assert {item["seq"]: item["read_at"] for item in notify.list()} == read_at, "and keeps the first read time"
    assert notify.mark_read(999) is False
    assert notify.mark_all_read() == 2
    assert notify.unread_count() == 0
    assert notify.mark_all_read() == 0


def test_mark_all_read_can_stop_at_what_the_user_saw():
    first, second = publish("seen"), publish("seen too")
    unseen = publish("arrived while the list was open")
    assert notify.mark_all_read(up_to_seq=second) == 2
    assert [item["seq"] for item in notify.list() if item["read_at"] is None] == [unseen]
    assert first < second < unseen


def test_inbox_matches_the_separate_readers():
    seqs = [publish(f"n{index}") for index in range(3)]
    notify.mark_read(seqs[0])
    assert notify.inbox(after_seq=seqs[0], limit=1) == {
        "items": notify.list(after_seq=seqs[0], limit=1), "unread": 2, "last_seq": seqs[2]}
    assert notify.inbox() == {"items": notify.list(), "unread": 2, "last_seq": seqs[2]}


def test_empty_store():
    assert notify.inbox() == {"items": [], "unread": 0, "last_seq": 0}


def test_reading_or_marking_a_missing_store_creates_nothing(desk):
    assert notify.inbox() == {"items": [], "unread": 0, "last_seq": 0}
    assert notify.list() == [] and notify.unread_count() == 0 and notify.last_seq() == 0
    assert notify.mark_read(1) is False and notify.mark_all_read() == 0
    assert list(desk.iterdir()) == []


@pytest.mark.parametrize("arguments", [
    ("alert", "urgent", "Title", "Body"),
    ("Alert Kind", "info", "Title", "Body"),
    ("alert", "info", "   ", "Body"),
    ("alert", "info", "Title", None),
])
def test_wrong_arguments_are_rejected_and_nothing_is_stored(arguments):
    with pytest.raises(ValueError):
        notify.publish(*arguments)
    assert notify.list() == []


@pytest.mark.parametrize("extra", [{"data": ["not", "an", "object"]}, {"data": {"when": object()}},
                                   {"data": {"big": "x" * 30000}}, {"dedupe_key": ""}])
def test_wrong_data_or_key_is_rejected(extra):
    with pytest.raises(ValueError):
        publish(**extra)
    assert notify.list() == []


class Clock:
    def __init__(self, monkeypatch, start="2026-01-01T00:00:00+00:00"):
        self.moment = datetime.fromisoformat(start)
        monkeypatch.setattr(desk_store, "now", lambda: self.moment.isoformat(timespec="seconds"))

    def advance(self, **delta):
        self.moment += timedelta(**delta)


def test_read_notifications_are_pruned_90_days_after_reading(monkeypatch):
    clock = Clock(monkeypatch)
    old_read, old_unread = publish("old, read"), publish("old, never read")
    notify.mark_read(old_read)
    clock.advance(days=89)
    recent = publish("read recently")
    notify.mark_read(recent)
    clock.advance(days=2)  # old_read was read 91 days ago, recent 2 days ago
    newest = publish("the write that prunes")
    assert [item["seq"] for item in notify.list()] == [newest, recent, old_unread]
    assert notify.last_seq() == newest


def test_pruning_is_bounded_per_write_and_keeps_the_newest_row(monkeypatch):
    monkeypatch.setattr(notify, "PRUNE_BATCH", 2)
    clock = Clock(monkeypatch)
    seqs = [publish(f"n{index}") for index in range(5)]
    assert notify.mark_all_read() == 5
    clock.advance(days=91)
    assert notify.mark_all_read() == 0  # a write with nothing to mark still prunes, two rows at most
    assert [item["seq"] for item in notify.list()] == seqs[:1:-1]
    notify.mark_all_read()
    assert [item["seq"] for item in notify.list()] == [seqs[4]]
    notify.mark_all_read()
    assert [item["seq"] for item in notify.list()] == [seqs[4]], "the newest row keeps last_seq stable"
    assert notify.last_seq() == seqs[4]
    assert publish("after") == seqs[4] + 1


def test_overlong_text_is_cut_not_lost():
    publish("t" * 1000, body="b" * 10000)
    [item] = notify.list()
    assert len(item["title"]) == notify.MAX_TITLE and len(item["body"]) == notify.MAX_BODY


def test_publish_joins_the_callers_transaction():
    desk_store.ensure_schema(notify.SCHEMA, notify.MIGRATIONS)  # create the table outside the transaction under test
    delivered = []
    notify.register_deliverer(delivered.append)
    with pytest.raises(RuntimeError):
        with desk_store.transaction():
            assert publish("rolled back") is not None
            raise RuntimeError("the caller's own write failed")
    assert notify.flush() and delivered == [] and notify.list() == []

    with desk_store.transaction():
        seq = publish("committed together")
        assert notify.flush() and delivered == [], "nothing is delivered before the commit"
    assert notify.flush()
    assert [item["seq"] for item in delivered] == [seq]


def test_deliverers_receive_committed_notifications_in_order():
    delivered = []

    def deliverer(notification):
        # The row must already be readable by anyone when a channel is told about it.
        stored = [item for item in notify.list() if item["seq"] == notification["seq"]]
        delivered.append((notification, stored))

    notify.register_deliverer(deliverer)
    notify.register_deliverer(deliverer)  # registering twice must not deliver twice
    seqs = [publish(f"n{index}", data={"index": index}) for index in range(5)]
    assert publish(dedupe_key="once") is not None and publish(dedupe_key="once") is None
    assert notify.flush()
    assert [notification["seq"] for notification, _ in delivered] == seqs + [seqs[-1] + 1]
    assert all(stored == [notification] for notification, stored in delivered)


def test_failing_deliverer_does_not_break_publish_or_other_deliverers(caplog):
    delivered = []

    def broken(notification):
        notification["title"] = "changed by a deliverer"
        raise RuntimeError("channel down: " + notification["body"])

    notify.register_deliverer(broken)
    notify.register_deliverer(delivered.append)
    with caplog.at_level(logging.WARNING, logger="core.notify"):
        seq = publish("Private title 12345.67", body="Private body 76543.21")
        assert seq is not None
        assert publish("Next") == seq + 1
        assert notify.flush()
    assert [item["title"] for item in delivered] == ["Private title 12345.67", "Next"]
    assert [item["seq"] for item in notify.list()] == [seq + 1, seq]
    assert "broken" in caplog.text and "RuntimeError" in caplog.text
    assert "12345.67" not in caplog.text and "76543.21" not in caplog.text


def test_slow_deliverer_does_not_block_publish():
    release = threading.Event()
    delivered = []

    def slow(notification):
        release.wait(10)
        delivered.append(notification["seq"])

    notify.register_deliverer(slow)
    started = time.monotonic()
    try:
        seqs = [publish(f"n{index}") for index in range(3)]
        assert time.monotonic() - started < 5
        assert delivered == [] and len(notify.list()) == 3
    finally:
        release.set()
    assert notify.flush()
    assert delivered == seqs


def wait_for(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_stalled_deliverer_does_not_delay_the_others():
    release, healthy = threading.Event(), []

    def stalled(notification):
        release.wait(10)

    notify.register_deliverer(stalled)  # registered first, so it would block the others in one queue
    notify.register_deliverer(healthy.append)
    try:
        seqs = [publish("a"), publish("b")]
        assert wait_for(lambda: len(healthy) == 2), "the healthy channel got nothing while the other one hung"
        assert [item["seq"] for item in healthy] == seqs
    finally:
        release.set()


def test_full_channel_drops_are_counted_and_logged_without_content(monkeypatch, caplog):
    monkeypatch.setattr(notify, "DELIVERY_QUEUE_SIZE", 2)
    entered, release, received = threading.Event(), threading.Event(), []

    def stalled(notification):
        entered.set()
        release.wait(10)

    def healthy(notification):
        received.append(notification)

    notify.register_deliverer(stalled)
    notify.register_deliverer(healthy)
    try:
        with caplog.at_level(logging.WARNING, logger="core.notify"):
            first = publish("Private title 12345.67", body="Private body 76543.21")
            assert entered.wait(5)
            rest = [publish("Private title 12345.67", body="Private body 76543.21") for _ in range(4)]
        assert wait_for(lambda: len(received) == 5), "the healthy channel has its own queue"
        stats = notify.delivery_stats()
        assert stats["stalled"]["dropped"] == 2 and stats["healthy"]["dropped"] == 0
    finally:
        release.set()
    assert notify.flush()
    assert notify.delivery_stats()["stalled"]["delivered"] == 3
    assert [item["seq"] for item in received] == [first, *rest]
    dropped = [record for record in caplog.records if "dropped" in record.getMessage()]
    assert len(dropped) == 2 and all("stalled" in record.getMessage() for record in dropped)
    assert str(rest[-1]) in dropped[-1].getMessage()
    assert "12345.67" not in caplog.text and "76543.21" not in caplog.text


def test_deliverers_that_accept_a_timeout_get_the_budget(monkeypatch, caplog):
    monkeypatch.setattr(notify, "DELIVERY_TIMEOUT", 0.05)
    budgets = []

    def bounded(notification, timeout):
        budgets.append(timeout)

    def slow(notification):
        time.sleep(0.2)

    notify.register_deliverer(bounded)
    notify.register_deliverer(slow)
    notify.register_deliverer(lambda notification, timeout=None: budgets.append(("lambda", timeout)), timeout=2.5)
    with caplog.at_level(logging.WARNING, logger="core.notify"):
        seq = publish("Private title 12345.67")
        assert notify.flush()
    assert sorted(map(str, budgets)) == sorted(map(str, [0.05, ("lambda", 2.5)]))
    assert notify.delivery_stats()["slow"]["slow"] == 1
    assert "slow" in caplog.text and str(seq) in caplog.text and "12345.67" not in caplog.text
    with pytest.raises(ValueError):
        notify.register_deliverer(lambda notification: None, timeout=0)


def test_unregistered_deliverer_is_not_called():
    delivered = []
    notify.register_deliverer(delivered.append)
    notify.unregister_deliverer(delivered.append)
    notify.unregister_deliverer(delivered.append)
    publish()
    assert notify.flush() and delivered == []
