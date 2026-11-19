"""Finished close reports reach each listener once per report content, and never touch the pipeline.

Two kinds of tests:

* the files a finished pipeline leaves (checkpoint, snapshot, report), written by ``finish`` here,
  for the watcher's own decisions (first look, retries, leases, what is not finished);
* the real engine pipeline on a synthetic portfolio (``engine_fakes``): the close snapshot routine
  makes the day, a late entry changes the day's holdings, ``--action pipeline`` makes the day again.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from core import close_reports
from core.close_reports import Watcher
from core.routines import close_snapshot
from tests.engine_fakes import engine_copy, engine_env, engine_portfolio, position, price_server

TUESDAY = "2026-11-17"
WEDNESDAY = "2026-11-18"


class Clock:
    """Real time by default; ``advance`` moves it on (retries, leases)."""

    def __init__(self):
        self.moment = datetime.now(timezone.utc)

    def __call__(self):
        return self.moment

    def advance(self, **delta):
        self.moment += timedelta(**delta)


@pytest.fixture
def world(tmp_path, monkeypatch):
    portfolio = tmp_path / "portfolio"
    portfolio.mkdir()
    monkeypatch.setenv("PORTFOLIO_DIR", str(portfolio))
    engine = tmp_path / "checkout" / "engine" / "scripts"
    return portfolio, engine


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finish(portfolio, engine, day, text, **state):
    """What the pipeline leaves for a finished day: the snapshot, its history row, the report and the checkpoint."""
    snapshots = portfolio / "snapshots"
    snapshots.mkdir(exist_ok=True)
    snapshot = snapshots / f"{day}.json"
    snapshot.write_text(json.dumps({"date": day, "summary": {"note": text}}))
    reports = engine.parent / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / f"portfolio-{day.replace('-', '')}.md"
    report.write_text(text, encoding="utf-8")
    made = sha(snapshot)
    checkpoint = {"holdings_hash": "h", "snapshot": True, "history": True, "snapshot_sha": made,
                  "history_snapshot": made, "report": True, "report_snapshot": made, "report_sha": sha(report),
                  "push": "not_started", "complete": True, **state}
    (portfolio / "pipeline-state").mkdir(exist_ok=True)
    (portfolio / "pipeline-state" / f"{day}.json").write_text(json.dumps(checkpoint))
    return report


def look(watcher, times=1):
    async def run():
        started = []
        for _ in range(times):
            started += await watcher.poll()
            await watcher.settle()
        return started
    return asyncio.run(run())


def fingerprint(root):
    return {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob("*"))
            if path.is_file() and not path.name.startswith("desk-extensions.sqlite3")}


# ── What counts as finished ──

def test_a_finished_report_is_read_with_its_hashes(world):
    portfolio, engine = world
    report = finish(portfolio, engine, TUESDAY, "# 组合日报 2026-11-17\n")
    found = close_reports.completed(TUESDAY, engine_dir=engine)
    assert found == {"trading_date": TUESDAY, "report": "# 组合日报 2026-11-17\n", "report_sha": sha(report),
                     "snapshot_sha": sha(portfolio / "snapshots" / f"{TUESDAY}.json"),
                     "report_file": str(report.resolve()),
                     "snapshot_file": str((portfolio / "snapshots" / f"{TUESDAY}.json").resolve())}


@pytest.mark.parametrize("spoil", ["report_changed", "snapshot_changed", "report_missing", "old_checkpoint",
                                   "report_of_another_snapshot", "history_unfinished", "no_checkpoint",
                                   "no_engine", "bad_day"])
def test_what_is_not_finished_is_not_a_report(world, spoil, monkeypatch):
    portfolio, engine = world
    report = finish(portfolio, engine, TUESDAY, "# report\n")
    checkpoint = portfolio / "pipeline-state" / f"{TUESDAY}.json"
    state = json.loads(checkpoint.read_text())
    day, engine_dir = TUESDAY, engine
    if spoil == "report_changed":
        report.write_text("# edited by hand\n")
    elif spoil == "snapshot_changed":
        (portfolio / "snapshots" / f"{TUESDAY}.json").write_text("{}")   # another snapshot of the day is being made
    elif spoil == "report_missing":
        report.unlink()
    elif spoil == "old_checkpoint":   # a checkpoint of a release before 4.0.0: stage flags only
        checkpoint.write_text(json.dumps({key: state[key] for key in ("snapshot", "history", "report", "push")}))
    elif spoil == "report_of_another_snapshot":
        checkpoint.write_text(json.dumps({**state, "report_snapshot": "0" * 64}))
    elif spoil == "history_unfinished":
        checkpoint.write_text(json.dumps({**state, "history": False}))
    elif spoil == "no_checkpoint":
        checkpoint.unlink()
    elif spoil == "no_engine":
        engine_dir = None
        monkeypatch.setattr(close_snapshot, "default_engine_dir", lambda: None)   # the Docker backend image
    elif spoil == "bad_day":
        day = "../2026-11-17"
    assert close_reports.completed(day, engine_dir=engine_dir) is None


def test_only_checkpoints_written_within_the_lookback_are_looked_at(world):
    portfolio, engine = world
    finish(portfolio, engine, TUESDAY, "old\n")
    finish(portfolio, engine, WEDNESDAY, "new\n")
    old = time.time() - (close_reports.LOOKBACK + timedelta(hours=1)).total_seconds()
    os.utime(portfolio / "pipeline-state" / f"{TUESDAY}.json", (old, old))
    (portfolio / "pipeline-state" / "notes.json").write_text("{}")   # not a day's checkpoint
    found = close_reports.recent(datetime.now(timezone.utc), engine_dir=engine)
    assert [report["trading_date"] for report in found] == [WEDNESDAY]


# ── The watcher ──

def test_first_look_knows_what_is_finished_then_each_new_report_is_told_once(world):
    portfolio, engine = world
    finish(portfolio, engine, TUESDAY, "already sent by the old installation\n")
    told = []
    watcher = Watcher({"publisher": told.append}, portfolio_dir=portfolio, engine_dir=engine)
    assert look(watcher, times=3) == [] and told == [], "enabling a listener does not resend old reports"

    report = finish(portfolio, engine, WEDNESDAY, "# 2026-11-18\n")
    assert look(watcher) == [("publisher", WEDNESDAY, sha(report))]
    assert look(watcher, times=3) == []
    assert [(item["trading_date"], item["report"], item["replaces"], item["attempt"]) for item in told] == [
        (WEDNESDAY, "# 2026-11-18\n", None, 1)]

    # Saved again with the same content (a resumed pipeline re-saves its checkpoint): nothing new.
    checkpoint = portfolio / "pipeline-state" / f"{WEDNESDAY}.json"
    checkpoint.write_text(json.dumps({**json.loads(checkpoint.read_text()), "push": "complete"}))
    assert look(watcher) == [] and len(told) == 1

    # Made again with other content for the same day (a late entry): told again, naming what it replaces.
    first = told[0]["report_sha"]
    again = finish(portfolio, engine, WEDNESDAY, "# 2026-11-18（补录后）\n")
    assert look(watcher) == [("publisher", WEDNESDAY, sha(again))]
    assert told[1]["replaces"] == first and told[1]["report"] == "# 2026-11-18（补录后）\n"
    # The report of an already known day, made again, names the known report too.
    replaced = finish(portfolio, engine, TUESDAY, "Tuesday, made again\n")
    look(watcher)
    assert told[2]["trading_date"] == TUESDAY and told[2]["report_sha"] == sha(replaced)
    assert told[2]["replaces"] is not None
    assert [row["status"] for row in close_reports.deliveries("publisher")].count("delivered") == 3


def test_each_listener_is_told_on_its_own_and_a_new_listener_starts_from_now(world):
    portfolio, engine = world
    first, second = [], []
    look(Watcher({"a": first.append}, portfolio_dir=portfolio, engine_dir=engine))
    finish(portfolio, engine, TUESDAY, "one\n")
    watcher = Watcher({"a": first.append, "b": second.append}, portfolio_dir=portfolio, engine_dir=engine)
    look(watcher, times=2)
    assert len(first) == 1 and second == [], "b was enabled after the report was finished"
    finish(portfolio, engine, WEDNESDAY, "two\n")
    look(watcher)
    assert [item["trading_date"] for item in first] == [TUESDAY, WEDNESDAY]
    assert [item["trading_date"] for item in second] == [WEDNESDAY]


def test_a_listener_that_raises_is_retried_then_given_up_and_the_files_stay_as_they_were(world, caplog):
    portfolio, engine = world
    clock = Clock()
    calls, failures = [], []

    def broken(report):
        calls.append(report["attempt"])
        raise ConnectionError("feishu is down, token=secret-value")

    watcher = Watcher({"publisher": broken}, now=clock, portfolio_dir=portfolio, engine_dir=engine,
                      failed=lambda name, report, exc: failures.append((name, report["trading_date"], type(exc))))
    look(watcher)
    finish(portfolio, engine, TUESDAY, "report\n")
    before = fingerprint(portfolio.parent)
    look(watcher, times=2)
    assert calls == [1], "not again before the first retry delay"
    for delay in close_reports.RETRY_DELAYS:
        clock.advance(seconds=delay.total_seconds() - 1)
        look(watcher)
        clock.advance(seconds=1)
        look(watcher)
    assert calls == [1, 2, 3, 4]
    clock.advance(days=1)
    look(watcher)
    assert calls == [1, 2, 3, 4], "given up after the last retry"
    assert failures == [("publisher", TUESDAY, ConnectionError)] * 4
    [row] = close_reports.deliveries("publisher")
    assert (row["status"], row["attempts"], row["next_attempt_at"], row["error"]) == ("failed", 4, None,
                                                                                      "ConnectionError")
    assert fingerprint(portfolio.parent) == before, "the pipeline's files are only read"
    assert "secret-value" not in caplog.text and "given up" in caplog.text

    # A newer report of the day is another report: it is delivered on its own.
    finish(portfolio, engine, TUESDAY, "report made again\n")
    look(watcher)
    assert calls == [1, 2, 3, 4, 1]


def test_a_hanging_listener_holds_up_only_itself(world):
    portfolio, engine = world
    release = threading.Event()
    told = []

    def hangs(report):
        release.wait(30)

    async def scenario():
        watcher = Watcher({"stuck": hangs, "other": told.append}, portfolio_dir=portfolio, engine_dir=engine)
        await watcher.poll()
        finish(portfolio, engine, TUESDAY, "one\n")
        await watcher.poll()
        await asyncio.wait_for(watcher._calls["other"], 5)
        assert [item["trading_date"] for item in told] == [TUESDAY]
        # The event loop and the shared worker threads stay free while it hangs.
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "worker thread free"), 5) == "worker thread free"
        second = finish(portfolio, engine, WEDNESDAY, "two\n")
        assert await watcher.poll() == [("other", WEDNESDAY, sha(second))], "the stuck one is not called again yet"
        await asyncio.wait_for(watcher._calls["other"], 5)
        release.set()
        await watcher.settle()
        return watcher

    asyncio.run(scenario())
    statuses = {(row["listener"], row["trading_date"]): row["status"] for row in close_reports.deliveries()}
    assert statuses == {("stuck", TUESDAY): "delivered", ("other", TUESDAY): "delivered",
                        ("other", WEDNESDAY): "delivered"}


def test_an_async_listener_runs_in_its_own_event_loop(world):
    portfolio, engine = world
    seen = []

    async def listener(report):
        await asyncio.sleep(0)
        seen.append((report["trading_date"], threading.current_thread().name))

    watcher = Watcher({"async_one": listener}, portfolio_dir=portfolio, engine_dir=engine)
    look(watcher)
    finish(portfolio, engine, TUESDAY, "one\n")
    look(watcher)
    assert seen == [(TUESDAY, "close-report-async_one")]


def test_a_call_cut_off_by_a_stop_is_made_again_once_its_lease_has_run_out(world):
    portfolio, engine = world
    clock = Clock()
    told = []
    look(Watcher({"publisher": told.append}, now=clock, portfolio_dir=portfolio, engine_dir=engine))
    finish(portfolio, engine, TUESDAY, "one\n")
    # Another process claimed the report and stopped during the call: its row stays running.
    gone = Watcher({"publisher": lambda report: None}, now=clock, portfolio_dir=portfolio, engine_dir=engine,
                   owner="stopped-process")
    assert gone._claim("publisher", close_reports.recent(clock(), engine_dir=engine), clock()) is not None
    watcher = Watcher({"publisher": told.append}, now=clock, portfolio_dir=portfolio, engine_dir=engine)
    assert look(watcher) == [] and told == [], "a live claim is not taken over"
    clock.advance(seconds=close_reports.LEASE.total_seconds() + 1)
    look(watcher)
    assert [(item["trading_date"], item["attempt"]) for item in told] == [(TUESDAY, 2)]
    # The process that stopped cannot record a result over it.
    gone._finish("publisher", {"trading_date": TUESDAY, "report_sha": told[0]["report_sha"]}, None)
    assert [row["status"] for row in close_reports.deliveries("publisher")] == ["delivered"]


def test_the_watcher_loop_runs_until_stopped(world):
    portfolio, engine = world
    told = []

    async def scenario():
        watcher = Watcher({"publisher": told.append}, interval=0.05, portfolio_dir=portfolio, engine_dir=engine)
        await watcher.poll()   # the first look, before the report exists
        watcher.start()
        finish(portfolio, engine, TUESDAY, "one\n")
        for _ in range(100):
            if told:
                break
            await asyncio.sleep(0.05)
        await watcher.stop()
        assert watcher._task is None

    asyncio.run(scenario())
    assert [item["trading_date"] for item in told] == [TUESDAY]


# ── The real pipeline: the day, then a late entry ──

@pytest.fixture
def engine_environment(monkeypatch):
    for name in ("TRACKER_DEMO_MODE", "TRACKER_QUOTE_SOURCE", "TRACKER_CLOSE_SNAPSHOT", "CONDA_ENV",
                 "PORTFOLIO_WRITE_LOCK_INHERITED", "TRACKER_LAB_ONLY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")


def test_the_real_pipeline_is_told_once_and_again_after_a_late_entry(tmp_path, monkeypatch, engine_environment):
    portfolio = engine_portfolio(tmp_path / "portfolio", "http://127.0.0.1:9/unused", positions=[position("SHA:600001")])
    monkeypatch.setenv("PORTFOLIO_DIR", str(portfolio))
    scripts = engine_copy(tmp_path / "checkout")
    report_file = tmp_path / "checkout" / "engine" / "reports" / "portfolio-20261117.md"
    told = []
    watcher = Watcher({"publisher": told.append}, portfolio_dir=portfolio, engine_dir=scripts)
    look(watcher)   # enabled before the day is made

    def snapshot_routine():
        return asyncio.run(close_snapshot.run(TUESDAY, engine_dir=scripts, portfolio_dir=portfolio))

    with price_server({"SHA:600001": 12.5}) as base:
        config = json.loads((portfolio / "config.json").read_text())
        (portfolio / "config.json").write_text(json.dumps({**config, "yahoo_base": base}))
        assert snapshot_routine().status == "ok"
        look(watcher, times=2)
        assert [item["trading_date"] for item in told] == [TUESDAY]
        assert told[0]["report"] == report_file.read_text(encoding="utf-8") and told[0]["replaces"] is None

        # A late entry: the day's holdings change after its snapshot; the pipeline makes the day again.
        holdings = portfolio / "holdings" / f"{TUESDAY}.json"
        data = json.loads(holdings.read_text())
        data["groups"]["Example"]["cash"] = 3000
        holdings.write_text(json.dumps(data))
        result = subprocess.run([sys.executable, str(scripts / "portfolio_daily_update.py"), "--action", "pipeline",
                                 "--date", TUESDAY, "--no-push"], env=engine_env(portfolio), cwd=scripts,
                                capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    look(watcher, times=2)
    assert len(told) == 2 and told[1]["trading_date"] == TUESDAY
    assert told[1]["replaces"] == told[0]["report_sha"] != told[1]["report_sha"]
    assert told[1]["report"] == report_file.read_text(encoding="utf-8") != told[0]["report"]

    # The routine completing the day again (its checkpoint is saved again) is no new report.
    assert snapshot_routine().status == "ok"
    look(watcher)
    assert len(told) == 2
