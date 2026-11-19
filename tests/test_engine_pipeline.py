"""The daily pipeline must be able to start its child scripts for real.

Other pipeline tests replace ``run_snapshot``/``run_report`` with stubs, which
hides a missing interpreter.  These tests launch tiny synthetic scripts.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

import portfolio_daily_update as daily
import portfolio_scheduler as scheduler


SCRIPTS = Path(__file__).resolve().parents[1] / 'engine/scripts'
DAY = '2026-01-02'

FAKE_SNAPSHOT = '''
import argparse, json, os, sys
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument("--date")
args = parser.parse_args()
folder = Path(os.environ["PORTFOLIO_DIR"]) / "snapshots"
folder.mkdir(parents=True, exist_ok=True)
(folder / (args.date + ".json")).write_text(json.dumps({"date": args.date, "python": sys.executable}))
# Like the real snapshot: the history row is written after the JSON.
(folder.parent / "history.csv").write_text("date,total_value\\n" + args.date + ",0\\n")
print("synthetic snapshot")
'''

HISTORY_HEADER = ('date,total_value,total_cost,total_profit,return_pct,daily_change,daily_change_pct,'
                  'max_drawdown_pct,capital_change,market_daily_change,market_daily_change_pct')
SUMMARY = {'total_value': 1500.5, 'total_cost': 1000, 'total_profit': 500.5, 'total_return_pct': 50.05,
           'daily_change': 0, 'daily_change_pct': None, 'max_drawdown_pct': 0, 'capital_change': 0,
           'market_daily_change': 0, 'market_daily_change_pct': None}

FAKE_REPORT = '''
import argparse
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument("--date")
parser.add_argument("-o")
args = parser.parse_args()
Path(args.o).write_text("Synthetic report " + args.date)
'''

FAKE_UPDATER = '''
import json, os, sys
from pathlib import Path
Path(os.environ["FAKE_UPDATER_LOG"]).write_text(json.dumps({"argv": sys.argv[1:], "python": sys.executable}))
'''


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A synthetic engine directory whose child scripts only write marker files."""
    scripts = tmp_path / 'engine' / 'scripts'
    scripts.mkdir(parents=True)
    (scripts / 'portfolio_snapshot.py').write_text(FAKE_SNAPSHOT)
    (scripts / 'portfolio_report.py').write_text(FAKE_REPORT)
    (scripts / 'portfolio_daily_update.py').write_text(FAKE_UPDATER)
    portfolio = tmp_path / 'portfolio'
    (portfolio / 'holdings').mkdir(parents=True)
    (portfolio / 'holdings' / f'{DAY}.json').write_text('{"groups":{}}')
    (portfolio / 'config.json').write_text('{}')
    empty_bin = tmp_path / 'empty-bin'
    empty_bin.mkdir()
    monkeypatch.setattr(daily, 'BASE_DIR', scripts)
    monkeypatch.setattr(daily, 'REPORTS_DIR', tmp_path / 'engine' / 'reports')
    monkeypatch.setattr(daily, 'PORTFOLIO_DIR', portfolio)
    monkeypatch.setattr(daily, 'HOLDINGS_DIR', portfolio / 'holdings')
    monkeypatch.setattr(daily, 'SNAPSHOTS_DIR', portfolio / 'snapshots')
    monkeypatch.setattr(daily, 'CONFIG_PATH', portfolio / 'config.json')
    monkeypatch.setattr(scheduler, 'BASE_DIR', scripts)
    monkeypatch.setattr(scheduler, 'LOG_DIR', tmp_path / 'engine' / 'logs')
    monkeypatch.setenv('PORTFOLIO_DIR', str(portfolio))
    monkeypatch.setenv('FAKE_UPDATER_LOG', str(tmp_path / 'updater.json'))
    # No conda anywhere: the default must not depend on it.
    monkeypatch.setenv('PATH', str(empty_bin))
    monkeypatch.delenv('CONDA_EXE', raising=False)
    monkeypatch.delenv('CONDA_ENV', raising=False)
    return tmp_path


def fake_conda(workspace, monkeypatch):
    """``conda run -n ENV python3 ...`` stand-in that records its arguments."""
    conda = executable(workspace / 'fake-conda', '#!/bin/sh\n'
                       'printf \'%s\\n\' "$@" > "$FAKE_CONDA_LOG"\n'
                       'shift 4\n'
                       'exec "$FAKE_PYTHON" "$@"\n')
    monkeypatch.setenv('FAKE_CONDA_LOG', str(workspace / 'conda.log'))
    monkeypatch.setenv('FAKE_PYTHON', sys.executable)
    monkeypatch.setenv('CONDA_EXE', str(conda))
    return workspace / 'conda.log'


