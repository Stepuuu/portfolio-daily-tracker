"""
LLM Provider 抽象基类
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass


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
