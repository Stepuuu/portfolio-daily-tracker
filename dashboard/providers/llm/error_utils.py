"""
LLM 上游错误格式化工具
"""
from __future__ import annotations

from typing import Any
import json

import httpx


def _extract_response_detail(response: httpx.Response) -> str:
    try:
        data: Any = response.json()
        if isinstance(data, dict):
            for key in ("detail", "message", "error", "msg"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    nested = value.get("message") or value.get("detail")
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
        text = json.dumps(data, ensure_ascii=False)
    except Exception:
        text = response.text

    text = (text or "").strip()
    if len(text) > 300:
        text = text[:300] + "..."
    return text


def format_httpx_error(provider_name: str, error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        return f"{provider_name} 请求超时，请稍后重试"

    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        detail = _extract_response_detail(error.response)

        if status == 401:
            return f"{provider_name} 鉴权失败 (401)。请检查 API Key"
        if status == 403:
            return f"{provider_name} 无权限访问 (403)"
        if status == 404:
            return f"{provider_name} 接口地址不存在 (404)"
        if status == 408:
            return f"{provider_name} 请求超时 (408)"
        if status == 429:
            return f"{provider_name} 触发限流 (429)。{detail or '请稍后重试'}"
        if 500 <= status < 600:
            return f"{provider_name} 上游服务错误 ({status})。{detail or '请稍后重试'}"
        return f"{provider_name} 请求失败 ({status})。{detail or '请检查请求配置'}"

    if isinstance(error, httpx.RequestError):
        return f"{provider_name} 网络请求失败：{str(error)}"

    return f"{provider_name} 调用失败：{str(error)}"
