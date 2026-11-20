"""Card pages added by extensions: a module named in ``TRACKER_EXTENSIONS`` that defines ``workbench_pages()``.

The card service imports each module named in ``TRACKER_EXTENSIONS`` (the backend's variable, parsed by
``backend.extensions.configured``, but read from this process's environment only, never from the repository's
``.env``), once, at start, and asks only ``workbench_pages()``: a module without
it adds nothing here, and its backend hooks (routers, background tasks, ``on_close_report``) are never used in
this process. A module that cannot be imported, or whose pages are refused, is logged with the exception type
and stack frames (never the message) and left out; the service starts without it. Without the variable, or
without any module defining ``workbench_pages()``, the service is exactly as before.

``workbench_pages()`` returns a list of mappings, each one page (every key optional)::

    {'actions':  {'news.open': handle},                 # pressed on a card this service rendered
     'views':    {'news_list': ('市场资讯', build)},      # cards this service renders for the page
     'external': {'news.page': click},                  # pressed on a card another process sent
     'entry':    ('市场资讯', 'news.open')}               # a button on the home card

* ``handle(request: PageRequest) -> (view, model)``: like a built-in action, it runs in the service's worker
  after the press was checked (allow-listed user, the card's nonce, the card's session); ``view`` must be one
  of the same page's views, and the service renders it as the next state of that card. An action whose name
  ends with ``.confirm`` is accepted only from a card of one of the page's ``_preview`` views, and that card
  lives 15 minutes, as a built-in preview does.
* ``build(model, context) -> [element, ...]``: the body of a Card 2.0, as the built-in views build it; the
  service adds the header and enforces the card budget. Buttons for the page's own actions (or ``home``):
  ``button(context, action, label)``, which binds them to the card's nonces. Text from outside:
  ``markdown(text)``, escaped.
* ``click(request: CardClick) -> answer``: answered at once, inside Feishu's callback (keep it well under a
  second, no network). ``answer`` is ``{}``, ``{'toast': {'type': 'info'|'success'|'warning'|'error',
  'content': text}}`` and/or ``{'card': {'type': 'raw', 'data': card}}`` (the pressed card replaced, within the
  same budget).

The model of a view is stored with the card, so it is JSON (no ``NaN``); its ``title`` and ``subtitle`` set the
card's header, and ``notice`` is for built-in views only. A handler that raises ``Refused`` (the service's own
refusal) shows its message as a built-in refusal does; any other exception shows a fixed message and is
logged. A page may not use a built-in action or view name, or the service's namespaces (``RESERVED_ACTIONS``,
``RESERVED_VIEWS``), or a name another page took: such a module's pages are refused as a whole.

**What an external click means.** Such a card was sent by another process with the bot's credentials, so the
press carries no nonce of this service. The service vouches only that the press came through the bot's
authenticated bridge, from a user on the workbench's allow-list, with an action name a page declared
``external``. It does not vouch that the card was sent by the page's own module, that the user is the one it
was sent to, that the button's values are the ones the module wrote, or that the press is fresh (an old card
can be pressed again at any time, and Feishu redelivers). The page must check that ``message`` (and ``chat``
or ``actor``) is a card it recorded as sent, to that chat or user, and still current; treat every value as
untrusted input; and only do what is harmless to repeat (show another page of the same content). Anything
that changes data belongs on a card this service renders, through a ``_preview`` view and its ``.confirm``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import importlib
import json
import logging
import re
import traceback
from typing import Any, Optional

from .store import Rejected

logger = logging.getLogger(__name__)

# The service's own refusal: a handler raises it to show its message (Chinese, safe to show).
Refused = Rejected

ACTION = re.compile(r'[a-z][a-z0-9_]{0,30}(?:\.[a-z0-9_]{1,30}){1,3}')
VIEW = re.compile(r'[a-z][a-z0-9_]{0,40}')
# The service's own namespaces, now and for later releases: a page never takes a name in them.
RESERVED_ACTIONS = ('home.', 'daily.', 'assets.', 'research.', 'reports.')
RESERVED_VIEWS = ('home_', 'daily_', 'assets_', 'research_', 'reports_', 'error_')
PAGE_KEYS = {'actions', 'views', 'external', 'entry'}
MAX_VALUE_BYTES = 4000
MAX_TOAST = 200
TOASTS = {'info', 'success', 'warning', 'error'}
FAILED = '操作未完成，请稍后再试。'


@dataclass(frozen=True)
class PageRequest:
    """A press on a card this service rendered for one of the page's views; nonce and session already checked."""
    action: str
    actor: str
    fields: Mapping[str, str]       # the card's form values
    view: str                       # the view of the card that was pressed
    model: Mapping[str, Any]        # the model that card was rendered from (stored by the service)


