"""The daily snapshot prices from Tencent the holdings Yahoo did not price, by Today's rules.

Tencent's answers are the ones recorded on 2026-11-19 (``tests/tencent_recorded.py``), read by Today's own
reading (``core.market.tencent``); requests are stubbed, or served by a loopback server for the real script.
No network.
"""
from datetime import datetime, timezone
import json
import subprocess
import sys

import pytest

import portfolio_report as report
import portfolio_snapshot as snapshot
from tests.engine_fakes import SCRIPTS, engine_env, engine_portfolio, position, price_server
from tests.tencent_recorded import ROWS, answer, fields

DAY = '2026-11-19'                      # Thursday
UTC = timezone.utc
EVENING = datetime(2026, 11, 19, 14, 59, 1, tzinfo=UTC)          # 22:59 Shanghai, when the rows were recorded


class Chart:
    def __init__(self, meta):
        self.meta = meta

    def raise_for_status(self):
        pass

    def json(self):
        return {'chart': {'result': [{'meta': self.meta}]}}


class Body:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def sources(monkeypatch, *, yahoo=None, rows=None, now=EVENING, asked=None):
    """Stub both sources: Yahoo answers ``yahoo`` (symbol -> meta; anything else fails), Tencent ``rows``."""
    asked = [] if asked is None else asked

    def get(url, **options):
        if url == snapshot.TENCENT_URL:
            asked.append(('tencent', options['params']['q'], options['proxies']))
            return Body(answer(options['params']['q'].split(','), rows))
        symbol = url.rsplit('/', 1)[-1]
        asked.append(('yahoo', symbol, options['proxies']))
        if symbol not in (yahoo or {}):
            raise OSError('Yahoo cannot be reached in this test')
        return Chart(yahoo[symbol])
    monkeypatch.setattr(snapshot.requests, 'get', get)
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    return asked


def priced(monkeypatch, tickers, config=None, day=DAY, **stubs):
    """``(prices, currencies, not_final, estimated, look_again, requests made)`` of ``fetch_prices``."""
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    monkeypatch.delenv('TRACKER_TENCENT_PROXY', raising=False)
    asked = sources(monkeypatch, **stubs)
    not_final, estimated, look_again = {}, {}, {}
    holdings = {'groups': {'E': {'positions': [position(ticker) for ticker in tickers]}}}
    prices, currencies = snapshot.fetch_prices(holdings, config or {}, day, not_final, estimated, look_again)
    return prices, currencies, not_final, estimated, look_again, asked


def test_holdings_yahoo_cannot_price_are_priced_from_tencent_in_one_request(monkeypatch, capsys):
    prices, currencies, not_final, estimated, _look, asked = priced(
        monkeypatch, ['SHA:600519', 'HKG:00700', 'SHA:510300'])
    assert prices == {'SHA:600519': 1460.21, 'HKG:00700': 348.4, 'SHA:510300': 4.06}
    assert currencies == {'SHA:600519': 'CNY', 'HKG:00700': 'HKD', 'SHA:510300': 'CNY'}
    assert (not_final, estimated) == ({}, {})
    assert [entry[:2] for entry in asked if entry[0] == 'tencent'] == [('tencent', 'hk00700,sh510300,sh600519')]
    out = capsys.readouterr().out
    assert 'SHA:600519 价格取自腾讯行情（sh600519）' in out and 'HKG:00700 价格取自腾讯行情（hk00700）' in out


def test_a_price_yahoo_books_is_kept_and_tencent_is_not_asked(monkeypatch):
    close = int(datetime(2026, 11, 19, 7, 0, 2, tzinfo=UTC).timestamp())
    prices, _currencies, _waits, _estimated, _look, asked = priced(monkeypatch, ['SHA:600519'], yahoo={
        '600519.SS': {'regularMarketPrice': 1460.2, 'currency': 'CNY', 'regularMarketTime': close}})
    assert prices == {'SHA:600519': 1460.2} and [entry[0] for entry in asked] == ['yahoo']


