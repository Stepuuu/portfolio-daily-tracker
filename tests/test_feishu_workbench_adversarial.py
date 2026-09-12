"""Offline recovery checks against temporary portfolios and fake message delivery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import sqlite3

import pytest

from feishu_workbench.cards import CardContext, build_card
from feishu_workbench.clients import DeliveryError
from feishu_workbench.portfolio import PortfolioAdapter, PortfolioError
from feishu_workbench.service import Workbench
from feishu_workbench.store import Store


@pytest.fixture
def adapter(tmp_path):
    project = tmp_path / 'example'
    holdings = project / 'portfolio' / 'holdings'
    holdings.mkdir(parents=True)
    (project / 'scripts').mkdir()
    data = {'date': '2026-01-02', 'groups': {'Example': {
        'cash': 1000, 'cash_balances': {'CNY': 1000, 'USD': 200}, 'fund': 50,
        'positions': [{'ticker': 'NASDAQ:EXAMPLE', 'name': 'Example',
                       'quantity': 1.5, 'cost_price': 10}],
    }}}
    (holdings / '2026-01-02.json').write_text(json.dumps(data))
    return PortfolioAdapter(project, tmp_path / 'state')


def trade():
    return {'operation': 'buy', 'account': 'Example', 'date': '2026-01-02',
            'ticker': 'NASDAQ:EXAMPLE', 'quantity': '0.125', 'price': '12',
            'fee': '0.01', 'currency': 'USD'}


def holdings(adapter):
    return json.loads((adapter.holdings_dir / '2026-01-02.json').read_text())


class Delivery:
    def __init__(self):
        self.cards = []

    def send(self, actor, chat, card, operation_id):
        self.cards.append(copy.deepcopy(card))
        return {'message_id': 'om_example', 'chat_id': 'example_chat'}

    def patch(self, message, card):
        self.cards.append(copy.deepcopy(card))


@pytest.fixture
def controller(tmp_path, adapter):
    return Workbench({'allowed_users': ['example_owner'], 'account_id': 'qr'},
                     Store(tmp_path / 'cards.sqlite3'), adapter, None, Delivery())


def test_dynamic_preview_delivery_retry_reuses_the_persisted_content(controller):
    """Holdings can change between a lost PATCH response and its retry."""
    identifier = controller.open('example_owner')
    initial = controller.store.claim()
    controller.execute(initial)
    controller.store.finish(initial)
    session = controller.store.session(identifier)
    assert controller.store.accept(session['actor'], session['chat'], session['message'],
                                   session['nonces']['daily.preview'], trade())
    command = controller.store.claim()
    attempts = []

    def patch(message, card):
        attempts.append(copy.deepcopy(card))
        if len(attempts) == 1:
            raise DeliveryError('simulated response loss')

    controller.delivery.patch = patch
    with pytest.raises(DeliveryError):
        controller.execute(command)
    path = controller.portfolio.holdings_dir / '2026-01-02.json'
    changed = holdings(controller.portfolio)
    changed['groups']['Example']['cash_balances']['USD'] = 201
    path.write_text(json.dumps(changed))
    controller.store = Store(controller.store.path)  # Simulated process restart.
    controller.execute(command)
    controller.store.finish(command)
    session = controller.store.session(identifier)
    expected = build_card(session['view'], session['model'],
                          CardContext(identifier, session['revision'], session['nonces']))
    assert attempts[0] == attempts[1] == expected
    # This old proposal must now require a fresh preview before any mutation.
    with pytest.raises(PortfolioError):
        controller.portfolio.confirm(session['model']['_preview'], 'later_confirmation')
    assert holdings(controller.portfolio) == changed


def test_worker_survives_transient_claim_lock(controller, monkeypatch):
    controller.open('example_owner')
    claim, finish = controller.store.claim, controller.store.finish
    attempts, executions = [], []

    def flaky_claim(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise sqlite3.OperationalError('database is locked')
        return claim(*args, **kwargs)

    def done(command, error=''):
        finish(command, error)
        controller.stopping = True

    monkeypatch.setattr(controller.store, 'claim', flaky_claim)
    monkeypatch.setattr(controller.store, 'finish', done)
    monkeypatch.setattr(controller, 'execute', lambda command: executions.append(command['id']))
    asyncio.run(asyncio.wait_for(controller.worker(), timeout=8))
    assert len(attempts) >= 2
    assert len(executions) == 1
    assert controller.store.counts() == {'completed': 1}


def test_worker_retries_completion_write_without_reexecuting_command(controller, monkeypatch):
    controller.open('example_owner')
    finish = controller.store.finish
    attempts, executions = [], []

    def flaky_finish(command, error=''):
        attempts.append(True)
        if len(attempts) == 1:
            raise sqlite3.OperationalError('database is locked')
        finish(command, error)
        controller.stopping = True

    monkeypatch.setattr(controller.store, 'finish', flaky_finish)
    monkeypatch.setattr(controller, 'execute', lambda command: executions.append(command['id']))
    asyncio.run(asyncio.wait_for(controller.worker(), timeout=8))
    assert len(attempts) == 2
    assert len(executions) == 1
    assert controller.store.counts() == {'completed': 1}


def test_observer_survives_transient_database_lock(controller, monkeypatch):
    attempts = []

    def observed():
        attempts.append(True)
        if len(attempts) == 1:
            raise sqlite3.OperationalError('database is locked')
        controller.stopping = True
        return []

    monkeypatch.setattr(controller.store, 'observed_sessions', observed)
    asyncio.run(asyncio.wait_for(controller.observe(), timeout=8))
    assert len(attempts) == 2


def test_delivery_error_after_financial_commit_does_not_apply_trade_twice(controller, monkeypatch):
    preview = controller.portfolio.preview(trade())
    controller.open('example_owner')
    calls = []

    def execute(command):
        receipt = controller.portfolio.confirm(preview, command['id'])
        calls.append(receipt)
        if len(calls) == 1:
            raise DeliveryError('simulated receipt delivery outage')
        controller.stopping = True

    monkeypatch.setattr(controller, 'execute', execute)
    asyncio.run(asyncio.wait_for(controller.worker(), timeout=8))
    assert len(calls) == 2 and calls[0] == calls[1]
    assert holdings(controller.portfolio)['groups']['Example']['positions'][0]['quantity'] == 1.625
    assert holdings(controller.portfolio)['groups']['Example']['cash_balances']['USD'] == 198.49
    assert controller.store.counts() == {'completed': 1}


def test_concurrent_confirmations_of_one_preview_share_one_receipt(adapter):
    preview = adapter.preview(trade())
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda index: adapter.confirm(preview, f'confirmation_{index}'), range(6)))
    assert all(receipt == receipts[0] for receipt in receipts)
    with adapter._db() as db:
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 1
    assert holdings(adapter)['groups']['Example']['positions'][0]['quantity'] == 1.625


def test_pending_report_resumes_checkpoint_without_another_financial_write(adapter, monkeypatch):
    receipt = adapter.confirm(adapter.preview(trade()), 'report_recovery')
    target = adapter.holdings_dir / '2026-01-02.json'
    confirmed = target.read_bytes()
    checkpoint = adapter.portfolio_dir / 'pipeline-state' / '2026-01-02.json'
    calls = []

    def pipeline(day, resume):
        calls.append((day, resume))
        checkpoint.parent.mkdir(exist_ok=True)
        checkpoint.write_text(json.dumps({'holdings_hash': hashlib.sha256(confirmed).hexdigest(),
                                          'snapshot': True, 'report': False, 'push': 'not_started'}))
        return len(calls) > 1

    monkeypatch.setattr(adapter, '_execute_pipeline', pipeline)
    with pytest.raises(PortfolioError):
        adapter.run_report(receipt)
    assert adapter.run_report(receipt)['report_status'] == 'complete'
    assert adapter.run_report(receipt)['report_status'] == 'complete'
    assert calls == [('2026-01-02', False), ('2026-01-02', True)]
    assert target.read_bytes() == confirmed


def test_pending_report_without_checkpoint_is_never_restarted(adapter, monkeypatch):
    receipt = adapter.confirm(adapter.preview(trade()), 'report_uncertain')
    confirmed = holdings(adapter)
    calls = []
    monkeypatch.setattr(adapter, '_execute_pipeline', lambda day, resume: calls.append((day, resume)) or False)
    with pytest.raises(PortfolioError):
        adapter.run_report(receipt)
    with pytest.raises(PortfolioError, match='缺少可安全续跑'):
        adapter.run_report(receipt)
    assert calls == [('2026-01-02', False)]
    assert holdings(adapter) == confirmed


def test_actual_manual_research_dto_renders_result_card(tmp_path):
    """Use the real offline synthetic research worker, without any model API."""
    from core.lab_service import LabService

    async def scenario():
        service = LabService(tmp_path / 'research')
        await service.start()
        try:
            submitted = service.submit({'objective': 'Learn synthetic stock volatility prediction',
                                        'dataset_id': service.store.datasets()[0]['id'],
                                        'template_id': 'volatility', 'mode': 'manual'})
            for _ in range(300):
                run = service.public_run(submitted['id'])
                if run['status'] in {'completed', 'failed', 'cancelled'}:
                    break
                await asyncio.sleep(.1)
            assert run['status'] == 'completed', run.get('error')
            model = Workbench.run_model(run)
            assert model['completed'] == 1
            assert model['can_cancel'] is False
            assert any('测试集' in metric['label'] for metric in model['metrics'])
            assert len(model['equity']) > 10
            assert any('合成' in warning for warning in model['warnings'])
            context = CardContext('example_session', 1,
                                  {action: 'example_' + action.replace('.', '_')
                                   for action in ('research.open', 'research.list', 'home')})
            card = build_card('research_result', model, context)
            assert card['schema'] == '2.0'
            assert len(json.dumps(card, ensure_ascii=False).encode()) <= 28_000
        finally:
            await service.stop()

    asyncio.run(scenario())