@dataclass(frozen=True)
class CardClick:
    """A press on a card another process sent with the bot; see "What an external click means"."""
    action: str
    actor: str                      # on the workbench's allow-list
    chat: str                       # Feishu's open_chat_id of the card ('' when absent)
    message: str                    # Feishu's open_message_id of the card ('' when absent)
    value: Mapping[str, Any]        # the button's value, as the card's sender wrote it
    fields: Mapping[str, str]       # form values, if the card had a form


@dataclass
class Page:
    module: str
    actions: dict = field(default_factory=dict)
    views: dict = field(default_factory=dict)
    external: dict = field(default_factory=dict)
    entry: Optional[tuple] = None


def _frames(exc: BaseException) -> str:
    missing = f" (module {exc.name})" if isinstance(exc, ImportError) and exc.name else ""
    return missing + "\n" + "".join(traceback.format_tb(exc.__traceback__)).rstrip()


def button(context, action: str, label: str, *, primary: bool = False) -> dict:
    """A button for a page's action, or ``home``, bound to the card's nonce (a built-in view's button).

    Not for other built-in actions: they act on the model of their own views (a ``daily.confirm`` on a page's
    card would confirm what the page put there), so a page's card cannot carry them.
    """
    from .cards import _button
    if action != 'home' and (context.pages is None or not context.pages.handles(action)):
        raise ValueError('A page button is for a page action or home')
    return _button(context, action, label, primary=primary)


def markdown(text: Any, *, caption: bool = False) -> dict:
    """A text element; ``text`` is escaped (no links, mentions or formatting from outside)."""
    from .cards import _escape, _markdown
    return _markdown(_escape(text), caption=caption)


class Pages:
    """The pages the card service has: none without extensions."""

    def __init__(self, pages=()):
        self.pages = list(pages)
        self.actions = {name: (page, handler) for page in self.pages for name, handler in page.actions.items()}
        self.external = {name: (page, handler) for page in self.pages for name, handler in page.external.items()}
        self.views = {name: view for page in self.pages for name, view in page.views.items()}
        self.owners = {name: page for page in self.pages for name in page.views}
        self.entries = [page.entry for page in self.pages if page.entry]

    def action_names(self) -> tuple:
        return tuple(self.actions)

    def handles(self, action: str) -> bool:
        return action in self.actions

    def execute(self, action: str, actor: str, payload: Mapping) -> tuple:
        """The next view and model of the card for a press of ``action``."""
        page, handler = self.actions[action]
        view = payload.get('view', '')
        if action.endswith('.confirm') and not (isinstance(view, str) and view.endswith('_preview')
                                                and self.owners.get(view) is page):
            raise Rejected('请先预览，再在预览卡片上确认。')
        model = payload.get('model') if isinstance(payload.get('model'), Mapping) else {}
        fields = payload.get('fields') if isinstance(payload.get('fields'), Mapping) else {}
        result = handler(PageRequest(action, actor, dict(fields), view if isinstance(view, str) else '', dict(model)))
        if (not isinstance(result, (tuple, list)) or len(result) != 2 or self.owners.get(result[0]) is not page
                or not isinstance(result[1], Mapping)):
            logger.error('workbench page %s: %s returned no view of its own page', page.module, action)
            raise Rejected(FAILED)
        return result[0], dict(result[1])

    def wants(self, event: Mapping) -> bool:
        """Whether a card press without a nonce is for an external action of a page."""
        value = event.get('value')
        return isinstance(value, Mapping) and isinstance(value.get('action'), str) and value['action'] in self.external

    def click(self, event: Mapping) -> dict:
        """The callback's answer to a press on a card another process sent (``wants``)."""
        value = dict(event['value'])
        action = value['action']
        page, handler = self.external[action]
        try:
            size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        except (TypeError, ValueError):
            size = MAX_VALUE_BYTES + 1
        if size > MAX_VALUE_BYTES:
            raise Rejected('卡片数据无效。')
        request = CardClick(action, event['actor'], event.get('chat') or '', event.get('message') or '', value,
                            dict(event.get('fields') or {}))
        try:
            answer = handler(request)
        except Rejected:
            raise
        except Exception as exc:
            logger.error('workbench page %s: %s failed: %s%s', page.module, action, type(exc).__name__, _frames(exc))
            raise Rejected(FAILED) from None
        return self._answer(page, action, answer)

    @staticmethod
    def _answer(page, action, answer) -> dict:
        from .cards import check_budget
        try:
            if not isinstance(answer, Mapping) or set(answer) - {'toast', 'card'}:
                raise ValueError('an answer has only toast and card')
            result = {}
            if 'toast' in answer:
                toast = answer['toast']
                if (not isinstance(toast, Mapping) or toast.get('type') not in TOASTS
                        or not isinstance(toast.get('content'), str) or not toast['content']
                        or len(toast['content']) > MAX_TOAST):
                    raise ValueError('a toast has a type and a short content')
                result['toast'] = {'type': toast['type'], 'content': toast['content']}
            if 'card' in answer:
                card = answer['card']
                if not isinstance(card, Mapping) or card.get('type') != 'raw' or not isinstance(card.get('data'), Mapping):
                    raise ValueError("a card is {'type': 'raw', 'data': card}")
                check_budget(card['data'])
                result['card'] = {'type': 'raw', 'data': card['data']}
            return result
        except (TypeError, ValueError) as exc:
            logger.error('workbench page %s: %s gave an answer that cannot be sent: %s', page.module, action,
                         type(exc).__name__)
            raise Rejected(FAILED) from None


