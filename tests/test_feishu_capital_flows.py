"""Capital flow cards use synthetic accounts and never access live financial data."""
import copy
import json
from datetime import date, timedelta

import pytest
import portfolio_snapshot as snapshot
from feishu_workbench.clients import DeliveryError
from feishu_workbench.portfolio import PortfolioError
from tests.test_feishu_daily_batch import lab, add, latest, open_daily, step, trade, balance, walk
from tests.test_feishu_workbench import callback


@pytest.fixture
def capital_lab(lab):
    w, path = lab
    data = json.loads(path.read_text())
    data['groups']['Growth']['cost_basis'] = 6000
    data['groups']['Reserve']['cost_basis'] = 3300
    for group in data['groups'].values():
        for position in group['positions']:
            position['name'] = position['ticker']
    path.write_text(json.dumps(data))
    return w, path


def flow(operation='deposit', account='Growth', amount='200', currency='CNY', **kwargs):
    return dict(operation=operation, account=account, amount=amount, currency=currency, **kwargs)


def test_deposit_and_withdraw_update_cash_and_capital_together(capital_lab):
    w, path = capital_lab
    before = latest(path)
    preview = w.portfolio.preview_batch(path.stem, [flow(amount='250.25'), flow('withdraw', 'Reserve', '100')])
    assert latest(path) == before
    receipt = w.portfolio.confirm(preview, 'capital_test')
    result = latest(path)
    assert result['Growth']['cash'] == 1250.25
    assert result['Growth']['cost_basis'] == 6250.25
    assert result['Growth']['cash_balances']['USD'] == 500
    assert result['Reserve']['cash'] == 1900
    assert result['Reserve']['cost_basis'] == 3200
    for a in before:
        assert result[a]['positions'] == before[a]['positions']
        assert result[a]['fund'] == before[a]['fund']
    assert w.portfolio.confirm(preview, 'capital_test') == receipt
    assert latest(path) == result


@pytest.mark.parametrize('reverse', [False, True])
def test_deposit_plus_final_cash_never_counts_the_same_cash_twice(capital_lab, reverse):
    w, path = capital_lab
    items = [flow(amount='500'), trade(ticker='SHA:600000', operation='buy', quantity='10'),
             balance(currency='CNY', amount='1379')]
    if reverse:
        items.insert(0, items.pop())
    w.portfolio.confirm(w.portfolio.preview_batch(path.stem, items), 'cash_final')
    result = latest(path)['Growth']
    assert result['cash'] == 1379
    assert result['cost_basis'] == 6500
    assert result['positions'][1]['quantity'] == 210


def test_principal_reconciliation_only_changes_account_principal(capital_lab):
    w, path = capital_lab
    before = latest(path)
    w.portfolio.confirm(w.portfolio.preview_batch(path.stem, [flow('set_cost_basis', amount='6200')]), 'principal_only')
    expected = copy.deepcopy(before)
    expected['Growth']['cost_basis'] = 6200
    assert latest(path) == expected
    assert w.portfolio.overview()['accounts'][0]['cost_basis'] == '6200'


def test_final_principal_is_applied_after_flows_regardless_of_position(capital_lab):
    w, path = capital_lab
    reconcile = flow('set_cost_basis', amount='6800')
    for items in ([reconcile, flow(amount='100')], [flow(amount='100'), reconcile]):
        result = w.portfolio.plan_batch(path.stem, items)
        assert result['after']['groups']['Growth']['cost_basis'] == 6800
        assert any('投入本金 6000 → 6800' in line for line in result['changes'])
    with pytest.raises(PortfolioError):
        w.portfolio.preview_batch(path.stem, [reconcile, reconcile])


@pytest.mark.parametrize('item', [flow(amount='0'), flow(amount='-1'), flow(amount='NaN'),
    flow('withdraw', amount='Infinity'), flow(currency='USD'), flow(currency='USD', principal_cny='NaN'),
    flow(currency='USD', principal_cny='-10'), flow(principal_cny='201'),
    flow('set_cost_basis', currency='USD'), flow('set_cost_basis', principal_cny='200'),
    flow(ticker='SHA:600000'), flow('withdraw', fee='2'), flow(account='unknown')])
def test_invalid_capital_fields_leave_the_whole_portfolio_unchanged(capital_lab, item):
    w, path = capital_lab
    before = path.read_bytes()
    with pytest.raises(PortfolioError):
        w.portfolio.preview_batch(path.stem, [trade(), item])
    assert path.read_bytes() == before


