"""A day's snapshot books the day's own closes, never an earlier day's close as the day's.

The Feishu card runs the pipeline after an entry dated today, at any hour: at 02:46 Shanghai the mainland's
and Hong Kong's latest close is the day before's. The snapshot of the day waits for the day's close, as it
waits for any close not final yet (it is not saved; the close snapshot routine looks again at that moment).
The session a day books is each market's last session that ends on the day (Shanghai's date): the mainland's
and Hong Kong's own, New York's of the night before. A market closed on the day carries its last close. A
snapshot an older version saved before the day's close was final is made again by the pipeline, with its
history row and report. Stubbed fetches and loopback servers; fictional prices.
"""
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import portfolio_daily_update as daily
import portfolio_report as report
import portfolio_snapshot as snapshot
from tests.engine_fakes import engine_copy, engine_env, position, price_server, tencent_url
from tests.fakes import build_ledger, opening
from tests.fakes import position as ledger_position
from tests.tencent_recorded import answer

DAY = '2026-11-17'                      # Tuesday
UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))
CN, SZ, HK, US = 'SHA:600001', 'SHE:000001', 'HKG:00003', 'NASDAQ:EXMP'


def at(*parts):
    return datetime(*parts, tzinfo=UTC)


def epoch(*parts):
    return int(at(*parts).timestamp())


MONDAY_CN_CLOSE = epoch(2026, 11, 16, 7, 0)       # 15:00 Shanghai
MONDAY_HK_CLOSE = epoch(2026, 11, 16, 8, 8)       # 16:08 Hong Kong, the closing auction's print
MONDAY_US_CLOSE = epoch(2026, 11, 16, 21, 0)      # 16:00 New York
CURRENCIES = {'SHA': 'CNY', 'SHE': 'CNY', 'HKG': 'HKD', 'NASDAQ': 'USD'}


class Response:
    def __init__(self, meta):
        self.meta = meta

    def raise_for_status(self):
        pass

    def json(self):
        return {'chart': {'result': [{'meta': self.meta}]}}


def judged(monkeypatch, ticker, stamp, now, day=DAY):
    """``(prices, not_final, estimated)`` of one holding Yahoo quotes at ``stamp``, fetched at ``now``."""
    monkeypatch.setattr(snapshot.requests, 'get', lambda url, **options: Response(
        {'regularMarketPrice': 10.5, 'currency': CURRENCIES[ticker.split(':')[0]], 'regularMarketTime': stamp}))
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    not_final, estimated = {}, {}
    prices, _currencies = snapshot.fetch_prices({'groups': {'E': {'positions': [position(ticker)]}}}, {}, day,
                                                not_final, estimated)
    return prices, not_final, estimated


# -- before the day's session has closed: the day before's close is not the day's -----------------

@pytest.mark.parametrize('ticker, stamp, now, day, final', [
    # 02:46 Shanghai on Tuesday: Monday's close is the latest; Tuesday's is final at 15:00.
    (CN, MONDAY_CN_CLOSE, at(2026, 11, 16, 18, 46), DAY, at(2026, 11, 17, 7, 0)),
    (SZ, MONDAY_CN_CLOSE, at(2026, 11, 16, 18, 46), DAY, at(2026, 11, 17, 7, 0)),
    # 09:00, before the opening auction's first price (09:15).
    (CN, MONDAY_CN_CLOSE, at(2026, 11, 17, 1, 0), DAY, at(2026, 11, 17, 7, 0)),
    # Hong Kong at 08:30, before its first price (09:00): Tuesday's close is final at 16:10.
    (HK, MONDAY_HK_CLOSE, at(2026, 11, 17, 0, 30), DAY, at(2026, 11, 17, 8, 10)),
    # Wednesday's snapshot asked on Tuesday evening: Tuesday's close is not Wednesday's.
    (CN, epoch(2026, 11, 17, 7, 0), at(2026, 11, 17, 12, 0), '2026-11-18', at(2026, 11, 18, 7, 0)),
    # New York: Tuesday's snapshot books Monday night's session; asked on Monday evening (07:00 New York),
    # Friday's close is the latest, and Monday's is final at 16:30 New York, 05:30 Shanghai on Tuesday.
    (US, epoch(2026, 11, 13, 21, 0), at(2026, 11, 16, 12, 0), DAY, at(2026, 11, 16, 21, 30)),
])
def test_before_the_days_session_has_closed_an_earlier_close_is_not_booked(monkeypatch, capsys, ticker, stamp,
                                                                            now, day, final):
    prices, not_final, estimated = judged(monkeypatch, ticker, stamp, now, day)
    assert (prices, not_final, estimated) == ({}, {ticker: final}, {})
    assert f'the snapshot is for {day}' in capsys.readouterr().err


