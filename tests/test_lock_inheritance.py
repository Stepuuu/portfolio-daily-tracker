"""A child works under its parent's portfolio lock only when that is verifiably true.

Every test starts real processes and real flock locks on synthetic, offline
portfolios; only the work inside the child processes is a stand-in.
"""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

import portfolio_daily_update as daily
from portfolio_write_lock import portfolio_write_lock


SCRIPTS = Path(__file__).resolve().parents[1] / 'engine/scripts'
DAY = '2000-01-03'
INHERITED = 'PORTFOLIO_WRITE_LOCK_INHERITED'


def lock_file(directory):
    return Path(directory).resolve() / '.portfolio-update.lock'


def is_locked(directory):
    """Whether some open file description holds the lock; never keeps it."""
    with open(lock_file(directory), 'a+b') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def workspace(directory, cash=100):
    """An offline portfolio: CNY cash only, so the snapshot downloads nothing."""
    (directory / 'holdings').mkdir(parents=True)
    (directory / 'config.json').write_text(json.dumps({'fx_tickers': {}}))
    (directory / 'holdings' / f'{DAY}.json').write_text(json.dumps({'date': DAY, 'groups': {
        'demo': {'cash': cash, 'fund': 0, 'cost_basis': 100, 'positions': []}}}))
    return directory


def snapshot_value(directory):
    return json.loads((directory / 'snapshots' / f'{DAY}.json').read_text())['summary']['total_value']


# The real snapshot CLI, paused just before it reads the history it rewrites.
PAUSED_SNAPSHOT = '''
import sys, time
from pathlib import Path
import portfolio_snapshot as snapshot

control = Path(sys.argv[1])
original = snapshot.load_previous_snapshot


def paused(day):
    (control / 'inside').write_text('')
    deadline = time.time() + 60
    while not (control / 'go').exists() and time.time() < deadline:
        time.sleep(.02)
    return original(day)


snapshot.load_previous_snapshot = paused
sys.argv = ['portfolio_snapshot.py', '--date', sys.argv[2]]
snapshot.main()
'''


