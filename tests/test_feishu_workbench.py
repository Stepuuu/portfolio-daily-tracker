import copy
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from feishu_workbench.app import create_app
from feishu_workbench.clients import ResearchClient
from feishu_workbench.portfolio import PortfolioAdapter, PortfolioError
from feishu_workbench.service import Workbench, normalize
from feishu_workbench.store import Rejected, Store


@pytest.fixture
def portfolio(tmp_path):
    project = tmp_path / 'project'
    holdings = project / 'portfolio' / 'holdings'
    holdings.mkdir(parents=True)
    (project / 'scripts').mkdir()
    data = {'date': '2026-01-02', 'groups': {'Example': {
        'cash': 1000, 'cash_balances': {'CNY': 1000, 'USD': 200}, 'fund': 50,
        'positions': [{'ticker': 'NASDAQ:EXAMPLE', 'name': 'Example', 'quantity': 1.5, 'cost_price': 10}],
    }}}
    (holdings / '2026-01-02.json').write_text(json.dumps(data))
    return PortfolioAdapter(project, tmp_path / 'state')


def values(**overrides):
    return {'operation': 'buy', 'account': 'Example', 'date': '2026-01-02',
            'ticker': 'NASDAQ:EXAMPLE', 'quantity': '0.125', 'price': '12', 'fee': '0.01',
            'currency': 'USD', **overrides}


def state(adapter):
    return json.loads((adapter.holdings_dir / '2026-01-02.json').read_text())


def test_preview_does_not_write_holdings_and_confirmation_is_idempotent(portfolio):
    before = state(portfolio)
    preview = portfolio.preview(values())
    assert state(portfolio) == before
    receipt = portfolio.confirm(preview, 'operation_one')
    assert state(portfolio)['groups']['Example']['positions'][0]['quantity'] == 1.625
    assert state(portfolio)['groups']['Example']['cash_balances']['USD'] == 198.49
    assert portfolio.confirm(preview, 'operation_one') == receipt
    assert state(portfolio)['groups']['Example']['positions'][0]['quantity'] == 1.625


def test_decimal_balance_is_in_units_not_ten_thousands(portfolio):
    preview = portfolio.preview({'operation': 'set_cash', 'date': '2026-01-02', 'account': 'Example',
                                 'currency': 'CNY', 'amount': '1.25'})
    portfolio.confirm(preview, 'cash_one')
    assert state(portfolio)['groups']['Example']['cash'] == 1.25


@pytest.mark.parametrize('overrides', [{'quantity': 'NaN'}, {'quantity': '0'}, {'quantity': '-1'},
    {'quantity': 'Infinity'}, {'fee': '-1'}, {'price': 'NaN'}, {'currency': 'CNY'},
    {'account': 'unknown'}, {'ticker': '../secret'}, {'operation': 'sell', 'quantity': '2'},
    {'operation': 'buy', 'quantity': '1000'}])
def test_invalid_or_ambiguous_trades_never_modify_assets(portfolio, overrides):
    before = state(portfolio)
    with pytest.raises(PortfolioError):
        portfolio.preview(values(**overrides))
    assert state(portfolio) == before


def test_stale_preview_and_reused_operation_id_are_rejected(portfolio):
    first = portfolio.preview(values())
    second = portfolio.preview(values(quantity='0.25'))
    portfolio.confirm(first, 'same_key')
    with pytest.raises(PortfolioError):
        portfolio.confirm(second, 'same_key')
    with pytest.raises(PortfolioError):
        portfolio.confirm(second, 'other_key')


def test_recovery_after_holdings_write_does_not_apply_trade_twice(portfolio, monkeypatch):
    preview = portfolio.preview(values())
    real_write = portfolio._write_holdings
    def crash_after_write(*args):
        real_write(*args)
        raise RuntimeError('simulated crash before receipt commit')
    monkeypatch.setattr(portfolio, '_write_holdings', crash_after_write)
    with pytest.raises(RuntimeError):
        portfolio.confirm(preview, 'crash_key')
    monkeypatch.setattr(portfolio, '_write_holdings', real_write)
    portfolio.confirm(preview, 'crash_key')
    assert state(portfolio)['groups']['Example']['positions'][0]['quantity'] == 1.625


def test_report_retry_requires_confirmed_unchanged_holdings(portfolio, monkeypatch):
    called = []
    monkeypatch.setattr(portfolio, '_execute_pipeline', lambda day, resume: called.append((day, resume)) or True)
    with pytest.raises(PortfolioError):
        portfolio.run_report({'operation_id': 'missing'})
    receipt = portfolio.confirm(portfolio.preview(values()), 'report_one')
    assert portfolio.run_report(receipt)['report_status'] == 'complete'
    assert portfolio.run_report(receipt)['report_status'] == 'complete'
    assert len(called) == 1


class FakeDelivery:
    def __init__(self):
        self.cards = {}
    def send(self, actor, chat, card, operation_id):
        message = 'om_' + operation_id.replace('_', '')
        self.cards[message] = card
        return {'message_id': message, 'chat_id': chat or 'private_chat'}
    def patch(self, message, card):
        self.cards[message] = card


