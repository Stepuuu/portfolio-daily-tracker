"""The daily card on a confirmed ledger: synthetic accounts, no live bot, quotes or network.

Each item of the card's list becomes ledger events (docs/FEISHU_WORKBENCH.md, "With the transaction ledger");
the card's preview is a ledger proposal and its confirmation is ``Ledger.confirm`` of it.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from core.ledger import Ledger
from feishu_workbench import ledger_book
from feishu_workbench.cards import ACTIONS, CardContext, build_card
from feishu_workbench.clients import DeliveryError
from feishu_workbench.daily import today
from feishu_workbench.portfolio import PortfolioAdapter, PortfolioError, StalePreview
from feishu_workbench.service import Workbench
from feishu_workbench.store import Rejected, Store
from tests.test_feishu_workbench import FakeDelivery, FakeResearch, callback

OPENED = '2026-01-05'


def opening(account, cash, contributed, positions=(), fund='0'):
    return {'kind': 'opening', 'date': OPENED, 'account': account, 'cash_balances': cash,
            'contributed_cny': contributed, 'fund_cny': fund,
            'positions': [{'ticker': t, 'quantity': q, 'cost_price': c} for t, q, c in positions]}


@pytest.fixture
def book(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    (project / 'portfolio').mkdir(parents=True)
    (project / 'scripts').mkdir()
    monkeypatch.setenv('PORTFOLIO_DIR', str(project / 'portfolio'))
    monkeypatch.delenv('QR_PORTFOLIO_DIR', raising=False)
    ledger = Ledger(project / 'portfolio' / 'ledger.sqlite3')
    proposal = ledger.propose([
        opening('Growth', {'CNY': '100000', 'HKD': '1000', 'USD': '500'}, '120000',
                [('SHA:600000', '200', '10'), ('NASDAQ:EXAMPLE', '10', '8'), ('HKG:00700', '100', '300')], fund='300'),
        opening('Reserve', {'CNY': '2000'}, '2000', [('SHE:000001', '100', '9')]),
    ])
    ledger.confirm(proposal['id'])
    adapter = PortfolioAdapter(project, tmp_path / 'state' / 'adapter')
    controller = Workbench({'allowed_users': ['owner'], 'account_id': 'qr'},
                           Store(tmp_path / 'state' / 'cards.sqlite3'), adapter, FakeResearch(), FakeDelivery())
    # The report step's clock: 06:00 the next morning, when every close of today's sessions is final (New York's
    # too), so a report for today runs whenever the tests do. Tests of the waiting set their own time.
    tomorrow = date.fromisoformat(today()) + timedelta(days=1)
    monkeypatch.setattr(ledger_book, '_now', lambda: datetime.fromisoformat(f'{tomorrow}T06:00:00+08:00'))
    return controller, ledger


def item(operation='buy', account='Growth', ident=None, **fields):
    defaults = {'buy': {'ticker': 'SHA:600000', 'quantity': '100', 'price': '10.5', 'fee': '5', 'currency': 'CNY'},
                'sell': {'ticker': 'SHA:600000', 'quantity': '100', 'price': '12', 'fee': '6', 'currency': 'CNY'},
                'deposit': {'amount': '1000', 'currency': 'CNY'}, 'withdraw': {'amount': '1000', 'currency': 'CNY'},
                'set_cash': {'amount': '90000', 'currency': 'CNY'}, 'set_fund': {'amount': '333.25', 'currency': 'CNY'},
                'set_capital': {'amount': '121000', 'currency': 'CNY'}}.get(operation, {})
    item.counter = getattr(item, 'counter', 0) + 1
    return {'id': ident or f'item{item.counter:04d}', 'account': account, 'operation': operation, **defaults, **fields}


def booked(ledger):
    """The card's events in the ledger, without ids and sequence numbers."""
    return [{k: v for k, v in e.items() if k not in {'id', '_sequence', 'external_id', 'source', 'date'}}
            for e in ledger.history() if e.get('source') == ledger_book.SOURCE]


def account(ledger, name='Growth'):
    return ledger.state()['accounts'][name]


def book_items(w, items, day=None, owner='owner', operation='op_one'):
    preview = w.portfolio.preview_batch(day or today(), items, owner=owner)
    return preview, w.portfolio.confirm(preview, operation)


def open_daily(w):
    sid = w.open('owner', 'daily')
    command = w.store.claim()
    w.execute(command)
    w.store.finish(command)
    return sid


def step(w, sid, action, **fields):
    w.accept(callback(w.store.session(sid), action, **fields))
    command = w.store.claim()
    assert command['action'] == action
    w.execute(command)
    w.store.finish(command)
    return w.store.session(sid)


def add(w, sid, account='Growth', operation='sell', **fields):
    step(w, sid, 'daily.add')
    step(w, sid, 'daily.item.choose', account=account, operation=operation)
    return step(w, sid, 'daily.item.save', **fields)


# -- mode -------------------------------------------------------------------------------------------

def test_the_card_records_into_the_ledger_only_while_it_has_a_confirmed_revision(book, tmp_path, monkeypatch):
    w, ledger = book
    assert w.portfolio.ledger_mode()
    overview = w.portfolio.overview()
    assert [a['value'] for a in overview['accounts']] == ['Growth', 'Reserve']
    assert overview['accounts'][0]['cash_balances'] == {'CNY': '100000', 'HKD': '1000', 'USD': '500'}
    assert overview['accounts'][0]['cost_basis'] == '120000' and overview['accounts'][0]['fund'] == '300'
    assert {p['value'] for p in overview['positions'] if p['account'] == 'Growth'} == {
        'SHA:600000', 'NASDAQ:EXAMPLE', 'HKG:00700'}
    # A ledger file whose opening was never confirmed keeps the holdings files in use.
    empty = tmp_path / 'legacy'
    (empty / 'portfolio' / 'holdings').mkdir(parents=True)
    (empty / 'scripts').mkdir()
    Ledger(empty / 'portfolio' / 'ledger.sqlite3')
    (empty / 'portfolio' / 'holdings' / f'{today()}.json').write_text(json.dumps({'date': today(), 'groups': {
        'Example': {'cash': 10, 'cash_balances': {'CNY': 10}, 'fund': 0, 'positions': []}}}))
    legacy = PortfolioAdapter(empty, tmp_path / 'legacy-state')
    assert not legacy.ledger_mode()
    receipt = legacy.confirm(legacy.preview({'operation': 'set_cash', 'date': today(), 'account': 'Example',
                                             'currency': 'CNY', 'amount': '12'}), 'legacy_one')
    assert 'mode' not in receipt
    assert json.loads((empty / 'portfolio' / 'holdings' / f'{today()}.json').read_text())['groups']['Example']['cash'] == 12


def test_a_companion_on_another_portfolio_directory_refuses_ledger_entries(book, tmp_path, monkeypatch):
    w, _ = book
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path / 'elsewhere'))
    with pytest.raises(PortfolioError, match='PORTFOLIO_DIR'):
        w.portfolio.preview_batch(today(), [item()])


def test_an_unreadable_ledger_stops_every_entry(book):
    w, ledger = book
    ledger.path.write_bytes(b'not a database')
    with pytest.raises(PortfolioError, match='无法确认交易账本状态'):
        w.portfolio.overview()


# -- item mapping -----------------------------------------------------------------------------------

def test_a_trade_with_its_fee_is_one_ledger_event_and_moves_cash_and_position(book):
    w, ledger = book
    preview, receipt = book_items(w, [item('buy', fee='5')])
    assert booked(ledger) == [{'kind': 'buy', 'account': 'Growth', 'ticker': 'SHA:600000', 'currency': 'CNY',
                               'quantity': '100', 'price': '10.5', 'fee': '5'}]
    growth = account(ledger)
    assert growth['cash_balances']['CNY'] == '98945'
    assert growth['positions']['SHA:600000']['quantity'] == '300'
    assert receipt['mode'] == 'ledger' and receipt['applied'] == 1
    assert '估算' not in ''.join(preview['changes'])


def test_an_empty_fee_is_the_fee_profile_estimate_marked_as_estimated(book):
    from core import account_profiles
    w, ledger = book
    preview = w.portfolio.preview_batch(today(), [item('sell', fee='')])
    # Default profile: commission 5 (minimum), transfer fee 0.01, stamp duty 0.6 on 1,200.
    assert '费用 5.61 CNY（估算：按默认费率估算）' in preview['changes'][0]
    assert any('费用为估算' in line for line in preview['changes'])
    account_profiles.save('Growth', {'a_share': {'commission_rate': '0.0001', 'commission_min': '0', 'waive_minimum': True}})
    preview, _ = book_items(w, [item('sell', fee='')])
    event, = booked(ledger)
    assert event['fee'] == '0.73' and event['note'] == '〔费用为估算：按账户费率估算〕'
    assert '估算：按账户费率估算' in preview['changes'][0]


