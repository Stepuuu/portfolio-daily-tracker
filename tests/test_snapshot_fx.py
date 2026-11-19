"""The snapshot judges its exchange rates as Today does (``core.market.quotes.derive_fx_status``).

A rate the source gives in CNY is used. One that is not current (``stale``: over an hour old while the
currency market trades) or has no time (``time_unknown``) is still used, but everything it converts is marked
as an estimate, as Today marks it 估算. The reviewer's case: yesterday's USD/CNY, or a rate without a
timestamp, gave an unqualified snapshot. Stubbed fetches and a loopback price server; fictional rates.
"""
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys

import pytest

import portfolio_snapshot as snapshot
from tests.engine_fakes import SCRIPTS, engine_env, engine_portfolio, position, price_server

UTC = timezone.utc
NOW = datetime(2026, 11, 17, 7, 40, tzinfo=UTC)       # Tuesday 15:40 Shanghai: the currency market trades


class Response:
    def __init__(self, meta):
        self.meta = meta

    def raise_for_status(self):
        pass

    def json(self):
        return {'chart': {'result': [{'meta': self.meta}]}}


def rates_at(monkeypatch, meta, now=NOW):
    """``(rates, estimated)`` for USD/CNY answered with ``meta`` at ``now``."""
    monkeypatch.setattr(snapshot.requests, 'get', lambda url, **options: Response(meta))
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    estimated = {}
    return snapshot.fetch_fx_rates({'fx_tickers': {'USD_CNY': 'USDCNY=X'}}, estimated), estimated


def usd(rate=7.1234, stamp=None, currency='CNY'):
    meta = {'regularMarketPrice': rate, 'currency': currency}
    if stamp is not None:
        meta['regularMarketTime'] = int(stamp.timestamp())
    return meta


def test_a_current_rate_is_used_unmarked(monkeypatch):
    assert rates_at(monkeypatch, usd(stamp=NOW - timedelta(seconds=10))) == ({'CNY': 1.0, 'USD': 7.1234}, {})


@pytest.mark.parametrize('meta, words', [
    (usd(stamp=NOW - timedelta(days=1)), '未更新'),          # yesterday's rate
    (usd(), '汇率时间'),                                      # no timestamp
])
def test_a_rate_that_is_not_current_is_used_and_marked(monkeypatch, meta, words):
    rates, estimated = rates_at(monkeypatch, meta)
    assert rates == {'CNY': 1.0, 'USD': 7.1234}
    assert set(estimated) == {'USD'} and words in estimated['USD']


def test_the_weekend_rate_is_the_markets_last_and_unmarked(monkeypatch):
    saturday = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)
    friday_close = datetime(2026, 11, 20, 21, 59, tzinfo=UTC)
    assert rates_at(monkeypatch, usd(stamp=friday_close), now=saturday) == ({'CNY': 1.0, 'USD': 7.1234}, {})


def test_a_rate_not_in_yuan_is_not_used(monkeypatch):
    rates, estimated = rates_at(monkeypatch, usd(stamp=NOW, currency='USD'))
    assert rates == {'CNY': 1.0} and estimated == {}


def holdings(cash=None, positions=()):
    balances = {'CNY': 0, **(cash or {})}
    return {'date': '2026-11-17', 'groups': {'Example': {
        'cash': balances['CNY'], 'cash_balances': balances, 'fund': 0, 'cost_basis': 1000,
        'positions': list(positions)}}}


def test_a_marked_rate_marks_what_it_converts():
    reason = '汇率已超过 1440 分钟未更新'
    saved = snapshot.calculate_snapshot(holdings(positions=[position('NASDAQ:EXMP', 1, 75)]), {'NASDAQ:EXMP': 75.0},
                                        {}, {'CNY': 1.0, 'USD': 7.1234}, None, [], rate_estimates={'USD': reason})
    row = saved['groups']['Example']['positions'][0]
    assert row['estimated_reason'] == f'汇率 USD/CNY：{reason}'
    assert saved['summary']['estimated'] is True
    assert saved['summary']['estimated_reasons'] == [f'汇率 USD/CNY：{reason}']
    cash = snapshot.calculate_snapshot(holdings(cash={'USD': 75}), {}, {}, {'CNY': 1.0, 'USD': 7.1234}, None, [],
                                       rate_estimates={'USD': reason})
    assert cash['summary']['estimated_reasons'] == [f'汇率 USD/CNY：{reason}']