class FakeResearch:
    def get(self, path):
        values = {
            '/api/lab/datasets': [{'id': 'ds_' + 'a' * 24, 'name': 'Synthetic'}],
            '/api/lab/templates': [{'id': 'momentum', 'name': 'Momentum'}],
            '/api/lab/connections': [{'id': 'configured', 'name': 'Configured model'}],
            '/api/lab/runs': [],
        }
        return values[path]


@pytest.fixture
def workbench(tmp_path, portfolio):
    config = {'allowed_users': ['owner'], 'account_id': 'qr', '_token': 'x' * 48,
              'state_dir': str(tmp_path / 'state')}
    controller = Workbench(config, Store(tmp_path / 'state/cards.sqlite3'), portfolio, FakeResearch(), FakeDelivery())
    return config, controller


def session_card(controller, view='daily_select'):
    identifier = controller.store.open('owner', 'private_chat')
    command = controller.store.claim()
    controller.render(command, view, controller.daily_form() if view in {'daily_form', 'daily_select'} else {})
    controller.store.finish(command)
    return controller.store.session(identifier)


def callback(session, action='daily.preview', **fields):
    return {'account_id': 'qr', 'event_type': 'card.action.trigger', 'event': {
        'event_id': 'event_example', 'operator': {'open_id': 'owner'},
        'context': {'open_message_id': session['message'], 'open_chat_id': session['chat']},
        'action': {'name': 'qrw_' + session['nonces'][action], 'form_value': fields},
    }}


def test_callback_ack_only_queues_and_duplicate_clicks_do_not_repeat(workbench):
    _, controller = workbench
    session = session_card(controller)
    event = callback(session, operation='no_change', date='2026-01-02', account='Example', currency='CNY', fee='0')
    before = state(controller.portfolio)
    assert controller.accept(event)['toast']['type'] == 'info'
    assert controller.accept(event)['toast']['type'] == 'info'
    assert controller.store.counts()['queued'] == 1
    assert state(controller.portfolio) == before
    command = controller.store.claim()
    controller.execute(command)
    controller.store.finish(command)
    assert controller.store.session(session['id'])['view'] == 'daily_preview'
    assert state(controller.portfolio) == before


@pytest.mark.parametrize('operation,fields', [
    ('no_change', {}),
    ('buy', {'symbol': 'NASDAQ:EXAMPLE', 'quantity': '0.125', 'price': '12', 'currency': 'USD', 'fee': '0'}),
    ('set_cash', {'amount': '1.25', 'currency': 'CNY'}),
    ('set_fund', {'amount': '50.25'}),
])
def test_two_step_form_preserves_selection_and_previews_without_writes(workbench, operation, fields):
    _, controller = workbench
    session = session_card(controller)
    before = state(controller.portfolio)
    controller.accept(callback(session, action='daily.choose', date='2026-01-02', account='Example', operation=operation))
    command = controller.store.claim()
    controller.execute(command)
    controller.store.finish(command)
    current = controller.store.session(session['id'])
    if operation != 'no_change':
        assert current['view'] == 'daily_form'
        assert current['model']['_selection']['account'] == 'Example'
        controller.accept(callback(current, **fields))
        command = controller.store.claim()
        controller.execute(command)
        controller.store.finish(command)
    current = controller.store.session(session['id'])
    assert current['view'] == 'daily_preview'
    assert current['model']['_preview']['date'] == '2026-01-02'
    assert state(controller.portfolio) == before


@pytest.mark.parametrize('change', ['actor', 'chat', 'message', 'nonce', 'account'])
def test_card_forwarding_and_forged_context_are_rejected(workbench, change):
    _, controller = workbench
    event = callback(session_card(controller))
    if change == 'actor': event['event']['operator']['open_id'] = 'stranger'
    if change == 'chat': event['event']['context']['open_chat_id'] = 'group_chat'
    if change == 'message': event['event']['context']['open_message_id'] = 'om_forwarded'
    if change == 'nonce': event['event']['action']['name'] = 'qrw_forged'
    if change == 'account': event['account_id'] = 'other_bot'
    with pytest.raises(Rejected):
        controller.accept(event)


def test_agent_preview_discloses_remote_model_calls(workbench):
    _, controller = workbench
    preview = controller.research_preview({'goal': 'Test synthetic momentum learning',
        'dataset_id': 'ds_' + 'a' * 24, 'template_id': 'momentum', 'mode': 'agent',
        'connection_id': 'configured', 'max_trials': '2'})
    assert preview['mode'] == 'agent'
    assert preview['_request']['connection_id'] == 'configured'
    assert any('模型服务' in change for change in preview['changes'])


def test_server_requires_local_bearer_and_rejects_oversized_body(workbench):
    config, controller = workbench
    with TestClient(create_app(config, controller)) as client:
        assert client.post('/bridge/open', json={'actor': 'owner'}).status_code == 401
        headers = {'Authorization': 'Bearer ' + config['_token']}
        assert client.post('/bridge/open', headers=headers, json={'actor': 'stranger'}).status_code == 403
        assert client.post('/bridge/event', headers=headers, content='x' * (256 * 1024 + 1)).status_code == 413
        assert client.get('/health').json()['ok'] is True