def test_pipeline_children_run_with_the_current_interpreter_without_conda(workspace):
    assert daily.run_snapshot(DAY) is True
    saved = json.loads((workspace / 'portfolio/snapshots' / f'{DAY}.json').read_text())
    assert saved == {'date': DAY, 'python': sys.executable}
    report = daily.run_report(DAY)
    assert report is not None and report.read_text() == f'Synthetic report {DAY}'


def test_whole_pipeline_runs_real_child_processes(workspace):
    assert daily.run_pipeline(DAY, send_report=False) is True
    state = json.loads((workspace / 'portfolio/pipeline-state' / f'{DAY}.json').read_text())
    assert state['snapshot'] is True and state['report'] is True and state['complete'] is True
    assert (workspace / 'engine/reports/portfolio-20260102.md').is_file()


def test_optional_conda_environment_is_used_by_pipeline_and_scheduler(workspace, monkeypatch):
    log = fake_conda(workspace, monkeypatch)
    monkeypatch.setenv('CONDA_ENV', 'research')
    assert daily.run_snapshot(DAY) is True
    assert log.read_text().split('\n')[:4] == ['run', '-n', 'research', 'python3']
    assert (workspace / 'portfolio/snapshots' / f'{DAY}.json').is_file()
    log.unlink()
    assert scheduler.run_action('clone', DAY) is True
    assert log.read_text().split('\n')[:4] == ['run', '-n', 'research', 'python3']


def test_missing_configured_conda_fails_without_a_traceback(workspace, monkeypatch, capsys):
    monkeypatch.setenv('CONDA_ENV', 'research')
    monkeypatch.setenv('CONDA_EXE', str(workspace / 'no-such-conda'))
    assert daily.run_snapshot(DAY) is False
    assert daily.run_report(DAY) is None
    assert 'no-such-conda' in capsys.readouterr().out


def test_auto_pipeline_without_a_feishu_chat_saves_snapshot_and_report_and_succeeds(workspace, capsys):
    assert daily.action_auto_pipeline(DAY) is True
    state = json.loads((workspace / 'portfolio/pipeline-state' / f'{DAY}.json').read_text())
    assert state['snapshot'] is True and state['report'] is True
    assert state['push'] == 'not_started' and state['complete'] is True
    assert '未配置 feishu_chat_id' in capsys.readouterr().out
    # Run again: today's snapshot exists, so nothing is repeated.
    assert daily.action_auto_pipeline(DAY) is True


def test_auto_pipeline_pushes_the_report_when_a_feishu_chat_is_configured(workspace, monkeypatch):
    (workspace / 'portfolio' / 'config.json').write_text('{"feishu_chat_id": "example-chat"}')
    pushed = []
    monkeypatch.setattr(daily, 'send_feishu_report', lambda day, text, config: pushed.append(day) or True)
    assert daily.action_auto_pipeline(DAY) is True
    assert pushed == [DAY]
    state = json.loads((workspace / 'portfolio/pipeline-state' / f'{DAY}.json').read_text())
    assert state['push'] == 'complete' and state['complete'] is True


def test_auto_pipeline_with_an_active_ledger_values_the_ledger_without_cloning(workspace):
    from tests.fakes import build_ledger, opening
    portfolio = workspace / 'portfolio'
    build_ledger(portfolio, [opening(cash={'CNY': '1000'}, contributed_cny='1000', date='2026-01-02')])
    later = '2026-01-05'
    assert daily.action_auto_pipeline(later) is True
    # The ledger refuses legacy holdings writes, so none was cloned for the new day.
    assert sorted(path.name for path in (portfolio / 'holdings').iterdir()) == [f'{DAY}.json']
    assert (portfolio / 'snapshots' / f'{later}.json').is_file()
    state = json.loads((portfolio / 'pipeline-state' / f'{later}.json').read_text())
    assert state['holdings_hash'] == daily.holdings_digest(later) and state['complete'] is True


