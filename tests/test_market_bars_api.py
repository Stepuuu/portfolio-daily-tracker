"""GET /api/market/bars: the stock drawer's daily bars, with provider, adjustment and provisional labels.

Offline: the bar source is a ``FakeBarSource`` (or, with scripted quotes, ``fake_bars.json``).
"""
import json
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import market
from core.market.bars import BarError, FakeBarSource, set_bar_source
from core.market.runtime import set_market_runtime


def make_bars(count, end='2026-11-16', base=10.0):
    last = date.fromisoformat(end)
    days = []
    day = last
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [{'date': day.isoformat(), 'open': base + i * .1, 'high': base + i * .1 + .2, 'low': base + i * .1 - .2,
             'close': base + i * .1 + .05, 'volume': 1000.0 + i} for i, day in enumerate(reversed(days))]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    app = FastAPI()
    app.include_router(market.router, prefix='/api/market')
    yield TestClient(app)
    set_bar_source(None)
    set_market_runtime(None)


def test_bars_come_with_their_source_adjustment_and_coverage(client):
    set_bar_source(FakeBarSource({'SHE:000001': make_bars(80)}, today='2026-11-17', label='fake-bars'))
    response = client.get('/api/market/bars', params={'ticker': 'she:000001', 'days': 60})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['ticker'] == 'SHE:000001' and payload['currency'] == 'CNY'
    assert payload['source'] == 'fake-bars'
    assert payload['adjust'] == 'qfq' and payload['adjust_label'] == '前复权' and '前复权' in payload['adjust_note']
    assert len(payload['bars']) == 60 and payload['coverage']['rows'] == 60
    assert payload['bars'][0]['date'] < payload['bars'][-1]['date'] == payload['data_date'] == '2026-11-16'
    assert set(payload['bars'][0]) == {'date', 'open', 'high', 'low', 'close', 'volume'}
    # Fetched after the close of a day whose bar is not in the data: nothing provisional.
    assert payload['last_bar_provisional'] is False


def test_bad_tickers_indices_and_ranges_are_refused_in_chinese(client):
    set_bar_source(FakeBarSource({}, today='2026-11-17'))
    for params in ({'ticker': '600519'}, {'ticker': 'IDX:000300'}, {'ticker': 'SHA:600519', 'days': 1},
                   {'ticker': 'SHA:600519', 'days': 400}):
        response = client.get('/api/market/bars', params=params)
        assert response.status_code == 422, (params, response.text)
    assert '交易所:代码' in client.get('/api/market/bars', params={'ticker': '600519'}).json()['detail']
    assert '指数' in client.get('/api/market/bars', params={'ticker': 'IDX:000300'}).json()['detail']


def test_a_failing_source_is_a_502_that_names_it(client):
    set_bar_source(FakeBarSource({'SHA:600519': BarError('数据源超时（30 秒）', 'akshare（东方财富）')}, today='2026-11-17'))
    response = client.get('/api/market/bars', params={'ticker': 'SHA:600519'})
    assert response.status_code == 502
    assert response.json()['detail'] == '日线暂不可用：数据源超时（30 秒）（akshare（东方财富））'


def test_scripted_quotes_read_scripted_bars_on_the_scripted_clock(client, monkeypatch, tmp_path):
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', 'fake')
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    set_market_runtime(None)
    (tmp_path / 'fake_quotes.json').write_text(json.dumps({'now': '2026-11-17T10:32:05+08:00', 'quotes': {}}),
                                               encoding='utf-8')
    # No fake_bars.json: no network either, and the page is told why.
    response = client.get('/api/market/bars', params={'ticker': 'SHA:DEMO'})
    assert response.status_code == 503 and '不联网' in response.json()['detail']

    (tmp_path / 'fake_bars.json').write_text(json.dumps({'SHA:DEMO': make_bars(30, end='2026-11-17')}),
                                             encoding='utf-8')
    payload = client.get('/api/market/bars', params={'ticker': 'SHA:DEMO', 'days': 20}).json()
    assert payload['source'] == '模拟日线' and len(payload['bars']) == 20
    assert payload['fetched_at'] == '2026-11-17T10:32:05+08:00'
    # Today's bar during the morning session is still moving.
    assert payload['data_date'] == '2026-11-17' and payload['last_bar_provisional'] is True
    missing = client.get('/api/market/bars', params={'ticker': 'SHA:600519'})
    assert missing.status_code == 502 and '没有该代码的日线' in missing.json()['detail']


def scripted(monkeypatch, tmp_path, now, script):
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', 'fake')
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    set_market_runtime(None)
    (tmp_path / 'fake_quotes.json').write_text(json.dumps({'now': now, 'quotes': {}}), encoding='utf-8')
    (tmp_path / 'fake_bars.json').write_text(json.dumps(script), encoding='utf-8')


def test_scripted_bars_dated_after_the_scripted_clock_are_not_served(client, monkeypatch, tmp_path):
    # The clock is pinned to the evening of November 17; the script runs to November 18.
    scripted(monkeypatch, tmp_path, '2026-11-17T20:00:00+08:00', {'SHA:DEMO': make_bars(30, end='2026-11-18')})
    response = client.get('/api/market/bars', params={'ticker': 'SHA:DEMO', 'days': 5})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [bar['date'] for bar in payload['bars']][-1] == payload['data_date'] == '2026-11-17'
    assert all(bar['date'] <= '2026-11-17' for bar in payload['bars'])
    assert len(payload['bars']) == 5 and payload['coverage']['last'] == '2026-11-17'
    assert payload['coverage']['requested_end'] == '2026-11-17'
    assert payload['last_bar_provisional'] is False   # the 17th has closed by 20:00


def test_scripted_bars_of_another_market_follow_that_exchange_s_date(client, monkeypatch, tmp_path):
    # 09:00 on the 18th in Shanghai is still the evening of the 17th in New York: no bar of the 18th yet.
    scripted(monkeypatch, tmp_path, '2026-11-18T09:00:00+08:00', {'NASDAQ:DEMO': make_bars(30, end='2026-11-18')})
    payload = client.get('/api/market/bars', params={'ticker': 'NASDAQ:DEMO', 'days': 5}).json()
    assert payload['data_date'] == '2026-11-17' and payload['coverage']['requested_end'] == '2026-11-17'


def test_a_scripted_row_without_a_date_is_refused_in_chinese(client, monkeypatch, tmp_path):
    rows = make_bars(10, end='2026-11-16')
    del rows[3]['date']
    scripted(monkeypatch, tmp_path, '2026-11-17T10:32:05+08:00', {'SHA:DEMO': rows, 'SHA:LIST': {'date': 'x'}})
    for ticker in ('SHA:DEMO', 'SHA:LIST'):
        response = client.get('/api/market/bars', params={'ticker': ticker, 'days': 5})
        assert response.status_code == 502, response.text
        assert response.json()['detail'].startswith('日线暂不可用：') and '模拟日线' in response.json()['detail']
