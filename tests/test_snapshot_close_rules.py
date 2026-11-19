"""The close snapshot judges each quote by Today's decision table (``core.market.quotes.assess``).

* A close Today verifies is booked.
* A price Today calls stale is not booked. One of the latest session stamped before its closing print (the
  reviewer's case: Hong Kong stamped 15:55:12, seen at 16:10) waits until the print plus the sources' usual
  delay (``quotes.CLOSE_WINDOW``); after that it is a missing price, and the run is retried.
* A holding that did not trade in its market's latest session (suspended; Today shows it as 估算) is booked at
  its last close and marked as an estimate (the reviewer's second case: a suspended mainland stock's Monday
  price entered Tuesday unqualified). While that market's session is under way it waits for the close.
* A close of a year the holiday table does not cover is booked and marked, as Today marks it.

The marks reach the snapshot file (``summary.estimated``, ``summary.estimated_reasons``, a position's
``estimated_reason``), the tracker history, the daily report and the close snapshot routine's summary, which
the post-market brief shows. Stubbed fetches and a loopback price server; fictional prices.
"""
import asyncio
from datetime import datetime, timezone
import json

import pytest

import portfolio_report as report
import portfolio_snapshot as snapshot
from tests.engine_fakes import eastmoney_url, engine_copy, position, price_server, tencent_url
from tests.fakes import build_ledger, opening
from tests.fakes import position as ledger_position

DAY = '2026-11-17'                      # Tuesday
UTC = timezone.utc
CN, HK = 'SHA:600001', 'HKG:00003'


def at(*parts):
    return datetime(*parts, tzinfo=UTC)


def epoch(*parts):
    return int(at(*parts).timestamp())


class Response:
    def __init__(self, meta):
        self.meta = meta

    def raise_for_status(self):
        pass

    def json(self):
        return {'chart': {'result': [{'meta': self.meta}]}}


def judged(monkeypatch, ticker, currency, stamp, now, day=DAY):
    """``(prices, not_final, estimated)`` of one holding fetched at ``now``."""
    monkeypatch.setattr(snapshot.requests, 'get', lambda url, **options: Response(
        {'regularMarketPrice': 10.5, 'currency': currency, 'regularMarketTime': stamp}))
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    not_final, estimated = {}, {}
    prices, _currencies = snapshot.fetch_prices({'groups': {'E': {'positions': [position(ticker)]}}}, {}, day,
                                                not_final, estimated)
    return prices, not_final, estimated


# -- stale: not booked ------------------------------------------------------------------------------

def test_a_hong_kong_price_stamped_before_the_closing_print_is_not_the_close(monkeypatch):
    # 16:10 Hong Kong: the close is final, but the source still shows 15:55:12; the print is at 16:08.
    prices, not_final, estimated = judged(monkeypatch, HK, 'HKD', epoch(2026, 11, 17, 7, 55, 12), at(2026, 11, 17, 8, 10))
    assert prices == {} and estimated == {}
    assert not_final == {HK: at(2026, 11, 17, 8, 28)}       # the print (16:08) plus 20 minutes


def test_after_the_sources_delay_the_days_last_price_is_booked_and_marked(monkeypatch):
    # A thinly traded stock with no trade in the closing auction, or a source that does not restamp its close:
    # Today shows the price stale (an estimate); the snapshot books it the same way instead of failing every day.
    prices, not_final, estimated = judged(monkeypatch, HK, 'HKD', epoch(2026, 11, 17, 7, 55, 12), at(2026, 11, 17, 8, 40))
    assert (prices, not_final) == ({HK: 10.5}, {})
    assert estimated == {HK: '收盘竞价后没有新的成交时间，按当日最后成交价记入'}


