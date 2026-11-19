"""The engine scripts only read the ledger: the snapshot's ``--dry-run`` and the checks before a legacy write
leave ``ledger.sqlite3`` as it is (no table created in an empty file, no ``-wal`` / ``-shm`` left beside a
WAL ledger), and a ledger that cannot be read is never taken for "no ledger". Fictional data, loopback only.
"""
import sqlite3
import subprocess
import sys

import pytest

import ledger_bridge
from tests.engine_fakes import SCRIPTS, engine_env, engine_portfolio, position as legacy_position, price_server
from tests.fakes import build_ledger, opening, position

DAY = '2026-11-17'


def files(folder):
    return sorted(path.name for path in folder.iterdir())


def wal_ledger(folder):
    build_ledger(folder, [opening('Main', cash={'CNY': '100'}, positions=[position('SHA:600001', '10', '9')],
                                  date='2026-11-13')])
    db = sqlite3.connect(folder / 'ledger.sqlite3')
    assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    db.close()
    return folder / 'ledger.sqlite3'


def test_an_empty_ledger_file_is_no_ledger_and_stays_empty(tmp_path):
    (tmp_path / 'ledger.sqlite3').write_bytes(b'')
    assert ledger_bridge.ledger_holdings(tmp_path, DAY) is None
    ledger_bridge.guard_legacy_write(tmp_path)                      # no confirmed ledger: legacy writes allowed
    assert ledger_bridge.active_ledger(tmp_path) is None
    assert (tmp_path / 'ledger.sqlite3').read_bytes() == b'' and files(tmp_path) == ['ledger.sqlite3']


def test_a_wal_ledger_is_read_without_leaving_sidecar_files(tmp_path):
    path = wal_ledger(tmp_path)
    before = path.read_bytes()
    holdings = ledger_bridge.ledger_holdings(tmp_path, DAY)
    assert holdings['ledger_revision'] == 1 and holdings['groups']['Main']['positions'][0]['ticker'] == 'SHA:600001'
    with pytest.raises(ValueError):
        ledger_bridge.guard_legacy_write(tmp_path)                  # a confirmed ledger: legacy writes refused
    assert files(tmp_path) == ['ledger.sqlite3'] and path.read_bytes() == before


def test_a_damaged_ledger_refuses_legacy_writes_instead_of_counting_as_none(tmp_path):
    (tmp_path / 'ledger.sqlite3').write_bytes(b'not a database' * 512)
    with pytest.raises(ValueError):
        ledger_bridge.guard_legacy_write(tmp_path)
    assert (tmp_path / 'ledger.sqlite3').read_bytes() == b'not a database' * 512


def test_the_snapshot_dry_run_leaves_an_empty_ledger_file_untouched(tmp_path):
    with price_server({'SHA:600001': 12.5}) as base:
        folder = engine_portfolio(tmp_path / 'portfolio', base, day=DAY, positions=[legacy_position('SHA:600001')])
        (folder / 'ledger.sqlite3').write_bytes(b'')
        before = files(folder)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio_snapshot.py'), '--date', DAY, '--dry-run'],
                                cwd=SCRIPTS, env=engine_env(folder), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert (folder / 'ledger.sqlite3').read_bytes() == b'' and files(folder) == before
