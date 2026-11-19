"""Routine ``close_snapshot``: the backend makes the close snapshot by itself.

Two kinds of tests:

* the real engine pipeline (a private copy of ``engine/scripts``) on synthetic
  portfolios, priced by a loopback stand-in for the chart API (``engine_fakes``);
* a fake runner where only the routine's own decisions matter (retries,
  notifications, environment, timeouts of the child are tested on tiny scripts).
"""
import asyncio
from contextlib import suppress
from datetime import timedelta
import functools
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from core import notify
from core.routines import close_snapshot, scheduler
from core.routines.close_snapshot import EngineResult, run_engine
from core.routines.scheduler import Routine, Scheduler
from tests.engine_fakes import SCRIPTS, engine_copy, engine_env, engine_portfolio, position, price_server
from tests.fakes import FakeClock, build_ledger, opening
from tests.fakes import position as ledger_position

TUESDAY = "2026-11-17"
# The mark of a snapshot made from the legacy holdings files (the `portfolio` fixture holds them).
LEGACY = "；含估算：持仓来自旧版持仓文件，尚未迁移到账本"


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for name in ("TRACKER_DEMO_MODE", "TRACKER_QUOTE_SOURCE", "TRACKER_CLOSE_SNAPSHOT", "CONDA_ENV",
                 "PORTFOLIO_WRITE_LOCK_INHERITED", "TRACKER_LAB_ONLY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")


def run(day=TUESDAY, **options):
    return asyncio.run(close_snapshot.run(day, **options))


def complete_day(portfolio, engine_dir, day, partial=False):
    """What a successful pipeline leaves: the snapshot JSON, the day's history row and the report."""
    folder = portfolio / "snapshots"
    folder.mkdir(exist_ok=True)
    (folder / f"{day}.json").write_text("{}")
    if partial:
        return
    (portfolio / "history.csv").write_text(f"date,total_value\n{day},1\n")
    reports = Path(engine_dir).parent / "reports"
    reports.mkdir(exist_ok=True)
    (reports / f"portfolio-{day.replace('-', '')}.md").write_text("report")


class FakeRunner:
    """Records the command and environment; answers with scripted results (the last repeats).

    With ``write_snapshot`` (the portfolio), a successful result also leaves what the
    pipeline leaves for the day; with ``partial`` only the snapshot JSON.
    """

    def __init__(self, *results, write_snapshot=None, partial=False):
        self.results = list(results) or [EngineResult(0, "ok")]
        self.write_snapshot = write_snapshot
        self.partial = partial
        self.calls = []

    async def __call__(self, command, *, env, cwd, timeout):
        self.calls.append({"command": list(command), "env": dict(env), "cwd": cwd, "timeout": timeout})
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if result.returncode == 0 and self.write_snapshot is not None:
            complete_day(self.write_snapshot, cwd, command[-1], partial=self.partial)
        return result


@pytest.fixture
def fake_engine(tmp_path):
    scripts = tmp_path / "engine" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "portfolio_daily_update.py").write_text("raise SystemExit('not used')\n")
    return scripts


@pytest.fixture
def portfolio(tmp_path, monkeypatch):
    """A legacy-holdings portfolio; the desk store lives in it too."""
    folder = engine_portfolio(tmp_path / "portfolio", "http://127.0.0.1:9/unused", positions=[position("SHA:600001")])
    monkeypatch.setenv("PORTFOLIO_DIR", str(folder))
    return folder


# ── When it is due ──

@pytest.mark.parametrize("moment, expected", [
    ("2026-11-17 09:00", "2026-11-17T15:40:00+08:00"),
    ("2026-11-17 23:00", "2026-11-17T15:40:00+08:00"),
    ("2026-11-21 16:00", None),     # Saturday
    ("2026-10-01 16:00", None),     # National Day
])
def test_due_at_15_40_shanghai_on_trading_days(moment, expected):
    due = close_snapshot.due(FakeClock(moment).session())
    assert (None if due is None else due.isoformat()) == expected


def test_registered_by_the_routine_package_with_two_retries():
    from core.routines import load_kinds
    load_kinds()
    kinds = {routine.kind: routine for routine in scheduler.registered()}
    assert kinds["close_snapshot"].title == "收盘快照"
    assert kinds["close_snapshot"].retry_delays == (timedelta(minutes=15), timedelta(minutes=30))


def test_a_retry_may_run_until_9_00_the_next_morning():
    """Later, the engine's prices may belong to the next session: the day is given up instead."""
    from core.routines import load_kinds
    from core.market.calendar import SHANGHAI
    load_kinds()
    routine = {routine.kind: routine for routine in scheduler.registered()}["close_snapshot"]
    assert routine.retry_until(TUESDAY).isoformat() == "2026-11-18T09:00:00+08:00"
    assert routine.retry_until("2026-11-20").tzinfo is SHANGHAI


# ── The real pipeline on synthetic portfolios ──

