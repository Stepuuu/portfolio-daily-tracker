"""Price history and indicators state the provider, adjustment, volume unit and
session state that are TRUE for the instrument, with only the upstream stubbed.

The real ``DataLoader`` runs; ``akshare`` and ``yfinance`` are fake modules.
"""
import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core.agent_tools import market_tools
from core.market.bars import BarError, LoaderBarSource, set_bar_source

SHANGHAI = timezone(timedelta(hours=8))


def run(coroutine):
    return asyncio.run(coroutine)


def at(day, hour, minute=0):
    return lambda: datetime(2026, 11, day, hour, minute, tzinfo=SHANGHAI)


def em_frame(last='2026-11-17', rows=300, volume=1000):
    days = pd.bdate_range(end=last, periods=rows)
    return pd.DataFrame({
        '日期': [d.strftime('%Y-%m-%d') for d in days], '开盘': 10.0, '最高': 10.5, '最低': 9.5, '收盘': 10.2,
        '成交量': float(volume), '成交额': 1.0e7, '涨跌幅': 0.1, '涨跌额': 0.01, '换手率': 0.5})


def yahoo_frame(last='2026-11-17', rows=300, volume=1000):
    days = pd.bdate_range(end=last, periods=rows)
    return pd.DataFrame({'Open': 10.0, 'High': 10.5, 'Low': 9.5, 'Close': 10.2, 'Volume': float(volume)},
                        index=pd.DatetimeIndex(days, name='Date'))


@pytest.fixture
def upstream(monkeypatch):
    """Fake akshare and yfinance; ``calls`` records which function got which arguments."""
    state = types.SimpleNamespace(calls=[], last='2026-11-17', volume=1000)

    def em(name):
        def function(**kwargs):
            state.calls.append((name, kwargs))
            return em_frame(state.last, volume=state.volume)
        return function

    akshare = types.ModuleType('akshare')
    for name in ('stock_zh_a_hist', 'fund_etf_hist_em', 'stock_hk_hist'):
        setattr(akshare, name, em(name))
    yfinance = types.ModuleType('yfinance')

    def download(symbol, **kwargs):
        state.calls.append(('yfinance.download', dict(kwargs, symbol=symbol)))
        return yahoo_frame(state.last, volume=state.volume)

    yfinance.download = download
    monkeypatch.setitem(sys.modules, 'akshare', akshare)
    monkeypatch.setitem(sys.modules, 'yfinance', yfinance)
    yield state
    set_bar_source(None)


def history(ticker, now, days=10):
    set_bar_source(LoaderBarSource(now=now))
    return run(market_tools.get_price_history(ticker, days))


def test_hong_kong_stock_during_hong_kong_trading_hours(upstream):
    """The reviewer's case: HKG:700 at 15:30 Shanghai, bar of today, exchange still open."""
    upstream.volume = 5_000_000
    out = history('HKG:700', at(17, 15, 30))
    assert 'error' not in out, out
    assert 'stock_hk_hist' in out['source'] and 'yfinance' not in out['source']
    assert out['provider'] == 'akshare.stock_hk_hist'
    assert out['adjust'] == 'qfq' and out['currency'] == 'HKD' and out['market'] == 'HK'
    assert out['data_date'] == '2026-11-17' and out['last_bar_provisional'] is True
    name, kwargs = upstream.calls[-1]
    assert name == 'stock_hk_hist' and kwargs['symbol'] == '00700' and kwargs['adjust'] == 'qfq'
    assert out['rows'][-1][5] == 5_000_000.0 and out['units']['volume'] == '股'
    # Changed with the HK/US tables: Hong Kong's 2026 calendar is known, so there is no weekday-only note.
    assert not any('工作日' in note for note in out['notes'])
    assert out['calendar'] == 'calendar'


def test_hong_kong_session_states(upstream):
    assert history('HKG:700', at(17, 12, 30))['last_bar_provisional'] is True       # lunch break: bar incomplete
    assert history('HKG:700', at(17, 16, 30))['last_bar_provisional'] is False      # closed since 16:00
    upstream.last = '2026-11-16'
    assert history('HKG:700', at(17, 15, 30))['last_bar_provisional'] is False      # last bar is not today's


def test_a_hong_kong_bar_stays_provisional_until_the_closing_auction_has_ended(upstream):
    # HKEX: continuous trading ends at 16:00, the closing auction session closes
    # at random between 16:08 and 16:10. Codex re-review: 16:06 was reported final.
    assert history('HKG:700', at(17, 16, 6))['last_bar_provisional'] is True
    assert history('HKG:700', at(17, 16, 9))['last_bar_provisional'] is True
    assert history('HKG:700', at(17, 16, 12))['last_bar_provisional'] is False


