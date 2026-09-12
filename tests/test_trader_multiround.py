"""Multi-round chat regressions using in-memory tools and no model calls."""
import asyncio
import copy
import json

import pytest

from agents.trader.agent import TraderAgent
from core.llm.base import LLMResponse
from core.tools import Tool, ToolExecutor


def answer(text="", calls=None):
    return LLMResponse(content=text, model="fixture", usage={}, finish_reason="tool_use" if calls else "stop",
                       stop_reason="tool_use" if calls else "stop", tool_calls=calls)


def call(name, number):
    return {"id": f"call_{number}", "name": name, "input": {}}


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        return self.responses[len(self.calls) - 1]

    async def chat_stream(self, messages, **kwargs):
        response = await self.chat(messages, **kwargs)
        if response.content:
            middle = len(response.content) // 2
            yield response.content[:middle]
            yield response.content[middle:]
        if response.tool_calls:
            yield {"stop_reason": "tool_use", "tool_calls": response.tool_calls}


def collect(agent, stream):
    async def run():
        return "".join([piece async for piece in agent.chat("Check evidence", stream=stream)])
    return asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
def test_two_sequential_tool_rounds_and_one_final_answer(stream):
    order = []
    async def first():
        order.append("first")
        return {"symbol": "TEST"}
    async def second():
        order.append("second")
        return {"price": 10}
    provider = ScriptedProvider([answer("Reading symbols", [call("first", 1)]),
                                 answer("Reading price", [call("second", 2)]), answer("Verified result")])
    agent = TraderAgent(provider, ToolExecutor([Tool("first", "first", [], first), Tool("second", "second", [], second)]))
    text = collect(agent, stream)
    assert order == ["first", "second"]
    assert len(provider.calls) == 3
    assert text.count("Verified result") == 1
    assert json.loads(provider.calls[2][-1]["content"][0]["content"]) == {"price": 10}
    assert agent.conversation.messages[-1].content == text
    if not stream:
        assert text == "Verified result"


def test_stream_has_no_duplicate_separator_after_silent_tool_round():
    async def fixture():
        return {"ok": True}
    provider = ScriptedProvider([answer("First", [call("fixture", 1)]), answer("", [call("fixture", 2)]), answer("Final")])
    agent = TraderAgent(provider, ToolExecutor([Tool("fixture", "fixture", [], fixture)]))
    assert collect(agent, True) == "First\n\nFinal"


@pytest.mark.parametrize("stream", [False, True])
def test_call_budget_stops_before_uninterpreted_tool_execution(stream):
    calls = []
    async def fixture():
        calls.append(1)
        return {"ok": True}
    provider = ScriptedProvider([answer(calls=[call("fixture", number)]) for number in range(1, 8)])
    agent = TraderAgent(provider, ToolExecutor([Tool("fixture", "fixture", [], fixture)]))
    agent.max_tool_iterations = 3
    text = collect(agent, stream)
    assert "上限" in text and "尚未完成" in text
    assert len(provider.calls) == 3
    assert len(calls) == 2


def test_unknown_tool_returns_failure_to_model_without_execution():
    provider = ScriptedProvider([answer(calls=[call("confirm_ledger", 1)]), answer("This action is unavailable")])
    agent = TraderAgent(provider, ToolExecutor([]))
    assert collect(agent, False) == "This action is unavailable"
    result = provider.calls[1][-1]["content"][0]
    assert result["is_error"] is True
    assert "未知工具" in json.loads(result["content"])["error"]


def test_tool_timeout_returns_failure_and_can_be_explained():
    async def slow():
        await asyncio.sleep(60)
    provider = ScriptedProvider([answer(calls=[call("slow", 1)]), answer("Data unavailable")])
    agent = TraderAgent(provider, ToolExecutor([Tool("slow", "slow", [], slow)]))
    agent.tool_timeout_seconds = .01
    assert collect(agent, False) == "Data unavailable"
    assert "超时" in provider.calls[1][-1]["content"][0]["content"]


def test_cancellation_stops_tool_and_does_not_start_another_model_round():
    async def check():
        started, stopped = asyncio.Event(), asyncio.Event()
        async def slow():
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()
        provider = ScriptedProvider([answer(calls=[call("slow", 1)]), answer("never reached")])
        agent = TraderAgent(provider, ToolExecutor([Tool("slow", "slow", [], slow)]))
        async def consume():
            return [part async for part in agent.chat("check", stream=True)]
        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        assert len(provider.calls) == 1
        assert len(agent.conversation.messages) == 1
    asyncio.run(check())


def test_duplicate_calls_rejected_before_any_tool_runs():
    invoked = []
    async def fixture():
        invoked.append(True)
    provider = ScriptedProvider([answer(calls=[call("fixture", 1), call("fixture", 1)])])
    agent = TraderAgent(provider, ToolExecutor([Tool("fixture", "fixture", [], fixture)]))
    with pytest.raises(RuntimeError, match="重复"):
        collect(agent, False)
    assert not invoked