def test_a_suspended_holding_is_booked_at_the_previous_close_and_marked(monkeypatch):
    prices, _currencies, not_final, estimated, _look, _asked = priced(monkeypatch, ['SHA:600185', 'HKG:00021'])
    assert prices == {'SHA:600185': 4.28, 'HKG:00021': 0.058} and not_final == {}
    # Untraded on the 19th: the price is the close of the 18th, the session before.
    assert estimated['SHA:600185'] == '2026-11-19 没有新的成交价（可能停牌或数据源未更新），按 2026-11-18 的收盘价记入'
    assert '按 2026-11-18 的收盘价' in estimated['HKG:00021']


def test_a_holding_suspended_for_weeks_is_booked_at_its_last_close_with_its_date(monkeypatch):
    prices, _currencies, _waits, estimated, _look, _asked = priced(monkeypatch, ['HKG:00064'])
    assert prices == {'HKG:00064': 3.98}
    assert estimated['HKG:00064'].startswith('2026-11-19 没有新的成交价') and '2026-11-05 的收盘价' in estimated['HKG:00064']


def test_tencent_never_prices_a_delisted_holding_or_one_it_does_not_know(monkeypatch, capsys):
    prices, _currencies, not_final, _estimated, _look, _asked = priced(monkeypatch, ['SHA:600087', 'HKG:08001'])
    assert prices == {} and not_final == {}
    err = capsys.readouterr().err
    assert '腾讯行情 SHA:600087 (sh600087) 没有价格: 数据源标记该证券已退市' in err and 'HKG:08001' in err


def test_while_a_market_trades_its_close_is_waited_for_and_tencent_is_not_asked(monkeypatch):
    # 14:00 Shanghai: Yahoo's price is of a session still trading; the close is final at 15:00, at every source.
    now = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
    stamp = int(datetime(2026, 11, 19, 5, 59, tzinfo=UTC).timestamp())
    prices, _currencies, not_final, _estimated, look_again, asked = priced(monkeypatch, ['SHA:600519'], now=now, yahoo={
        '600519.SS': {'regularMarketPrice': 1455.0, 'currency': 'CNY', 'regularMarketTime': stamp}})
    assert prices == {} and not_final == {'SHA:600519': datetime(2026, 11, 19, 7, 0, tzinfo=UTC)} and look_again == {}
    assert [entry[0] for entry in asked] == ['yahoo']


def test_a_closing_print_yahoo_has_not_shown_yet_is_taken_from_tencent(monkeypatch):
    # 16:12 Hong Kong: the close is final, Yahoo (about 15 minutes late) still shows 15:55:12, so the snapshot would
    # wait for the print; Tencent has it, stamped 16:08:13.
    now = datetime(2026, 11, 19, 8, 12, tzinfo=UTC)
    stamp = int(datetime(2026, 11, 19, 7, 55, 12, tzinfo=UTC).timestamp())
    prices, _currencies, not_final, estimated, look_again, asked = priced(monkeypatch, ['HKG:00700'], now=now, yahoo={
        '0700.HK': {'regularMarketPrice': 349.0, 'currency': 'HKD', 'regularMarketTime': stamp}})
    assert prices == {'HKG:00700': 348.4} and (not_final, look_again, estimated) == ({}, {}, {})
    assert [entry[0] for entry in asked] == ['yahoo', 'tencent']


def test_a_wait_only_tencent_can_end_is_kept_when_tencent_cannot_end_it(monkeypatch):
    now = datetime(2026, 11, 19, 8, 12, tzinfo=UTC)
    stamp = int(datetime(2026, 11, 19, 7, 55, 12, tzinfo=UTC).timestamp())
    early = {'hk00700': fields('hk00700', f30='2026/11/19 15:55:12')}
    prices, _currencies, not_final, _estimated, look_again, _asked = priced(
        monkeypatch, ['HKG:00700'], now=now, rows=early,
        yahoo={'0700.HK': {'regularMarketPrice': 349.0, 'currency': 'HKD', 'regularMarketTime': stamp}})
    assert prices == {} and not_final == {'HKG:00700': datetime(2026, 11, 19, 8, 28, tzinfo=UTC)}
    assert look_again == {'HKG:00700': datetime(2026, 11, 19, 8, 15, tzinfo=UTC)}