def test_mainland_and_new_york_bars_are_final_at_their_closing_auctions(upstream):
    # The mainland closing call auction runs 14:57-15:00; New York's closing auctions run at 16:00.
    # Changed with the 2026-11-18 observations: New York's official closes reach the providers minutes after
    # the bell, so its bar is final when its close has settled, at 16:30 (calendar.SessionClock.close_settled).
    assert history('SHE:000001', at(17, 14, 59))['last_bar_provisional'] is True
    assert history('SHE:000001', at(17, 15, 0))['last_bar_provisional'] is False
    upstream.last = '2026-11-16'
    assert history('NASDAQ:AAPL', at(17, 4, 59))['last_bar_provisional'] is True     # 15:59 in New York
    assert history('NASDAQ:AAPL', at(17, 5, 0))['last_bar_provisional'] is True      # 16:00: not settled yet
    assert history('NASDAQ:AAPL', at(17, 5, 29))['last_bar_provisional'] is True
    assert history('NASDAQ:AAPL', at(17, 5, 30))['last_bar_provisional'] is False    # 16:30 in New York


def test_hong_kong_is_not_judged_by_the_mainland_close(upstream):
    # 15:30 Shanghai: the mainland has closed (15:00) but Hong Kong has not (16:00)
    assert history('HKG:700', at(17, 15, 30))['last_bar_provisional'] is True
    assert history('SHE:000001', at(17, 15, 30))['last_bar_provisional'] is False


def test_a_year_without_a_table_is_weekday_only_and_says_so(upstream):
    upstream.last = '2028-11-14'
    out = history('HKG:700', lambda: datetime(2028, 11, 14, 15, 30, tzinfo=SHANGHAI))
    assert out['calendar'] == 'weekday_only' and any('工作日' in note for note in out['notes'])
    assert out['last_bar_provisional'] is True


def test_an_early_close_day_bar_is_final_after_the_early_close(upstream):
    # Hong Kong's Christmas Eve half day ends at 12:00, not 16:00.
    upstream.last = '2026-12-24'
    half_day = lambda hour, minute: (lambda: datetime(2026, 12, 24, hour, minute, tzinfo=SHANGHAI))
    assert history('HKG:700', half_day(11, 0))['last_bar_provisional'] is True
    assert history('HKG:700', half_day(12, 1))['last_bar_provisional'] is True        # closing auction session
    assert history('HKG:700', half_day(12, 9))['last_bar_provisional'] is True        # random close 12:08-12:10
    assert history('HKG:700', half_day(12, 11))['last_bar_provisional'] is False
    assert history('HKG:700', half_day(12, 30))['last_bar_provisional'] is False
    # New York closes at 13:00 on the day after Thanksgiving: 02:00 Saturday in Shanghai.
    upstream.last = '2026-11-27'
    friday = history('NASDAQ:AAPL', lambda: datetime(2026, 11, 28, 1, 30, tzinfo=SHANGHAI))
    assert friday['data_date'] == '2026-11-27' and friday['last_bar_provisional'] is True
    unsettled = history('NASDAQ:AAPL', lambda: datetime(2026, 11, 28, 2, 12, tzinfo=SHANGHAI))   # 13:12 New York
    assert unsettled['last_bar_provisional'] is True, 'closed at 13:00, settled at 13:30'
    assert history('NASDAQ:AAPL', lambda: datetime(2026, 11, 28, 2, 30, tzinfo=SHANGHAI))['last_bar_provisional'] is False


def test_etf_uses_the_etf_endpoint_and_fund_share_units(upstream):
    upstream.volume = 20_000                       # lots of 100 units, converted by the loader
    out = history('SHA:510300', at(17, 15, 30))
    assert 'error' not in out, out
    assert 'fund_etf_hist_em' in out['source'] and 'stock_zh_a_hist' not in out['source']
    assert out['provider'] == 'akshare.fund_etf_hist_em' and out['adjust'] == 'qfq' and out['market'] == 'CN'
    assert upstream.calls[-1][0] == 'fund_etf_hist_em'
    assert out['rows'][-1][5] == 2_000_000.0 and out['units']['volume'] == '份'
    assert out['last_bar_provisional'] is False and out['calendar'] == 'calendar'
    assert not any('工作日' in note for note in out['notes']), 'the 2026 mainland holiday table is known'
    assert history('SHA:510300', at(17, 10, 30))['last_bar_provisional'] is True