def test_a_day_whose_close_never_printed_ends_ok_with_the_mark(tmp_path, monkeypatch):
    from core.routines import close_snapshot
    for name in ('TRACKER_DEMO_MODE', 'TRACKER_QUOTE_SOURCE', 'TRACKER_CLOSE_SNAPSHOT', 'CONDA_ENV',
                 'PORTFOLIO_WRITE_LOCK_INHERITED', 'TRACKER_LAB_ONLY'):
        monkeypatch.delenv(name, raising=False)
    portfolio = tmp_path / 'portfolio'
    portfolio.mkdir()
    for name, value in (('NO_PROXY', '127.0.0.1,localhost'), ('no_proxy', '127.0.0.1,localhost'),
                        ('PYTHONWARNINGS', 'ignore'), ('PORTFOLIO_DIR', str(portfolio))):
        monkeypatch.setenv(name, value)
    build_ledger(portfolio, [opening('Example', cash={'CNY': '1000'}, contributed_cny='2000', date='2026-11-16',
                                     positions=[ledger_position(HK, '100', '8', name='Example')])])
    scripts = engine_copy(tmp_path / 'checkout')
    with price_server({HK: 9.0, 'FX:HKDCNY=X': 0.9}, times={HK: epoch(2026, 11, 17, 7, 55, 12)}) as base:
        (portfolio / 'config.json').write_text(json.dumps({'fx_tickers': {'HKD_CNY': 'HKDCNY=X'}, 'yahoo_base': base,
                                                           'eastmoney_url': eastmoney_url(base),
                                                           'tencent_url': tencent_url(base)}))
        outcome = asyncio.run(close_snapshot.run(DAY, engine_dir=scripts, portfolio_dir=portfolio))
    assert outcome.status == 'ok', outcome.summary
    assert '收盘竞价后没有新的成交时间' in outcome.summary
    saved = json.loads((portfolio / 'snapshots' / f'{DAY}.json').read_text())
    assert saved['groups']['Example']['positions'][0]['current_price'] == 9.0 and saved['summary']['estimated']


def test_the_closing_print_is_booked_without_a_mark(monkeypatch):
    prices, not_final, estimated = judged(monkeypatch, HK, 'HKD', epoch(2026, 11, 17, 8, 8, 2), at(2026, 11, 17, 8, 10))
    assert (prices, not_final, estimated) == ({HK: 10.5}, {}, {})


# -- did not trade in the latest session: the last close, marked ------------------------------------

def test_a_suspended_stock_is_booked_at_its_last_close_and_marked(monkeypatch):
    # Monday's 15:00 close, seen on Tuesday at 15:40: Tuesday's session passed without a price.
    prices, not_final, estimated = judged(monkeypatch, CN, 'CNY', epoch(2026, 11, 16, 7, 0), at(2026, 11, 17, 7, 40))
    assert prices == {CN: 10.5} and not_final == {}
    assert set(estimated) == {CN} and '2026-11-16' in estimated[CN] and '停牌' in estimated[CN]


@pytest.mark.parametrize('ticker, currency, now, final', [
    (CN, 'CNY', at(2026, 11, 17, 2, 0), at(2026, 11, 17, 7, 0)),       # 10:00: wait for Tuesday's 15:00 close
    (HK, 'HKD', at(2026, 11, 17, 7, 40), at(2026, 11, 17, 8, 10)),     # 15:40: Hong Kong still trades
])
def test_while_the_session_is_under_way_a_suspended_stock_waits_for_its_close(monkeypatch, ticker, currency, now,
                                                                              final):
    prices, not_final, estimated = judged(monkeypatch, ticker, currency, epoch(2026, 11, 16, 7, 0), now)
    assert (prices, not_final, estimated) == ({}, {ticker: final}, {})


def test_a_close_of_a_year_without_the_holiday_table_is_marked(monkeypatch):
    prices, not_final, estimated = judged(monkeypatch, CN, 'CNY', epoch(2030, 1, 8, 7, 0), at(2030, 1, 8, 7, 40),
                                          day='2030-01-08')
    assert prices == {CN: 10.5} and not_final == {}
    assert '节假日表' in estimated[CN]


