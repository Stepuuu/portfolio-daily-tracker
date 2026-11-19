"""Gaps the import page found in the importer's payload: texts, ``needs``, identity counts, duplicates and paging.

Each section is one gap reported by the page that renders ``/api/imports`` (the contract is the module docstring
of ``dashboard/backend/api/imports.py``).
"""
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import imports as api
from core.importers import session
from core.importers.mapping import OTHER_CURRENCIES, TEMPLATE_COLUMNS
from core.ledger import StaleProposal, encode
from tests.importer_support import ACCOUNT, book, csv, ledger_at, open_account, patch, run
from tests.test_importer_adversarial import BUY, CASES, CASH, FUNDS, LEND, BACK, SPLIT, SPLIT_HEADER, TRADE, changed

NOT_HERE = '无法在这里修改'


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger, cash_balances={'CNY': '500000.00', 'HKD': '100000.00', 'USD': '10000.00'})
    return ledger


DEPOSITS = csv(FUNDS, ['2026-11-03', '银行转证券', '10.00'], ['2026-11-04', '银行转证券', '20.00'])
IDENTITY = ['发生日期', '业务名称', '发生金额', '资金账号']
TEMPLATE = ['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee', 'amount']


# -- 1. cash to the cent (the cases are in test_importer_adversarial.py): earlier bookings stay 已导入 ----------

def _insert(ledger, event):
    """An event as an earlier release booked it (the ledger takes ``import:`` sources only from a bound preview)."""
    with ledger.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO events(id, payload, proposal_id) VALUES (?, ?, ?)', (event['id'], encode(event), 'earlier'))
        db.execute('UPDATE ledger_meta SET revision = revision + 1 WHERE id = 1')


ROUNDED = csv(TRADE, changed(TRADE, BUY, 成交数量='300', 成交价格='32.017', 成交金额='9605.00', 发生金额='-9610.01'))


@pytest.mark.parametrize('earlier, status', [({'price': '32.0166666667', 'fee': '5'}, 'imported'),
                                             ({'price': '32.0166666667', 'fee': '6'}, 'changed')],
                         ids=['booked under the earlier rules', 'booked with another fee'])
def test_a_trade_booked_under_the_earlier_price_and_fee_rules_is_already_imported(ledger, earlier, status):
    _c, _view, rows = run(ROUNDED, 'b.csv')
    event = rows[2]['events'][0]
    assert (event['price'], event['fee']) == ('32.016666666666666666666666667', '5.01'), event
    _insert(ledger, {**event, **earlier})
    _c, view, rows = run(ROUNDED, 'b.csv')
    assert rows[2]['status'] == status, rows[2]['messages']
    assert view['counts']['included_events'] == 0
    if status == 'imported':
        assert '旧规则' in rows[2]['messages'][-1], rows[2]['messages']


# -- 2. texts: the page's words, no API names, no English -----------------------------------------------------

def _withdrawn_by_a_patch(ledger):
    created, _view, _rows = run(DEPOSITS, 'f.csv')
    proposal = session.propose(created['id'])['proposal']
    patch(created['id'], rows=[{'row': 2, 'include': False}])
    return proposal['id']


def _ledger_changed(ledger):
    created, _view, _rows = run(DEPOSITS, 'f.csv')
    proposal = session.propose(created['id'])['proposal']
    book(ledger, [{'kind': 'deposit', 'date': '2026-11-05', 'account': ACCOUNT, 'currency': 'CNY', 'amount': '1'}])
    return proposal['id']


@pytest.mark.parametrize('prepare', [_withdrawn_by_a_patch, _ledger_changed], ids=['withdrawn', 'ledger changed'])
def test_a_refused_import_confirmation_speaks_of_the_entry_preview(ledger, prepare):
    proposal_id = prepare(ledger)
    with pytest.raises(StaleProposal) as raised:
        ledger.confirm(proposal_id)
    message = str(raised.value)
    assert '入账预览已作废' in message and '请重新生成入账预览' in message, message
    assert '导入页面' not in message and '提案' not in message and not re.search('[A-Za-z]', message), message


def test_identity_reasons_and_blockers_name_no_api_field(ledger):
    _c, view, rows = run(csv(IDENTITY, ['2026-11-03', '银行转证券', '10', '111'], ['2026-11-04', '银行转证券', '20', '222'],
                             ['2026-11-04', '银行转证券', '20', '111'], ['2026-11-05', '银行转证券', '30', '']), 'm.csv')
    texts = view['blockers'] + [m for row in rows.values() for m in row['messages']]
    assert any('账户识别' in t for t in texts)
    assert not [t for t in texts if 'account_filter' in t], texts