def test_a_trade_without_an_estimate_needs_its_fee(book):
    w, ledger = book
    with pytest.raises(PortfolioError, match='第 1 项：未设置该市场费率，请填写成交单上的费用'):
        w.portfolio.preview_batch(today(), [item('buy', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='1',
                                                 price='20', fee='')])
    assert booked(ledger) == []


def test_a_hong_kong_trade_settled_in_hkd_stays_in_hkd(book):
    w, ledger = book
    book_items(w, [item('sell', ticker='HKG:00700', currency='HKD', quantity='10', price='310', fee='3')])
    assert booked(ledger)[0]['currency'] == 'HKD'
    assert account(ledger)['cash_balances']['HKD'] == '4097'
    assert account(ledger)['cash_balances']['CNY'] == '100000'


def test_a_stock_connect_buy_is_booked_as_the_importer_books_it(book):
    w, ledger = book
    preview, _ = book_items(w, [item('buy', ticker='HKG:00700', currency='HKD', quantity='100', price='320',
                                     fee='5', settlement_cny='29440', fee_currency='CNY')])
    # Rate 29440 / 32000 = 0.92; the CNY fee 5 is HKD 5.43 at it.
    assert booked(ledger) == [
        {'kind': 'fx', 'account': 'Growth', 'currency': 'CNY', 'amount': '29445', 'to_currency': 'HKD',
         'received_amount': '32005.43', 'note': '港股通买入 HKG:00700 的人民币交收'},
        {'kind': 'buy', 'account': 'Growth', 'ticker': 'HKG:00700', 'currency': 'HKD', 'quantity': '100',
         'price': '320', 'fee': '5.43', 'allow_margin': True,
         'note': '港股通：人民币成交 29440，费用 5 CNY 按汇率 0.92 折合 5.43 HKD'}]
    growth = account(ledger)
    assert growth['cash_balances']['CNY'] == '70555' and growth['cash_balances']['HKD'] == '1000'
    assert growth['positions']['HKG:00700']['quantity'] == '200'
    assert '港股通买入' in preview['changes'][0] and '换汇 29445 CNY → 32005.43 HKD' in preview['changes'][0]


def test_a_stock_connect_sell_converts_its_hkd_back_to_cny(book):
    w, ledger = book
    book_items(w, [item('sell', ticker='HKG:00700', currency='HKD', quantity='50', price='320',
                        fee='4', settlement_cny='14720', fee_currency='CNY')])
    sell, fx = booked(ledger)
    assert (sell['kind'], sell['fee']) == ('sell', '4.35')
    assert (fx['kind'], fx['currency'], fx['amount'], fx['to_currency'], fx['received_amount']) == (
        'fx', 'HKD', '15995.65', 'CNY', '14716')
    growth = account(ledger)
    assert growth['cash_balances']['CNY'] == '114716' and growth['cash_balances']['HKD'] == '1000'


def test_stock_connect_needs_a_plausible_cny_amount_and_a_cny_fee(book):
    w, _ = book
    hk = dict(ticker='HKG:00700', currency='HKD', quantity='100', price='320')
    with pytest.raises(PortfolioError, match='不在合理的港币汇率范围内'):
        w.portfolio.preview_batch(today(), [item('buy', fee='5', settlement_cny='32000', **hk)])
    with pytest.raises(PortfolioError, match='港股通请填写人民币手续费'):
        w.portfolio.preview_batch(today(), [item('buy', fee='', settlement_cny='29440', **hk)])
    with pytest.raises(PortfolioError, match='只用于港股通'):
        w.portfolio.preview_batch(today(), [item('buy', settlement_cny='1000')])


def test_a_stock_connect_buy_beyond_the_cny_cash_needs_margin_like_any_buy(book):
    w, ledger = book
    # HKD 400 × 320 = 128,000 at 0.92 is CNY 117,760; with the fee 117,765 against CNY 100,000. The ledger books
    # an ``fx`` whatever the cash, so the card asks what it asks of any buy.
    big = dict(ticker='HKG:00700', currency='HKD', quantity='400', price='320', fee='5', settlement_cny='117760')
    refused = '第 1 项：人民币现金 100000 CNY 不足以支付这笔港股通买入的 117765 CNY'
    with pytest.raises(PortfolioError, match=refused):
        w.portfolio.preview_batch(today(), [item('buy', **big)])
    # A transfer earlier in the list pays for it; one after it does not.
    w.portfolio.preview_batch(today(), [item('deposit', amount='20000'), item('buy', **big)])
    with pytest.raises(PortfolioError, match=refused):
        w.portfolio.preview_batch(today(), [item('buy', **big), item('deposit', amount='20000')])
    preview, _ = book_items(w, [item('buy', allow_margin='yes', **big)])
    assert '允许融资' in preview['changes'][0]
    assert any('CNY 现金为负（-17765）' in line for line in preview['changes'])
    fx, buy = booked(ledger)
    assert fx['kind'] == 'fx' and 'allow_margin' not in fx and buy['allow_margin'] is True
    assert account(ledger)['cash_balances']['CNY'] == '-17765' and account(ledger)['cash_balances']['HKD'] == '1000'


def test_deposits_and_withdrawals_move_cash_and_contributions(book):
    w, ledger = book
    book_items(w, [item('deposit', amount='250.25'), item('withdraw', account='Reserve', amount='100')])
    assert account(ledger)['cash_balances']['CNY'] == '100250.25'
    assert account(ledger)['contributed_cny'] == '120250.25'
    assert account(ledger, 'Reserve')['contributed_cny'] == '1900'
    assert [e['kind'] for e in booked(ledger)] == ['deposit', 'withdrawal']
    assert all('fx_rate' not in e for e in booked(ledger))


def test_a_foreign_currency_flow_moves_contributions_by_exactly_its_cny_principal(book):
    w, ledger = book
    preview, _ = book_items(w, [item('deposit', amount='137', currency='USD', principal_cny='1000')])
    event, = booked(ledger)
    assert event['kind'] == 'deposit' and event['currency'] == 'USD'
    assert Decimal(event['fx_rate']) * 137 == Decimal('1000.000000000000000000000000')
    assert account(ledger)['contributed_cny'] == '121000'
    assert account(ledger)['cash_balances']['USD'] == '637'
    assert '累计净投入增加 1000 CNY' in preview['changes'][0]
    with pytest.raises(PortfolioError, match='外币转入或转出请填写本次折合人民币本金'):
        w.portfolio.preview_batch(today(), [item('withdraw', amount='10', currency='USD')])


def test_a_fund_valuation_is_a_fund_value_event(book):
    w, ledger = book
    book_items(w, [item('set_fund', amount='333.25')])
    assert booked(ledger) == [{'kind': 'fund_value', 'account': 'Growth', 'currency': 'CNY', 'amount': '333.25'}]
    assert account(ledger)['fund_cny'] == '333.25' and account(ledger)['cash_balances']['CNY'] == '100000'


def test_an_unchanged_fund_valuation_books_nothing(book):
    w, ledger = book
    revision = ledger.state()['revision']
    preview, receipt = book_items(w, [item('set_fund', amount='300.00')])
    assert booked(ledger) == [] and receipt['applied'] == 0 and ledger.state()['revision'] == revision
    assert '基金合计估值已是 300 CNY，不记录' in preview['changes'][0]


@pytest.mark.parametrize('typed,kind,difference', [('100500.5', 'interest', '500.5'), ('99000', 'fee', '1000')])
def test_cash_is_now_x_books_the_difference_without_changing_contributions(book, typed, kind, difference):
    w, ledger = book
    preview, _ = book_items(w, [item('set_cash', amount=typed)])
    event, = booked(ledger)
    assert (event['kind'], event['currency'], event['amount']) == (kind, 'CNY', difference)
    assert '账本为 100000' in event['note'] and '卡片填写 CNY 余额 ' + typed in event['note']
    growth = account(ledger)
    assert growth['cash_balances']['CNY'] == typed and growth['contributed_cny'] == '120000'
    line = preview['changes'][0]
    assert '账本 100000 → 填写 ' + typed in line and kind in line and '不改变累计净投入' in line
    assert ('差额 +' if kind == 'interest' else '差额 -') + difference in line


