"""Review round 3 of the broker importer (API level): bindings, upgrades, identity memory and locking.

Each section reproduces one finding of the third review (``(A)`` / ``(B)`` with the file and line it names).
"""
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
import threading
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import imports as api
from backend.api import ledger as ledger_api
from core import desk_store
from core.importers import aliases, session
from core.ledger import encode, normalize
from tests.importer_support import ACCOUNT, book, csv, ledger_at, open_account


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


def upload(client, data, name='交割单.csv', account=ACCOUNT):
    response = client.post('/api/imports/parse', files={'file': (name, data, 'application/octet-stream')},
                           data={'account': account})
    assert response.status_code == 200, response.json()
    return response.json()


def propose(client, body) -> dict:
    response = client.post(f"/api/imports/{body['id']}/propose")
    assert response.status_code == 200, response.json()
    return response.json()['proposal']


def confirm(client, proposal_id):
    return client.post(f'/api/ledger/proposals/{proposal_id}/confirm')


def cash(ledger, unit='CNY'):
    return ledger.state()['accounts'][ACCOUNT]['cash_balances'].get(unit)


def expire(session_id):
    past = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec='seconds')
    with desk_store.transaction() as db:
        db.execute('UPDATE import_sessions SET created_at=? WHERE id=?', (past, session_id))


DEPOSIT = csv(['发生日期', '业务名称', '发生金额'], ['2026-11-03', '银行转证券', '10'])


# -- (A) session.py:374,784: events and fingerprint come from one snapshot of the remembered exchanges ----------

def test_a_remembered_exchange_changed_while_proposing_refuses_the_proposal(client, ledger, monkeypatch):
    book(ledger, [{'kind': 'deposit', 'date': '2026-11-02', 'account': ACCOUNT, 'currency': 'USD', 'amount': '100',
                   'fx_rate': '7'}])
    aliases.remember('US:EXAMPLE', 'NASDAQ:EXAMPLE')
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '币种']
    body = upload(client, csv(header, ['2026-11-03', 'EXAMPLE', 'Example', '买入', 1, '10', '10', '1', '美元']), 'us.csv')
    assert body['rows'][0]['events'][0]['ticker'] == 'NASDAQ:EXAMPLE'
    real = session.analyse

    def changed_meanwhile(*args, **kwargs):
        result = real(*args, **kwargs)
        aliases.remember('US:EXAMPLE', 'NYSE:EXAMPLE')   # another session's choice, after this analysis
        return result
    monkeypatch.setattr(session, 'analyse', changed_meanwhile)
    refused = client.post(f"/api/imports/{body['id']}/propose")
    monkeypatch.setattr(session, 'analyse', real)
    assert refused.status_code == 409, refused.json()
    assert ledger.proposals() == []
    again = propose(client, body)
    assert [e['ticker'] for e in again['events']] == ['NYSE:EXAMPLE']


# -- (A) core/ledger.py:577; session.py:201,545; docs/LEDGER.md:149: proposals of earlier releases --------------

def stored_proposal(ledger, events, binding=None):
    """A pending proposal written into the ledger file the way an earlier release wrote it."""
    revision = ledger.state()['revision']
    payload = {'events': [normalize({k: v for k, v in e.items() if k != 'id'}) for e in events], 'before': {},
               'after': {}, 'duplicates': 0, 'warnings': []}
    proposal_id, created = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
    db = sqlite3.connect(str(ledger.path))
    with db:
        db.execute('INSERT INTO proposals VALUES (?,?,?,?,NULL)', (proposal_id, revision, encode(payload), created))
        if binding is not None:
            db.execute("INSERT INTO proposal_bindings(proposal_id, scope, subject, token, revision, state, info, "
                       "created_at) VALUES (?,?,?,?,?,'pending',?,?)",
                       (proposal_id, 'import_session', binding['subject'], binding['token'], revision,
                        encode({'number': 1, 'rows': binding['rows'], 'events': len(events)}), created))
    db.close()
    return proposal_id


