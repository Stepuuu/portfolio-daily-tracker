"""Trading lessons the assistant suggests: written to memory only after the person confirms.

A lesson in memory is shown to the model in every later conversation, so the
model (and text it was shown, such as a news item) must not write one on its
own. Every free text the model extracts for memory (a lesson, rule,
resolution or behavioural observation, whatever category or wording it
has; see "Memory writes" in ``backend/services/agent_service.py``) and the
``propose_lesson`` tool both store a ``lesson`` proposal;
``core.proposals.confirm`` applies it.

Payload::

    {"description": "...",          what happened, 1-500 characters
     "lesson": "...",               what to keep doing or avoid (default: the description)
     "type": "failure" | "success" | "insight",
     "symbol": "SHA:600519" | null, exchange-qualified when given
     "date": "YYYY-MM-DD"}          default: today in Shanghai, fixed when proposed

``apply`` writes through the running assistant's ``MemoryManager`` (the one
that keeps the memory file in its own state, so a second instance would undo
the write on its next save). The write leaves ``desk.sqlite3``, so it goes
through ``proposals.external_effect`` and is idempotent by the proposal id
(``TradingLesson.source_id``): a retried confirmation never adds it twice. It
also adds nothing when memory already holds a lesson with the same text (two
pending copies made before the duplicate check below still store it once).

The person decides once
-----------------------
Two lesson texts are the same when their ``text_key`` is (``core.memory.models``:
case, whitespace, punctuation and full/half width do not count). ``propose``
does not store a text that

* memory already holds (whether a confirmed proposal or the person wrote it);
* the person confirmed before, even if they later deleted it from memory;
* the person rejected: a rejection is remembered for as long as the rejected
  proposal is stored, which is for good (``proposals.KEEP_DECIDED_KINDS``
  exempts decided lessons from the 90-day pruning) or until the person
  deletes that rejection (``proposals.forget``, 设置 › 交易偏好). Only a
  materially different text (another ``text_key``) can be proposed. The
  person can still write the same lesson themselves (设置 › 交易偏好);

and for a text that is already pending it returns that proposal, whatever
the date in its payload (the same text proposed after midnight is the same
proposal). Only an expired proposal, which nobody decided, does not count.

Profile proposals
-----------------
The fixed choices of the user profile (experience, trading style, risk
tolerance) and the preferred sectors are written at once only by the fixed
rules on the user's own first-person words (``MemoryExtractor``); whatever the
model extracts for them is a ``profile`` proposal, because the model may have
read it from a report about someone else::

    {"field": "experience_level" | "trading_style" | "risk_tolerance" | "preferred_sector",
     "value": an enum value of that field | one of MemoryExtractor.SECTORS}

``propose_profile`` stores nothing when memory already has that value, or when
the person rejected the same change before (remembered like a rejected lesson).
``apply`` writes it through the same ``MemoryManager`` (idempotent: setting a
value twice changes nothing).
"""
from __future__ import annotations

import re
import threading
import unicodedata
from datetime import date, datetime
from typing import Any, Callable, Optional

from core import proposals
from core.market.calendar import SHANGHAI
from core.market.instruments import InstrumentError, parse
from core.memory.models import ExperienceLevel, RiskTolerance, TradingStyle, UserMemory, lesson_key

KIND = 'lesson'
EFFECT = 'memory_lesson'
TYPES = {'failure': '失败教训', 'success': '成功经验', 'insight': '交易心得'}
MAX_TEXT = 500
FAILURE_WORDS = ('教训', '失败', '亏')
SUCCESS_WORDS = ('成功', '赚')

_provider: Optional[Callable[[], Any]] = None


def use_memory_manager(provider: Optional[Callable[[], Any]]) -> None:
    """Tell ``apply`` how to reach the running assistant's memory manager (``None`` to forget it)."""
    global _provider
    _provider = provider


def _manager():
    try:
        return _provider() if _provider is not None else None
    except Exception:
        return None


def lesson_type(content: Any) -> str:
    """The label proposed for an extracted lesson: ``failure``, ``success`` or ``insight``.

    Only a label shown on the proposal (失败教训/成功经验/交易心得): whether an
    extracted text is proposed at all never depends on its words. A text
    mentioning 教训/失败/亏 is labelled a failure, one mentioning 成功/赚 a
    success, anything else an insight.
    """
    if not isinstance(content, str):
        return 'insight'
    if any(word in content for word in FAILURE_WORDS):
        return 'failure'
    if any(word in content for word in SUCCESS_WORDS):
        return 'success'
    return 'insight'


