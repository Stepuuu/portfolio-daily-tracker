"""Snapshot money: Decimal, rounded the way Today rounds it (``core.market.valuation``).

Each figure is rounded half up to the cent where it is first computed (a position's CNY value from its
unrounded native value, each cash balance's CNY value), and totals are sums of those rounded figures. The
reviewer's case: USD 75 x 7.1234 = 534.255 was stored as 534.25 (binary floats, then round half to even)
where Today showed 534.26. The vectors below are shared: the snapshot and Today's valuation of the same
ledger must both give the expected figures.
"""
from dataclasses import replace
from decimal import Decimal
import json

import pytest

import portfolio_snapshot as snapshot
from core.market.valuation import value_portfolio
from tests.fakes import build_ledger, opening, position, quote, shanghai

DAY = '2026-11-17'
US, CN, CN2 = 'NASDAQ:EXAMPLE', 'SHA:600001', 'SHE:000002'


def live(ticker, price):
    return replace(quote(ticker, price, at=shanghai(f'{DAY} 10:00')), status='live')


# (positions [(ticker, quantity, price)], cash balances, CNY per unit, each position's CNY value, total)
VECTORS = [
    ([(US, '1', '75')], {}, {'USD': '7.1234'}, [534.26], 534.26),                # the reviewer's case
    ([], {'USD': '75'}, {'USD': '7.1234'}, [], 534.26),                           # the same as a cash balance
    ([(CN, '1', '1.005')], {}, {}, [1.01], 1.01),
    ([(CN, '1', '1.125')], {}, {}, [1.13], 1.13),                                 # half up, not half to even
    ([(CN, '1', '1.005'), (CN2, '1', '1.005')], {}, {}, [1.01, 1.01], 2.02),      # the sum of the rounded parts
    ([(US, '10', '0.0705')], {'USD': '1.5'}, {'USD': '7.1234'}, [5.02], 15.71),
]


def both(tmp_path, rows, cash, rates):
    """(the snapshot, Today's valuation) of one ledger account holding ``rows`` and ``cash``."""
    ledger = build_ledger(tmp_path, [opening('Example', cash=cash, date='2026-11-16',
                                             positions=[position(t, q, '1') for t, q, _ in rows])])
    prices = {t: p for t, _, p in rows}
    today = value_portfolio(ledger.state(), {t: live(t, p) for t, p in prices.items()},
                            {c: Decimal(r) for c, r in rates.items()}, None, [])
    saved = snapshot.calculate_snapshot(ledger.holdings(DAY), {t: float(p) for t, p in prices.items()}, {},
                                        {'CNY': 1.0, **{c: float(r) for c, r in rates.items()}}, None, [])
    return saved, today


@pytest.mark.parametrize('rows, cash, rates, values, total', VECTORS)
def test_snapshot_money_is_rounded_like_today(tmp_path, rows, cash, rates, values, total):
    saved, today = both(tmp_path, rows, cash, rates)
    group, account = saved['groups']['Example'], today.accounts[0]
    assert [float(p.market_value_cny) for p in account.positions] == values
    assert float(today.total_cny) == total
    assert [p['market_value_cny'] for p in group['positions']] == values
    assert group['positions_value'] == float(account.market_value_cny)
    assert group['cash'] == float(account.cash_cny)
    assert group['total_value'] == saved['summary']['total_value'] == total
    json.dumps(saved, allow_nan=False)    # plain numbers, as the snapshot file stores them


def test_profit_and_cost_are_rounded_like_today(tmp_path):
    saved, today = both(tmp_path, [(US, '1', '75')], {}, {'USD': '7.1234'})
    row, mine = saved['groups']['Example']['positions'][0], today.accounts[0].positions[0]
    assert row['cost_value_cny'] == float(mine.cost_cny) == 7.12
    assert row['profit_cny'] == float(mine.total_pnl_cny) == 527.13       # 74 x 7.1234 = 527.1316


