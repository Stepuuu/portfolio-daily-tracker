"""
LLM 上游错误格式化工具

A failed model call becomes a fixed text: the provider's name, the HTTP status
and the kind of failure. What the provider answered in its error body and what
the library said are never shown, and not logged either: a provider's error
text can echo part of the key (as ``core.llm_setup`` says for the connection
test). The log gets the exception's class and the status only.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def format_httpx_error(provider_name: str, error: Exception) -> str:
    status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
    logger.warning("model call to %s failed: %s%s", provider_name, type(error).__name__,
                   f" (HTTP {status})" if status else "")
    if isinstance(error, httpx.TimeoutException):
        return f"{provider_name} 请求超时，请稍后重试"

    if status is not None:
        if status == 401:
            return f"{provider_name} 鉴权失败 (401)。请检查 API Key"
        if status == 403:
            return f"{provider_name} 无权限访问 (403)"
        if status == 404:
            return f"{provider_name} 接口地址不存在 (404)"
        if status == 408:
            return f"{provider_name} 请求超时 (408)"
        if status == 429:
            return f"{provider_name} 触发限流 (429)。请稍后重试"
        if 500 <= status < 600:
            return f"{provider_name} 上游服务错误 ({status})。请稍后重试"
        return f"{provider_name} 请求失败 ({status})。请检查请求配置"

    if isinstance(error, httpx.RequestError):
        return f"{provider_name} 网络请求失败。请检查网络、代理和 API 地址"

    return f"{provider_name} 调用失败：模型服务的回应无法处理"
