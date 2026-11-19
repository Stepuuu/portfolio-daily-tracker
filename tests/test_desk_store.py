"""The shared desk store: private file, run-once migrations, atomic nested writes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
import logging
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest

from core import desk_store

NOTES = [["CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"]]


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    return tmp_path


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def add(text):
    desk_store.ensure_schema("notes", NOTES)
    with desk_store.transaction() as db:
        return db.execute("INSERT INTO notes(text) VALUES (?)", (text,)).lastrowid


def texts():
    desk_store.ensure_schema("notes", NOTES)
    with desk_store.connect() as db:
        return [row["text"] for row in db.execute("SELECT text FROM notes ORDER BY id")]


def test_file_is_private_and_in_wal_mode(desk):
    add("first")
    target = desk / "desk.sqlite3"
    assert desk_store.path() == target
    assert mode(target) == 0o600
    with sqlite3.connect(target) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with desk_store.connect():
        side_files = [path for path in desk.iterdir() if path.name != "desk.sqlite3"]
        assert side_files, "WAL mode keeps side files while a connection is open"
        assert all(mode(path) == 0o600 for path in side_files)


def test_pinned_now_stamps_what_is_written_inside_the_block(desk):
    from datetime import datetime, timedelta, timezone
    moment = datetime(2026, 11, 2, 9, 30, tzinfo=timezone(timedelta(hours=8)))
    with desk_store.pinned_now(moment):
        assert desk_store.now() == "2026-11-02T01:30:00+00:00"
        with desk_store.pinned_now(moment + timedelta(days=1)):
            assert desk_store.now() == "2026-11-03T01:30:00+00:00"   # the innermost block wins
        assert desk_store.now() == "2026-11-02T01:30:00+00:00"
        desk_store.ensure_schema("stamps", [["CREATE TABLE stamps (at TEXT NOT NULL)"]])
        with desk_store.transaction() as db:
            db.execute("INSERT INTO stamps(at) VALUES (?)", (desk_store.now(),))
    assert desk_store.now() != "2026-11-02T01:30:00+00:00", "the pin ends with the block"
    with desk_store.connect() as db:
        assert [row["at"] for row in db.execute("SELECT at FROM stamps")] == ["2026-11-02T01:30:00+00:00"]
    with pytest.raises(RuntimeError):
        with desk_store.pinned_now(moment):
            raise RuntimeError("stop")
    assert desk_store.now() != "2026-11-02T01:30:00+00:00", "also after a block that raised"
    with pytest.raises(ValueError):
        with desk_store.pinned_now(datetime(2026, 11, 2, 9, 30)):   # no time zone: refused
            pass


def test_existing_file_with_loose_mode_is_tightened(desk):
    target = desk / "desk.sqlite3"
    target.touch(mode=0o644)
    os.chmod(target, 0o644)
    add("first")
    assert mode(target) == 0o600


def sidecars(desk):
    return [desk / "desk.sqlite3-wal", desk / "desk.sqlite3-shm"]


def test_existing_loose_side_files_are_tightened(desk):
    target = desk / "desk.sqlite3"
    target.touch(mode=0o644)
    os.chmod(target, 0o644)
    other = sqlite3.connect(target)  # another program keeps the file open in WAL mode
    try:
        other.execute("PRAGMA journal_mode=WAL")
        other.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        assert [mode(path) for path in sidecars(desk)] == [0o644, 0o644]
        add("first")
        assert [mode(path) for path in [target, *sidecars(desk)]] == [0o600] * 3
    finally:
        other.close()


def test_side_files_are_tightened_on_every_connection(desk):
    add("first")
    holder = sqlite3.connect(desk / "desk.sqlite3")  # keeps the side files in place
    try:
        holder.execute("SELECT 1 FROM notes").fetchall()
        for path in [desk / "desk.sqlite3", *sidecars(desk)]:
            os.chmod(path, 0o644)  # loosened after the first use, for example by a restore
        assert texts() == ["first"]  # reading is enough
        assert [mode(path) for path in [desk / "desk.sqlite3", *sidecars(desk)]] == [0o600] * 3
    finally:
        holder.close()


def test_missing_portfolio_directory_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path / "not" / "yet"))
    add("first")
    assert texts() == ["first"]


def test_schema_steps_run_exactly_once(desk):
    # CREATE TABLE without IF NOT EXISTS fails if a step were applied twice.
    assert desk_store.ensure_schema("notes", NOTES) == 1
    assert desk_store.ensure_schema("notes", NOTES) == 1
    add("kept")
    longer = NOTES + [["ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"]]
    assert desk_store.ensure_schema("notes", longer) == 2
    assert desk_store.ensure_schema("notes", longer) == 2
    assert desk_store.schema_version("notes") == 2
    with desk_store.connect() as db:
        assert [tuple(row) for row in db.execute("SELECT text, pinned FROM notes")] == [("kept", 0)]


def test_schemas_of_different_features_are_independent(desk):
    desk_store.ensure_schema("notes", NOTES)
    assert desk_store.schema_version("other") == 0
    assert desk_store.ensure_schema("other", [["CREATE TABLE other (id INTEGER PRIMARY KEY)"]]) == 1
    assert desk_store.schema_version("notes") == 1


def test_failed_step_leaves_no_partial_schema(desk):
    broken = [["CREATE TABLE half (id INTEGER PRIMARY KEY)", "THIS IS NOT SQL"]]
    with pytest.raises(sqlite3.Error):
        desk_store.ensure_schema("half", broken)
    assert desk_store.schema_version("half") == 0
    with desk_store.connect() as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='half'").fetchone() is None


def test_file_newer_than_the_code_is_refused(desk):
    desk_store.ensure_schema("notes", NOTES + [["ALTER TABLE notes ADD COLUMN pinned INTEGER"]])
    with pytest.raises(desk_store.DeskStoreError):
        desk_store.ensure_schema("notes", NOTES)
    assert desk_store.schema_version("notes") == 2


def test_invalid_schema_name_is_refused(desk):
    with pytest.raises(desk_store.DeskStoreError):
        desk_store.ensure_schema("Notes; DROP", NOTES)


def test_store_follows_portfolio_dir_at_call_time(tmp_path, monkeypatch):
    first, second = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("PORTFOLIO_DIR", str(first))
    add("in a")
    monkeypatch.setenv("PORTFOLIO_DIR", str(second))
    assert desk_store.path() == second / "desk.sqlite3"
    assert texts() == []
    add("in b")
    monkeypatch.setenv("PORTFOLIO_DIR", str(first))
    assert texts() == ["in a"]
    assert mode(second / "desk.sqlite3") == 0o600


def test_deleted_file_is_recreated(desk):
    add("gone")
    for path in desk.iterdir():
        path.unlink()
    assert texts() == []
    assert mode(desk / "desk.sqlite3") == 0o600


def test_reading_a_missing_store_creates_nothing(desk):
    assert desk_store.ready("notes", NOTES) is False
    assert desk_store.schema_version("notes") == 0
    with pytest.raises(desk_store.DeskStoreError):
        with desk_store.connect():
            pass
    assert list(desk.iterdir()) == []


def test_reading_an_empty_file_adds_no_tables(desk):
    target = desk / "desk.sqlite3"
    target.touch(mode=0o600)
    assert desk_store.ready("notes", NOTES) is False
    assert desk_store.schema_version("notes") == 0
    assert target.stat().st_size == 0


def test_ready_reports_but_never_migrates(desk):
    add("kept")
    longer = NOTES + [["ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"]]
    assert desk_store.ready("notes", NOTES) is True
    assert desk_store.ready("other", [["CREATE TABLE other (id INTEGER PRIMARY KEY)"]]) is False
    with pytest.raises(desk_store.DeskStoreError):
        desk_store.ready("notes", longer)  # older than the code: upgraded at start-up or by a write
    with pytest.raises(desk_store.DeskStoreError):
        desk_store.ready("notes", [])  # newer than the code
    assert desk_store.schema_version("notes") == 1
    with desk_store.connect() as db:
        assert "pinned" not in [row["name"] for row in db.execute("PRAGMA table_info(notes)")]
    with pytest.raises(desk_store.DeskStoreError):
        desk_store.ready("Bad Name", NOTES)


def start_up():
    from backend import features

    async def run():
        async with features.background(None):
            pass

    asyncio.run(run())


def test_start_up_migrates_an_existing_store_outside_demo_mode(desk, monkeypatch):
    monkeypatch.setattr(desk_store, "_registered", {})
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    longer = NOTES + [["ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"]]
    desk_store.register_schema("notes", longer)
    desk_store.register_schema("notes", longer)  # the same registration again changes nothing
    with pytest.raises(ValueError):
        desk_store.register_schema("notes", NOTES)  # a different history under the same name

    start_up()
    assert list(desk.iterdir()) == [], "start-up never creates the store"

    add("kept")  # version 1, written by an older release
    monkeypatch.setenv("TRACKER_DEMO_MODE", "1")
    start_up()
    assert desk_store.schema_version("notes") == 1, "the demo never migrates"

    monkeypatch.delenv("TRACKER_DEMO_MODE")
    start_up()
    assert desk_store.schema_version("notes") == 2
    assert desk_store.ready("notes", longer) is True


def test_start_up_survives_a_store_it_cannot_migrate(desk, monkeypatch, caplog):
    monkeypatch.setattr(desk_store, "_registered", {})
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    desk_store.ensure_schema("notes", NOTES + [["ALTER TABLE notes ADD COLUMN pinned INTEGER"]])
    desk_store.register_schema("notes", NOTES)  # this code is older than the file
    with caplog.at_level(logging.WARNING, logger="core.desk_store"):
        start_up()
    assert "notes" in caplog.text and desk_store.schema_version("notes") == 2


def test_read_connection_refuses_writes(desk):
    add("first")
    with pytest.raises(sqlite3.OperationalError):
        with desk_store.connect() as db:
            db.execute("INSERT INTO notes(text) VALUES ('sneaky')")
    assert texts() == ["first"]


def test_transaction_rolls_back_when_the_block_raises(desk):
    desk_store.ensure_schema("notes", NOTES)
    with pytest.raises(RuntimeError):
        with desk_store.transaction() as db:
            db.execute("INSERT INTO notes(text) VALUES ('lost')")
            raise RuntimeError("stop")
    assert texts() == []


def test_nested_write_joins_the_open_transaction(desk):
    desk_store.ensure_schema("notes", NOTES)
    with pytest.raises(RuntimeError):
        with desk_store.transaction() as outer:
            add("inner")  # opens its own transaction() when called on its own
            with desk_store.transaction() as inner:
                assert inner is outer
            assert texts() == ["inner"]  # the block reads its own uncommitted write
            raise RuntimeError("stop")
    assert texts() == [], "the nested write must be undone with the outer block"


def test_failed_nested_block_undoes_only_itself(desk):
    desk_store.ensure_schema("notes", NOTES)
    with desk_store.transaction() as db:
        db.execute("INSERT INTO notes(text) VALUES ('before')")
        with pytest.raises(RuntimeError):
            with desk_store.transaction():
                add("inside")
                raise RuntimeError("stop")
        db.execute("INSERT INTO notes(text) VALUES ('after')")
    assert texts() == ["before", "after"]


def test_schema_can_be_created_inside_an_open_transaction(desk):
    with desk_store.transaction():
        add("first use of this schema happens mid-transaction")
    assert len(texts()) == 1


def test_after_commit_runs_only_after_a_successful_commit(desk):
    desk_store.ensure_schema("notes", NOTES)
    seen = []

    def visible_from_another_connection():
        with sqlite3.connect(desk / "desk.sqlite3") as db:
            seen.append([row[0] for row in db.execute("SELECT text FROM notes ORDER BY id")])

    with desk_store.transaction():
        add("committed")
        desk_store.after_commit(visible_from_another_connection)
        with pytest.raises(RuntimeError):
            with desk_store.transaction():
                desk_store.after_commit(lambda: seen.append("from a rolled-back nested block"))
                raise RuntimeError("stop")
        assert seen == [], "hooks must wait for the commit"
    assert seen == [["committed"]]

    with pytest.raises(RuntimeError):
        with desk_store.transaction():
            desk_store.after_commit(lambda: seen.append("from a rolled-back transaction"))
            raise RuntimeError("stop")
    assert seen == [["committed"]]


def test_failing_after_commit_hook_does_not_undo_or_raise(desk):
    def explode():
        raise RuntimeError("hook failed")

    with desk_store.transaction():
        add("kept")
        desk_store.after_commit(explode)
    assert texts() == ["kept"]


def test_after_commit_needs_a_transaction(desk):
    with pytest.raises(desk_store.DeskStoreError):
        desk_store.after_commit(lambda: None)


def test_child_task_started_in_a_block_gets_its_own_connection(desk):
    desk_store.ensure_schema("notes", NOTES)

    async def main():
        async def child():
            add("child")
            return texts()

        with desk_store.transaction() as db:
            db.execute("INSERT INTO notes(text) VALUES ('parent')")
            task = asyncio.create_task(child())  # copies the context, open transaction included
        return await task

    assert asyncio.run(main()) == ["parent", "child"]
    assert texts() == ["parent", "child"]


def test_thread_with_a_copied_context_never_uses_the_owners_connection(desk):
    desk_store.ensure_schema("notes", NOTES)
    with ThreadPoolExecutor(1) as pool:
        with desk_store.transaction() as db:
            db.execute("INSERT INTO notes(text) VALUES ('not committed yet')")
            inside = contextvars.copy_context()
            assert pool.submit(inside.run, texts).result(10) == [], "its own snapshot of committed data"
            with pytest.raises(desk_store.DeskStoreError):
                pool.submit(inside.run, desk_store.after_commit, lambda: None).result(10)
        assert pool.submit(inside.run, add, "from the thread").result(10)
    assert texts() == ["not committed yet", "from the thread"]


def test_after_commit_from_a_child_after_the_block_raises(desk):
    ran = []

    async def main():
        async def child():
            desk_store.after_commit(lambda: ran.append("never runs"))

        with desk_store.transaction():
            task = asyncio.create_task(child())
        await task

    with pytest.raises(desk_store.DeskStoreError):
        asyncio.run(main())
    assert ran == []


def test_threads_do_not_lose_updates(desk):
    desk_store.ensure_schema("counter", [["CREATE TABLE counter (id INTEGER PRIMARY KEY, n INTEGER NOT NULL)",
                                          "INSERT INTO counter VALUES (1, 0)"]])

    def bump(_):
        with desk_store.transaction() as db:
            n = db.execute("SELECT n FROM counter WHERE id=1").fetchone()["n"]
            db.execute("UPDATE counter SET n=? WHERE id=1", (n + 1,))

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(bump, range(80)))
    with desk_store.connect() as db:
        assert db.execute("SELECT n FROM counter WHERE id=1").fetchone()["n"] == 80


WORKER = """
import sys
from core import desk_store
desk_store.ensure_schema("jobs", [["CREATE TABLE jobs (id INTEGER PRIMARY KEY, worker TEXT NOT NULL)"]])
for _ in range(25):
    with desk_store.transaction() as db:
        db.execute("INSERT INTO jobs(worker) VALUES (?)", (sys.argv[1],))
