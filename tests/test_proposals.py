"""Proposals: validated twice, applied by an explicit confirm only, and exactly once."""
from concurrent.futures import ThreadPoolExecutor
import contextvars
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time

import pytest

from core import desk_store, notify, proposals

NOTES = [["CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL UNIQUE)"]]


class NoteKind:
    """A fictional feature: confirmed proposals become rows in a notes table."""

    def __init__(self):
        self.applied = []      # (payload, proposal) for every apply call
        self.forbidden = set()
        self.fail_with = None
        self.delay = 0

    def validate(self, payload):
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise proposals.ProposalError("请填写备注内容。")
        if text.strip() in self.forbidden:
            raise proposals.ProposalError("这条备注现在不能添加。")
        return {"text": text.strip()}

    def describe(self, payload):
        return f"添加备注：{payload['text']}"

    def apply(self, payload, proposal):
        self.applied.append((payload, proposal))
        time.sleep(self.delay)
        desk_store.ensure_schema("notes", NOTES)
        with desk_store.transaction() as db:  # the feature's ordinary write path
            row_id = db.execute("INSERT INTO notes(text) VALUES (?)", (payload["text"],)).lastrowid
        notify.publish("note", "info", "Note added", "Example", dedupe_key=f"note:{proposal['id']}")
        if self.fail_with is not None:
            raise self.fail_with
        return {"note_id": row_id}