def test_tencent_unreachable_leaves_the_holdings_without_a_price(monkeypatch, capsys):
    def get(url, **options):
        raise OSError('the network is switched off in this test')
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    monkeypatch.setattr(snapshot.requests, 'get', get)
    monkeypatch.setattr(snapshot, '_now', lambda: EVENING)
    not_final = {}
    prices, _ = snapshot.fetch_prices({'groups': {'E': {'positions': [position('SHA:600519')]}}}, {}, DAY, not_final)
    assert prices == {} and not_final == {}
    assert '获取腾讯行情失败' in capsys.readouterr().err


@pytest.mark.parametrize('choice, asked_sources', [
    ('tencent', ['tencent']), ('yahoo', ['yahoo']),
    ('eastmoney', ['yahoo']),           # no reading of Eastmoney's quotes in the snapshot: Yahoo alone, as before
    ('', ['yahoo', 'tencent']),
])
def test_tracker_quote_source_selects_the_snapshots_price_sources(monkeypatch, choice, asked_sources):
    asked = sources(monkeypatch)
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', choice)
    snapshot.fetch_prices({'groups': {'E': {'positions': [position('SHA:600519')]}}}, {}, DAY)
    assert [entry[0] for entry in asked] == asked_sources


def test_tencent_is_reached_directly_unless_tracker_tencent_proxy_says_otherwise(monkeypatch):
    *_rest, asked = priced(monkeypatch, ['SHA:600519'])
    assert asked[-1] == ('tencent', 'sh600519', {'http': None, 'https': None, 'all': None})
    monkeypatch.setenv('TRACKER_TENCENT_PROXY', '1')
    asked = sources(monkeypatch)
    snapshot.fetch_prices({'groups': {'E': {'positions': [position('SHA:600519')]}}}, {}, DAY)
    assert asked[-1] == ('tencent', 'sh600519', None), "the environment's proxy"


def test_the_snapshot_script_books_tencents_price_when_yahoo_has_none(tmp_path):
    """The real script on a loopback server: Yahoo does not know the holding, Tencent does."""
    with price_server({}, tencent={'sh600519': ROWS['sh600519'], 'hk00700': ROWS['hk00700']}) as base:
        folder = engine_portfolio(tmp_path / 'portfolio', base, day=DAY, cash=0, cost_basis=0,
                                  positions=[position('SHA:600519', quantity=10, cost_price=1400)])
        env = engine_env(folder)
        for name in ('TRACKER_QUOTE_SOURCE', 'TRACKER_TENCENT_PROXY'):
            env.pop(name, None)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio_snapshot.py'), '--date', DAY],
                                cwd=SCRIPTS, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert 'SHA:600519 价格取自腾讯行情（sh600519）' in result.stdout
    saved = json.loads((folder / 'snapshots' / f'{DAY}.json').read_text())
    assert saved['groups']['Example']['positions'][0]['current_price'] == 1460.21
    # The legacy holdings file is the only mark: Tencent's close is a verified one.
    assert saved['summary']['total_value'] == 14602.1
    assert saved['summary']['estimated_reasons'] == [snapshot.LEGACY_HOLDINGS]


# -- review cr_r41b: when a Tencent row stands for a close (items 3 and 4) and its time budget (item 7) ----------

FRIDAY_16_12_HK = datetime(2026, 11, 20, 8, 12, tzinfo=UTC)
YAHOO_WAITING = {'0700.HK': {'regularMarketPrice': 349.0, 'currency': 'HKD',      # Friday 15:55:12: before the print
                            'regularMarketTime': int(datetime(2026, 11, 20, 7, 55, 12, tzinfo=UTC).timestamp())}}
WAIT = ({'HKG:00700': datetime(2026, 11, 20, 8, 28, tzinfo=UTC)}, {'HKG:00700': datetime(2026, 11, 20, 8, 15, tzinfo=UTC)})


