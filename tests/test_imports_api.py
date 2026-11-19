"""HTTP contract of the broker importer: parse → review → propose in batches → confirm → re-parse."""
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from backend.api import imports as api
from backend.api import ledger as ledger_api
from core.importers import aliases, session, sniff
from tests.importer_support import ACCOUNT, book, csv, fixture, ledger_at, open_account


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger)
    return ledger


@pytest.fixture
def client(ledger):
    app = FastAPI()
    app.include_router(api.router, prefix='/api/imports')
    app.include_router(ledger_api.router, prefix='/api/ledger')
    return TestClient(app)


def upload(client, data, name='交割单.xls', account=ACCOUNT, **form):
    return client.post('/api/imports/parse', files={'file': (name, data, 'application/octet-stream')},
                       data={'account': account, **form})


def test_parse_propose_confirm_and_reparse(client, ledger):
    response = upload(client, fixture('ths_trades_gbk.xls'))
    assert response.status_code == 200
    body = response.json()
    assert body['format']['type'] == 'text' and body['format']['encoding'] == 'GBK'
    assert body['preset']['label'] == '同花顺交割单' and body['counts']['new'] == 5
    assert body['reconciliation']['consistent'] and body['batches']['total'] == 1
    assert ledger.state()['revision'] == 1, 'parsing never writes to the ledger'
    proposed = client.post(f"/api/imports/{body['id']}/propose").json()
    assert proposed['batch'] == {'number': 1, 'total': 1, 'label': '第 1/1 批', 'events': 5,
                                 'rows': [3, 4, 5, 7, 10], 'reused': False}
    assert proposed['proposal']['before']['accounts'][ACCOUNT]['cash_balances']['CNY'] == '500000'
    assert 'rows' not in proposed['import'] and proposed['import']['batches']['pending']['number'] == 1
    assert ledger.state()['revision'] == 1, 'proposing never writes to the ledger'
    confirmed = client.post(f"/api/ledger/proposals/{proposed['proposal']['id']}/confirm")
    assert confirmed.status_code == 200 and confirmed.json()['applied'] == 5
    after = client.get(f"/api/imports/{body['id']}").json()
    assert after['counts']['imported'] == 5 and after['counts']['new'] == 0
    assert after['batches']['confirmed'] == 1 and after['batches']['next'] is None
    nothing = client.post(f"/api/imports/{body['id']}/propose")
    assert nothing.status_code == 409 and '没有需要入账' in nothing.json()['detail']
    again = upload(client, fixture('ths_trades_gbk.xls'), name='再次.xls').json()
    assert again['counts']['new'] == 0 and again['counts']['imported'] == 5
    assert again['id'] != body['id']


def large_statement(rows=2300):
    header = ['发生日期', '业务名称', '发生金额', '资金余额', '流水号']
    start, balance, lines = date(2026, 11, 2), 500000, []
    for index in range(rows):
        balance += 1
        lines.append([(start + timedelta(days=index // 100)).isoformat(), '利息归本' if index % 2 else '银行转证券',
                      '1.00', f'{balance}.00', f'S{index:05d}'])
    return csv(header, *lines)


def test_2300_rows_are_three_sequential_batches(client, ledger):
    body = upload(client, large_statement(), name='statement.csv').json()
    assert body['counts']['new'] == 2300 and body['batches']['total'] == 3
    numbers = []
    for expected_events in (1000, 1000, 300):
        first = client.post(f"/api/imports/{body['id']}/propose").json()
        repeat = client.post(f"/api/imports/{body['id']}/propose").json()
        assert repeat['batch']['reused'] is True and repeat['proposal']['id'] == first['proposal']['id']
        assert len(first['proposal']['events']) == expected_events == first['batch']['events']
        assert first['batch']['total'] == 3
        dates = [e['date'] for e in first['proposal']['events']]
        assert dates == sorted(dates), 'oldest first'
        numbers.append(first['batch']['label'])
        assert client.post(f"/api/ledger/proposals/{first['proposal']['id']}/confirm").status_code == 200
    assert numbers == ['第 1/3 批', '第 2/3 批', '第 3/3 批']
    final = client.get(f"/api/imports/{body['id']}").json()
    assert final['counts']['imported'] == 2300 and final['batches']['confirmed'] == 3
    assert final['reconciliation']['consistent']
    assert len(ledger.history()) == 2301
    assert client.post(f"/api/imports/{body['id']}/propose").status_code == 409


def test_a_stale_batch_is_prepared_again(client, ledger):
    body = upload(client, fixture('tdx_trades_gbk.txt'), name='tdx.txt').json()
    first = client.post(f"/api/imports/{body['id']}/propose").json()
    book(ledger, [{'kind': 'deposit', 'date': '2026-11-15', 'account': ACCOUNT, 'currency': 'CNY', 'amount': '1'}])
    view = client.get(f"/api/imports/{body['id']}").json()
    assert view['batches']['pending']['stale'] is True
    second = client.post(f"/api/imports/{body['id']}/propose").json()
    assert second['batch']['reused'] is False and second['batch']['number'] == 1
    assert second['proposal']['id'] != first['proposal']['id']
    assert client.get(f"/api/ledger/proposals/{first['proposal']['id']}").status_code == 404
    assert client.post(f"/api/ledger/proposals/{second['proposal']['id']}/confirm").status_code == 200


def test_any_change_to_the_session_withdraws_the_pending_proposal(client, ledger):
    data = csv(['发生日期', '业务名称', '发生金额'], ['2026-11-03', '银行转证券', '10'], ['2026-11-04', '银行转证券', '5'])
    body = upload(client, data, name='pending_edit.csv').json()
    first = client.post(f"/api/imports/{body['id']}/propose").json()
    same = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, 'amount': None}]}).json()
    assert same['batches']['pending']['proposal_id'] == first['proposal']['id'], 'a PATCH that changes nothing keeps it'
    patched = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, 'amount': '200'}]}).json()
    assert patched['rows'][0]['events'][0]['amount'] == '200'
    assert patched['batches']['pending'] is None and patched['batches']['history'][-1]['state'] == 'discarded'
    assert client.get(f"/api/ledger/proposals/{first['proposal']['id']}").status_code == 404, 'withdrawn from the ledger'
    second = client.post(f"/api/imports/{body['id']}/propose").json()
    assert second['batch']['reused'] is False and second['batch']['number'] == 1
    assert [e['amount'] for e in second['proposal']['events']] == ['200', '5']
    excluded = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 3, 'include': False}]}).json()
    assert excluded['batches']['pending'] is None
    third = client.post(f"/api/imports/{body['id']}/propose").json()
    assert third['batch']['reused'] is False and [e['amount'] for e in third['proposal']['events']] == ['200']
    assert client.post(f"/api/ledger/proposals/{third['proposal']['id']}/confirm").status_code == 200
    assert ledger.state()['accounts'][ACCOUNT]['cash_balances']['CNY'] == '500200'