def test_a_marked_rate_that_converts_nothing_marks_nothing():
    saved = snapshot.calculate_snapshot(holdings(cash={'CNY': 500}), {}, {}, {'CNY': 1.0, 'USD': 7.1234}, None, [],
                                        rate_estimates={'USD': '数据源未提供汇率时间'})
    assert (saved['summary']['estimated'], saved['summary']['estimated_reasons']) == (False, [])


US_TICKER = 'NASDAQ:EXMP'
US_CLOSE_TUESDAY = 1794949200      # 16:00 New York, Tuesday 2026-11-17


def test_the_snapshot_script_marks_a_rate_without_a_timestamp(tmp_path):
    with price_server({US_TICKER: 10.0, 'FX:USDCNY=X': 7.2},
                      times={US_TICKER: US_CLOSE_TUESDAY, 'FX:USDCNY=X': None}) as base:
        folder = engine_portfolio(tmp_path / 'portfolio', base, day='2026-11-17', cash=0, cost_basis=0,
                                  positions=[position(US_TICKER, quantity=1, cost_price=10)])
        config = json.loads((folder / 'config.json').read_text())
        (folder / 'config.json').write_text(json.dumps({**config, 'fx_tickers': {'USD_CNY': 'USDCNY=X'}}))
        result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio_snapshot.py'), '--date', '2026-11-17'],
                                cwd=SCRIPTS, env=engine_env(folder), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    saved = json.loads((folder / 'snapshots' / '2026-11-17.json').read_text())
    assert saved['summary']['total_value'] == 72 and saved['summary']['estimated'] is True
    assert any(reason.startswith('汇率 USD/CNY：') for reason in saved['summary']['estimated_reasons'])


# -- a rate that is not current yields to the next source, as in Today ------------------------------------------
# Today asks Yahoo first and Eastmoney next (``core.market.runtime``); a stale or time-unknown rate yields to the
# next source, and only when no source has a verified rate is the latest one used, marked. The reviewer's case
# (round 5): at 16:12 Yahoo's USD/CNY 7.00 was stale and Eastmoney's 7.01 current; Today showed ¥372,920.50
# complete, the snapshot saved ¥372,675.50 marked 含估算. At 40868ac both took Yahoo's 7.00 and agreed. The
# choice of source itself is pinned against Today's quote service in tests/test_snapshot_today_vectors.py.
AT_16_12 = datetime(2026, 11, 17, 8, 12, tzinfo=UTC)
USD_EXPOSURE = {'CNY': '201175.50', 'USD': '24500'}


def two_sources(monkeypatch, yahoo, eastmoney, now=AT_16_12, choice=None):
    """``(rates, estimated, requests)`` for USD/CNY: Yahoo answers the ``yahoo`` meta, Eastmoney the
    ``eastmoney`` row (None: it has no such rate); ``choice`` is ``TRACKER_QUOTE_SOURCE`` (None: unset)."""
    requests_made = []

    class Answer:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def get(url, **options):
        requests_made.append((url, options))
        if 'eastmoney' in url:
            return Answer({'rc': 0, 'data': {'diff': [eastmoney]}} if eastmoney else {'rc': 102, 'data': None})
        return Answer({'chart': {'result': [{'meta': yahoo}]}})
    monkeypatch.setattr(snapshot.requests, 'get', get)
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    if choice is None:
        monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)     # both sources, Today's default
    else:
        monkeypatch.setenv('TRACKER_QUOTE_SOURCE', choice)
    estimated = {}
    return snapshot.fetch_fx_rates({'fx_tickers': {'USD_CNY': 'USDCNY=X'}}, estimated), estimated, requests_made


def cnh(rate, stamp):
    return {'f12': 'USDCNH', 'f13': 133, 'f2': rate, 'f124': int(stamp.timestamp())}


def totals(tmp_path, rates, estimated, todays_rate):
    """(the snapshot's total and mark, Today's total) of an account holding ``USD_EXPOSURE``."""
    from decimal import Decimal
    from core.market.valuation import value_portfolio
    from tests.fakes import build_ledger, opening
    ledger = build_ledger(tmp_path, [opening('Example', cash=USD_EXPOSURE, contributed_cny='350000',
                                             date='2026-11-16')])
    saved = snapshot.calculate_snapshot(ledger.holdings('2026-11-17'), {}, {}, rates, None, [],
                                        rate_estimates=estimated)
    today = value_portfolio(ledger.state(), {}, {'USD': Decimal(todays_rate)}, None, [])
    return (saved['summary']['total_value'], saved['summary']['estimated']), float(today.total_cny)


