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
