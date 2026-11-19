"""Identity of imported rows: re-imports add nothing, changed rows are excluded, fuzzy duplicates."""
import pytest

from core.importers import ImportFailure, session
from core.importers.dedupe import ledger_id
from tests.importer_support import ACCOUNT, book, csv, fixture, ledger_at, open_account, patch, run

HEADER = ['成交日期', '成交时间', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额']
FILL = ['2026-11-03', '09:31:25', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '43.68', '-168043.68']


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger = ledger_at(tmp_path)
    open_account(ledger)
    return ledger


def confirm_all(ledger, session_id):
    while True:
        try:
            result = session.propose(session_id)
        except Exception:
            return
        ledger.confirm(result['proposal']['id'])


def test_reimport_adds_nothing(ledger):
    for name in ('ths_trades_gbk.xls', 'eastmoney_statement_bom.csv', 'tdx_trades_gbk.txt', 'hk_connect_bom.csv'):
        created, view, _rows = run(fixture(name), name)
        confirm_all(ledger, created['id'])
        _again, view, rows = run(fixture(name), 'again-' + name)
        assert view['counts']['new'] == 0 and view['counts']['included_events'] == 0, name
        assert view['counts']['imported'] >= 1
        assert all(r['status'] != 'new' for r in rows.values())
        assert view['batches']['next'] is None


SMALL = ['2026-11-03', '09:31:25', '000001', '平安银行', '证券买入', 100, '11.00', '1100.00', '5.00', '-1105.00']


def test_two_identical_fills_both_import_and_reimport_dedupes(ledger):
    data = csv(HEADER, SMALL, SMALL)
    created, view, rows = run(data, 'twice.csv')
    assert [rows[2]['status'], rows[3]['status']] == ['new', 'new']
    first, second = rows[2]['external_id'], rows[3]['external_id']
    assert first.startswith('h:') and first.endswith(':1') and second == first[:-1] + '2'
    ledger.confirm(session.propose(created['id'])['proposal']['id'])
    assert ledger.state()['accounts'][ACCOUNT]['positions']['SHE:000001']['quantity'] == '2200'
    _again, view, rows = run(data, 'twice-again.csv')
    assert [rows[2]['status'], rows[3]['status']] == ['imported', 'imported']
    _more, view, rows = run(csv(HEADER, SMALL, SMALL, SMALL), 'three.csv')
    assert [rows[n]['status'] for n in (2, 3, 4)] == ['imported', 'imported', 'new'], 'a third fill is new'


def test_changed_content_is_excluded_with_an_explanation_not_a_conflict(ledger):
    created, _view, _rows = run(fixture('ths_trades_gbk.xls'), 'jgd.xls')
    ledger.confirm(session.propose(created['id'])['proposal']['id'])
    text = fixture('ths_trades_gbk.xls').decode('gb18030')
    changed = text.replace('\t42.00\t0.00\t1.68\t0.00\t-168043.68', '\t42.01\t0.00\t1.68\t0.00\t-168043.69')
    changed += '20261113\t09:40:00\t000001\t平安银行\t证券买入\t100\t11.00\t1100.00\t5.00\t0.00\t0.01\t0.00\t' \
               '-1105.01\t0\t100\t0012500\tH0012500\t0000000001\t深圳A股\r\n'
    again, view, rows = run(changed.encode('gb18030'), 'changed.xls')
    assert rows[3]['status'] == 'changed' and not rows[3]['can_include']
    assert '费用 已入账 43.68 / 文件 43.69' in rows[3]['messages'][0] and '冲销' in rows[3]['messages'][0]
    assert rows[3]['existing']['fee'] == '43.68'
    new = [r for r in rows.values() if r['status'] == 'new']
    assert [r['code'] for r in new] == ['000001']
    result = session.propose(again['id'])
    assert [e['external_id'] for e in result['proposal']['events']] == ['t:2026-11-13:000001:0012500']


def test_name_or_note_differences_still_count_as_imported(ledger):
    created, _v, _rows = run(csv(HEADER, FILL), 'a.csv')
    ledger.confirm(session.propose(created['id'])['proposal']['id'])
    renamed = FILL[:3] + ['*ST茅台'] + FILL[4:]
    _again, _view, rows = run(csv(HEADER, renamed), 'b.csv')
    assert rows[2]['status'] == 'imported' and '名称或备注不同' in rows[2]['messages'][0]


def test_manual_entry_is_a_possible_duplicate_unchecked_by_default(ledger):
    book(ledger, [{'kind': 'deposit', 'date': '2026-11-02', 'account': ACCOUNT, 'currency': 'CNY', 'amount': '200000'}])
    book(ledger, [{'kind': 'buy', 'date': '2026-11-03', 'account': ACCOUNT, 'ticker': 'SHA:600519', 'currency': 'CNY',
                   'quantity': '100', 'price': '1680.1', 'fee': '5'}])
    created, view, rows = run(csv(HEADER, FILL, FILL), 'dup.csv')
    assert rows[2]['status'] == 'possible_duplicate' and rows[2]['include'] is False and rows[2]['can_include']
    assert rows[2]['duplicate_of']['source'] == 'manual' and '可能已经记过' in rows[2]['messages'][0]
    assert rows[3]['status'] == 'new', 'one manual entry explains at most one row'
    assert view['counts']['possible_duplicate'] == 1 and view['counts']['included_events'] == 1
    view, rows = patch(created['id'], rows=[{'row': 2, 'include': True}])
    assert rows[2]['include'] is True and view['counts']['included_events'] == 2
    # Review round 3: the same account, date, listing, kind and quantity is a probable duplicate whatever the
    # price (an overlapping export can round the average price differently).
    far = FILL[:6] + ['1690.00', '169000.00', '43.68', '-169043.68']
    _other, _view, rows = run(csv(HEADER, far), 'far.csv')
    assert rows[2]['status'] == 'possible_duplicate' and rows[2]['include'] is False
    other = FILL[:5] + [200, '1680.00', '336000.00', '87.36', '-336087.36']
    _other, _view, rows = run(csv(HEADER, other), 'other_quantity.csv')
    assert rows[2]['status'] == 'new', 'another quantity is another fill'


def test_cash_flow_from_another_source_is_a_possible_duplicate(ledger):
    book(ledger, [{'kind': 'deposit', 'date': '2026-11-02', 'account': ACCOUNT, 'currency': 'CNY', 'amount': '50000',
                   'source': 'agent:chat'}])
    _created, _view, rows = run(fixture('eastmoney_statement_bom.csv'), 'dzd.csv')
    assert rows[2]['status'] == 'possible_duplicate' and rows[2]['duplicate_of']['source'] == 'agent:chat'


def test_identity_rules():
    assert ledger_id(f'import:{ACCOUNT}', 'x') == ledger_id(f'import:{ACCOUNT}', 'x')
    assert ledger_id(f'import:{ACCOUNT}', 'x') != ledger_id('import:其他账户', 'x')


CASH = ['发生日期', '业务名称', '发生金额']


def test_identical_rows_keep_distinct_ids_after_their_dates_are_filled_in(ledger):
    created, view, rows = run(csv(CASH, ['', '银行转证券', '10'], ['', '银行转证券', '10']), 'missing_dates.csv')
    assert [rows[2]['status'], rows[3]['status']] == ['review', 'review'] and rows[2]['needs'] == ['date']
    view, rows = patch(created['id'], rows=[{'row': 2, 'date': '2026-11-03'}, {'row': 3, 'date': '2026-11-03'}])
    assert rows[2]['external_id'] != rows[3]['external_id'] and view['counts']['included_events'] == 2
    result = session.propose(created['id'])
    assert len(result['proposal']['events']) == 2 == result['batch']['events']
    ledger.confirm(result['proposal']['id'])
    assert ledger.state()['accounts'][ACCOUNT]['cash_balances']['CNY'] == '500020'
    view = session.view(session.load(created['id']))
    assert view['counts']['imported'] == 2


def test_a_filled_in_date_never_shifts_the_ids_of_rows_dated_in_the_file(ledger):
    _c, _v, alone = run(csv(CASH, ['2026-11-03', '银行转证券', '10']), 'alone.csv')
    created, _v, _rows = run(csv(CASH, ['', '银行转证券', '10'], ['2026-11-03', '银行转证券', '10']), 'mixed.csv')
    _view, rows = patch(created['id'], rows=[{'row': 2, 'date': '2026-11-03'}])
    assert rows[3]['external_id'] == alone[2]['external_id'], 'the file-dated row keeps the id it has on its own'
    assert rows[2]['external_id'] != rows[3]['external_id'] and rows[2]['status'] == 'new'


def test_preview_and_proposal_must_hold_the_same_events(ledger, monkeypatch):
    from core.importers import dedupe

    def same_id(rows, *args, **kwargs):
        for row in rows:
            row.external_id = 'h:same:1' if row.date else None
    monkeypatch.setattr(dedupe, 'assign_ids', same_id)
    created, view, _rows = run(csv(CASH, ['2026-11-03', '银行转证券', '10'], ['2026-11-03', '银行转证券', '10']), 'x.csv')
    assert view['counts']['included_events'] == 2
    with pytest.raises(ImportFailure) as caught:
        session.propose(created['id'])
    assert caught.value.status == 409 and '不一致' in str(caught.value)
    assert ledger.proposals() == [], 'the mismatching proposal is withdrawn'


def test_template_rows_with_the_same_external_id_need_review(ledger):
    header = ['external_id', 'date', 'kind', 'account', 'currency', 'amount']
    _c, _v, rows = run(csv(header, ['t-1', '2026-11-03', 'deposit', ACCOUNT, 'CNY', '10'],
                           ['t-1', '2026-11-03', 'deposit', ACCOUNT, 'CNY', '10'],
                           ['t-2', '2026-11-03', 'deposit', ACCOUNT, 'CNY', '10']), 'template.csv')
    assert [rows[n]['status'] for n in (2, 3, 4)] == ['review', 'review', 'new']
    assert 't-1' in rows[2]['messages'][0]


def test_trade_ids_are_used_only_when_unique_per_date(ledger):
    header = HEADER + ['成交编号', '合同编号']
    rows_in = [FILL + ['0001', 'C1'], FILL[:1] + ['09:40:00'] + FILL[2:] + ['0001', 'C1'],
               ['2026-11-04'] + FILL[1:] + ['0001', 'C2'], ['2026-11-05'] + FILL[1:] + ['0', 'C3']]
    _created, _view, rows = run(csv(header, *rows_in), 'ids.csv')
    assert rows[2]['external_id'].startswith('h:') and rows[3]['external_id'].startswith('h:')
    assert rows[4]['external_id'] == 't:2026-11-04:600519:0001'
    assert rows[5]['external_id'].startswith('h:'), '0 is not a trade id'