def test_a_retry_after_midnight_still_books_the_days_close_unmarked(monkeypatch):
    prices, not_final, estimated = judged(monkeypatch, CN, 'CNY', epoch(2026, 11, 17, 7, 0), at(2026, 11, 17, 17, 0))
    assert (prices, not_final, estimated) == ({CN: 10.5}, {}, {})


# -- the marks in the snapshot, the history, the report and the routine ------------------------------

def holdings():
    return {'date': DAY, 'groups': {'Example': {'cash': 1000, 'fund': 0, 'cost_basis': 2000, 'positions': [
        position(CN, 100, 10), position('SHA:600002', 100, 10)]}}}


def test_a_marked_price_marks_its_position_and_the_snapshot():
    reason = '2026-11-16 之后没有成交（可能停牌），按当日收盘价记入'
    saved = snapshot.calculate_snapshot(holdings(), {CN: 10.5, 'SHA:600002': 11.0}, {}, {'CNY': 1.0}, None, [],
                                        price_estimates={CN: reason})
    rows = {p['ticker']: p for p in saved['groups']['Example']['positions']}
    assert rows[CN]['estimated_reason'] == reason and 'estimated_reason' not in rows['SHA:600002']
    assert saved['summary']['estimated'] is True
    assert saved['summary']['estimated_reasons'] == [f'{CN}：{reason}']
    text = report.generate_report(saved)
    assert '含估算' in text and reason in text


def test_an_unmarked_snapshot_says_so():
    saved = snapshot.calculate_snapshot(holdings(), {CN: 10.5, 'SHA:600002': 11.0}, {}, {'CNY': 1.0}, None, [])
    assert (saved['summary']['estimated'], saved['summary']['estimated_reasons']) == (False, [])
    assert '含估算' not in report.generate_report(saved)


MONDAY_CLOSE = epoch(2026, 11, 16, 7, 0)


def test_a_suspended_holding_reaches_the_routine_summary_and_the_tracker_history(tmp_path, monkeypatch):
    from core.routines import close_snapshot
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import portfolio_tracker as tracker

    for name in ('TRACKER_DEMO_MODE', 'TRACKER_QUOTE_SOURCE', 'TRACKER_CLOSE_SNAPSHOT', 'CONDA_ENV',
                 'PORTFOLIO_WRITE_LOCK_INHERITED', 'TRACKER_LAB_ONLY'):
        monkeypatch.delenv(name, raising=False)
    portfolio = tmp_path / 'portfolio'
    portfolio.mkdir()
    for name, value in (('NO_PROXY', '127.0.0.1,localhost'), ('no_proxy', '127.0.0.1,localhost'),
                        ('PYTHONWARNINGS', 'ignore'), ('PORTFOLIO_DIR', str(portfolio))):
        monkeypatch.setenv(name, value)
    build_ledger(portfolio, [opening('Example', cash={'CNY': '1000'}, contributed_cny='2000', date='2026-11-16',
                                     positions=[ledger_position(CN, '100', '8', name='Example')])])
    scripts = engine_copy(tmp_path / 'checkout')
    with price_server({CN: 9.0}, times={CN: MONDAY_CLOSE}) as base:
        (portfolio / 'config.json').write_text(json.dumps({'fx_tickers': {}, 'yahoo_base': base,
                                                            'tencent_url': tencent_url(base)}))
        outcome = asyncio.run(close_snapshot.run(DAY, engine_dir=scripts, portfolio_dir=portfolio))
    assert outcome.status == 'ok', outcome.summary
    assert '含估算' in outcome.summary and CN in outcome.summary
    saved = json.loads((portfolio / 'snapshots' / f'{DAY}.json').read_text())
    assert saved['summary']['estimated'] is True and saved['summary']['total_value'] == 1900

    monkeypatch.setattr(tracker, 'PORTFOLIO_DIR', str(portfolio))
    app = FastAPI()
    app.include_router(tracker.router, prefix='/api/tracker')
    row = TestClient(app).get('/api/tracker/history').json()['history'][-1]
    assert row['date'] == DAY and row['estimated'] is True
    assert any(CN in reason for reason in row['estimated_reasons'])
