"""Worker failures leave a diagnosable log line that carries no private data."""
import asyncio
import copy
import json
import logging

import pytest

from fastapi.testclient import TestClient

from feishu_workbench.app import create_app
from feishu_workbench.clients import DeliveryError
from feishu_workbench.portfolio import PortfolioAdapter, PortfolioError
from feishu_workbench.service import Workbench
from feishu_workbench.store import Rejected, Store


LOGGER = 'feishu_workbench.service'
PRIVATE = 'synthetic-credential 1234567.89 /synthetic/private/path'


class Delivery:
    def __init__(self):
        self.cards = []

    def send(self, actor, chat, card, operation_id):
        self.cards.append(copy.deepcopy(card))
        return {'message_id': 'om_example', 'chat_id': 'example_chat'}

    def patch(self, message, card):
        self.cards.append(copy.deepcopy(card))


@pytest.fixture
def controller(tmp_path):
    project = tmp_path / 'example'
    holdings = project / 'portfolio' / 'holdings'
    holdings.mkdir(parents=True)
    (project / 'scripts').mkdir()
    (holdings / '2026-01-02.json').write_text(json.dumps({'date': '2026-01-02', 'groups': {'Example': {
        'cash': 1000, 'cash_balances': {'CNY': 1000}, 'fund': 50, 'positions': []}}}))
    return Workbench({'allowed_users': ['example_owner'], 'account_id': 'qr'},
                     Store(tmp_path / 'cards.sqlite3'),
                     PortfolioAdapter(project, tmp_path / 'state'), None, Delivery())


def run_one(controller, monkeypatch, execute, reports=False):
    """Run the worker until its first command has been finished."""
    finish = controller.store.finish
    commands = []

    def done(command, error=''):
        finish(command, error)
        commands.append(command)
        controller.stopping = True

    monkeypatch.setattr(controller.store, 'finish', done)
    monkeypatch.setattr(controller, 'execute', execute)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, 'sleep', lambda seconds: real_sleep(0))
    asyncio.run(asyncio.wait_for(controller.worker(reports=reports), timeout=8))
    return commands[0]


def records(caplog, level):
    return [record for record in caplog.records if record.name == LOGGER and record.levelno == level]


def test_unexpected_error_is_logged_with_its_origin_and_without_its_message(controller, monkeypatch, caplog):
    controller.open('example_owner')

    def explode_inside_adapter(command):
        raise RuntimeError(PRIVATE)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        command = run_one(controller, monkeypatch, explode_inside_adapter)
    [record] = records(caplog, logging.ERROR)
    text = record.getMessage()
    assert 'home' in text and 'RuntimeError' in text
    # The stack locates the failure; the exception text may hold private values.
    assert 'explode_inside_adapter' in text and 'test_feishu_workbench_logging.py' in text
    for secret in (PRIVATE, 'synthetic-credential', '1234567.89', command['id'], 'example_owner'):
        assert secret not in caplog.text
    assert controller.store.counts() == {'failed': 1}
    assert PRIVATE not in json.dumps(controller.delivery.cards, ensure_ascii=False)


def test_chained_cause_is_located_without_either_message(controller, monkeypatch, caplog):
    controller.open('example_owner')

    def read_private_file():
        raise OSError(PRIVATE)

    def execute(command):
        try:
            read_private_file()
        except OSError as exc:
            raise ValueError(PRIVATE) from exc

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        run_one(controller, monkeypatch, execute)
    [record] = records(caplog, logging.ERROR)
    text = record.getMessage()
    assert 'ValueError' in text and 'OSError' in text and 'read_private_file' in text
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize('error', [Rejected('请先重新预览资产变化。'), PortfolioError('预览已过期，请刷新并重新确认。')])
def test_refused_command_is_logged_as_a_warning_without_a_stack(controller, monkeypatch, caplog, error):
    controller.open('example_owner')

    def execute(command):
        raise error

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        command = run_one(controller, monkeypatch, execute)
    assert not records(caplog, logging.ERROR)
    [record] = records(caplog, logging.WARNING)
    assert 'home' in record.getMessage() and type(error).__name__ in record.getMessage()
    assert 'Traceback' not in caplog.text and command['id'] not in caplog.text


