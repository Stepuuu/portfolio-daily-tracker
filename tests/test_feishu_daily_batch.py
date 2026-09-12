"""Daily workflows against synthetic portfolios; no live bot or financial data."""
import copy
import json

import pytest

from feishu_workbench.cards import build_card, CardContext, ACTIONS
from feishu_workbench.clients import DeliveryError
from feishu_workbench.daily import today
from feishu_workbench.drafts import Drafts
from feishu_workbench.portfolio import PortfolioAdapter, PortfolioError
from feishu_workbench.service import Workbench
from feishu_workbench.store import Store, Rejected
from tests.test_feishu_workbench import FakeDelivery, FakeResearch, callback


@pytest.fixture
def lab(tmp_path):
    project = tmp_path / 'project'
    holdings = project / 'portfolio/holdings'
    holdings.mkdir(parents=True)
    (project / 'scripts').mkdir()
    groups = {
        'Growth': {'cash': 1000, 'cash_balances': {'CNY': 1000, 'USD': 500}, 'fund': 300,
                   'positions': [{'ticker': 'NASDAQ:EXAMPLE', 'quantity': 10, 'cost_price': 8},
                                 {'ticker': 'SHA:600000', 'quantity': 200, 'cost_price': 10}]},
        'Reserve': {'cash': 2000, 'cash_balances': {'CNY': 2000}, 'fund': 400,
                    'positions': [{'ticker': 'SHE:000001', 'quantity': 100, 'cost_price': 9}]},
    }
    path = holdings / (today() + '.json')
    path.write_text(json.dumps({'date': today(), 'groups': groups}))
    portfolio = PortfolioAdapter(project, tmp_path / 'state/adapter')
    controller = Workbench({'allowed_users': ['owner'], 'account_id': 'qr'},
                           Store(tmp_path / 'state/cards.sqlite3'), portfolio, FakeResearch(), FakeDelivery())
    return controller, path


def trade(account='Growth', ticker='NASDAQ:EXAMPLE', operation='sell', quantity='2', **kwargs):
    return {'account': account, 'operation': operation, 'ticker': ticker, 'quantity': quantity,
            'price': '12', 'fee': '1', 'currency': 'USD' if ticker.startswith('NASDAQ:') else 'CNY', **kwargs}


def balance(account='Growth', operation='set_cash', amount='400', currency='USD'):
    return dict(account=account, operation=operation, amount=amount, currency=currency)


def latest(path):
    return json.loads(path.read_text())['groups']


def open_daily(w):
    sid = w.open('owner', 'daily')
    command = w.store.claim()
    w.execute(command)
    w.store.finish(command)
    return sid


def step(w, sid, action, **fields):
    session = w.store.session(sid)
    w.accept(callback(session, action, **fields))
    command = w.store.claim()
    assert command['action'] == action
    w.execute(command)
    w.store.finish(command)
    return w.store.session(sid)


def add(w, sid, account='Growth', operation='sell', **fields):
    step(w, sid, 'daily.add')
    step(w, sid, 'daily.item.choose', account=account, operation=operation)
    return step(w, sid, 'daily.item.save', **fields)


