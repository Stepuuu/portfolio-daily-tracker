"""Bounded conversation history for the trader agent.

``window(messages)`` keeps what is sent to the model small and predictable:

* the system prompt (the first message when its role is ``system``) is kept;
* the conversation is cut into turns. A turn starts at a user message that
  is not only tool results and runs until the next such message, so a tool
  call and its result always stay together;
* ``max_chars`` is the budget of everything sent: the system prompt (the
  rules, the memory block, the live context, which ``core.agent_context``
  caps at ``MAX_CONTEXT_CHARS``, and the skills section, capped at
  ``SKILLS_MAX_CHARS``), the notes below and the conversation. The newest
  turn is always kept, then older turns while there are at most
  ``max_turns`` and everything stays within ``max_chars``. The first turn
  that does not fit ends the window; nothing older is kept;
* an oversized newest turn: the system prompt is kept whole and so is the
  newest turn, except that its first message (the user's own message, when
  it is text) is shortened to what is left of the budget, but never to less
  than ``MIN_KEPT`` characters: its beginning and its end are kept and the
  middle is replaced by a visible marker (``SHORTENED_MARK``) that says how
  many characters were left out. ``window`` then reports
  ``{"total", "omitted"}`` in its ``shortened`` attribute, and the agent adds
  ``shortened_note(...)`` to its reply, so the user learns their message was
  shortened. The stored conversation keeps the whole message;
* when turns were left out, a one-line note says how many; when the window
  has earlier turns at all, a fixed note says that prices in them may be out
  of date. Both are appended to the system prompt, because some providers
  keep only one system message.

Deterministic: the same messages give the same window. No model call.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from core.agent_context import MAX_CHARS as MAX_CONTEXT_CHARS

MAX_TURNS = 12
# Everything sent: about 1,500 characters of rules, the memory block (under 6,000), the live
# context (at most MAX_CONTEXT_CHARS = 12,000), the skills section (at most 4,000,
# agents/trader/prompts.py SKILLS_MAX_CHARS) and the conversation, which keeps at least
# about 12,000 characters when all of them are full.
MAX_CHARS = 36000
# the shortest a shortened newest message gets, whatever the system prompt leaves
MIN_KEPT = 2000
SHORTENED_MARK = '\n\n【……用户消息过长，此处省略了 {count} 个字……】\n\n'
SHORTENED_NOTE = ('（你的消息有 {total} 个字，超出了一次能发给模型的长度：发送时保留了开头和结尾，省略了其余 {omitted} 个字。'
                  '如果省略的部分很重要，请分几次发送。）')
STALE_NOTE = '更早对话中的价格可能已过期，以本轮上下文和工具结果为准'
OMITTED_NOTE = '为控制长度，已省略更早的 {count} 轮对话。'
NOTES_HEAD = '【对话历史说明】'
# the longest the notes can be, reserved in the budget before they are known
_NOTES_MAX = len('\n\n' + NOTES_HEAD + OMITTED_NOTE.format(count=10 ** 9) + STALE_NOTE)


def _size(message: Dict[str, Any]) -> int:
    content = message.get('content')
    if isinstance(content, str):
        return len(content)
    try:
        return len(json.dumps(content, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(content))


def _only_tool_results(message: Dict[str, Any]) -> bool:
    content = message.get('content')
    return (isinstance(content, list) and bool(content)
            and all(isinstance(block, dict) and block.get('type') == 'tool_result' for block in content))


class Window(list):
    """The messages to send; ``shortened`` is ``{"total", "omitted"}`` when the newest message was shortened."""
    shortened: Optional[Dict[str, int]] = None


def shorten(text: str, room: int):
    """(``text`` cut to ``room`` characters with ``SHORTENED_MARK`` in its middle, characters left out).

    ``room`` is raised to ``MIN_KEPT``; a text that fits is returned as it is, with 0.
    """
    room = max(room, MIN_KEPT)
    if not isinstance(text, str) or len(text) <= room:
        return text, 0
    omitted = len(text) - room
    while True:                              # the mark's own length depends on the count it shows
        needed = len(text) - (room - len(SHORTENED_MARK.format(count=omitted)))
        if needed <= omitted:
            break
        omitted = needed
    keep = len(text) - omitted
    head = keep // 2
    return text[:head] + SHORTENED_MARK.format(count=omitted) + text[len(text) - (keep - head):], omitted


def shortened_note(shortened: Optional[Dict[str, int]]) -> str:
    """``"\n\n" + SHORTENED_NOTE`` for a ``Window.shortened``, else ``""``."""
    if not shortened:
        return ''
    return '\n\n' + SHORTENED_NOTE.format(**shortened)


def turns(messages: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """The conversation (no system message) cut into turns; see the module docstring."""
    grouped: List[List[Dict[str, Any]]] = []
    for message in messages:
        starts = message.get('role') == 'user' and not _only_tool_results(message)
        if starts or not grouped:
            grouped.append([message])
        else:
            grouped[-1].append(message)
    return grouped


def window(messages: Sequence[Dict[str, Any]], max_turns: int = MAX_TURNS,
           max_chars: int = MAX_CHARS) -> Window:
    """``messages`` with only the newest turns that fit; see the module docstring.

    ``max_chars`` counts the system prompt, the notes (their longest form,
    reserved up front) and the conversation's text (tool blocks as JSON). A
    newest turn that does not fit is still kept; its first message, when it
    is the user's text, is shortened in its middle (see the module
    docstring), never below ``MIN_KEPT`` characters.
    """
    messages = list(messages)
    system = messages[0] if messages and messages[0].get('role') == 'system' else None
    grouped = turns(messages[1:] if system is not None else messages)
    kept: List[List[Dict[str, Any]]] = []
    used = (_size(system) if system is not None else 0) + _NOTES_MAX
    for turn in reversed(grouped):
        size = sum(_size(message) for message in turn)
        if kept and (len(kept) >= max_turns or used + size > max_chars):
            break
        kept.append(turn)
        used += size
    shortened = None
    if len(kept) == 1 and used > max_chars:
        newest = kept[0]
        first = newest[0]
        if first.get('role') == 'user' and isinstance(first.get('content'), str):
            text, cut = shorten(first['content'], max_chars - (used - len(first['content'])))
            if cut:
                kept[0] = [{**first, 'content': text}] + newest[1:]
                shortened = {'total': len(first['content']), 'omitted': cut}
    kept.reverse()
    omitted = len(grouped) - len(kept)

    notes = []
    if omitted:
        notes.append(OMITTED_NOTE.format(count=omitted))
    if omitted or len(kept) > 1:
        notes.append(STALE_NOTE)
    head: List[Dict[str, Any]] = []
    if system is not None or notes:
        content = system.get('content', '') if system is not None else ''
        if notes:
            addition = NOTES_HEAD + ''.join(notes)
            content = f'{content}\n\n{addition}' if content else addition
        head = [{**(system or {'role': 'system'}), 'content': content}]
    result = Window(head + [message for turn in kept for message in turn])
    result.shortened = shortened
    return result