"""


def test_processes_starting_together_share_one_file(desk):
    dashboard = Path(desk_store.__file__).resolve().parents[1]
    environment = {**os.environ, "PORTFOLIO_DIR": str(desk), "PYTHONPATH": str(dashboard)}
    workers = [subprocess.Popen([sys.executable, "-c", WORKER, f"w{index}"], env=environment,
                                stderr=subprocess.PIPE, text=True) for index in range(4)]
    errors = [worker.communicate(timeout=120)[1] for worker in workers]
    assert [worker.returncode for worker in workers] == [0, 0, 0, 0], errors
    assert desk_store.schema_version("jobs") == 1
    with desk_store.connect() as db:
        counts = dict(db.execute("SELECT worker, COUNT(*) FROM jobs GROUP BY worker").fetchall())
    assert counts == {"w0": 25, "w1": 25, "w2": 25, "w3": 25}
    assert mode(desk / "desk.sqlite3") == 0o600


def test_read_deadline_bounds_the_wait_for_a_locked_store(desk):
    import time
    desk_store.ensure_schema("notes", NOTES)
    with desk_store.transaction() as db:
        db.execute("INSERT INTO notes(text) VALUES ('a')")
    holder = sqlite3.connect(desk / desk_store.FILENAME, isolation_level=None)
    holder.execute("PRAGMA locking_mode=EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute("INSERT INTO notes(text) VALUES ('b')")
    try:
        started = time.monotonic()
        with desk_store.read_deadline(0.2):
            with pytest.raises((sqlite3.OperationalError, desk_store.DeskStoreError)):
                desk_store.ready("notes", NOTES)
            with desk_store.read_deadline(5):         # nesting never lengthens the deadline
                with pytest.raises((sqlite3.OperationalError, desk_store.DeskStoreError)):
                    desk_store.ready("notes", NOTES)
        assert time.monotonic() - started < 2.0
        with desk_store.read_deadline(0.0):
            with pytest.raises(desk_store.DeskStoreError, match="has passed"):
                desk_store.ready("notes", NOTES)
    finally:
        holder.close()
    with desk_store.read_deadline(0.0):
        with desk_store.read_deadline(None):          # None lifts the bound (background threads)
            assert desk_store.ready("notes", NOTES) is True
