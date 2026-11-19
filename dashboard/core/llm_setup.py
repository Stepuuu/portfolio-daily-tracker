"""The setup wizard's model step: save the chat model and its key, and test the connection.

Storage is the existing one and nothing else: ``config.settings.Config``, the
JSON file named by ``TRACKER_CONFIG_FILE`` (default ``config.json`` in the
backend's working directory). A preset writes the API group it stands for
(``api_groups.<group>``: ``base_url``, ``api_key``, ``provider_type``,
``models``), selects it (``current_api_group``) and the model
(``llm.current_model``), the same keys 设置 › 对话默认模型 switches. A key in
``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` still wins for the official hosts,
read at use time by ``Config.get_current_api_group`` as before.

The key is write-only. Nothing here returns it, logs it, or puts it in an
exception message; ``view()`` says only whether a key is configured and where
it comes from (the existing settings API shows nothing about keys, so neither
does this: no last four characters). The file is made private (mode 0600)
before a key is written into it. A group with custom request headers keeps
them and gets the key in its auth header, as ``get_current_api_group`` does
for a key from the environment; otherwise headers are left to the provider.

The base URL follows the rule the research connections already apply
(``core.lab_models.Connection``): http or https, a host name, no user name,
password, query or fragment. Loopback hosts are allowed (a local Ollama or
vLLM is the point of the 兼容接口 preset). The settings API itself has no
base-URL check: it cannot set one.

``test_connection()`` sends one minimal chat request (``max_tokens`` 8) with a
bounded timeout to the saved configuration and answers ``ok`` or one of the
fixed Chinese texts in ``FAILURES``: by HTTP status, else by the classes
``core.provider_errors.classify`` recognises. The provider's own error text
(which may echo part of a key) is never returned or logged; the log carries
the outcome code and the exception's class name only.
"""
from __future__ import annotations

import asyncio
import copy
import ipaddress
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

TEST_TIMEOUT_SECONDS = 15
MAX_KEY_LENGTH = 400
MODEL = re.compile(r'[A-Za-z0-9_./:+-]{1,160}')
OFFICIAL_KEY_ENV = {'api.openai.com': 'OPENAI_API_KEY', 'api.anthropic.com': 'ANTHROPIC_API_KEY'}

PRESETS: List[Dict[str, Any]] = [
    {'id': 'deepseek', 'label': 'DeepSeek', 'group': 'deepseek', 'name': 'DeepSeek',
     'description': 'DeepSeek 官方 API（OpenAI 兼容）', 'provider_type': 'openai',
     'base_url': 'https://api.deepseek.com/v1', 'base_url_editable': False,
     'models': [{'id': 'deepseek-chat', 'name': 'DeepSeek Chat', 'supports_vision': False},
                {'id': 'deepseek-reasoner', 'name': 'DeepSeek Reasoner', 'supports_vision': False}]},
    {'id': 'openai', 'label': 'OpenAI', 'group': 'openai_official', 'name': 'OpenAI 官方',
     'description': 'GPT 官方 API', 'provider_type': 'openai',
     'base_url': 'https://api.openai.com/v1', 'base_url_editable': False,
     'models': [{'id': 'gpt-4o', 'name': 'GPT-4o', 'supports_vision': True},
                {'id': 'gpt-4o-mini', 'name': 'GPT-4o Mini', 'supports_vision': True}]},
    {'id': 'anthropic', 'label': 'Anthropic', 'group': 'anthropic_official', 'name': 'Anthropic 官方',
     'description': 'Claude 官方 API', 'provider_type': 'claude',
     'base_url': 'https://api.anthropic.com/v1', 'base_url_editable': False,
     'models': [{'id': 'claude-sonnet-4-20250514', 'name': 'Claude Sonnet 4', 'supports_vision': True}]},
    {'id': 'compatible', 'label': '兼容接口', 'group': 'openai_compatible', 'name': 'OpenAI-Compatible API',
     'description': '任何 OpenAI 兼容接口（Ollama、vLLM、LiteLLM 或第三方聚合服务）', 'provider_type': 'openai',
     'base_url': 'http://localhost:11434/v1', 'base_url_editable': True, 'models': []},
]
PRESET_BY_ID = {preset['id']: preset for preset in PRESETS}

