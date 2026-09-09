"""Accounting invariants, import identity and transaction safety, using fictional balances."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.ledger import Ledger, LedgerError, Conflict, csv_events, opening_events, amount
from backend.api import ledger as ledger_api

DAY = '2026-08-03'


def event(kind, **values):
    return {'kind': kind, 'date': DAY, 'account': 'Example', **values}


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / 'ledger.sqlite3')


def book(ledger, events):
    return ledger.confirm(ledger.propose(events)['id'])


def opening(ledger):
    return book(ledger, [event('opening', cash_balances={'CNY': '10000', 'USD': '1000'}, contributed_cny='17000')])


def test_preview_is_not_a_financial_write_and_confirmation_is_idempotent(ledger):
    p = ledger.propose([event('opening', cash_balances={'CNY': '100'})])
    assert ledger.state()['accounts'] == {}
    assert ledger.history() == []
    receipt = ledger.confirm(p['id'])
    assert ledger.confirm(p['id']) == receipt
    assert len(ledger.history()) == 1
    assert ledger.state()['revision'] == 1


def test_fractional_shares_fees_weighted_cost_and_sale(ledger):
    opening(ledger)
    book(ledger, [event('buy', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='1.5', price='100', fee='1.5'),
                  event('buy', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='.5', price='110', fee='.5'),
                  event('sell', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='.5', price='120', fee='1')])
    a = ledger.state()['accounts']['Example']
    assert amount(a['cash_balances']['USD']) == amount('852')
    assert amount(a['positions']['NASDAQ:EXAMPLE']['quantity']) == amount('1.5')
    assert amount(a['positions']['NASDAQ:EXAMPLE']['cost_price']) == amount('103.5')
    assert amount(a['realized']['USD']) == amount('7.25')
    assert a['contributed_cny'] == '17000'


def test_fx_transfer_dividend_and_external_flows(ledger):
    opening(ledger)
    book(ledger, [event('opening', account='Other'),
                  event('deposit', currency='USD', amount='10', fx_rate='7.2'),
                  event('dividend', currency='USD', amount='5'),
                  event('transfer', currency='USD', amount='100', to_account='Other', fx_rate='7.2'),
                  event('fx', account='Other', currency='USD', amount='10', to_currency='HKD', received_amount='77'),
                  event('withdrawal', account='Other', currency='HKD', amount='7', fx_rate='.9')])
    a = ledger.state()['accounts']
    assert amount(a['Example']['contributed_cny']) == amount('16352')
    assert amount(a['Other']['contributed_cny']) == amount('713.7')
    assert amount(a['Other']['cash_balances']['HKD']) == 70
    assert sum(amount(x['contributed_cny']) for x in a.values()) == amount('17065.7')


def test_split_preserves_basis_and_reversal_preserves_history(ledger):
    opening(ledger)
    bought = book(ledger, [event('buy', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='2', price='100')])
    book(ledger, [event('split', ticker='NASDAQ:EXAMPLE', ratio='2')])
    p = ledger.state()['accounts']['Example']['positions']['NASDAQ:EXAMPLE']
    assert amount(p['quantity']) == 4 and amount(p['cost_price']) == 50
    split_id = ledger.history()[-1]['id']
    book(ledger, [{'kind': 'reverse', 'date': DAY, 'target_id': split_id}])
    assert amount(ledger.state()['accounts']['Example']['positions']['NASDAQ:EXAMPLE']['quantity']) == 2
    assert len(ledger.history()) == 4
    assert bought['applied'] == 1
    with pytest.raises(LedgerError):
        ledger.propose([{'kind': 'reverse', 'date': DAY, 'target_id': split_id}])


def test_invalid_reversal_cannot_orphan_later_sale(ledger):
    opening(ledger)
    book(ledger, [event('buy', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='2', price='100')])
    buy_id = ledger.history()[-1]['id']
    book(ledger, [event('sell', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='1', price='110')])
    before = ledger.state()
    with pytest.raises(LedgerError, match='sell'):
        ledger.propose([{'kind': 'reverse', 'date': DAY, 'target_id': buy_id}])
    assert ledger.state() == before


def test_backdated_event_replays_and_historical_state_excludes_later_reversal(ledger):
    opening(ledger)
    book(ledger, [event('deposit', date='2026-08-05', currency='CNY', amount='20')])
    target = ledger.history()[-1]['id']
    book(ledger, [event('deposit', date='2026-08-04', currency='CNY', amount='10')])
    book(ledger, [{'kind': 'reverse', 'date': '2026-08-06', 'target_id': target}])
    assert amount(ledger.state('2026-08-05')['accounts']['Example']['cash_balances']['CNY']) == 10030
    assert amount(ledger.state()['accounts']['Example']['cash_balances']['CNY']) == 10010
    with pytest.raises(LedgerError, match='follow'):
        ledger.propose([{'kind': 'reverse', 'date': '2026-08-01', 'target_id': ledger.history()[0]['id']}])


def test_stale_preview_rejected(ledger):
    opening(ledger)
    p = ledger.propose([event('deposit', currency='CNY', amount='10')])
    book(ledger, [event('deposit', currency='CNY', amount='20')])
    with pytest.raises(Conflict):
        ledger.confirm(p['id'])
    assert len(ledger.history()) == 2


def test_concurrent_confirmations_have_one_receipt(ledger):
    p = ledger.propose([event('opening')])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ledger.confirm(p['id']), range(8)))
    assert all(r == results[0] for r in results)
    assert len(ledger.history()) == 1


def test_duplicate_import_and_changed_external_identity(ledger):
    opening(ledger)
    row = event('deposit', currency='CNY', amount='10', source='broker-a', external_id='ref-001')
    book(ledger, [row, row])
    p = ledger.propose([row])
    assert p['duplicates'] == 1 and not p['events']
    revision = ledger.state()['revision']
    book(ledger, [row])
    assert ledger.state()['revision'] == revision
    with pytest.raises(Conflict):
        ledger.propose([{**row, 'amount': '11'}])


@pytest.mark.parametrize('patch', [
    {'amount': 'NaN'}, {'amount': 'Infinity'}, {'amount': True}, {'amount': '-1'},
    {'currency': 'EUR'}, {'currency': []}, {'date': '../secrets'}, {'date': '2026-02-30'},
    {'allow_margin': 'false'}, {'id': 'spoof'}, {'_sequence': -1},
])
def test_invalid_inputs_do_not_change_balance(ledger, patch):
    opening(ledger)
    with pytest.raises(LedgerError):
        ledger.propose([event('deposit', currency='CNY', amount='10') | patch])
    assert len(ledger.history()) == 1


def test_missing_fx_and_oversell_fail_whole_batch(ledger):
    opening(ledger)
    for row in [event('deposit', currency='USD', amount='10'),
                event('sell', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='1', price='10'),
                event('buy', ticker='NASDAQ:EXAMPLE', currency='CNY', quantity='1', price='10'),
                event('buy', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='100', price='100')]:
        with pytest.raises(LedgerError):
            ledger.propose([event('deposit', currency='CNY', amount='1'), row])
    assert len(ledger.history()) == 1


def test_explicit_margin_shows_warning(ledger):
    opening(ledger)
    p = ledger.propose([event('buy', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='20', price='100', allow_margin=True)])
    assert p['warnings'] and '-1000' in p['warnings'][0]


def test_csv_template_bom_and_strict_columns():
    rows = csv_events('\ufeffexternal_id,date,kind,account,currency,amount\n001,2026-08-03,deposit,Example,CNY,10\n', 'broker')
    assert rows[0]['external_id'] == '001'
    for content in ['date,kind,account\n', 'external_id,date,kind,account\n,2026-08-03,buy,Example\n',
                    'external_id,date,kind,account\nx,2026-08-03,buy,Example,extra\n']:
        with pytest.raises(LedgerError):
            csv_events(content, 'broker')


def test_opening_migration_is_explicit_and_preserves_legacy(tmp_path, ledger):
    directory = tmp_path / 'holdings'; directory.mkdir()
    path = directory / f'{DAY}.json'
    original = json.dumps({'groups': {'Example': {'cash': 1, 'cash_balances': {'CNY': 1, 'USD': 2}, 'cost_basis': 15,
                                               'positions': [{'ticker': 'NASDAQ:EXAMPLE', 'quantity': .5, 'cost_price': 2}]}}})
    path.write_text(original)
    rows = opening_events(tmp_path, '2026-08-04')
    ledger.propose(rows)
    assert ledger.state()['accounts'] == {}
    assert path.read_text() == original
    assert rows[0]['cash_balances']['USD'] == 2


def test_backup_restores_all_confirmed_and_pending_data(tmp_path, ledger):
    opening(ledger)
    pending = ledger.propose([event('fee', currency='USD', amount='1')])
    restored_path = tmp_path / 'restored.sqlite3'
    restored_path.write_bytes(ledger.backup())
    restored = Ledger(restored_path)
    assert restored.state() == ledger.state()
    assert restored.proposals()[0]['id'] == pending['id']
    with sqlite3.connect(restored_path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    app = FastAPI(); app.include_router(ledger_api.router, prefix='/api/ledger')
    return TestClient(app)


def test_http_preview_confirm_conflict_export_and_research(client):
    p = client.post('/api/ledger/proposals', json={'events': [event('opening')]}).json()
    assert client.get('/api/ledger').json()['revision'] == 0
    assert client.post(f"/api/ledger/proposals/{p['id']}/confirm").status_code == 200
    assert client.post('/api/ledger/proposals', json={'events': [event('deposit', currency='EUR', amount=1)]}).status_code == 422
    assert client.get('/api/ledger/template.csv').text.startswith('external_id,')
    assert client.get('/api/ledger/export.json').json()['format'] == 'portfolio-ledger-v1'
    assert client.get('/api/ledger/backup.sqlite3').content.startswith(b'SQLite format 3')
    journal = {'ticker': 'NASDAQ:EXAMPLE', 'thesis': 'Fictional thesis', 'invalidation': 'Fictional condition', 'review_on': DAY}
    assert client.post('/api/ledger/journal', json=journal).status_code == 200
    assert client.get('/api/ledger/journal').json()['entries'][0]['thesis'] == journal['thesis']


def test_snapshot_bridge_prefers_confirmed_ledger(tmp_path, monkeypatch, ledger):
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    scripts = root / 'engine/scripts' if (root / 'engine').exists() else root / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    from ledger_bridge import ledger_holdings, guard_legacy_write
    assert ledger_holdings(tmp_path, DAY) is None
    opening(ledger)
    assert ledger_holdings(tmp_path, DAY)['groups']['Example']['cash_balances']['USD'] == 1000
    with pytest.raises(ValueError, match='Ledger is active'):
        guard_legacy_write(tmp_path)
    assert ledger_holdings(tmp_path, '2026-08-01') is None


def test_image_recognition_never_mutates_financial_balances():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from backend.services.agent_service import AgentService
    service = AgentService()
    service._build_context = AsyncMock(return_value=None)
    service.llm_provider = SimpleNamespace(chat=AsyncMock(return_value=SimpleNamespace(content='Fictional recognition')))
    service.llm_extractor = SimpleNamespace(extract=AsyncMock(return_value={
        'positions': [{'symbol': 'EXAMPLE', 'name': 'Example', 'quantity': 1, 'cost_price': 10}], 'cash': 100}))
    service.agent = SimpleNamespace(conversation=SimpleNamespace(add_message=Mock()))
    service.portfolio_provider = SimpleNamespace(set_cash=Mock(), add_position=Mock())
    result = asyncio.run(service.chat_with_images('Read this fictional position', []))
    service.portfolio_provider.set_cash.assert_not_called()
    service.portfolio_provider.add_position.assert_not_called()
    assert result['imported_positions'] == 0 and result['cash_updated'] is False
    assert len(result['proposed_positions']) == 1


def test_dashboard_agent_registers_proposal_only():
    from backend.services.agent_service import AgentService
    service = AgentService()
    service._init_agent()
    names = set(service.agent.tool_executor.tools)
    assert 'propose_ledger_events' in names
    assert not {'update_holdings', 'run_portfolio_pipeline'} & names


def test_fund_flows_and_valuation_do_not_change_contributions(ledger):
    opening(ledger)
    book(ledger, [event('fund_buy', amount='1000', fee='2'), event('fund_value', amount='1100'),
                  event('fund_sell', amount='500', fee='1')])
    account = ledger.state()['accounts']['Example']
    assert amount(account['cash_balances']['CNY']) == 9497
    assert account['fund_cny'] == '600'
    assert account['contributed_cny'] == '17000'
    with pytest.raises(LedgerError):
        ledger.propose([event('fund_sell', amount='601')])
    with pytest.raises(LedgerError):
        ledger.propose([event('fund_value', amount='-1')])


def test_pathological_precision_and_normalized_duplicate(ledger):
    with pytest.raises(LedgerError):
        amount('1e-99999999')
    opening(ledger)
    row = event('deposit', currency='CNY', amount='1', external_id='test')
    book(ledger, [row])
    assert ledger.propose([{**row, 'amount': '1.00'}])['duplicates'] == 1