def test_a_pending_proposal_is_not_reused_when_its_events_would_differ(client, ledger, monkeypatch):
    data = csv(['发生日期', '业务名称', '发生金额'], ['2026-11-03', '银行转证券', '10'])
    body = upload(client, data, name='fingerprint.csv').json()
    first = client.post(f"/api/imports/{body['id']}/propose").json()
    stored = session.load(body['id'])
    stored['edits'] = {'2': {'amount': '300'}}   # changed behind the API's back: only the fingerprint can tell
    session._store(stored)
    view = client.get(f"/api/imports/{body['id']}").json()
    assert view['batches']['pending']['stale'] is True
    again = client.post(f"/api/imports/{body['id']}/propose").json()
    assert again['batch']['reused'] is False and again['proposal']['events'][0]['amount'] == '300'
    assert client.get(f"/api/ledger/proposals/{first['proposal']['id']}").status_code == 404


DEPOSIT = csv(['发生日期', '业务名称', '发生金额'], ['2026-11-03', '银行转证券', '10'])


def cash(ledger):
    return ledger.state()['accounts'][ACCOUNT]['cash_balances']['CNY']


def test_concurrent_first_proposals_leave_one_proposal_and_no_orphan(client, ledger, monkeypatch):
    # Review round 2, finding 1: two simultaneous first propose calls created two proposals but kept one id;
    # after a PATCH 10 -> 200 only that one was withdrawn and the orphan still confirmed 10.
    import threading
    body = upload(client, DEPOSIT, name='race.csv').json()
    barrier, real, results = threading.Barrier(2), session.analyse, []

    def analysed_together(*args, **kwargs):
        result = real(*args, **kwargs)
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        return result

    def propose():
        try:
            results.append(session.propose(body['id'])['proposal']['id'])
        except Exception as exc:  # the store may refuse the second one; it must never keep two
            results.append(exc)
    monkeypatch.setattr(session, 'analyse', analysed_together)
    threads = [threading.Thread(target=propose) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    monkeypatch.setattr(session, 'analyse', real)
    proposed = {result for result in results if isinstance(result, str)}
    assert proposed and len(ledger.proposals()) == 1, 'one pending proposal per session'
    assert client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, 'amount': '200'}]}).status_code == 200
    for proposal_id in proposed:
        assert client.post(f'/api/ledger/proposals/{proposal_id}/confirm').status_code != 200
    assert cash(ledger) == '500000' and ledger.proposals() == []
    final = client.post(f"/api/imports/{body['id']}/propose").json()
    assert client.post(f"/api/ledger/proposals/{final['proposal']['id']}/confirm").status_code == 200
    assert cash(ledger) == '500200'


def test_a_patch_that_cannot_reach_the_ledger_changes_nothing(client, ledger, monkeypatch):
    # Review round 2, finding 1: with the ledger write lock held elsewhere, PATCH stored amount 200, answered 503
    # and left the proposal for 10 confirmable.
    import sqlite3
    body = upload(client, DEPOSIT, name='busy.csv').json()
    first = client.post(f"/api/imports/{body['id']}/propose").json()['proposal']
    real_connect, real_analyse = sqlite3.connect, session.analyse
    holder = real_connect(str(ledger.path), isolation_level=None, check_same_thread=False)

    def analysed_then_busy(*args, **kwargs):
        result = real_analyse(*args, **kwargs)
        holder.execute('BEGIN IMMEDIATE')   # another writer takes the ledger once the PATCH has been analysed
        return result
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: real_connect(*a, **{**k, 'timeout': 0.2}))
    monkeypatch.setattr(session, 'analyse', analysed_then_busy)
    try:
        refused = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, 'amount': '200'}]})
    finally:
        if holder.in_transaction:
            holder.execute('ROLLBACK')
        holder.close()
        monkeypatch.setattr(sqlite3, 'connect', real_connect)
        monkeypatch.setattr(session, 'analyse', real_analyse)
    assert refused.status_code == 503
    after = client.get(f"/api/imports/{body['id']}").json()
    assert after['rows'][0]['edits'] == {} and after['updated_at'] == body['updated_at'], 'nothing was stored'
    assert after['batches']['pending']['proposal_id'] == first['id'] and after['batches']['pending']['stale'] is False
    assert client.post(f"/api/ledger/proposals/{first['id']}/confirm").status_code == 200
    assert cash(ledger) == '500010', 'the proposal still describes the session exactly'


def test_a_patch_built_on_an_older_version_is_refused_and_withdraws_nothing(client, ledger, monkeypatch):
    # Two PATCHes read the same version; the second to store must not silently undo the first.
    body = upload(client, DEPOSIT, name='lost_update.csv').json()
    first = client.post(f"/api/imports/{body['id']}/propose").json()['proposal']
    real = session.analyse

    def another_request_in_between(*args, **kwargs):
        result = real(*args, **kwargs)
        monkeypatch.setattr(session, 'analyse', real)
        session.patch(body['id'], {'rows': [{'row': 2, 'amount': '300'}]})
        return result
    monkeypatch.setattr(session, 'analyse', another_request_in_between)
    refused = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, 'amount': '200'}]})
    assert refused.status_code == 409 and '刷新' in refused.json()['detail']
    after = client.get(f"/api/imports/{body['id']}").json()
    assert after['rows'][0]['edits'] == {'amount': '300'} and ledger.proposals() == []
    assert client.post(f"/api/ledger/proposals/{first['id']}/confirm").status_code != 200


