"""Agent tools contributed by feature packages reach the agent in its existing tool format."""
import asyncio
import json
import logging
import sys

import pytest

from core import agent_tools, proposals
from core.tools import Tool, ToolExecutor, ToolParameter, ToolParameterType


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(agent_tools, "_factories", [])
    monkeypatch.setattr(agent_tools, "__path__", [])  # no feature modules: each test brings its own


def echo_tool(name="echo_text", prefix=""):
    """The tiny example tool: returns its argument."""
    async def echo(text: str, repeat: float = 1) -> dict:
        return {"text": prefix + text * int(repeat)}

    return Tool(name=name, description="Repeat the given text",
                parameters=[ToolParameter("text", ToolParameterType.STRING, "Text to repeat"),
                            ToolParameter("repeat", ToolParameterType.NUMBER, "How many times", required=False)],
                function=echo)


def run(coroutine):
    return asyncio.run(coroutine)


def test_registered_tool_runs_through_the_existing_executor():
    seen = []

    @agent_tools.register
    def factory(runtime):
        seen.append(runtime)
        return echo_tool(prefix=runtime["prefix"])

    runtime = {"prefix": "> "}
    tools = agent_tools.build_all(runtime)
    assert [tool.name for tool in tools] == ["echo_text"]
    assert seen == [runtime], "the factory receives the runtime given to build_all"
    assert callable(factory), "register returns the factory so it can decorate"

    executor = ToolExecutor(tools)
    assert executor.get_tool_schemas() == [{
        "name": "echo_text", "description": "Repeat the given text",
        "input_schema": {"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string", "description": "Text to repeat"},
            "repeat": {"type": "number", "description": "How many times"}}}}]
    assert run(executor.execute_tool("echo_text", {"text": "ab", "repeat": 2})) == {"text": "> abab"}


def test_factories_may_return_one_tool_several_or_none():
    agent_tools.register(lambda runtime: echo_tool("one"))
    agent_tools.register(lambda runtime: (echo_tool(name) for name in ("two", "three")))
    agent_tools.register(lambda runtime: None)
    agent_tools.register(lambda runtime: [])
    assert [tool.name for tool in agent_tools.build_all()] == ["one", "two", "three"]


def test_same_factory_registered_twice_builds_once():
    def factory(runtime):
        return echo_tool()

    agent_tools.register(factory)
    agent_tools.register(factory)
    assert [tool.name for tool in agent_tools.build_all()] == ["echo_text"]
    with pytest.raises(TypeError):
        agent_tools.register("not callable")


def test_tools_are_rebuilt_for_every_runtime():
    agent_tools.register(lambda runtime: echo_tool(prefix=runtime))
    first, second = agent_tools.build_all("a:"), agent_tools.build_all("b:")
    assert run(ToolExecutor(first).execute_tool("echo_text", {"text": "x"})) == {"text": "a:x"}
    assert run(ToolExecutor(second).execute_tool("echo_text", {"text": "x"})) == {"text": "b:x"}


def test_broken_factory_does_not_take_the_others_down(caplog):
    def broken(runtime):
        raise RuntimeError("provider missing 76543.21")

    agent_tools.register(lambda runtime: echo_tool("before"))
    agent_tools.register(broken)
    agent_tools.register(lambda runtime: 5)  # not a tool and not iterable
    agent_tools.register(lambda runtime: echo_tool("after"))
    with caplog.at_level(logging.WARNING, logger="core.agent_tools"):
        assert [tool.name for tool in agent_tools.build_all()] == ["before", "after"]
    assert "broken" in caplog.text and "RuntimeError" in caplog.text and "76543.21" not in caplog.text


def test_tools_the_executor_could_not_run_are_skipped(caplog):
    def sync_function(text: str) -> dict:
        return {"text": text}

    good = echo_tool("good")
    agent_tools.register(lambda runtime: [
        Tool("sync_tool", "Not awaitable", [], sync_function),
        {"name": "dict_tool"},
        Tool("bad name!", "Invalid name", [], good.function),
        Tool("no_description", "", [], good.function),
        Tool("raw_parameters", "Parameters in the wrong shape", [{"name": "text"}], good.function),
        good,
    ])
    with caplog.at_level(logging.WARNING, logger="core.agent_tools"):
        assert agent_tools.build_all() == [good]
    assert "sync_tool function must be async" in caplog.text
    assert len(caplog.records) == 5


def test_duplicate_names_never_replace_an_existing_tool(caplog):
    agent_tools.register(lambda runtime: echo_tool("echo_text", prefix="first "))
    agent_tools.register(lambda runtime: [echo_tool("echo_text", prefix="second "), echo_tool("other")])
    agent_tools.register(lambda runtime: echo_tool("propose_ledger_events", prefix="impostor "))
    with caplog.at_level(logging.WARNING, logger="core.agent_tools"):
        tools = agent_tools.build_all(exclude=["propose_ledger_events", "get_portfolio"])
    assert [tool.name for tool in tools] == ["echo_text", "other"]
    assert run(ToolExecutor(tools).execute_tool("echo_text", {"text": "x"})) == {"text": "first x"}
    assert "echo_text is already taken" in caplog.text and "propose_ledger_events is already taken" in caplog.text