def _text(value: Any, field: str, required: bool) -> str:
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise proposals.ProposalError(f'{field}必须是文字。')
    text = unicodedata.normalize('NFC', value)
    text = ''.join(' ' if unicodedata.category(ch)[0] == 'C' else ch for ch in text)
    text = re.sub(r'\s+', ' ', text).strip()
    if required and not text:
        raise proposals.ProposalError(f'{field}不能为空。')
    if len(text) > MAX_TEXT:
        raise proposals.ProposalError(f'{field}最多 {MAX_TEXT} 个字。')
    return text


def validate(payload: dict) -> dict:
    unknown = sorted(set(payload) - {'description', 'lesson', 'type', 'symbol', 'date'})
    if unknown:
        raise proposals.ProposalError(f'方案包含不支持的字段：{"、".join(map(str, unknown))}')
    description = _text(payload.get('description'), '事件描述', True)
    lesson = _text(payload.get('lesson'), '教训', False) or description
    kind = payload.get('type') or 'insight'
    if kind not in TYPES:
        raise proposals.ProposalError('type 必须是 failure（失败教训）、success（成功经验）或 insight（交易心得）。')
    symbol = payload.get('symbol') or None
    if symbol is not None:
        try:
            symbol = parse(symbol).ticker
        except InstrumentError:
            raise proposals.ProposalError('symbol 必须是带交易所前缀的代码，例如 SHA:600519，或者留空。') from None
    day = payload.get('date') or datetime.now(SHANGHAI).date().isoformat()
    try:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError:
        raise proposals.ProposalError('date 必须是 YYYY-MM-DD 格式的日期。') from None
    return {'description': description, 'lesson': lesson, 'type': kind, 'symbol': symbol, 'date': day}


def describe(payload: dict) -> str:
    about = f"（{payload['symbol']}）" if payload.get('symbol') else ''
    text = f"记录一条{TYPES[payload['type']]}{about}：{payload['lesson']}"
    if payload['description'] != payload['lesson']:
        text += f"（事件：{payload['description']}）"
    return text + f"。日期 {payload['date']}。确认后写入助手的记忆，以后的对话会参考它。"


def _write(manager, payload: dict, proposal_id: str) -> dict:
    added = manager.add_lesson(description=payload['description'], lesson_type=payload['type'],
                               lesson=payload['lesson'], symbol=payload.get('symbol'), date=payload['date'],
                               source_id=proposal_id, unique_text=True)
    return {'added': bool(added), 'source_id': proposal_id}


NOT_RUNNING = '助手服务没有运行，暂时无法写入记忆。方案仍在待确认列表中，请稍后再确认。'
KNOWN = '这条经验教训已经记录过了。'
CONFIRMED_BEFORE = '这条经验教训你之前已经确认过（之后可能在设置 › 交易偏好中删除了），助手不会再提出；需要时请在那里自己添加。'
REJECTED_BEFORE = '这条经验教训你之前已经放弃过，助手不会再提出；需要时请在设置 › 交易偏好中自己添加，或在那里删除这次放弃。'
PROFILE_CURRENT = '助手记忆里已经是这个设置，不需要修改。'
PROFILE_REJECTED = '同样的修改你之前已经放弃过，助手不会再提出；需要时请在设置 › 交易偏好中自己修改，或在那里删除这次放弃。'

# One propose at a time in this process, so that two concurrent proposals of one text cannot both pass the checks.
_proposing = threading.Lock()


def apply(payload: dict, proposal: dict) -> dict:
    manager = _manager()
    if manager is None:
        raise proposals.ProposalError(NOT_RUNNING)
    return proposals.external_effect(proposal, EFFECT, lambda: _write(manager, payload, proposal['id']))