def test_a_proposal_made_before_bindings_existed_can_never_be_confirmed(client, ledger):
    body = upload(client, DEPOSIT, 'old.csv')
    old = stored_proposal(ledger, body['rows'][0]['events'])
    stored = session.load(body['id'])
    stored['batches'] = [{'number': 1, 'proposal_id': old, 'events': 1, 'rows': [2]}]
    session._store(stored)
    refused = confirm(client, old)
    assert refused.status_code == 409 and cash(ledger) == '500000', refused.json()
    assert ledger.proposal(old) is None and confirm(client, old).status_code == 409, 'withdrawn, and says so'
    # After the session expired and was purged, too.
    body = upload(client, DEPOSIT, 'expired.csv')
    older = stored_proposal(ledger, body['rows'][0]['events'])
    stored = session.load(body['id'])
    stored['batches'] = [{'number': 1, 'proposal_id': older, 'events': 1, 'rows': [2]}]
    session._store(stored)
    expire(body['id'])
    upload(client, DEPOSIT, 'purge.csv')
    assert confirm(client, older).status_code == 409
    assert cash(ledger) == '500000' and ledger.proposals() == []


def earlier_token(stored):
    """The fingerprint an earlier release computed: no interpretation version (as of 1d5faea)."""
    content = {key: stored.get(key) for key in session.CONTENT_KEYS}
    return hashlib.sha256(desk_store.encode([stored['id'], stored['account'], content, aliases.lookup()]).encode()).hexdigest()


def test_a_bound_proposal_built_by_earlier_rules_is_stale(client, ledger):
    header = ['发生日期', '业务名称', '发生金额', '股东代码']
    body = upload(client, csv(header, ['2026-11-03', '银行转证券', '1', 'A000000001'],
                              ['2026-11-04', '银行转证券', '2', 'A000000002'], ['2026-11-05', '银行转证券', '100', '']),
                  'rules.csv')
    chosen = client.patch(f"/api/imports/{body['id']}", json={'account_filter': ['A000000001']}).json()
    assert [r['status'] for r in chosen['rows']] == ['new', 'review', 'review']
    # What the earlier rules proposed for this very session: the deposit without an identity passed as well.
    blank = chosen['rows'][2]
    events = chosen['rows'][0]['events'] + [
        normalize({'kind': 'deposit', 'date': '2026-11-05', 'account': ACCOUNT, 'source': f'import:{ACCOUNT}',
                   'external_id': blank['external_id'], 'currency': 'CNY', 'amount': '100', 'note': '银行转证券'})]
    old = stored_proposal(ledger, events, {'subject': body['id'], 'token': earlier_token(session.load(body['id'])),
                                           'rows': [2, 4]})
    assert client.get(f"/api/imports/{body['id']}").json()['batches']['pending']['stale'] is True
    refused = confirm(client, old)
    assert refused.status_code == 409 and cash(ledger) == '500000', refused.json()
    assert ledger.proposals() == []
    fresh = propose(client, body)
    assert [e['amount'] for e in fresh['events']] == ['1']
    assert confirm(client, fresh['id']).status_code == 200 and cash(ledger) == '500001'


# -- (A) accounts.py:119,176; session.py:274: identities remembered per ledger account ---------------------------

FUNDS = ['发生日期', '业务名称', '发生金额', '资金账号']
REORDERED = ['资金账号', '发生日期', '业务名称', '发生金额']   # another header layout: no saved mapping applies


def test_an_identity_excluded_earlier_needs_review_in_a_file_that_holds_only_it(client, ledger):
    body = upload(client, csv(FUNDS, ['2026-11-03', '银行转证券', '10', '111'], ['2026-11-04', '银行转证券', '20', '222']),
                  'both.csv')
    assert client.patch(f"/api/imports/{body['id']}", json={'account_filter': ['111']}).status_code == 200
    other = upload(client, csv(REORDERED, ['222', '2026-11-05', '银行转证券', '10']), 'only222.csv')
    row = other['rows'][0]
    assert row['status'] == 'review' and 'account' in row['needs'] and '222' in row['messages'][0], row
    assert client.post(f"/api/imports/{other['id']}/propose").status_code == 409
    same = upload(client, csv(REORDERED, ['111', '2026-11-06', '银行转证券', '10']), 'only111.csv')
    assert same['rows'][0]['status'] == 'new'


