"""Backtest reflection: provider lookup, response handling and rule-based fallback (no network)."""
import asyncio
import json
import logging

import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from backtesting.reflection.llm_reflector import BacktestReflector
from backtesting.strategies.base import Strategy
from core.llm.base import LLMResponse


class BuyOnceStrategy(Strategy):
    name = "BuyOnce"

    def on_init(self):
        self.did_buy = False

    def on_bar(self):
        if not self.did_buy:
            self.buy(pct_cash=0.5)
            self.did_buy = True


@pytest.fixture
def result():
    dates = pd.date_range("2024-01-01", periods=8, freq="B")
    df = pd.DataFrame(
        {
            "open": [10, 11, 12, 13, 14, 15, 16, 17],
            "high": [11, 12, 13, 14, 15, 16, 17, 18],
            "low": [9, 10, 11, 12, 13, 14, 15, 16],
            "close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5],
            "volume": [1_000_000] * 8,
            "amount": [10_000_000] * 8,
        },
        index=dates,
    )
    df.index.name = "date"
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("TEST", "", "", df=df, warmup=1)
    engine.add_strategy(BuyOnceStrategy)
    return engine.run()


class FakeProvider:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.reply


class FakeConfig:
    def __init__(self, api_group, model="fake-model"):
        self._api_group = api_group
        self._model = model

    @property
    def llm_config(self):
        return {"current_model": self._model, "temperature": 0.0, "timeout": 5}

    def get_current_api_group(self):
        return dict(self._api_group)

    def get_current_model(self):
        return self._model


def use_config(monkeypatch, api_group, model="fake-model"):
    import config.settings as settings
    monkeypatch.setattr(settings, "get_config", lambda *args, **kwargs: FakeConfig(api_group, model))


def reflect(reflector, result):
    return asyncio.run(reflector.reflect(result))


def test_llm_response_text_becomes_the_analysis(result):
    reply = LLMResponse(content="模型分析: 趋势行情中表现稳定", model="fake-model",
                        usage={"prompt_tokens": 1, "completion_tokens": 1}, finish_reason="stop")
    provider = FakeProvider(reply=reply)

    reflection = reflect(BacktestReflector(llm_provider=provider, history_store=object()), result)

    assert len(provider.calls) == 1
    assert reflection["analysis"] == "模型分析: 趋势行情中表现稳定"
    assert reflection["source"] == "llm"


def test_provider_lookup_builds_the_configured_provider(monkeypatch):
    from providers.llm import ClaudeProvider, OpenAIProvider

    use_config(monkeypatch, {"api_key": "test-key", "base_url": "http://127.0.0.1:9/v1",
                             "provider_type": "openai", "headers": None})
    provider = BacktestReflector()._get_llm_provider()
    assert isinstance(provider, OpenAIProvider)
    assert provider.config.model == "fake-model"
    assert provider.config.api_key == "test-key"

    use_config(monkeypatch, {"api_key": "test-key", "base_url": "http://127.0.0.1:9/v1",
                             "provider_type": "claude", "headers": None})
    assert isinstance(BacktestReflector()._get_llm_provider(), ClaudeProvider)


@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1:11434/v1",
    "http://localhost:8000/v1",
    "http://192.168.1.20:8000/v1",
])
def test_keyless_openai_compatible_endpoint_counts_as_configured(monkeypatch, result, base_url):
    # A local server (Ollama, vLLM, LiteLLM) needs no key; the chat service uses it
    # as is and OpenAIProvider then sends no Authorization header.
    from providers.llm import OpenAIProvider

    use_config(monkeypatch, {"api_key": "", "base_url": base_url, "provider_type": "openai", "headers": None},
               model="llama3")
    provider = BacktestReflector()._get_llm_provider()

    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url == base_url
    assert "Authorization" not in provider._get_headers()

    async def local_reply(self, messages, **kwargs):
        return LLMResponse(content="本地模型分析", model="llama3", usage={}, finish_reason="stop")

    monkeypatch.setattr(OpenAIProvider, "chat", local_reply)
    reflection = reflect(BacktestReflector(llm_provider=provider, history_store=object()), result)
    assert reflection["source"] == "llm"
    assert reflection["analysis"] == "本地模型分析"


