"""Offline model/tool -> exact preview -> confirmation -> FX valuation journey."""
import asyncio
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.trader.agent import TraderAgent
from core.tools import ToolExecutor, propose_ledger_tool, get_portfolio_tool
from core.ledger import Ledger
from backend.api import ledger as api
import portfolio_snapshot as snapshot
from ledger_bridge import ledger_holdings
from tests.test_trader_multiround import ScriptedProvider, answer

DAY = '2026-09-01'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    proposal = ledger.propose([
        dict(kind='opening', date=DAY, account='Example', cash_balances={'CNY':'500','USD':'1000','HKD':'1000'}, contributed_cny='8400'),
        dict(kind='opening', date=DAY, account='Other', cash_balances={'CNY':'300'}, contributed_cny='300'),
    ])
    ledger.confirm(proposal['id'])
    app = FastAPI(); app.include_router(api.router, prefix='/api/ledger')
    return ledger, TestClient(app), tmp_path


def buy():
    return dict(kind='buy', date=DAY, account='Example', ticker='NASDAQ:EXAMPLE', currency='USD', quantity='2', price='100', fee='2')


@pytest.mark.parametrize('stream', [False, True])
def test_model_tool_confirmation_reload_and_fx_valuation(setup, stream):
    ledger, client, directory = setup
    call = dict(id='proposal-call', name='propose_ledger_events', input={'events':[buy()]})
    model = ScriptedProvider([answer(calls=[call]), answer('方案待确认，请核对现金和股数变化。')])
    agent = TraderAgent(model, ToolExecutor([propose_ledger_tool()]))
    async def chat():
        return ''.join([part async for part in agent.chat('Example 账户买入 NASDAQ:EXAMPLE 2 股，每股 100 美元，手续费 2 美元，日期 2026-09-01。', stream=stream)])
    asyncio.run(chat())
    result = json.loads(model.calls[1][-1]['content'][0]['content'])
    assert result['status'] == 'awaiting_user_confirmation'
    identifier = parse_qs(urlsplit(result['review_url']).query)['proposal'][0]
    assert identifier == result['id']
    assert ledger.state()['accounts']['Example']['cash_balances']['USD'] == '1000'
    preview = client.get('/api/ledger/proposals/'+identifier).json()
    assert preview['receipt'] is None
    assert preview['before']['accounts']['Example']['cash_balances']['USD'] == '1000'
    assert preview['after']['accounts']['Example']['cash_balances']['USD'] == '798'
    response = client.post(f'/api/ledger/proposals/{identifier}/confirm')
    assert response.status_code == 200
    assert client.post(f'/api/ledger/proposals/{identifier}/confirm').json() == response.json()
    restored = Ledger(directory/'ledger.sqlite3')
    durable = client.get('/api/ledger/proposals/'+identifier).json()
    assert durable['receipt'] == response.json() == restored.proposal(identifier)['receipt']
    assert durable['before'] == preview['before'] and durable['after'] == preview['after']
    assert len(restored.history()) == 3
    accounts = restored.state()['accounts']
    assert accounts['Example']['cash_balances'] == {'CNY':'500','USD':'798','HKD':'1000'}
    assert accounts['Other']['cash_balances'] == {'CNY':'300'}
    assert accounts['Example']['positions']['NASDAQ:EXAMPLE']['quantity'] == '2'
    # A subsequent AI read uses the confirmed ledger, not a cached flat portfolio.
    current = asyncio.run(get_portfolio_tool(None).function())
    assert current['accounts'] == accounts
    holdings = ledger_holdings(directory, DAY)
    def value(day, usd, hkd, previous=None):
        return snapshot.calculate_snapshot({**holdings,'date':day},{'NASDAQ:EXAMPLE':100}, {'NASDAQ:EXAMPLE':'USD'},
            {'CNY':1,'USD':usd,'HKD':hkd},previous,[])
    first = value(DAY,7,.9)
    assert first['summary']['total_value'] == 8686
    updated = value('2026-09-02',7.2,.88,first)
    assert updated['summary']['capital_change'] == 0
    assert updated['summary']['market_daily_change'] == pytest.approx(179.6)
    assert restored.state()['accounts'] == accounts


def test_discarded_and_unknown_links_are_404_and_do_not_book(setup):
    ledger, client, _ = setup
    proposal = ledger.propose([buy()])
    client.delete('/api/ledger/proposals/'+proposal['id'])
    assert client.get('/api/ledger/proposals/'+proposal['id']).status_code == 404
    assert client.get('/api/ledger/proposals/missing').status_code == 404
    assert len(ledger.history()) == 2


def test_stale_link_remains_reviewable_but_cannot_be_confirmed(setup):
    ledger, client, _ = setup
    first = ledger.propose([buy()]); second = ledger.propose([buy()])
    ledger.confirm(first['id'])
    assert client.get('/api/ledger/proposals/'+second['id']).status_code == 200
    assert client.post('/api/ledger/proposals/'+second['id']+'/confirm').status_code == 409
    assert len(ledger.history()) == 3


def test_openclaw_returns_same_durable_review_link(setup):
    ledger, client, directory = setup
    path = Path(__file__).resolve().parents[1]/'openclaw/tools/portfolio_tools.py'
    spec=importlib.util.spec_from_file_location('issue2_openclaw',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=asyncio.run(module.get_propose_events_tool(str(directory))['function']([buy()]))
    assert result['review_url'] == '/ledger?proposal='+result['id']
    assert client.get('/api/ledger/proposals/'+result['id']).status_code == 200
    assert len(ledger.history()) == 2
