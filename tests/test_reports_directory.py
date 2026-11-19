"""``TRACKER_REPORTS_DIR`` moves the days' reports out of the code checkout, for every writer and reader.

By default the pipeline saves ``portfolio-YYYYMMDD.md`` in ``engine/reports`` next to its scripts, outside
``PORTFOLIO_DIR`` and its backups. With the setting the engine writes the reports there, and the readers
look there: the close snapshot routine (whether the day is complete), the close reports told to extensions,
and the Feishu workbench (its 日报 card, and the pipeline child it starts). Fictional data.
"""
import asyncio
import json
import subprocess
import sys

import pytest

from core import close_reports
from core.routines import close_snapshot
from tests.engine_fakes import engine_copy, engine_env, engine_portfolio, position, price_server

DAY = '2026-11-17'
CN = 'SHA:600001'


@pytest.fixture
def day(tmp_path, monkeypatch):
    """A legacy portfolio priced from a loopback server, a private engine copy and a reports directory."""
    for name in ('TRACKER_QUOTE_SOURCE', 'TRACKER_TENCENT_PROXY', 'CONDA_ENV', 'PORTFOLIO_WRITE_LOCK_INHERITED',
                 'TRACKER_DEMO_MODE', 'TRACKER_CLOSE_SNAPSHOT', 'TRACKER_LAB_ONLY'):
        monkeypatch.delenv(name, raising=False)
    reports = tmp_path / 'data' / 'reports'
    with price_server({CN: 9.0}) as base:
        portfolio = engine_portfolio(tmp_path / 'portfolio', base, day=DAY,
                                     positions=[position(CN, quantity=1000, cost_price=8)], cash=10000,
                                     cost_basis=18000)
        yield portfolio, engine_copy(tmp_path / 'checkout'), reports


def test_the_pipeline_saves_the_report_in_the_configured_directory(day):
    portfolio, scripts, reports = day
    result = subprocess.run([sys.executable, str(scripts / 'portfolio_daily_update.py'), '--action', 'pipeline',
                             '--date', DAY, '--no-push'], cwd=scripts, capture_output=True, text=True, timeout=120,
                            env=engine_env(portfolio, TRACKER_REPORTS_DIR=str(reports)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert '| 总资产 | ¥1.90万 |' in (reports / 'portfolio-20261117.md').read_text()
    assert not (scripts.parent / 'reports').exists(), 'nothing is written into the code checkout'
    state = json.loads((portfolio / 'pipeline-state' / f'{DAY}.json').read_text())
    assert state['report'] and state['complete']


def test_the_close_snapshot_routine_and_the_close_reports_read_the_configured_directory(day, monkeypatch):
    portfolio, scripts, reports = day
    monkeypatch.setenv('TRACKER_REPORTS_DIR', str(reports))
    for name, value in (('NO_PROXY', '127.0.0.1,localhost'), ('no_proxy', '127.0.0.1,localhost'),
                        ('PYTHONWARNINGS', 'ignore'), ('PORTFOLIO_DIR', str(portfolio))):
        monkeypatch.setenv(name, value)
    assert close_snapshot.reports_directory(scripts) == reports
    outcome = asyncio.run(close_snapshot.run(DAY, engine_dir=scripts, portfolio_dir=portfolio))
    assert outcome.status == 'ok', outcome.summary
    assert (reports / 'portfolio-20261117.md').is_file() and not (scripts.parent / 'reports').exists()
    finished = close_reports.completed(DAY, portfolio_dir=portfolio, engine_dir=scripts)
    assert finished['report_file'] == str((reports / 'portfolio-20261117.md').resolve())
    assert '¥1.90万' in finished['report']


def test_a_relative_setting_is_made_absolute_and_a_blank_one_is_no_setting(day, monkeypatch, tmp_path):
    _portfolio, scripts, _reports = day
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('TRACKER_REPORTS_DIR', 'kept-reports')
    assert close_snapshot.reports_directory(scripts) == tmp_path / 'kept-reports'
    monkeypatch.setenv('TRACKER_REPORTS_DIR', '  ')
    assert close_snapshot.reports_directory(scripts) == scripts.parent / 'reports'


def test_without_the_setting_the_reports_stay_beside_the_engine(day, monkeypatch):
    _portfolio, scripts, _reports = day
    monkeypatch.delenv('TRACKER_REPORTS_DIR', raising=False)
    assert close_snapshot._report_file(scripts, DAY) == scripts.parent / 'reports' / 'portfolio-20261117.md'


def test_the_feishu_workbench_reads_and_has_its_pipeline_write_the_configured_directory(tmp_path):
    from feishu_workbench.portfolio import PortfolioAdapter
    project = tmp_path / 'project'
    (project / 'engine' / 'scripts').mkdir(parents=True)
    reports = tmp_path / 'data' / 'reports'
    reports.mkdir(parents=True)
    (reports / 'portfolio-20261117.md').write_text('# 组合日报 2026-11-17\n总资产 ¥1.90万\n', encoding='utf-8')
    adapter = PortfolioAdapter.configured(project, tmp_path / 'state', environ={
        'PORTFOLIO_DIR': str(tmp_path / 'portfolio'), 'TRACKER_REPORTS_DIR': str(reports)})
    assert adapter.reports_dir == reports.resolve()
    assert [item['date'] for item in adapter.reports()['items']] == ['2026-11-17']
    default = PortfolioAdapter.configured(project, tmp_path / 'state2', environ={})
    assert default.reports_dir == project.resolve() / 'engine' / 'reports'
