"""
OpenAI GPT Provider 实现
"""
from typing import List, Dict, Any, AsyncIterator
import httpx
import json

from core.llm.base import LLMProvider, LLMConfig, LLMResponse


class OpenAIProvider(LLMProvider):
    """OpenAI GPT Provider"""

    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(self, config: LLMConfig, custom_headers: Dict[str, str] = None):
        super().__init__(config)
        self.base_url = config.base_url or self.DEFAULT_BASE_URL
        self.custom_headers = custom_headers or {}

    @property
    def name(self) -> str:
        return "OpenAI"

    def _get_headers(self) -> Dict[str, str]:
        # 如果有自定义 headers，使用自定义的
        if self.custom_headers:
            return self.custom_headers
        # 否则使用标准格式
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }

    async def chat(
        self,
        messages: List[Dict[str, Any]],  # 改为 Any 以支持复杂结构
        **kwargs
    ) -> LLMResponse:
        payload = {
            "model": kwargs.get("model", self.config.model),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.config.temperature)
        }

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json=payload
            )
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]
        return LLMResponse(
            content=choice["message"]["content"],
            model=data["model"],
            usage={
                "prompt_tokens": data["usage"]["prompt_tokens"],
                "completion_tokens": data["usage"]["completion_tokens"]
            },
            finish_reason=choice["finish_reason"]
        )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> AsyncIterator[str]:
        payload = {
            "model": kwargs.get("model", self.config.model),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.config.temperature),
            "stream": True
        }

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
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
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except Exception as e:
                            print(f"[OpenAI Stream] 解析错误: {e}")
                            continue