def test_report_failure_is_logged_as_a_warning(controller, monkeypatch, caplog):
    session = controller.open('example_owner')
    controller.store.finish(controller.store.claim())
    controller.store.enqueue_report(session, {'operation_id': 'example_operation'}, 'example_operation')

    def execute(command):
        raise PortfolioError('组合已保存，日报尚未完成；可重试日报，不会重复买卖。')

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        run_one(controller, monkeypatch, execute, reports=True)
    [record] = records(caplog, logging.WARNING)
    assert 'daily.report' in record.getMessage()
    assert 'example_operation' not in caplog.text


def test_delivery_retry_and_exhaustion_are_logged(controller, monkeypatch, caplog):
    controller.open('example_owner')

    def execute(command):
        raise DeliveryError(PRIVATE)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        run_one(controller, monkeypatch, execute)
    messages = [record.getMessage() for record in records(caplog, logging.WARNING)]
    assert all('home' in m and 'DeliveryError' in m for m in messages)
    assert ['retry' in m for m in messages] == [True, True, False] and 'attempt 3; gave up' in messages[2]
    assert PRIVATE not in caplog.text and not records(caplog, logging.ERROR)


def test_undelivered_error_card_is_logged(controller, monkeypatch, caplog):
    controller.open('example_owner')

    def execute(command):
        raise Rejected('请先重新预览资产变化。')

    def render(command, view, model):
        raise DeliveryError(PRIVATE)

    monkeypatch.setattr(controller, 'render', render)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        run_one(controller, monkeypatch, execute)
    messages = [record.getMessage() for record in records(caplog, logging.WARNING)]
    assert len(messages) == 2 and 'error card' in messages[1] and 'DeliveryError' in messages[1]
    assert PRIVATE not in caplog.text


def test_observer_outage_is_logged_once_per_card_until_it_recovers(controller, monkeypatch, caplog):
    run = 'run_' + 'a' * 32
    session = {'id': 'example_session', 'revision': 1, 'model': {'_run_id': run, 'status': 'running'}}
    outcomes = ['down', 'down', 'up', 'down']

    class Research:
        def get(self, path):
            outcome = outcomes.pop(0)
            if not outcomes:
                controller.stopping = True
            if outcome == 'down':
                raise DeliveryError(PRIVATE)
            return {'id': run, 'status': 'running'}

    controller.research = Research()
    monkeypatch.setattr(controller.store, 'observed_sessions', lambda: [session])
    monkeypatch.setattr(controller, 'run_model', lambda run: session['model'])
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, 'sleep', lambda seconds: real_sleep(0))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        asyncio.run(asyncio.wait_for(controller.observe(), timeout=8))
    messages = [record.getMessage() for record in records(caplog, logging.WARNING)]
    assert len(messages) == 2 and all('DeliveryError' in m for m in messages)
    assert PRIVATE not in caplog.text and run not in caplog.text


def test_refused_and_malformed_bridge_events_are_logged_without_their_content(controller, caplog):
    config = {'_token': 'x' * 48}
    client = TestClient(create_app(config, controller))
    headers = {'Authorization': 'Bearer ' + config['_token']}
    stranger = {'account_id': 'qr', 'event_type': 'application.bot.menu_v6',
                'event': {'event_id': 'example_event', 'event_key': 'qrw_home',
                          'operator': {'operator_id': {'open_id': 'example_stranger'}}}}
    with caplog.at_level(logging.WARNING, logger='feishu_workbench.app'):
        assert client.post('/bridge/event', headers=headers, json=stranger).json()['toast']['type'] == 'error'
        malformed = {'account_id': 'qr', 'event_type': PRIVATE, 'event': {'event': PRIVATE}}
        assert client.post('/bridge/event', headers=headers, json=malformed).json()['toast']['type'] == 'error'
    messages = [record.getMessage() for record in caplog.records if record.name == 'feishu_workbench.app']
    assert len(messages) == 2 and all(record.levelno == logging.WARNING for record in caplog.records)
    assert 'application.bot.menu_v6' in messages[0] and 'Rejected' in messages[0]
    assert 'unrecognised' in messages[1]
    for private in ('example_stranger', 'example_event', PRIVATE, 'x' * 48):
        assert private not in caplog.text