FAILURES = {
    'auth': '密钥无效或已失效（服务返回 401）。请检查 API 密钥。',
    'forbidden': '密钥没有调用这个模型的权限（服务返回 403）。',
    'not_found': '找不到接口或模型（服务返回 404）。请检查 API 地址和模型名称。',
    'rejected': '请求被拒绝：模型名称或参数可能不受支持（服务返回 400）。',
    'rate_limited': '请求过多或额度不足（服务返回 429）。请稍后再试或检查账户余额。',
    'upstream': '模型服务暂时不可用（服务返回 5xx）。请稍后再试。',
    'http': '模型服务拒绝了请求（HTTP 状态异常）。',
    'timeout': f'模型服务在 {TEST_TIMEOUT_SECONDS} 秒内没有回应。',
    'connection': '无法连接到模型服务。请检查 API 地址、网络或代理。',
    'parse': '模型服务的回应无法解析。API 地址可能不是兼容的接口。',
    'provider': '模型服务调用失败。',
    'not_configured': '还没有保存模型设置。',
    'no_key': '还没有保存 API 密钥。',
    'bad_url': 'API 地址必须是 HTTP(S) 地址，不能包含用户名、密码、查询或片段。',
    'new_address': 'API 地址或服务商与保存密钥时不同：请为新地址填写 API 密钥。已保存的密钥只会发往保存它时的地址。',
}


class SetupError(ValueError):
    """The request cannot be saved; the message is Chinese and never contains the submitted key."""


# -- the configuration file -------------------------------------------------------

def config_path() -> Path:
    """Where ``Config`` reads and writes (its own rule: ``TRACKER_CONFIG_FILE``, else ``config.json``)."""
    return Path(os.environ.get('TRACKER_CONFIG_FILE', 'config.json'))


def _config_for_reading():
    """The settings object, or None when there is no configuration file yet (reading must not create one)."""
    from config import settings
    if settings._config is None and not config_path().is_file():
        return None
    return settings.get_config()


def _private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning('could not make the configuration file private: %s', type(exc).__name__)


def check_base_url(value: Any) -> Optional[str]:
    """None when ``value`` is an acceptable API address, else the fixed reason."""
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        return FAILURES['bad_url']
    try:
        parsed = urlsplit(value.strip())
        hostname = parsed.hostname
        parsed.port   # raises on a malformed port
    except ValueError:
        return FAILURES['bad_url']
    if parsed.scheme not in ('http', 'https') or not hostname or parsed.username or parsed.password \
            or parsed.query or parsed.fragment or any(ch.isspace() for ch in value.strip()):
        return FAILURES['bad_url']
    return None


