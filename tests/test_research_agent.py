import asyncio
import json
import os
from pathlib import Path
import sys

import httpx
import pytest

from core.llm.base import LLMConfig, LLMResponse
from core.research_agent import ResearchAgentError, plan_research, review_research, validate_experiment_spec
from providers.llm.openai import OpenAIProvider, _messages_for_openai
from providers.llm import research as transport


class FakeProvider:
    def __init__(self, output):
        self.output = output
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return LLMResponse(content=self.output if isinstance(self.output, str) else json.dumps(self.output),
                           model="test-model", usage={"prompt_tokens": 20, "completion_tokens": 10}, finish_reason="stop")


@pytest.fixture
def context():
    return {"templates": [{"id": "momentum"}, {"id": "mean_reversion"}], "dataset": {"id": "ds_fixture"},
            "initial_spec": {"template_id": "momentum", "params": {"horizon": 5, "train_ratio": .6,
                             "validation_ratio": .2, "fee_bps": 10, "slippage_bps": 5, "seed": 42,
                             "market": "us", "adjustment": "qfq"}}}


def proposal(params=None):
    return {"question": "Does momentum predict returns?", "hypotheses": ["Momentum has stable validation improvement."],
            "experiments": [{"template_id": "momentum", "params": params or {"lookback": 20}}],
            "limitations": ["Synthetic fixture cannot establish a trading edge."]}


def test_plan_one_call_fixes_protocol_and_dataset(context):
    fake = FakeProvider(proposal())
    plan = asyncio.run(plan_research("Evaluate momentum", context, fake, {"max_experiments": 2}))
    assert len(fake.calls) == plan["model_calls"] == 1
    assert plan["experiments"][0]["dataset_id"] == "ds_fixture"
    assert plan["experiments"][0]["params"]["horizon"] == 5
    assert len(plan["plan_hash"]) == 64
    assert fake.calls[0][1]["output_schema"]["properties"]["experiments"]["maxItems"] == 2


@pytest.mark.parametrize("spec", [
    {"template_id": "shell", "params": {}},
    {"template_id": "momentum", "params": {"code": "import os"}},
    {"template_id": "momentum", "params": {"lookback": float("inf")}},
    {"template_id": "momentum", "params": {"lookback": True}},
    {"template_id": "momentum", "params": {"lookback": 121}},
    {"template_id": "momentum", "params": {"horizon": 6}},
    {"template_id": "momentum", "params": {"fee_bps": 0}},
    {"template_id": "momentum", "params": {}, "dataset_id": "different"},
    {"template_id": "momentum", "params": {}, "command": "touch something"},
])
def test_reject_unsafe_or_protocol_changing_specs(context, spec):
    with pytest.raises(ResearchAgentError):
        validate_experiment_spec(spec, context)


def test_zero_budget_makes_no_call(context):
    fake = FakeProvider(proposal())
    with pytest.raises(ResearchAgentError):
        asyncio.run(plan_research("Evaluate momentum", context, fake, {"max_calls": 0}))
    assert not fake.calls


@pytest.mark.parametrize("output", ["not JSON", "{\"question\":NaN}", {"question": "incomplete"}])
def test_bad_model_output_has_no_fallback_success(context, output):
    fake = FakeProvider(output)
    with pytest.raises(ResearchAgentError):
        asyncio.run(plan_research("Evaluate momentum", context, fake))
    assert len(fake.calls) == 1


def test_duplicate_or_excess_experiments_rejected(context):
    draft = proposal()
    draft["experiments"] *= 2
    with pytest.raises(ResearchAgentError, match="duplicate"):
        asyncio.run(plan_research("Evaluate momentum", context, FakeProvider(draft)))
    with pytest.raises(ResearchAgentError, match="count"):
        asyncio.run(plan_research("Evaluate momentum", context, FakeProvider(draft), {"max_experiments": 1}))


def review(spec=None):
    return {"conclusion": "Evidence is inconclusive.", "evidence": [{"experiment_id": "run_one", "observation": "Validation improvement is zero."}],
            "limitations": ["Insufficient independent samples."], "revised_spec": spec}


def result_context(context, phase="validation"):
    return {**context, "phase": phase, "experiments": [{"id": "run_one", "validation": {"improvement_pct": 0}}]}