def test_a_proposal_whose_session_changed_cannot_be_confirmed(client, ledger):
    # A crash after the session was stored and before its proposal was withdrawn leaves the proposal in the
    # ledger file: confirmation itself compares it with the session.
    body = upload(client, DEPOSIT, name='crash.csv').json()
    first = client.post(f"/api/imports/{body['id']}/propose").json()['proposal']
    stored = session.load(body['id'])
    stored['edits'] = {'2': {'amount': '200'}}
    session._store(stored)
    refused = client.post(f"/api/ledger/proposals/{first['id']}/confirm")
    assert refused.status_code == 409 and '导入' in refused.json()['detail']
    assert cash(ledger) == '500000' and ledger.proposals() == [], 'refused and withdrawn'
    again = client.post(f"/api/imports/{body['id']}/propose").json()['proposal']
    assert client.post(f"/api/ledger/proposals/{again['id']}/confirm").status_code == 200
    assert cash(ledger) == '500200'


def test_proposals_of_expired_or_deleted_sessions_cannot_be_confirmed(client, ledger):
    # Review round 2, finding 12: after expiry the session disappeared but its proposal still confirmed.
    from datetime import datetime, timezone
    from core import desk_store
    expired = upload(client, DEPOSIT, name='expired.csv').json()
    old = client.post(f"/api/imports/{expired['id']}/propose").json()['proposal']
    past = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec='seconds')
    with desk_store.transaction() as db:
        db.execute('UPDATE import_sessions SET created_at=? WHERE id=?', (past, expired['id']))
    refused = client.post(f"/api/ledger/proposals/{old['id']}/confirm")   # before any import write tidies up
    assert refused.status_code == 409 and '过期' in refused.json()['detail']
    deleted = upload(client, DEPOSIT, name='deleted.csv').json()
    pending = client.post(f"/api/imports/{deleted['id']}/propose").json()['proposal']
    assert client.delete(f"/api/imports/{deleted['id']}").json() == {'deleted': True}
    assert client.post(f"/api/ledger/proposals/{pending['id']}/confirm").status_code != 200
    assert cash(ledger) == '500000' and ledger.proposals() == []


def test_the_next_import_write_withdraws_proposals_of_expired_sessions(client, ledger):
    # Review round 2, finding 12: expiry must not leave a pending proposal in the ledger's list either.
    from datetime import datetime, timezone
    from core import desk_store
    expired = upload(client, DEPOSIT, name='expired.csv').json()
    old = client.post(f"/api/imports/{expired['id']}/propose").json()['proposal']
    past = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec='seconds')
    with desk_store.transaction() as db:
        db.execute('UPDATE import_sessions SET created_at=? WHERE id=?', (past, expired['id']))
    assert [p['id'] for p in ledger.proposals()] == [old['id']]
    upload(client, DEPOSIT, name='newer.csv')
    assert ledger.proposals() == [], 'withdrawn by the upload that deleted the expired session'
    assert client.get(f"/api/ledger/proposals/{old['id']}").status_code == 404


def test_a_proposal_discarded_in_the_ledger_can_be_prepared_again(client, ledger):
    body = upload(client, DEPOSIT, name='discarded.csv').json()
    first = client.post(f"/api/imports/{body['id']}/propose").json()['proposal']
    assert client.delete(f"/api/ledger/proposals/{first['id']}").json() == {'discarded': True}
    view = client.get(f"/api/imports/{body['id']}").json()
    assert view['batches']['pending'] is None and view['batches']['history'][-1]['state'] == 'discarded'
    again = client.post(f"/api/imports/{body['id']}/propose").json()
    assert again['batch']['reused'] is False and again['batch']['number'] == 1
    assert client.post(f"/api/ledger/proposals/{again['proposal']['id']}/confirm").status_code == 200
    assert cash(ledger) == '500010'


def test_corrected_mapping_is_saved_and_applied_next_time(client):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '变动额(元)']
    data = csv(header, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '-168043.68'])
    body = upload(client, data, name='a.csv').json()
    assert body['rows'][0]['status'] == 'review' and body['mapping_source'] == 'synonyms'
    assert body['columns'][7] == {'index': 7, 'header': '变动额(元)', 'field': None, 'field_label': None}
    patched = client.patch(f"/api/imports/{body['id']}", json={'mapping': {'cash_change': 7}})
    assert patched.status_code == 200
    patched = patched.json()
    assert patched['rows'][0]['status'] == 'new' and patched['rows'][0]['fee'] == '43.68'
    assert patched['mapping_source'] == 'edited' and patched['columns'][7]['field'] == 'cash_change'
    again = upload(client, data, name='b.csv').json()
    assert again['mapping_source'] == 'saved' and again['rows'][0]['status'] == 'new'
    other = upload(client, data, name='c.csv', account='其他账户').json()
    assert other['mapping_source'] == 'synonyms', 'mappings are saved per account'


ACCOUNTS = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '资金账号', '股东代码']


