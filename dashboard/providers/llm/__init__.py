"""
LLM Provider 工厂
"""
from typing import Optional, Dict
from enum import Enum

from core.llm.base import LLMProvider, LLMConfig
from .claude import ClaudeProvider
from .openai import OpenAIProvider


class LLMProviderType(Enum):
    CLAUDE = "claude"
    OPENAI = "openai"


def create_llm_provider(
    provider_type: LLMProviderType,
    config: LLMConfig,
    custom_headers: Optional[Dict[str, str]] = None
) -> LLMProvider:
    """
    创建 LLM Provider

    Args:
        provider_type: Provider 类型
        config: 配置
        custom_headers: 自定义请求头（用于第三方 API）

    Returns:
        LLMProvider 实例
    """
    if provider_type == LLMProviderType.CLAUDE:
        return ClaudeProvider(config, custom_headers)
    elif provider_type == LLMProviderType.OPENAI:
        return OpenAIProvider(config, custom_headers)
    else:
        raise ValueError(f"不支持的 Provider 类型: {provider_type}")


# 默认模型配置
DEFAULT_MODELS = {
    LLMProviderType.CLAUDE: "claude-sonnet-4-20250514",
    LLMProviderType.OPENAI: "gpt-4o"
}


__all__ = [
    "ClaudeProvider",
    "OpenAIProvider",
    "LLMProviderType",
    "create_llm_provider",
    "DEFAULT_MODELS"
]
