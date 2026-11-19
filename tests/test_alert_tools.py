"""The agent can read alert rules and propose one; only the user's confirmation creates it, exactly once."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from core import agent_tools, notify, proposals
from core.agent_tools import alert_tools
from core.alerts import proposal, rules
from core.market.runtime import set_market_runtime
from core.tools import ToolExecutor
from tests.test_alert_monitor import Market

CN = 'SHA:600001'


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.setattr(notify, '_deliverers', [])
    monkeypatch.setattr(proposals, '_kinds', {})
    proposal.register()
    scripted = Market()
    scripted.price(CN, '10.5')
    set_market_runtime(scripted.runtime)
    yield scripted
    set_market_runtime(None)


@pytest.fixture
def executor():
    return ToolExecutor(alert_tools.alert_tools(None))


@pytest.fixture
def client():
    app = FastAPI()
    for router, prefix, tags in features.routers():
        app.include_router(router, prefix=prefix, tags=tags)
    return TestClient(app)


def call(executor, name, **arguments):
    return asyncio.run(executor.execute_tool(name, arguments))


def test_the_tools_are_registered_for_the_agent():
    names = [tool.name for tool in agent_tools.build_all(None)]
    assert 'list_alert_rules' in names and 'propose_alert_rule' in names
    [schema] = [schema for schema in ToolExecutor(alert_tools.alert_tools(None)).get_tool_schemas()
                if schema['name'] == 'propose_alert_rule']
    assert schema['input_schema']['required'] == ['ticker', 'kind', 'threshold']
    assert schema['input_schema']['properties']['kind']['enum'] == [
        'stop_loss', 'target_price', 'day_change_pct', 'position_weight_max']


def test_a_proposal_creates_nothing_until_the_user_confirms_it_once(executor, client):
    result = call(executor, 'propose_alert_rule', ticker='sha:600001', kind='stop_loss', threshold='9.50',
                  note='跌破年线')
    # Changed in review round 3: the summary repeats the person's words and reaches the model only as
    # untrusted_text, as do the payload's free texts (core.agent_tools._envelope.proposal_response).
    assert result['status'] == 'awaiting_user_confirmation'
    assert result['summary'] == {'untrusted_text': '新增止损提醒：SHA:600001，价格跌到 9.5 或以下时提醒（每个交易日最多 3 次）。'
                                                   '备注：跌破年线。只提醒，不会下单。'}
    assert result['payload'] == {'ticker': CN, 'kind': 'stop_loss', 'threshold': '9.5', 'account': None,
                                 'enabled': True, 'cooldown_seconds': 300, 'note': {'untrusted_text': '跌破年线'}}
    assert 'Nothing has been created' in result['message']
    assert rules.list_rules() == []
    [pending] = proposals.list()
    assert pending['id'] == result['proposal_id'] and pending['source'] == 'agent:chat'

    confirmed = client.post(f"/api/proposals/{result['proposal_id']}/confirm")
    assert confirmed.status_code == 200 and confirmed.json()['status'] == 'applied'
    again = client.post(f"/api/proposals/{result['proposal_id']}/confirm")
    assert again.status_code == 200 and again.json()['result'] == confirmed.json()['result']
    [rule] = rules.list_rules()
    assert confirmed.json()['result'] == {'rule_id': rule['id']}
    assert (rule['ticker'], rule['threshold'], rule['source'], rule['note']) == (CN, '9.5', 'agent:chat', '跌破年线')


def test_proposing_the_same_rule_twice_stores_one_proposal(executor):
    first = call(executor, 'propose_alert_rule', ticker=CN, kind='target_price', threshold='12')
    second = call(executor, 'propose_alert_rule', ticker=CN, kind='target_price', threshold='12.0')
    assert first['proposal_id'] == second['proposal_id'] and len(proposals.list()) == 1


def test_an_invalid_proposal_is_refused_with_a_chinese_reason(executor):
    result = call(executor, 'propose_alert_rule', ticker='600001', kind='stop_loss', threshold='9')
    assert result['status'] == 'not_proposed' and '交易所:代码' in result['error']
    assert call(executor, 'propose_alert_rule', ticker=CN, kind='day_change_pct', threshold='0')['status'] == 'not_proposed'
    assert proposals.list() == [] and rules.list_rules() == []


def test_a_rule_that_already_exists_cannot_be_proposed_or_confirmed_again(executor, client):
    result = call(executor, 'propose_alert_rule', ticker=CN, kind='stop_loss', threshold='9')
    existing = rules.create({'ticker': CN, 'kind': 'stop_loss', 'threshold': '9'})      # the user made it by hand
    refused = client.post(f"/api/proposals/{result['proposal_id']}/confirm")
    assert refused.status_code == 422 and f"#{existing['id']}" in refused.json()['detail']
    assert [rule['id'] for rule in rules.list_rules()] == [existing['id']]
    duplicate = call(executor, 'propose_alert_rule', ticker=CN, kind='stop_loss', threshold='9')
    assert duplicate['status'] == 'not_proposed' and '已有相同的提醒规则' in duplicate['error']


@pytest.mark.parametrize('threshold', ['1e22', '1e999999', '1e1000000', 'NaN', '٩'])
def test_unsupported_thresholds_from_the_model_are_not_proposed(executor, threshold):
    result = call(executor, 'propose_alert_rule', ticker=CN, kind='stop_loss', threshold=threshold)
    assert result['status'] == 'not_proposed' and '阈值' in result['error']
    assert proposals.list() == []


def test_whole_seconds_from_the_model_are_accepted(executor):
    result = call(executor, 'propose_alert_rule', ticker=CN, kind='stop_loss', threshold='9', cooldown_seconds=600.0)
    assert result['payload']['cooldown_seconds'] == 600
    assert call(executor, 'propose_alert_rule', ticker=CN, kind='stop_loss', threshold='8',
                cooldown_seconds=1.5)['status'] == 'not_proposed'


def test_listing_shows_prices_with_their_source_and_the_monitor_state(executor):
    rules.create({'ticker': CN, 'kind': 'stop_loss', 'threshold': '10'})
    listing = call(executor, 'list_alert_rules')
    [rule] = listing['rules']
    assert rule['condition'] == '价格跌到 10 或以下' and rule['source'] == 'manual'
    assert rule['market']['price'] == '10.5' and rule['market']['distance'] == '0.5'
    assert rule['market']['quote']['source'] == 'scripted' and rule['market']['quote']['status'] == 'live'
    assert listing['monitor']['status'] == 'not_started' and listing['monitor']['running'] is False
    assert '不会下单' in listing['note']