@pytest.mark.parametrize('rows', [
    ROWS,                                                   # Thursday's closing row, verbatim: Tencent did not update
    # Today's row showing nothing traded: the previous close (372.80) as its price, no day range, no volume.
    {'hk00700': fields('hk00700', f3='372.800', f5='0.000', f6='0.0', f33='0.000', f34='0.000',
                       f30='2026/11/20 16:08:13')},
])
def test_during_the_wait_for_the_closing_print_only_tencents_verified_close_ends_it(monkeypatch, rows):
    # Item 3: Yahoo shows Friday 15:55:12 at 16:12, so the snapshot waits for the print and looks again at 16:15.
    # Tencent's answer is not Friday's close: the wait stays exactly as without Tencent (not 100 x 372.80 booked).
    prices, _currencies, not_final, estimated, look_again, _asked = priced(
        monkeypatch, ['HKG:00700'], day='2026-11-20', now=FRIDAY_16_12_HK, yahoo=YAHOO_WAITING, rows=rows)
    assert (prices, estimated) == ({}, {}) and (not_final, look_again) == WAIT


def test_a_previous_sessions_row_is_carried_forward_only_when_tencent_marks_the_holding_suspended(monkeypatch, capsys):
    # Yahoo cannot be reached. Thursday's closing row of a share Tencent shows trading is not Friday's price.
    prices, _currencies, not_final, _estimated, _look, _asked = priced(
        monkeypatch, ['HKG:00700'], day='2026-11-20', now=FRIDAY_16_12_HK)
    assert (prices, not_final) == ({}, {})
    assert 'does not mark the holding suspended' in capsys.readouterr().err
    # Marked suspended ('S'), the same row carries Thursday's close forward, as an estimate.
    prices, _currencies, _waits, estimated, _look, _asked = priced(
        monkeypatch, ['HKG:00700'], day='2026-11-20', now=FRIDAY_16_12_HK,
        rows={'hk00700': fields('hk00700', f40='S')})
    assert prices == {'HKG:00700': 348.4} and '按 2026-11-19 的收盘价记入' in estimated['HKG:00700']


@pytest.mark.parametrize('day', ['2026-11-19', '2026-11-20'])
def test_a_mid_session_row_is_never_a_close_whatever_day_the_snapshot_runs(monkeypatch, capsys, day):
    # Item 4: run at 15:40 Shanghai on the 20th, Yahoo unreachable, Tencent still showing AAPL's 09:58:40 row of
    # the 19th (recorded verbatim). Today's rule would book it as the day's last price; a Tencent row never is.
    prices, _currencies, not_final, estimated, look_again, _asked = priced(
        monkeypatch, ['NASDAQ:AAPL'], day=day, now=datetime(2026, 11, 20, 7, 40, tzinfo=UTC))
    assert (prices, not_final, estimated, look_again) == ({}, {}, {}, {})
    assert 'not the closing row of the 2026-11-19 session' in capsys.readouterr().err


@pytest.mark.parametrize('ticker, rows, now', [
    # A Shanghai row stamped 10:15, read at 15:40 (Yahoo's would be booked at 15:20 as the day's last price).
    ('SHA:600519', {'sh600519': fields('sh600519', f30='20261119101500')}, datetime(2026, 11, 19, 7, 40, tzinfo=UTC)),
    # A Hong Kong row stamped before the closing auction's print, read at 16:40, after the print's usual delay.
    ('HKG:00700', {'hk00700': fields('hk00700', f30='2026/11/19 15:55:12')}, datetime(2026, 11, 19, 8, 40, tzinfo=UTC)),
])
def test_a_row_stamped_before_the_closing_print_is_not_booked_after_the_wait_either(monkeypatch, ticker, rows, now):
    prices, _currencies, not_final, estimated, _look, _asked = priced(monkeypatch, [ticker], now=now, rows=rows)
    assert (prices, not_final, estimated) == ({}, {}, {})


@pytest.mark.parametrize('stamp, booked', [
    ('2026-11-19 16:00:00', True), ('2026-11-19 16:00:59', True),
    ('2026-11-19 15:59:59', False),      # before the bell
    ('2026-11-19 16:01:00', False),      # possibly an after-hours trade: Tencent does not say
    ('2026-11-19 16:05:00', False),      # the review's synthetic 16:05 row
])
def test_a_new_york_row_is_the_close_only_when_stamped_in_the_bells_first_minute(monkeypatch, stamp, booked):
    # Tencent's New York close was not observed: these are the recorded AAPL row with its stamp moved.
    rows = {'usAAPL': fields('usAAPL', f3='354.00', f30=stamp)}
    prices, _currencies, not_final, estimated, _look, _asked = priced(
        monkeypatch, ['NASDAQ:AAPL'], now=datetime(2026, 11, 20, 7, 40, tzinfo=UTC), rows=rows)
    assert prices == ({'NASDAQ:AAPL': 354.0} if booked else {}) and (not_final, estimated) == ({}, {})


