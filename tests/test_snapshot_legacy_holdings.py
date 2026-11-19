"""A snapshot made from the legacy holdings files says so.

The reviewer's case: after an upgrade, before the legacy files (cash 18,000) are confirmed into the ledger,
Today has no confirmed total, yet the close snapshot routine recorded 18,000 and reported ok without a word.
The snapshot is still made from the files, but it records where its holdings came from (``holdings_source``:
``legacy_files`` or ``ledger``) and is marked 含估算 with the reason 持仓来自旧版持仓文件，尚未迁移到账本, which
the run's summary (and so the post-market brief), the report and the tracker history show.
"""
import asyncio
import json

import pytest

import portfolio_snapshot as snapshot
from tests.engine_fakes import engine_copy, engine_portfolio, position, price_server, tencent_url
from tests.fakes import build_ledger, opening
from tests.fakes import position as ledger_position

DAY = '2026-11-17'
LEGACY = '持仓来自旧版持仓文件，尚未迁移到账本'


@pytest.fixture
def routine_env(monkeypatch):
    for name in ('TRACKER_DEMO_MODE', 'TRACKER_QUOTE_SOURCE', 'TRACKER_CLOSE_SNAPSHOT', 'CONDA_ENV',
                 'PORTFOLIO_WRITE_LOCK_INHERITED', 'TRACKER_LAB_ONLY'):
        monkeypatch.delenv(name, raising=False)
    for name, value in (('NO_PROXY', '127.0.0.1,localhost'), ('no_proxy', '127.0.0.1,localhost'),
                        ('PYTHONWARNINGS', 'ignore')):
        monkeypatch.setenv(name, value)

    def run(portfolio, scripts):
        from core.routines import close_snapshot
        monkeypatch.setenv('PORTFOLIO_DIR', str(portfolio))
        return asyncio.run(close_snapshot.run(DAY, engine_dir=scripts, portfolio_dir=portfolio))
    return run


def test_an_upgrade_before_the_migration_is_confirmed_is_snapshotted_and_marked(tmp_path, routine_env):
    portfolio = engine_portfolio(tmp_path / 'portfolio', 'http://127.0.0.1:9/unused', day='2026-11-16',
                                 cash=18000, cost_basis=18000)
    build_ledger(portfolio)                       # upgraded: a ledger, nothing confirmed in it yet
    outcome = routine_env(portfolio, engine_copy(tmp_path / 'checkout'))
    assert outcome.status == 'ok' and LEGACY in outcome.summary
    saved = json.loads((portfolio / 'snapshots' / f'{DAY}.json').read_text())
    assert saved['summary']['total_value'] == 18000 and saved['holdings_source'] == 'legacy_files'
    assert saved['summary']['estimated'] is True and LEGACY in saved['summary']['estimated_reasons']

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import portfolio_tracker as tracker
    import portfolio_report as report
    assert LEGACY in report.generate_report(saved)
    app = FastAPI()
    app.include_router(tracker.router, prefix='/api/tracker')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tracker, 'PORTFOLIO_DIR', str(portfolio))
        row = TestClient(app).get('/api/tracker/history').json()['history'][-1]
    assert row['estimated'] is True and LEGACY in row['estimated_reasons']


def test_a_snapshot_of_the_confirmed_ledger_is_not_marked(tmp_path, routine_env):
    portfolio = tmp_path / 'portfolio'
    portfolio.mkdir()
    build_ledger(portfolio, [opening('Example', cash={'CNY': '1000'}, contributed_cny='2000', date='2026-11-16',
                                     positions=[ledger_position('SHA:600001', '100', '8', name='Example')])])
    with price_server({'SHA:600001': 9.0}) as base:
        (portfolio / 'config.json').write_text(json.dumps({'fx_tickers': {}, 'yahoo_base': base,
                                                            'tencent_url': tencent_url(base)}))
        outcome = routine_env(portfolio, engine_copy(tmp_path / 'checkout'))
    assert (outcome.status, outcome.summary) == ('ok', '快照已生成；日报已保存')
    saved = json.loads((portfolio / 'snapshots' / f'{DAY}.json').read_text())
    assert saved['holdings_source'] == 'ledger' and saved['summary']['estimated'] is False


def test_the_legacy_mark_comes_first():
    holdings = {'date': DAY, 'groups': {'Example': {'cash': 1000, 'fund': 0, 'cost_basis': 1000,
                                                    'positions': [position('SHA:600001', 1, 10)]}}}
    saved = snapshot.calculate_snapshot(holdings, {'SHA:600001': 10.0}, {}, {'CNY': 1.0}, None, [],
                                        price_estimates={'SHA:600001': '示例原因'}, holdings_source='legacy_files')
    assert saved['holdings_source'] == 'legacy_files'
    assert saved['summary']['estimated_reasons'] == [LEGACY, 'SHA:600001：示例原因']
