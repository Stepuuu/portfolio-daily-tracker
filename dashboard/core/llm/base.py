"""
LLM Provider 抽象基类

Tool-call arguments are JSON written by the model. Their numbers reach the
tools exactly: the adapters read them with ``exact_loads`` (a number with a
fraction or an exponent becomes a ``Decimal``, never a binary float that would
round ``1000000000000.00000001``), and send them back in the conversation with
``exact_dumps`` (the same digits). A tool gets ``int`` or ``Decimal``.
"""
import json
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import List, Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass


def exact_loads(text):
    """``json.loads`` with every non-integer number as ``Decimal``."""
    return json.loads(text, parse_float=Decimal)


def _key(name) -> str:
    """An object key as ``json.dumps`` writes it (a non-string key becomes its JSON text, quoted)."""
    if not isinstance(name, str):
        name = {True: "true", False: "false", None: "null"}.get(name, str(name)) if isinstance(
            name, (bool, type(None))) else str(name)
    return json.dumps(name, ensure_ascii=False)


def exact_dumps(value: Any) -> str:
    """``json.dumps(value, ensure_ascii=False)``, writing a ``Decimal`` with its exact digits as a JSON number."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("a JSON number must be finite")
        return str(value)
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_key(name)}: {exact_dumps(item)}" for name, item in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(exact_dumps(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


@dataclass
class LLMConfig:
    """LLM 配置"""
    api_key: str
    model: str
    base_url: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: int = 60


@dataclass
class LLMResponse:
    """LLM 响应"""
    content: str
    model: str
    usage: Dict[str, int]  # {"prompt_tokens": x, "completion_tokens": y}
    finish_reason: str
    stop_reason: Optional[str] = None  # For tool use
    tool_calls: Optional[List[Dict]] = None  # Tool calls if any


class LLMProvider(ABC):
    """LLM Provider 抽象基类"""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> LLMResponse:
        """
        发送对话请求

        Args:
            messages: 消息列表，格式 [{"role": "user/assistant/system", "content": "..."}]
            **kwargs: 额外参数（如 temperature, max_tokens 覆盖）

        Returns:
            LLMResponse
        """
        pass

    @abstractmethod
    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> AsyncIterator[str]:
        """
        流式对话请求

        Args:
            messages: 消息列表
            **kwargs: 额外参数

        Yields:
            响应内容片段
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称"""
        pass

    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息"""
        return {
            "provider": self.name,
            "model": self.config.model,
            "max_tokens": self.config.max_tokens
        }