def test_a_share_stock_endpoint_and_volume_in_shares(upstream):
    upstream.volume = 1000
    out = history('SHE:000001', at(17, 10, 30))
    assert 'stock_zh_a_hist' in out['source'] and out['provider'] == 'akshare.stock_zh_a_hist'
    assert out['adjust'] == 'qfq' and out['rows'][-1][5] == 100_000.0 and out['units']['volume'] == '股'
    assert out['last_bar_provisional'] is True and out['currency'] == 'CNY'


def test_us_ticker_uses_yahoo_and_new_york_hours(upstream):
    open_now = history('NASDAQ:AAPL', at(17, 22, 30))                 # 09:30 in New York
    assert 'yfinance' in open_now['source'] and open_now['provider'] == 'yfinance'
    assert open_now['adjust'] == 'yahoo_auto_adjust' and open_now['currency'] == 'USD' and open_now['market'] == 'US'
    assert upstream.calls[-1][0] == 'yfinance.download' and upstream.calls[-1][1]['auto_adjust'] is True
    assert open_now['last_bar_provisional'] is True and open_now['units']['volume'] == '股'
    assert open_now['calendar'] == 'calendar' and not any('工作日' in note for note in open_now['notes'])
    upstream.last = '2026-11-16'
    closed = history('NASDAQ:AAPL', at(17, 6, 0))                      # 17:00 on the 16th in New York
    assert closed['data_date'] == '2026-11-16' and closed['last_bar_provisional'] is False


def test_indicators_report_the_same_provenance_and_units(upstream):
    set_bar_source(LoaderBarSource(now=at(17, 15, 30)))
    out = run(market_tools.get_indicators('SHA:510300', ['volume_ma_20', 'obv', 'sma_5'], 3))
    assert 'error' not in out, out
    assert 'fund_etf_hist_em' in out['source'] and out['provider'] == 'akshare.fund_etf_hist_em'
    assert out['units']['volume_ma_20'].startswith('份') and out['units']['obv'].startswith('份')
    assert out['units']['sma_5'].startswith('价格')
    hk = run(market_tools.get_indicators('HKG:700', ['sma_5'], 3))
    assert 'stock_hk_hist' in hk['source'] and hk['adjust'] == 'qfq' and hk['last_bar_provisional'] is True
    assert hk['market'] == 'HK' and hk['calendar'] == 'calendar' and not any('工作日' in note for note in hk['notes'])


def test_source_label_before_a_fetch_names_the_endpoint_the_ticker_will_use(upstream):
    source = LoaderBarSource(now=at(17, 15, 30))
    assert 'fund_etf_hist_em' in source.source_label('SHA:510300')
    assert 'stock_hk_hist' in source.source_label('HKG:700')
    assert 'stock_zh_a_hist' in source.source_label('SHE:000001')
    assert 'yfinance' in source.source_label('NASDAQ:AAPL')


class Stub:
    def __init__(self, frame):
        self.frame = frame

    def get_daily(self, symbol, start, end, adjust='qfq'):
        return self.frame


def stub_frame(**attrs):
    days = pd.bdate_range(end='2026-11-16', periods=40)
    frame = pd.DataFrame({'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.2, 'volume': 1000.0}, index=days)
    frame.attrs.update(attrs)
    return frame


def test_provider_comes_from_the_loader_result_not_from_the_market():
    source = LoaderBarSource(lambda: Stub(stub_frame(source='akshare.fund_etf_hist_em', volume_unit='shares')),
                             now=at(17, 9, 0))
    result = run(source.get_daily('SHE:000001', 10))        # an A-share ticker, but the loader says: ETF endpoint
    assert result.provider == 'akshare.fund_etf_hist_em' and 'fund_etf_hist_em' in result.source


def test_an_undeclared_provider_is_routed_by_the_loader_table_and_said_so():
    result = run(LoaderBarSource(lambda: Stub(stub_frame()), now=at(17, 9, 0)).get_daily('SHA:510300', 10))
    assert result.provider == 'akshare.fund_etf_hist_em'
    assert any('未声明' in note for note in result.notes)


@pytest.mark.parametrize('attrs', [{'source': 'somewhere.else'}, {'source': 'yfinance', 'volume_unit': 'lots'}])
def test_unknown_provider_or_volume_unit_is_an_error_not_a_guess(attrs):
    with pytest.raises(BarError):
        run(LoaderBarSource(lambda: Stub(stub_frame(**attrs)), now=at(17, 9, 0)).get_daily('SHA:600519', 10))
