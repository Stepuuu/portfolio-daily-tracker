"""
Claude LLM Provider 实现
"""
from typing import List, Dict, Any, AsyncIterator, Optional
import httpx

from core.llm.base import LLMProvider, LLMConfig, LLMResponse


class ClaudeProvider(LLMProvider):
    """Anthropic Claude Provider"""

    DEFAULT_BASE_URL = "https://api.anthropic.com/v1"

    def __init__(self, config: LLMConfig, custom_headers: Optional[Dict[str, str]] = None):
        super().__init__(config)
        self.base_url = config.base_url or self.DEFAULT_BASE_URL
        self.custom_headers = custom_headers

    @property
    def name(self) -> str:
        return "Claude"

    def _get_headers(self) -> Dict[str, str]:
        # 如果有自定义 headers，使用自定义的（用于第三方 API 代理）
        if self.custom_headers:
            return self.custom_headers
        # 否则使用标准 Anthropic 格式
        return {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

    def _convert_messages(self, messages: List[Dict[str, str]]) -> tuple:
        """转换消息格式，分离 system 消息"""
        system_content = ""
        converted = []

        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            else:
                converted.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

        return system_content, converted

    async def chat(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> LLMResponse:
        system_content, converted_messages = self._convert_messages(messages)

        payload = {
            "model": kwargs.get("model", self.config.model),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "messages": converted_messages
        }

        if system_content:
            payload["system"] = system_content

        if "temperature" in kwargs or self.config.temperature:
            payload["temperature"] = kwargs.get("temperature", self.config.temperature)

        # 添加工具定义
        if "tools" in kwargs and kwargs["tools"]:
            payload["tools"] = kwargs["tools"]

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                f"{self.base_url}/messages",
                headers=self._get_headers(),
                json=payload
            )
            response.raise_for_status()
            data = response.json()

        # 处理响应内容
        content_text = ""
        tool_calls = []

        for block in data.get("content", []):
            if block.get("type") == "text":
                content_text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input", {})
                })

        return LLMResponse(
            content=content_text,
            model=data["model"],
            usage={
                "prompt_tokens": data["usage"]["input_tokens"],
                "completion_tokens": data["usage"]["output_tokens"]
            },
            finish_reason=data["stop_reason"],
            stop_reason=data["stop_reason"],
            tool_calls=tool_calls if tool_calls else None
        )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> AsyncIterator[str]:
        system_content, converted_messages = self._convert_messages(messages)

        payload = {
            "model": kwargs.get("model", self.config.model),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "messages": converted_messages,
            "stream": True
        }

        if system_content:
            payload["system"] = system_content

        if "temperature" in kwargs or self.config.temperature:
            payload["temperature"] = kwargs.get("temperature", self.config.temperature)

        # 添加工具定义
        if "tools" in kwargs and kwargs["tools"]:
            payload["tools"] = kwargs["tools"]

        tool_calls = []
        current_tool_input_json = ""  # 累积工具输入 JSON 片段
        stop_reason = None

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers=self._get_headers(),
                json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            import json
                            data = json.loads(data_str)
                            event_type = data.get("type")

                            # 处理工具调用开始
                            if event_type == "content_block_start":
                                block = data.get("content_block", {})
                                if block.get("type") == "tool_use":
                                    current_tool_input_json = ""
                                    tool_calls.append({
                                        "id": block.get("id"),
                                        "name": block.get("name"),
                                        "input": {}
                                    })

                            # 处理内容增量
                            elif event_type == "content_block_delta":
                                delta = data.get("delta", {})
                                delta_type = delta.get("type")
                                if delta_type == "text_delta" and "text" in delta:
                                    yield delta["text"]
                                elif delta_type == "input_json_delta" and tool_calls:
                                    # 累积工具输入 JSON 片段
                                    current_tool_input_json += delta.get("partial_json", "")

                            # 处理内容块结束（解析累积的工具 JSON）
                            elif event_type == "content_block_stop":
                                if current_tool_input_json and tool_calls:
                                    try:
                                        tool_calls[-1]["input"] = json.loads(current_tool_input_json)
                                    except json.JSONDecodeError as e:
                                        print(f"[Stream] 工具输入 JSON 解析失败: {e}")
                                    current_tool_input_json = ""

                            # 处理消息完成
                            elif event_type == "message_delta":
                                delta = data.get("delta", {})
                                if "stop_reason" in delta:
                                    stop_reason = delta["stop_reason"]

                        except Exception as e:
                            print(f"[Stream] 解析错误: {e}")
                            continue

        # 如果有工具调用，返回元数据
        if tool_calls or stop_reason:
            yield {"stop_reason": stop_reason, "tool_calls": tool_calls}