class PageRefused(ValueError):
    """A page definition this service refuses; the message is a fixed text, safe to log."""


def _page(module: str, entry: Any, taken_actions: set, taken_views: set) -> Page:
    """One page of ``workbench_pages()``, checked; ``PageRefused`` names what is wrong."""
    if not isinstance(entry, Mapping) or set(entry) - PAGE_KEYS:
        raise PageRefused('a page is a mapping of actions, views, external and entry')
    page = Page(module)
    for key, target in (('actions', page.actions), ('external', page.external)):
        handlers = entry.get(key) or {}
        if not isinstance(handlers, Mapping):
            raise PageRefused(f'{key} is a mapping of action names to functions')
        for name, handler in handlers.items():
            if not isinstance(name, str) or not ACTION.fullmatch(name) or name.startswith(RESERVED_ACTIONS):
                raise PageRefused(f'{key}: not an action name a page may use')
            if name in taken_actions:
                raise PageRefused(f'{key}: {name} is built in or taken by another page')
            if not callable(handler):
                raise PageRefused(f'{key}: {name} is not callable')
            target[name] = handler
            taken_actions.add(name)
    views = entry.get('views') or {}
    if not isinstance(views, Mapping):
        raise PageRefused('views is a mapping of view names to (title, function)')
    for name, view in views.items():
        if not isinstance(name, str) or not VIEW.fullmatch(name) or name.startswith(RESERVED_VIEWS):
            raise PageRefused('views: not a view name a page may use')
        if name in taken_views:
            raise PageRefused(f'views: {name} is built in or taken by another page')
        if (not isinstance(view, (tuple, list)) or len(view) != 2 or not isinstance(view[0], str)
                or not view[0] or not callable(view[1])):
            raise PageRefused(f'views: {name} is not (title, function)')
        page.views[name] = (view[0], view[1])
        taken_views.add(name)
    if entry.get('entry') is not None:
        label_action = entry['entry']
        if (not isinstance(label_action, (tuple, list)) or len(label_action) != 2 or not isinstance(label_action[0], str)
                or not label_action[0] or label_action[1] not in page.actions):
            raise PageRefused('entry is (label, one of the page\'s own actions)')
        page.entry = (label_action[0], label_action[1])
    return page


def builtin_names() -> tuple:
    """The action and view names of the service itself, which no page may take."""
    from .cards import ACTIONS as CARD_ACTIONS, _VIEWS
    from .daily_cards import VIEWS as DAILY_VIEWS
    from .service import ACTIONS as SERVICE_ACTIONS
    return set(SERVICE_ACTIONS) | set(CARD_ACTIONS), set(_VIEWS) | set(DAILY_VIEWS)


def load(environ: Optional[Mapping] = None) -> Pages:
    """Import the modules named in ``TRACKER_EXTENSIONS`` and collect their pages; never raises."""
    from backend.extensions import _NAME, ENV, configured
    if environ is not None:
        names = [name for name in dict.fromkeys(part.strip() for part in environ.get(ENV, '').split(',')) if name]
    else:
        names = configured()
    if not names:
        return Pages()
    actions, views = builtin_names()
    pages = []
    for name in names:
        if not _NAME.fullmatch(name):
            logger.error('extension %r: not a Python module name; no workbench pages', name[:200])
            continue
        try:
            module = importlib.import_module(name)
        except (Exception, SystemExit) as exc:
            logger.error('extension %s not loaded by the card service: import failed: %s%s', name,
                         type(exc).__name__, _frames(exc))
            continue
        provider = getattr(module, 'workbench_pages', None)
        if provider is None:
            continue   # a module for the backend only
        taken_actions, taken_views = set(actions), set(views)
        try:
            if not callable(provider):
                raise TypeError('workbench_pages must be callable')
            entries = provider()
            if not isinstance(entries, (list, tuple)):
                raise TypeError('workbench_pages() returns a list of pages')
            found = [_page(name, entry, taken_actions, taken_views) for entry in entries]
        except (Exception, SystemExit) as exc:
            refused = isinstance(exc, PageRefused)
            logger.error('extension %s: workbench pages refused: %s%s', name,
                         str(exc) if refused else type(exc).__name__, '' if refused else _frames(exc))
            continue
        actions, views = taken_actions, taken_views
        pages += found
        logger.info('extension %s: %s workbench page(s)', name, len(found))
    return Pages(pages)
