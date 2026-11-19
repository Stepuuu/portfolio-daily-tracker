"""The card, CLI and agent entrypoints must share one financial-write lock."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import portfolio_daily_update as daily
from portfolio_write_lock import portfolio_write_lock, portfolio_write_locked


SCRIPTS = Path(__file__).resolve().parents[1] / 'engine/scripts'


def assert_locked(directory):
    with (directory / '.portfolio-update.lock').open('a+b') as handle:
        with pytest.raises(BlockingIOError):
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_nested_calls_reuse_the_lock_and_exceptions_release_it(tmp_path):
    @portfolio_write_locked(lambda: tmp_path)
    def nested():
        with portfolio_write_lock(tmp_path):
            assert_locked(tmp_path)
            raise ValueError('expected')

    with pytest.raises(ValueError, match='expected'):
        nested()
    with (tmp_path / '.portfolio-update.lock').open('a+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert (tmp_path / '.portfolio-update.lock').stat().st_mode & 0o077 == 0


def test_parallel_threads_cannot_lose_a_read_modify_write(tmp_path):
    counter = tmp_path / 'counter'
    counter.write_text('0')

    def increment(_index):
        with portfolio_write_lock(tmp_path):
            value = int(counter.read_text())
            time.sleep(.002)
            counter.write_text(str(value + 1))

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(increment, range(20)))
    assert counter.read_text() == '20'


def test_lock_cooperates_with_raw_flock_in_another_process(tmp_path):
    code = '''
from pathlib import Path
import sys
from portfolio_write_lock import portfolio_write_lock
print('ready', flush=True)
with portfolio_write_lock(sys.argv[1]):
    Path(sys.argv[1], 'finished').write_text('done')
'''
    env = {**os.environ, 'PYTHONPATH': str(SCRIPTS)}
    with (tmp_path / '.portfolio-update.lock').open('a+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        process = subprocess.Popen([sys.executable, '-c', code, str(tmp_path)], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert process.stdout.readline().strip() == 'ready'
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=.1)
            assert not (tmp_path / 'finished').exists()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            process.communicate(timeout=5)
    assert process.returncode == 0
    assert (tmp_path / 'finished').read_text() == 'done'


def test_cli_waits_for_lock_before_reading_its_holdings(tmp_path):
    holdings = tmp_path / 'holdings'
    holdings.mkdir()
    source = holdings / '2000-01-01.json'
    source.write_text(json.dumps({'date': '2000-01-01', 'groups': {'demo': {'cash': 0, 'positions': []}}}))
    code = '''
import sys
import portfolio_manager as manager
sys.argv = ['portfolio_manager', '--date', '2000-01-01', 'set-cash', '--group', 'demo', '--value', '42']
print('ready', flush=True)
manager.main()
'''
    env = {**os.environ, 'PYTHONPATH': str(SCRIPTS), 'PORTFOLIO_DIR': str(tmp_path)}
    with portfolio_write_lock(tmp_path):
        process = subprocess.Popen([sys.executable, '-c', code], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert process.stdout.readline().strip() == 'ready'
        with pytest.raises(subprocess.TimeoutExpired):
            process.communicate(timeout=.1)
        assert json.loads(source.read_text())['groups']['demo']['cash'] == 0
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert json.loads(source.read_text())['groups']['demo']['cash'] == 42


def test_concurrent_agent_updates_lock_the_entire_operation(tmp_path, monkeypatch):
    from core.tools import update_holdings_tool
    monkeypatch.setattr(daily, 'PORTFOLIO_DIR', tmp_path)
    state = {'quantity': 0}

    def clone(_day):
        assert_locked(tmp_path)
        return tmp_path / 'holdings.json', False

    def load(_day):
        assert_locked(tmp_path)
        return dict(state)

    def parse(holdings, _text):
        time.sleep(.01)
        holdings['quantity'] += 1
        return [{'action': 'test_update', 'description': 'synthetic change'}]

    def save(holdings, _day):
        assert_locked(tmp_path)
        state.update(holdings)

    monkeypatch.setattr(daily, 'clone_holdings', clone)
    monkeypatch.setattr(daily, 'load_holdings', load)
    monkeypatch.setattr(daily, 'parse_and_apply_changes', parse)
    monkeypatch.setattr(daily, 'save_holdings', save)
    function = update_holdings_tool().function

    async def run():
        return await asyncio.gather(*(function('2000-01-01', 'synthetic') for _ in range(8)))

    assert all(item['success'] for item in asyncio.run(run()))
    assert state['quantity'] == 8


def test_agent_pipeline_uses_configured_portfolio_and_holds_lock(tmp_path, monkeypatch):
    from core.tools import run_portfolio_pipeline_tool
    monkeypatch.setattr(daily, 'PORTFOLIO_DIR', tmp_path)
    snapshots = tmp_path / 'snapshots'
    snapshots.mkdir()
    (snapshots / '2000-01-01.json').write_text(json.dumps({'summary': {'total_value': 42}}))

    def clone(_day):
        assert_locked(tmp_path)
        return tmp_path / 'holdings.json', False

    def pipeline(_day, *, send_report):
        assert send_report is False
        assert_locked(tmp_path)
        return True

    monkeypatch.setattr(daily, 'clone_holdings', clone)
    monkeypatch.setattr(daily, 'run_pipeline', pipeline)
    result = asyncio.run(run_portfolio_pipeline_tool().function('2000-01-01', False))
    assert result['success'] is True
    assert result['summary']['total_value'] == 42


def snapshot_workspace(tmp_path, cash=100):
    """An offline portfolio: CNY cash only and no exchange-rate downloads."""
    (tmp_path / 'holdings').mkdir()
    (tmp_path / 'config.json').write_text(json.dumps({'fx_tickers': {}}))
    holdings = tmp_path / 'holdings' / '2000-01-03.json'
    holdings.write_text(json.dumps({'date': '2000-01-03', 'groups': {
        'demo': {'cash': cash, 'fund': 0, 'cost_basis': 100, 'positions': []}}}))
    return holdings


def test_snapshot_cli_waits_for_the_lock_and_sees_the_finished_update(tmp_path):
    holdings = snapshot_workspace(tmp_path)
    snapshot = tmp_path / 'snapshots' / '2000-01-03.json'
    code = '''
import sys
import portfolio_snapshot as snapshot
sys.argv = ['portfolio_snapshot', '--date', '2000-01-03']
print('ready', flush=True)
snapshot.main()
'''
    env = {**os.environ, 'PYTHONPATH': str(SCRIPTS), 'PORTFOLIO_DIR': str(tmp_path)}
    env.pop('PORTFOLIO_WRITE_LOCK_INHERITED', None)
    with portfolio_write_lock(tmp_path):
        process = subprocess.Popen([sys.executable, '-c', code], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert process.stdout.readline().strip() == 'ready'
        with pytest.raises(subprocess.TimeoutExpired):
            process.communicate(timeout=1)
        assert not snapshot.exists() and not (tmp_path / 'history.csv').exists()
        # The writer that owns the lock finishes its update before the snapshot may read.
        data = json.loads(holdings.read_text())
        data['groups']['demo']['cash'] = 142
        holdings.write_text(json.dumps(data))
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr
    assert json.loads(snapshot.read_text())['summary']['total_value'] == 142
    assert (tmp_path / 'history.csv').read_text().split('\n')[1].startswith('2000-01-03,142')


def test_snapshot_dry_run_writes_nothing_and_does_not_wait(tmp_path):
    snapshot_workspace(tmp_path)
    env = {**os.environ, 'PYTHONPATH': str(SCRIPTS), 'PORTFOLIO_DIR': str(tmp_path)}
    with portfolio_write_lock(tmp_path):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio_snapshot.py'), '--date', '2000-01-03',
                                 '--dry-run'], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'snapshots').exists() and not (tmp_path / 'history.csv').exists()


def test_pipeline_snapshot_child_runs_under_the_lock_its_parent_holds(tmp_path, monkeypatch):
    snapshot_workspace(tmp_path)
    monkeypatch.setattr(daily, 'PORTFOLIO_DIR', tmp_path)
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('CONDA_ENV', raising=False)
    # The CLI entrypoints, the agent tools and the Feishu adapter all call the
    # pipeline while owning the lock; the child must not wait for it.
    with portfolio_write_lock(tmp_path):
        assert daily.run_snapshot('2000-01-03') is True
        assert_locked(tmp_path)
    assert json.loads((tmp_path / 'snapshots' / '2000-01-03.json').read_text())['summary']['total_value'] == 100


def test_inherited_lock_only_covers_the_same_portfolio(tmp_path, monkeypatch):
    from portfolio_write_lock import inherited_lock_environment, lock_inherited
    first, second = tmp_path / 'first', tmp_path / 'second'
    assert not lock_inherited(first)
    environment = inherited_lock_environment(first, {'KEEP': '1'})
    assert environment['KEEP'] == '1'
    monkeypatch.setenv('PORTFOLIO_WRITE_LOCK_INHERITED', environment['PORTFOLIO_WRITE_LOCK_INHERITED'])
    assert lock_inherited(first) and lock_inherited(str(first) + '/')
    assert not lock_inherited(second)


# ── Platforms without fcntl (native Windows) ──

WITHOUT_FCNTL = '''
import os, runpy, sys
sys.modules['fcntl'] = None  # importing it now fails, as on native Windows
script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(script))
runpy.run_path(script, run_name='__main__')
'''


def run_without_fcntl(tmp_path, script, *arguments):
    # Unrelated import warnings (e.g. from urllib3) would hide the one-line message.
    env = {**os.environ, 'PORTFOLIO_DIR': str(tmp_path), 'PYTHONWARNINGS': 'ignore'}
    env.pop('PORTFOLIO_WRITE_LOCK_INHERITED', None)
    return subprocess.run([sys.executable, '-c', WITHOUT_FCNTL, str(SCRIPTS / script), *arguments],
                          env=env, capture_output=True, text=True, timeout=60)


def test_entrypoints_that_need_no_lock_work_without_fcntl(tmp_path):
    snapshot_workspace(tmp_path)
    result = run_without_fcntl(tmp_path, 'portfolio_snapshot.py', '--date', '2000-01-03', '--dry-run')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout[result.stdout.index('{'):result.stdout.index('\n=')])['summary']['total_value'] == 100
    result = run_without_fcntl(tmp_path, 'portfolio_scheduler.py', '--help')
    assert result.returncode == 0, result.stderr
    assert '--notify-time' in result.stdout


def test_a_write_without_fcntl_fails_with_one_clear_line(tmp_path):
    snapshot_workspace(tmp_path)
    result = run_without_fcntl(tmp_path, 'portfolio_snapshot.py', '--date', '2000-01-03')
    assert result.returncode != 0
    assert 'Traceback' not in result.stderr
    message = result.stderr.strip()
    assert '\n' not in message and 'fcntl' in message and 'lock' in message
    assert not (tmp_path / 'snapshots').exists() and not (tmp_path / 'history.csv').exists()


# The child lists the records it can see once its own has appeared, then prints its pid.
LIST_RECORDS = '''
import os, time
folder = os.environ["PORTFOLIO_CHILD_GROUPS_DIR"]
deadline = time.time() + 10
while str(os.getpid()) not in os.listdir(folder) and time.time() < deadline:
    time.sleep(.01)
print(sorted(os.listdir(folder)), os.getpid())
'''


def test_run_child_records_its_childs_group_only_while_the_child_runs(tmp_path, monkeypatch):
    """What a runner needs to kill the pipeline's children, which live in sessions of their own."""
    import subprocess as process
    from portfolio_write_lock import CHILD_GROUPS_ENV, run_child
    folder = tmp_path / 'groups'
    folder.mkdir()
    monkeypatch.setenv(CHILD_GROUPS_ENV, str(folder))
    listed, pid = run_child([sys.executable, '-c', LIST_RECORDS], timeout=30).stdout.rsplit(' ', 1)
    assert listed == repr([pid.strip()]) and os.getpgid(os.getpid()) != int(pid)
    assert list(folder.iterdir()) == []
    with pytest.raises(process.TimeoutExpired):
        run_child([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.5)
    assert list(folder.iterdir()) == [], 'a stopped child leaves no record'
    monkeypatch.delenv(CHILD_GROUPS_ENV)
    assert run_child([sys.executable, '-c', 'print(1)'], timeout=30).stdout == '1\n'


def test_a_child_that_cannot_record_its_group_never_runs_and_a_failed_exec_leaves_no_record(tmp_path, monkeypatch):
    """The child records its own group before it executes anything; without the record it does not run."""
    from portfolio_write_lock import CHILD_GROUPS_ENV, run_child
    marker = tmp_path / 'ran'
    monkeypatch.setenv(CHILD_GROUPS_ENV, str(tmp_path / 'missing-folder'))
    with pytest.raises(OSError):
        run_child([sys.executable, '-c', f'open({str(marker)!r}, "w").close()'], timeout=30)
    assert not marker.exists()
    folder = tmp_path / 'groups'
    folder.mkdir()
    monkeypatch.setenv(CHILD_GROUPS_ENV, str(folder))
    with pytest.raises(OSError):
        run_child([str(tmp_path / 'no-such-program')], timeout=30)
    assert list(folder.iterdir()) == [], 'the record of a child whose exec failed is removed'