def test_holdings_rows_have_a_chinese_category_label(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger_at(tmp_path)
    _c, _view, rows = run(csv(['证券代码', '证券名称', '股票余额', '成本价'], ['600519', '贵州茅台', '100', '1650.00']), 'k.csv',
                          account='新账户')
    assert rows[2]['category'] == 'position' and rows[2]['category_label'] == '持仓'


TICKER = re.compile(r'(?:SHA|SHE|HKG|NASDAQ|NYSE):[A-Za-z0-9.\-]+')
# Words the page may show: market and currency codes, format names, A/B/H 股, ETF.
GLOSSARY = {'A', 'B', 'H', 'ETF', 'HK', 'US', 'CNY', 'HKD', 'USD', 'NASDAQ', 'NYSE', 'GBK', 'GB', 'UTF', 'BOM', 'CSV',
            'Excel', 'xls', 'xlsx', 'XML', 'MB'} | {code for code in OTHER_CURRENCIES.values() if code.isalpha()}


def _words(value) -> set:
    return set(re.findall(r'[A-Za-z]+', TICKER.sub(' ', str(value or ''))))


def user_texts(view: dict):
    """(where, text) for every text of a session payload the page shows as it is."""
    yield from (('blocker', t) for t in view['blockers'])
    yield from (('warning', t) for t in view['warnings'])
    yield 'statement_label', view['statement_label']
    yield 'format', view['format']['label']
    yield 'preset', view['preset']['label']
    yield 'preset note', view['preset'].get('note')
    yield from (('column', c['field_label']) for c in view['columns'])
    yield from (('field', f['label']) for f in view['fields'])
    yield from (('conflict', c['label']) for c in view['column_conflicts'])
    yield from (('skipped', g['label']) for g in view['skipped'])
    yield from (('status label', label) for label in view['status_labels'].values())
    accounts = view['accounts'] or {}
    yield 'accounts summary', accounts.get('summary')
    yield from (('accounts column', c['label']) for c in accounts.get('columns', []))
    recon = view['reconciliation'] or {}
    yield 'reconciliation', recon.get('reason')
    yield 'reconciliation', recon.get('summary')
    yield from (('cause', c) for c in recon.get('causes', []))
    for item in recon.get('cash', []) + recon.get('positions', []):
        yield 'reconciliation line', item['label']
        yield 'reconciliation status', item['status']
    holdings = view['holdings'] or {}
    yield 'holdings', holdings.get('cost_note')
    yield from (('holdings summary', s['label']) for s in holdings.get('summary', []))
    for item in holdings.get('reconciliation') or []:
        yield 'holdings line', item['label']
        yield 'holdings line', item.get('cost_note')
    for row in view['rows']:
        yield from ((f"row {row['row']} message", m) for m in row['messages'])
        yield from ((f"row {row['row']} note", n) for n in row['notes'])
        yield f"row {row['row']} label", row['category_label']
        yield f"row {row['row']} label", row['status_label']
        yield from ((f"row {row['row']} identity", i['label']) for i in row['account_identity'])


def english(view: dict, filename: str) -> list:
    """Words in the payload's texts that come neither from the file, nor a ticker, nor the glossary."""
    supplied = _words(filename) | _words(view['account']) | set(TEMPLATE_COLUMNS if view['statement'] == 'template' else ())
    for line in view['format']['title_lines'] + [c['header'] for c in view['columns']]:
        supplied |= _words(line)
    for row in view['rows']:
        for cell in row['cells'] + [row['code'], row['name'], row['action']]:
            supplied |= _words(cell)
    return [(where, value) for where, value in user_texts(view) if value and _words(value) - supplied - GLOSSARY]


def _scenario_files(ledger):
    """Files beyond the adversarial sweep: duplicates of a manual entry, two accounts, a template, a reconciled
    holdings file, a split before a later ledger event, a sale of more than is held, a 逆回购 pair."""
    book(ledger, [{'kind': 'buy', 'date': '2026-11-03', 'account': ACCOUNT, 'ticker': 'SHA:600036', 'currency': 'CNY',
                   'quantity': '100', 'price': '30', 'fee': '5'},
                  {'kind': 'deposit', 'date': '2026-11-20', 'account': ACCOUNT, 'currency': 'CNY', 'amount': '1'}])
    return [
        ('duplicate of a manual entry', csv(TRADE, BUY), 'b.csv'),
        ('two accounts', csv(IDENTITY, ['2026-11-03', '银行转证券', '10', '111'], ['2026-11-04', '银行转证券', '20', '222'],
                             ['2026-11-05', '银行转证券', '20', '']), 'm.csv'),
        ('a template', csv(TEMPLATE, ['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600036', 'CNY', '100', '30', '', ''],
                           ['t-2', '2026-11-03', 'deposit', ACCOUNT, '', '', '', '', '', '10'],
                           ['', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '', '10'],
                           ['t-3', '2026-11-03', 'deposit', '另一个账户', '', 'CNY', '', '', '', '10'],
                           ['t-4', '2026-11-03', 'opening', ACCOUNT, '', 'CNY', '', '', '', '10'],
                           ['t-5', '2026-11-03', 'buy', ACCOUNT, 'SHA:600036', 'HKD', '100', '30', '5', '']), 't.csv'),
        ('holdings of an existing account', csv(['证券代码', '证券名称', '股票余额', '成本价'], ['600036', '招商银行', '900', '30'],
                                                ['600000', '浦发银行', '100', '8']), 'k.csv'),
        ('a split before a later ledger event', csv(SPLIT_HEADER, SPLIT), 's.csv'),
        ('a sale of more than is held', csv(TRADE, changed(TRADE, BUY, 操作='证券卖出', 成交数量='3000', 成交金额='90000.00',
                                                             发生金额='89995.00')), 'x.csv'),
        ('a repo pair', csv(CASH, LEND, BACK), 'r.csv'),
    ]


def _all_views(ledger):
    for name, data, filename, *_rest in CASES:
        _c, view, _rows = run(data, filename, account=_rest[2] if len(_rest) > 2 else ACCOUNT)
        yield name, filename, view
    for name, data, filename in _scenario_files(ledger):
        _c, view, _rows = run(data, filename)
        yield name, filename, view


def test_the_text_and_needs_sweeps_cover_every_kind_of_file(ledger):
    statements = {view['statement'] for _name, _filename, view in _all_views(ledger)}
    assert statements == {'transactions', 'holdings', 'template'}


def test_no_english_word_or_api_name_in_the_texts_the_page_shows(ledger):
    found = [f'{name} / {where}: {text}' for name, filename, view in _all_views(ledger)
             for where, text in english(view, filename)]
    assert not found, '\n'.join(found)


# -- 3. every review row says what to fill in, or that it cannot be fixed here -----------------------------------

# What a row edit (or the mapping / account choice) can set, per statement (EDIT_FIELDS in transactions.py).
EDITABLE = {'transactions': {'ticker', 'kind', 'quantity', 'price', 'fee', 'amount', 'currency', 'fx_rate', 'date',
                             'allow_margin', 'mapping', 'account'},
            'holdings': {'ticker', 'quantity', 'price', 'mapping', 'account'},
            'template': {'fee'}}


def test_every_review_row_lists_what_to_fill_in_or_says_it_cannot_be_fixed_here(ledger):
    problems = []
    for name, _filename, view in _all_views(ledger):
        for row in view['rows']:
            if row['status'] != 'review':
                continue
            fixable = row['needs'] and set(row['needs']) <= EDITABLE[view['statement']]
            if not fixable and not any(NOT_HERE in m for m in row['messages']):
                problems.append((name, row['row'], row['needs'], row['messages']))
    assert not problems


def test_a_template_buy_without_a_fee_needs_the_fee(ledger):
    created, _view, rows = run(csv(TEMPLATE, ['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600036', 'CNY', '100', '30', '',
                                              '']), 't.csv')
    assert rows[2]['status'] == 'review' and rows[2]['needs'] == ['fee'], rows[2]
    _view, rows = patch(created['id'], rows=[{'row': 2, 'fee': '0'}])
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['fee'] == '0'


def test_a_split_before_a_later_ledger_event_says_why_it_can_only_be_skipped(ledger):
    book(ledger, [{'kind': 'deposit', 'date': '2026-11-20', 'account': ACCOUNT, 'currency': 'CNY', 'amount': '1'}])
    _c, _view, rows = run(csv(SPLIT_HEADER, SPLIT), 's.csv')
    message = ' '.join(rows[2]['messages'])
    assert rows[2]['status'] == 'review' and NOT_HERE in message and '跳过' in message, rows[2]


# -- 4. an identity the account's memory refuses is not shown accepted; rows kept out are counted ----------------

def test_an_identity_the_memory_refuses_is_not_shown_accepted(ledger):
    created, view, _rows = run(csv(IDENTITY, ['2026-11-03', '银行转证券', '10', '111'],
                                   ['2026-11-04', '银行转证券', '20', '222']), 'm.csv')
    assert view['accounts']['rows_kept_out'] == 2   # a tie: the preview accepts neither
    patch(created['id'], account_filter=['111'])     # 222 is remembered as not this account's
    # Another header layout (no saved choice applies): a file of 222 alone is refused by the account's memory.
    _c, view, rows = run(csv(['资金账号', '发生日期', '业务名称', '发生金额'], ['222', '2026-11-05', '银行转证券', '30'],
                             ['222', '2026-11-06', '银行转证券', '40']), 'n.csv')
    assert view['accounts']['source'] == 'single'
    assert rows[2]['status'] == 'review' and rows[2]['needs'] == ['account']
    values = view['accounts']['columns'][0]['values']
    assert values[0]['value'] == '222' and values[0]['accepted'] is False, values
    assert view['accounts']['accepted'] == [] and view['accounts']['rows_kept_out'] == 2


# -- 5. a possible duplicate names the ledger event it matches ----------------------------------------------------

def test_a_possible_duplicate_names_the_ledger_event_it_matches(ledger):
    book(ledger, [{'kind': 'buy', 'date': '2026-11-03', 'account': ACCOUNT, 'ticker': 'SHA:600036', 'currency': 'CNY',
                   'quantity': '100', 'price': '30', 'fee': '5'}])   # entered by hand, without a name
    _c, _view, rows = run(csv(TRADE, BUY), 'b.csv')
    row = rows[2]
    assert row['status'] == 'possible_duplicate'
    duplicate = row['duplicate_of']
    assert (duplicate['name'], duplicate['date'], duplicate['quantity'], duplicate['source_label']) == \
        ('招商银行', '2026-11-03', '100', '手工'), duplicate
    assert '手工' in row['messages'][-1] and 'manual' not in row['messages'][-1], row['messages']


def test_a_possible_duplicate_split_names_its_listing_and_the_shares_it_added(ledger):
    book(ledger, [{'kind': 'split', 'date': '2026-11-05', 'account': ACCOUNT, 'ticker': 'SHA:600036', 'ratio': '1.3'}])
    _c, _view, rows = run(csv(SPLIT_HEADER, SPLIT), 's.csv')
    duplicate = rows[2]['duplicate_of']
    assert rows[2]['status'] == 'possible_duplicate'
    assert (duplicate['name'], duplicate['date'], duplicate['added_quantity'], duplicate['source_label']) == \
        ('招商银行', '2026-11-05', '300', '手工'), duplicate


# -- 6. PATCH and parse page their rows like GET -------------------------------------------------------------------

@pytest.fixture
def client(ledger):
    app = FastAPI()
    app.include_router(api.router, prefix='/api/imports')
    return TestClient(app)


THREE = csv(TRADE, BUY, changed(TRADE, BUY, 成交日期='2026-11-04', 成交数量=''), changed(TRADE, BUY, 成交日期='2026-11-05'))


def _upload(client, query=''):
    response = client.post('/api/imports/parse' + query, files={'file': ('b.csv', THREE, 'text/csv')},
                           data={'account': ACCOUNT})
    assert response.status_code == 200, response.json()
    return response.json()


def test_parse_and_patch_return_every_row_by_default(client):
    body = _upload(client)
    assert len(body['rows']) == body['rows_total'] == 3 and body['rows_offset'] == 0
    patched = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 3, 'include': False}]}).json()
    assert len(patched['rows']) == patched['rows_total'] == 3