# -- the first snapshot's day change -------------------------------------------------------------------
# Nothing before it to compare with: its day change is unavailable (None in the file, an empty history field,
# 不可用 in the report, null from the tracker API), never 0. The reviewer's case: 100 shares held before
# moved 1500 -> 1501; Today showed +100 and the first snapshot stored a day change of 0.

UNKNOWN_ON_THE_FIRST_DAY = ('prev_total_value', 'daily_change', 'daily_change_pct', 'capital_change',
                            'market_daily_change', 'market_daily_change_pct')


def first_holdings():
    return {'date': DAY, 'groups': {'Example': {'cash': 0, 'fund': 0, 'cost_basis': 150000, 'positions': [
        {'name': 'Example', 'ticker': CN, 'quantity': 100, 'cost_price': 1500}]}}}


def test_the_first_snapshot_has_no_day_change():
    summary = snapshot.calculate_snapshot(first_holdings(), {CN: 1501.0}, {}, {'CNY': 1.0}, None, [])['summary']
    assert summary['total_value'] == 150100 and summary['prev_date'] is None
    assert {key: summary[key] for key in UNKNOWN_ON_THE_FIRST_DAY} == dict.fromkeys(UNKNOWN_ON_THE_FIRST_DAY)


def test_the_next_snapshot_compares_with_the_first():
    first = snapshot.calculate_snapshot({**first_holdings(), 'date': '2026-11-16'}, {CN: 1501.0}, {},
                                        {'CNY': 1.0}, None, [])
    summary = snapshot.calculate_snapshot(first_holdings(), {CN: 1502.0}, {}, {'CNY': 1.0}, first, [first])['summary']
    assert (summary['prev_date'], summary['daily_change'], summary['market_daily_change']) == ('2026-11-16', 100, 100)
    assert summary['capital_change'] == 0


def test_an_unknown_day_change_reaches_the_history_the_report_and_the_tracker(tmp_path, monkeypatch, capsys):
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import portfolio_report as report
    from backend.api import portfolio_tracker as tracker

    for name in ('holdings', 'snapshots'):
        (tmp_path / name).mkdir()
    (tmp_path / 'config.json').write_text('{}')
    (tmp_path / 'holdings' / f'{DAY}.json').write_text(json.dumps(first_holdings()))
    for module in (snapshot, report, tracker):
        monkeypatch.setattr(module, 'PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.setattr(snapshot, 'CONFIG_PATH', str(tmp_path / 'config.json'))
    monkeypatch.setattr(snapshot, 'fetch_prices', lambda *args: ({CN: 1501.0}, {}))
    monkeypatch.setattr(snapshot, 'fetch_fx_rates', lambda *args: {'CNY': 1.0})
    monkeypatch.setattr(sys, 'argv', ['portfolio_snapshot.py', '--date', DAY])
    snapshot.main()
    assert 'None' not in capsys.readouterr().out

    header, row = (tmp_path / 'history.csv').read_text().strip().split('\n')
    fields = dict(zip(header.split(','), row.split(',')))
    assert [fields[key] for key in ('daily_change', 'daily_change_pct', 'capital_change', 'market_daily_change',
                                    'market_daily_change_pct')] == [''] * 5
    saved = json.loads((tmp_path / 'snapshots' / f'{DAY}.json').read_text())
    text = report.generate_report(saved, report.load_history_csv())
    assert 'None' not in text and '| 日变动 | 不可用（没有上一日的记录可比） |' in text

    app = FastAPI()
    app.include_router(tracker.router, prefix='/api/tracker')
    client = TestClient(app)
    history = client.get('/api/tracker/history').json()['history']
    assert [history[0][key] for key in ('daily_change', 'capital_change', 'market_daily_change')] == [None] * 3
    assert client.get('/api/tracker/summary').json()['daily_change'] is None
    (tmp_path / 'snapshots' / f'{DAY}.json').unlink()
    synthetic = client.get('/api/tracker/snapshot', params={'date': DAY}).json()['summary']
    assert (synthetic['daily_change'], synthetic['market_daily_change'], synthetic['capital_change']) == (None,) * 3