def test_review_validates_evidence_and_revision(context):
    fake = FakeProvider(review({"template_id": "mean_reversion", "params": {"lookback": 30}}))
    result = asyncio.run(review_research("Evaluate returns", result_context(context), fake))
    assert result["revised_spec"]["params"]["fee_bps"] == 10
    assert result["model_calls"] == 1
    bad = review()
    bad["evidence"][0]["experiment_id"] = "made_up"
    with pytest.raises(ResearchAgentError, match="unknown experiment"):
        asyncio.run(review_research("Evaluate returns", result_context(context), FakeProvider(bad)))


def test_test_metrics_forbidden_during_planning_and_validation(context):
    context["feedback"] = {"metrics": {"test": {"return": .2}}}
    fake = FakeProvider(proposal())
    with pytest.raises(ResearchAgentError, match="Test results"):
        asyncio.run(plan_research("Evaluate returns", context, fake))
    with pytest.raises(ResearchAgentError, match="Test results"):
        asyncio.run(review_research("Evaluate returns", result_context(context), fake))
    assert not fake.calls


def test_final_review_allows_test_explanation_but_rejects_revision(context):
    results = result_context(context, phase="final")
    results["experiments"][0]["test"] = {"return": 0}
    completed = asyncio.run(review_research("Evaluate returns", results, FakeProvider(review())))
    assert completed["revised_spec"] is None
    with pytest.raises(ResearchAgentError, match="cannot revise"):
        asyncio.run(review_research("Evaluate returns", results, FakeProvider(review({"template_id": "momentum", "params": {}}))))


def test_custom_template_parameters_are_extensible(context):
    context["templates"] = [{"id": "custom_statistic", "parameters": {"bins": {"type": "integer", "minimum": 2, "maximum": 10}}}]
    item = validate_experiment_spec({"template_id": "custom_statistic", "params": {"bins": 4}}, context)
    assert item["params"] == {"bins": 4}


def test_openai_tool_request_response_round_trip(monkeypatch):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "fixture", "choices": [{"finish_reason": "tool_calls", "message": {
            "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "quote", "arguments": '{"symbol":"TEST"}'}}]}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    provider = OpenAIProvider(LLMConfig(api_key="fixture", model="fixture"))
    response = asyncio.run(provider.chat([{"role": "user", "content": "quote"}], tools=[{
        "name": "quote", "description": "read quote", "input_schema": {"type": "object", "properties": {}}}]))
    assert requests[0]["tools"][0]["function"]["name"] == "quote"
    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0]["input"] == {"symbol": "TEST"}
    messages = _messages_for_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "quote", "input": {"symbol": "TEST"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '{"price":10}'}]}])
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == '{"symbol": "TEST"}'
    assert messages[1] == {"role": "tool", "tool_call_id": "call_1", "content": '{"price":10}'}


def test_openai_stream_collects_fragmented_tool_arguments(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "quote", "arguments": '{"sym'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'bol":"TEST"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    original = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, text="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    provider = OpenAIProvider(LLMConfig(api_key="fixture", model="fixture"))
    async def consume():
        return [chunk async for chunk in provider.chat_stream([{"role": "user", "content": "quote"}])]
    output = asyncio.run(consume())
    assert output[-1]["tool_calls"][0]["input"] == {"symbol": "TEST"}


def test_factory_separates_api_and_official_cli_credentials(monkeypatch):
    monkeypatch.setenv("TEST_RESEARCH_KEY", "test-secret")
    provider = transport.make_research_provider({"provider": "openai", "model": "custom-model", "api_key_env": "TEST_RESEARCH_KEY"})
    assert provider.config.api_key == "test-secret"
    assert transport.make_research_provider({"kind": "codex_cli", "model": ""}).config.api_key == ""
    with pytest.raises(ValueError, match="never a credential"):
        transport.make_research_provider({"kind": "openai", "model": "test", "api_key": "literal"})


@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1"])
def test_keyless_local_models(url):
    provider = transport.make_research_provider({"kind": "openai", "model": "local-model", "base_url": url})
    assert provider.config.api_key == ""
    assert "Authorization" not in provider.delegate._get_headers()


@pytest.mark.parametrize("url", ["https://example.invalid/v1", "http://localhost.attacker.invalid/v1", "http://user:password@localhost/v1"])
def test_external_models_require_explicit_credential_env(url):
    with pytest.raises(ValueError):
        transport.make_research_provider({"kind": "openai", "model": "model", "base_url": url})


def test_cli_env_drops_credentials_but_preserves_login_location(monkeypatch):
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CODEX_ACCESS_TOKEN", "UNRELATED_SECRET"):
        monkeypatch.setenv(key, "do-not-inherit")
    monkeypatch.setenv("CODEX_HOME", "/fixture/official-cli")
    env = transport._cli_env()
    assert env["CODEX_HOME"] == "/fixture/official-cli"
    assert "do-not-inherit" not in env.values()


def test_cli_commands_have_no_execution_tools_or_bypass(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "_cli_binary", lambda kind: kind)
    schema = {"type": "object", "properties": {}}
    codex = transport.make_research_provider({"kind": "codex_cli", "model": ""})._command(tmp_path, schema)
    claude = transport.make_research_provider({"kind": "claude_cli", "model": ""})._command(tmp_path, schema)
    assert "-m" not in codex and "--model" not in claude
    assert 'features.shell_tool=false' in codex and "read-only" in codex
    assert claude[claude.index("--tools") + 1] == ""
    assert "--safe-mode" in claude and "--bare" not in claude
    assert not any("bypass" in token for token in codex + claude)


def test_cli_probe_discards_account_information(monkeypatch):
    async def fake(command, **kwargs):
        if "--version" in command:
            return 0, "Claude Code 2.1.251", ""
        return 0, json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "private@example.invalid"}), ""
    monkeypatch.setattr(transport, "_cli_binary", lambda kind: kind)
    monkeypatch.setattr(transport, "_run_process", fake)
    probe = asyncio.run(transport.probe_research_provider({"kind": "claude_cli", "model": ""}))
    assert probe["available"] is True
    assert "private" not in json.dumps(probe)