def test_scheduler_runs_the_real_pipeline_once_and_restarts_find_it_done(tmp_path, portfolio):
    scripts = engine_copy(tmp_path / "checkout")
    clock = FakeClock(f"{TUESDAY} 15:40")
    with price_server({"SHA:600001": 12.5}) as base:
        config = json.loads((portfolio / "config.json").read_text())
        (portfolio / "config.json").write_text(json.dumps({**config, "yahoo_base": base}))
        routine = Routine(close_snapshot.KIND, close_snapshot.due,
                          functools.partial(close_snapshot.run, engine_dir=scripts, portfolio_dir=portfolio),
                          close_snapshot.TITLE)

        async def ticks():
            for moment in ("15:40", "15:41"):
                clock.set(f"{TUESDAY} {moment}")
                for each in (Scheduler([routine], now=clock), Scheduler([routine], now=clock)):
                    await each.tick()
                    await each.settle()
        asyncio.run(ticks())

    saved = scheduler.run_for(close_snapshot.KIND, TUESDAY)
    assert saved["status"] == "ok" and saved["attempts"] == 1
    assert saved["summary"] == "快照已生成；日报已保存" + LEGACY
    assert saved["detail"]["exit_code"] == 0 and "快照生成成功" in saved["detail"]["log_tail"]
    # The polled status leaves the output tail (which shows totals) to the runs list.
    assert Scheduler([routine], now=clock).routine_status(routine)["today"]["detail"] == {"exit_code": 0}
    snapshot = json.loads((portfolio / "snapshots" / f"{TUESDAY}.json").read_text())
    assert snapshot["summary"]["total_value"] == 1000 + 100 * 12.5
    assert (tmp_path / "checkout" / "engine" / "reports" / "portfolio-20261117.md").is_file()
    assert notify.list() == []


def test_real_pipeline_values_an_active_ledger_and_is_idempotent(tmp_path, monkeypatch):
    portfolio = tmp_path / "portfolio"
    portfolio.mkdir()
    monkeypatch.setenv("PORTFOLIO_DIR", str(portfolio))
    build_ledger(portfolio, [opening("Example", cash={"CNY": "1000"}, contributed_cny="2000", date="2026-11-16",
                                     positions=[ledger_position("SHE:000002", "200", "8", name="Example")])])
    scripts = engine_copy(tmp_path / "checkout")
    with price_server({"SHE:000002": 9.0}) as base:
        (portfolio / "config.json").write_text(json.dumps({"fx_tickers": {}, "yahoo_base": base}))
        first = run(engine_dir=scripts, portfolio_dir=portfolio)
        second = run(engine_dir=scripts, portfolio_dir=portfolio)
    assert first.status == "ok" and first.summary == "快照已生成；日报已保存"
    assert second.status == "ok" and second.summary.startswith("当日快照已存在，未重复生成")
    snapshot = json.loads((portfolio / "snapshots" / f"{TUESDAY}.json").read_text())
    assert snapshot["summary"]["total_value"] == 1000 + 200 * 9.0 and snapshot["ledger_revision"] == 1
    assert not (portfolio / "holdings").exists(), "no legacy holdings are written for a ledger"


def kill_after_saving_the_snapshot(scripts):
    """The copied snapshot entrypoint kills itself right after its atomic JSON save, before history.csv."""
    path = scripts / "portfolio_snapshot.py"
    marker = 'if __name__ == "__main__":'
    text = path.read_text(encoding="utf-8")
    assert text.count(marker) == 1
    hook = ("_save_snapshot = save_snapshot\n\n\n"
            "def save_snapshot(snapshot):\n"
            "    path = _save_snapshot(snapshot)\n"
            "    import signal\n"
            "    os.kill(os.getpid(), signal.SIGKILL)\n"
            "    return path\n\n\n")
    path.write_text(text.replace(marker, hook + marker), encoding="utf-8")


def fail_the_first_report(scripts):
    path = scripts / "portfolio_report.py"
    text = path.read_text(encoding="utf-8")
    assert text.count("def main():\n") == 1
    path.write_text(text.replace("def main():\n", (
        "def main():\n"
        "    flag = os.path.join(PORTFOLIO_DIR, '.synthetic-report-failure')\n"
        "    if not os.path.exists(flag):\n"
        "        open(flag, 'w').close()\n"
        "        sys.exit('synthetic report failure')\n")), encoding="utf-8")


def history_row(portfolio, day=TUESDAY):
    path = portfolio / "history.csv"
    rows = [line for line in path.read_text().splitlines()[1:] if line.startswith(day + ",")] if path.exists() else []
    assert len(rows) <= 1
    return rows[0].split(",") if rows else None


def priced(portfolio, base):
    config = json.loads((portfolio / "config.json").read_text())
    (portfolio / "config.json").write_text(json.dumps({**config, "yahoo_base": base}))


def test_a_snapshot_killed_after_its_json_save_is_completed_on_the_retry_without_new_prices(tmp_path, portfolio):
    """The reviewer's case: the snapshot JSON exists, history.csv does not."""
    scripts = engine_copy(tmp_path / "checkout")
    kill_after_saving_the_snapshot(scripts)
    report = tmp_path / "checkout" / "engine" / "reports" / "portfolio-20261117.md"
    with price_server({"SHA:600001": 12.5}) as base:
        priced(portfolio, base)
        first = run(engine_dir=scripts, portfolio_dir=portfolio)
    assert first.status == "failed"
    saved = (portfolio / "snapshots" / f"{TUESDAY}.json").read_text()
    assert history_row(portfolio) is None and not report.exists()

    # The price server is gone: completing the day must not need a price.
    second = run(engine_dir=scripts, portfolio_dir=portfolio)
    assert second.status == "ok", second.detail["log_tail"]
    assert second.summary == "当日快照已存在；已补写当日历史记录；已补生成日报" + LEGACY
    summary = json.loads(saved)["summary"]
    row = history_row(portfolio)
    assert len(row) == 11 and float(row[1]) == summary["total_value"] == 2250
    assert float(row[2]) == summary["total_cost"]
    assert (portfolio / "snapshots" / f"{TUESDAY}.json").read_text() == saved, "the snapshot is not made again"
    assert report.is_file()

    third = run(engine_dir=scripts, portfolio_dir=portfolio)
    assert (third.status, third.summary) == ("ok", "当日快照已存在，未重复生成；日报已保存" + LEGACY)