def test_no_public_research_endpoint_or_wildcard_authorization(workbench):
    config, controller = workbench
    with pytest.raises(ValueError): ResearchClient('http://example.invalid')
    with pytest.raises(ValueError): Workbench({**config, 'allowed_users': ['*']}, controller.store, None, None, None)


def test_send_response_loss_reuses_identical_card_after_restart(workbench):
    from feishu_workbench.clients import DeliveryError
    _, controller = workbench
    session_id = controller.open('owner')
    command = controller.store.claim()
    cards = []
    def send(actor, chat, card, operation_id):
        cards.append(card)
        if len(cards) == 1:
            raise DeliveryError('response lost after successful delivery')
        return {'message_id': 'om_sent', 'chat_id': 'private_chat'}
    controller.delivery.send = send
    with pytest.raises(DeliveryError):
        controller.execute(command)
    controller.store = Store(controller.store.path)
    controller.execute(command)
    controller.store.finish(command)
    assert cards[0] == cards[1]
    session = controller.store.session(session_id)
    assert controller.accept(callback(session, action='daily.open'))['toast']['type'] == 'info'


def test_failed_patch_keeps_visible_card_buttons_valid(workbench):
    from feishu_workbench.clients import DeliveryError
    _, controller = workbench
    session = session_card(controller)
    controller.accept(callback(session, action='home'))
    command = controller.store.claim()
    def fail(*args):
        raise DeliveryError('temporary outage')
    controller.delivery.patch = fail
    with pytest.raises(DeliveryError):
        controller.execute(command)
    controller.store.finish(command, 'delivery failed')
    assert controller.store.session(session['id'])['nonces'] == session['nonces']
    assert controller.accept(callback(session))['toast']['type'] == 'info'


def test_expired_confirmation_rejected_and_daily_open_request_deduplicated(workbench):
    _, controller = workbench
    assert controller.open('owner', 'daily', 'daily_2026-01-02') == controller.open('owner', 'daily', 'daily_2026-01-02')
    pending = controller.store.claim()
    controller.store.finish(pending)
    session = session_card(controller)
    with controller.store.db() as db:
        db.execute('UPDATE sessions SET expires=0 WHERE id=?', (session['id'],))
    with pytest.raises(Rejected, match='过期'):
        controller.accept(callback(session))


def test_negative_cash_reconciliation_is_explicit(portfolio):
    proposal = portfolio.preview({'operation': 'set_cash', 'date': '2026-01-02',
                                  'account': 'Example', 'currency': 'CNY', 'amount': '-1.25'})
    assert state(portfolio)['groups']['Example']['cash'] == 1000
    portfolio.confirm(proposal, 'reconcile')
    assert state(portfolio)['groups']['Example']['cash'] == -1.25


def test_asset_valuation_is_used_only_for_matching_holdings(portfolio):
    snapshot = {'date': '2026-01-02', 'groups': copy.deepcopy(state(portfolio)['groups']),
                'summary': {'total_value': 1500, 'market_daily_change': 5}, 'generated_at': '2026-01-02T16:00:00'}
    snapshot['groups']['Example']['positions'][0]['market_value_cny'] = 150
    target = portfolio.portfolio_dir / 'snapshots/2026-01-02.json'
    target.parent.mkdir()
    target.write_text(json.dumps(snapshot))
    assert portfolio.overview()['total'] == '¥1,500.00'
    assert portfolio.overview()['positions'][0]['market_value'] == '¥150.00'
    portfolio.confirm(portfolio.preview(values()), 'later_trade')
    assert portfolio.overview()['total'] == '等待日报估值'
    assert 'market_value' not in portfolio.overview()['positions'][0]


def test_real_research_results_fit_card_and_show_both_evaluation_sets(tmp_path):
    import asyncio
    from core.lab_service import LabService
    from feishu_workbench.cards import build_card, CardContext
    from feishu_workbench.service import ACTIONS
    async def scenario():
        service = LabService(tmp_path / 'isolated_lab')
        await service.start()
        try:
            run = service.submit({'objective': 'Synthetic stock research',
                                  'dataset_id': service.store.datasets()[0]['id'], 'template_id': 'volatility'})
            for _ in range(200):
                result = service.public_run(run['id'])
                if result['status'] in {'completed', 'failed'}:
                    break
                await asyncio.sleep(.03)
            assert result['status'] == 'completed', result.get('error')
            model = Workbench.run_model(result)
            labels = [v['label'] for v in model['metrics']]
            assert any('验证集' in label for label in labels)
            assert any('测试集' in label for label in labels)
            card = build_card('research_result', model, CardContext('test', 1, {a: a.replace('.', '_') for a in ACTIONS}))
            assert 'chart' in json.dumps(card)
            assert len(json.dumps(card, ensure_ascii=False).encode()) < 28000
        finally:
            await service.stop()
    asyncio.run(scenario())