def test_cash_is_now_x_is_the_final_balance_after_every_other_item(book):
    w, ledger = book
    items = [item('set_cash', amount='97000'), item('buy', quantity='100', price='10', fee='5')]
    preview, _ = book_items(w, items)
    trade, adjustment = booked(ledger)
    assert trade['kind'] == 'buy'
    # 100,000 − 1,005 = 98,995; the card says 97,000.
    assert (adjustment['kind'], adjustment['amount']) == ('fee', '1995')
    assert account(ledger)['cash_balances']['CNY'] == '97000'


def test_a_matching_cash_balance_books_nothing(book):
    w, ledger = book
    preview, receipt = book_items(w, [item('set_cash', amount='500', currency='USD')])
    assert booked(ledger) == [] and receipt['applied'] == 0
    assert '不记录' in preview['changes'][0] and preview['summary'].endswith('账本无需新增记录')


def test_a_cash_difference_beside_a_transfer_of_that_currency_needs_an_explanation(book):
    w, ledger = book
    items = [item('deposit', amount='500'), item('set_cash', amount='100400')]
    with pytest.raises(PortfolioError, match='第 2 项：.*请核对并填写差额说明'):
        w.portfolio.preview_batch(today(), items)
    items[1]['reconciliation_note'] = '银行到账少 100'
    book_items(w, items)
    assert booked(ledger)[1]['note'].endswith('说明：银行到账少 100')
    assert account(ledger)['contributed_cny'] == '120500'


def test_a_principal_target_is_a_cny_transfer_of_the_difference(book):
    w, ledger = book
    preview, _ = book_items(w, [item('set_capital', amount='119000')])
    event, = booked(ledger)
    assert (event['kind'], event['currency'], event['amount']) == ('withdrawal', 'CNY', '1000')
    assert account(ledger)['contributed_cny'] == '119000' and account(ledger)['cash_balances']['CNY'] == '99000'
    with pytest.raises(PortfolioError, match='选一种'):
        w.portfolio.preview_batch(today(), [item('set_capital'), item('deposit')])


# -- refusals ---------------------------------------------------------------------------------------

@pytest.mark.parametrize('refused', [
    item('set_cost_basis', amount='1000', currency='CNY'),
    item('set_capital', capital_mode='basis_correction'),
    item('set_capital', capital_mode='cash_recorded'),
    item('set_quantity', ticker='SHA:600000', quantity='1', currency='CNY'),
    item('set_cost_price', ticker='SHA:600000', price='1', currency='CNY'),
])
def test_overwrites_the_ledger_has_no_event_for_are_refused_with_the_way_to_correct(book, refused):
    w, ledger = book
    with pytest.raises(PortfolioError, match='第 1 项：账本不直接改写.*冲销.*记一笔'):
        w.portfolio.preview_batch(today(), [refused])
    assert booked(ledger) == [] and ledger.proposals() == []


def test_the_card_menu_explains_corrections_instead_of_offering_them(book):
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    choose = step(w, sid, 'daily.add')
    card = json.dumps(w.delivery.cards[choose['message']], ensure_ascii=False)
    assert '更正数量、成本或本金' in card and '本金核对（只改本金）' not in card
    explained = step(w, sid, 'daily.item.choose', account='Growth', operation='correction')
    assert explained['view'] == 'daily_item_choose' and '冲销' in explained['model']['notice']
    refused = step(w, sid, 'daily.item.choose', account='Growth', operation='set_cost_basis')
    assert refused['view'] == 'daily_item_choose' and '冲销' in refused['model']['notice']
    assert w.daily.drafts.get('owner', today())['items'] == []


def test_an_item_reversed_in_the_web_app_is_refused_rather_than_shown_as_booked(book):
    w, ledger = book
    trade, adjustment = item('buy', fee='5'), item('set_cash', currency='USD', amount='450')
    book_items(w, [trade, adjustment])
    for event in ledger.history():
        if event.get('source') == ledger_book.SOURCE:
            reversal = ledger.propose([{'kind': 'reverse', 'date': today(), 'target_id': event['id']}])
            ledger.confirm(reversal['id'])
    # The ledger would count the events as duplicates of the reversed ones and book nothing.
    for again in (trade, adjustment):
        with pytest.raises(PortfolioError, match='第 1 项：这一项入账后已在网页 流水 中冲销'):
            w.portfolio.preview_batch(today(), [again])
    growth = account(ledger)
    assert growth['cash_balances']['CNY'] == '100000' and growth['cash_balances']['USD'] == '500'


def test_a_sale_beyond_the_position_names_the_item(book):
    w, ledger = book
    with pytest.raises(PortfolioError, match='第 2 项：卖出超过了持仓'):
        w.portfolio.preview_batch(today(), [item('deposit'), item('sell', quantity='201')])


def test_a_buy_beyond_the_cash_needs_margin_and_says_so(book):
    w, ledger = book
    big = dict(ticker='NASDAQ:EXAMPLE', currency='USD', quantity='100', price='20', fee='1')
    with pytest.raises(PortfolioError, match='第 1 项：现金不足'):
        w.portfolio.preview_batch(today(), [item('buy', **big)])
    preview = w.portfolio.preview_batch(today(), [item('buy', allow_margin='yes', **big)])
    assert any('USD 现金为负（-1501）' in line for line in preview['changes'])


# -- the card flow ----------------------------------------------------------------------------------

def test_the_card_forms_ask_for_what_the_ledger_needs(book):
    w, _ = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    step(w, sid, 'daily.add')
    form = step(w, sid, 'daily.item.choose', account='Growth', operation='buy')
    assert form['model']['fee'] == '' and form['model']['ledger'] is True
    names = {n.get('name') for n in walk(w.delivery.cards[form['message']])}
    assert {'fee', 'settlement_cny', 'allow_margin', 'product', 'new_ticker'} <= names
    step(w, sid, 'daily.batch')
    step(w, sid, 'daily.add')
    form = step(w, sid, 'daily.item.choose', account='Growth', operation='set_cash')
    assert 'reconciliation_note' in {n.get('name') for n in walk(w.delivery.cards[form['message']])}


def walk(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def test_the_whole_list_is_one_proposal_confirmed_on_the_card_with_its_receipt(book):
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, product='SHA:600000', quantity_mode='partial', quantity='100', price='12', fee='', settlement_cny='')
    add(w, sid, operation='buy', product='HKG:00700', new_ticker='', quantity='100', price='320', fee='5',
        settlement_cny='29440', allow_margin='no')
    add(w, sid, account='Reserve', operation='deposit', amount='500', currency='CNY', principal_cny='')
    add(w, sid, operation='set_fund', amount='350')
    preview = step(w, sid, 'daily.batch.preview')
    assert preview['view'] == 'daily_batch_preview'
    assert booked(ledger) == []
    pending, = ledger.proposals()
    assert pending['id'] == preview['model']['_preview']['id'] and len(pending['events']) == 5
    card = json.dumps(w.delivery.cards[preview['message']], ensure_ascii=False)
    assert '确认入账并更新日报' in card and '港股通买入' in card and '估算' in card
    receipt = step(w, sid, 'daily.batch.confirm')
    assert receipt['view'] == 'daily_receipt'
    assert receipt['model']['summary'] == f'{today()} · 已入账 5 笔账本记录'
    assert any('港股通买入' in line for line in receipt['model']['changes'])
    assert [e['kind'] for e in booked(ledger)] == ['sell', 'fx', 'buy', 'deposit', 'fund_value']
    assert ledger.proposals() == []
    assert w.daily.drafts.get('owner', today())['items'] == []
    report, = w.store.recent_reports('owner')
    assert report['payload']['receipt']['mode'] == 'ledger'


def test_no_change_books_nothing_and_queues_the_report(book):
    w, ledger = book
    sid = open_daily(w)
    receipt = step(w, sid, 'daily.unchanged')
    assert receipt['view'] == 'daily_receipt' and receipt['model']['summary'] == f'{today()} · 账本没有新增记录'
    assert receipt['model']['notice'] == '账本没有新增记录。日报正在更新，可从“日报”查看进度。'
    assert booked(ledger) == [] and ledger.proposals() == []
    report, = w.store.recent_reports('owner')
    assert report['payload']['receipt']['mode'] == 'ledger'