def walk(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def test_multi_account_batch_reconciles_final_cash_after_all_trades(lab):
    w, path = lab
    before = path.read_bytes()
    items = [balance(amount='600'), trade(), trade(ticker='SHA:600000', quantity='100'),
             balance(operation='set_fund', amount='333.25', currency='CNY'),
             trade(account='Reserve', ticker='SHE:000001', quantity='25'),
             balance(account='Reserve', currency='CNY', amount='2500'),
             balance(account='Reserve', operation='set_fund', currency='CNY', amount='450')]
    preview = w.portfolio.preview_batch(today(), items)
    assert path.read_bytes() == before
    receipt = w.portfolio.confirm(preview, 'batch_one')
    result = latest(path)
    assert result['Growth']['cash_balances'] == {'USD': 600, 'CNY': 2199}
    assert result['Growth']['fund'] == 333.25
    assert [p['quantity'] for p in result['Growth']['positions']] == [8, 100]
    assert result['Reserve']['cash'] == 2500
    assert result['Reserve']['fund'] == 450
    assert result['Reserve']['positions'][0]['quantity'] == 75
    assert w.portfolio.confirm(preview, 'batch_one') == receipt
    assert latest(path) == result


def test_only_one_account_changes_and_cash_and_fund_are_independent(lab):
    w, path = lab
    before = latest(path)
    w.portfolio.confirm(w.portfolio.preview_batch(today(), [balance(operation='set_fund', currency='CNY', amount='325')]), 'fund_only')
    result = latest(path)
    assert result['Reserve'] == before['Reserve']
    assert result['Growth']['cash_balances'] == before['Growth']['cash_balances']
    assert result['Growth']['positions'] == before['Growth']['positions']
    assert result['Growth']['fund'] == 325


@pytest.mark.parametrize('items', [
    [trade(), trade(quantity='9')],
    [trade(), balance(account='missing')],
    [trade(), balance(amount='NaN')],
    [balance(), balance(amount='20')],
    [balance(operation='set_fund', currency='CNY'), balance(operation='set_fund', currency='CNY')],
])
def test_invalid_batch_never_partially_writes(lab, items):
    w, path = lab
    before = path.read_bytes()
    with pytest.raises(PortfolioError):
        w.portfolio.preview_batch(today(), items)
    assert path.read_bytes() == before


def test_cash_reconciliation_order_independent_and_negative_balance_explicit(lab):
    w, _ = lab
    purchase = trade(operation='buy', quantity='100')
    cash = balance(amount='-100')
    first = w.portfolio.plan_batch(today(), [cash, purchase])
    second = w.portfolio.plan_batch(today(), [purchase, cash])
    assert first['after'] == second['after']
    assert first['after']['groups']['Growth']['cash_balances']['USD'] == -100
    assert any('500 → -100' in s for s in first['changes'])


def test_unchanged_has_no_fields_and_one_click_commits_and_queues_once(lab):
    w, path = lab
    before = path.read_bytes()
    sid = open_daily(w)
    session = w.store.session(sid)
    card = w.delivery.cards[session['message']]
    assert not any(n.get('tag') in {'form', 'input', 'select_static', 'date_picker'} for n in walk(card))
    event = callback(session, 'daily.unchanged')
    w.accept(event)
    w.accept(event)
    command = w.store.claim()
    w.execute(command)
    w.execute(command)  # Simulate redelivery after a dropped acknowledgement.
    w.store.finish(command)
    assert path.read_bytes() == before
    assert w.store.session(sid)['view'] == 'daily_receipt'
    assert len(w.store.recent_reports('owner')) == 1
    with w.portfolio._db() as db:
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 1


def test_add_edit_remove_resume_and_confirm_multiple_products(lab):
    w, path = lab
    before = path.read_bytes()
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    first = add(w, sid, product='NASDAQ:EXAMPLE', quantity='2', price='12', fee='1')
    first_id = first['model']['items'][0]['id']
    add(w, sid, account='Reserve', operation='set_fund', amount='500')
    add(w, sid, account='Reserve', product='SHE:000001', quantity_mode='all', quantity='', price='10', fee='0')
    add(w, sid, operation='set_cash', currency='USD', amount='600')
    assert path.read_bytes() == before
    step(w, sid, 'home')
    second_sid = open_daily(w)
    assert len(w.store.session(second_sid)['model']['items']) == 4
    step(w, second_sid, 'daily.batch')
    step(w, second_sid, 'daily.item.edit', item_id=first_id)
    changed = step(w, second_sid, 'daily.item.save', product='NASDAQ:EXAMPLE', quantity='3', price='13', fee='0')
    assert changed['model']['items'][0]['quantity'] == '3'
    last_id = changed['model']['items'][-1]['id']
    removed = step(w, second_sid, 'daily.item.remove', item_id=last_id)
    assert len(removed['model']['items']) == 3
    step(w, second_sid, 'daily.batch.preview')
    assert path.read_bytes() == before
    step(w, second_sid, 'daily.batch.confirm')
    groups = latest(path)
    assert groups['Growth']['positions'][0]['quantity'] == 7
    assert groups['Growth']['cash_balances']['USD'] == 539
    assert groups['Reserve']['positions'] == []
    assert groups['Reserve']['cash'] == 3000
    assert groups['Reserve']['fund'] == 500
    assert w.daily.drafts.get('owner', today())['items'] == []
    assert len(w.store.recent_reports('owner')) == 1


def test_bad_entry_keeps_form_inputs_and_draft(lab):
    w, path = lab
    before = path.read_bytes()
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    bad = add(w, sid, product='SHE:000001', quantity='2', price='12')
    assert bad['view'] == 'daily_item_form'
    assert bad['model']['quantity'] == '2'
    assert '该账户' in bad['model']['notice']
    assert not w.daily.drafts.get('owner', today())['items']
    assert path.read_bytes() == before


def test_pending_draft_cannot_be_overwritten_with_unchanged(lab):
    w, path = lab
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, operation='set_fund', amount='550')
    step(w, sid, 'daily.open')
    with pytest.raises(Rejected, match='未保存'):
        step(w, sid, 'daily.unchanged')
    assert latest(path)['Growth']['fund'] == 300