def test_with_a_current_rate_from_the_next_source_the_history_equals_todays_total(tmp_path, monkeypatch):
    rates, estimated, made = two_sources(monkeypatch, usd(7.0, AT_16_12 - timedelta(minutes=72)),
                                         cnh(7.01, AT_16_12 - timedelta(seconds=10)))
    assert (rates, estimated) == ({'CNY': 1.0, 'USD': 7.01}, {})
    assert totals(tmp_path, rates, estimated, '7.01') == ((372920.5, False), 372920.5)
    assert ['eastmoney' in url for url, _options in made] == [False, True]


def test_with_no_current_rate_anywhere_the_latest_is_used_and_marked(tmp_path, monkeypatch):
    """Both ways: Eastmoney's rate is older still, so Yahoo's 7.00 is the latest; Today marks it too."""
    rates, estimated, _made = two_sources(monkeypatch, usd(7.0, AT_16_12 - timedelta(minutes=72)),
                                          cnh(7.01, AT_16_12 - timedelta(minutes=130)))
    assert rates == {'CNY': 1.0, 'USD': 7.0} and '未更新' in estimated['USD']
    assert totals(tmp_path, rates, estimated, '7.00') == ((372675.5, True), 372675.5)


def test_a_current_yahoo_rate_is_used_without_asking_eastmoney(monkeypatch):
    rates, estimated, made = two_sources(monkeypatch, usd(7.0, AT_16_12 - timedelta(seconds=10)),
                                         cnh(7.01, AT_16_12 - timedelta(seconds=10)))
    assert (rates, estimated, len(made)) == ({'CNY': 1.0, 'USD': 7.0}, {}, 1)


def test_eastmoney_is_reached_as_today_reaches_it(monkeypatch):
    """Directly, unless ``TRACKER_EASTMONEY_PROXY=1`` (``core.market.runtime``); Yahoo as before."""
    monkeypatch.delenv('TRACKER_EASTMONEY_PROXY', raising=False)
    _rates, _estimated, made = two_sources(monkeypatch, usd(7.0, None), None)
    (yahoo_url, yahoo_options), (url, options) = made
    assert yahoo_url.endswith('/USDCNY=X') and yahoo_options['proxies'] is None
    assert url == 'https://push2.eastmoney.com/api/qt/ulist.np/get' and options['params']['secids'] == '133.USDCNH'
    assert options['proxies'] == {'http': None, 'https': None, 'all': None}, 'no proxy from the environment'
    monkeypatch.setenv('TRACKER_EASTMONEY_PROXY', '1')
    _rates, _estimated, made = two_sources(monkeypatch, usd(7.0, None), None)
    assert made[1][1]['proxies'] is None, "the environment's proxy"


def test_the_snapshot_script_takes_a_current_rate_from_the_next_source(tmp_path):
    """The real script, at the real time: Yahoo's rate three days old, Eastmoney's current."""
    now = datetime.now(UTC)
    with price_server({US_TICKER: 10.0, 'FX:USDCNY=X': 7.0},
                      times={US_TICKER: US_CLOSE_TUESDAY, 'FX:USDCNY=X': int((now - timedelta(days=3)).timestamp())},
                      eastmoney={'133.USDCNH': (7.01, int(now.timestamp()) - 10)}) as base:
        folder = engine_portfolio(tmp_path / 'portfolio', base, day='2026-11-17', cash=0, cost_basis=0,
                                  positions=[position(US_TICKER, quantity=1, cost_price=10)])
        config = json.loads((folder / 'config.json').read_text())
        (folder / 'config.json').write_text(json.dumps({**config, 'fx_tickers': {'USD_CNY': 'USDCNY=X'}}))
        result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio_snapshot.py'), '--date', '2026-11-17'],
                                cwd=SCRIPTS, env=engine_env(folder), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    saved = json.loads((folder / 'snapshots' / '2026-11-17.json').read_text())
    assert saved['summary']['total_value'] == 70.1
    assert not any(reason.startswith('汇率') for reason in saved['summary']['estimated_reasons'])


# -- round 6: the proxy, the selected source, and no second source when nothing needs it -------------------------
DEAD_PROXY = 'http://127.0.0.1:9'          # nothing listens there: a request sent through it fails
PROXY_VARIABLES = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')