def test_closing_rows_of_each_market_are_booked_as_verified_closes(monkeypatch):
    # The recorded Shanghai (16:14:55) and Hong Kong (16:08:13) rows, and New York's at the bell (stamp moved).
    rows = {**ROWS, 'usAAPL': fields('usAAPL', f30='2026-11-19 16:00:00')}
    prices, _currencies, not_final, estimated, _look, _asked = priced(
        monkeypatch, ['SHA:600519', 'HKG:00700', 'NASDAQ:AAPL'], now=datetime(2026, 11, 20, 0, 0, tzinfo=UTC),
        rows=rows)
    assert prices == {'SHA:600519': 1460.21, 'HKG:00700': 348.4, 'NASDAQ:AAPL': 349.71}
    assert (not_final, estimated) == ({}, {})


def test_the_tencent_budget_matches_the_pipelines_limit_for_the_snapshot():
    import portfolio_daily_update
    assert snapshot.CHILD_TIMEOUT == portfolio_daily_update.SNAPSHOT_TIMEOUT


def test_with_a_slow_yahoo_tencent_is_not_asked_when_the_run_has_too_little_time_left(monkeypatch, capsys):
    # Item 7: seven Hong Kong holdings, each Yahoo request taking 14 s and showing a price before the print. After
    # 98 s the rates (up to 30 s) still have to fit in the pipeline's 120 s: Tencent is skipped, the waits stand.
    elapsed = [0.0]
    monkeypatch.setattr(snapshot, '_run_started', 0.0)
    monkeypatch.setattr(snapshot, '_monotonic', lambda: elapsed[0])
    tickers = [f'HKG:{code:05d}' for code in (1, 2, 3, 5, 6, 11, 700)]
    stamp = int(datetime(2026, 11, 19, 7, 55, 12, tzinfo=UTC).timestamp())
    asked = sources(monkeypatch, now=datetime(2026, 11, 19, 8, 12, tzinfo=UTC))
    get = snapshot.requests.get     # the stub: Tencent's recorded rows, each request recorded in ``asked``

    def slow(url, **options):
        if url != snapshot.TENCENT_URL:
            elapsed[0] += 14
            return Chart({'regularMarketPrice': 10.0, 'currency': 'HKD', 'regularMarketTime': stamp})
        return get(url, **options)
    monkeypatch.setattr(snapshot.requests, 'get', slow)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    not_final, look_again = {}, {}
    holdings = {'groups': {'E': {'positions': [position(ticker) for ticker in tickers]}}}
    prices, _ = snapshot.fetch_prices(holdings, {}, DAY, not_final, {}, look_again)
    assert elapsed[0] == 98 and prices == {} and set(not_final) == set(look_again) == set(tickers)
    assert asked == [], 'Tencent was not asked'
    assert '未查询腾讯行情: 快照已运行 98 秒' in capsys.readouterr().err


@pytest.mark.parametrize('spent, timeout', [(0, (2.0, 4.0)), (86, (2.0, 4.0)), (87, (2.0, 3.0)), (88.5, None)])
def test_the_tencent_request_gets_what_is_left_of_the_run(monkeypatch, spent, timeout):
    monkeypatch.setattr(snapshot, '_run_started', 100.0)
    monkeypatch.setattr(snapshot, '_monotonic', lambda: 100.0 + spent)
    assert snapshot._tencent_timeout() == timeout
    monkeypatch.setattr(snapshot, '_run_started', None)        # imported, not run: no pipeline limit to keep
    assert snapshot._tencent_timeout() == snapshot.TENCENT_TIMEOUT


# -- each price's source in the snapshot and the report's 数据来源 (T4) -------------------------------------------

