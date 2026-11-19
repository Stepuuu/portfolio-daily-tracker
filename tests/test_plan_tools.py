"""The agent can list trade plans and propose one; only the user's confirmation creates a draft, exactly once."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from core import agent_tools, plans, proposals
from core.agent_tools import plan_tools
from core.alerts import rules
from core.market.runtime import set_market_runtime
from core.tools import ToolExecutor
from tests.fakes import build_ledger, opening, position
from tests.plan_support import CN, OTHER, active, payload, pin
from tests.test_alert_monitor import Market


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    pin(monkeypatch)
    monkeypatch.setattr(proposals, '_kinds', {})
    plans.register_proposal_kind()
    scripted = Market()
    scripted.price(CN, '10.5')
    set_market_runtime(scripted.runtime)
    yield tmp_path
    set_market_runtime(None)


@pytest.fixture
def executor():
    return ToolExecutor(plan_tools.plan_tools(None))


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
    assert 'list_trade_plans' in names and 'propose_trade_plan' in names
    [schema] = [schema for schema in ToolExecutor(plan_tools.plan_tools(None)).get_tool_schemas()
                if schema['name'] == 'propose_trade_plan']
    assert schema['input_schema']['required'] == ['ticker', 'thesis', 'invalidation']
    assert 'activate' not in ' '.join(names) and 'close_trade_plan' not in names, 'the agent cannot change a plan'


def test_a_proposal_creates_nothing_until_the_user_confirms_it_once(executor, client):
    result = call(executor, 'propose_trade_plan', ticker='sha:600001', thesis='示例理由', invalidation='示例条件',
                  account='Main', entry_low='10', entry_high='10.50', stop_price='9.5', target_price='12',
                  planned_quantity='1000', review_on='2026-12-01')
    # Changed in review round 3: the summary repeats the person's words and reaches the model only as
    # untrusted_text, as do the payload's free texts (core.agent_tools._envelope.proposal_response).
    assert result['status'] == 'awaiting_user_confirmation' and 'Nothing has been created' in result['message']
    assert result['summary']['untrusted_text'] == (
        '新建交易计划（草稿）：买入 SHA:600001，账户“Main”；入场区间 10–10.5；止损价 9.5；目标价 12；'
        '计划仓位 1000 股；最大亏损 1000.00 CNY；复查日期 2026-12-01；买入理由：示例理由；判断失效条件：示例条件。'
        '确认后保存为草稿，需要你在计划页启用后才会生成提醒。交易计划只用于记录和提醒，不会下单。')
    assert result['payload'] == {
        'ticker': CN, 'account': {'untrusted_text': 'Main'}, 'direction': 'buy',
        'thesis': {'untrusted_text': '示例理由'}, 'invalidation': {'untrusted_text': '示例条件'},
        'entry_low': '10', 'entry_high': '10.5', 'stop_price': '9.5', 'target_price': '12',
        'planned_weight_pct': None, 'planned_quantity': '1000', 'review_on': '2026-12-01'}
    assert plans.list() == [] and rules.list_rules() == []
    [pending] = proposals.list()
    assert (pending['id'], pending['kind'], pending['source']) == (result['proposal_id'], 'trade_plan', 'agent:chat')

    confirmed = client.post(f"/api/proposals/{result['proposal_id']}/confirm")
    again = client.post(f"/api/proposals/{result['proposal_id']}/confirm")
    assert confirmed.status_code == again.status_code == 200
    assert confirmed.json()['status'] == 'applied' and again.json()['result'] == confirmed.json()['result']
    [plan] = plans.list()
    assert confirmed.json()['result'] == {'plan_id': plan['id'], 'status': 'draft'}
    assert (plan['status'], plan['source'], plan['stop_price'], plan['account']) == ('draft', 'agent:chat', '9.5', 'Main')
    assert rules.list_rules() == [], 'a draft has no alerts; activation is the user\'s step'


def test_the_summary_names_every_field_even_when_unset_or_long(executor):
    result = call(executor, 'propose_trade_plan', ticker=CN, thesis='长' * 400, invalidation='条件',
                  planned_weight_pct='8')
    summary = result['summary']['untrusted_text']
    for words in ('全部账户', '入场区间 未填–未填', '止损价 未填', '目标价 未填', '计划仓位 占组合 8%', '最大亏损 无法计算',
                  '复查日期 未填', '判断失效条件：条件', '不会下单'):
        assert words in summary, words
    assert len(summary) <= proposals.MAX_SUMMARY and '…' in summary


def test_the_longest_plan_still_fits_the_summary_with_every_field(executor):
    price, quantity = '99999999.99999999', '999999999999.99999999'   # the most digits each may have
    result = call(executor, 'propose_trade_plan', ticker='NASDAQ:ABCDEFGHIJKLMNOPQRST', thesis='理' * 4000,
                  invalidation='条' * 4000, account='账' * 240, entry_low='0.00000002', entry_high='0.00000003',
                  stop_price='0.00000001', target_price=price, planned_quantity=quantity, review_on='2026-12-01')
    assert result['status'] == 'awaiting_user_confirmation', result
    summary = result['summary']['untrusted_text']
    assert len(summary) < 500 and summary.endswith(plans.NOT_AN_ORDER), len(summary)
    for words in ('入场区间', '止损价', '目标价', '计划仓位', '最大亏损', '复查日期 2026-12-01', '买入理由', '判断失效条件'):
        assert words in summary, words
    assert result['payload']['thesis'] == {'untrusted_text': '理' * 4000}, 'the payload keeps the full text'


def test_proposing_the_same_plan_twice_stores_one_proposal(executor):
    first = call(executor, 'propose_trade_plan', ticker=CN, thesis='理由', invalidation='条件', stop_price='9')
    second = call(executor, 'propose_trade_plan', ticker=CN, thesis='理由', invalidation='条件', stop_price='9.0')
    assert first['proposal_id'] == second['proposal_id'] and len(proposals.list()) == 1


def test_an_invalid_plan_is_refused_with_a_chinese_reason(executor):
    for arguments, words in (({'ticker': '600001'}, '交易所:代码'), ({'stop_price': '11', 'entry_low': '10'}, '止损价'),
                             ({'planned_weight_pct': '5', 'planned_quantity': '100'}, '只填一项')):
        result = call(executor, 'propose_trade_plan', **{'ticker': CN, 'thesis': '理由', 'invalidation': '条件',
                                                         **arguments})
        assert result['status'] == 'not_proposed' and words in result['error']
    assert proposals.list() == [] and plans.list() == []


def test_numbers_reach_the_plan_rules_exactly_or_not_at_all(executor):
    # A JSON number with a fraction arrives as a float, already rounded (100000000.000000001 is 1e8): it is
    # refused, never checked in its rounded form. A whole number is exact and is passed on as its digits.
    for value in (100000000.000000001, 9.5, True):
        result = call(executor, 'propose_trade_plan', ticker=CN, thesis='理由', invalidation='条件', stop_price=value)
        assert result['status'] == 'not_proposed' and 'stop_price' in result['error'], result
    assert proposals.list() == []
    exact = call(executor, 'propose_trade_plan', ticker=CN, thesis='理由', invalidation='条件',
                 stop_price='100000000.000000001')
    assert exact['status'] == 'not_proposed', 'the exact string is checked as written'
    whole = call(executor, 'propose_trade_plan', ticker=CN, thesis='理由', invalidation='条件', stop_price=9,
                 planned_quantity=1000)
    assert whole['status'] == 'awaiting_user_confirmation', whole
    assert (whole['payload']['stop_price'], whole['payload']['planned_quantity']) == ('9', '1000')


def test_listing_shows_plans_and_the_market_of_active_ones(executor, desk):
    build_ledger(desk, [opening('Main', cash={'CNY': '9000'}, positions=[position(CN, '100', '10')], date='2026-11-13')])
    running = active(stop_price='9', planned_weight_pct='10')
    draft = plans.create(payload(OTHER))
    listing = call(executor, 'list_trade_plans')
    assert [item['id'] for item in listing['plans']] == [draft['id'], running['id']]
    shown = listing['plans'][1]
    assert (shown['status'], shown['stop_price'], shown['market']['price'], shown['market']['weight_pct']) == (
        'active', '9', '10.5', '10.45')
    assert shown['market']['quote']['source'] == 'scripted' and listing['plans'][0]['market'] is None
    assert shown['conflicts'] == [] and listing['plans'][0]['conflicts'] == []
    assert '不会下单' in listing['note']
    assert [item['id'] for item in call(executor, 'list_trade_plans', status='draft')['plans']] == [draft['id']]
    assert '未知的计划状态' in call(executor, 'list_trade_plans', status='open')['error']