def script_behind_a_proxy(tmp_path, **environment):
    """The real script at the real time, one US share: Yahoo, reached through ``proxy`` in config.json (the
    loopback server answers as a proxy too), has a rate three days old; Eastmoney, reachable only directly,
    a current one. ``environment`` sets proxy variables. Returns the saved snapshot's summary."""
    now = datetime.now(UTC)
    with price_server({US_TICKER: 10.0, 'FX:USDCNY=X': 7.0},
                      times={US_TICKER: US_CLOSE_TUESDAY, 'FX:USDCNY=X': int((now - timedelta(days=3)).timestamp())},
                      eastmoney={'133.USDCNH': (7.01, int(now.timestamp()) - 10)}) as base:
        folder = engine_portfolio(tmp_path / 'portfolio', base, day='2026-11-17', cash=0, cost_basis=0,
                                  positions=[position(US_TICKER, quantity=1, cost_price=10)])
        config = json.loads((folder / 'config.json').read_text())
        (folder / 'config.json').write_text(json.dumps({**config, 'fx_tickers': {'USD_CNY': 'USDCNY=X'},
                                                        'proxy': base.rsplit('/chart', 1)[0]}))
        env = engine_env(folder)
        for name in (*PROXY_VARIABLES, 'NO_PROXY', 'no_proxy', 'TRACKER_EASTMONEY_PROXY', 'TRACKER_QUOTE_SOURCE'):
            env.pop(name, None)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio_snapshot.py'), '--date', '2026-11-17'],
                                cwd=SCRIPTS, env={**env, **environment}, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    summary = json.loads((folder / 'snapshots' / '2026-11-17.json').read_text())['summary']
    return summary['total_value'], any(reason.startswith('汇率') for reason in summary['estimated_reasons'])


@pytest.mark.parametrize('variable', ['ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy'])
def test_eastmoney_is_reached_directly_whatever_proxy_the_environment_names(tmp_path, variable):
    """cr_v6b: with ALL_PROXY set, Today reached Eastmoney directly and took 7.01; the snapshot sent it through
    the proxy and took Yahoo's stale 7.00 (at 40868ac the two agreed). Without ``TRACKER_EASTMONEY_PROXY=1``
    no proxy variable applies to Eastmoney, as for Today (``open_client(use_env_proxy=False)``)."""
    assert script_behind_a_proxy(tmp_path, **{variable: DEAD_PROXY}) == (70.1, False)


def test_with_tracker_eastmoney_proxy_eastmoney_goes_through_the_environments_proxy(tmp_path):
    """Both ways: asked to, Eastmoney is reached through the environment's proxy, here one that fails, so Yahoo's
    stale rate is the latest there is: used and marked, as Today does then."""
    assert script_behind_a_proxy(tmp_path, ALL_PROXY=DEAD_PROXY, TRACKER_EASTMONEY_PROXY='1') == (70.0, True)


def test_with_yahoo_selected_the_snapshot_takes_yahoos_rate_as_today_does(tmp_path, monkeypatch):
    """cr_v6b N2 / cr_v6bs B1: with ``TRACKER_QUOTE_SOURCE=yahoo`` Today asks Yahoo alone and shows ¥372,675.50
    estimated; so did the snapshot at 40868ac. Eastmoney is not asked. (Every source choice is pinned against
    Today's runtime in tests/test_snapshot_today_vectors.py.)"""
    stale, current = usd(7.0, AT_16_12 - timedelta(minutes=72)), cnh(7.01, AT_16_12 - timedelta(seconds=10))
    rates, estimated, made = two_sources(monkeypatch, stale, current, choice='yahoo')
    assert rates == {'CNY': 1.0, 'USD': 7.0} and '未更新' in estimated['USD']
    assert ['eastmoney' in url for url, _options in made] == [False]
    assert totals(tmp_path, rates, estimated, '7.00') == ((372675.5, True), 372675.5)


def fetched(monkeypatch, holdings, now, charts, rows):
    """``(rates, estimated, Eastmoney requests)`` of ``_fetch_quotes`` for ``holdings`` at ``now``, the default
    sources and rate symbols: Yahoo answers ``charts`` (symbol -> meta; any other symbol fails), Eastmoney
    ``rows`` (secid -> row)."""
    asked = []

    class Answer:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def get(url, **options):
        if 'eastmoney' in url:
            asked.append(options['params']['secids'])
            found = [rows[secid] for secid in options['params']['secids'].split(',') if secid in rows]
            return Answer({'rc': 0, 'data': {'diff': found}} if found else {'rc': 102, 'data': None})
        symbol = url.rsplit('/', 1)[-1]
        if symbol not in charts:
            raise snapshot.requests.HTTPError('404 in this test')
        return Answer({'chart': {'result': [{'meta': charts[symbol]}]}})
    monkeypatch.setattr(snapshot.requests, 'get', get)
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    quotes = snapshot._fetch_quotes(holdings, {'fx_tickers': {'HKD_CNY': 'HKDCNY=X', 'USD_CNY': 'USDCNY=X'}},
                                    '2026-11-17')
    return quotes[2], quotes[5], asked


