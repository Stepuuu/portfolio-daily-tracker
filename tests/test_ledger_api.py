"""The ledger API as the 记一笔 dialog and the 账本 pages use it. Fictional data only.

Reads never create or change ``ledger.sqlite3``; B shares cannot be booked; a confirmation is idempotent and a
confirmed proposal cannot be discarded; abandoned manual previews expire without crowding out genuine ones;
the form can ask for the trading calendar and for the ledger as of a date; legacy holdings are read exactly.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import ledger as api
from core.ledger import Ledger, LedgerError, normalize
from core.market.runtime import set_market_runtime
from tests.fakes import FakeClock, fake_runtime

DAY = '2026-11-16'


def event(kind, **values):
    return {'kind': kind, 'date': DAY, 'account': 'Example', **values}


def opening(**values):
    return event('opening', cash_balances={'CNY': '10000', 'USD': '1000', 'HKD': '1000'}, contributed_cny='17000', **values)


@pytest.fixture
def directory(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    return tmp_path


@pytest.fixture
def client(directory):
    app = FastAPI()
    app.include_router(api.router, prefix='/api/ledger')
    return TestClient(app)


@pytest.fixture
def ledger(directory):
    ledger = Ledger(directory / 'ledger.sqlite3')
    ledger.confirm(ledger.propose([opening()])['id'])
    return ledger


def schema(path):
    with sqlite3.connect(path) as db:
        return sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"))


# -- B shares --------------------------------------------------------------------------------------

@pytest.mark.parametrize('ticker, currency', [('SHA:900901', 'USD'), ('SHE:200002', 'HKD'), ('SHE:201872', 'HKD')])
def test_a_b_share_cannot_be_booked_by_any_path(ledger, client, ticker, currency):
    refusals = [
        [event('buy', ticker=ticker, currency='CNY', quantity='1000', price='0.498', fee='0')],
        [event('dividend', ticker=ticker, currency='CNY', amount='5')],
        [event('opening', account='New', positions=[{'ticker': ticker, 'quantity': '100', 'cost_price': '1'}])],
    ]
    for events in refusals:
        with pytest.raises(LedgerError) as caught:
            ledger.propose(events)
        assert str(caught.value) == f'{ticker} 是 B 股，以 {currency} 交易；账本按人民币记录沪深标的，暂不支持记入 B 股。'
        response = client.post('/api/ledger/proposals', json={'events': events})
        assert response.status_code == 422
        assert response.json()['detail'] == str(caught.value)
    assert ledger.state()['accounts']['Example']['cash_balances']['CNY'] == '10000'
    # An A share and a Hong Kong listing with similar codes are fine.
    assert ledger.propose([event('buy', ticker='SHA:600901', currency='CNY', quantity='100', price='1', fee='0')])
    assert ledger.propose([event('buy', ticker='HKG:00900', currency='HKD', quantity='100', price='1', fee='0')])


def test_a_b_share_booked_earlier_still_replays_and_can_be_reversed(ledger, client):
    # Rows from before the rule: inserted the way an old release stored them.
    old = {'id': 'old-b-share', 'kind': 'buy', 'date': DAY, 'account': 'Example', 'source': 'manual',
           'ticker': 'SHA:900901', 'currency': 'CNY', 'quantity': '100', 'price': '0.5', 'fee': '0'}
    with sqlite3.connect(ledger.path) as db:
        db.execute('INSERT INTO events(id, payload, proposal_id) VALUES (?,?,?)', (old['id'], json.dumps(old), 'legacy'))
        db.execute('UPDATE ledger_meta SET revision = revision + 1')
    state = client.get('/api/ledger').json()
    assert state['accounts']['Example']['positions']['SHA:900901']['quantity'] == '100'
    preview = client.post('/api/ledger/proposals', json={'events': [{'kind': 'reverse', 'date': DAY, 'target_id': old['id']}]})
    assert preview.status_code == 200, preview.text


# -- confirmation and discard ----------------------------------------------------------------------

def test_confirming_the_same_id_again_returns_the_same_receipt(ledger, client):
    preview = client.post('/api/ledger/proposals', json={'events': [event('deposit', currency='CNY', amount='5')]}).json()
    first = client.post(f"/api/ledger/proposals/{preview['id']}/confirm")
    again = client.post(f"/api/ledger/proposals/{preview['id']}/confirm")
    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    assert len(ledger.history()) == 2
    assert client.get(f"/api/ledger/proposals/{preview['id']}").json()['receipt'] == first.json()


def test_a_confirmed_proposal_cannot_be_discarded(ledger, client):
    preview = client.post('/api/ledger/proposals', json={'events': [event('deposit', currency='CNY', amount='5')]}).json()
    receipt = client.post(f"/api/ledger/proposals/{preview['id']}/confirm").json()
    response = client.delete(f"/api/ledger/proposals/{preview['id']}")
    assert response.status_code == 409
    assert response.json()['detail'] == '这份方案已经入账，不能放弃；如需更正，请在流水中冲销。'
    assert client.get(f"/api/ledger/proposals/{preview['id']}").json()['receipt'] == receipt
    # A pending one is discarded, and discarding it twice (or an unknown id) is not an error.
    pending = client.post('/api/ledger/proposals', json={'events': [event('deposit', currency='CNY', amount='6')]}).json()
    assert client.delete(f"/api/ledger/proposals/{pending['id']}").json() == {'discarded': True}
    assert client.delete(f"/api/ledger/proposals/{pending['id']}").json() == {'discarded': True}
    assert client.get(f"/api/ledger/proposals/{pending['id']}").status_code == 404


# -- reads never write -----------------------------------------------------------------------------

READS = ['/api/ledger', '/api/ledger/events', '/api/ledger/journal', '/api/ledger/proposals/missing',
         f'/api/ledger/as-of?date={DAY}', '/api/ledger/export.json']


def test_reads_without_a_ledger_create_nothing(directory, client):
    for path in READS:
        response = client.get(path)
        assert response.status_code in (200, 404), (path, response.text)
    assert not (directory / 'ledger.sqlite3').exists()
    assert client.get('/api/ledger').json() == {'revision': 0, 'accounts': {}, 'date': None, 'pending': []}
    assert client.get('/api/ledger/events').json() == {'events': []}
    assert client.get('/api/ledger/journal').json() == {'entries': []}
    assert client.get('/api/ledger/backup.sqlite3').status_code == 404
    assert not (directory / 'ledger.sqlite3').exists()


def test_reads_of_an_existing_ledger_change_neither_its_schema_nor_its_bytes(ledger, client):
    pending = ledger.propose([event('deposit', currency='CNY', amount='5')])
    before, tables = ledger.path.read_bytes(), schema(ledger.path)
    for path in READS + [f"/api/ledger/proposals/{pending['id']}", '/api/ledger/backup.sqlite3']:
        assert client.get(path).status_code in (200, 404), path
    assert 'journal' not in tables
    assert schema(ledger.path) == tables
    assert ledger.path.read_bytes() == before
    assert client.get('/api/ledger').json()['pending'][0]['id'] == pending['id']


def test_state_and_pending_come_from_one_read(ledger, client, monkeypatch):
    opened = []
    real = sqlite3.connect

    def counting(*args, **kwargs):
        opened.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(api.sqlite3, 'connect', counting)
    ledger.propose([event('deposit', currency='CNY', amount='5')])
    opened.clear()
    body = client.get('/api/ledger').json()
    assert len(opened) == 1, opened
    assert all(proposal['revision'] <= body['revision'] for proposal in body['pending'])


def test_writing_a_note_still_creates_its_table(ledger, client):
    note = {'ticker': 'NASDAQ:EXAMPLE', 'thesis': '虚构论点', 'invalidation': '虚构条件', 'review_on': DAY}
    assert client.post('/api/ledger/journal', json=note).status_code == 200
    assert client.get('/api/ledger/journal').json()['entries'][0]['thesis'] == '虚构论点'


# -- abandoned previews ----------------------------------------------------------------------------

def test_an_abandoned_manual_preview_expires_and_never_crowds_out_a_genuine_one(ledger, client, monkeypatch):
    genuine = ledger.propose([event('deposit', currency='CNY', amount='1', source='agent:chat')])
    transient = [client.post('/api/ledger/proposals', json={'events': [event('deposit', currency='CNY', amount=str(n + 2))],
                                                              'transient': True}).json() for n in range(31)]
    pending = client.get('/api/ledger').json()['pending']
    assert genuine['id'] in [proposal['id'] for proposal in pending]
    assert all(proposal.get('expires_at') for proposal in pending if proposal['id'] != genuine['id'])
    later = datetime.now(timezone.utc) + api.MANUAL_PREVIEW_LIFETIME + timedelta(minutes=1)
    monkeypatch.setattr(api, 'utc_now', lambda: later)
    assert [proposal['id'] for proposal in client.get('/api/ledger').json()['pending']] == [genuine['id']]
    refused = client.post(f"/api/ledger/proposals/{transient[0]['id']}/confirm")
    assert refused.status_code == 409
    assert refused.json()['detail'] == '这份预览超过 2 小时没有确认，已失效，没有入账。请重新预览。'
    assert client.get(f"/api/ledger/proposals/{transient[0]['id']}").json()['expired'] is True
    # The next write removes them; a confirmation within the lifetime works as always.
    fresh = client.post('/api/ledger/proposals', json={'events': [event('deposit', currency='CNY', amount='99')], 'transient': True}).json()
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT COUNT(*) FROM proposals WHERE receipt IS NULL').fetchone()[0] == 2
    assert client.post(f"/api/ledger/proposals/{fresh['id']}/confirm").status_code == 200


# -- the form's backend: calendar and the ledger as of a date ------------------------------------

@pytest.mark.parametrize('moment, cn, us', [
    ('2026-11-17 10:32', '2026-11-17', '2026-11-16'),   # Tuesday morning: Monday is New York's last session
    ('2026-11-17 08:00', '2026-11-16', '2026-11-16'),   # before the first A-share price: Monday
    ('2026-11-21 10:00', '2026-11-20', '2026-11-20'),   # Saturday: Friday
])
def test_the_calendar_names_the_latest_verified_session_per_market(client, moment, cn, us):
    set_market_runtime(fake_runtime(FakeClock(moment)))
    try:
        body = client.get('/api/ledger/calendar').json()
    finally:
        set_market_runtime(None)
    assert body['today'] == moment[:10]
    assert body['markets']['CN']['session'] == cn
    assert body['markets']['US']['session'] == us


def test_without_a_verified_calendar_there_is_no_session(client):
    set_market_runtime(fake_runtime(FakeClock('2027-03-02 10:00')))   # the mainland table ends with 2026
    try:
        body = client.get('/api/ledger/calendar').json()
    finally:
        set_market_runtime(None)
    assert body['today'] == '2027-03-02'
    assert body['markets']['CN'] == {'session': None, 'verified': False}


def test_the_ledger_as_of_a_date_counts_later_events_out(ledger, client):
    ledger.confirm(ledger.propose([event('buy', ticker='SHA:600001', currency='CNY', quantity='100', price='10', fee='0')])['id'])
    ledger.confirm(ledger.propose([event('split', date='2026-11-18', ticker='SHA:600001', ratio='0.1')])['id'])
    ledger.confirm(ledger.propose([event('withdrawal', date='2026-11-18', currency='CNY', amount='8000')])['id'])
    before = client.get('/api/ledger/as-of?date=2026-11-17').json()
    account = before['accounts']['Example']
    assert before['as_of'] == '2026-11-17' and before['revision'] == ledger.state()['revision']
    assert account['positions']['SHA:600001']['quantity'] == '100'
    assert account['cash_balances']['CNY'] == '9000'
    assert client.get('/api/ledger/as-of?date=2026-11-18').json()['accounts']['Example']['positions']['SHA:600001']['quantity'] == '10'
    assert client.get('/api/ledger/as-of?date=2026-11-31').status_code == 422
    # The backdated sale the form now allows is what the ledger accepts.
    sale = client.post('/api/ledger/proposals', json={'events': [
        event('sell', date='2026-11-17', ticker='SHA:600001', currency='CNY', quantity='20', price='10', fee='0')]})
    assert sale.status_code == 200
    assert sale.json()['after']['accounts']['Example']['positions']['SHA:600001']['quantity'] == '8'


# -- legacy holdings ---------------------------------------------------------------------------------

def test_legacy_holdings_are_read_as_exact_decimals(directory, client):
    (directory / 'holdings').mkdir()
    (directory / 'holdings' / '2026-11-13.json').write_text(
        '{"groups": {"Example": {"cash": 100.10, "cash_balances": {"CNY": 100.10, "USD": 0.1}, "cost_basis": 1000.0000000000000001,'
        ' "positions": [{"ticker": "SHA:600001", "quantity": 1.1234567890123456789, "cost_price": 10.123456789012345678}]}}}')
    preview = client.post('/api/ledger/opening-preview', json={'date': DAY})
    assert preview.status_code == 200, preview.text
    opening_event = preview.json()['events'][0]
    assert opening_event['positions'][0]['quantity'] == '1.1234567890123456789'
    assert opening_event['positions'][0]['cost_price'] == '10.123456789012345678'
    assert opening_event['contributed_cny'] == '1000.0000000000000001'
    assert opening_event['cash_balances'] == {'CNY': '100.1', 'USD': '0.1'}


def test_a_malformed_legacy_file_is_refused_in_chinese_without_parser_text(directory, client):
    (directory / 'holdings').mkdir()
    (directory / 'holdings' / '2026-11-13.json').write_text('{"groups": {"Example": ')
    response = client.post('/api/ledger/opening-preview', json={'date': DAY})
    assert response.status_code == 422
    assert response.json()['detail'] == '旧持仓文件 2026-11-13.json 不是有效的 JSON，请修正后再预览。'
    (directory / 'holdings' / '2026-11-13.json').write_text('[1, 2]')
    assert client.post('/api/ledger/opening-preview', json={'date': DAY}).json()['detail'] == \
        '旧持仓文件 2026-11-13.json 的格式不对：需要 groups 对象。'


# -- release gate --------------------------------------------------------------------------------------

def book_now(ledger, *events):
    return ledger.confirm(ledger.propose(list(events))['id'])


def test_the_form_sees_what_booking_checks_after_a_reversal(directory, client):
    # The documented correction: reverse a mistaken sale, then book the corrected one on its own date.
    ledger = Ledger(directory / 'ledger.sqlite3')
    book_now(ledger, opening(date='2026-11-02'))
    book_now(ledger, event('buy', date='2026-11-03', ticker='SHA:600001', currency='CNY', quantity='100', price='10', fee='0'))
    sale = event('sell', date='2026-11-10', ticker='SHA:600001', currency='CNY', quantity='100', price='12', fee='0')
    book_now(ledger, sale)
    sold = [e for e in ledger.history() if e['kind'] == 'sell'][0]
    book_now(ledger, {'kind': 'reverse', 'date': '2026-11-17', 'target_id': sold['id']})
    # A mistaken buy reversed the same way: its cash is back for the corrected buy.
    book_now(ledger, event('buy', date='2026-11-10', ticker='SHA:600002', currency='CNY', quantity='800', price='10', fee='0'))
    bought = [e for e in ledger.history() if e.get('ticker') == 'SHA:600002'][0]
    book_now(ledger, {'kind': 'reverse', 'date': '2026-11-17', 'target_id': bought['id']})

    day = client.get('/api/ledger/as-of?date=2026-11-10').json()['accounts']['Example']
    assert day['positions']['SHA:600001']['quantity'] == '100', 'the reversed sale does not count on its date'
    assert day['cash_balances']['CNY'] == '9000', 'neither does the reversed buy'
    corrected = client.post('/api/ledger/proposals', json={'events': [
        {**sale, 'price': '12.5'},
        event('buy', date='2026-11-10', ticker='SHA:600002', currency='CNY', quantity='800', price='11', fee='0')]})
    assert corrected.status_code == 200, corrected.text
    # Without a later reversal the dated view is unchanged.
    assert client.get('/api/ledger/as-of?date=2026-11-03').json()['accounts']['Example']['cash_balances']['CNY'] == '9000'


@pytest.mark.parametrize('held, typed', [('HKG:700', 'HKG:00700'), ('HKG:00700', 'HKG:0700'), ('NYSE:BRK-B', 'NYSE:BRK.B')])
def test_one_listing_is_one_position_whatever_the_spelling(directory, client, held, typed):
    currency = 'HKD' if held.startswith('HKG') else 'USD'
    ledger = Ledger(directory / 'ledger.sqlite3')
    book_now(ledger, event('opening', cash_balances={currency: '100000'}, contributed_cny='0',
                           positions=[{'ticker': held, 'quantity': '100', 'cost_price': '300'}]))
    buy = client.post('/api/ledger/proposals', json={'events': [
        event('buy', ticker=typed, currency=currency, quantity='100', price='320', fee='0')]}).json()
    assert buy['events'][0]['ticker'] == held, 'booked under the spelling the ledger holds'
    assert list(buy['after']['accounts']['Example']['positions']) == [held]
    assert buy['after']['accounts']['Example']['positions'][held]['quantity'] == '200'
    assert client.post(f"/api/ledger/proposals/{buy['id']}/confirm").status_code == 200
    for kind, values in (('sell', dict(quantity='200', price='330', fee='0', currency=currency)),):
        sale = client.post('/api/ledger/proposals', json={'events': [event(kind, ticker=typed, **values)]})
        assert sale.status_code == 200, sale.text
    dividend = client.post('/api/ledger/proposals', json={'events': [event('dividend', ticker=typed, currency=currency, amount='5')]})
    assert dividend.json()['events'][0]['ticker'] == held
    split = client.post('/api/ledger/proposals', json={'events': [event('split', ticker=typed, ratio='2')]})
    assert split.json()['after']['accounts']['Example']['positions'][held]['quantity'] == '400'
    twice = client.post('/api/ledger/proposals', json={'events': [event('opening', account='Other', positions=[
        {'ticker': held, 'quantity': '1', 'cost_price': '1'}, {'ticker': typed, 'quantity': '1', 'cost_price': '1'}])]})
    assert twice.status_code == 422 and twice.json()['detail'] == 'Duplicate opening position'


def test_the_ledgers_listing_rule_is_the_market_search_rule():
    # core.ledger spells the rule out (the engine's bridge ships it alone); it must stay core.market.search's
    # for every ticker the ledger accepts, on the vectors the frontend's copy is held to as well.
    from pathlib import Path
    from core.ledger import _identity, instrument
    from core.market.search import identity_key
    vectors = json.loads((Path(__file__).parent / 'fixtures' / 'instrument_identity.json').read_text(encoding='utf-8'))['vectors']
    tickers = []
    for given, _expected in vectors:
        try:
            tickers.append(instrument(given))
        except LedgerError:
            continue
    assert len(tickers) > 10
    assert [_identity(ticker) for ticker in tickers] == [identity_key(ticker) for ticker in tickers]


def test_a_ledger_that_already_has_both_spellings_keeps_its_two_positions(directory, client):
    # As at 40868ac: events replay under the spelling they were booked with (a documented limitation).
    ledger = Ledger(directory / 'ledger.sqlite3')
    book_now(ledger, event('opening', cash_balances={'HKD': '100000'}, contributed_cny='0',
                           positions=[{'ticker': 'HKG:700', 'quantity': '100', 'cost_price': '300'}]))
    # As an earlier release stored it: a second spelling of the same listing.
    old = {'id': 'old-spelling', 'kind': 'buy', 'date': DAY, 'account': 'Example', 'source': 'manual',
           'ticker': 'HKG:00700', 'currency': 'HKD', 'quantity': '100', 'price': '400', 'fee': '0'}
    with sqlite3.connect(ledger.path) as db:
        db.execute('INSERT INTO events(id, payload, proposal_id) VALUES (?,?,?)', (old['id'], json.dumps(old), 'legacy'))
        db.execute('UPDATE ledger_meta SET revision = revision + 1')
    positions = client.get('/api/ledger').json()['accounts']['Example']['positions']
    assert {ticker: (p['quantity'], p['cost_price']) for ticker, p in positions.items()} == {
        'HKG:700': ('100', '300'), 'HKG:00700': ('100', '400')}
    from core.market.today import build_today
    from tests.fakes import ScriptedSource
    today = build_today(ledger, fake_runtime(FakeClock('2026-11-17 10:00'), ScriptedSource()))
    assert [(row['ticker'], row['quantity']) for row in today['accounts'][0]['positions']] == [
        ('HKG:700', '100'), ('HKG:00700', '100')]
    # A new sale is booked against the spelling typed when the account holds it.
    sale = client.post('/api/ledger/proposals', json={'events': [
        event('sell', date='2026-11-17', ticker='HKG:00700', currency='HKD', quantity='100', price='410', fee='0')]}).json()
    assert sale['events'][0]['ticker'] == 'HKG:00700'
    assert list(sale['after']['accounts']['Example']['positions']) == ['HKG:700']


def test_an_entry_takes_the_spelling_its_account_holds_and_other_accounts_keep_theirs(directory, client):
    ledger = Ledger(directory / 'ledger.sqlite3')
    book_now(ledger, event('opening', cash_balances={'HKD': '100000'}, contributed_cny='0',
                           positions=[{'ticker': 'HKG:700', 'quantity': '100', 'cost_price': '300'}]))
    book_now(ledger, event('sell', ticker='HKG:700', currency='HKD', quantity='100', price='310', fee='0'))
    # Bought back by an earlier release under another spelling: that is the position now.
    old = {'id': 'old-spelling', 'kind': 'buy', 'date': DAY, 'account': 'Example', 'source': 'manual',
           'ticker': 'HKG:00700', 'currency': 'HKD', 'quantity': '100', 'price': '320', 'fee': '0'}
    with sqlite3.connect(ledger.path) as db:
        db.execute('INSERT INTO events(id, payload, proposal_id) VALUES (?,?,?)', (old['id'], json.dumps(old), 'legacy'))
        db.execute('UPDATE ledger_meta SET revision = revision + 1')
    buy = client.post('/api/ledger/proposals', json={'events': [
        event('buy', ticker='HKG:0700', currency='HKD', quantity='100', price='330', fee='0')]}).json()
    assert buy['events'][0]['ticker'] == 'HKG:00700'
    assert buy['after']['accounts']['Example']['positions']['HKG:00700']['quantity'] == '200'
    other = client.post('/api/ledger/proposals', json={'events': [event('opening', account='Other', positions=[
        {'ticker': 'HKG:0700', 'quantity': '1', 'cost_price': '1'}])]}).json()
    assert other['events'][0]['positions'][0]['ticker'] == 'HKG:0700'


def test_a_b_share_proposal_made_before_the_upgrade_is_refused_at_confirmation(ledger, client):
    # Pending rows as v3.2.1 stored them: a B-share buy, and an opening holding one.
    staged = {
        'v321-buy': [{'id': 'b1', 'kind': 'buy', 'date': DAY, 'account': 'Example', 'source': 'manual', 'ticker': 'SHA:900901',
                      'currency': 'CNY', 'quantity': '100', 'price': '0.5', 'fee': '1'}],
        'v321-opening': [{'id': 'b2', 'kind': 'opening', 'date': DAY, 'account': 'Other', 'source': 'manual', 'cash_balances': {},
                          'contributed_cny': '0', 'fund_cny': '0',
                          'positions': [{'ticker': 'SHA:900901', 'name': 'SHA:900901', 'quantity': '100', 'cost_price': '0.6', 'currency': 'CNY'}]}],
    }
    revision = ledger.state()['revision']
    with sqlite3.connect(ledger.path) as db:
        for proposal_id, events in staged.items():
            payload = {'events': events, 'before': {}, 'after': {}, 'duplicates': 0, 'warnings': []}
            db.execute('INSERT INTO proposals VALUES (?,?,?,?,NULL)', (proposal_id, revision, json.dumps(payload), '2026-11-16T00:00:00+00:00'))
    for proposal_id in staged:
        refused = client.post(f'/api/ledger/proposals/{proposal_id}/confirm')
        assert refused.status_code == 422
        assert refused.json()['detail'] == 'SHA:900901 是 B 股，以 USD 交易；账本按人民币记录沪深标的，暂不支持记入 B 股。'
    assert len(ledger.history()) == 1 and ledger.state()['revision'] == revision
    assert ledger.state()['accounts']['Example']['cash_balances']['CNY'] == '10000'
    assert 'Other' not in ledger.state()['accounts']
    assert all(ledger.proposal(proposal_id)['receipt'] is None for proposal_id in staged)


def test_legacy_holdings_without_a_contribution_ask_for_it(directory, client):
    (directory / 'holdings').mkdir()
    (directory / 'holdings' / '2026-11-13.json').write_text(json.dumps({'groups': {
        'Example': {'cash': 10000}, 'Kept': {'cash': 5, 'cost_basis': 5}}}))
    asked = client.post('/api/ledger/opening-preview', json={'date': DAY})
    assert asked.status_code == 422
    assert asked.json()['detail'] == ('旧持仓文件没有记录部分账户的累计净投入（cost_basis）。请填写这些账户历年转入减转出的本金合计'
                                      '（人民币）后再预览；不会按 0 记账。')
    assert asked.json()['missing_contributions'] == ['Example']
    assert client.get('/api/ledger').json()['pending'] == [], 'nothing to confirm without it'
    filled = client.post('/api/ledger/opening-preview', json={'date': DAY, 'contributions': {'Example': '9000.5'}})
    assert filled.status_code == 200, filled.text
    contributed = {e['account']: e['contributed_cny'] for e in filled.json()['events']}
    assert contributed == {'Example': '9000.5', 'Kept': '5'}
    bad = client.post('/api/ledger/opening-preview', json={'date': DAY, 'contributions': {'Example': 'abc'}})
    assert bad.status_code == 422


# -- release gate, follow-ups ----------------------------------------------------------------------

def test_todays_trade_under_another_spelling_shows_what_the_ledger_holds(directory):
    # As at 40868ac: each spelling the ledger holds is its own row, with its own trades of the session.
    from datetime import timedelta
    from core.market.today import build_today
    from tests.fakes import ScriptedSource, build_ledger, fx_rate, opening as opened, position, quote
    ledger = build_ledger(directory, [opened('A', cash={'HKD': '100000'}, date='2026-11-13',
                                             positions=[position('HKG:700', '100', '300')])])
    # As an earlier release stored it, in today's session.
    old = {'id': 'old-spelling', 'kind': 'buy', 'date': '2026-11-17', 'account': 'A', 'source': 'manual',
           'ticker': 'HKG:00700', 'currency': 'HKD', 'quantity': '100', 'price': '395', 'fee': '0'}
    with sqlite3.connect(ledger.path) as db:
        db.execute('INSERT INTO events(id, payload, proposal_id) VALUES (?,?,?)', (old['id'], json.dumps(old), 'legacy'))
        db.execute('UPDATE ledger_meta SET revision = revision + 1')
    clock = FakeClock('2026-11-17 10:00')
    # A provider answers either spelling.
    source = ScriptedSource({spelling: quote(spelling, '400', prev_close='390', at=clock.now() - timedelta(seconds=3))
                             for spelling in ('HKG:700', 'HKG:00700')}, {'HKD': fx_rate('HKD', '0.9', at=clock.now())})
    account, = build_today(ledger, fake_runtime(clock, source))['accounts']
    # 100 x (400 - 390) held before the session; 100 x 400 - 100 x 395 bought in it.
    assert [(row['ticker'], row['quantity'], row['day_pnl']) for row in account['positions']] == [
        ('HKG:700', '100', '1000.00'), ('HKG:00700', '100', '500.00')]
    assert account['closed_today'] == []
    assert account['day_pnl_cny'] == '1350.00'


def test_the_assistant_cannot_propose_an_opening_without_its_contribution(directory):
    import asyncio
    from core.tools import propose_ledger_tool

    def propose(events):
        return asyncio.run(propose_ledger_tool().function(events=events))
    bare = {'kind': 'opening', 'date': DAY, 'account': 'Example', 'cash_balances': {'CNY': '10000'}, 'positions': []}
    text = ('期初余额需要每个账户的累计净投入（contributed_cny：历年转入减转出的本金合计，人民币）。'
            '请先向用户问清每个账户的这个金额，再生成期初余额预览；不能按 0 记账。')
    for events in ([bare], [{**bare, 'contributed_cny': None}], [{**bare, 'contributed_cny': ' '}],
                   [opening(), {**bare, 'account': 'Other'}]):
        assert propose(events) == {'status': 'not_proposed', 'error': text}, events
    assert not (directory / 'ledger.sqlite3').exists(), 'nothing was proposed'
    # A contribution the user gave, zero included, is theirs to give.
    assert propose([{**bare, 'contributed_cny': '0'}])['status'] == 'awaiting_user_confirmation'


# -- round 5: existing ledgers replay exactly as before the upgrade ---------------------------------

def old_event(n, kind, account='A', date='2026-11-10', **values):
    return {'id': f'e{n}', 'kind': kind, 'date': date, 'account': account, 'source': 'manual', **values}


def old_position(ticker, quantity, cost):
    return {'ticker': ticker, 'name': ticker, 'currency': 'HKD' if ticker.startswith('HKG') else 'USD',
            'quantity': quantity, 'cost_price': cost}


OLD_HKD = {'HKD': '100000'}
MIXED_SPELLINGS = {
    # One opening holding 100 shares under each Hong Kong spelling.
    'opening_both': [old_event(1, 'opening', date='2026-11-02', cash_balances=OLD_HKD, contributed_cny='90000', fund_cny='0',
                               positions=[old_position('HKG:700', '100', '300'), old_position('HKG:00700', '100', '350')])],
    # A second spelling bought, then the first sold.
    'history': [old_event(1, 'opening', date='2026-11-02', cash_balances=OLD_HKD, contributed_cny='90000', fund_cny='0',
                          positions=[old_position('HKG:700', '100', '300')]),
                old_event(2, 'buy', date='2026-11-03', ticker='HKG:00700', currency='HKD', quantity='100', price='350', fee='0'),
                old_event(3, 'sell', date='2026-11-04', ticker='HKG:700', currency='HKD', quantity='100', price='380', fee='0')],
    # A 2-for-1 split booked once per spelling.
    'split': [old_event(1, 'opening', date='2026-11-02', cash_balances=OLD_HKD, contributed_cny='90000', fund_cny='0',
                        positions=[old_position('HKG:700', '100', '300')]),
              old_event(2, 'buy', date='2026-11-03', ticker='HKG:00700', currency='HKD', quantity='100', price='300', fee='0'),
              old_event(3, 'split', date='2026-11-05', ticker='HKG:700', ratio='2'),
              old_event(4, 'split', date='2026-11-05', ticker='HKG:00700', ratio='2')],
    # US class shares, fees in the cost, partial sales.
    'us_class': [old_event(1, 'opening', date='2026-11-02', cash_balances={'USD': '10000'}, contributed_cny='70000',
                           fund_cny='0', positions=[old_position('NYSE:BRK-B', '10', '400')]),
                 old_event(2, 'buy', date='2026-11-03', ticker='NYSE:BRK.B', currency='USD', quantity='5', price='410', fee='1'),
                 old_event(3, 'sell', date='2026-11-04', ticker='NYSE:BRK-B', currency='USD', quantity='4', price='420', fee='1.5'),
                 old_event(4, 'sell', date='2026-11-05', ticker='NYSE:BRK.B', currency='USD', quantity='5', price='405', fee='0')],
    # A reversed buy under one spelling, then a buy and a sale under a third.
    'reversal': [old_event(1, 'opening', date='2026-11-02', cash_balances=OLD_HKD, contributed_cny='90000', fund_cny='0',
                           positions=[old_position('HKG:700', '100', '300')]),
                 old_event(2, 'buy', date='2026-11-03', ticker='HKG:00700', currency='HKD', quantity='100', price='350', fee='0'),
                 {'id': 'e3', 'kind': 'reverse', 'date': '2026-11-04', 'target_id': 'e2', 'source': 'manual'},
                 old_event(4, 'buy', date='2026-11-05', ticker='HKG:0700', currency='HKD', quantity='50', price='360', fee='2'),
                 old_event(5, 'sell', date='2026-11-06', ticker='HKG:0700', currency='HKD', quantity='20', price='370', fee='0')],
    # Two accounts, each with its own spelling.
    'two_accounts': [old_event(1, 'opening', date='2026-11-02', cash_balances=OLD_HKD, contributed_cny='90000', fund_cny='0',
                               positions=[old_position('HKG:700', '100', '300')]),
                     old_event(2, 'opening', account='B', date='2026-11-02', cash_balances=OLD_HKD, contributed_cny='90000',
                               fund_cny='0', positions=[old_position('HKG:00700', '200', '310')]),
                     old_event(3, 'sell', account='B', date='2026-11-04', ticker='HKG:00700', currency='HKD', quantity='50',
                               price='330', fee='0')],
}
# What 40868ac (before 4.0) replays these ledgers to: positions {ticker: [quantity, cost]}, cash, realized, contributed.
BEFORE_THE_UPGRADE = {
    'opening_both': {'A': {'positions': {'HKG:700': ['100', '300'], 'HKG:00700': ['100', '350']},
                           'cash': {'HKD': '100000'}, 'realized': {}, 'contributed': '90000'}},
    'history': {'A': {'positions': {'HKG:00700': ['100', '350']}, 'cash': {'HKD': '103000'}, 'realized': {'HKD': '8000'},
                      'contributed': '90000'}},
    'split': {'A': {'positions': {'HKG:700': ['200', '150'], 'HKG:00700': ['200', '150']}, 'cash': {'HKD': '70000'},
                    'realized': {}, 'contributed': '90000'}},
    'us_class': {'A': {'positions': {'NYSE:BRK-B': ['6', '400']}, 'cash': {'USD': '11652.5'}, 'realized': {'USD': '52.5'},
                       'contributed': '70000'}},
    'reversal': {'A': {'positions': {'HKG:700': ['100', '300'], 'HKG:0700': ['30', '360.04']}, 'cash': {'HKD': '89398'},
                       'realized': {'HKD': '199.2'}, 'contributed': '90000'}},
    'two_accounts': {'A': {'positions': {'HKG:700': ['100', '300']}, 'cash': {'HKD': '100000'}, 'realized': {},
                           'contributed': '90000'},
                     'B': {'positions': {'HKG:00700': ['150', '310']}, 'cash': {'HKD': '116500'}, 'realized': {'HKD': '1000'},
                           'contributed': '90000'}},
}


def old_ledger(directory, events):
    ledger = Ledger(directory / 'ledger.sqlite3')
    with sqlite3.connect(ledger.path) as db:
        for row in events:
            db.execute('INSERT INTO events(id, payload, proposal_id) VALUES (?,?,?)', (row['id'], json.dumps(row), 'legacy'))
        db.execute('UPDATE ledger_meta SET revision = revision + 1')
    return ledger


def figures(accounts):
    return {name: {'positions': {ticker: [p['quantity'], p['cost_price']] for ticker, p in account['positions'].items()},
                   'cash': account['cash_balances'], 'realized': account['realized'], 'contributed': account['contributed_cny']}
            for name, account in accounts.items()}


@pytest.mark.parametrize('case', sorted(MIXED_SPELLINGS))
def test_an_existing_ledger_with_mixed_spellings_replays_to_its_figures_from_before_the_upgrade(directory, client, case):
    from datetime import timedelta
    from core.market.today import build_today
    from tests.fakes import ScriptedSource, fx_rate, quote
    ledger = old_ledger(directory, MIXED_SPELLINGS[case])
    assert figures(ledger.state()['accounts']) == BEFORE_THE_UPGRADE[case]
    state = client.get('/api/ledger')
    assert state.status_code == 200 and figures(state.json()['accounts']) == BEFORE_THE_UPGRADE[case]
    assert client.get('/api/ledger/events').status_code == 200
    assert figures(client.get('/api/ledger/as-of?date=2026-11-30').json()['accounts']) == BEFORE_THE_UPGRADE[case]
    # Today shows what the ledger holds: one row per position, priced at 400.
    clock = FakeClock('2026-11-17 10:00')
    spellings = ('HKG:700', 'HKG:0700', 'HKG:00700', 'NYSE:BRK-B', 'NYSE:BRK.B')
    source = ScriptedSource({t: quote(t, '400', prev_close='400', at=clock.now() - timedelta(seconds=3)) for t in spellings},
                            {'HKD': fx_rate('HKD', '0.9', at=clock.now()), 'USD': fx_rate('USD', '7', at=clock.now())})
    today = build_today(ledger, fake_runtime(clock, source))
    shown = {account['name']: {row['ticker']: row['quantity'] for row in account['positions']} for account in today['accounts']}
    assert shown == {name: {ticker: quantity for ticker, (quantity, _cost) in account['positions'].items()}
                     for name, account in BEFORE_THE_UPGRADE[case].items()}
    for account in today['accounts'] if case != 'us_class' else []:   # Hong Kong rows, priced at HK$400
        held = BEFORE_THE_UPGRADE[case][account['name']]['positions']
        assert sum(Decimal(row['market_value']) for row in account['positions']) == sum(
            Decimal(quantity) * 400 for quantity, _cost in held.values())


def test_a_new_entry_takes_the_spelling_its_account_holds_and_nothing_else(directory, client):
    # The booking rule kept from round 4, for new events only: the held spelling; of two held, the one typed,
    # else the larger position; a listing not held keeps the spelling typed.
    old_ledger(directory, [old_event(1, 'opening', date='2026-11-02', cash_balances=OLD_HKD, contributed_cny='90000',
                                     fund_cny='0', positions=[old_position('HKG:700', '100', '300'),
                                                              old_position('HKG:00700', '200', '350')])])
    def booked_as(typed, kind='buy', account='A'):
        values = dict(currency='HKD', quantity='10', price='400', fee='0')
        proposal = client.post('/api/ledger/proposals', json={'events': [
            {'kind': kind, 'date': DAY, 'account': account, 'ticker': typed, **values}]})
        assert proposal.status_code == 200, proposal.text
        return proposal.json()['events'][0]['ticker']
    assert booked_as('HKG:700') == 'HKG:700'
    assert booked_as('HKG:00700') == 'HKG:00700'
    assert booked_as('HKG:0700') == 'HKG:00700', 'the larger of the two'
    assert booked_as('HKG:0700', 'sell') == 'HKG:00700'
    assert booked_as('HKG:5') == 'HKG:5', 'not held: as typed'


def test_a_fill_booked_under_the_held_spelling_is_recognised_when_it_comes_again(directory, client):
    ledger = Ledger(directory / 'ledger.sqlite3')
    book_now(ledger, event('opening', cash_balances={'HKD': '100000'}, contributed_cny='0',
                           positions=[{'ticker': 'HKG:700', 'quantity': '100', 'cost_price': '300'}]))
    fill = event('buy', ticker='HKG:00700', currency='HKD', quantity='100', price='320', fee='0', source='broker-csv',
                 external_id='fill-1')
    first = book_now(ledger, fill)
    booked = [e for e in ledger.history() if e.get('external_id') == 'fill-1'][0]
    assert booked['ticker'] == 'HKG:700' and booked['source'] == 'broker-csv'
    # Same id, same external id: what an importer recognises a booked row by.
    assert booked['id'] == normalize(fill)['id']
    again = client.post('/api/ledger/proposals', json={'events': [fill]}).json()
    assert (again['duplicates'], again['events']) == (1, [])
    # Still recognised after the position is sold out (the spelling it was booked under is no longer held).
    book_now(ledger, event('sell', ticker='HKG:700', currency='HKD', quantity='200', price='330', fee='0'))
    later = client.post('/api/ledger/proposals', json={'events': [fill]})
    assert later.status_code == 200 and (later.json()['duplicates'], later.json()['events']) == (1, [])
    assert first['applied'] == 1


# -- round 6: a dated entry takes the spelling held on its own date --------------------------------

def sold_out_then_reopened(directory, sale_price):
    """HKG:700 held from 2 Nov and sold out on 10 Nov; Tencent bought again as HKG:00700 on 12 Nov."""
    ledger = Ledger(directory / 'ledger.sqlite3')
    book_now(ledger, event('opening', date='2026-11-02', account='A', cash_balances={'HKD': '100000'}, contributed_cny='90000',
                           positions=[{'ticker': 'HKG:700', 'quantity': '100', 'cost_price': '300'}]))
    book_now(ledger, event('sell', date='2026-11-10', account='A', ticker='HKG:700', currency='HKD', quantity='100',
                           price=sale_price, fee='0'))
    book_now(ledger, event('buy', date='2026-11-12', account='A', ticker='HKG:00700', currency='HKD', quantity='100',
                           price='360', fee='0'))
    return ledger


# What 40868ac books (the spelling typed, HKG:700, on a date it was held).
DATED_BEFORE_THE_UPGRADE = {
    'missed_purchase': {'A': {'positions': {'HKG:700': ['100', '310'], 'HKG:00700': ['100', '360']}, 'cash': {'HKD': '70000'},
                              'realized': {'HKD': '7000'}, 'contributed': '90000'}},
    'split': {'A': {'positions': {'HKG:700': ['100', '150'], 'HKG:00700': ['100', '360']}, 'cash': {'HKD': '102000'},
                    'realized': {'HKD': '23000'}, 'contributed': '90000'}},
    'backdated_buy': {'A': {'positions': {'HKG:700': ['100', '320'], 'HKG:00700': ['100', '360']}, 'cash': {'HKD': '65000'},
                            'realized': {'HKD': '3000'}, 'contributed': '90000'}},
}


@pytest.mark.parametrize('case, sale_price, entry', [
    ('missed_purchase', '380', dict(kind='buy', date='2026-11-05', currency='HKD', quantity='100', price='320', fee='0')),
    ('split', '380', dict(kind='split', date='2026-11-06', ratio='2')),
    ('backdated_buy', '350', dict(kind='buy', date='2026-11-05', currency='HKD', quantity='100', price='340', fee='0')),
])
def test_an_entry_dated_before_a_reopening_takes_the_spelling_held_on_its_date(directory, client, case, sale_price, entry):
    ledger = sold_out_then_reopened(directory, sale_price)
    preview = client.post('/api/ledger/proposals', json={'events': [{'account': 'A', 'ticker': 'HKG:700', **entry}]})
    assert preview.status_code == 200, preview.text
    assert preview.json()['events'][0]['ticker'] == 'HKG:700'
    assert figures(preview.json()['after']['accounts']) == DATED_BEFORE_THE_UPGRADE[case]
    assert client.post(f"/api/ledger/proposals/{preview.json()['id']}/confirm").status_code == 200
    assert figures(ledger.state()['accounts']) == DATED_BEFORE_THE_UPGRADE[case]


def test_a_dated_entry_takes_the_spelling_held_on_that_date_and_none_when_none_was_held(directory, client):
    sold_out_then_reopened(directory, '380')

    def booked_as(day):
        proposal = client.post('/api/ledger/proposals', json={'events': [event(
            'buy', date=day, account='A', ticker='HKG:700', currency='HKD', quantity='10', price='370', fee='0')]})
        assert proposal.status_code == 200, proposal.text
        return proposal.json()['events'][0]['ticker']
    assert booked_as('2026-11-05') == 'HKG:700', 'held as HKG:700 then'
    assert booked_as('2026-11-11') == 'HKG:700', 'none held then: as typed'
    assert booked_as('2026-11-15') == 'HKG:00700', 'held as HKG:00700 then'


# -- round 7: nothing held on the date, one spelling in the account's history ----------------------

def fresh_account(directory):
    ledger = Ledger(directory / 'ledger.sqlite3')
    book_now(ledger, event('opening', date='2026-11-02', account='A', cash_balances={'HKD': '100000'},
                           contributed_cny='90000', positions=[]))
    return ledger


def hk_trade(kind, day, ticker, quantity, price):
    return event(kind, date=day, account='A', ticker=ticker, currency='HKD', quantity=quantity, price=price, fee='0')


def test_an_earlier_purchase_joins_the_only_spelling_the_account_has_used(directory, client):
    # 5db322a: one position of 200 at HK$325; selling all 200 at HK$380 realizes HK$11,000.
    ledger = fresh_account(directory)
    book_now(ledger, hk_trade('buy', '2026-11-10', 'HKG:00700', '100', '350'))
    earlier = client.post('/api/ledger/proposals', json={'events': [hk_trade('buy', '2026-11-05', 'HKG:700', '100', '300')]}).json()
    assert earlier['events'][0]['ticker'] == 'HKG:00700'
    assert figures(earlier['after']['accounts'])['A']['positions'] == {'HKG:00700': ['200', '325']}
    assert client.post(f"/api/ledger/proposals/{earlier['id']}/confirm").status_code == 200
    sale = client.post('/api/ledger/proposals', json={'events': [hk_trade('sell', '2026-11-12', 'HKG:00700', '200', '380')]})
    assert sale.status_code == 200, sale.text
    assert client.post(f"/api/ledger/proposals/{sale.json()['id']}/confirm").status_code == 200
    assert figures(ledger.state()['accounts'])['A'] == {'positions': {}, 'cash': {'HKD': '111000'},
                                                         'realized': {'HKD': '11000'}, 'contributed': '90000'}


def test_a_missed_purchase_before_a_buy_and_a_sale_joins_their_spelling(directory, client):
    # 5db322a: one position of 150 at HK$330 average cost, HK$3,500 realized.
    ledger = fresh_account(directory)
    book_now(ledger, hk_trade('buy', '2026-11-10', 'HKG:00700', '100', '360'))
    book_now(ledger, hk_trade('sell', '2026-11-12', 'HKG:00700', '50', '400'))
    missed = client.post('/api/ledger/proposals', json={'events': [hk_trade('buy', '2026-11-05', 'HKG:700', '100', '300')]}).json()
    assert missed['events'][0]['ticker'] == 'HKG:00700'
    assert client.post(f"/api/ledger/proposals/{missed['id']}/confirm").status_code == 200
    assert figures(ledger.state()['accounts'])['A'] == {'positions': {'HKG:00700': ['150', '330']}, 'cash': {'HKD': '54000'},
                                                         'realized': {'HKD': '3500'}, 'contributed': '90000'}


def test_only_the_accounts_own_history_names_the_spelling_when_nothing_was_held(directory, client):
    ledger = fresh_account(directory)
    book_now(ledger, event('opening', date='2026-11-02', account='B', cash_balances={'HKD': '100000'}, contributed_cny='0',
                           positions=[{'ticker': 'HKG:00700', 'quantity': '100', 'cost_price': '300'}]))
    # Another account's spelling does not count: A has never traded the listing, so the spelling typed stays.
    first = client.post('/api/ledger/proposals', json={'events': [hk_trade('buy', '2026-11-05', 'HKG:700', '10', '300')]}).json()
    assert first['events'][0]['ticker'] == 'HKG:700'
    # Two spellings in A's history and none held on the date: the spelling typed (round 6, kept).
    book_now(ledger, hk_trade('buy', '2026-11-10', 'HKG:700', '10', '300'))
    book_now(ledger, hk_trade('sell', '2026-11-11', 'HKG:700', '10', '310'))
    book_now(ledger, hk_trade('buy', '2026-11-13', 'HKG:00700', '10', '320'))
    book_now(ledger, hk_trade('sell', '2026-11-14', 'HKG:00700', '10', '330'))
    assert client.post('/api/ledger/proposals', json={'events': [
        hk_trade('buy', '2026-11-12', 'HKG:0700', '10', '300')]}).json()['events'][0]['ticker'] == 'HKG:0700'


def test_a_listing_bought_again_after_a_sell_out_keeps_the_spelling_typed(directory, client):
    # One spelling in the account's history, but the entry comes after all of it: as 40868ac and 5db322a book it.
    ledger = fresh_account(directory)
    book_now(ledger, hk_trade('buy', '2026-11-05', 'HKG:700', '100', '300'))
    book_now(ledger, hk_trade('sell', '2026-11-10', 'HKG:700', '100', '380'))
    again = client.post('/api/ledger/proposals', json={'events': [hk_trade('buy', '2026-11-12', 'HKG:00700', '100', '360')]})
    assert again.json()['events'][0]['ticker'] == 'HKG:00700'
    # Dated in a gap of that history (sold out on 10 Nov, bought again on 12 Nov): the account's one spelling.
    book_now(ledger, hk_trade('buy', '2026-11-12', 'HKG:700', '100', '360'))
    gap = client.post('/api/ledger/proposals', json={'events': [hk_trade('buy', '2026-11-11', 'HKG:00700', '10', '370')]})
    assert gap.json()['events'][0]['ticker'] == 'HKG:700'