def test_cli_reads_only_official_final_result(monkeypatch):
    monkeypatch.setattr(transport, "_cli_binary", lambda kind: kind)
    async def fake(command, **kwargs):
        assert kwargs["cwd"] != os.getcwd()
        Path(command[command.index("-o") + 1]).write_text('{"ok":true}')
        return 0, '{"type":"turn.completed","usage":{"input_tokens":20,"output_tokens":5}}', "private diagnostic"
    monkeypatch.setattr(transport, "_run_process", fake)
    provider = transport.make_research_provider({"kind": "codex_cli", "model": "test"})
    response = asyncio.run(provider.chat([{"role": "user", "content": "fixture"}]))
    assert json.loads(response.content) == {"ok": True}
    assert response.usage == {"prompt_tokens": 20, "completion_tokens": 5}


def test_cli_failure_does_not_expose_diagnostics(monkeypatch):
    monkeypatch.setattr(transport, "_cli_binary", lambda kind: kind)
    async def fake(command, **kwargs):
        return 1, "private@example.invalid", "/private/auth.json bearer secret"
    monkeypatch.setattr(transport, "_run_process", fake)
    provider = transport.make_research_provider({"kind": "claude_cli", "model": "test"})
    with pytest.raises(transport.ResearchProviderError) as err:
        asyncio.run(provider.chat([{"role": "user", "content": "fixture"}]))
    assert "private" not in str(err.value) and "secret" not in str(err.value)


def test_process_runner_timeout_and_output_limit():
    async def check():
        with pytest.raises(asyncio.TimeoutError):
            await transport._run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=.1)
        with pytest.raises(transport.ResearchProviderError, match="output"):
            await transport._run_process([sys.executable, "-c", "print('x'*20000)"], output_limit=1000, timeout=2)
        task = asyncio.create_task(transport._run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=40))
        await asyncio.sleep(.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(check())


def test_api_error_does_not_expose_response_body():
    class FailingProvider(FakeProvider):
        name = "API"
        config = LLMConfig(api_key="", model="fixture")
        async def chat(self, *args, **kwargs):
            raise RuntimeError("Authorization=secret /private/path")
    provider = transport.ResearchAPIProvider(FailingProvider(None))
    with pytest.raises(transport.ResearchProviderError) as err:
        asyncio.run(provider.chat([]))
    assert "secret" not in str(err.value) and "/private" not in str(err.value)


def test_connection_timeout_cannot_be_overridden_by_job_budget(context):
    fake = FakeProvider(proposal())
    fake.config = LLMConfig(api_key="", model="fixture", timeout=10)
    asyncio.run(plan_research("Evaluate momentum", context, fake, {"timeout_seconds": 120}))
    assert fake.calls[0][1]["timeout"] == 10


def test_review_base_spec_also_freezes_evaluation_protocol(context):
    context["base_spec"] = context.pop("initial_spec")
    fake = FakeProvider(review({"template_id": "momentum", "params": {"horizon": 6}}))
    with pytest.raises(ResearchAgentError, match="fixed evaluation protocol"):
        asyncio.run(review_research("Evaluate returns", result_context(context), fake))