def test_a_report_that_failed_is_generated_on_the_retry(tmp_path, portfolio):
    scripts = engine_copy(tmp_path / "checkout")
    fail_the_first_report(scripts)
    report = tmp_path / "checkout" / "engine" / "reports" / "portfolio-20261117.md"
    with price_server({"SHA:600001": 12.5}) as base:
        priced(portfolio, base)
        first = run(engine_dir=scripts, portfolio_dir=portfolio)
        assert first.status == "failed" and not report.exists() and history_row(portfolio) is not None
        second = run(engine_dir=scripts, portfolio_dir=portfolio)
    assert (second.status, second.summary) == ("ok", "当日快照已存在；已补生成日报" + LEGACY)
    assert report.is_file()


def test_real_pipeline_with_a_missing_price_fails_without_a_snapshot(tmp_path, portfolio):
    scripts = engine_copy(tmp_path / "checkout")
    with price_server({}) as base:
        config = json.loads((portfolio / "config.json").read_text())
        (portfolio / "config.json").write_text(json.dumps({**config, "yahoo_base": base}))
        outcome = run(engine_dir=scripts, portfolio_dir=portfolio)
    assert outcome.status == "failed" and outcome.error_category == "missing_price"
    assert outcome.summary == "部分持仓没有取得有效价格，未生成快照（不会按 0 计算）"
    assert "SHA:600001" in outcome.detail["log_tail"] and outcome.detail["exit_code"] == 1
    assert not (portfolio / "snapshots" / f"{TUESDAY}.json").exists()
    assert not (portfolio / "history.csv").exists()


# ── What the routine decides before and after the engine ──

def test_demo_mode_never_runs_the_engine(fake_engine, portfolio, monkeypatch):
    monkeypatch.setenv("TRACKER_DEMO_MODE", "1")
    runner = FakeRunner()
    outcome = run(engine_dir=fake_engine, portfolio_dir=portfolio, runner=runner)
    assert (outcome.status, outcome.summary, outcome.error_category) == ("skipped", "演示模式", "demo")
    assert runner.calls == []


@pytest.mark.parametrize("name, value, category", [("TRACKER_QUOTE_SOURCE", "fake", "fake_quotes"),
                                                   ("TRACKER_CLOSE_SNAPSHOT", "0", "disabled")])
def test_scripted_quotes_and_the_off_switch_skip(fake_engine, portfolio, monkeypatch, name, value, category):
    monkeypatch.setenv(name, value)
    runner = FakeRunner()
    outcome = run(engine_dir=fake_engine, portfolio_dir=portfolio, runner=runner)
    assert outcome.status == "skipped" and outcome.error_category == category and runner.calls == []


def test_without_the_engine_scripts_the_routine_is_unavailable(tmp_path, portfolio, monkeypatch):
    runner = FakeRunner()
    outcome = run(engine_dir=tmp_path / "no-engine", portfolio_dir=portfolio, runner=runner)
    assert outcome.status == "unavailable" and outcome.error_category == "engine_missing"
    assert outcome.detail == {"doc": close_snapshot.DOC} and close_snapshot.DOC in outcome.summary
    # The backend image has core/ at /app/core: no engine directory is assumed there.
    monkeypatch.setattr(close_snapshot, "__file__", "/app/core/routines/close_snapshot.py")
    assert close_snapshot.default_engine_dir() is None
    assert run(portfolio_dir=portfolio, runner=runner).status == "unavailable"
    assert runner.calls == []


def test_the_source_checkout_finds_its_engine():
    assert close_snapshot.default_engine_dir() == Path(__file__).resolve().parents[1] / "engine" / "scripts"


def test_nothing_to_snapshot_is_skipped(tmp_path, fake_engine, monkeypatch):
    empty = tmp_path / "empty"
    (empty / "holdings").mkdir(parents=True)
    (empty / "holdings" / "example.json").write_text("{}")  # the shipped example is not data
    (empty / "config.json").write_text("{}")
    runner = FakeRunner()
    outcome = run(engine_dir=fake_engine, portfolio_dir=empty, runner=runner)
    assert outcome.status == "skipped" and outcome.error_category == "no_data" and runner.calls == []


def test_a_ledger_without_engine_configuration_is_unavailable(tmp_path, fake_engine):
    folder = tmp_path / "ledger-only"
    build_ledger(folder, [opening(cash={"CNY": "100"})])
    outcome = run(engine_dir=fake_engine, portfolio_dir=folder, runner=FakeRunner())
    assert outcome.status == "unavailable" and outcome.error_category == "config_missing"