def held(ticker, cash):
    return {'date': '2026-11-17', 'groups': {'E': {'cash': cash['CNY'], 'cash_balances': cash, 'fund': 0,
                                                   'cost_basis': 1000, 'positions': [position(ticker)]}}}


def stale_rates(now):
    """Yahoo's USD/CNY and HKD/CNY, both three days old."""
    old = int((now - timedelta(days=3)).timestamp())
    return {'USDCNY=X': {'regularMarketPrice': 7.0, 'currency': 'CNY', 'regularMarketTime': old},
            'HKDCNY=X': {'regularMarketPrice': 0.9, 'currency': 'CNY', 'regularMarketTime': old}}


CURRENT_CNH = {'133.USDCNH': {'f12': 'USDCNH', 'f13': 133, 'f2': 7.01, 'f124': int(NOW.timestamp()) - 10},
               '133.HKDCNH': {'f12': 'HKDCNH', 'f13': 133, 'f2': 0.91, 'f124': int(NOW.timestamp()) - 10}}


def test_a_rate_the_holdings_do_not_convert_asks_no_second_source(monkeypatch):
    """cr_v6b N3 / cr_v6bs B2 (a): a mainland-only snapshot with a stale USD/CNY it does not use waited for a slow
    Eastmoney (0.62 s at 40868ac, which asked no second source; 3.85 s in round 5). Yahoo's rates are fetched
    as before; the stale USD rate converts nothing, so nothing is marked."""
    a_share = {'600001.SS': {'regularMarketPrice': 10.5, 'currency': 'CNY',
                             'regularMarketTime': int(datetime(2026, 11, 17, 7, 0, tzinfo=UTC).timestamp())}}
    rates, _estimated, asked = fetched(monkeypatch, held('SHA:600001', {'CNY': 1000}), NOW,
                                       {**a_share, **stale_rates(NOW)}, CURRENT_CNH)
    assert asked == [] and rates['USD'] == 7.0
    # Both ways: dollars in the account need the dollar rate; Eastmoney is asked for it, and for it alone.
    rates, _estimated, asked = fetched(monkeypatch, held('SHA:600001', {'CNY': 1000, 'USD': 100}), NOW,
                                       {**a_share, **stale_rates(NOW)}, CURRENT_CNH)
    assert asked == ['133.USDCNH'] and rates['USD'] == 7.01


def test_a_wait_already_known_asks_no_second_source(monkeypatch):
    """cr_v6b N3 / cr_v6bs B2 (b): at 15:40 a Hong Kong-only snapshot waits for the 16:10 close; it reported that
    after an unrelated fallback request (4.05 s at 40868ac, 8.27 s in round 5). No second source is asked."""
    hong_kong = {'0003.HK': {'regularMarketPrice': 10.5, 'currency': 'HKD',
                             'regularMarketTime': int(datetime(2026, 11, 17, 7, 39, tzinfo=UTC).timestamp())}}
    rates, _estimated, asked = fetched(monkeypatch, held('HKG:00003', {'CNY': 0}), NOW,
                                       {**hong_kong, **stale_rates(NOW)}, CURRENT_CNH)
    assert asked == []
    # Both ways: at 16:30 the close is booked and its stale HKD/CNY yields to Eastmoney's, asked for HKD alone.
    at_16_30 = datetime(2026, 11, 17, 8, 30, tzinfo=UTC)
    closed = {'0003.HK': {**hong_kong['0003.HK'],
                          'regularMarketTime': int(datetime(2026, 11, 17, 8, 8, 2, tzinfo=UTC).timestamp())}}
    current = {secid: {**row, 'f124': int(at_16_30.timestamp()) - 10} for secid, row in CURRENT_CNH.items()}
    rates, _estimated, asked = fetched(monkeypatch, held('HKG:00003', {'CNY': 0}), at_16_30,
                                       {**closed, **stale_rates(at_16_30)}, current)
    assert asked == ['133.HKDCNH'] and rates['HKD'] == 0.91
