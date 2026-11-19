"""Every action of ``portfolio_daily_update.py`` works on a ledger portfolio, as ``auto-pipeline`` and ``notify`` do.

With a confirmed ledger the holdings are the ledger's and the legacy holdings files are never written (the
ledger refuses it): ``pipeline`` makes the day from the ledger without cloning, as the docs and the auto
pipeline's notice tell the user to do after a late entry; ``clone`` has nothing to clone; ``update`` says that
holdings change through the ledger instead of failing with a traceback. Real child processes on a synthetic
portfolio, loopback quotes; fictional data.
"""
import json
import subprocess
import sys

import pytest

import portfolio_daily_update as daily
from tests.engine_fakes import engine_copy, engine_env, engine_portfolio, position, price_server, tencent_url
from tests.fakes import book, build_ledger, opening, trade
from tests.fakes import position as ledger_position

DAY = '2026-11-17'      # Tuesday; the loopback quote is its 15:00 close
CN = 'SHA:600001'


@pytest.fixture
def ledger_portfolio(tmp_path, monkeypatch):
    for name in ('TRACKER_QUOTE_SOURCE', 'TRACKER_TENCENT_PROXY', 'CONDA_ENV', 'PORTFOLIO_WRITE_LOCK_INHERITED',
                 'TRACKER_REPORTS_DIR'):
        monkeypatch.delenv(name, raising=False)
    portfolio = tmp_path / 'portfolio'
    portfolio.mkdir()
    ledger = build_ledger(portfolio, [opening('Example', cash={'CNY': '10000'}, contributed_cny='18000',
                                              date='2026-11-16',
                                              positions=[ledger_position(CN, '1000', '8', name='Example')])])
    return portfolio, ledger, engine_copy(tmp_path / 'checkout')


def engine(scripts, portfolio, base, *args):
    (portfolio / 'config.json').write_text(json.dumps({'fx_tickers': {}, 'yahoo_base': base,
                                                       'tencent_url': tencent_url(base)}))
    return subprocess.run([sys.executable, str(scripts / 'portfolio_daily_update.py'), *args], cwd=scripts,
                          env=engine_env(portfolio), capture_output=True, text=True, timeout=120)


def total(portfolio):
    return json.loads((portfolio / 'snapshots' / f'{DAY}.json').read_text())['summary']['total_value']


def test_pipeline_makes_the_day_from_the_ledger_without_cloning(ledger_portfolio):
    portfolio, _ledger, scripts = ledger_portfolio
    with price_server({CN: 9.0}) as base:
        result = engine(scripts, portfolio, base, '--action', 'pipeline', '--date', DAY, '--no-push')
    assert result.returncode == 0, result.stdout + result.stderr
    assert daily.LEDGER_HOLDINGS in result.stdout and 'Traceback' not in result.stderr
    assert total(portfolio) == 19000
    assert not (portfolio / 'holdings').exists(), 'no legacy holdings file is written beside the ledger'
    assert (scripts.parent / 'reports' / 'portfolio-20261117.md').is_file()


def test_after_a_late_entry_pipeline_makes_the_day_again_for_the_ledgers_holdings(ledger_portfolio):
    """The flow ``docs/EXTENSIONS.md`` and the auto pipeline's notice describe."""
    portfolio, ledger, scripts = ledger_portfolio
    with price_server({CN: 9.0}) as base:
        made = engine(scripts, portfolio, base, '--action', 'auto-pipeline', '--date', DAY)
        assert made.returncode == 0, made.stdout + made.stderr
        assert total(portfolio) == 19000
        book(ledger, [trade('buy', CN, '100', '9', date=DAY)])       # entered after the day was made
        kept = engine(scripts, portfolio, base, '--action', 'auto-pipeline', '--date', DAY)
        assert daily.HOLDINGS_CHANGED in kept.stdout and '--action pipeline' in kept.stdout
        again = engine(scripts, portfolio, base, '--action', 'pipeline', '--date', DAY, '--no-push')
    assert again.returncode == 0, again.stdout + again.stderr
    assert total(portfolio) == 19000                                  # 1100 shares at 9 and 9100 in cash
    snapshot = json.loads((portfolio / 'snapshots' / f'{DAY}.json').read_text())
    assert snapshot['groups']['Example']['positions'][0]['quantity'] == 1100
    assert snapshot['ledger_revision'] == ledger.state()['revision']


