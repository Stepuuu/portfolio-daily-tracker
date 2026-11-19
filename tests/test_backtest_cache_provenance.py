"""The 数据 tab's per-instrument facts and a history row's real bar dates.

``GET /api/backtest/data/cache`` says, per cached instrument, where its bars came from, when, and whether they
are complete (additive fields; unknown is null, never "complete"). A saved run's history row keeps the
requested start, and adds the first and last bar the run really used. Fictional bars, no network.
"""
import pytest

from backend.api import backtest
from tests.test_backtest_api import (cache_bars, client, env, make_bars, run_payload,  # noqa: F401  (fixtures)
                                     use_buy_once_strategy)


def details(client):
    return {row['symbol']: row for row in client.get('/api/backtest/data/cache').json()['symbol_details']}


def test_each_cached_instrument_says_its_source_time_and_completeness(client, env):
    covered = make_bars(start='2024-01-01', periods=20)
    env.bars.save_daily('600000', covered, 'qfq')
    env.bars.record_coverage('600000', 'qfq', '2024-01-01', '2024-01-26', fetched_at='2024-02-01T10:00:00',
                             source='akshare.stock_zh_a_hist')
    unrecorded = make_bars(start='2024-01-01', periods=10)
    env.bars.save_daily('600001', unrecorded, 'qfq')                  # bars without any download record
    partly = make_bars(start='2024-01-01', periods=20)
    env.bars.save_daily('600002', partly, 'qfq')
    env.bars.record_coverage('600002', 'qfq', '2024-01-01', '2024-01-10', fetched_at='2024-02-02T09:30:00')

    rows = details(client)
    full = rows['600000']
    assert (full['source'], full['fetched_at'], full['complete']) == (
        'akshare.stock_zh_a_hist', '2024-02-01T10:00:00', True)
    [qfq] = full['adjustments']
    assert (qfq['adjust'], qfq['rows'], qfq['first'], qfq['last']) == ('qfq', 20, '2024-01-01', '2024-01-26')
    assert qfq['coverage'] == [['2024-01-01', '2024-01-26']] and qfq['complete'] is True
    assert (full['start_date'], full['end_date']) == ('2024-01-01', '2024-01-26')     # the old fields stay

    unknown = rows['600001']
    assert (unknown['source'], unknown['fetched_at'], unknown['complete']) == (None, None, None)

    gap = rows['600002']
    assert (gap['source'], gap['fetched_at'], gap['complete']) == ('import', '2024-02-02T09:30:00', False)


def test_a_history_row_names_the_first_and_last_bar_the_run_used(client, env, monkeypatch):
    bars = make_bars(start='2024-01-01', periods=30)
    cache_bars(env, '600000', bars)
    use_buy_once_strategy(monkeypatch)
    # Saturday 6 January: the first bar there is is Monday the 8th.
    run = client.post('/api/backtest/run-sync', json=run_payload('600000', bars, start_date='2024-01-06'))
    assert run.status_code == 200, run.text
    assert run.json()['saved_to_history'] is True

    [row] = client.get('/api/backtest/history').json()['runs']
    assert row['start_date'] == '2024-01-06'                         # what was asked for, unchanged
    assert (row['first_date'], row['last_date']) == ('2024-01-08', str(bars.index[-1].date()))
    saved = client.get(f"/api/backtest/history/{row['run_id']}").json()
    assert (saved['first_date'], saved['last_date']) == ('2024-01-08', str(bars.index[-1].date()))


def test_a_row_saved_before_the_bar_dates_were_kept_says_they_are_unknown(client, env):
    env.history.save_run(result_dict={'run_id': 'old', 'strategy_name': 'S', 'primary_symbol': '600000',
                                      'start_date': '2024-01-06', 'end_date': '2024-02-09'})
    [row] = client.get('/api/backtest/history').json()['runs']
    assert row['start_date'] == '2024-01-06' and (row['first_date'], row['last_date']) == (None, None)


@pytest.mark.parametrize('path', ['/api/backtest/data/cache', '/api/backtest/data/symbols',
                                  '/api/backtest/data/preview/600000', '/api/backtest/history',
                                  '/api/backtest/history/best', '/api/backtest/history/lessons',
                                  '/api/backtest/history/x', '/api/backtest/runs/x'])
def test_reading_before_the_first_download_or_run_creates_no_database(tmp_path, monkeypatch, path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv('TRACKER_BACKTEST_DIR', str(tmp_path / 'bt'))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    backtest.SERVICE.registry.clear()
    app = FastAPI()
    app.include_router(backtest.router)
    answer = TestClient(app).get(path)
    assert answer.status_code in (200, 404), answer.text
    assert not (tmp_path / 'bt').exists() or list((tmp_path / 'bt').iterdir()) == []