def test_rows_of_another_account_identity_need_review_until_chosen(client, ledger):
    # Review finding 4: buys under 资金账号 111 and 222 were all booked into the selected account.
    data = csv(ACCOUNTS, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '1000.00', '5.00', '111', 'A000000001'],
               ['2026-11-04', '600036', '招商银行', '证券买入', 100, '30.00', '3000.00', '5.00', '111', 'A000000001'],
               ['2026-11-05', '600000', '浦发银行', '证券买入', 100, '8.00', '800.00', '5.00', '222', 'A000000002'])
    body = upload(client, data, name='accounts.csv').json()
    statuses = [r['status'] for r in body['rows']]
    assert statuses == ['new', 'new', 'review'] and '222' in body['rows'][2]['messages'][0]
    accounts = body['accounts']
    assert accounts['mixed'] is True and accounts['source'] == 'dominant' and set(accounts['accepted']) == {'111', 'A000000001'}
    fund = next(c for c in accounts['columns'] if c['dimension'] == 'fund_account')
    assert fund['label'] == '资金账号' and fund['values'] == [{'value': '111', 'rows': 2, 'accepted': True},
                                                            {'value': '222', 'rows': 1, 'accepted': False}]
    assert any('账户识别' in b and 'account_filter' not in b for b in body['blockers'])
    assert client.post(f"/api/imports/{body['id']}/propose").status_code == 409
    assert client.patch(f"/api/imports/{body['id']}", json={'account_filter': ['333']}).status_code == 422
    chosen = client.patch(f"/api/imports/{body['id']}", json={'account_filter': ['222', 'A000000002']}).json()
    assert [r['status'] for r in chosen['rows']] == ['review', 'review', 'new'] and chosen['blockers'] == []
    assert chosen['accounts']['source'] == 'chosen'
    proposed = client.post(f"/api/imports/{body['id']}/propose").json()
    assert [e['ticker'] for e in proposed['proposal']['events']] == ['SHA:600000']
    again = upload(client, data, name='again.csv').json()
    assert again['accounts']['source'] == 'saved' and again['blockers'] == [], 'remembered with the saved mapping'
    assert [r['status'] for r in again['rows']] == ['review', 'review', 'new']
    other = upload(client, csv(ACCOUNTS, ['2026-11-06', '600519', '贵州茅台', '证券买入', 100, '10.00', '1000.00', '5.00',
                                         '111', 'A000000001']), name='other.csv').json()
    assert other['rows'][0]['status'] == 'review' and '111' in other['rows'][0]['messages'][0], \
        'a file of the identity not chosen for this account'


def test_a_saved_account_choice_applies_only_to_rows_that_match_it(client, ledger):
    # Review round 2, finding 2: with 资金账号 111 saved, a later file with blank 资金账号 and the never chosen
    # 股东代码 A999999999 became new buys.
    data = csv(ACCOUNTS, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '1000.00', '5.00', '111', 'A000000001'],
               ['2026-11-05', '600000', '浦发银行', '证券买入', 100, '8.00', '800.00', '5.00', '222', 'A000000002'])
    body = upload(client, data, name='accounts.csv').json()
    only_fund = client.patch(f"/api/imports/{body['id']}", json={'account_filter': ['111']}).json()
    assert [r['status'] for r in only_fund['rows']] == ['review', 'review'], '股东代码 has two values here: choose it too'
    chosen = client.patch(f"/api/imports/{body['id']}", json={'account_filter': ['111', 'A000000001']}).json()
    assert [r['status'] for r in chosen['rows']] == ['new', 'review']
    later = upload(client, csv(ACCOUNTS, ['2026-11-06', '600519', '贵州茅台', '证券买入', 100, '10.00', '1000.00', '5.00', '',
                                         'A999999999'],
                               ['2026-11-06', '600036', '招商银行', '证券买入', 100, '30.00', '3000.00', '5.00', '', '']),
                   name='later.csv').json()
    assert later['accounts']['source'] == 'saved'
    stranger, blank = later['rows']
    assert stranger['status'] == 'review' and 'account' in stranger['needs'] and 'A999999999' in stranger['messages'][0]
    assert blank['status'] == 'review' and 'account' in blank['needs'], 'no 资金账号 where the choice expects one'
    assert later['counts']['included_events'] == 0
    assert client.post(f"/api/imports/{later['id']}/propose").status_code == 409
    same = upload(client, csv(ACCOUNTS, ['2026-11-07', '600519', '贵州茅台', '证券买入', 100, '10.00', '1000.00', '5.00', '111',
                                        'A000000001']), name='same.csv').json()
    assert same['rows'][0]['status'] == 'new', 'a row that matches the saved choice stays bookable'
    accepted = client.patch(f"/api/imports/{later['id']}", json={'account_filter': ['A999999999']}).json()
    assert [r['status'] for r in accepted['rows']] == ['new', 'review']


def test_rows_without_an_account_in_a_mixed_file_need_a_choice_whatever_was_chosen(client, ledger):
    data = csv(ACCOUNTS, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '1000.00', '5.00', '', 'A000000001'],
               ['2026-11-04', '600000', '浦发银行', '证券买入', 100, '8.00', '800.00', '5.00', '', 'A000000002'],
               ['2026-11-04', '', '', '银行转证券', '', '', '', '', '', ''])
    body = upload(client, data, name='mixed_blank.csv').json()
    chosen = client.patch(f"/api/imports/{body['id']}", json={'account_filter': ['A000000001']}).json()
    assert [r['status'] for r in chosen['rows']] == ['new', 'review', 'review']
    assert 'account' in chosen['rows'][2]['needs'], 'two accounts in the file: a row without one belongs to neither'


def test_one_shanghai_and_one_shenzhen_shareholder_code_are_one_account(client, ledger):
    body = upload(client, fixture('ths_trades_gbk.xls')).json()
    assert body['accounts']['mixed'] is False and body['blockers'] == []
    assert {c['dimension'] for c in body['accounts']['columns']} == {'shareholder:SH', 'shareholder:SZ'}


def test_an_even_split_between_identities_has_no_default(client, ledger):
    data = csv(ACCOUNTS, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '1000.00', '5.00', '111', ''],
               ['2026-11-04', '600036', '招商银行', '证券买入', 100, '30.00', '3000.00', '5.00', '222', ''],
               ['2026-11-04', '', '', '银行转证券', '', '', '', '', '', ''])
    body = upload(client, data, name='even.csv').json()
    assert [r['status'] for r in body['rows']] == ['review', 'review', 'review'] and body['accounts']['accepted'] == []
    assert '无法判断' in body['rows'][2]['messages'][0]
    chosen = client.patch(f"/api/imports/{body['id']}", json={'account_filter': ['111', '']}).json()
    assert [r['status'] for r in chosen['rows']] == ['new', 'review', 'review'], 'the deposit has no amount'
    assert chosen['accounts']['accept_rows_without_account'] is True


SHEET_HEADER = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费']


