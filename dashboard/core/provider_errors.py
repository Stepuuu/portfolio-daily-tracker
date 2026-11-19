"""Fixed error texts for failures of upstream calls.

What an upstream library raised (its class name, message, ``repr``) is
attacker-influenced text: it must never be copied into a field the model
trusts, and an identifier-shaped class name is no allow-list. ``classify``
maps an exception to one of the fixed strings below by ``isinstance`` against
a short list of well-known base classes; everything else is ``ERR_PROVIDER``.
The real class name and message go to the server log only (``log_failure``).
"""
from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from typing import Tuple, Type

ERR_PROVIDER = '数据源出错'
ERR_TIMEOUT = '数据源响应超时'
ERR_CONNECTION = '数据源连接失败'
ERR_PARSE = '数据源返回的数据无法解析'

ERROR_KINDS = (ERR_PROVIDER, ERR_TIMEOUT, ERR_CONNECTION, ERR_PARSE)


def _library_bases(module: str, names: Tuple[str, ...]) -> Tuple[Type[BaseException], ...]:
    try:
        imported = __import__(module, fromlist=list(names))
    except Exception:
        return ()
    found = tuple(getattr(imported, name, None) for name in names)
    return tuple(c for c in found if isinstance(c, type) and issubclass(c, BaseException))


_TIMEOUTS: Tuple[Type[BaseException], ...] = (
    TimeoutError, asyncio.TimeoutError, socket.timeout,
    *_library_bases('requests.exceptions', ('Timeout',)),
    *_library_bases('urllib3.exceptions', ('TimeoutError',)),
    *_library_bases('httpx', ('TimeoutException',)))
_CONNECTIONS: Tuple[Type[BaseException], ...] = (
    ConnectionError, ssl.SSLError, socket.gaierror, EOFError,
    *_library_bases('requests.exceptions', ('ConnectionError', 'SSLError', 'ProxyError')),
    *_library_bases('urllib3.exceptions', ('HTTPError',)),
    *_library_bases('httpx', ('TransportError',)))
_PARSE: Tuple[Type[BaseException], ...] = (
    ValueError, KeyError, IndexError, TypeError, AttributeError, UnicodeError, ArithmeticError,
    *_library_bases('requests.exceptions', ('JSONDecodeError',)),
    *_library_bases('httpx', ('DecodingError',)))


def classify(exc: BaseException) -> str:
    """One of ``ERROR_KINDS`` for ``exc``; never anything the exception said."""
    if isinstance(exc, _TIMEOUTS):
        return ERR_TIMEOUT
    if isinstance(exc, _CONNECTIONS) or isinstance(exc, OSError):
        return ERR_CONNECTION
    if isinstance(exc, _PARSE):
        return ERR_PARSE
    return ERR_PROVIDER


def log_failure(logger: logging.Logger, what: str, exc: BaseException) -> str:
    """Log the real exception server-side and return its fixed text."""
    logger.warning('%s failed: %s: %r', what, type(exc).__name__, str(exc)[:300])
    return classify(exc)