def test_the_wait_says_when_the_snapshot_will_be_made():
    holdings = {'date': DAY, 'groups': {'E': {'cash': 0, 'fund': 0, 'cost_basis': 0, 'positions': [position(CN)]}}}
    with pytest.raises(SystemExit) as stopped:
        snapshot._generate(SimpleNamespace(date=DAY, dry_run=True), {}, holdings,
                           ({}, {}, {'CNY': 1.0}, {CN: at(2026, 11, 17, 7, 0)}))
    text = str(stopped.value.code)
    assert '等待A股收盘价确定，15:00 后记入' in text and snapshot.MISSING_PRICE_MESSAGE not in text
    assert text.splitlines()[-1] == 'CLOSE_NOT_FINAL_UNTIL=2026-11-17T15:00:00+08:00 MARKETS=CN'


def test_tencents_row_of_the_day_before_is_not_the_days_close_either(monkeypatch, capsys):
    """The recorded row of 2026-11-19 (stamped 16:14:55), asked for Friday's snapshot at 02:46 on Friday."""
    class Body:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    def get(url, **options):
        if url == snapshot.TENCENT_URL:
            return Body(answer(options['params']['q'].split(',')))
        raise OSError('Yahoo cannot be reached in this test')
    monkeypatch.setattr(snapshot.requests, 'get', get)
    monkeypatch.setattr(snapshot, '_now', lambda: at(2026, 11, 19, 18, 46))
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    not_final = {}
    prices, _ = snapshot.fetch_prices({'groups': {'E': {'positions': [position('SHA:600519')]}}}, {}, '2026-11-20',
                                      not_final)
    assert prices == {} and not_final == {'SHA:600519': at(2026, 11, 20, 7, 0)}
    assert '价格取自腾讯行情' not in capsys.readouterr().out


# -- what stays as it was --------------------------------------------------------------------------

@pytest.mark.parametrize('ticker, stamp, now, day', [
    # After the day's close: booked, as always.
    (CN, epoch(2026, 11, 17, 7, 0), at(2026, 11, 17, 7, 40), DAY),
    (HK, epoch(2026, 11, 17, 8, 8), at(2026, 11, 17, 8, 25), DAY),
    # New York at 15:40 Shanghai: Monday night's close, final since 05:30, is Tuesday's.
    (US, MONDAY_US_CLOSE, at(2026, 11, 17, 7, 40), DAY),
    # Friday after Thanksgiving (2026-11-26, New York closed): Wednesday night's close.
    (US, epoch(2026, 11, 25, 21, 0), at(2026, 11, 27, 7, 40), '2026-11-27'),
    # A market closed on the day carries its last close, at any hour: Saturday at 02:46 ...
    (CN, epoch(2026, 11, 20, 7, 0), at(2026, 11, 20, 18, 46), '2026-11-21'),
    # ... the mainland's National Day holiday at 02:46, and Hong Kong's Christmas at 02:46.
    (CN, epoch(2026, 9, 30, 7, 0), at(2026, 9, 30, 18, 46), '2026-10-01'),
    (HK, epoch(2026, 12, 24, 4, 8), at(2026, 12, 24, 18, 46), '2026-12-25'),
])
def test_a_close_the_day_books_is_booked_as_before(monkeypatch, ticker, stamp, now, day):
    assert judged(monkeypatch, ticker, stamp, now, day) == ({ticker: 10.5}, {}, {})


def test_hong_kong_trading_while_the_mainland_is_closed_waits_for_its_own_close(monkeypatch):
    # 2026-10-05: the mainland's holiday, a Hong Kong trading day; asked at 02:46.
    prices, not_final, _ = judged(monkeypatch, HK, epoch(2026, 9, 30, 8, 8), at(2026, 10, 4, 18, 46), '2026-10-05')
    assert prices == {} and not_final == {HK: at(2026, 10, 5, 8, 10)}