def shown_url(value: Any) -> Optional[str]:
    """The address without anything that could be a credential (user info, query, fragment)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        port = f':{parsed.port}' if parsed.port else ''
    except ValueError:
        return None
    if ':' in host:
        host = f'[{host}]'
    return urlunsplit((parsed.scheme, host + port, parsed.path, '', ''))


def _origin(url: Any) -> Optional[Tuple[str, str, Optional[int]]]:
    """Scheme, host and port of an address (the scheme's default port when none is written); None when unreadable."""
    try:
        parsed = urlsplit(str(url or '').strip())
        host = (parsed.hostname or '').lower()
        port = parsed.port or {'http': 80, 'https': 443}.get(parsed.scheme.lower())
    except ValueError:
        return None
    return (parsed.scheme.lower(), host, port) if parsed.scheme and host else None


# Request headers that may carry a credential: they belong to the address they were saved for.
_CREDENTIAL_HEADER = re.compile(r'.*(auth|key|token|secret).*', re.IGNORECASE)


def _without_credentials(group: dict) -> None:
    """Drop the group's key and every credential header (the address or the provider changed)."""
    group['api_key'] = ''
    headers = group.get('headers')
    if isinstance(headers, dict):
        group['headers'] = {name: value for name, value in headers.items()
                            if not _CREDENTIAL_HEADER.fullmatch(str(name))} or None


def _loopback(url: Any) -> bool:
    try:
        host = urlsplit(url).hostname or ''
    except (TypeError, ValueError):
        return False
    if host == 'localhost' or host.endswith('.localhost'):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# The placeholders of config.example.json ("sk-YOUR_OPENAI_API_KEY_HERE"), which `make setup` copies to
# config.json: a key that was never filled in is no key.
_PLACEHOLDER = re.compile(r'YOUR_[A-Z_]*KEY_HERE')


def _real_key(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not _PLACEHOLDER.search(value)


def _header_key(headers: Any) -> bool:
    """A custom header that carries a credential (``Authorization`` with more than the scheme, ``x-api-key``)."""
    if not isinstance(headers, dict):
        return False
    for name, value in headers.items():
        lowered = str(name).lower()
        text = str(value or '').strip()
        if lowered == 'authorization' and text.lower() not in ('bearer', 'basic') and _real_key(text.split(' ')[-1]):
            return True
        if lowered == 'x-api-key' and _real_key(text):
            return True
    return False


def _key_source(stored: dict, effective: dict) -> Optional[str]:
    hostname = urlsplit(effective.get('base_url') or '').hostname
    env = OFFICIAL_KEY_ENV.get(hostname or '')
    if env and os.environ.get(env):
        return 'env'
    if _real_key(stored.get('api_key')) or _header_key(stored.get('headers')):
        return 'config'
    return None


def provider_type(group: dict, model: str) -> str:
    """``claude`` or ``openai``: the rule ``AgentService._init_llm`` applies to the same group."""
    explicit = group.get('provider_type', '')
    if explicit in ('claude', 'anthropic'):
        return 'claude'
    if explicit == 'openai':
        return 'openai'
    if 'anthropic' in (group.get('base_url') or '').lower() or 'claude' in model.lower():
        return 'claude'
    return 'openai'


def _preset_of(group_id: str) -> Optional[str]:
    return next((preset['id'] for preset in PRESETS if preset['group'] == group_id), None)


def _vision(group: dict, model: str) -> Optional[bool]:
    for entry in group.get('models') or []:
        if isinstance(entry, dict) and entry.get('id') == model:
            return bool(entry.get('supports_vision', False))
    return None


def _origin_text(origin: Tuple[str, str, Optional[int]]) -> str:
    """``scheme://host:port`` as a browser's URL spells it (IDN hosts in punycode, IPv6 hosts in brackets)."""
    scheme, host, port = origin
    try:
        host = host.encode('idna').decode('ascii')
    except UnicodeError:
        pass
    return f'{scheme}://[{host}]:{port}' if ':' in host else f'{scheme}://{host}:{port}'


def _stored_key(group: dict) -> bool:
    return _real_key(group.get('api_key')) or _header_key(group.get('headers'))


def _keyless(preset: dict, stored: dict) -> dict:
    """Where ``save`` takes ``preset`` without a typed key (the same rule, for the form): ``{origins, env,
    loopback, saved_key}``. A blank key is accepted at an address whose ``scheme://host:port`` is in ``origins``
    (the stored key's, for the same provider), whose host takes its key from the server environment (``env``:
    ``[{host, name}]``, never the key), or on a loopback host; anywhere else the save is refused with
    ``FAILURES['new_address']`` when ``saved_key`` (a key is stored for another address or provider), else with
    请填写 API 密钥."""
    origin = _origin(stored.get('base_url'))
    keeps = _stored_key(stored) and origin is not None and provider_type(stored, '') == preset['provider_type']
    return {'origins': [_origin_text(origin)] if keeps else [],
            'env': [{'host': host, 'name': name} for host, name in OFFICIAL_KEY_ENV.items() if os.environ.get(name)],
            'loopback': True, 'saved_key': bool(_stored_key(stored))}


def _key_free(rules: dict, base_url: str) -> bool:
    origin = _origin(base_url)
    host = urlsplit(base_url).hostname or ''
    return (origin is not None and _origin_text(origin) in rules['origins']) \
        or any(entry['host'] == host for entry in rules['env']) or (rules['loopback'] and _loopback(base_url))


def _env_key(preset: dict) -> Optional[dict]:
    """For a preset whose fixed host takes a key from the environment: ``{name, available}`` (never the key)."""
    if preset['base_url_editable']:
        return None
    name = OFFICIAL_KEY_ENV.get(urlsplit(preset['base_url']).hostname or '')
    return {'name': name, 'available': bool(os.environ.get(name))} if name else None


def view() -> dict:
    """The presets and the saved choice, without the key: ``{presets, current}``. Each preset carries
    ``env_key`` (``{name, available}`` or None): whether the server environment provides its key, and
    ``keyless`` (``_keyless``): where saving it needs no typed key, exactly as ``save`` decides."""
    config = _config_for_reading()
    groups = (config.get_all_api_groups() or {}) if config is not None else {}
    current = None
    if config is not None:
        from config import DEFAULT_API_GROUP
        group_id = config.get('current_api_group', DEFAULT_API_GROUP)
        stored = (config.get_all_api_groups() or {}).get(group_id) or {}
        effective = config.get_current_api_group()
        model = config.get_current_model()
        source = _key_source(stored, effective)
        current = {
            'preset': _preset_of(group_id), 'group': group_id, 'group_name': stored.get('name') or group_id,
            'base_url': shown_url(effective.get('base_url')), 'model': model,
            'supports_vision': _vision(effective, model),
            'key': {'configured': source is not None, 'source': source,
                    'env': OFFICIAL_KEY_ENV.get(urlsplit(effective.get('base_url') or '').hostname or '')},
            'local': _loopback(effective.get('base_url')),
        }
    return {'presets': [{**copy.deepcopy(preset), 'env_key': _env_key(preset),
                         'keyless': _keyless(preset, groups.get(preset['group']) or {})} for preset in PRESETS],
            'current': current}


def summary() -> dict:
    """For the checklist: ``{configured, text}`` (never the key)."""
    current = view()['current']
    if current is None:
        return {'configured': False, 'text': '还没有模型设置'}
    name = f'{current["group_name"]} / {current["model"]}'
    if current['key']['configured']:
        where = f'密钥来自环境变量 {current["key"]["env"]}' if current['key']['source'] == 'env' else '密钥已保存'
        return {'configured': True, 'text': f'{name}（{where}）'}
    if current['local']:
        return {'configured': True, 'text': f'{name}（本机服务，未设置密钥）'}
    return {'configured': False, 'text': f'{name}：还没有 API 密钥'}


# -- saving -----------------------------------------------------------------------

def _text(body: dict, name: str, maximum: int) -> Optional[str]:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise SetupError({'preset': '请选择服务商。', 'model': '模型名称无效。', 'base_url': FAILURES['bad_url'],
                          'api_key': f'API 密钥无效（最多 {MAX_KEY_LENGTH} 个字符）。'}.get(name, '请求内容无效。'))
    return value.strip()


def parse_request(body: Any) -> dict:
    """Check a save request without echoing any of it: ``{preset, model, base_url, api_key, supports_vision}``."""
    if not isinstance(body, dict):
        raise SetupError('请求内容必须是 JSON 对象。')
    unknown = set(body) - {'preset', 'model', 'base_url', 'api_key', 'supports_vision'}
    if unknown:
        raise SetupError('请求里有不认识的字段。')
    preset_id = _text(body, 'preset', 40)
    if preset_id not in PRESET_BY_ID:
        raise SetupError('请选择服务商。')
    preset = PRESET_BY_ID[preset_id]
    model = _text(body, 'model', 160) or ''
    if not MODEL.fullmatch(model):
        raise SetupError('请填写模型名称（字母、数字和 _ . / : + -）。')
    base_url = _text(body, 'base_url', 500)
    if preset['base_url_editable']:
        problem = check_base_url(base_url)
        if problem:
            raise SetupError(problem)
        base_url = base_url.rstrip('/')
    elif base_url not in (None, '', preset['base_url']):
        raise SetupError('这个服务商的 API 地址是固定的；其他地址请选择“兼容接口”。')
    else:
        base_url = preset['base_url']
    api_key = _text(body, 'api_key', MAX_KEY_LENGTH) or ''
    if api_key and (any(ch.isspace() or ord(ch) < 32 for ch in api_key) or not api_key.isascii()):
        raise SetupError('API 密钥里有空格或无法识别的字符，请检查是否复制完整。')
    vision = body.get('supports_vision')
    if vision is not None and not isinstance(vision, bool):
        raise SetupError('“支持图片”只能是或否。')
    return {'preset': preset_id, 'model': model, 'base_url': base_url, 'api_key': api_key, 'supports_vision': vision}


def _with_key(group: dict, key: str, kind: str) -> dict:
    headers = group.get('headers')
    if isinstance(headers, dict) and headers:
        headers = dict(headers)
        if kind == 'claude':
            headers['x-api-key'] = key
        else:
            headers['Authorization'] = f'Bearer {key}'
        group['headers'] = headers
    group['api_key'] = key
    return group


def save(request: dict) -> dict:
    """Write the preset's API group, select it and the model; returns ``view()['current']``."""
    from config import get_config
    preset = PRESET_BY_ID[request['preset']]
    config = get_config()   # the shipped defaults when there is no file yet; saving creates it private
    if config.config_file.exists():
        _private(config.config_file)
    groups = config.get_all_api_groups() or {}
    stored = groups.get(preset['group']) or {}
    group = copy.deepcopy(stored)
    # A stored key belongs to the address (scheme, host, port) and the provider it was saved with: it is never
    # sent anywhere else, so another address or provider needs a key typed for it.
    moved = _origin(stored.get('base_url')) != _origin(request['base_url']) \
        or provider_type(stored, '') != preset['provider_type']
    group.setdefault('name', preset['name'])
    group.setdefault('description', preset['description'])
    group.setdefault('headers', None)
    group['base_url'] = request['base_url']
    group['provider_type'] = preset['provider_type']
    models = [dict(entry) for entry in group.get('models') or [] if isinstance(entry, dict) and entry.get('id')]
    for known in preset['models']:
        if not any(entry['id'] == known['id'] for entry in models):
            models.append({**known, 'description': ''})
    entry = next((entry for entry in models if entry['id'] == request['model']), None)
    if entry is None:
        entry = {'id': request['model'], 'name': request['model'], 'description': '', 'supports_vision': False}
        models.append(entry)
    if request['supports_vision'] is not None:
        entry['supports_vision'] = request['supports_vision']
    group['models'] = models
    rules = _keyless(preset, stored)
    if moved:
        _without_credentials(group)
    if request['api_key']:
        group = _with_key(group, request['api_key'], preset['provider_type'])
    elif not _key_free(rules, group['base_url']):
        raise SetupError(FAILURES['new_address'] if rules['saved_key'] else '请填写 API 密钥。')
    group.setdefault('api_key', '')
    config.set(f'api_groups.{preset["group"]}', group)
    config.set('current_api_group', preset['group'])
    config.set('llm.current_model', request['model'])
    _private(config.config_file)
    return view()['current']


async def reload_running_service() -> bool:
    """Let a running chat service pick the new model up (as 设置 does after a switch); never raises."""
    try:
        from backend import main
        service = getattr(main, 'agent_service', None)
        if service is None:
            return False
        await service.reload_llm()
        return True
    except Exception as exc:
        logger.warning('chat service did not reload the model settings: %s', type(exc).__name__)
        return False


# -- the connection test ------------------------------------------------------------

def classify(exc: BaseException) -> Tuple[str, Optional[int]]:
    """``(code, HTTP status or None)`` for a failed test; the providers wrap the real error as ``__cause__``."""
    import httpx
    from core import provider_errors
    cause = exc.__cause__ if isinstance(exc, RuntimeError) and exc.__cause__ is not None else exc
    if isinstance(cause, httpx.HTTPStatusError):
        status = cause.response.status_code
        code = {401: 'auth', 403: 'forbidden', 404: 'not_found', 400: 'rejected', 422: 'rejected',
                429: 'rate_limited'}.get(status)
        return code or ('upstream' if 500 <= status < 600 else 'http'), status
    if isinstance(cause, asyncio.TimeoutError):
        return 'timeout', None
    kind = provider_errors.classify(cause)
    return {provider_errors.ERR_TIMEOUT: 'timeout', provider_errors.ERR_CONNECTION: 'connection',
            provider_errors.ERR_PARSE: 'parse'}.get(kind, 'provider'), None


def _failure(code: str, **extra) -> dict:
    return {'ok': False, 'code': code, 'message': FAILURES[code], **extra}


async def test_connection(timeout: Optional[float] = None) -> dict:
    """One minimal request to the saved model: ``{ok, code, message, model, latency_ms?, status?}``."""
    timeout = TEST_TIMEOUT_SECONDS if timeout is None else timeout
    from core.llm.base import LLMConfig
    from providers.llm import LLMProviderType, create_llm_provider
    config = _config_for_reading()
    if config is None:
        return _failure('not_configured')
    group = config.get_current_api_group()
    model = config.get_current_model()
    if not group:
        return _failure('not_configured')
    if check_base_url(group.get('base_url')):
        return _failure('bad_url', model=model)
    if not _real_key(group.get('api_key')) and not _header_key(group.get('headers')) and not _loopback(group.get('base_url')):
        return _failure('no_key', model=model)
    kind = LLMProviderType.CLAUDE if provider_type(group, model) == 'claude' else LLMProviderType.OPENAI
    provider = create_llm_provider(kind, LLMConfig(api_key=group.get('api_key') or '', model=model,
                                                   base_url=group.get('base_url'), max_tokens=8, temperature=0,
                                                   timeout=timeout), group.get('headers') or None)
    started = time.monotonic()
    try:
        await asyncio.wait_for(provider.chat([{'role': 'user', 'content': 'ping'}], max_tokens=8), timeout + 2)
    except Exception as exc:
        code, status = classify(exc)
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        logger.warning('model connection test failed: %s (%s%s)', code, type(cause).__name__,
                       f', HTTP {status}' if status else '')
        return _failure(code, model=model, status=status)
    return {'ok': True, 'code': 'ok', 'message': '连接成功：模型已回应。', 'model': model,
            'latency_ms': int((time.monotonic() - started) * 1000)}