def test_modules_in_the_package_are_discovered(tmp_path, monkeypatch, caplog):
    (tmp_path / "sample_tools.py").write_text(
        "from core import agent_tools\n"
        "from core.tools import Tool\n"
        "@agent_tools.register\n"
        "def sample(runtime):\n"
        "    async def sample_tool() -> dict:\n"
        "        return {'from': 'sample module'}\n"
        "    return Tool('sample_tool', 'A tool defined in a package module', [], sample_tool)\n")
    (tmp_path / "broken_tools.py").write_text("raise RuntimeError('this module cannot be imported')\n")
    monkeypatch.setattr(agent_tools, "__path__", [str(tmp_path)])
    for name in ("sample_tools", "broken_tools"):
        monkeypatch.delitem(sys.modules, f"core.agent_tools.{name}", raising=False)
    try:
        with caplog.at_level(logging.WARNING, logger="core.agent_tools"):
            tools = agent_tools.build_all()
            assert [tool.name for tool in agent_tools.build_all()] == ["sample_tool"], "discovery is repeatable"
        assert [tool.name for tool in tools] == ["sample_tool"]
        assert run(ToolExecutor(tools).execute_tool("sample_tool", {})) == {"from": "sample module"}
        assert "broken_tools" in caplog.text
    finally:
        for name in ("sample_tools", "broken_tools"):
            sys.modules.pop(f"core.agent_tools.{name}", None)
            if hasattr(agent_tools, name):
                delattr(agent_tools, name)


def test_shipped_package_modules_import_cleanly(monkeypatch, caplog):
    monkeypatch.undo()  # the real package path and the real registry
    with caplog.at_level(logging.WARNING, logger="core.agent_tools"):
        tools = agent_tools.build_all()
    assert caplog.records == [], "every module shipped in core/agent_tools must import and build"
    assert len({tool.name for tool in tools}) == len(tools)


def initialised_service(monkeypatch):
    """A real AgentService.initialize() with fake configuration and providers."""
    from types import SimpleNamespace
    from backend.services import agent_service as module

    async def fake_llm(self):
        self.llm_provider = SimpleNamespace(display_name="fake")

    def fake(attribute):
        return lambda self: setattr(self, attribute, SimpleNamespace(name=attribute))

    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace(llm_config={}))
    monkeypatch.setattr(module.AgentService, "_init_llm", fake_llm)
    for step, attribute in (("_init_market_provider", "market_provider"), ("_init_portfolio_provider", "portfolio_provider"),
                            ("_init_news_provider", "news_provider"), ("_init_memory_manager", "memory_manager"),
                            ("_init_extractors", "memory_extractor")):
        monkeypatch.setattr(module.AgentService, step, fake(attribute))
    service = module.AgentService()
    asyncio.run(service.initialize())
    return service


def test_registered_tools_reach_the_agent_of_the_running_service(monkeypatch):
    seen = []

    @agent_tools.register
    def review_probe(runtime):
        seen.append(runtime)
        return [echo_tool("review_probe"), echo_tool("get_portfolio", prefix="impostor ")]

    service = initialised_service(monkeypatch)
    tools = service.agent.tool_executor.tools
    assert "review_probe" in tools and "propose_ledger_events" in tools
    assert seen == [service], "factories receive the AgentService as runtime"
    assert run(service.agent.tool_executor.execute_tool("review_probe", {"text": "x"})) == {"text": "x"}
    assert tools["get_portfolio"].function is not review_probe(service)[1].function, "a built-in is never replaced"
    assert len(tools) == 8


@pytest.mark.parametrize("stream", [False, True])
def test_model_can_only_propose_through_a_registered_tool(tmp_path, monkeypatch, stream):
    """Registry tool -> real agent loop -> stored proposal; only confirm() changes anything."""
    from agents.trader.agent import TraderAgent
    from tests.test_trader_multiround import ScriptedProvider, answer

    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    monkeypatch.setattr(proposals, "_kinds", {})
    labels = []
    proposals.register_kind(
        "label", lambda payload: {"label": str(payload["label"]).strip()},
        lambda payload, proposal: labels.append((payload["label"], proposal["source"])),
        lambda payload: f"添加标签：{payload['label']}")

    @agent_tools.register
    def label_tools(runtime):
        async def propose_label(label: str) -> dict:
            proposal = proposals.propose("label", {"label": label}, "agent:chat")
            return {"proposal_id": proposal["id"], "status": "awaiting_user_confirmation",
                    "summary": proposal["summary"]}

        return Tool("propose_label", "Prepare (never apply) a new label",
                    [ToolParameter("label", ToolParameterType.STRING, "Label text")], propose_label)

    call = {"id": "call_1", "name": "propose_label", "input": {"label": " Example "}}
    model = ScriptedProvider([answer(calls=[call]), answer("方案待确认。")])
    agent = TraderAgent(model, ToolExecutor(agent_tools.build_all()))

    async def chat():
        return "".join([part async for part in agent.chat("Add a label", stream=stream)])

    asyncio.run(chat())
    result = json.loads(model.calls[1][-1]["content"][0]["content"])
    assert result["status"] == "awaiting_user_confirmation" and result["summary"] == "添加标签：Example"
    [pending] = proposals.list()
    assert pending["id"] == result["proposal_id"] and pending["source"] == "agent:chat"
    assert labels == [], "the model's tool call must not apply anything"
    proposals.confirm(pending["id"])
    assert labels == [("Example", "agent:chat")]