def test_new_york_overnight_waits_for_the_close_of_the_night_before_as_before(monkeypatch):
    # 02:46 Shanghai on Tuesday is 13:46 New York on Monday: Monday's session is under way.
    prices, not_final, _ = judged(monkeypatch, US, epoch(2026, 11, 16, 18, 45), at(2026, 11, 16, 18, 46))
    assert prices == {} and not_final == {US: at(2026, 11, 16, 21, 30)}


# -- a snapshot an older version saved before the close was final ----------------------------------

def saved(generated_at, *tickers, day=DAY, **extra):
    return {'date': day, 'generated_at': generated_at, **extra, 'groups': {'Example': {'positions': [
        {'ticker': ticker, 'quantity': 100} for ticker in tickers]}}}


@pytest.fixture
def utc_machine(monkeypatch):
    """This process on a machine set to UTC (an older snapshot's ``generated_at`` is that machine's local time)."""
    monkeypatch.setenv('TZ', 'UTC')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize('snap, final', [
    (saved('2026-11-17T02:46:00.123456+08:00', CN), at(2026, 11, 17, 7, 0)),
    (saved('2026-11-16T18:46:00.123456', CN, HK), at(2026, 11, 17, 8, 10)),     # an older version's, on UTC
    (saved('2026-11-17T15:05:00+08:00', CN, HK), at(2026, 11, 17, 8, 10)),      # before Hong Kong's close
])
def test_a_snapshot_made_before_a_close_it_books_was_final_is_found(utc_machine, snap, final):
    made, latest = snapshot.made_before_close(snap, DAY)
    assert latest == final and made < final


@pytest.mark.parametrize('snap, day', [
    (saved('2026-11-17T15:40:12.500000+08:00', CN), DAY),
    (saved('2026-11-17T07:40:12', CN), DAY),                                  # 15:40 Shanghai, on UTC
    (saved('2026-11-17T15:40:00+08:00', US), DAY),                             # Monday night's close
    (saved('2026-11-21T02:46:00+08:00', CN, day='2026-11-21'), '2026-11-21'),   # Saturday: Friday's close
    (saved('2026-11-17T02:46:00+08:00'), DAY),                                 # cash only: no close
    ({**saved('2026-11-17T02:46:00+08:00'), 'groups': {'Example': {'positions': [
        {'ticker': CN, 'quantity': 0}]}}}, DAY),                               # nothing held
    (saved('2026-11-17', CN), DAY),                                            # no time: not judged
    ({'date': DAY, 'groups': {'Example': {'positions': [{'ticker': CN, 'quantity': 100}]}}}, DAY),
    (saved('not a time T', CN), DAY),
])
def test_a_snapshot_made_after_its_closes_or_without_a_time_is_not_judged_early(utc_machine, snap, day):
    assert snapshot.made_before_close(snap, day) is None


def early_day(tmp_path, monkeypatch, *, ledger=True):
    """A portfolio whose Tuesday snapshot, history row, report and checkpoint an older version made at 02:46
    Shanghai on a machine set to UTC, from Monday's close of 8.5. Returns ``(portfolio, scripts, report file)``."""
    for name in ('TRACKER_QUOTE_SOURCE', 'TRACKER_TENCENT_PROXY', 'CONDA_ENV', 'PORTFOLIO_WRITE_LOCK_INHERITED'):
        monkeypatch.delenv(name, raising=False)
    portfolio = tmp_path / 'portfolio'
    (portfolio / 'snapshots').mkdir(parents=True)
    (portfolio / 'holdings').mkdir()
    holdings = {'date': DAY, 'groups': {'Example': {'cash': 10000, 'fund': 0, 'cost_basis': 18000, 'positions': [
        position(CN, quantity=1000, cost_price=8)]}}}
    if ledger:
        from ledger_bridge import ledger_holdings
        build_ledger(portfolio, [opening('Example', cash={'CNY': '10000'}, contributed_cny='18000',
                                         date='2026-11-16', positions=[ledger_position(CN, '1000', '8', name='Example')])])
        holdings = {**ledger_holdings(portfolio, DAY), 'date': DAY}
    else:
        (portfolio / 'holdings' / f'{DAY}.json').write_text(json.dumps({**holdings, 'cloned_from': '2026-11-16'}))
    scripts = engine_copy(tmp_path / 'checkout')
    monkeypatch.setattr(daily, 'PORTFOLIO_DIR', portfolio)
    monkeypatch.setattr(daily, 'HOLDINGS_DIR', portfolio / 'holdings')
    monkeypatch.setattr(daily, 'SNAPSHOTS_DIR', portfolio / 'snapshots')
    morning = {**snapshot.calculate_snapshot(holdings, {CN: 8.5}, {CN: 'CNY'}, {'CNY': 1.0}, None, []),
               'generated_at': '2026-11-16T18:46:03.250000'}
    snap_file = portfolio / 'snapshots' / f'{DAY}.json'
    snap_file.write_text(json.dumps(morning, ensure_ascii=False, indent=2))
    snapshot.upsert_history(morning, DAY, portfolio)
    report_file = scripts.parent / 'reports' / 'portfolio-20261117.md'
    report_file.parent.mkdir()
    report_file.write_text(report.generate_report(morning))
    sha = daily.file_sha(snap_file)
    (portfolio / 'pipeline-state').mkdir()
    (portfolio / 'pipeline-state' / f'{DAY}.json').write_text(json.dumps({
        'holdings_hash': daily.holdings_digest(DAY), 'snapshot': True, 'history': True, 'report': True,
        'push': 'not_started', 'snapshot_before': None, 'snapshot_sha': sha, 'history_snapshot': sha,
        'report_snapshot': sha, 'report_sha': daily.file_sha(report_file), 'complete': True}))
    return portfolio, scripts, report_file


