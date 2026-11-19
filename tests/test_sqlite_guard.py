"""The guard for the SQLite releases that deadlock when threads open and close one file."""
import gc
import sqlite3
import threading
import time

import pytest

import core  # noqa: F401  (importing it is what installs the guard)
from core import sqlite_guard


@pytest.fixture
def guard():
    """The guard switched on whatever the release, and put back as it was afterwards."""
    was_on = sqlite_guard.installed()
    sqlite_guard.install(force=True)
    yield sqlite_guard
    if not was_on:
        sqlite_guard.uninstall()


@pytest.fixture
def database(tmp_path):
    target = tmp_path / "one.sqlite3"
    db = sqlite3.connect(target)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t (x)")
    db.commit()
    db.close()
    return target


@pytest.mark.parametrize("version, expected", [
    ((3, 51, 0), True), ((3, 51, 1), True), ((3, 51, 1, 0), True),
    ((3, 50, 4), False), ((3, 51, 2), False), ((3, 52, 0), False), ((3, 45, 1), False),
])
def test_only_the_two_releases_with_the_deadlock_are_affected(version, expected):
    assert sqlite_guard.affected(version) is expected


def test_importing_core_installs_it_exactly_on_an_affected_release():
    assert sqlite_guard.installed() is sqlite_guard.affected()
    assert (sqlite3.connect is sqlite_guard._connect) is sqlite_guard.affected()
    assert (sqlite_guard.notice() is None) is not sqlite_guard.affected()


def test_on_other_releases_install_leaves_sqlite_alone():
    if sqlite_guard.affected():
        pytest.skip("this release needs the guard")
    before = sqlite3.connect
    assert sqlite_guard.install() is False
    assert sqlite3.connect is before and not sqlite_guard.installed()


def test_install_and_uninstall_are_idempotent():
    was_on = sqlite_guard.installed()
    plain = sqlite_guard._original_connect or sqlite3.connect
    assert sqlite_guard.install(force=True) and sqlite_guard.install(force=True)
    assert sqlite3.connect is sqlite_guard._connect and sqlite_guard._original_connect is plain
    assert sqlite3.sqlite_version in sqlite_guard.notice() and sqlite_guard.FIXED_IN in sqlite_guard.notice()
    if not was_on:
        sqlite_guard.uninstall()
        sqlite_guard.uninstall()
        assert sqlite3.connect is plain and not sqlite_guard.installed()


def test_connections_work_as_before(guard, database):
    db = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=1)
    assert isinstance(db, guard.GuardedConnection)
    db.row_factory = sqlite3.Row
    db.execute("INSERT INTO t VALUES (7)")
    assert db.execute("SELECT x FROM t").fetchone()["x"] == 7
    db.close()
    db.close()                                             # closing twice stays harmless
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect((database.parent / "missing.sqlite3").as_uri() + "?mode=rw", uri=True)


def test_a_factory_chosen_by_the_caller_is_kept(guard, database):
    class Mine(sqlite3.Connection):
        pass

    by_name = sqlite3.connect(database, factory=Mine)
    by_position = sqlite3.connect(database, 5.0, 0, "DEFERRED", True, Mine)
    assert type(by_name) is Mine and type(by_position) is Mine
    by_name.close()
    by_position.close()


def test_open_and_close_wait_for_the_gate_and_statements_do_not(guard, database):
    db = sqlite3.connect(database, check_same_thread=False)
    done = {"read": threading.Event(), "close": threading.Event(), "open": threading.Event()}

    def read():
        db.execute("SELECT count(*) FROM t").fetchone()
        done["read"].set()

    def close():
        db.close()
        done["close"].set()

    def open_another():
        sqlite3.connect(database).close()
        done["open"].set()

    with guard._gate:                                      # another thread is opening or closing
        threading.Thread(target=read, daemon=True).start()
        assert done["read"].wait(5)                        # a statement is not held up
        for work in (close, open_another):
            threading.Thread(target=work, daemon=True).start()
        time.sleep(0.2)
        assert not done["close"].is_set() and not done["open"].is_set()
    assert done["close"].wait(5) and done["open"].wait(5)


class CountingGate:
    def __init__(self, inner):
        self.inner, self.entered = inner, 0

    def __enter__(self):
        self.entered += 1
        return self.inner.__enter__()

    def __exit__(self, *exc):
        return self.inner.__exit__(*exc)


def test_a_connection_left_to_the_collector_is_closed_through_the_gate(guard, database, monkeypatch):
    gc.collect()                                           # connections other tests left behind close first
    gate = CountingGate(guard._gate)
    monkeypatch.setattr(guard, "_gate", gate)
    with sqlite3.connect(database) as db:                  # commits, does not close
        db.execute("INSERT INTO t VALUES (1)")
    assert gate.entered == 1                               # the open
    del db
    gc.collect()
    assert gate.entered == 2                               # and the close the collector would have done


def test_threads_opening_and_closing_one_file_keep_going(guard, database):
    """The case that stops for good on 3.51.0 and 3.51.1 without the guard, usually within 200 rounds."""
    rounds, failures = [0, 0], []
    until = time.monotonic() + 1.5

    def work(slot):
        try:
            while time.monotonic() < until:
                db = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, isolation_level=None)
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                db.execute("SELECT count(*) FROM t").fetchone()
                db.execute("ROLLBACK")
                db.close()
                rounds[slot] += 1
        except Exception as exc:                           # pragma: no cover - reported below
            failures.append(exc)

    threads = [threading.Thread(target=work, args=(slot,), daemon=True) for slot in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert not any(thread.is_alive() for thread in threads), f"stopped after {rounds} rounds"
    assert failures == [] and min(rounds) > 50