def workbook(*sheets):
    """xlsx bytes; each sheet is (title, hidden, rows)."""
    import io
    import openpyxl
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, hidden, rows in sheets:
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
        if hidden:
            sheet.sheet_state = 'hidden'
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_hidden_sheets_are_ignored_by_default(client, ledger):
    # Review finding 11: a hidden "old" sheet was imported instead of the visible "Current" one.
    data = workbook(('old', True, [SHEET_HEADER, ['2026-11-03', '000001', '平安银行', '证券买入', 100, 10, 1000, 5]]),
                    ('Current', False, [SHEET_HEADER, ['2026-11-03', '600036', '招商银行', '证券买入', 200, 30, 6000, 5]]))
    body = upload(client, data, name='hidden.xlsx').json()
    assert body['format']['sheet'] == 'Current' and body['rows'][0]['code'] == '600036' and body['blockers'] == []
    assert body['format']['sheet_options'] == [{'name': 'old', 'hidden': True, 'data_rows': 1},
                                               {'name': 'Current', 'hidden': False, 'data_rows': 1}]
    assert body['format']['sheet_chosen'] is False
    hidden = client.patch(f"/api/imports/{body['id']}", json={'sheet': 'old'}).json()
    assert hidden['format']['sheet'] == 'old' and hidden['rows'][0]['code'] == '000001' and hidden['blockers'] == []
    assert client.patch(f"/api/imports/{body['id']}", json={'sheet': 'missing'}).status_code == 422


def test_two_visible_data_sheets_need_a_choice(client, ledger):
    data = workbook(('A', False, [SHEET_HEADER, ['2026-11-03', '000001', '平安银行', '证券买入', 100, 10, 1000, 5]]),
                    ('B', False, [SHEET_HEADER, ['2026-11-03', '600036', '招商银行', '证券买入', 200, 30, 6000, 5]]),
                    ('notes', False, [['说明'], ['这是一张说明表']]))
    body = upload(client, data, name='two.xlsx').json()
    assert body['format']['sheet'] == 'A' and any('工作表' in b and '“B”' in b for b in body['blockers'])
    assert [o['name'] for o in body['format']['sheet_options']] == ['A', 'B']
    assert client.post(f"/api/imports/{body['id']}/propose").status_code == 409
    chosen = client.patch(f"/api/imports/{body['id']}", json={'sheet': 'B'}).json()
    assert chosen['format']['sheet'] == 'B' and chosen['format']['sheet_chosen'] is True and chosen['blockers'] == []
    proposed = client.post(f"/api/imports/{body['id']}/propose").json()
    assert [e['ticker'] for e in proposed['proposal']['events']] == ['SHA:600036']


def test_a_workbook_with_only_hidden_data_needs_a_choice(client, ledger):
    data = workbook(('old', True, [SHEET_HEADER, ['2026-11-03', '000001', '平安银行', '证券买入', 100, 10, 1000, 5]]),
                    ('cover', False, [['封面']]))
    body = upload(client, data, name='only_hidden.xlsx').json()
    assert body['format']['sheet'] == 'old' and any('隐藏' in b for b in body['blockers'])
    chosen = client.patch(f"/api/imports/{body['id']}", json={'sheet': 'old'}).json()
    assert chosen['blockers'] == []


DUPLICATE = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交价格', '手续费']


def test_duplicate_columns_with_different_values_must_be_chosen(client, ledger):
    # Review finding 10: the first of two 成交价格 columns (10 and 11) was used without a word.
    data = csv(DUPLICATE, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '11.00', '5.00'])
    body = upload(client, data, name='duplicate_headers.csv').json()
    assert body['column_conflicts'] == [{'field': 'price', 'label': '成交价格', 'columns': [5, 6], 'chosen': 5,
                                         'rows': [2], 'resolved': False}]
    assert any('成交价格' in b and '第 6 列' in b and '第 7 列' in b for b in body['blockers'])
    refused = client.post(f"/api/imports/{body['id']}/propose")
    assert refused.status_code == 409 and '成交价格' in refused.json()['detail']
    chosen = client.patch(f"/api/imports/{body['id']}", json={'mapping': {'price': 6}}).json()
    assert chosen['blockers'] == [] and chosen['column_conflicts'][0]['resolved'] is True
    assert chosen['rows'][0]['events'][0]['price'] == '11'
    proposed = client.post(f"/api/imports/{body['id']}/propose").json()
    assert proposed['proposal']['events'][0]['price'] == '11'
    again = upload(client, data, name='again.csv').json()
    assert again['mapping_source'] == 'saved' and again['blockers'] == [], 'the choice is remembered with the mapping'
    same = csv(DUPLICATE, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '10', '5.00'])
    assert upload(client, same, name='same.csv', account='其他账户').json()['column_conflicts'] == [], 'equal values'


ACTIONS = ['成交日期', '证券代码', '证券名称', '操作', '业务名称', '成交数量', '成交价格', '成交金额', '手续费']


def test_contradictory_action_columns_must_be_chosen(client, ledger):
    # Review round 2, finding 4: 操作=卖出 and 业务名称=买入 on one row became an included buy.
    data = csv(ACTIONS, ['2026-11-03', '600519', '贵州茅台', '卖出', '买入', 100, '10.00', '1000.00', '5.00'])
    body = upload(client, data, name='actions.csv').json()
    conflict = next(c for c in body['column_conflicts'] if c['field'] == 'action')
    assert conflict['columns'] == [3, 4] and conflict['resolved'] is False and conflict['rows'] == [2]
    assert any('操作' in b and '业务名称' in b for b in body['blockers'])
    assert client.post(f"/api/imports/{body['id']}/propose").status_code == 409
    chosen = client.patch(f"/api/imports/{body['id']}", json={'mapping': {'action': 4}}).json()
    assert chosen['blockers'] == [] and chosen['rows'][0]['status'] == 'new'
    assert chosen['rows'][0]['events'][0]['kind'] == 'buy'
    agreeing = csv(ACTIONS, ['2026-11-03', '600519', '贵州茅台', '买入', '证券买入', 100, '10.00', '1000.00', '5.00'],
                   ['2026-11-04', '600519', '贵州茅台', '', '证券买入', 100, '10.00', '1000.00', '5.00'],
                   ['2026-11-05', '600519', '贵州茅台', '卖出', '证券卖出', 100, '10.00', '1000.00', '5.00'])
    same = upload(client, agreeing, name='agreeing.csv', account='其他账户').json()
    assert [c for c in same['column_conflicts'] if c['field'] == 'action'] == [], 'the same direction in other words'