@pytest.mark.parametrize('action, ledger', [('auto-pipeline', True), ('pipeline', False)])
def test_the_pipeline_makes_an_early_snapshot_of_an_older_version_again(tmp_path, monkeypatch, action, ledger):
    portfolio, scripts, report_file = early_day(tmp_path, monkeypatch, ledger=ledger)
    with price_server({CN: 9.0}, times={CN: epoch(2026, 11, 17, 7, 0)}) as base:
        (portfolio / 'config.json').write_text(json.dumps({'fx_tickers': {}, 'yahoo_base': base,
                                                           'tencent_url': tencent_url(base)}))
        command = [sys.executable, str(scripts / 'portfolio_daily_update.py'), '--action', action, '--date', DAY]
        if action == 'pipeline':
            command.append('--no-push')
        result = subprocess.run(command, cwd=scripts, env=engine_env(portfolio, TZ='UTC'), capture_output=True,
                                text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert daily.MADE_BEFORE_CLOSE in result.stdout and '今日快照已存在' not in result.stdout
    assert '生成时间 2026-11-17 02:46:03（北京时间），收盘价 2026-11-17 15:00:00（北京时间）才确定' in result.stdout
    again = json.loads((portfolio / 'snapshots' / f'{DAY}.json').read_text())
    assert again['groups']['Example']['positions'][0]['current_price'] == 9.0
    assert again['summary']['total_value'] == 19000
    assert daily.history_line(DAY).startswith(f'{DAY},19000')
    assert '| 总资产 | ¥1.90万 |' in report_file.read_text()
    state = json.loads((portfolio / 'pipeline-state' / f'{DAY}.json').read_text())
    assert state['snapshot_sha'] == state['report_snapshot'] == daily.file_sha(portfolio / 'snapshots' / f'{DAY}.json')
    assert state['report_sha'] == daily.file_sha(report_file) and state['complete'] is True


def test_a_snapshot_made_after_the_close_is_completed_not_made_again(tmp_path, monkeypatch):
    portfolio, scripts, report_file = early_day(tmp_path, monkeypatch)
    snap_file = portfolio / 'snapshots' / f'{DAY}.json'
    made = {**json.loads(snap_file.read_text()), 'generated_at': '2026-11-17T15:40:03+08:00'}
    snap_file.write_text(json.dumps(made))
    with price_server({}) as base:
        (portfolio / 'config.json').write_text(json.dumps({'fx_tickers': {}, 'yahoo_base': base}))
        result = subprocess.run([sys.executable, str(scripts / 'portfolio_daily_update.py'), '--action',
                                 'auto-pipeline', '--date', DAY], cwd=scripts, env=engine_env(portfolio, TZ='UTC'),
                                capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '今日快照已存在' in result.stdout and daily.MADE_BEFORE_CLOSE not in result.stdout
    assert json.loads(snap_file.read_text()) == made