def test_the_child_gets_this_interpreter_the_portfolio_and_no_lock_claim(fake_engine, portfolio, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_WRITE_LOCK_INHERITED", "/somewhere/.portfolio-update.lock")
    runner = FakeRunner(write_snapshot=portfolio)
    assert run(engine_dir=fake_engine, portfolio_dir=portfolio, runner=runner).status == "ok"
    [call] = runner.calls
    assert call["command"] == [sys.executable, str(fake_engine / "portfolio_daily_update.py"),
                               "--action", "auto-pipeline", "--date", TUESDAY]
    assert call["cwd"] == fake_engine and call["timeout"] == close_snapshot.TIMEOUT_SECONDS
    assert call["env"]["PORTFOLIO_DIR"] == str(portfolio.resolve())
    assert "PORTFOLIO_WRITE_LOCK_INHERITED" not in call["env"]


def test_engine_results_become_outcomes(fake_engine, portfolio):
    def outcome(result, **options):
        return run(engine_dir=fake_engine, portfolio_dir=portfolio, runner=FakeRunner(result, **options))

    timed_out = outcome(EngineResult(None, "partial output", timed_out=True))
    assert (timed_out.status, timed_out.error_category) == ("failed", "timeout")
    assert timed_out.summary == "超过 10 分钟未完成，已终止"
    crashed = outcome(EngineResult(2, "Traceback ...\nproxy http://user:secret@proxy.example:8080 refused"))
    assert (crashed.status, crashed.error_category, crashed.summary) == ("failed", "engine_error", "快照引擎失败（退出码 2）")
    assert "secret" not in crashed.detail["log_tail"] and "[redacted]@proxy.example" in crashed.detail["log_tail"]
    claimed = outcome(EngineResult(0, "done"))
    assert (claimed.status, claimed.error_category) == ("failed", "no_snapshot")
    made_elsewhere = outcome(EngineResult(0, "  ℹ️  今日快照已存在"), write_snapshot=portfolio)
    assert (made_elsewhere.status, made_elsewhere.summary) == ("ok", "当日快照已存在，未重复生成")
    (portfolio / "pipeline-state").mkdir()
    (portfolio / "pipeline-state" / f"{TUESDAY}.json").write_text(json.dumps(
        {"snapshot": True, "report": True, "push": "uncertain"}))
    assert outcome(EngineResult(0, "  ℹ️  今日快照已存在"), write_snapshot=portfolio).summary == \
        "当日快照已存在，未重复生成；日报已保存；飞书推送结果未确认，不会自动重发"


def test_success_is_recorded_only_for_a_complete_day(fake_engine, portfolio):
    """The engine exits 0, but only the snapshot JSON is there: not ok, so the scheduler retries."""
    partial = run(engine_dir=fake_engine, portfolio_dir=portfolio,
                  runner=FakeRunner(EngineResult(0, "done"), write_snapshot=portfolio, partial=True))
    assert (partial.status, partial.error_category) == ("failed", "incomplete")
    assert "历史记录" in partial.summary and "日报" in partial.summary


def test_a_history_row_or_report_of_another_snapshot_is_not_success(fake_engine, portfolio):
    """The checkpoint names the snapshot each later stage was made from; an older one is not today's."""
    import hashlib
    runner = FakeRunner(EngineResult(0, "done"), write_snapshot=portfolio)
    first = run(engine_dir=fake_engine, portfolio_dir=portfolio, runner=runner)
    saved = hashlib.sha256((portfolio / "snapshots" / f"{TUESDAY}.json").read_bytes()).hexdigest()
    (portfolio / "pipeline-state").mkdir()
    checkpoint = portfolio / "pipeline-state" / f"{TUESDAY}.json"
    for stale, label in (("history_snapshot", "历史记录"), ("report_snapshot", "日报")):
        state = {"snapshot": True, "history": True, "report": True, "push": "not_started", "snapshot_sha": saved,
                 "history_snapshot": saved, "report_snapshot": saved, stale: "0" * 64}
        checkpoint.write_text(json.dumps(state))
        outcome = run(engine_dir=fake_engine, portfolio_dir=portfolio, runner=runner)
        assert (outcome.status, outcome.error_category) == ("failed", "incomplete"), stale
        assert label in outcome.summary
    assert first.status == "ok"


def test_a_failing_engine_is_retried_twice_and_notified_exactly_once(fake_engine, portfolio):
    runner = FakeRunner(EngineResult(1, "总资产: ¥12.34万\n❌ 快照生成失败"))
    routine = Routine(close_snapshot.KIND, close_snapshot.due,
                      functools.partial(close_snapshot.run, engine_dir=fake_engine, portfolio_dir=portfolio,
                                        runner=runner), close_snapshot.TITLE)
    clock = FakeClock()
    s = Scheduler([routine], now=clock)

    async def at(*moments):
        for moment in moments:
            clock.set(f"{TUESDAY} {moment}")
            await s.tick()
            await s.settle()

    asyncio.run(at("15:40", "15:50", "15:55", "16:20", "16:25", "17:00", "20:00"))
    assert len(runner.calls) == 3
    saved = scheduler.run_for(close_snapshot.KIND, TUESDAY)
    assert saved["status"] == "failed" and saved["attempts"] == 3 and saved["next_attempt_at"] is None
    [message] = notify.list()
    assert message["title"] == "收盘快照未完成"
    assert message["body"] == (f"{TUESDAY} 的收盘快照尝试 3 次后仍未完成：快照引擎失败（退出码 1）。"
                               "不会再自动重试；问题解决后可手动重试。")
    assert "¥" not in json.dumps(message, ensure_ascii=False), "no amounts in notifications"


# ── The child process ──

def child(tmp_path, code):
    script = tmp_path / "child.py"
    script.write_text(code)
    return [sys.executable, str(script)]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_runner_keeps_a_bounded_tail_of_the_output(tmp_path):
    command = child(tmp_path, "import sys\nfor i in range(5000):\n    print('line', i)\nsys.exit(3)\n")
    result = asyncio.run(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=60, tail_chars=200))
    assert result.returncode == 3 and not result.timed_out
    assert len(result.output) <= 200 and result.output.startswith("line ")
    assert result.output.rstrip().endswith("line 4999")