def test_clone_has_nothing_to_clone_beside_a_ledger(ledger_portfolio):
    portfolio, _ledger, scripts = ledger_portfolio
    with price_server({}) as base:
        result = engine(scripts, portfolio, base, '--action', 'clone', '--date', DAY)
    assert result.returncode == 0, result.stdout + result.stderr
    assert daily.LEDGER_HOLDINGS in result.stdout and not (portfolio / 'holdings').exists()


def test_update_says_that_holdings_change_through_the_ledger(ledger_portfolio):
    portfolio, ledger, scripts = ledger_portfolio
    revision = ledger.state()['revision']
    with price_server({}) as base:
        result = engine(scripts, portfolio, base, '--action', 'update', '--date', DAY, '--text', '现金变为5000')
    assert result.returncode == 1
    assert daily.LEDGER_UPDATE in result.stderr and 'Traceback' not in result.stderr
    assert not (portfolio / 'holdings').exists() and build_ledger(portfolio).state()['revision'] == revision
    assert not (portfolio / 'snapshots').exists()


def test_pipeline_still_clones_legacy_holdings(tmp_path, monkeypatch):
    monkeypatch.delenv('TRACKER_REPORTS_DIR', raising=False)
    with price_server({CN: 9.0}) as base:
        portfolio = engine_portfolio(tmp_path / 'portfolio', base, day='2026-11-16',
                                     positions=[position(CN, quantity=1000, cost_price=8)], cash=10000,
                                     cost_basis=18000)
        scripts = engine_copy(tmp_path / 'checkout')
        result = subprocess.run([sys.executable, str(scripts / 'portfolio_daily_update.py'), '--action', 'pipeline',
                                 '--date', DAY, '--no-push'], cwd=scripts, env=engine_env(portfolio),
                                capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((portfolio / 'holdings' / f'{DAY}.json').read_text())['cloned_from'] == '2026-11-16'
    assert total(portfolio) == 19000


def test_pipeline_without_a_chat_id_saves_the_report_and_succeeds_as_the_auto_pipeline_does(ledger_portfolio):
    portfolio, _ledger, scripts = ledger_portfolio
    with price_server({CN: 9.0}) as base:
        result = engine(scripts, portfolio, base, '--action', 'pipeline', '--date', DAY)    # no --no-push
    assert result.returncode == 0, result.stdout + result.stderr
    assert daily.SAVE_ONLY in result.stdout
    assert (scripts.parent / 'reports' / 'portfolio-20261117.md').is_file() and total(portfolio) == 19000
    # "Saved, not pushed": complete, and no push claimed that could never be resent.
    state = json.loads((portfolio / 'pipeline-state' / f'{DAY}.json').read_text())
    assert (state['report'], state['complete'], state['push']) == (True, True, 'not_started')


def test_pipeline_pushes_only_with_a_chat_id_and_never_with_no_push(monkeypatch):
    pushes = []
    monkeypatch.setattr(daily, 'ledger_in_use', lambda: True)
    monkeypatch.setattr(daily, 'run_pipeline', lambda day, send_report: pushes.append(send_report) or True)
    monkeypatch.setattr(daily, 'load_config', lambda: {'feishu_chat_id': 'user:example'})
    assert daily.action_pipeline(DAY, send_report=True) and daily.action_pipeline(DAY, send_report=False)
    monkeypatch.setattr(daily, 'load_config', lambda: {'feishu_chat_id': ''})
    assert daily.action_pipeline(DAY, send_report=True)
    assert pushes == [True, False, False]