def saved_snapshot_only(workspace, checkpoint=None):
    """The snapshot JSON as the engine saves it, and nothing after it: the process died right there."""
    folder = workspace / 'portfolio' / 'snapshots'
    folder.mkdir(parents=True)
    (folder / f'{DAY}.json').write_text(json.dumps({'date': DAY, 'summary': SUMMARY}))
    (workspace / 'engine' / 'scripts' / 'portfolio_snapshot.py').write_text('raise SystemExit("made again")\n')
    if checkpoint is not None:
        (workspace / 'portfolio' / 'pipeline-state').mkdir()
        (workspace / 'portfolio' / 'pipeline-state' / f'{DAY}.json').write_text(json.dumps(
            {'holdings_hash': daily.holdings_digest(DAY), 'snapshot': False, 'report': False, **checkpoint}))


def checkpoint_of(workspace):
    return json.loads((workspace / 'portfolio/pipeline-state' / f'{DAY}.json').read_text())


@pytest.mark.parametrize('checkpoint', [None, {'push': 'not_started'}])
def test_auto_pipeline_completes_a_day_whose_snapshot_exists_without_making_it_again(workspace, capsys, checkpoint):
    saved_snapshot_only(workspace, checkpoint)
    assert daily.action_auto_pipeline(DAY) is True
    out = capsys.readouterr().out
    assert '今日快照已存在' in out and '已补写当日历史记录' in out and '已补生成日报' in out
    assert (workspace / 'portfolio/history.csv').read_text().splitlines() == [
        HISTORY_HEADER, f'{DAY},1500.5,1000,500.5,50.05,0,,0,0,0,']
    assert (workspace / 'engine/reports/portfolio-20260102.md').read_text() == f'Synthetic report {DAY}'
    state = checkpoint_of(workspace)
    assert state['snapshot'] and state['history'] and state['report'] and state['complete']
    assert daily.action_auto_pipeline(DAY) is True
    assert '已补' not in capsys.readouterr().out, 'a complete day is left alone'


def test_auto_pipeline_pushes_a_report_never_sent_but_never_resends_an_uncertain_one(workspace, monkeypatch):
    (workspace / 'portfolio' / 'config.json').write_text('{"feishu_chat_id": "example-chat"}')
    pushed = []
    monkeypatch.setattr(daily, 'send_feishu_report', lambda day, text, config: pushed.append(day) or True)
    saved_snapshot_only(workspace, {'push': 'not_started'})
    assert daily.action_auto_pipeline(DAY) is True
    assert pushed == [DAY] and checkpoint_of(workspace)['push'] == 'complete'
    assert daily.action_auto_pipeline(DAY) is True and pushed == [DAY]

    state = {**checkpoint_of(workspace), 'push': 'uncertain', 'complete': False}
    (workspace / 'portfolio/pipeline-state' / f'{DAY}.json').write_text(json.dumps(state))
    assert daily.action_auto_pipeline(DAY) is True, 'snapshot, history and report are done'
    assert pushed == [DAY] and checkpoint_of(workspace)['push'] == 'uncertain'


def test_a_snapshot_whose_history_cannot_be_derived_fails_the_pipeline(workspace, capsys):
    saved_snapshot_only(workspace)
    (workspace / 'portfolio/snapshots' / f'{DAY}.json').write_text('{"date": "%s"}' % DAY)   # no summary
    assert daily.action_auto_pipeline(DAY) is False
    assert 'history.csv' in capsys.readouterr().err
    assert not (workspace / 'portfolio/history.csv').exists()


# A snapshot stand-in that writes what the real one writes: the JSON with a summary,
# then the day's history row (with the real upsert_history). FAKE_TOTAL sets the total;
# FAKE_SNAPSHOT_END=after_json dies right after the JSON, =before_json fails before it.
STAGED_SNAPSHOT = """
import argparse, json, os, signal, sys
sys.path.insert(0, {scripts!r})
from portfolio_snapshot import upsert_history
parser = argparse.ArgumentParser()
parser.add_argument("--date")
args = parser.parse_args()
end = os.environ.get("FAKE_SNAPSHOT_END", "")
if end == "before_json":
    sys.exit("synthetic failure before the snapshot was saved")
portfolio = os.environ["PORTFOLIO_DIR"]
total = float(os.environ.get("FAKE_TOTAL", "100"))
snapshot = {{"date": args.date, "summary": {{**{summary!r}, "total_value": total}}}}
os.makedirs(os.path.join(portfolio, "snapshots"), exist_ok=True)
with open(os.path.join(portfolio, "snapshots", args.date + ".json"), "w") as handle:
    json.dump(snapshot, handle)
if end == "after_json":
    os.kill(os.getpid(), signal.SIGKILL)
upsert_history(snapshot, args.date, portfolio)
print("staged snapshot", total)
"""