# The keyless 127.0.0.1 group this test used before is a configured local endpoint
# (see the test above); a group is unconfigured when it has no endpoint, or points at
# a hosted API that cannot be called without a key.
@pytest.mark.parametrize("api_group", [
    {"api_key": "", "base_url": "https://api.openai.com/v1", "provider_type": "openai", "headers": None},
    {"api_key": "", "base_url": "https://api.anthropic.com/v1", "provider_type": "claude", "headers": None},
    {"api_key": "", "base_url": "", "provider_type": "openai", "headers": None},
    {},
], ids=["openai-official", "anthropic-official", "no-endpoint", "no-group"])
def test_without_a_configured_llm_the_rule_based_text_is_labelled(monkeypatch, result, api_group):
    use_config(monkeypatch, api_group)
    reflector = BacktestReflector(history_store=object())

    assert reflector._get_llm_provider() is None
    reflection = reflect(reflector, result)

    assert reflection["source"] == "rules"
    assert "规则分析" in reflection["analysis"]
    assert "未使用 LLM" in reflection["analysis"]
    assert "整体评级" in reflection["analysis"]


@pytest.mark.parametrize("provider", [
    FakeProvider(error=RuntimeError("provider unavailable")),
    FakeProvider(reply=LLMResponse(content="", model="fake-model", usage={}, finish_reason="stop")),
])
def test_failed_or_empty_llm_call_falls_back_to_rules(result, provider):
    reflection = reflect(BacktestReflector(llm_provider=provider, history_store=object()), result)

    assert reflection["source"] == "rules"
    assert "未使用 LLM" in reflection["analysis"]


# A configured key with a trailing newline makes httpx/h11 raise an error whose
# text is the whole Authorization header; provider errors must never be logged.
FAKE_KEY = "sk-FAKE-REVIEW-KEY"


class FakeTimeout(Exception):
    pass


def leaky_errors():
    header_error = RuntimeError(f"Illegal header value b'Bearer {FAKE_KEY}\\n'")
    wrapped = RuntimeError(f"OpenAI 网络请求失败：Illegal header value b'Bearer {FAKE_KEY}\\n'")
    wrapped.__cause__ = ValueError(f"Bearer {FAKE_KEY}")
    timeout = RuntimeError(f"timed out for key {FAKE_KEY}")
    timeout.__cause__ = FakeTimeout(FAKE_KEY)
    return [header_error, wrapped, timeout]


@pytest.mark.parametrize("error", leaky_errors(), ids=["header", "wrapped", "timeout"])
def test_provider_error_text_is_not_logged_or_returned(result, caplog, error):
    provider = FakeProvider(error=error)

    with caplog.at_level(logging.DEBUG):
        reflection = reflect(BacktestReflector(llm_provider=provider, history_store=object()), result)

    assert reflection["source"] == "rules"
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in json.dumps(reflection, ensure_ascii=False, default=str)
    # The class and a coarse category are still logged for diagnosis.
    assert "RuntimeError" in caplog.text


def test_timeout_is_logged_as_a_timeout_category(result, caplog):
    error = leaky_errors()[2]

    with caplog.at_level(logging.DEBUG):
        reflect(BacktestReflector(llm_provider=FakeProvider(error=error), history_store=object()), result)

    assert "timeout" in caplog.text
    assert FAKE_KEY not in caplog.text


def test_provider_construction_error_text_is_not_logged(monkeypatch, caplog):
    import providers.llm as llm_module

    def leaky_factory(*args, **kwargs):
        raise ValueError(f"Illegal header value b'Bearer {FAKE_KEY}\\n'")

    use_config(monkeypatch, {"api_key": f"{FAKE_KEY}\n", "base_url": "http://127.0.0.1:9/v1",
                             "provider_type": "openai", "headers": None})
    monkeypatch.setattr(llm_module, "create_llm_provider", leaky_factory)

    with caplog.at_level(logging.DEBUG):
        assert BacktestReflector()._get_llm_provider() is None

    assert FAKE_KEY not in caplog.text
    assert "ValueError" in caplog.text


