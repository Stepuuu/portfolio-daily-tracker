"""Numbers in tool calls reach the tools exactly as the model wrote them, never through a binary float.

Every provider adapter (Claude and OpenAI-compatible, plain and streamed) reads tool-call arguments with
``Decimal`` for JSON numbers: ``1000000000000.00000001`` and ``0.1`` written with 20 digits survive, where a
float would round them. The arguments are sent back in the conversation with the same digits. The tools that
propose something with a number (an alert rule, ledger events) check the exact value: a float (from any
other path) is refused, a ``Decimal`` is checked and stored as written. Mock transports; nothing leaves the
process.
"""
import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from core.llm.base import LLMConfig
from providers.llm.claude import ClaudeProvider
from providers.llm.openai import OpenAIProvider

BIG = '1000000000000.00000001'
TENTH = '0.10000000000000000555'          # 0.1 printed with 20 digits
ARGUMENTS = '{"ticker": "SHA:600001", "threshold": ' + BIG + ', "price": ' + TENTH + '}'


def exact(arguments):
    assert isinstance(arguments['threshold'], Decimal) and isinstance(arguments['price'], Decimal)
    assert format(arguments['threshold'], 'f') == BIG and format(arguments['price'], 'f') == TENTH
    assert arguments['ticker'] == 'SHA:600001'


@pytest.fixture
def transport(monkeypatch):
    """Answer every request with ``answer['body']``; keep the request bodies."""
    answer, sent = {}, []

    def handler(request):
        sent.append(request.content.decode())
        return httpx.Response(200, text=answer['body'], headers={'content-type': answer.get('type', 'application/json')})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return answer, sent


def stream(provider):
    async def consume():
        return [chunk async for chunk in provider.chat_stream([{'role': 'user', 'content': 'x'}])]
    return asyncio.run(consume())


def claude():
    return ClaudeProvider(LLMConfig(api_key='fixture', model='fixture'))


def openai():
    return OpenAIProvider(LLMConfig(api_key='fixture', model='fixture'))


def test_claude_reads_tool_arguments_exactly(transport):
    answer, _ = transport
    answer['body'] = ('{"model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}, "stop_reason": "tool_use", '
                      '"content": [{"type": "tool_use", "id": "t1", "name": "propose_alert_rule", "input": '
                      + ARGUMENTS + '}]}')
    response = asyncio.run(claude().chat([{'role': 'user', 'content': 'x'}]))
    exact(response.tool_calls[0]['input'])


def test_claude_stream_reads_tool_arguments_exactly(transport):
    answer, _ = transport
    events = [{'type': 'content_block_start', 'content_block': {'type': 'tool_use', 'id': 't1', 'name': 'x'}},
              {'type': 'content_block_delta', 'delta': {'type': 'input_json_delta', 'partial_json': ARGUMENTS[:30]}},
              {'type': 'content_block_delta', 'delta': {'type': 'input_json_delta', 'partial_json': ARGUMENTS[30:]}},
              {'type': 'content_block_stop'}, {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}}]
    answer['body'] = ''.join(f'data: {json.dumps(event)}\n\n' for event in events)
    answer['type'] = 'text/event-stream'
    exact(stream(claude())[-1]['tool_calls'][0]['input'])


@pytest.mark.parametrize('as_text', [True, False])
def test_openai_reads_tool_arguments_exactly(transport, as_text):
    answer, _ = transport
    arguments = json.dumps(ARGUMENTS) if as_text else ARGUMENTS     # some compatible servers send an object
    answer['body'] = ('{"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {"content": null, '
                      '"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": '
                      + arguments + '}}]}}]}')
    response = asyncio.run(openai().chat([{'role': 'user', 'content': 'x'}]))
    exact(response.tool_calls[0]['input'])


def test_openai_stream_reads_tool_arguments_exactly(transport):
    answer, _ = transport
    chunks = [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c1', 'function': {
                  'name': 'x', 'arguments': ARGUMENTS[:25]}}]}}]},
              {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': ARGUMENTS[25:]}}]}}]},
              {'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}]
    answer['body'] = ''.join(f'data: {json.dumps(chunk)}\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
    answer['type'] = 'text/event-stream'
    exact(stream(openai())[-1]['tool_calls'][0]['input'])


CONVERSATION = [
    {'role': 'user', 'content': 'x'},
    {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'c1', 'name': 'x',
                                       'input': {'threshold': Decimal(BIG), 'price': Decimal(TENTH)}}]},
    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'c1', 'content': '{}'}]},
]


@pytest.mark.parametrize('make', [claude, openai])
def test_the_exact_arguments_are_sent_back_with_their_digits(transport, make):
    answer, sent = transport
    answer['body'] = ('{"model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}, "stop_reason": "end_turn", '
                      '"content": [{"type": "text", "text": "ok"}], "choices": [{"finish_reason": "stop", '
                      '"message": {"content": "ok"}}]}')
    asyncio.run(make().chat(CONVERSATION))
    assert BIG in sent[-1] and TENTH in sent[-1]
    json.loads(sent[-1])                                              # still valid JSON


# -- the tools check the exact value -----------------------------------------------

@pytest.fixture
def desk(tmp_path, monkeypatch):
    from core.market.runtime import set_market_runtime
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    set_market_runtime(None)
    yield tmp_path
    set_market_runtime(None)


def propose_alert(**arguments):
    from core.agent_tools import alert_tools
    [_listing, propose] = alert_tools.alert_tools(None)
    return asyncio.run(propose.function(ticker='SHA:600001', kind='stop_loss', **arguments))


def test_an_alert_threshold_is_checked_on_its_exact_value(desk):
    # 100000000.000000001 as a float is 1e8, the largest threshold allowed; exactly, it is above it.
    refused = propose_alert(threshold=Decimal('100000000.000000001'))
    assert refused['status'] == 'not_proposed' and '1 亿' in str(refused['error'])
    floated = propose_alert(threshold=100000000.000000001)
    assert floated['status'] == 'not_proposed' and 'threshold' in str(floated['error'])
    kept = propose_alert(threshold=Decimal('9.50'))
    assert kept['status'] == 'awaiting_user_confirmation' and kept['payload']['threshold'] == '9.5'


def test_alert_cooldown_seconds_are_whole_and_exact(desk):
    assert propose_alert(threshold='9.5', cooldown_seconds=Decimal('600.0'))['status'] == 'awaiting_user_confirmation'
    assert propose_alert(threshold='9.6', cooldown_seconds=Decimal('600.5'))['status'] == 'not_proposed'


def propose_events(events):
    from core.tools import propose_ledger_tool
    return asyncio.run(propose_ledger_tool().function(events=events))


def buy(price):
    return {'kind': 'buy', 'date': '2026-11-16', 'account': 'Main', 'ticker': 'SHA:600001', 'currency': 'CNY',
            'quantity': 100, 'price': price, 'fee': '0'}


OPENING = {'kind': 'opening', 'date': '2026-11-13', 'account': 'Main', 'source': 'test',
           'external_id': 'numbers:opening', 'cash_balances': {'CNY': '10000'}, 'positions': [],
           'contributed_cny': '10000', 'fund_cny': '0'}


def test_ledger_event_numbers_are_checked_on_their_exact_value(desk):
    floated = propose_events([OPENING, buy(0.1)])
    assert floated['status'] == 'not_proposed' and 'price' in str(floated['error'])
    kept = propose_events([OPENING, buy(Decimal(TENTH))])
    assert kept['status'] == 'awaiting_user_confirmation', kept
    assert kept['events'][1]['price'] == TENTH.rstrip('0')
