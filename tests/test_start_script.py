"""start.sh waits BACKEND_START_TIMEOUT seconds (30 by default) for the backend's /health and says how to wait longer.

A copy of start.sh runs in the test's directory with a stand-in for Python whose backend never answers.
"""
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]

FAKE_PYTHON = '''#!/usr/bin/env bash
# Stands in for Python in start.sh. The backend starts but never answers /health.
case "$1" in
    -m) echo started >> "$START_LOG"; exec sleep 60 ;;          # uvicorn
    -c) exit 0 ;;                                               # the dependency check
esac
cat > /dev/null                                                 # a script on standard input
if [ "$#" -eq 2 ]; then echo probe >> "$START_LOG"; exit 1; fi  # the /health probe
exit 0                                                          # the pid file and the port check
'''


def start(tmp_path, *arguments, **variables):
    shutil.copy(ROOT / 'start.sh', tmp_path / 'start.sh')
    (tmp_path / 'dashboard').mkdir(exist_ok=True)
    fake = tmp_path / 'python'
    fake.write_text(FAKE_PYTHON)
    fake.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != 'BACKEND_START_TIMEOUT'}
    env.update(PYTHON=str(fake), START_LOG=str(tmp_path / 'log'), BACKEND_PORT='8765', **variables)
    return subprocess.run(['bash', str(tmp_path / 'start.sh'), *arguments], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)


def log(tmp_path):
    path = tmp_path / 'log'
    return path.read_text().split() if path.exists() else []


def test_the_wait_follows_the_variable_and_the_message_names_it(tmp_path):
    began = time.monotonic()
    result = start(tmp_path, 'backend', BACKEND_START_TIMEOUT='2')
    assert result.returncode == 1
    assert 'Backend did not become ready within 2 seconds (BACKEND_START_TIMEOUT).' in result.stderr
    assert 'BACKEND_START_TIMEOUT=120 make start' in result.stderr
    assert sorted(log(tmp_path)) == ['probe', 'probe', 'started']
    assert time.monotonic() - began >= 2


def test_the_default_is_still_30_seconds():
    script = (ROOT / 'start.sh').read_text()
    assert 'BACKEND_START_TIMEOUT="${BACKEND_START_TIMEOUT:-30}"' in script
    assert 'attempt<BACKEND_START_TIMEOUT' in script


@pytest.mark.parametrize('value', ['0', '-1', 'abc', '1.5', '3601', '010', ' 5', '30s'])
def test_an_invalid_wait_is_refused_before_anything_starts(tmp_path, value):
    result = start(tmp_path, 'backend', BACKEND_START_TIMEOUT=value)
    assert result.returncode == 2
    assert result.stderr.strip() == (f"BACKEND_START_TIMEOUT must be a whole number of seconds from 1 to 3600, "
                                     f"not '{value}'.")
    assert log(tmp_path) == [] and not (tmp_path / '.runtime').exists()


def test_the_frontend_alone_does_not_read_it(tmp_path):
    result = start(tmp_path, 'frontend', BACKEND_START_TIMEOUT='abc')
    assert 'BACKEND_START_TIMEOUT' not in result.stderr
    assert 'make setup' in result.stderr   # this stand-in checkout has no frontend dependencies