def test_runner_interrupts_the_child_group_on_timeout(tmp_path):
    marker = tmp_path / "grandchild.pid"
    command = child(tmp_path, "import subprocess, sys, time\n"
                    f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                    f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
                    "time.sleep(60)\n")
    result = asyncio.run(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=2, grace=5))
    assert result.timed_out and result.returncode != 0
    grandchild = int(marker.read_text())
    for _ in range(100):
        if not alive(grandchild):
            break
        time.sleep(0.05)
    assert not alive(grandchild), "the whole process group is stopped"


def test_runner_kills_a_child_that_ignores_the_interrupt(tmp_path):
    command = child(tmp_path, "import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                    "print('ready', flush=True)\ntime.sleep(60)\n")
    result = asyncio.run(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=1, grace=0.5))
    assert result.timed_out and result.returncode == -9


def running(pid):
    """Alive and not a zombie (a zombie is dead; only its parent has not collected it yet)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().rsplit(")", 1)[1].split()[0] not in ("Z", "X") if stat.exists() else True
    except (OSError, IndexError):
        return False


# A pipeline stand-in: the real run_child starts the rest of argv, as the pipeline starts a snapshot.
PIPELINE = """
import os, sys
sys.path.insert(0, {scripts!r})
from portfolio_write_lock import run_child
open(os.path.join(sys.argv[1], 'parent.pid'), 'w').write(str(os.getpid()))
run_child(sys.argv[2:], timeout=600)
"""
SLEEPER = "import os, sys, time\nopen(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(120)\n"


def nested_command(tmp_path):
    """The pipeline stand-in, its child a shell wrapper (like ``conda run``) that waits for a Python sleeper."""
    from tests.engine_fakes import SCRIPTS
    (tmp_path / "sleeper.py").write_text(SLEEPER)
    wrapper = f'"{sys.executable}" "{tmp_path / "sleeper.py"}" "{tmp_path / "sleeper.pid"}"; status=$?; exit $status'
    return child(tmp_path, PIPELINE.format(scripts=str(SCRIPTS))) + [str(tmp_path), "/bin/sh", "-c", wrapper]


def wait_for_file(path, seconds=20):
    deadline = time.monotonic() + seconds
    while not (path.exists() and path.read_text()):
        assert time.monotonic() < deadline, f"{path.name} never appeared"
        time.sleep(0.02)
    return int(path.read_text())


@pytest.mark.skipif(not hasattr(os, "killpg") or not hasattr(signal, "SIGSTOP"), reason="needs POSIX process groups")
def test_a_suspended_pipeline_and_everything_it_started_are_gone_before_the_timeout_is_reported(tmp_path):
    """The reviewer's case: the pipeline is suspended after its child started, then the runner times out."""
    command = nested_command(tmp_path)

    async def scenario():
        task = asyncio.ensure_future(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=6, grace=0.5))
        sleeper = await asyncio.to_thread(wait_for_file, tmp_path / "sleeper.pid")
        os.kill(int((tmp_path / "parent.pid").read_text()), signal.SIGSTOP)
        return sleeper, await task

    sleeper, result = asyncio.run(scenario())
    assert result.timed_out and result.returncode == -9
    assert not running(sleeper), "a process the pipeline started outlived the reported termination"


# The pipeline stand-in again, suspended right after Popen returned: before run_child could do anything more.
PIPELINE_SUSPENDED_AFTER_POPEN = """
import os, signal, subprocess, sys, time
sys.path.insert(0, {scripts!r})
from portfolio_write_lock import run_child
open(os.path.join(sys.argv[1], 'parent.pid'), 'w').write(str(os.getpid()))
marker = os.path.join(sys.argv[1], 'sleeper.pid')


class Suspending(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        while not (os.path.exists(marker) and open(marker).read()):
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGSTOP)


subprocess.Popen = Suspending
run_child(sys.argv[2:], timeout=600)
"""


@pytest.mark.skipif(not hasattr(os, "killpg") or not hasattr(signal, "SIGSTOP"), reason="needs POSIX process groups")
def test_a_pipeline_suspended_before_it_could_record_its_child_leaves_nothing_running(tmp_path):
    """The reviewer's case: the wrapper has started its grandchild, the pipeline is stopped before recording."""
    from tests.engine_fakes import SCRIPTS
    command = nested_command(tmp_path)
    command[1] = str(tmp_path / "suspending.py")
    (tmp_path / "suspending.py").write_text(PIPELINE_SUSPENDED_AFTER_POPEN.format(scripts=str(SCRIPTS)))
    result = asyncio.run(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=4, grace=0.5))
    sleeper = int((tmp_path / "sleeper.pid").read_text())
    try:
        assert result.timed_out
        assert not running(sleeper) or sleeper in result.stray, "an unregistered grandchild outlived the run"
    finally:
        with suppress(ProcessLookupError):
            os.kill(sleeper, signal.SIGKILL)


