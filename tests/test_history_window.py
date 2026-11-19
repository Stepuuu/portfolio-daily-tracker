"""The trader agent's history window: bounded, whole turns, tool pairs intact, deterministic."""
import asyncio
import copy
import json

import pytest

from agents.trader import history
from agents.trader.agent import TraderAgent
from core.models import MessageRole
from core.tools import Tool, ToolExecutor
from tests.test_trader_multiround import ScriptedProvider, answer

SYSTEM = {'role': 'system', 'content': '系统提示'}


def chat_turn(number, size=900):
    return [{'role': 'user', 'content': f'问题 {number} ' + '问' * size},
            {'role': 'assistant', 'content': f'回答 {number}：价格 {number}.50 元 ' + '答' * size}]


def tool_turn(number, size=400):
    call = f'call_{number}'
    return [{'role': 'user', 'content': f'查一下 {number}'},
            {'role': 'assistant', 'content': [{'type': 'text', 'text': '查询中'},
                                              {'type': 'tool_use', 'id': call, 'name': 'probe', 'input': {}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call,
                                          'content': json.dumps({'value': 'x' * size})}]},
            {'role': 'assistant', 'content': f'结果 {number}'}]


def conversation(turns=60, tools_every=3):
    messages = [SYSTEM]
    for number in range(turns):
        messages += tool_turn(number) if number % tools_every == 0 else chat_turn(number)
    return messages


def history_size(messages):
    return sum(history._size(message) for message in messages if message['role'] != 'system')


def test_sixty_turns_stay_under_the_cap():
    messages = conversation(60)
    windowed = history.window(messages, max_turns=12, max_chars=24000)
    assert history_size(windowed) <= 24000
    kept = history.turns(windowed[1:])
    assert 1 <= len(kept) <= 12
    assert windowed[0]['role'] == 'system' and windowed[0]['content'].startswith('系统提示')
    assert windowed[-len(kept[-1]):] == history.turns(messages[1:])[-1], 'the newest turn is kept whole'
    omitted = 60 - len(kept)
    assert history.OMITTED_NOTE.format(count=omitted) in windowed[0]['content']
    assert history.STALE_NOTE in windowed[0]['content']
    assert len([message for message in windowed if message['role'] == 'system']) == 1


@pytest.mark.parametrize('max_turns, max_chars', [(12, 24000), (3, 24000), (12, 3000), (60, 10 ** 6), (1, 1)])
def test_tool_calls_keep_their_results_and_turns_start_with_the_user(max_turns, max_chars):
    windowed = history.window(conversation(60), max_turns=max_turns, max_chars=max_chars)
    body = windowed[1:]
    assert body[0]['role'] == 'user' and isinstance(body[0]['content'], str)
    calls, results = [], []
    for message in body:
        if isinstance(message['content'], list):
            calls += [block['id'] for block in message['content'] if block.get('type') == 'tool_use']
            results += [block['tool_use_id'] for block in message['content'] if block.get('type') == 'tool_result']
    assert calls == results
    assert len(history.turns(body)) <= max_turns


def test_the_window_is_deterministic_and_does_not_change_its_input():
    messages = conversation(60)
    before = copy.deepcopy(messages)
    first = history.window(messages)
    assert history.window(copy.deepcopy(messages)) == first == history.window(messages)
    assert messages == before


def test_a_newest_turn_longer_than_the_cap_is_still_kept():
    # Changed in review round 3: the newest message is kept, shortened in its middle to fit the budget.
    messages = [SYSTEM, *chat_turn(1), {'role': 'user', 'content': '长' * 30000}]
    windowed = history.window(messages, max_chars=24000)
    assert len(windowed) == 2 and windowed[-1]['role'] == 'user'
    assert windowed[-1]['content'].startswith('长' * 1000) and windowed[-1]['content'].endswith('长' * 1000)
    assert sum(history._size(message) for message in windowed) <= 24000
    assert '已省略更早的 1 轮对话' in windowed[0]['content']


def test_an_oversized_newest_message_is_shortened_in_its_middle_with_a_visible_marker():
    """Review finding: a 12,000-character system prompt plus a 25,000-character latest message sent all 37,000
    characters against the 36,000-character budget."""
    system = {'role': 'system', 'content': '上' * 12000}
    message = '头' * 100 + '中' * 24800 + '尾' * 100
    windowed = history.window([system, *chat_turn(1), {'role': 'user', 'content': message}], max_chars=36000)
    assert sum(history._size(item) for item in windowed) <= 36000
    assert windowed[0]['content'].startswith('上' * 12000), 'the system prompt is kept whole'
    sent = windowed[-1]['content']
    assert sent.startswith('头' * 100) and sent.endswith('尾' * 100)
    assert '用户消息过长' in sent
    omitted = 25000 - sent.count('头') - sent.count('中') - sent.count('尾')
    assert f'省略了 {omitted} 个字' in sent
    assert windowed.shortened == {'total': 25000, 'omitted': omitted}
    small = history.window([system, {'role': 'user', 'content': '现在呢'}], max_chars=36000)
    assert small.shortened is None and small[-1]['content'] == '现在呢'