def test_every_ledger_item_can_be_entered_on_the_card(book):
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    entries = [
        ('Growth', 'buy', dict(product='__new__', new_ticker='hkg:9988', quantity='100', price='80', fee='3',
                               settlement_cny='7360', allow_margin='no')),
        ('Growth', 'sell', dict(product='NASDAQ:EXAMPLE', quantity_mode='all', quantity='', price='9', fee='1',
                                settlement_cny='')),
        ('Growth', 'deposit', dict(amount='200', currency='USD', principal_cny='1426')),
        ('Reserve', 'withdraw', dict(amount='100', currency='CNY', principal_cny='')),
        ('Growth', 'set_fund', dict(amount='280')),
        ('Reserve', 'set_cash', dict(amount='1950', currency='CNY', reconciliation_note='利息到账')),
    ]
    for account_name, operation, fields in entries:
        saved = add(w, sid, account=account_name, operation=operation, **fields)
        assert saved['view'] == 'daily_batch', saved['model'].get('notice')
    labels = [entry['label'] for entry in saved['model']['entries']]
    assert '港股通：人民币成交 7360' in labels[0] and 'HKG:09988' in labels[0]
    step(w, sid, 'daily.batch.preview')
    step(w, sid, 'daily.batch.confirm')
    kinds = [e['kind'] for e in booked(ledger)]
    assert kinds == ['fx', 'buy', 'sell', 'deposit', 'withdrawal', 'fund_value', 'interest']
    growth = account(ledger)
    assert 'NASDAQ:EXAMPLE' not in growth['positions'] and growth['positions']['HKG:09988']['quantity'] == '100'
    assert growth['contributed_cny'] == '121426' and growth['cash_balances']['USD'] == '789'
    assert account(ledger, 'Reserve')['cash_balances']['CNY'] == '1950'
    # A principal target replaces transfers of the same account in one list.
    step(w, sid, 'daily.open')
    step(w, sid, 'daily.batch')
    saved = add(w, sid, account='Reserve', operation='set_capital', amount='2500')
    assert saved['view'] == 'daily_batch'
    step(w, sid, 'daily.batch.preview')
    step(w, sid, 'daily.batch.confirm')
    assert account(ledger, 'Reserve')['contributed_cny'] == '2500'


def test_double_taps_and_redelivered_callbacks_book_once(book):
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, operation='set_cash', amount='99000', currency='CNY', reconciliation_note='')
    step(w, sid, 'daily.batch.preview')
    event = callback(w.store.session(sid), 'daily.batch.confirm')
    assert w.accept(event)['toast']['content'] == '正在处理…'
    assert '请勿重复提交' in w.accept(event)['toast']['content']
    command = w.store.claim()
    w.execute(command)
    w.execute(command)  # redelivered after a dropped acknowledgement
    w.store.finish(command)
    assert w.store.claim() is None
    assert len(booked(ledger)) == 1 and account(ledger)['cash_balances']['CNY'] == '99000'
    assert len(w.store.recent_reports('owner')) == 1


def test_a_restart_between_preview_and_confirm_books_the_previewed_proposal_once(book):
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, operation='deposit', amount='10', currency='CNY', principal_cny='')
    step(w, sid, 'daily.batch.preview')
    restarted = Workbench(w.config, Store(w.store.path), PortfolioAdapter(w.portfolio.project_dir,
                          w.portfolio.state_dir.parent), w.research, w.delivery)
    step(restarted, sid, 'daily.batch.confirm')
    assert len(booked(ledger)) == 1 and account(ledger)['contributed_cny'] == '120010'


@pytest.mark.parametrize('crash_at', ['ledger', 'operation', 'drafts', 'delivery'])
def test_an_interrupted_confirmation_resumes_without_booking_twice(book, monkeypatch, crash_at):
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, operation='withdraw', amount='10', currency='CNY', principal_cny='')
    step(w, sid, 'daily.batch.preview')
    w.accept(callback(w.store.session(sid), 'daily.batch.confirm'))
    command = w.store.claim()
    if crash_at == 'ledger':        # the ledger committed, then the process stopped
        target, name = Ledger, 'confirm'
        original = Ledger.confirm

        def crash(self, proposal_id):
            original(self, proposal_id)
            raise DeliveryError('interrupted after the ledger committed')
    elif crash_at == 'operation':   # ... before the card recorded its operation as committed
        target, name = ledger_book.LedgerBook, '_links'
        original = ledger_book.LedgerBook.__dict__['_links']

        @staticmethod
        def crash(ledger, proposal_id, prepare):
            if not prepare:
                raise DeliveryError('interrupted before the operation was recorded')
            return original.__func__(ledger, proposal_id, prepare)
    else:                           # ... before the list was cleared, or the receipt card was sent
        target, name = (w.daily.drafts, 'complete') if crash_at == 'drafts' else (w.delivery, 'patch')
        original = getattr(target, name)

        def crash(*args, **kwargs):
            raise DeliveryError('interrupted')
    monkeypatch.setattr(target, name, crash)
    with pytest.raises(DeliveryError):
        w.execute(command)
    monkeypatch.setattr(target, name, original)
    resumed = Workbench(w.config, Store(w.store.path), w.portfolio, w.research, w.delivery)
    resumed.execute(command)
    resumed.store.finish(command)
    assert len(booked(ledger)) == 1 and account(ledger)['cash_balances']['CNY'] == '99990'
    assert resumed.daily.drafts.get('owner', today())['items'] == []
    assert len(resumed.store.recent_reports('owner')) == 1


def test_two_cards_for_one_day_book_one_list_once(book):
    w, ledger = book
    first = open_daily(w)
    step(w, first, 'daily.batch')
    add(w, first, operation='deposit', amount='100', currency='CNY', principal_cny='')
    step(w, first, 'daily.batch.preview')
    second = open_daily(w)
    step(w, second, 'daily.batch')
    step(w, second, 'daily.batch.preview')
    # The newer preview of the same person withdrew the first card's proposal: that one is made again.
    again = step(w, first, 'daily.batch.confirm')
    assert again['view'] == 'daily_batch_preview' and '重新生成预览' in again['model']['notice']
    assert booked(ledger) == []
    step(w, first, 'daily.batch.confirm')
    assert len(booked(ledger)) == 1
    with pytest.raises(Rejected, match='其他卡片'):
        step(w, second, 'daily.batch.confirm')
    assert len(booked(ledger)) == 1 and account(ledger)['contributed_cny'] == '120100'
    assert ledger.proposals() == []


def test_interleaved_confirmations_of_one_preview_share_one_receipt(book):
    w, ledger = book
    preview = w.portfolio.preview_batch(today(), [item('deposit', amount='7')])
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda n: w.portfolio.confirm(preview, f'op_{n}'), range(4)))
    assert len({json.dumps(r, sort_keys=True) for r in receipts}) == 1
    assert len(booked(ledger)) == 1
    with w.portfolio._db() as db:
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 1


def test_a_card_proposal_is_confirmed_only_on_the_card_and_then_never_booked_again(book):
    from fastapi import HTTPException
    from backend.api import ledger as api
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    # From CNY 100,000: buy 100 at 10 without a fee, and "cash is now 99,000", which needs no adjustment.
    add(w, sid, operation='buy', product='SHA:600000', new_ticker='', quantity='100', price='10', fee='0',
        settlement_cny='', allow_margin='no')
    add(w, sid, operation='set_cash', amount='99000', currency='CNY', reconciliation_note='')
    preview = step(w, sid, 'daily.batch.preview')['model']['_preview']
    # The web's 待确认 shows it as the card's and refuses to confirm it; nothing is booked there.
    assert api.proposal(preview['id'])['confirm_on'] == 'feishu-card'
    with pytest.raises(HTTPException) as refused:
        api.confirm(preview['id'])
    assert refused.value.status_code == 409 and refused.value.detail == api.CARD_ONLY
    assert booked(ledger) == []
    receipt = step(w, sid, 'daily.batch.confirm')
    assert receipt['model']['summary'] == f'{today()} · 已入账 1 笔账本记录'
    assert w.daily.drafts.get('owner', today())['items'] == [] and len(w.store.recent_reports('owner')) == 1
    # Once booked, the web shows the card's receipt; a later entry does not reopen the list's cash item.
    assert api.confirm(preview['id'])['applied'] == 1
    later = ledger.propose([{'kind': 'deposit', 'date': today(), 'account': 'Growth', 'currency': 'CNY', 'amount': '500'}])
    ledger.confirm(later['id'])
    assert w.portfolio.confirm(preview, 'card_again')['applied'] == 1
    assert [e['kind'] for e in booked(ledger)] == ['buy']
    assert account(ledger)['cash_balances']['CNY'] == '99500'