@pytest.fixture
def kind(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    monkeypatch.setattr(proposals, "_kinds", {})
    note = NoteKind()
    proposals.register_kind("note", note.validate, note.apply, note.describe)
    return note


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def notes():
    desk_store.ensure_schema("notes", NOTES)
    with desk_store.connect() as db:
        return [row["text"] for row in db.execute("SELECT text FROM notes ORDER BY id")]


def propose(text="Example note", **values):
    return proposals.propose("note", {"text": text}, values.pop("source", "agent:chat"), **values)


def test_propose_stores_a_description_and_applies_nothing(kind):
    proposal = propose("  Example note  ")
    assert proposal == {
        "id": proposal["id"], "kind": "note", "status": "pending", "source": "agent:chat",
        "summary": "添加备注：Example note", "payload": {"text": "Example note"},
        "created_at": proposal["created_at"], "expires_at": proposal["expires_at"],
        "resolved_at": None, "result": None, "error": None, "attempts": 0}
    assert proposal["expires_at"] > proposal["created_at"]
    assert proposals.get(proposal["id"]) == proposal
    assert proposals.list() == [proposal]
    assert proposals.pending_count() == 1
    assert kind.applied == [] and notes() == [] and notify.list() == []


def test_only_confirm_applies(kind):
    kept, dropped = propose("kept"), propose("dropped")
    proposals.list("all"), proposals.get(kept["id"]), proposals.pending_count()
    proposals.reject(dropped["id"])
    propose("kept")
    assert kind.applied == [] and notes() == []
    proposals.confirm(kept["id"])
    assert notes() == ["kept"]


def test_unknown_kind_is_refused_and_not_stored(kind):
    with pytest.raises(proposals.UnknownKind) as error:
        proposals.propose("order", {"text": "Example"}, "agent:chat")
    assert "未知的方案类型" in str(error.value)
    assert proposals.list("all") == []


@pytest.mark.parametrize("payload", [{"text": ""}, {}, {"text": 5}, "not an object", None, ["text"]])
def test_invalid_payload_is_refused_at_propose(kind, payload):
    with pytest.raises(proposals.InvalidProposal):
        proposals.propose("note", payload, "agent:chat")
    assert proposals.list("all") == []


def test_validator_crash_is_an_invalid_proposal_without_internals(kind, caplog):
    def crash(payload):
        return {"text": payload["missing-field-76543.21"]}

    proposals.register_kind("crashy", crash, kind.apply, kind.describe)
    with caplog.at_level(logging.WARNING, logger="core.proposals"):
        with pytest.raises(proposals.InvalidProposal) as error:
            proposals.propose("crashy", {"text": "Example"}, "agent:chat")
    assert "76543.21" not in str(error.value) and "方案内容" in str(error.value)
    assert "KeyError" in caplog.text and "76543.21" not in caplog.text


def test_bad_registration_and_source_are_programming_errors(kind):
    async def later(payload):
        return payload

    with pytest.raises(TypeError):
        proposals.register_kind("slow", kind.validate, later, kind.describe)
    with pytest.raises(ValueError):
        proposals.register_kind("Bad Kind", kind.validate, kind.apply, kind.describe)
    with pytest.raises(ValueError):
        propose(source="Somebody else's words")
    with pytest.raises(ValueError):
        propose(ttl_seconds=0)
    assert proposals.kinds() == ["note"] and proposals.list("all") == []


def test_registering_the_same_functions_again_changes_nothing(kind):
    proposals.register_kind("note", kind.validate, kind.apply, kind.describe)
    proposals.register_kind("note", kind.validate, kind.apply, kind.describe, version="1")
    assert proposals.kinds() == ["note"]
    assert proposals.confirm(propose()["id"])["status"] == "applied" and len(kind.applied) == 1


def test_another_registration_for_a_known_kind_is_refused(kind):
    deleted = []

    def delete_note(payload, proposal):
        deleted.append(payload)

    proposal = propose()
    with pytest.raises(ValueError):
        proposals.register_kind("note", kind.validate, delete_note, kind.describe)
    with pytest.raises(ValueError):
        proposals.register_kind("note", kind.validate, kind.apply, kind.describe, version="2")
    proposals.confirm(proposal["id"])
    assert deleted == [] and notes() == ["Example note"], "the first registration still applies"


def test_confirm_refuses_a_proposal_made_under_another_version(kind, monkeypatch):
    proposal = propose()
    monkeypatch.setattr(proposals, "_kinds", {})  # a restart with a newer release of the feature
    replaced = []
    proposals.register_kind("note", kind.validate, lambda payload, proposal: replaced.append(payload),
                            kind.describe, version="2")
    with pytest.raises(proposals.Conflict) as error:
        proposals.confirm(proposal["id"])
    assert "版本" in str(error.value)
    assert replaced == [] and kind.applied == [] and notes() == []
    stored = proposals.get(proposal["id"])
    assert stored["status"] == "pending" and stored["error"] == str(error.value)
    assert proposals.reject(proposal["id"])["status"] == "rejected"

    again = propose()  # the same payload under the new version is a new proposal
    assert again["id"] != proposal["id"]
    assert proposals.confirm(again["id"])["status"] == "applied" and replaced == [{"text": "Example note"}]


def test_pending_duplicate_is_matched_within_one_version(kind, monkeypatch):
    first = propose()
    monkeypatch.setattr(proposals, "_kinds", {})
    proposals.register_kind("note", kind.validate, kind.apply, kind.describe, version="2")
    assert propose()["id"] != first["id"]


@pytest.mark.parametrize("version", ["", "x" * 33, "with space", "版本", 1, None])
def test_version_tag_is_short_text(kind, version):
    with pytest.raises((TypeError, ValueError)):
        proposals.register_kind("other", kind.validate, kind.apply, kind.describe, version=version)
    assert proposals.kinds() == ["note"]


def test_confirm_applies_the_stored_payload_once(kind):
    proposal = propose("  Example note ", source="routine:postmarket")
    applied = proposals.confirm(proposal["id"])
    assert applied["status"] == "applied" and applied["result"] == {"note_id": 1}
    assert applied["resolved_at"] is not None and applied["error"] is None
    assert notes() == ["Example note"]
    [(payload, seen)] = kind.applied
    assert payload == {"text": "Example note"}
    assert seen["id"] == proposal["id"] and seen["source"] == "routine:postmarket" and seen["kind"] == "note"

    assert proposals.confirm(proposal["id"]) == applied, "a second click returns the stored result"
    assert len(kind.applied) == 1 and notes() == ["Example note"]
    assert proposals.list() == [] and proposals.list("applied") == [applied]


def test_concurrent_confirms_apply_exactly_once(kind):
    proposal = propose()
    kind.delay = 0.3
    start = threading.Barrier(4)

    def click(_):
        start.wait(5)
        return proposals.confirm(proposal["id"])

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(click, range(4)))
    assert len(kind.applied) == 1
    assert notes() == ["Example note"]
    assert all(result == results[0] for result in results)
    assert results[0]["status"] == "applied" and results[0]["result"] == {"note_id": 1}
    assert len(notify.list()) == 1