# A pipeline child that starts a grandchild in a session of its own, out of every recorded group.
ESCAPING_CHILD = """
import os, subprocess, sys, time
subprocess.Popen([sys.executable, {sleeper!r}, {marker!r}], start_new_session=True)
time.sleep(120)
"""


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="the environment sweep reads /proc")
def test_on_linux_a_descendant_in_a_session_of_its_own_is_gone_after_a_timeout(tmp_path):
    from tests.engine_fakes import SCRIPTS
    (tmp_path / "sleeper.py").write_text(SLEEPER)
    (tmp_path / "escaping.py").write_text(ESCAPING_CHILD.format(sleeper=str(tmp_path / "sleeper.py"),
                                                                marker=str(tmp_path / "sleeper.pid")))
    command = child(tmp_path, PIPELINE.format(scripts=str(SCRIPTS))) + [str(tmp_path), sys.executable,
                                                                        str(tmp_path / "escaping.py")]

    async def scenario():
        task = asyncio.ensure_future(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=4, grace=0.5))
        sleeper = await asyncio.to_thread(wait_for_file, tmp_path / "sleeper.pid")
        return sleeper, await task

    sleeper, result = asyncio.run(scenario())
    try:
        assert result.timed_out and not running(sleeper), "a descendant outside the recorded groups survived"
    finally:
        with suppress(ProcessLookupError):
            os.kill(sleeper, signal.SIGKILL)


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="the environment sweep reads /proc")
def test_on_linux_a_process_left_behind_by_a_pipeline_that_exited_is_gone_before_the_result(tmp_path):
    (tmp_path / "sleeper.py").write_text(SLEEPER)
    marker = tmp_path / "sleeper.pid"
    command = child(tmp_path, "import os, subprocess, sys, time\n"
                    f"subprocess.Popen([sys.executable, {str(tmp_path / 'sleeper.py')!r}, {str(marker)!r}])\n"
                    f"while not (os.path.exists({str(marker)!r}) and open({str(marker)!r}).read()):\n"
                    "    time.sleep(0.01)\n")
    result = asyncio.run(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=30, grace=0.5))
    sleeper = int(marker.read_text())
    try:
        assert result.returncode == 0 and not result.timed_out
        assert not running(sleeper), "the run was reported while a process it started still ran"
    finally:
        with suppress(ProcessLookupError):
            os.kill(sleeper, signal.SIGKILL)


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="the environment sweep reads /proc")
def test_on_linux_a_timeout_is_reported_while_a_process_left_behind_still_holds_the_output(tmp_path):
    """The pipeline runs too long; a process it started in a session of its own holds the output pipe.

    Stopping the pipeline must not wait for that pipe to close (from Python 3.12 ``Process.wait()``
    does): the timeout is reported within the grace and the sweep, and the process is gone by then.
    """
    (tmp_path / "sleeper.py").write_text(SLEEPER)
    marker = tmp_path / "sleeper.pid"
    command = child(tmp_path, "import subprocess, sys, time\n"
                    f"subprocess.Popen([sys.executable, {str(tmp_path / 'sleeper.py')!r}, {str(marker)!r}],"
                    " start_new_session=True)\n"
                    "time.sleep(120)\n")

    async def bounded():
        return await asyncio.wait_for(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=2, grace=0.5),
                                      30)
    started = time.monotonic()
    try:
        result = asyncio.run(bounded())
        took = time.monotonic() - started
        sleeper = int(marker.read_text())
        assert result.timed_out and took < 20, f"the timeout was reported after {took:.0f} s"
        assert not running(sleeper), "the timeout was reported while a process it started still ran"
    finally:
        if marker.exists() and marker.read_text():
            with suppress(ProcessLookupError):
                os.kill(int(marker.read_text()), signal.SIGKILL)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux-only")
def test_on_linux_a_pipeline_child_dies_with_the_pipeline(tmp_path):
    from tests.engine_fakes import SCRIPTS
    (tmp_path / "sleeper.py").write_text(SLEEPER)
    parent = subprocess.Popen(child(tmp_path, PIPELINE.format(scripts=str(SCRIPTS))) + [
        str(tmp_path), sys.executable, str(tmp_path / "sleeper.py"), str(tmp_path / "sleeper.pid")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        sleeper = wait_for_file(tmp_path / "sleeper.pid")
    finally:
        parent.kill()
        parent.wait(30)
    deadline = time.monotonic() + 5
    while running(sleeper) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not running(sleeper), "the parent-death signal stops the child"


def test_runner_stops_the_child_when_the_backend_stops(tmp_path):
    marker = tmp_path / "child.pid"
    command = child(tmp_path, f"import os, time\nopen({str(marker)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")

    async def cancel_while_running():
        task = asyncio.ensure_future(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=60, grace=5))
        for _ in range(400):
            await asyncio.sleep(0.025)
            if marker.exists() and marker.read_text():
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_while_running())
    assert not alive(int(marker.read_text()))


# ── Round 3: what a snapshot, a report and a day's holdings must be ──

MONDAY = "2026-11-16"
MONDAY_CLOSE = 1794812400        # 15:00 Shanghai, Monday 2026-11-16


def report_file(tmp_path, day=TUESDAY):
    return tmp_path / "checkout" / "engine" / "reports" / f"portfolio-{day.replace('-', '')}.md"


def test_a_snapshot_of_another_day_saved_under_this_days_name_is_refused(tmp_path, portfolio):
    """The reviewer's case: Monday's snapshot under Tuesday's file name; nothing of it may reach Tuesday."""
    scripts = engine_copy(tmp_path / "checkout")
    with price_server({"SHA:600001": 12.5}, times={"SHA:600001": MONDAY_CLOSE}) as base:
        priced(portfolio, base)
        assert run(MONDAY, engine_dir=scripts, portfolio_dir=portfolio).status == "ok"
    monday = (portfolio / "snapshots" / f"{MONDAY}.json").read_text()
    (portfolio / "snapshots" / f"{TUESDAY}.json").write_text(monday)
    outcome = run(engine_dir=scripts, portfolio_dir=portfolio)
    assert (outcome.status, outcome.error_category) == ("failed", "engine_error")
    assert "日期" in outcome.detail["log_tail"]
    assert history_row(portfolio) is None and not report_file(tmp_path).exists()