def test_foreign_deposit_uses_explicit_cny_capital_and_preserves_native_cash(capital_lab):
    w, path = capital_lab
    preview = w.portfolio.preview_batch(path.stem, [flow(currency='USD', amount='100', principal_cny='712.34')])
    w.portfolio.confirm(preview, 'foreign_flow')
    result = latest(path)['Growth']
    assert result['cash'] == 1000
    assert result['cash_balances']['USD'] == 600
    assert result['cost_basis'] == 6712.34


def test_missing_account_principal_is_not_silently_assumed_zero(lab):
    w, path = lab
    with pytest.raises(PortfolioError, match='初始化本金'):
        w.portfolio.preview_batch(path.stem, [flow()])


@pytest.mark.parametrize('operation,amount,delta', [('deposit', '500', 500), ('withdraw', '100', -100)])
def test_snapshot_treats_capital_flow_as_funding_instead_of_profit(capital_lab, operation, amount, delta):
    w, path = capital_lab
    data = json.loads(path.read_text())
    prices = {'NASDAQ:EXAMPLE': 8, 'SHA:600000': 10, 'SHE:000001': 9}
    rates = {'CNY': 1, 'USD': 7}
    prev = snapshot.calculate_snapshot(data, prices, {}, rates, None, [])
    prev['date'] = (date.fromisoformat(path.stem) - timedelta(days=1)).isoformat()
    w.portfolio.confirm(w.portfolio.preview_batch(path.stem, [flow(operation, amount=amount)]), 'return_flow')
    result = snapshot.calculate_snapshot(json.loads(path.read_text()), prices, {}, rates, prev, [])
    assert result['summary']['capital_change'] == delta
    assert result['summary']['daily_change'] == delta
    assert result['summary']['market_daily_change'] == 0
    assert result['summary']['total_profit'] == prev['summary']['total_profit']


def test_cards_add_edit_and_confirm_capital_with_existing_trade(capital_lab):
    w, path = capital_lab
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, product='NASDAQ:EXAMPLE', quantity='2', price='12', fee='1')
    step(w, sid, 'daily.add')
    choose = w.delivery.cards[w.store.session(sid)['message']]
    options = next(n['options'] for n in walk(choose) if n.get('name') == 'operation')
    assert {'deposit', 'withdraw', 'set_cost_basis'} <= {o['value'] for o in options}
    form = step(w, sid, 'daily.item.choose', account='Growth', operation='deposit')
    card = w.delivery.cards[form['message']]
    names = {n.get('name') for n in walk(card) if n.get('tag') == 'input'}
    assert names == {'amount', 'principal_cny'}
    result = step(w, sid, 'daily.item.save', amount='100', currency='CNY', principal_cny='')
    assert len(result['model']['items']) == 2
    item_id = result['model']['items'][-1]['id']
    step(w, sid, 'daily.item.edit', item_id=item_id)
    step(w, sid, 'daily.item.save', amount='250', currency='CNY', principal_cny='')
    step(w, sid, 'daily.batch.preview')
    step(w, sid, 'daily.batch.confirm')
    assert latest(path)['Growth']['cash'] == 1250
    assert latest(path)['Growth']['cost_basis'] == 6250
    assert latest(path)['Growth']['positions'][0]['quantity'] == 8
    assert len(w.store.recent_reports('owner')) == 1
    assets = step(w, sid, 'assets')
    assert any(n.get('kind') == '投入本金 · CNY' and n.get('amount') == '6250'
               for n in walk(w.delivery.cards[assets['message']]))


def test_interrupted_capital_confirmation_never_deposits_twice(capital_lab, monkeypatch):
    w, path = capital_lab
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, operation='deposit', amount='200', currency='CNY')
    step(w, sid, 'daily.batch.preview')
    w.accept(callback(w.store.session(sid), 'daily.batch.confirm'))
    command = w.store.claim()
    real = w.portfolio._write_holdings
    def interrupted(*args):
        real(*args)
        raise DeliveryError('simulated interruption')
    monkeypatch.setattr(w.portfolio, '_write_holdings', interrupted)
    with pytest.raises(DeliveryError):
        w.execute(command)
    monkeypatch.setattr(w.portfolio, '_write_holdings', real)
    w.execute(command)
    w.store.finish(command)
    assert latest(path)['Growth']['cash'] == 1200
    assert latest(path)['Growth']['cost_basis'] == 6200
    assert len(w.store.recent_reports('owner')) == 1