def test_a_preview_the_ledger_moved_past_is_withdrawn_and_made_again(book):
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, operation='set_cash', amount='100000', currency='USD', reconciliation_note='')
    stale = step(w, sid, 'daily.batch.preview')['model']['_preview']
    web = ledger.propose([{'kind': 'interest', 'date': today(), 'account': 'Growth', 'currency': 'USD', 'amount': '1'}])
    ledger.confirm(web['id'])
    with pytest.raises(StalePreview):
        w.portfolio.confirm(stale, 'direct')
    assert ledger.proposal(stale['id']) is None and not w.portfolio.operation_started('direct')
    fresh = step(w, sid, 'daily.batch.confirm')
    assert fresh['view'] == 'daily_batch_preview'
    assert fresh['model']['_preview']['id'] != stale['id']
    assert '账本 501 → 填写 100000' in fresh['model']['changes'][0]
    assert booked(ledger) == []
    step(w, sid, 'daily.batch.confirm')
    adjustment, = booked(ledger)
    assert adjustment['amount'] == '99499'
    assert account(ledger)['cash_balances']['USD'] == '100000'


def test_card_previews_expire_in_the_web_tray_like_manual_previews(book):
    from backend.api import ledger as api
    from core.routines import close_snapshot
    w, ledger = book
    preview = w.portfolio.preview_batch(today(), [item('deposit')])
    with sqlite3.connect(ledger.path) as db:
        expires = dict(db.execute('SELECT proposal_id, expires_at FROM proposal_expiry').fetchall())
    assert set(expires) == {preview['id']}
    assert ledger_book.PREVIEW_LIFETIME == api.MANUAL_PREVIEW_LIFETIME
    assert ledger_book.REPORT_UNTIL == close_snapshot.RETRY_UNTIL


def test_the_largest_list_fits_one_card(book):
    w, _ = book
    # 15 × CNY 29,445 is beyond the CNY cash: with margin, the longest line a buy has.
    items = [item('buy', ticker='HKG:00700', currency='HKD', quantity='100', price='320', fee='5',
                  settlement_cny='29440', allow_margin='yes') for _ in range(15)]
    items += [item('sell', ticker='SHA:600000', quantity='10', price='10.1234', fee='') for _ in range(15)]
    preview = w.portfolio.preview_batch(today(), items)
    context = CardContext('example', 1, {a: str(i) for i, a in enumerate(ACTIONS)})
    card = build_card('daily_batch_preview', {**preview, '_preview': preview}, context)
    encoded = json.dumps(card, ensure_ascii=False)
    assert encoded.count('港股通买入') == 15 and len(encoded.encode()) < 28000


def test_only_the_allowlisted_owner_of_the_card_can_confirm(book):
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, operation='deposit', amount='1', currency='CNY', principal_cny='')
    step(w, sid, 'daily.batch.preview')
    forged = callback(w.store.session(sid), 'daily.batch.confirm')
    forged['event']['operator']['open_id'] = 'stranger'
    with pytest.raises(Rejected, match='没有此工作台的操作权限'):
        w.accept(forged)
    assert booked(ledger) == []


def test_the_one_item_form_books_through_the_ledger_too(book):
    from tests.test_feishu_workbench import session_card
    w, ledger = book
    session = session_card(w)
    w.accept(callback(session, 'daily.choose', date=today(), account='Growth', operation='set_cash'))
    command = w.store.claim()
    w.execute(command)
    w.store.finish(command)
    session = w.store.session(session['id'])
    w.accept(callback(session, 'daily.preview', amount='450', currency='USD'))
    command = w.store.claim()
    w.execute(command)
    w.store.finish(command)
    session = w.store.session(session['id'])
    assert session['view'] == 'daily_preview' and '差额 -50' in session['model']['changes'][0]
    w.accept(callback(session, 'daily.confirm'))
    command = w.store.claim()
    w.execute(command)
    w.store.finish(command)
    receipt = w.store.session(session['id'])
    assert receipt['view'] == 'daily_receipt'
    assert receipt['model']['notice'] == '账本已入账。日报正在更新，可从“日报”查看进度。'
    assert booked(ledger) == [{'kind': 'fee', 'account': 'Growth', 'currency': 'USD', 'amount': '50',
                               'note': '现金对账调整：卡片填写 USD 余额 450，账本为 500，差额 -50'}]
    assert len(w.store.recent_reports('owner')) == 1


def test_the_one_item_form_says_when_a_backdated_report_is_not_remade(book):
    from tests.test_feishu_workbench import session_card
    w, ledger = book
    session = session_card(w)
    for action, fields in [('daily.choose', dict(date='2026-02-02', account='Growth', operation='set_cash')),
                           ('daily.preview', dict(amount='450', currency='USD')), ('daily.confirm', {})]:
        w.accept(callback(session, action, **fields))
        command = w.store.claim()
        w.execute(command)
        w.store.finish(command)
        session = w.store.session(session['id'])
    assert session['view'] == 'daily_receipt' and len(booked(ledger)) == 1
    assert session['model']['notice'] == ledger_book.BACKDATED.format(day='2026-02-02')
    assert w.store.recent_reports('owner') == []
    assert ledger_book.receipt_notice({'applied': 0, 'date': '2026-02-02'}, False).startswith('账本没有新增记录')


def test_assets_show_a_snapshot_valuation_only_for_the_ledger_revision_it_was_made_from(book):
    w, ledger = book
    state = ledger.state()
    folder = w.portfolio.portfolio_dir / 'snapshots'
    folder.mkdir()
    (folder / f"{state['date']}.json").write_text(json.dumps({
        'date': state['date'], 'ledger_revision': state['revision'], 'generated_at': f"{state['date']}T16:00:00",
        'summary': {'total_value': 123456.78, 'market_daily_change': 12.5},
        'groups': {'Growth': {'positions': [{'ticker': 'SHA:600000', 'market_value_cny': 2400}]},
                   'Reserve': {'positions': []}}}))
    overview = w.portfolio.overview()
    assert overview['total'] == '¥123,456.78' and overview['change'] == '¥12.50'
    assert next(p for p in overview['positions'] if p['ticker'] == 'SHA:600000')['market_value'] == '¥2,400.00'
    book_items(w, [item('deposit', amount='1')])
    overview = w.portfolio.overview()
    assert overview['total'] == '等待日报估值'
    assert all('market_value' not in p for p in overview['positions'])


# -- dates ------------------------------------------------------------------------------------------

def test_an_entry_dated_before_existing_ones_follows_the_ledger_rules(book):
    w, ledger = book
    later = ledger.propose([{'kind': 'buy', 'date': today(), 'account': 'Reserve', 'ticker': 'SHE:000001',
                             'currency': 'CNY', 'quantity': '100', 'price': '15', 'fee': '0'}])
    ledger.confirm(later['id'])
    earlier = '2026-02-02'
    # On that date Reserve still had 2,000: the difference is taken there, and the later buy still has its cash.
    preview, receipt = book_items(w, [item('set_cash', account='Reserve', amount='2500')], day=earlier)
    event, = booked(ledger)
    assert event['amount'] == '500' and ledger.history()[-1]['date'] == earlier
    assert account(ledger, 'Reserve')['cash_balances']['CNY'] == '1000'
    with pytest.raises(PortfolioError, match='现金不足'):
        w.portfolio.preview_batch(earlier, [item('withdraw', account='Reserve', amount='2400')])


def test_an_entry_dated_before_the_account_was_opened_is_refused(book):
    w, _ = book
    with pytest.raises(PortfolioError, match='账户不存在'):
        w.portfolio.preview_batch('2025-12-31', [item('deposit')])


def test_old_entries_do_not_remake_old_reports():
    day = '2026-11-18'
    assert ledger_book.report_window_open(day, datetime(2026, 11, 19, 8, 59, tzinfo=ledger_book.SHANGHAI))
    assert not ledger_book.report_window_open(day, datetime(2026, 11, 19, 9, 0, tzinfo=ledger_book.SHANGHAI))


def test_a_backdated_booking_says_its_report_is_not_remade(book, monkeypatch):
    w, ledger = book
    called = []
    monkeypatch.setattr(w.portfolio, '_execute_pipeline', lambda day, resume, push: called.append(day) or True)
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    step(w, sid, 'daily.date')
    step(w, sid, 'daily.date.save', date='2026-02-02')
    add(w, sid, operation='deposit', amount='1', currency='CNY', principal_cny='')
    step(w, sid, 'daily.batch.preview')
    receipt = step(w, sid, 'daily.batch.confirm')
    assert '补记' in receipt['model']['notice'] and w.store.recent_reports('owner') == []
    _, direct = book_items(w, [item('deposit', amount='2')], day='2026-02-02', operation='direct')
    assert w.portfolio.run_report(direct)['report_status'] == 'skipped' and called == []


# -- the report -------------------------------------------------------------------------------------