# A report stand-in that shows which snapshot it was built from.
SNAPSHOT_REPORT = """
import argparse, json, os
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument("--date")
parser.add_argument("-o")
args = parser.parse_args()
snapshot = json.loads((Path(os.environ["PORTFOLIO_DIR"]) / "snapshots" / (args.date + ".json")).read_text())
Path(args.o).write_text("report of total " + str(snapshot["summary"]["total_value"]))
"""


@pytest.fixture
def staged(workspace, monkeypatch):
    scripts = workspace / 'engine' / 'scripts'
    (scripts / 'portfolio_snapshot.py').write_text(STAGED_SNAPSHOT.format(scripts=str(SCRIPTS), summary=SUMMARY))
    (scripts / 'portfolio_report.py').write_text(SNAPSHOT_REPORT)
    monkeypatch.delenv('FAKE_SNAPSHOT_END', raising=False)
    monkeypatch.setenv('FAKE_TOTAL', '100')
    return workspace


def day_row(workspace):
    rows = [line for line in (workspace / 'portfolio/history.csv').read_text().splitlines() if line.startswith(DAY + ',')]
    assert len(rows) == 1
    return rows[0].split(',')


def report_text(workspace):
    return (workspace / 'engine/reports/portfolio-20260102.md').read_text()


def test_a_regeneration_stopped_after_its_snapshot_json_completes_history_from_that_snapshot(staged, monkeypatch):
    """The reviewer's case: total 100 saved and recorded; a regeneration saves 200 and dies before history.csv."""
    assert daily.run_pipeline(DAY, send_report=False) is True
    assert day_row(staged)[1] == '100.0' and report_text(staged) == 'report of total 100.0'
    (staged / 'portfolio/holdings' / f'{DAY}.json').write_text('{"groups":{"Changed":{}}}')
    monkeypatch.setenv('FAKE_TOTAL', '200')
    monkeypatch.setenv('FAKE_SNAPSHOT_END', 'after_json')
    assert daily.run_pipeline(DAY, send_report=False) is False
    assert day_row(staged)[1] == '100.0', 'the old row is still there'

    monkeypatch.setenv('FAKE_SNAPSHOT_END', 'before_json')          # no price may be fetched again
    assert daily.action_auto_pipeline(DAY) is True
    assert day_row(staged)[1] == '200.0' and report_text(staged) == 'report of total 200.0'
    assert checkpoint_of(staged)['complete'] is True


def test_a_regeneration_that_failed_before_its_json_makes_the_snapshot_again(staged, monkeypatch):
    """The JSON on disk is the earlier one: the unfinished snapshot stage is not done."""
    assert daily.run_pipeline(DAY, send_report=False) is True
    (staged / 'portfolio/holdings' / f'{DAY}.json').write_text('{"groups":{"Changed":{}}}')
    monkeypatch.setenv('FAKE_TOTAL', '200')
    monkeypatch.setenv('FAKE_SNAPSHOT_END', 'before_json')
    assert daily.run_pipeline(DAY, send_report=False) is False
    monkeypatch.delenv('FAKE_SNAPSHOT_END')
    assert daily.action_auto_pipeline(DAY) is True
    saved = json.loads((staged / 'portfolio/snapshots' / f'{DAY}.json').read_text())
    assert saved['summary']['total_value'] == 200
    assert day_row(staged)[1] == '200.0' and report_text(staged) == 'report of total 200.0'


def test_a_report_of_an_older_snapshot_of_the_same_day_is_made_again(staged, monkeypatch):
    assert daily.run_pipeline(DAY, send_report=False) is True
    monkeypatch.setenv('FAKE_TOTAL', '200')
    # Another route (the snapshot entrypoint run by hand) replaces the day's snapshot and its row.
    subprocess.run([sys.executable, str(staged / 'engine/scripts/portfolio_snapshot.py'), '--date', DAY],
                   check=True, capture_output=True)
    assert day_row(staged)[1] == '200.0' and report_text(staged) == 'report of total 100.0'
    monkeypatch.setenv('FAKE_SNAPSHOT_END', 'before_json')
    assert daily.action_auto_pipeline(DAY) is True
    assert report_text(staged) == 'report of total 200.0'


