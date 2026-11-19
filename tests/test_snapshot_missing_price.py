"""A price that could not be fetched means no snapshot, never a price of 0.

Prices come from a stubbed fetcher (in process) or from a loopback stand-in for
the Yahoo chart API (for the real scripts in child processes). Fictional data.
"""
import asyncio
import json
import subprocess
import sys

import pytest

import portfolio_daily_update as daily
import portfolio_snapshot as snapshot
from tests.engine_fakes import (QUOTE_TIME, SCRIPTS, engine_copy, engine_env, engine_portfolio, position,
                                price_server)

DAY = '2026-11-17'
HELD = ('SHA:600001', 'SHE:000002', 'HKG:00003')


def held_portfolio(directory, base='http://127.0.0.1:9/unused'):
    """Holdings for DAY with three positions, one earlier snapshot and a history.csv."""
    portfolio = engine_portfolio(directory, base, day=DAY, positions=[position(ticker) for ticker in HELD])
    (portfolio / 'snapshots').mkdir()
    (portfolio / 'snapshots' / '2026-11-16.json').write_text(json.dumps(
        {'date': '2026-11-16', 'summary': {'total_value': 4000, 'total_cost': 2000}, 'groups': {}}))
    (portfolio / 'history.csv').write_text('date,total_value,total_cost\n2026-11-16,4000,2000\n')
    return portfolio


def untouched(portfolio):
    """Nothing for DAY was written, and the history is byte for byte what it was."""
    assert not (portfolio / 'snapshots' / f'{DAY}.json').exists()
    assert sorted(path.name for path in (portfolio / 'snapshots').iterdir()) == ['2026-11-16.json']
    assert (portfolio / 'history.csv').read_text() == 'date,total_value,total_cost\n2026-11-16,4000,2000\n'


class Response:
    def __init__(self, price):
        self.price = price

    def raise_for_status(self):
        pass

    def json(self):
        return {'chart': {'result': [{'meta': {'regularMarketPrice': self.price, 'currency': 'CNY',
                                               'regularMarketTime': QUOTE_TIME}}]}}


def test_a_failed_or_unusable_price_is_left_out_instead_of_becoming_zero(monkeypatch, capsys):
    answers = {'600001.SS': 12.5, '000002.SZ': 0, '0003.HK': None, '600004.SS': float('nan')}

    def get(url, **_options):
        symbol = url.rsplit('/', 1)[-1]
        if symbol not in answers:
            raise ConnectionError('synthetic outage')
        return Response(answers[symbol])

    monkeypatch.setattr(snapshot.requests, 'get', get)
    holdings = {'groups': {'Example': {'positions': [position(ticker) for ticker in
                                                     (*HELD, 'SHA:600004', 'SHA:600005')]}}}
    prices, currencies = snapshot.fetch_prices(holdings, {}, DAY)   # judged as of that day, not of the run's time
    assert prices == {'SHA:600001': 12.5}
    assert currencies == {'SHA:600001': 'CNY'}
    assert snapshot.missing_prices(holdings, prices) == ['HKG:00003', 'SHA:600004', 'SHA:600005', 'SHE:000002']
    warnings = capsys.readouterr().err
    assert all(ticker in warnings for ticker in ('SHE:000002', 'HKG:00003', 'SHA:600004', 'SHA:600005'))


def test_a_closed_position_needs_no_price():
    holdings = {'groups': {'Example': {'positions': [position('SHA:600001', quantity=0), position('SHE:000002')]}}}
    assert snapshot.missing_prices(holdings, {'SHE:000002': 8.0}) == []


@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    """The snapshot CLI in this process, on a synthetic portfolio, with a stubbed fetcher."""
    portfolio = held_portfolio(tmp_path / 'portfolio')
    monkeypatch.setattr(snapshot, 'PORTFOLIO_DIR', str(portfolio))
    monkeypatch.setattr(snapshot, 'CONFIG_PATH', str(portfolio / 'config.json'))
    monkeypatch.setattr(snapshot, 'fetch_fx_rates', lambda config, *_estimated: {'CNY': 1.0})
    monkeypatch.delenv('PORTFOLIO_WRITE_LOCK_INHERITED', raising=False)
    monkeypatch.setattr(sys, 'argv', ['portfolio_snapshot.py', '--date', DAY])
    return portfolio


def test_snapshot_cli_with_missing_prices_writes_nothing_and_exits_non_zero(stubbed, monkeypatch):
    monkeypatch.setattr(snapshot, 'fetch_prices', lambda holdings, config, *_date: ({'SHA:600001': 12.5}, {}))
    with pytest.raises(SystemExit) as stopped:
        snapshot.main()
    message = str(stopped.value.code)
    assert stopped.value.code not in (0, None)
    assert 'HKG:00003, SHE:000002' in message and 'SHA:600001' not in message
    assert '不会按 0 计算' in message
    untouched(stubbed)


