"""One place that decides how quote sources reach the network."""
from __future__ import annotations

import os
from typing import Optional

import httpx


class ProxyUnavailable(httpx.TransportError):
    """The proxy named in the environment cannot be used by this install."""


def open_client(*, timeout: httpx.Timeout, headers: dict, transport: Optional[httpx.BaseTransport] = None,
                proxy: Optional[str] = None, use_env_proxy: bool = True) -> httpx.Client:
    """A client with a timeout on every call.

    ``proxy`` wins; otherwise the proxy variables in the environment apply
    unless ``use_env_proxy`` is false. A SOCKS ``ALL_PROXY`` needs an optional
    package: without it the plain ``HTTPS_PROXY`` is used when there is one,
    instead of failing every request.
    """
    options = dict(timeout=timeout, headers=headers, transport=transport)
    if proxy:
        return httpx.Client(proxy=proxy, **options)
    if not use_env_proxy:
        return httpx.Client(trust_env=False, **options)
    try:
        return httpx.Client(**options)
    except ImportError:
        fallback = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or ''
        if fallback.lower().startswith(('http://', 'https://')):
            return httpx.Client(proxy=fallback, trust_env=False, **options)
        raise ProxyUnavailable('SOCKS proxy support is not installed') from None