def test_the_report_runs_once_per_booking(book, monkeypatch):
    w, _ = book
    called = []
    monkeypatch.setattr(w.portfolio, '_execute_pipeline',
                        lambda day, resume, push: called.append((day, resume, push)) or True)
    _, receipt = book_items(w, [item('deposit')])
    assert w.portfolio.run_report(receipt)['report_status'] == 'complete'
    assert w.portfolio.run_report(receipt)['report_status'] == 'complete'
    assert called == [(today(), False, 'configured')]
    with pytest.raises(PortfolioError, match='尚未确认'):
        w.portfolio.run_report({'operation_id': 'missing', 'mode': 'ledger'})


# A Wednesday every market of the test ledger trades: the mainland closes at 15:00, Hong Kong's close is final at
# 16:10 and New York's session of that date at 05:30 the next Beijing morning (16:30 EST).
DAY = '2026-11-18'


def at(moment):
    return datetime.fromisoformat(moment)


def test_a_report_the_snapshot_engine_holds_back_waits_for_the_time_it_names(book, monkeypatch):
    w, _ = book
    monkeypatch.setattr(ledger_book, '_now', lambda: at('2026-11-19T06:00:00+08:00'))
    calls = []

    def waiting(day, resume, push):
        calls.append(day)
        w.portfolio._held.output = 'snapshot failed\nCLOSE_NOT_FINAL_UNTIL=2026-11-19T06:10:00+08:00 MARKETS=US\n'
        return False
    monkeypatch.setattr(w.portfolio, '_execute_pipeline', waiting)
    _, receipt = book_items(w, [item('deposit')], day=DAY)
    result = w.portfolio.run_report(receipt)
    assert result['report_status'] == 'waiting' and calls == [DAY]
    assert result['summary'] == ledger_book.WAITING.format(booked='账本已入账。', day=DAY, until='次日 06:10')
    assert w.portfolio.due_reports() == []
    assert w.portfolio.due_reports(at('2026-11-19T06:10:00+08:00')) == [receipt['operation_id']]
    monkeypatch.setattr(ledger_book, '_now', lambda: at('2026-11-19T06:10:00+08:00'))
    monkeypatch.setattr(w.portfolio, '_execute_pipeline', lambda day, resume, push: False)
    with pytest.raises(PortfolioError, match='日报尚未完成'):
        w.portfolio.run_report(receipt)
    assert w.portfolio.due_reports() == []


ENGINE = Path(__file__).parents[1] / 'engine' / 'scripts'
# The real ``run_pipeline`` state machine with the snapshot, the report text and the push replaced (as in
# test_feishu_report_lock_scope); the holdings it hashes are the test ledger's (ledger_bridge).
SHIM = '''
import json
import sys
import time
from pathlib import Path

ENGINE = Path({engine!r})
sys.path.insert(0, str(ENGINE))
exec(compile((ENGINE / 'portfolio_daily_update.py').read_text(encoding='utf-8'),
             str(ENGINE / 'portfolio_daily_update.py'), 'exec'))

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_DIR = ROOT / 'portfolio'
REPORTS_DIR = ROOT / 'reports'
SNAPSHOTS_DIR = PORTFOLIO_DIR / 'snapshots'
CONTROL = ROOT / 'control'


def _stage(name):
    with open(CONTROL / 'stages', 'a') as stream:
        stream.write(name + chr(10))
    (CONTROL / (name + '-started')).write_text('')
    if (CONTROL / ('hold-' + name)).exists():
        deadline = time.time() + 60
        while not (CONTROL / ('release-' + name)).exists():
            if time.time() > deadline:
                return False
            time.sleep(.02)
    return True


def load_config():
    path = CONTROL / 'config.json'
    return json.loads(path.read_text()) if path.exists() else {{'feishu_chat_id': 'synthetic'}}


def run_snapshot(day):
    return _stage('snapshot')


def run_report(day):
    REPORTS_DIR.mkdir(exist_ok=True)
    target = REPORTS_DIR / ('portfolio-' + day.replace('-', '') + '.md')
    target.write_text('Synthetic report of revision ' + str(holdings_digest(day))[:12])
    return target


def send_feishu_report(day, text, config):
    return _stage('delivery')
'''


@pytest.fixture
def pipeline(book):
    w, ledger = book
    project = w.portfolio.project_dir
    (project / 'control').mkdir()
    (project / 'scripts' / 'portfolio_daily_update.py').write_text(SHIM.format(engine=str(ENGINE)))
    return w, ledger


def stages(w):
    path = w.portfolio.project_dir / 'control' / 'stages'
    return path.read_text().split() if path.exists() else []


def close_snapshot(w, day):
    """``--action auto-pipeline`` as the backend's close snapshot runs it, under the same lock."""
    code = ('import sys; sys.path.insert(0, sys.argv[1]); import portfolio_daily_update as p; '
            'import portfolio_write_lock as l\n'
            'with l.portfolio_write_lock(p.PORTFOLIO_DIR):\n'
            '    sys.exit(0 if p.action_auto_pipeline(sys.argv[2]) else 1)')
    scripts = w.portfolio.scripts_dir
    return subprocess.run([sys.executable, '-c', code, str(scripts), day], cwd=scripts, capture_output=True,
                          timeout=120).returncode


def run_reports(w):
    """The report worker: every queued daily.report command, as ``Workbench.worker(reports=True)`` runs it."""
    while True:
        command = w.store.claim(reports=True)
        if not command:
            return
        try:
            w.execute(command)
        except PortfolioError as exc:
            w.store.finish(command, str(exc))
        else:
            w.store.finish(command)


def test_an_evening_entry_remakes_the_days_report_and_nothing_is_pushed_twice(pipeline):
    w, _ = pipeline
    day = today()
    assert close_snapshot(w, day) == 0                      # 15:40: the day's report for the morning's holdings
    assert stages(w) == ['snapshot', 'delivery']
    _, receipt = book_items(w, [item('deposit', amount='5000')])
    done = w.portfolio.run_report(receipt)
    assert done['report_status'] == 'complete' and done['summary'] == '日报已完成。'
    assert w.portfolio.report_note(receipt) is None
    assert stages(w) == ['snapshot', 'delivery'] * 2        # new holdings: snapshot, report and push again
    assert w.portfolio.run_report(receipt)['report_status'] == 'complete'
    assert close_snapshot(w, day) == 0                      # a retry of the close snapshot: same holdings
    assert stages(w) == ['snapshot', 'delivery'] * 2
    state = json.loads((w.portfolio.portfolio_dir / 'pipeline-state' / f'{day}.json').read_text())
    assert state['push'] == 'complete'
    _, second = book_items(w, [item('deposit', amount='1')], operation='op_two')
    w.portfolio.run_report(second)
    assert stages(w) == ['snapshot', 'delivery'] * 3


def test_without_a_chat_id_the_card_saves_the_report_as_the_close_pipeline_does(pipeline):
    w, _ = pipeline
    day = today()
    (w.portfolio.project_dir / 'control' / 'config.json').write_text(json.dumps({}))
    checkpoint = w.portfolio.portfolio_dir / 'pipeline-state' / f'{day}.json'
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, account='Reserve', operation='deposit', amount='5000', currency='CNY', principal_cny='')
    step(w, sid, 'daily.batch.preview')
    step(w, sid, 'daily.batch.confirm')
    run_reports(w)
    report, = w.store.recent_reports('owner')
    assert (report['status'], report['error']) == ('completed', '')
    assert stages(w) == ['snapshot']                        # made from the new holdings; nothing is delivered
    first = json.loads(checkpoint.read_text())
    # The close pipeline's "saved, not pushed": complete, and no push claimed that could wait forever.
    assert (first['report'], first['complete'], first['push']) == (True, True, 'not_started')
    listing = step(w, sid, 'reports')
    entry = listing['model']['items'][0]
    assert entry == {'title': '日报更新 · 已完成', 'summary': ledger_book.SAVED}
    card = json.dumps(w.delivery.cards[listing['message']], ensure_ascii=False)
    assert '日报已按入账后的持仓生成并保存' in card and '未完成' not in card and '已推送' not in card
    # A second entry the same day: new holdings, so the snapshot and report are made again, still saved only.
    _, second = book_items(w, [item('deposit', amount='1')], operation='op_two')
    result = w.portfolio.run_report(second)
    assert (result['report_status'], result['summary']) == ('complete', ledger_book.SAVED)
    assert stages(w) == ['snapshot', 'snapshot']
    again = json.loads(checkpoint.read_text())
    assert again['holdings_hash'] != first['holdings_hash']
    assert (again['report'], again['complete'], again['push']) == (True, True, 'not_started')
    # The close snapshot for the same holdings finds the day done; asking again repeats nothing.
    assert close_snapshot(w, day) == 0 and stages(w) == ['snapshot', 'snapshot']
    assert w.portfolio.run_report(second)['summary'] == ledger_book.SAVED
    assert stages(w) == ['snapshot', 'snapshot']