def test_two_market_columns_naming_different_exchanges_must_be_chosen(client, ledger):
    # Sweep after review round 2: the market column decides the exchange of a code, but a second one was ignored.
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '交易市场', '市场']
    body = upload(client, csv(header, ['2026-11-03', '000001', '平安银行', '证券买入', 100, '10.00', '1000.00', '5.00',
                                       '深圳A股', '上海']), name='markets.csv').json()
    assert [c['field'] for c in body['column_conflicts']] == ['market'] and body['blockers']
    same = upload(client, csv(header, ['2026-11-03', '000001', '平安银行', '证券买入', 100, '10.00', '1000.00', '5.00',
                                       '深圳A股', '深市']), name='same_market.csv', account='其他账户').json()
    assert same['column_conflicts'] == [], 'the same exchange in other words'


def test_mappings_saved_before_options_existed_still_apply(client, ledger):
    from core import desk_store
    from core.importers import mapping
    desk_store.ensure_schema(mapping.SCHEMA, mapping.MIGRATIONS[:1])
    data = csv(DUPLICATE, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '11.00', '5.00'])
    with desk_store.transaction() as db:
        db.execute('INSERT INTO import_mappings(account, signature, statement, mapping, updated_at) VALUES (?,?,?,?,?)',
                   (ACCOUNT, mapping.signature(DUPLICATE), 'transactions',
                    desk_store.encode(mapping.detect(DUPLICATE, 'transactions')), desk_store.now()))
    body = upload(client, data, name='old.csv').json()
    assert body['mapping_source'] == 'saved' and body['column_conflicts'][0]['resolved'] is False
    assert desk_store.schema_version(mapping.SCHEMA) == len(mapping.MIGRATIONS)


def test_a_fee_column_listed_twice_must_be_chosen(client, ledger):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '手续费', '手续费']
    body = upload(client, csv(header, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '5.00', '5.00']),
                  name='fees.csv').json()
    assert [c['field'] for c in body['column_conflicts']] == ['fees'] and body['blockers']
    chosen = client.patch(f"/api/imports/{body['id']}", json={'mapping': {'fees': [6]}}).json()
    assert chosen['blockers'] == [] and chosen['rows'][0]['fee'] == '5'


def test_row_edits_filters_and_us_exchange_choice(client):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '币种']
    data = csv(header, ['2026-11-03', 'EXAMPLE', 'Example Corp', '买入', 1, '10.00', '10.00', '1.00', '美元'],
               ['2026-11-04', '600519', '贵州茅台', '撤单', 0, '0', '0', '0', '人民币'])
    body = upload(client, data, name='us.csv').json()
    row = body['rows'][0]
    assert row['status'] == 'review' and row['choices'] == {'ticker': ['NASDAQ:EXAMPLE', 'NYSE:EXAMPLE']}
    patched = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, 'ticker': 'NYSE:EXAMPLE'}]}).json()
    assert patched['rows'][0]['ticker'] == 'NYSE:EXAMPLE'
    assert aliases.lookup(['US:EXAMPLE']) == {'US:EXAMPLE': 'NYSE:EXAMPLE'}
    assert upload(client, data, name='us2.csv').json()['rows'][0]['ticker'] == 'NYSE:EXAMPLE'
    filtered = client.get(f"/api/imports/{body['id']}", params={'status': 'skipped'}).json()
    assert filtered['rows_total'] == 1 and [r['row'] for r in filtered['rows']] == [3]
    page = client.get(f"/api/imports/{body['id']}", params={'offset': 1, 'limit': 1}).json()
    assert page['rows_total'] == 2 and [r['row'] for r in page['rows']] == [3]
    reset = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, 'reset': True}]}).json()
    assert reset['rows'][0]['edits'] == {} and reset['rows'][0]['ticker'] == 'NYSE:EXAMPLE', 'remembered alias'


@pytest.mark.parametrize('request_kwargs, status, words', [
    ({'files': {'file': ('a.csv', b'', 'text/csv')}, 'data': {'account': ACCOUNT}}, 422, '空的'),
    ({'data': {'account': ACCOUNT}}, 422, '请选择要导入的文件'),
    ({'files': {'file': ('a.csv', b'x', 'text/csv')}}, 422, '请选择账户'),
    ({'files': {'file': ('a.csv', b'x' * (sniff.MAX_BYTES + 1), 'text/csv')}, 'data': {'account': ACCOUNT}}, 413, '5 MB'),
    ({'files': {'file': ('a.xls', b'\x00\x01' * 99, 'application/vnd.ms-excel')}, 'data': {'account': ACCOUNT}}, 415, '二进制'),
    ({'files': {'file': ('a.xlsx', b'PK\x03\x04garbage', 'application/zip')}, 'data': {'account': ACCOUNT}}, 422, 'xlsx'),
    ({'files': {'file': ('a.csv', '成交日期,证券代码,成交数量\n'.encode(), 'text/csv')},
      'data': {'account': ACCOUNT, 'statement': 'everything'}}, 422, '文件类型'),
])
def test_parse_errors_are_chinese_4xx(client, request_kwargs, status, words):
    response = client.post('/api/imports/parse', **request_kwargs)
    assert response.status_code == status and words in response.json()['detail']


def test_unexpected_parser_failure_is_a_4xx_without_internals(client, monkeypatch):
    def broken(data, filename=''):
        raise RuntimeError('internal detail 76543.21')
    monkeypatch.setattr(sniff, 'sniff', broken)
    monkeypatch.setattr(sniff, 'sniff_all', broken)   # what session.create calls
    response = upload(client, b'anything')
    assert response.status_code == 422 and '76543.21' not in response.text and '无法解析' in response.json()['detail']