def test_a_history_row_that_does_not_match_the_snapshot_is_rewritten_from_it(staged, monkeypatch, capsys):
    assert daily.run_pipeline(DAY, send_report=False) is True
    history = staged / 'portfolio/history.csv'
    history.write_text(history.read_text().replace(f'{DAY},100.0,', f'{DAY},999.0,'))
    monkeypatch.setenv('FAKE_SNAPSHOT_END', 'before_json')
    assert daily.action_auto_pipeline(DAY) is True
    assert day_row(staged)[1] == '100.0' and '已补写当日历史记录' in capsys.readouterr().out
    assert report_text(staged) == 'report of total 100.0'


def test_a_complete_snapshot_for_holdings_changed_since_is_kept_with_a_notice(staged, monkeypatch, capsys):
    """The auto pipeline makes a day once: a later change of holdings is reported, not silently re-priced."""
    assert daily.run_pipeline(DAY, send_report=False) is True
    (staged / 'portfolio/holdings' / f'{DAY}.json').write_text('{"groups":{"Changed":{}}}')
    monkeypatch.setenv('FAKE_SNAPSHOT_END', 'before_json')
    assert daily.action_auto_pipeline(DAY) is True
    assert '当日快照之后持仓有变化' in capsys.readouterr().out
    assert day_row(staged)[1] == '100.0' and report_text(staged) == 'report of total 100.0'


def test_pipeline_without_any_holdings_for_the_day_fails_with_a_reason(workspace, capsys):
    assert daily.run_pipeline('2025-12-31', send_report=False) is False
    assert '没有 2025-12-31 的持仓' in capsys.readouterr().err


def test_scheduler_runs_the_updater_with_the_current_interpreter(workspace):
    assert scheduler.run_action('auto-pipeline', DAY) is True
    recorded = json.loads((workspace / 'updater.json').read_text())
    assert recorded['python'] == sys.executable
    assert recorded['argv'] == ['--action', 'auto-pipeline', '--date', DAY]


