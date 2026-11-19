"""The engine's dates and displayed times are Shanghai's, whatever the server's own time zone.

The trading day the scripts' ``--date`` defaults to, the scheduler's day and times, the snapshot's
``generated_at`` (stored with its offset) and the report footer's 生成时间 (shown with its zone). On a server
set to UTC the local date is the day before Shanghai's from 00:00 to 08:00 Shanghai, and a local time is
eight hours behind what a reader in China expects. An older snapshot's ``generated_at`` has no zone: it is
the local time of the machine that made it. Fictional data.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import time

import pytest

import portfolio_accounting as accounting
import portfolio_report as report
import portfolio_scheduler as scheduler
import portfolio_snapshot as snapshot
from tests.engine_fakes import position

SCRIPTS = Path(__file__).resolve().parents[1] / 'engine' / 'scripts'
UTC = timezone.utc


def clock_at(moment):
    """A ``datetime`` whose ``now`` is ``moment`` (aware) seen from a machine set to UTC."""
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            seen = moment.astimezone(tz or UTC)
            return seen if tz is not None else seen.replace(tzinfo=None)
    return Clock


@pytest.fixture
def utc_machine(monkeypatch):
    monkeypatch.setenv('TZ', 'UTC')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize('moment, day', [
    (datetime(2026, 11, 16, 18, 46, tzinfo=UTC), '2026-11-17'),    # 02:46 Shanghai: already Tuesday there
    (datetime(2026, 11, 16, 15, 59, tzinfo=UTC), '2026-11-16'),    # 23:59 Shanghai
    (datetime(2026, 11, 17, 7, 40, tzinfo=UTC), '2026-11-17'),
])
def test_the_default_date_is_shanghais_today(monkeypatch, moment, day):
    monkeypatch.setattr(accounting, 'datetime', clock_at(moment))
    assert accounting.market_today() == day


def test_every_script_defaults_its_date_to_shanghais_today():
    for name in ('portfolio_snapshot.py', 'portfolio_report.py', 'portfolio_daily_update.py', 'portfolio_manager.py'):
        source = (SCRIPTS / name).read_text(encoding='utf-8')
        assert re.search(r'"--date", default=market_today\(\)', source), name
        assert 'datetime.now().strftime' not in source, name
    for name in ('portfolio-cron.sh', 'portfolio-daily.sh'):
        source = (SCRIPTS / name).read_text(encoding='utf-8')
        assert 'TODAY=$(TZ=CST-8 date +%Y-%m-%d)' in source and 'DOW=$(TZ=CST-8 date +%u)' in source, name


def test_the_report_of_today_is_shanghais_today(monkeypatch, tmp_path, capsys):
    """``portfolio_report.py`` without ``--date`` at 02:46 Shanghai on a UTC machine reports Tuesday."""
    folder = tmp_path / 'snapshots'
    folder.mkdir()
    holdings = {'date': '2026-11-17', 'groups': {'E': {'cash': 0, 'fund': 0, 'cost_basis': 100, 'positions': [
        position('SHA:600001', quantity=10, cost_price=10)]}}}
    saved = snapshot.calculate_snapshot(holdings, {'SHA:600001': 10.0}, {}, {'CNY': 1.0}, None, [])
    (folder / '2026-11-17.json').write_text(json.dumps(saved))
    monkeypatch.setattr(report, 'PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.setattr(accounting, 'datetime', clock_at(datetime(2026, 11, 16, 18, 46, tzinfo=UTC)))
    monkeypatch.setattr(sys, 'argv', ['portfolio_report.py'])
    assert '2026-11-17' in report.main()


def test_the_snapshot_records_when_it_was_made_with_shanghais_offset():
    holdings = {'date': '2026-11-17', 'groups': {'E': {'cash': 0, 'fund': 0, 'cost_basis': 100, 'positions': [
        position('SHA:600001', quantity=10, cost_price=10)]}}}
    before = datetime.now(UTC)
    stamp = snapshot.calculate_snapshot(holdings, {'SHA:600001': 10.0}, {}, {'CNY': 1.0}, None, [])['generated_at']
    made = datetime.fromisoformat(stamp)
    assert stamp[-6:] == '+08:00' and made.utcoffset() == timedelta(hours=8)
    assert before - timedelta(seconds=1) <= made <= datetime.now(UTC) + timedelta(seconds=1)


@pytest.mark.parametrize('generated_at, shown', [
    ('2026-11-19T15:41:00.123456+08:00', '2026-11-19 15:41:00（北京时间）'),
    ('2026-11-19T07:41:00+00:00', '2026-11-19 15:41:00（北京时间）'),
    ('2026-11-19T07:41:00.123456', '2026-11-19 15:41:00（北京时间）'),   # an older snapshot, made on UTC
    ('2026-11-19', '2026-11-19'),                                       # not a time: as before
    ('not a time T at all, longer', 'not a time T at all'),
    (None, '?'),
])
def test_the_footer_shows_the_generation_time_in_beijing_time_with_its_zone(utc_machine, generated_at, shown):
    assert report.generated_text({'generated_at': generated_at} if generated_at is not None else {}) == shown


def test_the_whole_footer_line(utc_machine):
    holdings = {'date': '2026-11-19', 'groups': {'E': {'cash': 0, 'fund': 0, 'cost_basis': 100, 'positions': [
        position('SHA:600001', quantity=10, cost_price=10)]}}}
    saved = {**snapshot.calculate_snapshot(holdings, {'SHA:600001': 10.0}, {}, {'CNY': 1.0}, None, []),
             'generated_at': '2026-11-19T15:41:00.123456+08:00'}
    assert report.generate_report(saved).splitlines()[-1] == \
        '*数据来源: Yahoo Finance | 生成时间: 2026-11-19 15:41:00（北京时间）*'


# -- the scheduler ----------------------------------------------------------------------------------

@pytest.mark.parametrize('moment, runs', [
    (datetime(2026, 1, 9, 10, 0, tzinfo=UTC), [('notify', '2026-01-09')]),          # 18:00 Beijing, Friday
    (datetime(2026, 1, 9, 18, 0, tzinfo=UTC), []),                                  # 18:00 UTC is 02:00 Saturday
    (datetime(2026, 1, 8, 23, 1, tzinfo=UTC), [('auto-pipeline', '2026-01-09')]),   # 07:01 Beijing, Friday
])
def test_the_scheduler_keeps_beijing_time_and_the_trading_day(monkeypatch, moment, runs):
    started = []
    monkeypatch.setattr(scheduler, 'datetime', clock_at(moment))
    monkeypatch.setattr(scheduler, 'run_action', lambda action, day: started.append((action, day)))
    monkeypatch.setattr(scheduler, 'log', lambda message: None)
    monkeypatch.setattr(scheduler, 'daily_state', {'notified_date': None, 'pipeline_date': None})
    scheduler.check_and_run(18, 0, 7, 0)
    assert started == runs


def test_the_scheduler_logs_beijing_time(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(scheduler, 'datetime', clock_at(datetime(2026, 1, 8, 23, 1, tzinfo=UTC)))
    monkeypatch.setattr(scheduler, 'LOG_DIR', tmp_path)
    scheduler.log('example')
    assert capsys.readouterr().out == '[2026-01-09 07:01:00] example\n'
    assert (tmp_path / 'scheduler-20260109.log').read_text() == '[2026-01-09 07:01:00] example\n'
