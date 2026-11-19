"""The grounding footnote reaches the user: invented numbers are flagged, grounded ones are not. Offline."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from agents.trader.agent import TraderAgent
from backend.services.agent_service import AgentService
from core import agent_tools
from core.agent_tools import grounding
from core.market import search
from core.market.runtime import set_market_runtime
from core.memory import MemoryExtractor
from core.models import MessageRole
from core.tools import ToolExecutor, get_portfolio_tool
from tests.fakes import FakeClock, ScriptedSource, build_ledger, fake_runtime, fx_rate, opening, position, quote
from tests.test_trader_multiround import ScriptedProvider, answer

CN = 'SHA:600001'
FOOTNOTE_START = '以下数字未在本轮数据中找到'


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, date='2026-11-13',
                                    positions=[position(CN, '100', '10', '示例甲')])])
    clock = FakeClock('2026-11-17 10:00')
    now = clock.now()
    set_market_runtime(fake_runtime(clock, ScriptedSource(
        {CN: quote(CN, '12', prev_close='11', at=now - timedelta(seconds=3), name='示例甲'),
         'SHE:000002': quote('SHE:000002', '8.88', prev_close='8.5', at=now - timedelta(seconds=4), name='示例乙')},
        {'USD': fx_rate('USD', '7', at=now), 'HKD': fx_rate('HKD', '0.9', at=now)})))
    search.set_symbol_index(search.SymbolIndex(fetchers={}))
    yield tmp_path
    set_market_runtime(None)
    search.set_symbol_index(None)


def service_with(responses):
    provider = ScriptedProvider(responses)
    tools = [get_portfolio_tool(None)]
    tools += agent_tools.build_all(None, exclude=[tool.name for tool in tools])
    service = AgentService()
    service.agent = TraderAgent(provider, ToolExecutor(tools))
    service.agent.start_conversation()
    service.memory_manager = SimpleNamespace(get_context_string=lambda: '')
    service.memory_extractor = MemoryExtractor()
    return service, provider


def run_chat(service, message, stream):
    async def collect():
        return [chunk async for chunk in service.chat(message, stream=stream)]
    return asyncio.run(collect())


@pytest.mark.parametrize('stream', [False, True])
def test_an_invented_price_gets_the_footnote_as_the_final_chunk(desk, stream):
    service, provider = service_with([answer('示例甲现价 13.45 元，当日盈亏 100.00 元。')])
    chunks = run_chat(service, '我的持仓现在怎么样', stream)
    text = ''.join(chunks)
    assert chunks[-1].startswith('\n\n' + FOOTNOTE_START) and '13.45' in chunks[-1]
    assert '100.00' not in chunks[-1], 'the day profit is in the live context'
    assert text.endswith(grounding.footnote(['13.45元']))
    stored = service.agent.conversation.messages[-1]
    assert stored.role == MessageRole.ASSISTANT and stored.content == text, 'the history shows what the user saw'
    # The model was given the live context with the real numbers.
    assert '现价 12 CNY' in provider.calls[0][0]['content']


@pytest.mark.parametrize('stream', [False, True])
def test_a_grounded_answer_has_no_footnote(desk, stream):
    service, _ = service_with([answer('示例甲（SHA:600001）现价 12.00 元，涨跌幅 9.09%，持仓盈亏 200.00 元（20.00%），'
                                      '仓位 54.55%。这不是投资建议。')])
    text = ''.join(run_chat(service, '我的持仓现在怎么样', stream))
    assert FOOTNOTE_START not in text


@pytest.mark.parametrize('stream', [False, True])
def test_numbers_from_this_turns_tool_results_are_grounded(desk, stream):
    call = {'id': 'call_1', 'name': 'get_quotes', 'input': {'tickers': ['SHE:000002']}}
    service, provider = service_with([answer('查询中', [call]), answer('示例乙现价 8.88 元，涨跌幅 4.47%。')])
    text = ''.join(run_chat(service, '看看 SHE:000002', stream))
    assert FOOTNOTE_START not in text
    assert '8.88' in provider.calls[1][-1]['content'][0]['content']
    # The next turn does not inherit them: repeating the price from memory is flagged.
    service.agent.llm.responses.append(answer('示例乙还是 8.88 元。'))
    text = ''.join(run_chat(service, '再看看', stream))
    assert FOOTNOTE_START in text and '8.88' in text.split(FOOTNOTE_START)[1]


def test_numbers_the_user_typed_are_the_users(desk):
    service, _ = service_with([answer('按你说的 9.87 元止损，亏损约为 213.00 元。')])
    text = ''.join(run_chat(service, '我打算 9.87 元止损', False))
    assert FOOTNOTE_START in text
    flagged = text.split(FOOTNOTE_START)[1]
    assert '9.87' not in flagged and '213.00' in flagged, 'a computed figure is in no data and is flagged'


def test_extraction_path_appends_the_footnote_too(desk):
    service, _ = service_with([answer('示例甲现价 13.45 元。')])

    async def extract(user_message, ai_response):
        return {'memory_updates': [], 'suggestions': [], 'risks': []}
    service.llm_extractor = SimpleNamespace(extract=extract)
    result = asyncio.run(service.chat_with_extraction('我的持仓现在怎么样'))
    assert FOOTNOTE_START in result['response'] and '13.45' in result['response']


def test_a_failing_check_never_costs_the_answer(desk, monkeypatch):
    service, _ = service_with([answer('示例甲现价 13.45 元。')])

    def broken(*args, **kwargs):
        raise RuntimeError('broken')
    monkeypatch.setattr(grounding, 'ungrounded_numbers', broken)
    assert ''.join(run_chat(service, '怎么样', True)) == '示例甲现价 13.45 元。'


# ---------------------------------------------------------------- the context's evidence is its value fields only

@pytest.fixture
def empty_desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    set_market_runtime(fake_runtime(FakeClock('2026-11-17 10:00'), ScriptedSource()))
    search.set_symbol_index(search.SymbolIndex(fetchers={}))
    yield tmp_path
    set_market_runtime(None)
    search.set_symbol_index(None)


def flagged_numbers(text):
    """The numbers the footnote lists, exactly."""
    assert FOOTNOTE_START in text, text
    return text.split(FOOTNOTE_START)[1].split('：', 1)[1].split('、')


def test_instruction_text_and_codes_of_the_context_are_not_evidence(empty_desk):
    """Review finding: with no ledger the context's own 「补 0」 grounded an invented 0.00, and a
    watchlist line 「HKG:00700 现价不可用」 grounded an invented 700.00."""
    from core import watchlist
    watchlist.add('HKG:00700', '目标 66.6 元')
    service, provider = service_with([answer('示例现价 0.00 元，HKG:00700 现价 700.00 元，目标价 66.60 元。')])
    text = ''.join(run_chat(service, '我的持仓怎么样', False))
    system = provider.calls[0][0]['content']
    assert '补 0' in system and 'HKG:00700' in system and '66.6' in system
    flagged = flagged_numbers(text)
    assert '0.00元' in flagged and '700.00元' in flagged
    assert '66.60元' in flagged, 'a number inside a stored note is no value field'


def test_ledger_revision_and_counts_of_the_context_are_not_evidence(desk):
    from core.ledger import active_ledger
    revision = active_ledger().state()['revision']
    service, provider = service_with([answer(f'示例甲现价 {revision}.00 元。')])
    text = ''.join(run_chat(service, '我的持仓怎么样', False))
    assert f'账本版本 {revision}' in provider.calls[0][0]['content']
    assert f'{revision}.00元' in flagged_numbers(text), 'the revision is metadata, not a price'


# ---------------------------------------------------------------- image turns

IMAGE = [{'data': 'aGVsbG8=', 'type': 'image/png'}]


def image_service(text):
    provider = ScriptedProvider([answer(text)])
    service, _ = service_with([])
    service.llm_provider = provider

    async def extract(user_message, ai_response):
        return {'memory_updates': [], 'suggestions': [], 'risks': [], 'positions': []}
    service.llm_extractor = SimpleNamespace(extract=extract)
    return service, provider


@pytest.mark.parametrize('extract_data', [False, True])
def test_image_answers_get_the_footnote_with_image_wording(empty_desk, extract_data):
    """Review finding: an image answer inventing 「现价98765.43元」 with no prices in the context came back unchanged."""
    service, provider = image_service('截图里示例甲现价98765.43元，你说的止损 9.5 元在图中没有。')
    result = asyncio.run(service.chat_with_images('我想在 9.5 元止损，看看这张截图', IMAGE, extract_data=extract_data))
    response = result['response']
    assert FOOTNOTE_START in response
    first_line = response.split(FOOTNOTE_START)[1].splitlines()[0]
    assert first_line.split('：', 1)[1].split('、') == ['98765.43元'], 'the user\'s own 9.5 is theirs'
    assert '图片' in response.split(FOOTNOTE_START)[1] and '对照原图核对' in response
    stored = service.agent.conversation.messages[-1]
    assert stored.role == MessageRole.ASSISTANT and FOOTNOTE_START in stored.content
    assert provider.calls[0][0]['role'] == 'system'


def test_a_grounded_image_answer_has_no_footnote(desk):
    service, _ = image_service('截图与账本一致：示例甲现价 12 元。')
    result = asyncio.run(service.chat_with_images('看看这张截图', IMAGE, extract_data=False))
    assert FOOTNOTE_START not in result['response']


def test_an_oversized_image_message_is_shortened_and_the_reply_says_so(empty_desk):
    """Sibling of the oversized-message finding: the image turn sent the user's text whole, whatever its length."""
    from agents.trader import history
    service, provider = image_service('截图已收到。')
    message = '头' + '中' * 40000 + '尾'
    result = asyncio.run(service.chat_with_images(message, IMAGE, extract_data=False))
    sent = provider.calls[0]
    [text] = [block['text'] for block in sent[1]['content'] if block['type'] == 'text']
    assert text.startswith('头') and text.endswith('尾') and '用户消息过长' in text
    assert len(sent[0]['content']) + len(text) <= history.MAX_CHARS
    assert '你的消息有 40002 个字' in result['response']