def test_session_errors(client):
    body = upload(client, fixture('tdx_trades_gbk.txt'), name='tdx.txt').json()
    for response in (client.get('/api/imports/' + '0' * 32), client.get('/api/imports/not-an-id'),
                     client.post('/api/imports/' + '0' * 32 + '/propose'),
                     client.patch('/api/imports/' + '0' * 32, json={})):
        assert response.status_code == 404 and '导入记录不存在' in response.json()['detail']
    for params in ({'status': 'everything'}, {'offset': -1}, {'limit': 0}):
        assert client.get(f"/api/imports/{body['id']}", params=params).status_code == 422
    for payload, words in (({'colour': 1}, '不能修改'), ({'rows': 'all'}, '对象数组'), ({'mapping': [1]}, 'mapping'),
                           ({'rows': [{'row': 3, 'fee': 'abc'}]}, '不是数字')):
        response = client.patch(f"/api/imports/{body['id']}", json=payload)
        assert response.status_code == 422 and words in response.json()['detail']
    assert client.delete(f"/api/imports/{body['id']}").json() == {'deleted': True}
    assert client.get(f"/api/imports/{body['id']}").status_code == 404


def test_a_rejected_patch_changes_neither_the_session_nor_saved_mappings(client):
    data = csv(['发生日期', '业务名称', '发生金额', '备用金额'], ['2026-11-03', '银行转证券', '10', '100'])
    body = upload(client, data, name='rejected_mapping.csv').json()
    assert body['rows'][0]['events'][0]['amount'] == '10'
    for payload in ({'mapping': {'cash_change': 3}, 'rows': [{'row': 999}]},
                    {'mapping': {'cash_change': 3}, 'opening': {'date': 'tomorrow'}},
                    {'short_code_market': 'HKG', 'rows': [{'row': 2, 'amount': '200'}, {'row': 2, 'fee': 'abc'}]}):
        response = client.patch(f"/api/imports/{body['id']}", json=payload)
        assert response.status_code == 422, payload
    after = client.get(f"/api/imports/{body['id']}").json()
    assert after['mapping'] == body['mapping'] and after['mapping_source'] == body['mapping_source']
    assert after['short_code_market'] is None and after['updated_at'] == body['updated_at']
    assert after['rows'][0]['edits'] == {} and after['rows'][0]['events'][0]['amount'] == '10'
    again = upload(client, data, name='again.csv').json()
    assert again['mapping_source'] != 'saved' and again['rows'][0]['events'][0]['amount'] == '10'


def test_malformed_numbers_make_review_rows_or_422_never_500(client, monkeypatch):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交金额', '手续费']
    response = upload(client, csv(header, ['2026-11-03', '600519', '贵州茅台', '证券买入', '1e-100', '1000.00', '5.00']),
                      name='bad_quantity.csv')
    assert response.status_code == 200
    [row] = response.json()['rows']
    assert row['status'] == 'review' and row['events'] == [] and 'quantity' in row['needs']
    assert client.get(f"/api/imports/{response.json()['id']}").status_code == 200
    body = upload(client, csv(header, ['2026-11-03', '600519', '贵州茅台', '证券买入', '100', '168000.00', '5.00']),
                  name='good.csv').json()
    assert body['rows'][0]['status'] == 'new'
    for edit in ({'quantity': '1e-100'}, {'quantity': '0.' + '0' * 30 + '1'}, {'price': '1' + '0' * 20},
                 {'amount': '9' * 30}, {'fx_rate': '100000'}, {'fee': '1e3'}, {'quantity': 'NaN'}):
        response = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, **edit}]})
        assert response.status_code == 422, edit
    for opening in ({'cash_balances': {'CNY': '1e400'}}, {'contributed_cny': '9' * 30}, {'fund_cny': 'Infinity'}):
        assert client.patch(f"/api/imports/{body['id']}", json={'opening': opening}).status_code == 422, opening
    view = client.get(f"/api/imports/{body['id']}")
    assert view.status_code == 200 and view.json()['rows'][0]['status'] == 'new' and view.json()['rows'][0]['edits'] == {}
    from decimal import InvalidOperation
    from core.importers import transactions

    def broken(*_args, **_kwargs):
        raise InvalidOperation('quantize')
    monkeypatch.setattr(transactions, '_price', broken)
    response = upload(client, csv(header, ['2026-11-03', '600519', '贵州茅台', '证券买入', '100', '168000.00', '5.00']),
                      name='arith.csv')
    assert response.status_code == 200 and response.json()['rows'][0]['status'] == 'review'
    assert '无法计算' in response.json()['rows'][0]['messages'][-1]


def test_a_session_stored_in_the_earlier_format_still_opens(client, ledger):
    # Review round 2, finding 9: a fee edit saved before the stricter number parser (0.12345678901) made
    # GET /api/imports/{id} answer 500. A stored edit that today's rules refuse makes its row 需处理 instead.
    from core import desk_store
    created = session.create(fixture('ths_trades_gbk.xls'), 'jgd.xls', ACCOUNT)
    # The payload as the first release of the importer wrote it (no sheets, choices or fingerprints).
    old = {key: created[key] for key in ('id', 'account', 'created_at', 'updated_at', 'file', 'signature', 'statement',
                                         'mapping', 'mapping_source', 'short_code_market', 'opening')}
    old['table'] = {k: v for k, v in created['table'].items() if k not in ('sheet_options', 'sheet_ambiguous')}
    old['edits'] = {'3': {'fee': '0.12345678901'}, '4': {'quantity': '1e3'}, '5': {'include': 'yes'},
                    '10': {'ticker': 'SHA:787001', 'price': '11.00'}}
    old['batches'] = [{'number': 1, 'proposal_id': '0' * 32, 'events': 5, 'rows': [3, 4, 5, 7, 10],
                       'created_at': created['created_at']}]
    with desk_store.transaction() as db:
        db.execute('UPDATE import_sessions SET payload=? WHERE id=?', (desk_store.encode(old), created['id']))
    response = client.get(f"/api/imports/{created['id']}")
    assert response.status_code == 200
    body = response.json()
    rows = {row['row']: row for row in body['rows']}
    for number, field in ((3, 'fee'), (4, 'quantity'), (5, 'include'), (10, 'ticker')):
        row = rows[number]
        assert row['status'] == 'review' and row['events'] == [] and field in row['needs'], row
        assert any('之前保存的修改' in message for message in row['messages'])
    assert rows[7]['status'] == 'new' and body['batches']['history'][0]['state'] == 'discarded'
    repaired = client.patch(f"/api/imports/{created['id']}", json={'rows': [{'row': 3, 'fee': None}]})
    assert repaired.status_code == 200
    assert next(r for r in repaired.json()['rows'] if r['row'] == 3)['status'] == 'new'
    proposed = client.post(f"/api/imports/{created['id']}/propose")
    assert proposed.status_code == 200 and proposed.json()['batch']['rows'] == [3, 7]