LOG = [["CREATE TABLE log (id INTEGER PRIMARY KEY, proposal TEXT NOT NULL)"]]


def logged_rows():
    desk_store.ensure_schema("log", LOG)
    with desk_store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM log").fetchone()[0]


def log_write(proposal):
    desk_store.ensure_schema("log", LOG)
    with desk_store.transaction() as db:  # no UNIQUE constraint: a second apply would add a second row
        db.execute("INSERT INTO log(proposal) VALUES (?)", (proposal["id"],))


def test_confirm_from_inside_apply_is_a_conflict_and_applies_once(kind):
    calls, refused = [], []

    def apply(payload, proposal):
        calls.append(proposal["id"])
        if len(calls) == 1:
            try:
                proposals.confirm(proposal["id"])
            except proposals.Conflict as exc:
                refused.append(str(exc))
        log_write(proposal)
        return {"rows": 1}

    proposals.register_kind("logged", kind.validate, apply, kind.describe)
    proposal = proposals.propose("logged", {"text": "Example"}, "agent:chat")
    applied = proposals.confirm(proposal["id"])
    assert applied["status"] == "applied" and applied["result"] == {"rows": 1}
    assert len(calls) == 1 and len(refused) == 1 and "正在应用" in refused[0]
    assert logged_rows() == 1


def test_uncaught_reentrant_confirm_fails_the_apply(kind):
    calls = []

    def apply(payload, proposal):
        calls.append(proposal["id"])
        log_write(proposal)
        if len(calls) == 1:
            proposals.confirm(proposal["id"])

    proposals.register_kind("logged", kind.validate, apply, kind.describe)
    proposal = proposals.propose("logged", {"text": "Example"}, "agent:chat")
    with pytest.raises(proposals.ApplyFailed) as error:
        proposals.confirm(proposal["id"])
    assert "正在应用" in str(error.value)
    assert len(calls) == 1 and logged_rows() == 0
    assert proposals.get(proposal["id"])["status"] == "pending"


def test_confirm_from_a_thread_started_by_apply_is_a_conflict(kind):
    outcome = []

    def attempt(proposal_id):
        try:
            outcome.append(proposals.confirm(proposal_id)["status"])
        except proposals.ProposalError as exc:
            outcome.append(type(exc).__name__)

    def apply(payload, proposal):
        context = contextvars.copy_context()  # what asyncio.to_thread and create_task do
        worker = threading.Thread(target=context.run, args=(attempt, proposal["id"]))
        worker.start()
        worker.join(30)
        log_write(proposal)

    proposals.register_kind("logged", kind.validate, apply, kind.describe)
    proposal = proposals.propose("logged", {"text": "Example"}, "agent:chat")
    started = time.monotonic()
    assert proposals.confirm(proposal["id"])["status"] == "applied"
    assert time.monotonic() - started < desk_store.BUSY_TIMEOUT_MS / 2000, "refused at once, not after a lock wait"
    assert outcome == ["Conflict"] and logged_rows() == 1


CONFIRMER = """
import json, os, sys, time
from core import desk_store, proposals

def apply(payload, proposal):
    with desk_store.transaction() as db:
        db.execute("INSERT INTO log(proposal) VALUES (?)", (proposal["id"],))
    time.sleep(0.5)
    return {"pid": os.getpid()}

proposals.register_kind("logged", lambda payload: payload, apply, lambda payload: "记录")
print(json.dumps(proposals.confirm(sys.argv[1])["result"]))
"""