def test_reflection_does_not_claim_to_have_saved_anything(monkeypatch, result):
    # The API stores the reflection with the run (history.save_run); the reflector
    # itself writes nothing, so it must not report saved_to_memory=True.
    import backtesting.history as history_module

    def no_store(*args, **kwargs):
        raise AssertionError("the reflector must not open a history store")

    monkeypatch.setattr(history_module, "BacktestHistoryStore", no_store)
    reply = LLMResponse(content="模型分析", model="fake-model", usage={}, finish_reason="stop")

    reflection = reflect(BacktestReflector(llm_provider=FakeProvider(reply=reply)), result)

    assert reflection["source"] == "llm"
    assert "saved_to_memory" not in reflection


# ------------------------------------------------------------------ #
#  "AI 解读" works from the run's facts, sees its round trips, never tunes
# ------------------------------------------------------------------ #

def fact_payload(trips=3):
    return {
        "run_id": "r1",
        "headline_zh": "2023-11-17 至 2026-11-16（3 年），这个方法收益 +18.2%（年化 5.7%）。",
        "facts": {"ticker": "SHA:600519", "strategy": {"name": "均线趋势", "params": {"period": 20}},
                  "same_stock_return": 0.3, "total_return": 0.182},
        "metrics": {"total_return": 0.182, "annualized_return": 0.057, "max_drawdown": -0.25, "win_rate": 0.3,
                    "closed_trades": trips, "sharpe_ratio": None, "profit_factor": 0.8},
        "caveats": [{"code": "few_trades", "level": "warning", "text": "只有 3 笔完整交易"}],
        "round_trips": [{"entry_date": f"2024-01-{i + 1:02d}", "exit_date": f"2024-02-{i + 1:02d}",
                         "pnl": (i - 1) * 100.0} for i in range(trips)],
    }


def test_the_prompt_holds_the_facts_and_round_trips_and_forbids_tuning():
    reply = LLMResponse(content="解读", model="fake-model", usage={}, finish_reason="stop")
    provider = FakeProvider(reply=reply)

    reflection = asyncio.run(BacktestReflector(llm_provider=provider).reflect_facts(fact_payload()))

    [[message]] = provider.calls
    prompt = message["content"]
    assert "2023-11-17 至 2026-11-16" in prompt and "2024-01-02" in prompt and "只有 3 笔完整交易" in prompt
    assert "不要建议修改参数" in prompt and "具体参数调整" not in prompt
    assert reflection["source"] == "llm" and reflection["round_trips_seen"] == 3
    assert reflection["title"] == "AI 解读"


def test_many_round_trips_are_cut_to_the_largest():
    from backtesting.reflection.llm_reflector import MAX_PROMPT_TRIPS, build_prompt

    prompt = build_prompt(fact_payload(trips=90))
    assert f"共 90 笔，这里列出盈亏绝对值最大的 {MAX_PROMPT_TRIPS} 笔" in prompt


def test_rules_only_when_the_llm_is_switched_off(monkeypatch):
    def no_config(*args, **kwargs):
        raise AssertionError("the configuration must not be read")

    import config.settings as settings
    monkeypatch.setattr(settings, "get_config", no_config)

    reflection = asyncio.run(BacktestReflector(use_llm=False).reflect_facts(fact_payload()))

    assert reflection["source"] == "rules" and "未使用 LLM" in reflection["analysis"]
    assert "整体评级" in reflection["analysis"]
    # Facts only: no advice to change entry conditions or stops on the same history.
    for text in [reflection["analysis"], *reflection["lessons"]]:
        assert "优化" not in text and "调整" not in text and "止损机制" not in text
    assert "最大回撤 -25.0%" in reflection["analysis"]