def test_an_identity_other_than_the_one_imported_before_needs_review(client, ledger):
    first = upload(client, csv(FUNDS, ['2026-11-03', '银行转证券', '10', '111']), 'a.csv')
    assert confirm(client, propose(client, first)['id']).status_code == 200
    other = upload(client, csv(REORDERED, ['222', '2026-11-05', '银行转证券', '10']), 'b.csv')
    row = other['rows'][0]
    assert row['status'] == 'review' and 'account' in row['needs'] and '111' in row['messages'][0], row
    same = upload(client, csv(REORDERED, ['111', '2026-11-06', '银行转证券', '10']), 'c.csv')
    assert same['rows'][0]['status'] == 'new'
    chosen = client.patch(f"/api/imports/{other['id']}", json={'account_filter': ['222']}).json()
    assert chosen['rows'][0]['status'] == 'new', 'the person may still choose it explicitly'


# -- (A) session.py:114; core/ledger.py:411: a read never changes the ledger's schema ---------------------------

def test_reading_an_import_leaves_a_ledger_without_the_binding_table_unchanged(client, ledger, monkeypatch):
    body = upload(client, DEPOSIT, 'old.csv')
    db = sqlite3.connect(str(ledger.path))
    db.execute('DROP INDEX IF EXISTS proposal_bindings_one_pending')
    db.execute('DROP TABLE IF EXISTS proposal_bindings')
    db.commit()
    db.close()
    before = ledger.path.read_bytes()
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    view = client.get(f"/api/imports/{body['id']}")
    assert view.status_code == 200 and view.json()['batches']['pending'] is None
    assert ledger.path.read_bytes() == before, 'the read changed the ledger file'
    monkeypatch.delenv('TRACKER_DEMO_MODE')
    assert confirm(client, propose(client, body)['id']).status_code == 200, 'the first write creates what it needs'


# -- (B) session.py:720,725,184; core/ledger.py:420: a refused PATCH or DELETE stores nothing --------------------

def hold_reader(path):
    """Another connection reading the ledger: a writer cannot commit until it ends."""
    holder = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    holder.execute('BEGIN')
    holder.execute('SELECT COUNT(*) FROM events').fetchone()
    return holder


@pytest.fixture
def short_waits(monkeypatch):
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: real(*a, **{**k, 'timeout': 0.3}))
    monkeypatch.setattr(desk_store, 'BUSY_TIMEOUT_MS', 300)


def reader_once_locked(monkeypatch, ledger, holders):
    """Start a reader as soon as the import holds the ledger's write lock (it withdraws its proposals then)."""
    from core.ledger import Ledger
    real = Ledger.withdraw_bound

    def withdraw_then_read(db, scope, subject):
        result = real(db, scope, subject)
        if not holders:
            holders.append(hold_reader(ledger.path))   # held through the request's ledger commit
        return result
    monkeypatch.setattr(Ledger, 'withdraw_bound', staticmethod(withdraw_then_read))
    return real


def release(monkeypatch, real, holders):
    from core.ledger import Ledger
    monkeypatch.setattr(Ledger, 'withdraw_bound', staticmethod(real))
    for holder in holders:
        holder.execute('ROLLBACK')
        holder.close()


def test_a_patch_whose_ledger_commit_fails_stores_nothing(client, ledger, monkeypatch, short_waits):
    body = upload(client, DEPOSIT, 'busy.csv')
    first = propose(client, body)
    holders = []
    real = reader_once_locked(monkeypatch, ledger, holders)
    try:
        refused = client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, 'amount': '200'}]})
    finally:
        release(monkeypatch, real, holders)
    assert holders and refused.status_code == 503, refused.json()
    after = client.get(f"/api/imports/{body['id']}").json()
    assert after['rows'][0]['edits'] == {} and after['updated_at'] == body['updated_at'], 'nothing was stored'
    assert after['batches']['pending']['proposal_id'] == first['id'] and after['batches']['pending']['stale'] is False
    assert confirm(client, first['id']).status_code == 200 and cash(ledger) == '500010'


def test_a_delete_whose_ledger_commit_fails_deletes_nothing(client, ledger, monkeypatch, short_waits):
    body = upload(client, DEPOSIT, 'busy.csv')
    first = propose(client, body)
    holders = []
    real = reader_once_locked(monkeypatch, ledger, holders)
    try:
        refused = client.delete(f"/api/imports/{body['id']}")
    finally:
        release(monkeypatch, real, holders)
    assert holders and refused.status_code == 503, refused.json()
    assert client.get(f"/api/imports/{body['id']}").status_code == 200, 'the session is still there'
    assert confirm(client, first['id']).status_code == 200 and cash(ledger) == '500010'