def test_the_snapshot_records_which_source_priced_each_holding(monkeypatch):
    close = int(datetime(2026, 11, 19, 7, 0, 2, tzinfo=UTC).timestamp())
    recorded = {}
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    sources(monkeypatch, yahoo={'600001.SS': {'regularMarketPrice': 12.5, 'currency': 'CNY', 'regularMarketTime': close}})
    holdings = {'groups': {'E': {'positions': [position('SHA:600001'), position('SHA:600519')]}}}
    prices, _ = snapshot.fetch_prices(holdings, {}, DAY, {}, {}, {}, recorded)
    assert prices == {'SHA:600001': 12.5, 'SHA:600519': 1460.21}
    assert recorded == {'SHA:600001': 'yahoo', 'SHA:600519': 'tencent'}


def report_footer(snapshot_data):
    return report.generate_report(snapshot_data).splitlines()[-1]


def one_day(*sources):
    """A saved snapshot of one account holding a share per source (``None``: no ``price_source``, as before)."""
    tickers = [f'SHA:60000{index + 1}' for index in range(len(sources))]
    holdings = {'date': DAY, 'groups': {'E': {'cash': 0, 'fund': 0, 'cost_basis': 100, 'positions': [
        position(ticker, quantity=1, cost_price=10) for ticker in tickers]}}}
    recorded = {ticker: source for ticker, source in zip(tickers, sources) if source is not None}
    saved = snapshot.calculate_snapshot(holdings, {ticker: 10.0 for ticker in tickers}, {}, {'CNY': 1.0}, None, [],
                                        priced_by=recorded)
    return {**saved, 'generated_at': '2026-11-19T15:41:00.123456'}


def test_a_day_priced_from_yahoo_alone_gives_the_report_it_always_had():
    footer = '*数据来源: Yahoo Finance | 生成时间: 2026-11-19T15:41:00*'
    assert report_footer(one_day('yahoo', 'yahoo')) == footer
    assert report_footer(one_day(None, None)) == footer             # a snapshot made before sources were recorded
    assert report.generate_report(one_day('yahoo', 'yahoo')) == report.generate_report(one_day(None, None))
    assert report_footer({**one_day(), 'groups': {}}) == footer     # no positions: as every report said before


def test_a_day_priced_partly_from_tencent_names_both_sources():
    assert report_footer(one_day('yahoo', 'tencent')) == '*数据来源: Yahoo Finance、腾讯 | 生成时间: 2026-11-19T15:41:00*'
    assert report_footer(one_day('tencent', None)) == '*数据来源: Yahoo Finance、腾讯 | 生成时间: 2026-11-19T15:41:00*'
    assert report_footer(one_day('tencent')) == '*数据来源: 腾讯 | 生成时间: 2026-11-19T15:41:00*'
    saved = one_day('yahoo', 'tencent')
    assert [position['price_source'] for position in saved['groups']['E']['positions']] == ['yahoo', 'tencent']


def test_the_snapshot_script_and_its_report_name_the_sources_of_a_day_priced_partly_from_tencent(tmp_path):
    """The real script: Yahoo has Shanghai's close of the 19th for one holding, Tencent the other."""
    close = int(datetime(2026, 11, 19, 7, 0, 2, tzinfo=UTC).timestamp())
    with price_server({'SHA:600001': 12.5}, times={'SHA:600001': close},
                      tencent={'sh600519': ROWS['sh600519']}) as base:
        folder = engine_portfolio(tmp_path / 'portfolio', base, day=DAY, cash=0, cost_basis=0,
                                  positions=[position('SHA:600001', quantity=100, cost_price=10),
                                             position('SHA:600519', quantity=10, cost_price=1400)])
        env = engine_env(folder)
        for name in ('TRACKER_QUOTE_SOURCE', 'TRACKER_TENCENT_PROXY'):
            env.pop(name, None)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio_snapshot.py'), '--date', DAY],
                                cwd=SCRIPTS, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    saved = json.loads((folder / 'snapshots' / f'{DAY}.json').read_text())
    assert {position['ticker']: position['price_source'] for position in saved['groups']['Example']['positions']} == {
        'SHA:600001': 'yahoo', 'SHA:600519': 'tencent'}
    assert saved['summary']['total_value'] == 15852.1
    assert report_footer(saved).startswith('*数据来源: Yahoo Finance、腾讯 | 生成时间: ')