def test_stored_opening_values_are_checked_again(client, ledger):
    # Sweep after review round 2: opening values saved under looser rules went into the opening event unchecked.
    from core import desk_store
    created = session.create(fixture('ths_holdings_gbk.xls'), 'cc.xls', '新账户')
    stored = session.load(created['id'])
    stored['edits'] = {'8': {'include': False}}
    stored['opening'] = {'date': '2026-11-14', 'cash_balances': {'CNY': '1e3'}, 'contributed_cny': '200000'}
    with desk_store.transaction() as db:
        db.execute('UPDATE import_sessions SET payload=? WHERE id=?', (desk_store.encode(stored), created['id']))
    body = client.get(f"/api/imports/{created['id']}").json()
    assert any('期初' in blocker and '1e3' in blocker for blocker in body['blockers']), body['blockers']
    assert client.post(f"/api/imports/{created['id']}/propose").status_code == 409
    fixed = client.patch(f"/api/imports/{created['id']}", json={'opening': {'cash_balances': {'CNY': '1000'}}}).json()
    assert fixed['blockers'] == []


def test_sessions_and_their_rows_are_deleted_30_days_after_upload(client, ledger):
    from datetime import datetime, timezone
    from core import desk_store

    def age(session_id, days):
        past = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec='seconds')
        with desk_store.transaction() as db:
            db.execute('UPDATE import_sessions SET created_at=? WHERE id=?', (past, session_id))

    def stored():
        with desk_store.connect() as db:
            return {row['id'] for row in db.execute('SELECT id FROM import_sessions')}
    data = fixture('tdx_trades_gbk.txt')
    old = upload(client, data, name='old.txt').json()
    kept = upload(client, data, name='kept.txt').json()
    age(old['id'], 31)
    age(kept['id'], 29)
    assert client.get(f"/api/imports/{old['id']}").status_code == 404, 'expired at once, before any clean-up'
    assert old['id'] in stored(), 'a read deletes nothing'
    assert client.patch(f"/api/imports/{kept['id']}", json={}).status_code == 200
    assert stored() == {kept['id']}, 'any import write removes expired sessions with their rows'
    assert client.patch(f"/api/imports/{old['id']}", json={}).status_code == 404


def test_reads_on_an_empty_portfolio_directory_create_no_file(tmp_path, monkeypatch):
    from core import desk_store
    from core.importers import mapping
    empty = tmp_path / 'portfolio'
    empty.mkdir()
    monkeypatch.setenv('PORTFOLIO_DIR', str(empty))
    app = FastAPI()
    app.include_router(api.router, prefix='/api/imports')
    client = TestClient(app)
    response = client.get('/api/imports/' + 'a' * 32)
    assert response.status_code == 404 and '导入记录不存在' in response.json()['detail']
    assert mapping.saved(ACCOUNT, 'x' * 40) is None and aliases.lookup() == {} and aliases.lookup(['US:X']) == {}
    assert list(empty.iterdir()) == [], 'a read never creates the desk store or the ledger'
    for name in ('import_sessions', 'import_mappings', 'instrument_aliases'):
        assert name in desk_store._registered, 'the schema is registered for start-up upgrades'


def test_viewing_a_session_never_creates_the_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    created = session.create(fixture('tdx_trades_gbk.txt'), 'tdx.txt', ACCOUNT)
    assert not (tmp_path / 'ledger.sqlite3').exists()
    view = session.view(session.load(created['id']))
    assert view['account_exists'] is False and view['ledger_revision'] == 0
    assert not (tmp_path / 'ledger.sqlite3').exists(), 'only propose may create the ledger file'


def test_router_is_registered_as_a_feature():
    mounted = {prefix: router for router, prefix, _tags in features.routers()}
    assert mounted.get('/api/imports') is api.router


def test_demo_mode_reads_work_and_writes_are_rejected(ledger, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # importing the application creates data directories in the working directory
    from backend.main import app
    # The real application only answers loopback Host headers.
    client = TestClient(app, base_url='http://127.0.0.1')
    created = session.create(fixture('tdx_trades_gbk.txt'), 'tdx.txt', ACCOUNT)
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    view = client.get(f"/api/imports/{created['id']}")
    assert view.status_code == 200 and view.json()['counts']['new'] == 3
    for response in (upload(client, fixture('tdx_trades_gbk.txt'), name='tdx.txt'),
                     client.patch(f"/api/imports/{created['id']}", json={'rows': [{'row': 3, 'include': False}]}),
                     client.post(f"/api/imports/{created['id']}/propose"),
                     client.delete(f"/api/imports/{created['id']}")):
        assert response.status_code == 403 and response.json() == {'detail': 'Demo mode is read-only'}
    assert ledger.state()['revision'] == 1
    monkeypatch.delenv('TRACKER_DEMO_MODE')
    proposed = client.post(f"/api/imports/{created['id']}/propose")
    assert proposed.status_code == 200 and len(proposed.json()['proposal']['events']) == 3


def test_the_interest_kind_is_labelled_in_the_backend():
    # Review finding 17: interest events had no label where event kinds are described.
    from core.importers.transactions import CATEGORY_LABELS
    from core.tools import propose_ledger_tool
    description = propose_ledger_tool().description
    assert 'interest' in description and '利息' in description
    assert CATEGORY_LABELS['interest'] == '利息'