def test_stale_card_and_stale_financial_preview_preserve_draft(lab):
    w, path = lab
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, operation='set_fund', amount='550')
    step(w, sid, 'daily.batch.preview')
    data = json.loads(path.read_text())
    data['groups']['Reserve']['fund'] = 401
    path.write_text(json.dumps(data))
    with pytest.raises(PortfolioError, match='发生了变化'):
        step(w, sid, 'daily.batch.confirm')
    draft = w.daily.drafts.get('owner', today())
    assert not draft['locked_by'] and len(draft['items']) == 1
    assert latest(path)['Growth']['fund'] == 300
    with pytest.raises(Rejected, match='其他卡片'):
        w.daily.drafts.save('owner', draft['revision'] - 1, 'stale', today(), [])


@pytest.mark.parametrize('crash_at', ['write', 'clear', 'delivery'])
def test_commit_recovery_after_partial_completion_never_repeats_finance(lab, monkeypatch, crash_at):
    w, path = lab
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, product='NASDAQ:EXAMPLE', quantity='2', price='12', fee='1')
    step(w, sid, 'daily.batch.preview')
    session = w.store.session(sid)
    w.accept(callback(session, 'daily.batch.confirm'))
    command = w.store.claim()
    target, name = {'write': (w.portfolio, '_write_holdings'), 'clear': (w.daily.drafts, 'complete'),
                    'delivery': (w.delivery, 'patch')}[crash_at]
    real = getattr(target, name)
    def crash(*args, **kwargs):
        if crash_at == 'write':
            real(*args, **kwargs)
        raise DeliveryError('test outage')
    monkeypatch.setattr(target, name, crash)
    with pytest.raises(DeliveryError):
        w.execute(command)
    monkeypatch.setattr(target, name, real)
    # Fresh controller, same databases, as after a service restart.
    resumed = Workbench(w.config, Store(w.store.path), w.portfolio, w.research, w.delivery)
    resumed.execute(command)
    resumed.store.finish(command)
    assert latest(path)['Growth']['positions'][0]['quantity'] == 8
    assert latest(path)['Growth']['cash_balances']['USD'] == 523
    assert not resumed.daily.drafts.get('owner', today())['items']
    assert len(resumed.store.recent_reports('owner')) == 1