def test_dry_run_with_a_missing_price_also_exits_non_zero(stubbed, monkeypatch):
    monkeypatch.setattr(snapshot, 'fetch_prices', lambda holdings, config, *_date: ({}, {}))
    monkeypatch.setattr(sys, 'argv', ['portfolio_snapshot.py', '--date', DAY, '--dry-run'])
    with pytest.raises(SystemExit) as stopped:
        snapshot.main()
    assert stopped.value.code not in (0, None)
    untouched(stubbed)


def test_every_price_present_still_saves_the_snapshot(stubbed, monkeypatch):
    prices = {ticker: 20.0 for ticker in HELD}
    monkeypatch.setattr(snapshot, 'fetch_fx_rates', lambda config, *_estimated: {'CNY': 1.0, 'HKD': 0.9})
    monkeypatch.setattr(snapshot, 'fetch_prices', lambda holdings, config, *_date: (prices, {'HKG:00003': 'HKD'}))
    snapshot.main()
    saved = json.loads((stubbed / 'snapshots' / f'{DAY}.json').read_text())
    assert saved['summary']['total_value'] == 1000 + 2000 + 2000 + 2000 * 0.9
    assert (stubbed / 'history.csv').read_text().splitlines()[-1].startswith(f'{DAY},')


def test_an_accounting_error_ends_with_a_message_not_a_traceback(stubbed, monkeypatch):
    # Every price is there, but the HKD rate is not: refused, with the reason.
    monkeypatch.setattr(snapshot, 'fetch_prices',
                        lambda holdings, config, *_date: ({ticker: 20.0 for ticker in HELD}, {'HKG:00003': 'HKD'}))
    with pytest.raises(SystemExit) as stopped:
        snapshot.main()
    assert 'HKD/CNY exchange rate' in str(stopped.value.code)
    untouched(stubbed)


# ── The real scripts in child processes, priced by a loopback stand-in ──

def test_snapshot_script_reports_the_missing_tickers_and_exits_non_zero(tmp_path):
    with price_server({'SHA:600001': 12.5}) as base:
        portfolio = held_portfolio(tmp_path / 'portfolio', base)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio_snapshot.py'), '--date', DAY],
                                cwd=SCRIPTS, env=engine_env(portfolio), capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert 'Traceback' not in result.stderr
    assert 'HKG:00003, SHE:000002' in result.stderr.strip().splitlines()[-1]
    untouched(portfolio)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """The daily pipeline module pointed at a synthetic portfolio (reports stay in tmp_path)."""
    def start(base):
        portfolio = held_portfolio(tmp_path / 'portfolio', base)
        monkeypatch.setattr(daily, 'PORTFOLIO_DIR', portfolio)
        monkeypatch.setattr(daily, 'CONFIG_PATH', portfolio / 'config.json')
        monkeypatch.setattr(daily, 'HOLDINGS_DIR', portfolio / 'holdings')
        monkeypatch.setattr(daily, 'SNAPSHOTS_DIR', portfolio / 'snapshots')
        monkeypatch.setattr(daily, 'REPORTS_DIR', tmp_path / 'reports')
        for name, value in engine_env(portfolio).items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv('PORTFOLIO_WRITE_LOCK_INHERITED', raising=False)
        monkeypatch.delenv('CONDA_ENV', raising=False)
        return portfolio
    return start


def test_pipeline_reports_a_failed_snapshot_as_a_failure(pipeline, capsys):
    with price_server({'SHA:600001': 12.5, 'HKG:00003': 3.0}) as base:
        portfolio = pipeline(base)
        assert daily.run_snapshot(DAY) is False
        output = capsys.readouterr().out
        assert '快照生成失败' in output and 'SHE:000002' in output and 'Traceback' not in output
        assert daily.run_pipeline(DAY, send_report=False) is False
    state = json.loads((portfolio / 'pipeline-state' / f'{DAY}.json').read_text())
    assert state['snapshot'] is False and state['report'] is False
    untouched(portfolio)


def test_assistant_pipeline_tool_reports_failure(pipeline):
    from core.tools import run_portfolio_pipeline_tool
    with price_server({}) as base:
        portfolio = pipeline(base)
        result = asyncio.run(run_portfolio_pipeline_tool().function(DAY, False))
    assert result['success'] is False and '失败' in result['message']
    untouched(portfolio)


def test_auto_pipeline_cli_exits_non_zero_without_a_traceback(tmp_path):
    with price_server({'SHA:600001': 12.5}) as base:
        portfolio = held_portfolio(tmp_path / 'portfolio', base)
        # A private copy of the scripts keeps the report directory inside tmp_path.
        scripts = engine_copy(tmp_path / 'checkout')
        result = subprocess.run([sys.executable, str(scripts / 'portfolio_daily_update.py'),
                                 '--action', 'auto-pipeline', '--date', DAY],
                                cwd=scripts, env=engine_env(portfolio), capture_output=True, text=True, timeout=180)
    assert result.returncode == 1
    assert 'Traceback' not in result.stdout + result.stderr
    assert 'HKG:00003, SHE:000002' in result.stdout
    untouched(portfolio)
    assert not (tmp_path / 'checkout' / 'engine' / 'reports').exists()