def test_a_report_replaced_after_it_was_made_is_made_again(tmp_path, portfolio):
    """The reviewer's case: the report replaced by "total 100" still passed."""
    scripts = engine_copy(tmp_path / "checkout")
    with price_server({"SHA:600001": 12.5}) as base:
        priced(portfolio, base)
        assert run(engine_dir=scripts, portfolio_dir=portfolio).status == "ok"
    report = report_file(tmp_path)
    report.write_text("total 100")
    again = run(engine_dir=scripts, portfolio_dir=portfolio)
    assert (again.status, again.summary) == ("ok", "当日快照已存在；已补生成日报" + LEGACY)
    assert "total 100" not in report.read_text() and TUESDAY in report.read_text()


def test_a_report_that_is_not_the_one_the_checkpoint_names_is_not_success(fake_engine, portfolio):
    import hashlib
    runner = FakeRunner(EngineResult(0, "done"), write_snapshot=portfolio)
    assert run(engine_dir=fake_engine, portfolio_dir=portfolio, runner=runner).status == "ok"
    saved = hashlib.sha256((portfolio / "snapshots" / f"{TUESDAY}.json").read_bytes()).hexdigest()
    (portfolio / "pipeline-state").mkdir()
    (portfolio / "pipeline-state" / f"{TUESDAY}.json").write_text(json.dumps(
        {"snapshot": True, "history": True, "report": True, "push": "not_started", "snapshot_sha": saved,
         "history_snapshot": saved, "report_snapshot": saved, "report_sha": "0" * 64}))
    outcome = run(engine_dir=fake_engine, portfolio_dir=portfolio, runner=runner)
    assert (outcome.status, outcome.error_category) == ("failed", "incomplete") and "日报" in outcome.summary


def test_holdings_dated_after_the_day_are_never_used_for_it(tmp_path):
    """The reviewer's case: no ledger, only Wednesday's holdings; Tuesday's routine recorded Wednesday's 700."""
    scripts = engine_copy(tmp_path / "checkout")
    folder = engine_portfolio(tmp_path / "portfolio", "http://127.0.0.1:9/unused", day="2026-11-18", cash=700)
    outcome = run(engine_dir=scripts, portfolio_dir=folder)
    assert (outcome.status, outcome.error_category) == ("unavailable", "no_holdings_for_date")
    assert TUESDAY in outcome.summary
    assert not (folder / "snapshots").exists() and not (folder / "history.csv").exists()
    assert sorted(path.name for path in (folder / "holdings").iterdir()) == ["2026-11-18.json"]


def test_a_ledger_that_starts_after_the_day_has_no_holdings_for_it(tmp_path, fake_engine):
    folder = tmp_path / "ledger-later"
    build_ledger(folder, [opening(cash={"CNY": "700"}, date="2026-11-18")])
    (folder / "config.json").write_text("{}")
    runner = FakeRunner(write_snapshot=folder)
    outcome = run(engine_dir=fake_engine, portfolio_dir=folder, runner=runner)
    assert (outcome.status, outcome.error_category) == ("unavailable", "no_holdings_for_date")
    assert runner.calls == []