def test_a_card_confirmation_waits_for_the_close_snapshot_stage(pipeline):
    w, ledger = pipeline
    _, receipt = book_items(w, [item('deposit')])
    preview = w.portfolio.preview_batch(today(), [item('deposit', amount='3')], owner='other')
    control = w.portfolio.project_dir / 'control'
    (control / 'hold-snapshot').write_text('')
    with ThreadPoolExecutor(max_workers=2) as pool:
        report = pool.submit(w.portfolio.run_report, receipt)
        try:
            deadline = time.time() + 60
            while not (control / 'snapshot-started').exists():
                assert time.time() < deadline and not report.done()
                time.sleep(.02)
            confirmation = pool.submit(w.portfolio.confirm, preview, 'op_three')
            with pytest.raises(FutureTimeout):
                confirmation.result(timeout=1)
            assert len(booked(ledger)) == 1
        finally:
            (control / 'release-snapshot').write_text('')
        assert report.result(timeout=60)['report_status'] == 'complete'
        assert confirmation.result(timeout=30)['applied'] == 1


# -- the reminder -----------------------------------------------------------------------------------

def test_the_evening_reminder_opens_the_card_without_cloning_holdings(book, monkeypatch):
    import feishu_workbench_notify
    import portfolio_daily_update as daily
    w, _ = book
    folder = w.portfolio.portfolio_dir
    (folder / 'config.json').write_text(json.dumps({'feishu_chat_id': 'user:owner'}))
    monkeypatch.setattr(daily, 'PORTFOLIO_DIR', folder)
    monkeypatch.setattr(daily, 'CONFIG_PATH', folder / 'config.json')
    monkeypatch.setattr(daily, 'HOLDINGS_DIR', folder / 'holdings')
    sent = []
    monkeypatch.setattr(feishu_workbench_notify, 'try_daily_card', lambda day, target: sent.append((day, target)) or True)
    assert daily.action_notify(today())
    assert sent == [(today(), 'user:owner')]
    assert not (folder / 'holdings').exists()


# -- review follow-ups ------------------------------------------------------------------------------

def service(tmp_path, project):
    """The companion as ``app.create_app`` builds it (its one choice of portfolio directory)."""
    from feishu_workbench.app import create_app
    app = create_app({'state_dir': str(tmp_path / 'service'), 'project_dir': str(project),
                      'allowed_users': ['owner'], 'account_id': 'qr'})
    return app.state.controller.portfolio


def test_the_service_uses_the_configured_portfolio_and_never_writes_old_holdings_beside_it(tmp_path, monkeypatch):
    import os
    project = tmp_path / 'project'
    old = project / 'portfolio' / 'holdings' / f'{today()}.json'
    old.parent.mkdir(parents=True)
    (project / 'scripts').mkdir()
    old.write_text(json.dumps({'date': today(), 'groups': {
        'Growth': {'cash': 100000, 'cash_balances': {'CNY': 100000}, 'fund': 0, 'positions': []}}}))
    before = old.read_bytes()
    external = tmp_path / 'external'
    ledger = Ledger(external / 'ledger.sqlite3')
    ledger.confirm(ledger.propose([opening('Growth', {'CNY': '100000'}, '100000')])['id'])
    monkeypatch.setenv('PORTFOLIO_DIR', str(external))
    monkeypatch.delenv('QR_PORTFOLIO_DIR', raising=False)
    adapter = service(tmp_path, project)
    assert adapter.portfolio_dir == external.resolve() and adapter.ledger_mode()
    assert os.environ['PORTFOLIO_DIR'] == str(external.resolve())
    receipt = adapter.confirm(adapter.preview_batch(today(), [item('deposit', amount='500')], owner='owner'), 'op')
    adapter.confirm(adapter.preview({'operation': 'set_cash', 'date': today(), 'account': 'Growth',
                                     'currency': 'CNY', 'amount': '100600'}), 'op_form')
    assert receipt['applied'] == 1 and ledger.state()['accounts']['Growth']['cash_balances']['CNY'] == '100600'
    assert old.read_bytes() == before
    # QR_PORTFOLIO_DIR names it as well (the backend's rule).
    monkeypatch.delenv('PORTFOLIO_DIR')
    monkeypatch.setenv('QR_PORTFOLIO_DIR', str(external))
    assert PortfolioAdapter.configured(project, tmp_path / 'other').portfolio_dir == external.resolve()


PROBE = '''
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, {engine!r})
PORTFOLIO_DIR = Path(os.environ.get('PORTFOLIO_DIR', ''))


def load_config():
    return json.loads((PORTFOLIO_DIR / 'config.json').read_text())


def send_feishu_report(day, text, config):
    return True


def run_pipeline(day, send_report=True, resume=False):
    # The engine reads everything from PORTFOLIO_DIR; from the scripts directory a relative one is elsewhere.
    return PORTFOLIO_DIR.is_absolute() and (PORTFOLIO_DIR / 'ledger.sqlite3').is_file()
'''


def test_a_relative_portfolio_dir_is_made_absolute_once_for_bookings_and_reports(book, tmp_path, monkeypatch):
    w, ledger = book
    project = w.portfolio.project_dir
    (project / 'portfolio' / 'config.json').write_text('{}')
    (project / 'scripts' / 'portfolio_daily_update.py').write_text(PROBE.format(engine=str(ENGINE)))
    monkeypatch.chdir(project)
    monkeypatch.setenv('PORTFOLIO_DIR', 'portfolio')
    adapter = service(tmp_path, project)
    assert adapter.portfolio_dir == (project / 'portfolio').resolve()
    receipt = adapter.confirm(adapter.preview_batch(today(), [item('deposit', amount='10')]), 'op_relative')
    result = adapter.run_report(receipt)
    assert result['report_status'] == 'complete' and result['summary'] == ledger_book.SAVED
    assert account(ledger)['cash_balances']['CNY'] == '100010'


def test_replacing_or_clearing_a_list_withdraws_its_earlier_proposal_even_with_nothing_left_to_book(book):
    from core.ledger import StaleProposal
    w, ledger = book
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    add(w, sid, operation='set_cash', amount='99000', currency='CNY', reconciliation_note='')
    first = step(w, sid, 'daily.batch.preview')['model']['_preview']
    assert [p['id'] for p in ledger.proposals()] == [first['id']]
    # Correct the target to the ledger's own balance: the edit withdraws the CNY 1,000 fee proposal.
    step(w, sid, 'daily.batch')
    step(w, sid, 'daily.item.edit', item_id=w.daily.drafts.get('owner', today())['items'][0]['id'])
    step(w, sid, 'daily.item.save', amount='100000', currency='CNY', reconciliation_note='')
    assert ledger.proposals() == []
    corrected = step(w, sid, 'daily.batch.preview')
    assert corrected['model']['summary'] == f'{today()} · 账本无需新增记录'
    assert step(w, sid, 'daily.batch.confirm')['model']['summary'] == f'{today()} · 账本没有新增记录'
    with pytest.raises(StaleProposal):
        ledger.confirm(first['id'])
    # A new preview that books nothing withdraws a pending one too, and so does clearing the list.
    pending = w.portfolio.preview_batch(today(), [item('set_cash', amount='99000')], owner='owner')
    w.portfolio.preview_batch(today(), [item('set_cash', amount='100000')], owner='owner')
    assert ledger.proposal(pending['id']) is None
    step(w, sid, 'daily.batch')
    add(w, sid, operation='deposit', amount='5', currency='CNY', principal_cny='')
    step(w, sid, 'daily.batch.preview')
    assert len(ledger.proposals()) == 1
    step(w, sid, 'daily.batch')
    step(w, sid, 'daily.discard')
    assert ledger.proposals() == []
    assert booked(ledger) == [] and account(ledger)['cash_balances']['CNY'] == '100000'