def test_item_add_retry_is_idempotent_after_card_delivery_failure(lab, monkeypatch):
    w, _ = lab
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    step(w, sid, 'daily.add')
    step(w, sid, 'daily.item.choose', account='Growth', operation='set_cash')
    session = w.store.session(sid)
    w.accept(callback(session, 'daily.item.save', amount='450', currency='USD'))
    command = w.store.claim()
    real = w.delivery.patch
    monkeypatch.setattr(w.delivery, 'patch', lambda *a: (_ for _ in ()).throw(DeliveryError('test')))
    with pytest.raises(DeliveryError):
        w.execute(command)
    monkeypatch.setattr(w.delivery, 'patch', real)
    w.execute(command)
    w.store.finish(command)
    assert len(w.daily.drafts.get('owner', today())['items']) == 1


def test_new_product_buy_and_per_account_picklists(lab):
    w, path = lab
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    result = add(w, sid, operation='buy', product='__new__', new_ticker='NASDAQ:NEW', quantity='0.5', price='20', fee='0')
    assert result['view'] == 'daily_batch'
    step(w, sid, 'daily.add')
    form = step(w, sid, 'daily.item.choose', account='Growth', operation='sell')
    assert {p['value'] for p in form['model']['positions']} == {'NASDAQ:EXAMPLE', 'SHA:600000', 'NASDAQ:NEW'}
    step(w, sid, 'daily.item.save', product='NASDAQ:NEW', quantity_mode='all', quantity='', price='22', fee='0')
    step(w, sid, 'daily.batch.preview')
    step(w, sid, 'daily.batch.confirm')
    assert latest(path)['Growth']['cash_balances']['USD'] == 501
    assert {p['ticker'] for p in latest(path)['Growth']['positions']} == {'NASDAQ:EXAMPLE', 'SHA:600000'}


def test_maximum_batch_card_contains_all_changes(lab):
    w, _ = lab
    items = [trade(operation='buy', quantity='0.125', fee='0') for _ in range(30)]
    preview = w.portfolio.preview_batch(today(), items)
    context = CardContext('example', 1, {a: str(i) for i, a in enumerate(ACTIONS)})
    card = build_card('daily_batch_preview', preview, context)
    encoded = json.dumps(card, ensure_ascii=False)
    assert encoded.count('买入') == 30
    assert len(encoded.encode()) < 28000


def test_pending_update_recovers_from_another_card_without_replaying_trades(lab, monkeypatch):
    w, path = lab
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, product='NASDAQ:EXAMPLE', quantity='2', price='12', fee='1')
    step(w, sid, 'daily.batch.preview')
    real = w.daily.drafts.complete
    monkeypatch.setattr(w.daily.drafts, 'complete', lambda *a: (_ for _ in ()).throw(RuntimeError('interrupted')))
    with pytest.raises(RuntimeError):
        step(w, sid, 'daily.batch.confirm')
    monkeypatch.setattr(w.daily.drafts, 'complete', real)
    other = open_daily(w)
    assert w.store.session(other)['view'] == 'daily_pending'
    step(w, other, 'daily.commit.retry')
    assert latest(path)['Growth']['positions'][0]['quantity'] == 8
    assert not w.daily.drafts.get('owner', today())['locked_by']
    assert len(w.store.recent_reports('owner')) == 1


def test_confirmation_rejects_a_draft_changed_in_another_card(lab):
    w, path = lab
    before = path.read_bytes()
    first = open_daily(w)
    step(w, first, 'daily.batch')
    add(w, first, operation='set_fund', amount='350')
    step(w, first, 'daily.batch.preview')
    second = open_daily(w)
    step(w, second, 'daily.batch')
    add(w, second, account='Reserve', operation='set_cash', amount='2100', currency='CNY')
    with pytest.raises(Rejected, match='其他卡片'):
        step(w, first, 'daily.batch.confirm')
    assert path.read_bytes() == before
    assert len(w.daily.drafts.get('owner', today())['items']) == 2