@pytest.mark.parametrize('stream', [False, True])
def test_the_agent_says_in_its_reply_that_the_message_was_shortened(stream):
    from core.models import AgentContext
    provider = ScriptedProvider([answer('好的')])
    agent = TraderAgent(provider)
    agent.start_conversation()
    context = AgentContext(conversation=agent.conversation, portfolio_summary='示' * 12000)
    message = '头' + '中' * 25000 + '尾'

    async def run():
        return ''.join([part async for part in agent.chat(message, context, stream=stream)])
    reply = asyncio.run(run())
    sent = provider.calls[0]
    assert sum(history._size(item) for item in sent) <= agent.history_max_chars
    assert sent[-1]['content'].startswith('头') and sent[-1]['content'].endswith('尾') and '用户消息过长' in sent[-1]['content']
    assert reply.startswith('好的') and '你的消息有 25002 个字' in reply and '省略' in reply
    assert agent.conversation.messages[-1].content == reply, 'the history shows what the user saw'
    assert agent.conversation.messages[-2].content == message, 'the stored message itself is whole'


def test_short_histories_have_no_omission_note():
    single = history.window([SYSTEM, {'role': 'user', 'content': '你好'}])
    assert single == [SYSTEM, {'role': 'user', 'content': '你好'}], 'one turn: nothing to say'
    two = history.window([SYSTEM, *chat_turn(1, 10), {'role': 'user', 'content': '现在呢'}])
    assert history.STALE_NOTE in two[0]['content'] and '已省略' not in two[0]['content']
    assert len(two) == 4


def test_without_a_system_message_the_notes_get_one():
    messages = conversation(30)[1:]
    windowed = history.window(messages, max_turns=2)
    assert windowed[0]['role'] == 'system' and history.STALE_NOTE in windowed[0]['content']
    assert windowed[1:] == [message for turn in history.turns(messages)[-2:] for message in turn]


def test_the_agent_sends_a_bounded_history_and_tool_rounds_still_work():
    async def probe():
        return {'price': '12.34'}
    provider = ScriptedProvider([answer('查询中', [{'id': 'call_new', 'name': 'probe', 'input': {}}]),
                                 answer('现价 12.34 元')])
    agent = TraderAgent(provider, ToolExecutor([Tool('probe', 'probe', [], probe)]))
    agent.start_conversation()
    for number in range(60):
        agent.conversation.add_message(MessageRole.USER, f'问题 {number} ' + '问' * 900)
        agent.conversation.add_message(MessageRole.ASSISTANT, f'回答 {number} ' + '答' * 900)

    async def run():
        return ''.join([part async for part in agent.chat('现在怎么样', stream=False)])
    assert asyncio.run(run()) == '现价 12.34 元'
    first = provider.calls[0]
    assert history_size(first) <= agent.history_max_chars
    assert len(history.turns(first[1:])) <= agent.history_max_turns
    assert first[-1] == {'role': 'user', 'content': '现在怎么样'}
    assert history.STALE_NOTE in first[0]['content']
    second = provider.calls[1]
    assert second[:len(first)] == first, 'the tool round extends the same windowed list'
    assert second[-1]['content'][0]['tool_use_id'] == 'call_new'
    assert agent.last_tool_results == [{'price': '12.34'}]
    assert len(agent.conversation.messages) == 122, 'the stored conversation itself is not truncated'


def test_the_system_prompt_counts_against_the_budget():
    """Review finding: the live context in the system prompt sat outside the history budget."""
    system = {'role': 'system', 'content': '上下文' * 4000}            # 12,000 characters
    messages = [system] + conversation(60)[1:]
    windowed = history.window(messages, max_turns=60, max_chars=24000)
    assert sum(history._size(message) for message in windowed) <= 24000
    assert windowed[-1] == messages[-1], 'the newest turn is still kept'
    oversized = history.window([{'role': 'system', 'content': '长' * 30000}, *chat_turn(1),
                                {'role': 'user', 'content': '现在呢'}], max_chars=24000)
    assert oversized[-1] == {'role': 'user', 'content': '现在呢'} and len(oversized) == 2


def test_everything_the_agent_sends_fits_its_budget_with_a_full_context():
    from core.models import AgentContext
    provider = ScriptedProvider([answer('好的')])
    agent = TraderAgent(provider)
    agent.start_conversation()
    for number in range(60):
        agent.conversation.add_message(MessageRole.USER, f'问题 {number} ' + '问' * 900)
        agent.conversation.add_message(MessageRole.ASSISTANT, f'回答 {number} ' + '答' * 900)
    context = AgentContext(conversation=agent.conversation, portfolio_summary='示' * 12000)

    async def run():
        return ''.join([part async for part in agent.chat('现在怎么样', context, stream=False)])
    assert asyncio.run(run()) == '好的'
    sent = provider.calls[0]
    assert '示' * 12000 in sent[0]['content']
    assert sum(history._size(message) for message in sent) <= agent.history_max_chars
    assert sent[-1] == {'role': 'user', 'content': '现在怎么样'}