def start_paused_snapshot(portfolio, control, claim=None):
    env = {**os.environ, 'PYTHONPATH': str(SCRIPTS), 'PORTFOLIO_DIR': str(portfolio), 'PYTHONWARNINGS': 'ignore'}
    env.pop(INHERITED, None)
    if claim is not None:
        env[INHERITED] = str(claim)
    return subprocess.Popen([sys.executable, '-c', PAUSED_SNAPSHOT, str(control), DAY], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for(path, finished, seconds=30):
    deadline = time.time() + seconds
    while not path.exists():
        assert not finished(), 'finished before reaching ' + path.name
        assert time.time() < deadline, 'never reached ' + path.name
        time.sleep(.02)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """Point the daily pipeline at a synthetic portfolio and the real snapshot script."""
    portfolio = workspace(tmp_path / 'portfolio')
    monkeypatch.setattr(daily, 'PORTFOLIO_DIR', portfolio)
    monkeypatch.setenv('PORTFOLIO_DIR', str(portfolio))
    monkeypatch.setenv('PYTHONWARNINGS', 'ignore')
    monkeypatch.delenv('CONDA_ENV', raising=False)
    monkeypatch.delenv(INHERITED, raising=False)
    return portfolio


# ── Who may claim the lock for a child ──

def test_pipeline_that_does_not_hold_the_lock_does_not_claim_it(pipeline):
    result = {}
    with open(lock_file(pipeline), 'a+b') as other_writer:
        fcntl.flock(other_writer, fcntl.LOCK_EX)
        worker = threading.Thread(target=lambda: result.update(ok=daily.run_snapshot(DAY)))
        worker.start()
        try:
            time.sleep(1.5)
            # The snapshot waits for the writer that holds the lock instead of writing now.
            assert not (pipeline / 'snapshots').exists()
        finally:
            fcntl.flock(other_writer, fcntl.LOCK_UN)
        worker.join(60)
    assert result == {'ok': True}
    assert snapshot_value(pipeline) == 100


def test_lock_held_by_another_thread_is_not_claimed(pipeline):
    from portfolio_write_lock import holds_lock
    held, release, seen = threading.Event(), threading.Event(), {}

    def holder():
        with portfolio_write_lock(pipeline):
            seen['holder'] = holds_lock(pipeline)
            held.set()
            release.wait(30)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(30)
        assert seen['holder'] is True and holds_lock(pipeline) is False
    finally:
        release.set()
        thread.join(30)
    assert holds_lock(pipeline) is False


# ── What a child told that it inherits the lock checks ──

def test_claim_that_nobody_backs_makes_the_child_take_the_lock(tmp_path):
    portfolio = workspace(tmp_path / 'portfolio')
    process = start_paused_snapshot(portfolio, tmp_path, claim=lock_file(portfolio))
    try:
        wait_for(tmp_path / 'inside', lambda: process.poll() is not None)
        assert is_locked(portfolio)
    finally:
        (tmp_path / 'go').write_text('')
        _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr
    assert not is_locked(portfolio)
    assert snapshot_value(portfolio) == 100


def test_claim_for_another_portfolios_lock_is_ignored(tmp_path):
    portfolio = workspace(tmp_path / 'portfolio')
    other = tmp_path / 'other'
    other.mkdir()
    # The claimed lock is really held, but it is not the lock of this portfolio.
    with portfolio_write_lock(other):
        process = start_paused_snapshot(portfolio, tmp_path, claim=lock_file(other))
        try:
            wait_for(tmp_path / 'inside', lambda: process.poll() is not None)
            assert is_locked(portfolio)
        finally:
            (tmp_path / 'go').write_text('')
            _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr
    assert snapshot_value(portfolio) == 100


def test_child_of_the_lock_holder_works_under_its_lock(pipeline):
    with portfolio_write_lock(pipeline):
        started = time.time()
        assert daily.run_snapshot(DAY) is True
        assert time.time() - started < 30
        assert is_locked(pipeline)
    assert snapshot_value(pipeline) == 100
    assert not is_locked(pipeline)


# ── Composition with the Feishu adapter's inherited descriptor ──

# The adapter's child process loads this in place of the engine module: the real
# run_pipeline and run_snapshot (with the real snapshot script); only the report
# text and the network delivery are stand-ins.
FEISHU_SHIM = '''
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ENGINE = Path({engine!r})
sys.path.insert(0, str(ENGINE))
exec(compile((ENGINE / 'portfolio_daily_update.py').read_text(encoding='utf-8'),
             str(ENGINE / 'portfolio_daily_update.py'), 'exec'))

ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = ENGINE
PORTFOLIO_DIR = ROOT / 'portfolio'
HOLDINGS_DIR = PORTFOLIO_DIR / 'holdings'
REPORTS_DIR = ROOT / 'reports'
CONTROL = ROOT / 'control'
os.environ['PORTFOLIO_DIR'] = str(PORTFOLIO_DIR)
SNAPSHOT_TIMEOUT = 20


def load_config():
    return {{}}


def run_report(day):
    REPORTS_DIR.mkdir(exist_ok=True)
    target = REPORTS_DIR / ('portfolio-' + day.replace('-', '') + '.md')
    target.write_text('Synthetic test report')
    return target


def send_feishu_report(day, text, config):
    # Runs while the adapter's child has released the shared lock for delivery.
    planned = CONTROL / 'during-delivery.json'
    if planned.exists():
        plan = json.loads(planned.read_text())
        subprocess.Popen(plan['argv'], env={{**os.environ, **plan['env']}},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        for step in ('inside', 'delivered'):
            while not (CONTROL / step).exists():
                if time.time() > deadline:
                    return False
                time.sleep(.02)
    return True
'''


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    from feishu_workbench.portfolio import PortfolioAdapter
    project = tmp_path / 'project'
    workspace(project / 'portfolio')
    (project / 'scripts').mkdir()
    (project / 'control').mkdir()
    (project / 'scripts' / 'portfolio_daily_update.py').write_text(FEISHU_SHIM.format(engine=str(SCRIPTS)))
    monkeypatch.setenv('PYTHONWARNINGS', 'ignore')
    monkeypatch.delenv('CONDA_ENV', raising=False)
    monkeypatch.delenv(INHERITED, raising=False)
    return PortfolioAdapter(project, tmp_path / 'state')


def checkpoint(adapter):
    return json.loads((adapter.portfolio_dir / 'pipeline-state' / f'{DAY}.json').read_text())


def test_feishu_pipeline_runs_the_real_snapshot_under_the_adapters_lock(adapter):
    with adapter._lock():
        assert adapter._execute_pipeline(DAY, False) is True
        assert is_locked(adapter.portfolio_dir)
    assert snapshot_value(adapter.portfolio_dir) == 100
    assert checkpoint(adapter)['push'] == 'complete'
    assert not is_locked(adapter.portfolio_dir)


def test_snapshot_started_during_delivery_takes_the_lock_itself(adapter):
    """Delivery runs unlocked, so a snapshot started then holds the lock on its own.

    Even a child that claims an inherited lock finds the lock free and takes it,
    and the adapter's child records the delivery only after that snapshot ends.
    """
    control = adapter.project_dir / 'control'
    (control / 'during-delivery.json').write_text(json.dumps({
        'argv': [sys.executable, '-c', PAUSED_SNAPSHOT, str(control), DAY],
        'env': {'PYTHONPATH': str(SCRIPTS), INHERITED: str(lock_file(adapter.portfolio_dir))}}))

    def report():
        with adapter._lock():
            return adapter._execute_pipeline(DAY, False)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(report)
        try:
            wait_for(control / 'inside', pending.done)
            # The adapter's child is still delivering, unlocked: the snapshot holds the lock.
            assert is_locked(adapter.portfolio_dir)
            (control / 'delivered').write_text('')
            time.sleep(1)
            # Delivery is over, but the delivery is recorded only once the snapshot ends.
            assert not pending.done()
            assert checkpoint(adapter)['push'] == 'uncertain'
        finally:
            (control / 'delivered').write_text('')
            (control / 'go').write_text('')
        assert pending.result(timeout=60) is True
    assert checkpoint(adapter)['push'] == 'complete'
    assert not is_locked(adapter.portfolio_dir)


# ── A child cannot write after the lock it works under is released ──

# Stand-in for a snapshot or report that hangs while writing: it appends a tick
# every 20 ms, for TICKS_TOTAL ticks (about 40 s by default).
SLOW_WRITER = '''
import os, time
from pathlib import Path
ticks = Path(os.environ['TICKS'])
for _ in range(int(os.environ.get('TICKS_TOTAL', '2000'))):
    with ticks.open('a') as stream:
        stream.write('tick\\n')
    time.sleep(.02)
'''


def ticks_written(path):
    return len(path.read_text().split()) if path.exists() else 0


def fake_conda(tmp_path, monkeypatch):
    """``conda run -n research python3 ...`` stand-in that waits for Python as its child."""
    wrapper = tmp_path / 'fake-conda'
    wrapper.write_text('#!/bin/sh\nshift 4\n"$FAKE_PYTHON" "$@"\nstatus=$?\nexit $status\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv('CONDA_ENV', 'research')
    monkeypatch.setenv('CONDA_EXE', str(wrapper))
    monkeypatch.setenv('FAKE_PYTHON', sys.executable)


@pytest.fixture
def slow_children(pipeline, tmp_path, monkeypatch):
    scripts = tmp_path / 'slow-scripts'
    scripts.mkdir()
    (scripts / 'portfolio_snapshot.py').write_text(SLOW_WRITER)
    (scripts / 'portfolio_report.py').write_text(SLOW_WRITER)
    monkeypatch.setattr(daily, 'BASE_DIR', scripts)
    monkeypatch.setattr(daily, 'REPORTS_DIR', tmp_path / 'reports')
    monkeypatch.setattr(daily, 'SNAPSHOT_TIMEOUT', 1, raising=False)
    monkeypatch.setattr(daily, 'REPORT_TIMEOUT', 1, raising=False)
    fake_conda(tmp_path, monkeypatch)
    ticks = tmp_path / 'ticks'
    monkeypatch.setenv('TICKS', str(ticks))
    return ticks


def test_timed_out_snapshot_and_its_wrapper_stop_before_the_lock_is_released(pipeline, slow_children, capsys):
    with portfolio_write_lock(pipeline):
        assert daily.run_snapshot(DAY) is False
        written = ticks_written(slow_children)
        assert written > 0
    # Another writer now holds the lock: nothing of the timed-out run may write.
    with portfolio_write_lock(pipeline):
        time.sleep(.5)
        assert ticks_written(slow_children) == written
    assert '超过' in capsys.readouterr().out


def test_timed_out_report_fails_without_a_traceback_and_leaves_no_writer(pipeline, slow_children, capsys):
    with portfolio_write_lock(pipeline):
        assert daily.run_report(DAY) is None
        written = ticks_written(slow_children)
    time.sleep(.5)
    assert ticks_written(slow_children) == written
    assert '超过' in capsys.readouterr().out


def test_the_inherited_descriptor_survives_a_spawn_that_runs_the_fork_handlers(pipeline, monkeypatch):
    """With a preexec_fn (the Linux parent-death signal), subprocess runs the at-fork handlers in the
    child; the handler that closes this module's lock descriptors must keep the one being passed on."""
    import portfolio_write_lock as lock
    monkeypatch.setattr(lock, '_die_with_parent', lambda: (lambda: None))   # any preexec_fn, on any platform
    with portfolio_write_lock(pipeline):
        assert daily.run_snapshot(DAY) is True
        assert is_locked(pipeline)
    assert snapshot_value(pipeline) == 100
    assert not is_locked(pipeline)


def test_child_of_the_lock_holder_works_under_its_lock_through_a_wrapper(pipeline, tmp_path, monkeypatch):
    fake_conda(tmp_path, monkeypatch)
    with portfolio_write_lock(pipeline):
        assert daily.run_snapshot(DAY) is True
        assert is_locked(pipeline)
    assert snapshot_value(pipeline) == 100


PARENT = '''
import os, sys
from pathlib import Path
import portfolio_daily_update as daily
from portfolio_write_lock import portfolio_write_lock
daily.PORTFOLIO_DIR = Path(os.environ['PORTFOLIO_DIR'])
daily.BASE_DIR = Path(sys.argv[1])
with portfolio_write_lock(daily.PORTFOLIO_DIR):
    daily.run_snapshot('2000-01-03')
'''


def test_no_writer_outlives_a_killed_parent_without_the_lock(pipeline, tmp_path):
    """Killing the lock holder while its child writes never leaves a writer without the lock.

    On Linux the child gets the parent-death signal and stops with its parent.
    Elsewhere the orphaned child keeps the inherited lock until it has finished.
    """
    import signal
    scripts = tmp_path / 'slow-scripts'
    scripts.mkdir()
    (scripts / 'portfolio_snapshot.py').write_text(SLOW_WRITER)
    ticks = tmp_path / 'ticks'
    env = {**os.environ, 'PYTHONPATH': str(SCRIPTS), 'TICKS': str(ticks), 'TICKS_TOTAL': '100'}
    parent = subprocess.Popen([sys.executable, '-c', PARENT, str(scripts)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(ticks, lambda: parent.poll() is not None)
    finally:
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(30)
    if sys.platform.startswith('linux'):
        deadline = time.time() + 5
        while is_locked(pipeline) and time.time() < deadline:
            time.sleep(.02)
        assert not is_locked(pipeline), 'the child died with its parent and released the lock'
        stopped = ticks_written(ticks)
        time.sleep(.3)
        assert ticks_written(ticks) == stopped < 100
        return
    # The orphaned child keeps writing, so the lock must stay taken until it stops.
    assert is_locked(pipeline)
    with portfolio_write_lock(pipeline):
        finished = ticks_written(ticks)
        time.sleep(.3)
        assert ticks_written(ticks) == finished == 100


# ── A standalone snapshot does not hold the lock while it downloads quotes ──

@pytest.fixture
def standalone(tmp_path, monkeypatch):
    """The snapshot CLI run on its own (no inherited lock), with stubbed downloads."""
    import portfolio_snapshot as snapshot
    portfolio = workspace(tmp_path / 'portfolio')
    monkeypatch.setattr(snapshot, 'PORTFOLIO_DIR', str(portfolio))
    monkeypatch.setattr(snapshot, 'CONFIG_PATH', str(portfolio / 'config.json'))
    monkeypatch.setattr(snapshot, 'fetch_fx_rates', lambda config, *_estimated: {'CNY': 1.0})
    monkeypatch.setattr(sys, 'argv', ['portfolio_snapshot.py', '--date', DAY])
    monkeypatch.delenv(INHERITED, raising=False)
    return snapshot, portfolio


def set_cash_with_the_holdings_cli(portfolio, cash):
    """The real holdings CLI, which waits for the lock; it fails here if it cannot get it."""
    env = {**os.environ, 'PYTHONPATH': str(SCRIPTS), 'PORTFOLIO_DIR': str(portfolio), 'PYTHONWARNINGS': 'ignore'}
    env.pop(INHERITED, None)
    result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio_manager.py'), '--date', DAY,
                             'set-cash', '--group', 'demo', '--value', str(cash)],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_writer_takes_the_lock_while_a_standalone_snapshot_downloads(standalone, monkeypatch):
    snapshot, portfolio = standalone
    fetched = []

    def slow_quotes(holdings, config, *_date):
        fetched.append(holdings['groups']['demo']['cash'])
        if len(fetched) == 1:
            time.sleep(.5)
            set_cash_with_the_holdings_cli(portfolio, 142)
        return {}, {}

    monkeypatch.setattr(snapshot, 'fetch_prices', slow_quotes)
    snapshot.main()
    # The change made during the download was noticed, so the quotes were fetched again.
    assert fetched == [100, 142]
    assert snapshot_value(portfolio) == 142
    assert (portfolio / 'history.csv').read_text().split('\n')[1].startswith(f'{DAY},142')


def test_holdings_that_change_during_both_downloads_are_not_snapshotted(standalone, monkeypatch):
    snapshot, portfolio = standalone
    fetched = []

    def changing_quotes(holdings, config, *_date):
        fetched.append(holdings['groups']['demo']['cash'])
        set_cash_with_the_holdings_cli(portfolio, 200 + len(fetched))
        return {}, {}

    monkeypatch.setattr(snapshot, 'fetch_prices', changing_quotes)
    with pytest.raises(SystemExit) as stopped:
        snapshot.main()
    assert fetched == [100, 201]
    assert isinstance(stopped.value.code, str) and '变化' in stopped.value.code
    assert not (portfolio / 'snapshots').exists() and not (portfolio / 'history.csv').exists()
    assert not is_locked(portfolio)