@pytest.mark.parametrize('script', ['portfolio-cron.sh', 'portfolio-daily.sh'])
@pytest.mark.parametrize('conda_env', [None, 'research'])
def test_shell_entrypoints_share_the_interpreter_setting(workspace, monkeypatch, script, conda_env):
    bash = shutil.which('bash', path='/bin:/usr/bin:/usr/local/bin')
    if not bash:
        pytest.skip('bash is unavailable')
    scripts = workspace / 'engine' / 'scripts'
    shutil.copy(SCRIPTS / script, scripts / script)
    fake_bin = workspace / 'fake-bin'
    fake_bin.mkdir()
    # A fixed Friday that is not an exchange holiday keeps the test independent of the calendar.
    executable(fake_bin / 'date', '#!/bin/sh\ncase "$1" in\n'
               '  +%Y-%m-%d) echo 2026-01-09;;\n  +%u) echo 5;;\n  +%Y%m%d) echo 20260109;;\n  *) echo 12:00:00;;\nesac\n')
    executable(fake_bin / 'python3', f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    environment = {'PATH': f'{fake_bin}:/usr/bin:/bin', 'HOME': str(workspace),
                   'FAKE_UPDATER_LOG': str(workspace / 'updater.json')}
    if conda_env:
        log = fake_conda(workspace, monkeypatch)
        environment.update(CONDA_ENV=conda_env, CONDA=os.environ['CONDA_EXE'], CONDA_EXE=os.environ['CONDA_EXE'],
                           FAKE_CONDA_LOG=str(log), FAKE_PYTHON=sys.executable)
    result = subprocess.run([bash, str(scripts / script), 'pipeline'], env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = json.loads((workspace / 'updater.json').read_text())
    assert recorded['argv'] == ['--action', 'pipeline', '--date', '2026-01-09']
    if conda_env:
        assert log.read_text().split('\n')[:4] == ['run', '-n', conda_env, 'python3']
    else:
        assert not (workspace / 'conda.log').exists()



@pytest.mark.parametrize('status', [0, 3])
def test_cron_script_returns_and_logs_the_pipeline_exit_code(workspace, status):
    bash = shutil.which('bash', path='/bin:/usr/bin:/usr/local/bin')
    if not bash:
        pytest.skip('bash is unavailable')
    scripts = workspace / 'engine' / 'scripts'
    shutil.copy(SCRIPTS / 'portfolio-cron.sh', scripts / 'portfolio-cron.sh')
    (scripts / 'portfolio_daily_update.py').write_text(
        f'import sys\nprint("synthetic pipeline output")\nsys.exit({status})\n')
    fake_bin = workspace / 'fake-bin'
    fake_bin.mkdir()
    executable(fake_bin / 'date', '#!/bin/sh\ncase "$1" in\n'
               '  +%Y-%m-%d) echo 2026-01-09;;\n  +%u) echo 5;;\n  +%Y%m%d) echo 20260109;;\n  *) echo 12:00:00;;\nesac\n')
    executable(fake_bin / 'python3', f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    result = subprocess.run([bash, str(scripts / 'portfolio-cron.sh'), 'pipeline'],
                            env={'PATH': f'{fake_bin}:/usr/bin:/bin', 'HOME': str(workspace)},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == status, result.stdout + result.stderr
    log = (workspace / 'engine' / 'logs' / 'portfolio-20260109.log').read_text()
    assert 'synthetic pipeline output' in log
    # The final log line records the pipeline's own status, including a failure.
    assert log.strip().split('\n')[-1].endswith(f'(exit: {status})'), log


# ── Trading calendar ──

# Shanghai Stock Exchange notice 上证公告〔2025〕45号 (2025-12-22), "关于上海证券交易所
# 2026年部分节假日休市安排的通知": inclusive closure ranges for 2026.
CLOSURES_2026 = [
    ('2026-01-01', '2026-01-03'),  # 元旦
    ('2026-02-15', '2026-02-23'),  # 春节
    ('2026-04-04', '2026-04-06'),  # 清明节
    ('2026-05-01', '2026-05-05'),  # 劳动节
    ('2026-06-19', '2026-06-21'),  # 端午节
    ('2026-09-25', '2026-09-27'),  # 中秋节
    ('2026-10-01', '2026-10-07'),  # 国庆节
]


def weekday_closures():
    from datetime import date, timedelta
    days = set()
    for first, last in CLOSURES_2026:
        day = date.fromisoformat(first)
        while day <= date.fromisoformat(last):
            if day.weekday() < 5:
                days.add(day.isoformat())
            day += timedelta(days=1)
    return days


def test_2026_holiday_tables_match_the_exchange_schedule():
    import re
    expected = weekday_closures()
    assert len(expected) == 19
    assert scheduler.HOLIDAYS == expected
    for script in ('portfolio-cron.sh', 'portfolio-daily.sh'):
        listed = re.search(r'^HOLIDAYS="([^"]*)"', (SCRIPTS / script).read_text(), re.M).group(1).split()
        assert set(listed) == expected and len(listed) == len(expected), script


@pytest.mark.parametrize('day, trading', [
    ('2026-01-02', False),  # Friday inside the New Year closure
    ('2026-01-26', True),   # an ordinary Monday, formerly listed as a holiday
    ('2026-02-13', True),   # last session before the Spring Festival
    ('2026-02-14', False),  # Saturday: a working day, but the exchange stays closed
    ('2026-02-18', False),
    ('2026-02-23', False),
    ('2026-02-24', True),
    ('2026-04-06', False),
    ('2026-05-04', False),
    ('2026-09-25', False),
    ('2026-10-07', False),
    ('2026-10-08', True),
])
def test_scheduler_trading_days_in_2026(day, trading):
    from datetime import datetime
    assert scheduler.is_trading_day(datetime.fromisoformat(day + 'T18:00:00')) is trading


def test_scheduler_times_are_documented_as_beijing_time(capsys, monkeypatch):
    # Its days are the trading days (Shanghai's dates), so its times are Beijing's too, whatever the machine's zone.
    monkeypatch.setattr(sys, 'argv', ['portfolio_scheduler.py', '--help'])
    with pytest.raises(SystemExit):
        scheduler.main()
    text = capsys.readouterr().out
    assert 'local time' not in text
    assert text.count('Beijing time') == 2


@pytest.mark.parametrize('clock, runs', [
    ('17:57', False), ('17:58', True), ('18:00', True), ('18:02', True), ('18:03', False),
])
def test_scheduler_runs_from_two_minutes_before_to_two_minutes_after_the_target(monkeypatch, clock, runs):
    from datetime import datetime
    hour, minute = map(int, clock.split(':'))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 9, hour, minute)  # a Friday trading day

    started = []
    monkeypatch.setattr(scheduler, 'datetime', Clock)
    monkeypatch.setattr(scheduler, 'run_action', lambda action, day: started.append((action, day)))
    monkeypatch.setattr(scheduler, 'log', lambda message: None)
    monkeypatch.setattr(scheduler, 'daily_state', {'notified_date': None, 'pipeline_date': None})
    scheduler.check_and_run(18, 0, 23, 0)
    assert started == ([('notify', '2026-01-09')] if runs else [])