def test_parse_and_patch_page_and_filter_their_rows_like_get(client):
    body = _upload(client, '?limit=1&offset=1')
    assert [row['row'] for row in body['rows']] == [3] and body['rows_total'] == 3 and body['rows_offset'] == 1
    assert body['counts']['total'] == 3 and body['counts']['review'] == 1
    patched = client.patch(f"/api/imports/{body['id']}?status=new&limit=1",
                           json={'rows': [{'row': 2, 'include': False}]}).json()
    assert [row['row'] for row in patched['rows']] == [2] and patched['rows_total'] == 2
    assert patched['counts']['included_rows'] == 1
    got = client.get(f"/api/imports/{body['id']}?status=new&limit=1").json()
    assert got['rows'] == patched['rows'] and got['rows_total'] == patched['rows_total']


@pytest.mark.parametrize('query', ['?limit=0', '?offset=-1', '?status=unknown'])
def test_parse_and_patch_refuse_a_bad_window_like_get(client, query):
    body = _upload(client)
    response = client.patch(f"/api/imports/{body['id']}{query}", json={})
    assert response.status_code == 422, response.json()
    response = client.post('/api/imports/parse' + query, files={'file': ('b.csv', THREE, 'text/csv')},
                           data={'account': ACCOUNT})
    assert response.status_code == 422