def test_confirms_from_other_processes_apply_once(kind, tmp_path):
    proposals.register_kind("logged", lambda payload: payload, lambda payload, proposal: None, lambda payload: "记录")
    proposal = proposals.propose("logged", {"text": "Example"}, "agent:chat")
    assert logged_rows() == 0  # the table exists before the race
    dashboard = Path(desk_store.__file__).resolve().parents[1]
    environment = {**os.environ, "PORTFOLIO_DIR": str(tmp_path), "PYTHONPATH": str(dashboard)}
    workers = [subprocess.Popen([sys.executable, "-c", CONFIRMER, proposal["id"]], env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
    outputs = [worker.communicate(timeout=120) for worker in workers]
    assert [worker.returncode for worker in workers] == [0, 0, 0], [error for _, error in outputs]
    assert len({output for output, _ in outputs}) == 1, "every process returns the stored result"
    assert logged_rows() == 1


LESSON_KIND = """
import os
from pathlib import Path
from core import proposals

def lesson_kind(target, crash_at=None, calls=None):
    target = Path(target)

    def apply(payload, proposal):
        def write():
            if calls is not None:
                calls.append(proposal["id"])
            # Idempotent, keyed by the proposal id: an earlier attempt may have written already.
            lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
            if not any(line.startswith(proposal["id"] + "\\t") for line in lines):
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write(proposal["id"] + "\\t" + payload["text"] + "\\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            if crash_at == "after-write":
                os._exit(3)  # dies before the effect is recorded
            return {"file": target.name}

        result = proposals.external_effect(proposal, "lesson_file", write)
        if crash_at == "after-record":
            os._exit(3)  # dies before the status commit
        if crash_at == "raise":
            raise RuntimeError("a later step failed")
        return result

    proposals.register_kind("lesson", lambda payload: {"text": payload["text"]}, apply,
                            lambda payload: "记一条复盘：" + payload["text"])
"""

LESSON_CONFIRMER = LESSON_KIND + """
import sys
lesson_kind(sys.argv[2], sys.argv[3])
print(proposals.confirm(sys.argv[1])["status"])
"""


def lesson_lines(path):
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def recorded_effects(directory):
    import sqlite3
    with sqlite3.connect(directory / "desk-effects.sqlite3") as db:
        return db.execute("SELECT proposal_id, name, result FROM effects").fetchall()


@pytest.mark.parametrize("crash_at", ["after-write", "after-record"])
def test_external_write_survives_a_crash_before_the_status_commit(kind, tmp_path, crash_at):
    namespace = {}
    exec(LESSON_KIND, namespace)
    lessons = tmp_path / "lessons.txt"
    namespace["lesson_kind"](lessons)
    proposal = proposals.propose("lesson", {"text": "Example lesson"}, "agent:chat")

    dashboard = Path(desk_store.__file__).resolve().parents[1]
    environment = {**os.environ, "PORTFOLIO_DIR": str(tmp_path), "PYTHONPATH": str(dashboard)}

    def run(point):
        return subprocess.run([sys.executable, "-c", LESSON_CONFIRMER, proposal["id"], str(lessons), point],
                              env=environment, capture_output=True, text=True, timeout=120)

    crashed = run(crash_at)
    assert crashed.returncode == 3, crashed.stderr
    assert lesson_lines(lessons) == [proposal["id"] + "\tExample lesson"]
    assert proposals.get(proposal["id"])["status"] == "pending", "the crash came before the status commit"

    retried = run("none")
    assert retried.returncode == 0 and retried.stdout.strip() == "applied", retried.stderr
    assert lesson_lines(lessons) == [proposal["id"] + "\tExample lesson"], "written once"
    assert proposals.get(proposal["id"])["result"] == {"file": "lessons.txt"}
    assert recorded_effects(tmp_path) == [(proposal["id"], "lesson_file", '{"file":"lessons.txt"}')]
    assert mode(tmp_path / "desk-effects.sqlite3") == 0o600


def test_recorded_external_write_is_not_repeated_after_a_failed_apply(kind, tmp_path):
    namespace = {}
    exec(LESSON_KIND, namespace)
    lessons, calls = tmp_path / "lessons.txt", []
    namespace["lesson_kind"](lessons, "raise", calls)
    proposal = proposals.propose("lesson", {"text": "Example lesson"}, "agent:chat")
    with pytest.raises(proposals.ApplyFailed):
        proposals.confirm(proposal["id"])
    assert len(calls) == 1 and len(lesson_lines(lessons)) == 1
    assert proposals.get(proposal["id"])["status"] == "pending"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(proposals, "_kinds", {})
    try:
        namespace["lesson_kind"](lessons, None, calls)
        assert proposals.confirm(proposal["id"])["status"] == "applied"
    finally:
        monkeypatch.undo()
    assert len(calls) == 1, "the recorded effect is reused, write() is not called again"
    assert len(lesson_lines(lessons)) == 1


def test_external_effect_is_only_for_a_running_confirm(kind):
    proposal = propose()
    with pytest.raises(RuntimeError):
        proposals.external_effect(proposal, "lesson_file", lambda: None)


class Clock:
    """Moves ``proposals._now`` (expiry) and ``desk_store.now`` (resolved_at) together."""

    def __init__(self, monkeypatch, start=1_800_000_000.0):
        self.moment = start
        monkeypatch.setattr(proposals, "_now", lambda: self.moment)
        monkeypatch.setattr(desk_store, "now", lambda: datetime.fromtimestamp(
            self.moment, timezone.utc).isoformat(timespec="seconds"))

    def advance(self, days):
        self.moment += days * 24 * 3600


def test_resolved_proposals_are_pruned_90_days_after_resolution(kind, monkeypatch, tmp_path):
    clock = Clock(monkeypatch)
    fail = []

    def apply(payload, proposal):
        result = proposals.external_effect(proposal, "outside", lambda: {"done": True})
        if fail:
            raise RuntimeError("a later step failed")
        return result

    proposals.register_kind("outside", kind.validate, apply, kind.describe)
    applied = proposals.propose("outside", {"text": "applied"}, "agent:chat")
    proposals.confirm(applied["id"])
    rejected = propose("rejected")
    proposals.reject(rejected["id"])
    lapsed = propose("lapsed", ttl_seconds=60)  # expires and is never marked
    waiting = proposals.propose("outside", {"text": "waiting"}, "agent:chat", ttl_seconds=proposals.MAX_TTL_SECONDS)
    fail.append(True)
    with pytest.raises(proposals.ApplyFailed):
        proposals.confirm(waiting["id"])  # pending, with a recorded external effect
    clock.advance(days=89)
    recent = propose("recent")
    proposals.reject(recent["id"])
    clock.advance(days=2)
    trigger = propose("the write that prunes")

    assert {item["id"] for item in proposals.list("all")} == {waiting["id"], recent["id"], trigger["id"]}
    with pytest.raises(proposals.NotFound):
        proposals.get(applied["id"])
    assert [row[0] for row in recorded_effects(tmp_path)] == [waiting["id"]], "effects go with their proposal"
    assert proposals.get(waiting["id"])["status"] == "pending"


def test_proposal_pruning_is_bounded_per_write(kind, monkeypatch):
    monkeypatch.setattr(proposals, "PRUNE_BATCH", 1)
    clock = Clock(monkeypatch)
    old = [propose(f"old {index}") for index in range(3)]
    for proposal in old:
        proposals.reject(proposal["id"])
    clock.advance(days=91)
    propose("first write")
    assert len(proposals.list("rejected")) == 2
    propose("second write")
    assert [item["id"] for item in proposals.list("rejected")] == [old[2]["id"]]


def test_confirm_validates_again(kind):
    proposal = propose()
    kind.forbidden.add("Example note")  # the world changed after the proposal was made
    with pytest.raises(proposals.InvalidProposal) as error:
        proposals.confirm(proposal["id"])
    assert str(error.value) == "这条备注现在不能添加。"
    pending = proposals.get(proposal["id"])
    assert pending["status"] == "pending" and pending["error"] == "这条备注现在不能添加。"
    assert pending["attempts"] == 1
    assert kind.applied == [] and notes() == []

    kind.forbidden.clear()
    applied = proposals.confirm(proposal["id"])
    assert applied["status"] == "applied" and applied["error"] is None
    assert len(kind.applied) == 1


def test_failed_apply_leaves_it_pending_and_undoes_partial_writes(kind, caplog):
    proposal = propose()
    kind.fail_with = RuntimeError("disk said no 76543.21")
    with caplog.at_level(logging.WARNING, logger="core.proposals"):
        with pytest.raises(proposals.ApplyFailed) as error:
            proposals.confirm(proposal["id"])
    assert error.value.expected is False
    assert "76543.21" not in str(error.value) and "76543.21" not in caplog.text
    assert proposal["id"] in caplog.text and "RuntimeError" in caplog.text
    pending = proposals.get(proposal["id"])
    assert pending["status"] == "pending" and pending["result"] is None
    assert pending["error"] == str(error.value) and pending["attempts"] == 1
    assert notes() == [], "the insert made before the failure must be rolled back"
    assert notify.list() == [], "so must the notification"
    assert proposals.list() == [pending]

    kind.fail_with = None
    applied = proposals.confirm(proposal["id"])
    assert applied["status"] == "applied" and applied["error"] is None and applied["attempts"] == 1
    assert notes() == ["Example note"] and len(notify.list()) == 1
    assert len(kind.applied) == 2, "one failed attempt and one success"


def test_apply_refusal_keeps_the_kinds_own_message(kind):
    proposal = propose()
    kind.fail_with = proposals.ProposalError("备注已满，请先清理。")
    with pytest.raises(proposals.ApplyFailed) as error:
        proposals.confirm(proposal["id"])
    assert error.value.expected is True and str(error.value) == "备注已满，请先清理。"
    assert proposals.get(proposal["id"])["error"] == "备注已满，请先清理。"
    assert notes() == []


def test_apply_result_must_be_json(kind):
    proposals.register_kind("opaque", kind.validate, lambda payload, proposal: object(), kind.describe)
    proposal = proposals.propose("opaque", {"text": "Example"}, "agent:chat")
    with pytest.raises(proposals.ApplyFailed):
        proposals.confirm(proposal["id"])
    assert proposals.get(proposal["id"])["status"] == "pending"


def test_reject(kind):
    proposal = propose()
    rejected = proposals.reject(proposal["id"])
    assert rejected["status"] == "rejected" and rejected["resolved_at"] is not None
    assert proposals.reject(proposal["id"]) == rejected, "rejecting twice changes nothing"
    with pytest.raises(proposals.Conflict):
        proposals.confirm(proposal["id"])
    assert kind.applied == [] and notes() == []
    assert proposals.list() == [] and proposals.list("rejected") == [rejected]


def test_applied_proposal_cannot_be_rejected(kind):
    proposal = propose()
    applied = proposals.confirm(proposal["id"])
    with pytest.raises(proposals.Conflict):
        proposals.reject(proposal["id"])
    assert proposals.get(proposal["id"]) == applied


def test_expired_proposal_cannot_be_confirmed(kind, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(proposals, "_now", lambda: clock[0])
    proposal = propose(ttl_seconds=60)
    fresh = propose("still fresh", ttl_seconds=3600)
    clock[0] += 59
    assert [item["id"] for item in proposals.list()] == [fresh["id"], proposal["id"]]
    clock[0] += 1
    assert [item["id"] for item in proposals.list()] == [fresh["id"]]
    assert proposals.pending_count() == 1
    assert [item["id"] for item in proposals.list("expired")] == [proposal["id"]]
    assert proposals.get(proposal["id"])["status"] == "expired"
    with pytest.raises(proposals.Conflict) as error:
        proposals.confirm(proposal["id"])
    assert "过期" in str(error.value)
    assert kind.applied == [] and notes() == []
    assert proposals.reject(proposal["id"])["status"] == "expired"

    clock[0] -= 3600  # a clock that jumps back must not revive it
    with pytest.raises(proposals.Conflict):
        proposals.confirm(proposal["id"])
    assert kind.applied == []
    assert proposals.confirm(fresh["id"])["status"] == "applied"


def test_default_expiry_is_a_week(kind, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(proposals, "_now", lambda: clock[0])
    proposal = propose()
    clock[0] += proposals.DEFAULT_TTL_SECONDS - 1
    assert proposals.get(proposal["id"])["status"] == "pending"
    clock[0] += 1
    assert proposals.get(proposal["id"])["status"] == "expired"


def test_list_filters_by_status_newest_first(kind):
    first, second, third, fourth = (propose(f"note {index}") for index in range(4))
    proposals.confirm(second["id"])
    proposals.reject(third["id"])
    assert [item["id"] for item in proposals.list()] == [fourth["id"], first["id"]]
    assert [item["id"] for item in proposals.list("pending", limit=1)] == [fourth["id"]]
    assert [item["id"] for item in proposals.list("applied")] == [second["id"]]
    assert [item["id"] for item in proposals.list("rejected")] == [third["id"]]
    assert proposals.list("expired") == []
    assert [item["id"] for item in proposals.list("all")] == [p["id"] for p in (fourth, third, second, first)]
    assert proposals.list(None) == proposals.list("all")
    with pytest.raises(proposals.ProposalError):
        proposals.list("everything")


def test_same_pending_proposal_is_not_stored_twice(kind):
    first = propose("Example note")
    assert propose(" Example note ", source="routine:postmarket") == first
    assert len(proposals.list()) == 1
    other = propose("Another note")
    assert other["id"] != first["id"]
    proposals.confirm(first["id"])
    again = propose("Example note")
    assert again["id"] != first["id"] and again["status"] == "pending"


def test_pending_proposals_are_capped(kind, monkeypatch):
    monkeypatch.setattr(proposals, "MAX_PENDING", 3)
    made = [propose(f"note {index}") for index in range(3)]
    with pytest.raises(proposals.ProposalError) as error:
        propose("one too many")
    assert "待确认" in str(error.value)
    assert len(proposals.list("all")) == 3
    proposals.reject(made[0]["id"])
    assert propose("room again")["status"] == "pending"


def test_missing_proposal(kind):
    for operation in (proposals.get, proposals.confirm, proposals.reject):
        with pytest.raises(proposals.NotFound):
            operation("0" * 32)
        with pytest.raises(proposals.NotFound):
            operation(None)


def test_reading_a_missing_store_creates_nothing(kind, tmp_path):
    assert proposals.list() == [] and proposals.list("all") == [] and proposals.pending_count() == 0
    for operation in (proposals.get, proposals.confirm, proposals.reject):
        with pytest.raises(proposals.NotFound):
            operation("0" * 32)
    assert list(tmp_path.iterdir()) == []


def test_proposal_outlives_the_process_that_made_it(kind, monkeypatch):
    proposal = propose()
    monkeypatch.setattr(proposals, "_kinds", {})  # a restart in which the feature is not loaded
    assert proposals.list() == [proposal]
    with pytest.raises(proposals.UnknownKind):
        proposals.confirm(proposal["id"])
    assert proposals.get(proposal["id"]) == proposal
    proposals.register_kind("note", kind.validate, kind.apply, kind.describe)
    assert proposals.confirm(proposal["id"])["status"] == "applied"
    assert len(kind.applied) == 1