def test_a_delete_after_a_concurrent_edit_is_refused(client, ledger, monkeypatch):
    body = upload(client, DEPOSIT, 'race.csv')
    real = session.load

    def loaded_then_edited(session_id):
        loaded = real(session_id)
        monkeypatch.setattr(session, 'load', real)
        session.patch(session_id, {'rows': [{'row': 2, 'amount': '200'}]})   # another request, in between
        return loaded
    monkeypatch.setattr(session, 'load', loaded_then_edited)
    refused = client.delete(f"/api/imports/{body['id']}")
    assert refused.status_code == 409, refused.json()
    after = client.get(f"/api/imports/{body['id']}")
    assert after.status_code == 200 and after.json()['rows'][0]['edits'] == {'amount': '200'}


# -- (B) session.py:720; core/proposals.py:342,362; core/watchlist.py:72: one lock order for both files -----------

def test_a_patch_and_a_watchlist_confirmation_at_the_same_time_both_succeed(client, ledger, monkeypatch):
    from core import proposals, watchlist
    from core.market import search
    search.set_symbol_index(search.SymbolIndex(fetchers={}))
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: real_connect(*a, **{**k, 'timeout': 2}))
    monkeypatch.setattr(desk_store, 'BUSY_TIMEOUT_MS', 2000)
    try:
        body = upload(client, DEPOSIT, 'order.csv')
        propose(client, body)
        proposal = proposals.propose(watchlist.PROPOSAL_KIND, {'ticker': 'SHA:600036'}, 'agent:chat')
        holds_desk, analysed, calls = threading.Event(), threading.Event(), []
        real_known, real_analyse = watchlist._known_tickers, session.analyse

        def known_while_the_desk_is_locked():
            if not calls:   # inside the confirmation's desk transaction
                calls.append(1)
                holds_desk.set()
                analysed.wait(10)
                time.sleep(0.5)   # the PATCH asks for its first lock meanwhile
            return real_known()

        def analyse(*args, **kwargs):
            result = real_analyse(*args, **kwargs)
            analysed.set()
            return result
        monkeypatch.setattr(watchlist, '_known_tickers', known_while_the_desk_is_locked)
        monkeypatch.setattr(session, 'analyse', analyse)
        results = {}

        def confirm_watchlist():
            try:
                results['watchlist'] = proposals.confirm(proposal['id'])['status']
            except Exception as exc:
                results['watchlist'] = repr(exc)

        def patch_import():
            try:
                session.patch(body['id'], {'rows': [{'row': 2, 'amount': '200'}]})
                results['patch'] = 'stored'
            except Exception as exc:
                results['patch'] = repr(exc)
        first = threading.Thread(target=confirm_watchlist)
        first.start()
        assert holds_desk.wait(10)
        second = threading.Thread(target=patch_import)
        second.start()
        first.join(30)
        second.join(30)
        assert results == {'watchlist': 'applied', 'patch': 'stored'}
    finally:
        search.set_symbol_index(None)


# -- (B) core/ledger.py:570,575; backend/api/ledger.py:69: a withdrawn or invalid proposal answers 409 -----------

def test_confirming_a_withdrawn_import_proposal_answers_409(client, ledger):
    body = upload(client, DEPOSIT, 'w.csv')
    first = propose(client, body)
    assert client.patch(f"/api/imports/{body['id']}", json={'rows': [{'row': 2, 'amount': '200'}]}).status_code == 200
    refused = confirm(client, first['id'])
    assert refused.status_code == 409 and refused.json()['detail'], refused.json()
    second = propose(client, body)
    assert client.delete(f"/api/imports/{body['id']}").status_code == 200
    assert confirm(client, second['id']).status_code == 409
    assert cash(ledger) == '500000'


def test_a_proposal_invalid_for_two_reasons_is_withdrawn(client, ledger):
    body = upload(client, DEPOSIT, 'two.csv')
    first = propose(client, body)
    book(ledger, [{'kind': 'deposit', 'date': '2026-11-15', 'account': ACCOUNT, 'currency': 'CNY', 'amount': '1'}])
    expire(body['id'])
    assert confirm(client, first['id']).status_code == 409
    assert ledger.proposals() == [], 'an invalid proposal never stays pending'