def propose(payload: dict, source: str = 'agent:chat') -> dict:
    """Store a ``lesson`` proposal, or return the pending one with the same text; see "The person decides once".

    Raises ``ProposalError`` for a text memory holds or the person already
    confirmed or rejected. The duplicate checks are here and not in
    ``validate``: ``validate`` runs again on confirmation, when a retried
    write may already be in memory.
    """
    proposals.refuse_in_demo()
    checked = validate(dict(payload)) if isinstance(payload, dict) else payload
    if not isinstance(checked, dict):
        return proposals.propose(KIND, payload, source)
    key = lesson_key(checked)
    with _proposing:
        manager = _manager()
        if manager is not None and any(lesson_key(item) == key for item in manager.memory.history.lessons):
            raise proposals.ProposalError(KNOWN)
        if any(lesson_key(item['payload']) == key for item in proposals.of_kind(KIND, 'applied')):
            raise proposals.ProposalError(CONFIRMED_BEFORE)
        if any(lesson_key(item['payload']) == key for item in proposals.of_kind(KIND, 'rejected')):
            raise proposals.ProposalError(REJECTED_BEFORE)
        for item in proposals.of_kind(KIND, 'pending'):
            if lesson_key(item['payload']) == key:
                return item
        return proposals.propose(KIND, checked, source)


proposals.register_kind(KIND, validate, apply, describe, version='1')


# -- profile proposals ------------------------------------------------------------

PROFILE_KIND = 'profile'
PROFILE_EFFECT = 'memory_profile'
_CHOICES = {'experience_level': ('经验等级', ExperienceLevel, UserMemory._translate_experience),
            'trading_style': ('交易风格', TradingStyle, UserMemory._translate_style),
            'risk_tolerance': ('风险偏好', RiskTolerance, UserMemory._translate_risk)}
PROFILE_FIELDS = tuple(_CHOICES) + ('preferred_sector',)


def _sectors():
    from core.memory.extractor import MemoryExtractor
    return MemoryExtractor.SECTORS


def validate_profile(payload: dict) -> dict:
    unknown = sorted(set(payload) - {'field', 'value'})
    if unknown:
        raise proposals.ProposalError(f'方案包含不支持的字段：{"、".join(map(str, unknown))}')
    field = payload.get('field')
    if field not in PROFILE_FIELDS:
        raise proposals.ProposalError('field 必须是 experience_level、trading_style、risk_tolerance 或 preferred_sector。')
    value = payload.get('value')
    allowed = _sectors() if field == 'preferred_sector' else tuple(member.value for member in _CHOICES[field][1])
    if not isinstance(value, str) or value not in allowed:
        raise proposals.ProposalError(f'value 必须是以下之一：{"、".join(allowed)}。')
    return {'field': field, 'value': value}


def describe_profile(payload: dict) -> str:
    field, value = payload['field'], payload['value']
    if field == 'preferred_sector':
        change = f'把「{value}」加入助手记忆中你的偏好板块'
    else:
        label, choices, translate = _CHOICES[field]
        change = f'把助手记忆中你的{label}改为「{translate(None, choices(value))}」'
    return change + '。确认后写入助手的记忆，以后的对话会参考它。'


def _current(manager, field: str, value: str) -> bool:
    profile = manager.memory.profile
    if field == 'preferred_sector':
        return value in profile.preferred_sectors
    current = getattr(profile, field, None)
    return getattr(current, 'value', current) == value


def _write_profile(manager, payload: dict) -> dict:
    if payload['field'] == 'preferred_sector':
        manager.add_preferred_sector(payload['value'])
    else:
        manager.update_profile(**{payload['field']: payload['value']})
    return {'field': payload['field'], 'value': payload['value']}


def apply_profile(payload: dict, proposal: dict) -> dict:
    manager = _manager()
    if manager is None:
        raise proposals.ProposalError(NOT_RUNNING)
    return proposals.external_effect(proposal, PROFILE_EFFECT, lambda: _write_profile(manager, payload))


def propose_profile(field: Any, value: Any, source: str = 'agent:chat') -> dict:
    """Store a ``profile`` proposal; see "Profile proposals". Raises ``ProposalError`` when there is nothing to propose."""
    proposals.refuse_in_demo()
    checked = validate_profile({'field': field, 'value': value})
    with _proposing:
        manager = _manager()
        if manager is not None and _current(manager, checked['field'], checked['value']):
            raise proposals.ProposalError(PROFILE_CURRENT)
        if any(item['payload'] == checked for item in proposals.of_kind(PROFILE_KIND, 'rejected')):
            raise proposals.ProposalError(PROFILE_REJECTED)
        return proposals.propose(PROFILE_KIND, checked, source)


proposals.register_kind(PROFILE_KIND, validate_profile, apply_profile, describe_profile, version='1')