def test_the_engine_itself_never_clones_later_holdings_into_an_earlier_day(tmp_path):
    scripts = engine_copy(tmp_path / "checkout")
    folder = engine_portfolio(tmp_path / "portfolio", "http://127.0.0.1:9/unused", day="2026-11-18", cash=700)
    result = subprocess.run([sys.executable, str(scripts / "portfolio_daily_update.py"), "--action", "auto-pipeline",
                             "--date", TUESDAY], cwd=scripts, env=engine_env(folder), capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 1 and close_snapshot.NO_HOLDINGS_MARKER in result.stdout + result.stderr
    assert sorted(path.name for path in (folder / "holdings").iterdir()) == ["2026-11-18.json"]
    assert not (folder / "snapshots").exists()


def test_reading_the_ledger_to_decide_never_changes_it(tmp_path, fake_engine):
    folder = tmp_path / "empty-ledger"
    folder.mkdir()
    (folder / "ledger.sqlite3").write_bytes(b"")
    outcome = run(engine_dir=fake_engine, portfolio_dir=folder, runner=FakeRunner())
    assert (outcome.status, outcome.error_category) == ("skipped", "no_data")
    assert (folder / "ledger.sqlite3").stat().st_size == 0


US_TICKER = "NASDAQ:EXMP"
US_CLOSE_TUESDAY = 1794949200      # 16:00 New York, Tuesday 2026-11-17
US_CLOSE_WEDNESDAY = 1795035600


def us_snapshot(tmp_path, currency, moment):
    """Run the real snapshot for TUESDAY on one US share priced 10, at USD/CNY 7.2; returns (result, snapshot)."""
    with price_server({US_TICKER: 10.0, "FX:USDCNY=X": 7.2}, currencies={US_TICKER: currency},
                      times={US_TICKER: moment}) as base:
        folder = engine_portfolio(tmp_path / "portfolio", base, day=TUESDAY, cash=0, cost_basis=0,
                                  positions=[position(US_TICKER, quantity=1, cost_price=10)])
        config = json.loads((folder / "config.json").read_text())
        (folder / "config.json").write_text(json.dumps({**config, "fx_tickers": {"USD_CNY": "USDCNY=X"}}))
        result = subprocess.run([sys.executable, str(SCRIPTS / "portfolio_snapshot.py"), "--date", TUESDAY],
                                cwd=SCRIPTS, env=engine_env(folder), capture_output=True, text=True, timeout=120)
    path = folder / "snapshots" / f"{TUESDAY}.json"
    return result, (json.loads(path.read_text()) if path.exists() else None)


@pytest.mark.parametrize("currency, moment", [(None, None), ("USD", None), (None, US_CLOSE_TUESDAY),
                                              ("HKD", US_CLOSE_TUESDAY), ("USD", US_CLOSE_WEDNESDAY)])
def test_a_quote_without_its_own_currency_and_session_is_not_booked(tmp_path, currency, moment):
    """The reviewer's case: a US quote of 10 without currency and time was booked as ¥10."""
    result, saved = us_snapshot(tmp_path, currency, moment)
    assert result.returncode != 0 and saved is None
    assert US_TICKER in result.stderr and "Traceback" not in result.stderr


def test_a_quote_with_its_currency_and_session_is_booked_in_yuan(tmp_path):
    result, saved = us_snapshot(tmp_path, "USD", US_CLOSE_TUESDAY)
    assert result.returncode == 0, result.stderr
    assert saved["summary"]["total_value"] == 72


# ── Round 3: cleanup never signals a process it cannot prove is the run's ──

def sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="the environment sweep reads /proc")
def test_the_sweep_never_signals_a_process_that_does_not_carry_the_run_token(monkeypatch):
    """The reviewer's case: a matched worker exits and its pid is reused before the signal."""
    stranger = sleeper()
    try:
        monkeypatch.setattr(close_snapshot, "_token_processes", lambda token: (stranger.pid,))
        assert close_snapshot._sweep("a-token", time.monotonic() + 1) == ()
        time.sleep(0.1)
        assert stranger.poll() is None, "a process without the token was signalled"
    finally:
        stranger.kill()
        stranger.wait()


@pytest.mark.skipif(not hasattr(os, "killpg") or not os.path.isdir("/proc"), reason="needs /proc start times")
def test_a_recorded_group_whose_leader_is_another_process_is_left_alone(tmp_path):
    """The group number was recorded for a process that started at another time: it is not the run's any more."""
    stranger = sleeper()
    folder = tmp_path / "children"
    folder.mkdir()
    (folder / str(stranger.pid)).write_text(f"{stranger.pid} linux:1")
    try:
        assert close_snapshot._stop_children(str(folder), 1) == ()
        time.sleep(0.1)
        assert stranger.poll() is None, "a reused group number was signalled"
    finally:
        stranger.kill()
        stranger.wait()


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="the environment sweep reads /proc")
def test_a_bound_process_that_was_reaped_is_never_signalled(monkeypatch):
    child_process = sleeper()
    handle = close_snapshot._Process.bind(child_process.pid)
    assert handle is not None and handle.alive()
    child_process.kill()
    child_process.wait()
    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, signum: sent.append((pid, signum)))
    monkeypatch.setattr(os, "killpg", lambda group, signum: sent.append((group, signum)))
    assert handle.alive() is False
    handle.kill(signal.SIGKILL)
    assert close_snapshot._signal_group(handle, child_process.pid, signal.SIGKILL) is False
    assert sent == []
    handle.close()


HELPER_PIPELINE = """
import subprocess, sys
sys.path.insert(0, {scripts!r})
from portfolio_write_lock import detached_environment
helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env=detached_environment(),
                          start_new_session=True)
open(sys.argv[1], "w").write(str(helper.pid))
"""


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="the environment sweep reads /proc")
def test_a_helper_that_opted_out_of_the_run_outlives_it(tmp_path):
    """Everything carrying the run's token belongs to the run; a long-lived helper clears it to stay."""
    marker = tmp_path / "helper.pid"
    command = child(tmp_path, HELPER_PIPELINE.format(scripts=str(Path(close_snapshot.__file__).resolve().parents[3]
                                                                  / "engine" / "scripts"))) + [str(marker)]
    result = asyncio.run(run_engine(command, env=dict(os.environ), cwd=tmp_path, timeout=30))
    helper = int(marker.read_text())
    try:
        assert result.returncode == 0 and result.leftover == () and result.stray == ()
        assert running(helper), "the helper that cleared the run token was stopped"
    finally:
        with suppress(ProcessLookupError):
            os.kill(helper, signal.SIGKILL)


def test_success_is_reported_only_after_cleanup_is_confirmed(fake_engine, portfolio):
    runner = FakeRunner(EngineResult(0, "done", leftover=(4242,)), write_snapshot=portfolio)
    outcome = run(engine_dir=fake_engine, portfolio_dir=portfolio, runner=runner)
    assert (outcome.status, outcome.error_category) == ("failed", "cleanup_unconfirmed")
    assert outcome.detail["leftover_groups"] == 1
    stray = run(engine_dir=fake_engine, portfolio_dir=portfolio,
                runner=FakeRunner(EngineResult(0, "done", stray=(4243,)), write_snapshot=portfolio))
    assert (stray.status, stray.error_category) == ("failed", "cleanup_unconfirmed")
