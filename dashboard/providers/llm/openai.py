"""OpenAI-compatible chat transport with provider-neutral tool round trips."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator
import httpx

from core.llm.base import LLMProvider, LLMResponse
from .error_utils import format_httpx_error


def _messages_for_openai(messages: list[dict]) -> list[dict]:
    converted = []
    for original in messages:
        message = dict(original)
        content = message.get("content")
        if not isinstance(content, list):
            converted.append(message)
            continue
        calls, blocks, results = [], [], []
        for block in content:
            if not isinstance(block, dict):
                raise ValueError("Message blocks must be objects")
            if block.get("type") == "tool_use":
                calls.append({"id": block["id"], "type": "function", "function": {
                    "name": block["name"], "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)}})
            elif block.get("type") == "tool_result":
                result = block.get("content", "")
                results.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                                "content": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)})
            elif block.get("type") == "image" and isinstance(block.get("source"), dict):
                source = block["source"]
                if source.get("type") == "base64":
                    blocks.append({"type": "image_url", "image_url": {
                        "url": f"data:{source['media_type']};base64,{source['data']}"}})
                else:
                    raise ValueError("Unsupported image source")
            else:
                blocks.append(block)
        if calls:
            message["tool_calls"] = calls
        message["content"] = blocks or None
        converted.extend(results)
        if blocks or calls or not results:
            converted.append(message)
    return converted


def _tools_for_openai(tools: list[dict]) -> list[dict]:
    return [tool if tool.get("type") == "function" else {
        "type": "function", "function": {"name": tool["name"], "description": tool.get("description", ""),
                                           "parameters": tool.get("input_schema", {"type": "object", "properties": {}})}}
        for tool in tools]


def _tool_calls(calls: list[dict]) -> list[dict]:
    normalized = []
    for call in calls:
        function = call.get("function", {})
        arguments = function.get("arguments", "{}")
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (ValueError, TypeError) as exc:
            raise ValueError("Model returned invalid tool arguments") from exc
        if not isinstance(arguments, dict) or not function.get("name") or not call.get("id"):
            raise ValueError("Model returned an incomplete tool call")
        normalized.append({"id": call["id"], "name": function["name"], "input": arguments})
    return normalized


class OpenAIProvider(LLMProvider):
    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(self, config, custom_headers=None):
        super().__init__(config)
        self.base_url = (config.base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.custom_headers = custom_headers or {}
        self.display_name = "OpenAI"

    @property
    def name(self):
        return self.display_name

    def _get_headers(self):
        if self.custom_headers:
            return self.custom_headers
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _payload(self, messages, kwargs, stream=False):
        payload = {"model": kwargs.get("model", self.config.model),
                   "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
                   "messages": _messages_for_openai(messages),
                   "temperature": kwargs.get("temperature", self.config.temperature)}
        if kwargs.get("tools"):
            payload["tools"] = _tools_for_openai(kwargs["tools"])
        if kwargs.get("response_format"):
            payload["response_format"] = kwargs["response_format"]
        if stream:
            payload["stream"] = True
        return payload

    async def chat(self, messages, **kwargs) -> LLMResponse:
        try:
            async with httpx.AsyncClient(timeout=kwargs.get("timeout", self.config.timeout), trust_env=False) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=self._get_headers(),
                                             json=self._payload(messages, kwargs))
                response.raise_for_status()
                data = response.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length" and choice["message"].get("tool_calls"):
                raise ValueError("Model truncated a tool call")
            calls = _tool_calls(choice["message"].get("tool_calls") or [])
            usage = data.get("usage") or {}
            return LLMResponse(content=choice["message"].get("content") or "", model=data.get("model", self.config.model),
                               usage={"prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0)},
                               finish_reason=choice.get("finish_reason") or "stop",
                               stop_reason="tool_use" if calls else choice.get("finish_reason", "stop"), tool_calls=calls or None)
        except Exception as exc:
            raise RuntimeError(format_httpx_error(self.name, exc)) from exc

    async def chat_stream(self, messages, **kwargs) -> AsyncIterator[Any]:
        pending = {}
        finish = None
        try:
            async with httpx.AsyncClient(timeout=kwargs.get("timeout", self.config.timeout), trust_env=False) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", headers=self._get_headers(),
                                         json=self._payload(messages, kwargs, stream=True)) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        data = json.loads(raw)
                        if data.get("error"):
                            raise RuntimeError("Upstream stream reported an error")
                        for choice in data.get("choices") or []:
                            if choice.get("index", 0) != 0:
                                continue
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                yield delta["content"]
                            for partial in delta.get("tool_calls") or []:
                                target = pending.setdefault(partial["index"], {"id": "", "function": {"name": "", "arguments": ""}})
                                if partial.get("id"):
                                    target["id"] = partial["id"]
                                function = partial.get("function") or {}
                                for key in ("name", "arguments"):
                                    target["function"][key] += function.get(key) or ""
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
            if pending:
                if finish != "tool_calls":
                    raise ValueError("Tool call stream ended before completion")
                yield {"stop_reason": "tool_use", "tool_calls": _tool_calls([pending[i] for i in sorted(pending)])}
        except Exception as exc:
            raise RuntimeError(format_httpx_error(self.name, exc)) from exc