def test_a_confirmation_that_books_nothing_never_makes_a_finished_report_again(pipeline):
    w, _ = pipeline
    day = today()
    yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    assert close_snapshot(w, yesterday) == 0
    assert stages(w) == ['snapshot', 'delivery']
    book_items(w, [item('deposit', amount='100')], operation='today_entry')    # a new revision, for today only
    # At 08:05 the evening card for yesterday is answered: the balance is unchanged.
    _, receipt = book_items(w, [item('set_cash', account='Reserve', amount='2000')], day=yesterday,
                            operation='unchanged')
    assert receipt['applied'] == 0
    morning = datetime.fromisoformat(f'{day}T08:05:00+08:00')
    result = ledger_book.run_report(w.portfolio, 'unchanged', now=morning)
    assert (result['report_status'], result['summary']) == ('complete', ledger_book.UNCHANGED.format(day=yesterday))
    assert stages(w) == ['snapshot', 'delivery']                               # nothing made or pushed again
    assert w.portfolio.report_note(receipt) == ledger_book.UNCHANGED.format(day=yesterday)
    assert ledger_book.run_report(w.portfolio, 'unchanged', now=morning)['summary'] == result['summary']
    # A day without a finished report still gets its first one from 今日无变动.
    _, first = book_items(w, [item('set_cash', account='Reserve', amount='2000')], operation='first_report')
    assert w.portfolio.run_report(first)['report_status'] == 'complete'
    assert stages(w) == ['snapshot', 'delivery'] * 2


def test_hkd_owed_before_does_not_block_a_stock_connect_buy_paid_in_cny(book):
    w, ledger = book
    owed = ledger.propose([{'kind': 'fee', 'date': today(), 'account': 'Growth', 'currency': 'HKD', 'amount': '1100'}])
    ledger.confirm(owed['id'])
    book_items(w, [item('buy', ticker='HKG:00700', currency='HKD', quantity='100', price='320', fee='5',
                        settlement_cny='29440')])
    growth = account(ledger)
    assert growth['cash_balances']['CNY'] == '70555' and growth['cash_balances']['HKD'] == '-100'
    assert growth['positions']['HKG:00700']['quantity'] == '200'


def test_a_receipt_shown_again_says_what_was_reversed_since(book):
    w, ledger = book
    preview, receipt = book_items(w, [item('deposit', amount='100')], operation='op_rev')
    assert receipt['summary'] == f'{today()} · 已入账 1 笔账本记录'
    event = next(e for e in ledger.history() if e.get('source') == ledger_book.SOURCE)
    ledger.confirm(ledger.propose([{'kind': 'reverse', 'date': today(), 'target_id': event['id']}])['id'])
    again = w.portfolio.confirm(preview, 'op_rev')
    assert again['summary'] == ledger_book.REVERSED_LATER.format(day=today(), applied=1, count=1)
    assert again['changes'][0].startswith(again['summary'])
    assert w.portfolio.confirm(preview, 'another_tap')['reversed'] == 1
    assert account(ledger)['cash_balances']['CNY'] == '100000' and len(booked(ledger)) == 1


# -- before the day's closes: the report waits, and the card service runs it itself -------------------------

def entered_on(w, day, **fields):
    """A list with one 资金转入 for ``day``, confirmed on the card; returns the card's session id."""
    sid = open_daily(w)
    step(w, sid, 'daily.batch')
    step(w, sid, 'daily.date')
    step(w, sid, 'daily.date.save', date=day)
    add(w, sid, operation='deposit', amount=fields.get('amount', '100'), currency='CNY', principal_cny='')
    step(w, sid, 'daily.batch.preview')
    step(w, sid, 'daily.batch.confirm')
    return sid


@pytest.fixture
def waiting(book, monkeypatch):
    """The test ledger with a settable clock and a pipeline that only records that it ran."""
    w, ledger = book
    clock = {'now': at('2026-11-18T22:30:00+08:00')}      # an evening entry: New York trades until 05:30
    monkeypatch.setattr(ledger_book, '_now', lambda: clock['now'])
    ran = []
    monkeypatch.setattr(PortfolioAdapter, '_execute_pipeline',
                        lambda self, day, resume, push='always': ran.append(day) or True)
    return w, ledger, clock, ran


def test_before_the_days_closes_an_entry_is_booked_and_its_report_waits(waiting):
    w, ledger, clock, ran = waiting
    clock['now'] = at('2026-11-18T06:00:00+08:00')         # before the mainland opens: yesterday's prices only
    sid = entered_on(w, DAY)
    receipt = w.store.session(sid)
    expected = ledger_book.WAITING.format(booked='账本已入账。', day=DAY, until='次日 05:30')
    assert receipt['view'] == 'daily_receipt' and receipt['model']['notice'] == expected
    assert account(ledger)['contributed_cny'] == '120100'
    run_reports(w)
    assert ran == []                                         # no snapshot of the day from the day before's prices
    report, = w.store.recent_reports('owner')
    assert (report['status'], report['error']) == ('completed', '')
    listing = step(w, sid, 'reports')
    assert listing['model']['items'][0] == {'title': '日报更新 · 等待收盘', 'summary': expected}
    assert listing['model']['can_retry_report']
    card = json.dumps(w.delivery.cards[listing['message']], ensure_ascii=False)
    assert '等待收盘' in card and '未完成' not in card
    # 今日无变动 before the close books nothing and waits the same way.
    _, unchanged = book_items(w, [item('set_cash', account='Reserve', amount='2000')], day=DAY, operation='early')
    result = w.portfolio.run_report(unchanged)
    assert result['report_status'] == 'waiting' and ran == []
    assert result['summary'] == ledger_book.WAITING.format(booked='账本没有新增记录。', day=DAY, until='次日 05:30')
    # Only the markets the ledger holds count: with the US position sold, the day waits for Hong Kong only.
    book_items(w, [item('sell', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='10', price='9', fee='1')],
               day=DAY, operation='sold')
    assert ledger_book.waits_until(w.portfolio, DAY, at('2026-11-18T16:00:00+08:00')) == at('2026-11-18T16:10:00+08:00')
    assert ledger_book.waits_until(w.portfolio, DAY, at('2026-11-18T16:10:00+08:00')) is None


def test_a_waiting_report_is_run_once_when_its_time_comes_even_with_a_tap(waiting):
    w, ledger, clock, ran = waiting
    sid = entered_on(w, DAY)
    run_reports(w)
    assert ran == [] and w.release_due_reports() == 0
    clock['now'] = at('2026-11-19T05:31:00+08:00')
    step(w, sid, 'reports')
    assert w.release_due_reports() == 1                      # the timer queues the job ...
    step(w, sid, 'daily.report.retry')                       # ... and a tap at the same time adds nothing
    assert w.release_due_reports() == 0
    run_reports(w)
    assert ran == [DAY]
    assert w.release_due_reports() == 0
    listing = step(w, sid, 'reports')
    assert listing['model']['items'][0] == {'title': '日报更新 · 已完成', 'summary': ledger_book.SAVED}
    assert not listing['model']['can_retry_report']
    # A tap before the time runs the job, which only waits again; nothing is made twice.
    clock['now'] = at('2026-11-18T23:00:00+08:00')
    sid = entered_on(w, DAY, amount='5')
    run_reports(w)
    step(w, sid, 'reports')
    step(w, sid, 'daily.report.retry')
    run_reports(w)
    assert ran == [DAY]
    clock['now'] = at('2026-11-19T05:40:00+08:00')
    assert w.release_due_reports() == 1
    run_reports(w)
    assert ran == [DAY, DAY]


def test_a_waiting_report_survives_a_restart_of_the_card_service(waiting):
    w, _, clock, ran = waiting
    entered_on(w, DAY)
    run_reports(w)
    # The service was stopped and comes back after the time: the job is found in its stored state.
    clock['now'] = at('2026-11-19T07:15:00+08:00')
    restarted = Workbench(w.config, Store(w.store.path), PortfolioAdapter(w.portfolio.project_dir,
                          w.portfolio.state_dir.parent), w.research, w.delivery)
    assert restarted.release_due_reports() == 1
    run_reports(restarted)
    assert ran == [DAY] and restarted.release_due_reports() == 0


def test_a_waiting_report_stops_when_the_days_window_ends(waiting):
    w, _, clock, ran = waiting
    sid = entered_on(w, DAY)
    run_reports(w)
    clock['now'] = at('2026-11-19T09:00:00+08:00')         # back only when the day may no longer be remade
    assert w.release_due_reports() == 1
    run_reports(w)
    assert ran == [] and w.release_due_reports() == 0
    listing = step(w, sid, 'reports')
    assert listing['model']['items'][0] == {'title': '日报更新 · 未生成', 'summary': ledger_book.EXPIRED.format(day=DAY)}
    assert not listing['model']['can_retry_report']


def test_the_service_starts_its_report_timer(book):
    import asyncio
    w, _ = book

    async def started():
        await w.start()
        try:
            return [task.get_coro().__name__ for task in w.tasks]
        finally:
            await w.stop()
    assert 'report_timer' in asyncio.run(started())

