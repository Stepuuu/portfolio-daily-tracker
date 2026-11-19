"""Extensions: separately installed Python modules that add to the running application.

``TRACKER_EXTENSIONS=pkg.mod,other.mod`` names modules found on the normal
Python path (an installed package, or a directory on ``PYTHONPATH``).
``backend/main.py`` imports them once, when it is imported itself, after the
built-in routers are mounted. Nothing is imported, mounted or started when the
variable is unset or empty. docs/EXTENSIONS.md is the author's guide.

What a module may define, each optional:

* ``routers()``: ``[(APIRouter, prefix, tags), ...]``, as in
  ``backend/features.py``. Mounted after the built-in routes, which therefore
  win a clash of method and path, and behind the same middleware (the host
  allow-list, CORS);
* ``background_tasks()``: factories ``f(app)`` returning an async context
  manager, as ``features.background_tasks()``; entered at start-up after the
  built-in ones and left before them;
* ``on_close_report(report)``: called once per finished close report, also when
  a day's report is made again with other content (``core/close_reports.py``);
* and, in its module body, registrations through the existing registries:
  ``core.agent_tools.register`` (chat tools: ``build_all`` runs at start-up, so
  after this import) and ``core.notify.register_deliverer`` (delivery channels:
  registered before anything is published).

Failures never take the API down and are never silent. They are logged with
the exception type and the stack frames (never the message, which can quote
data or credentials) and listed by ``GET /api/app/status`` (``status()``):

* a module that cannot be imported, or whose ``routers()``,
  ``background_tasks()`` or ``on_close_report`` cannot be used, is ``failed`` and
  contributes nothing: the tool factories and deliverers it registered are
  removed again (``_attributed``: those its own module defines, and those added
  while it was imported that no other named extension defines, so that another
  extension it imported keeps its own);
* a router with its own lifespan or start-up/shutdown handlers is refused
  (stage ``router_lifespan``): FastAPI would run them inside the application's
  start-up, where a failure stops the whole API. Start-up work belongs in
  ``background_tasks()``;
* a background task that raises when it starts, or takes longer than
  ``START_SECONDS``, is left out; one that raises or hangs when it stops is
  logged; both, and every ``on_close_report`` call that raised, are listed under
  the extension's ``errors``. The time limit only covers code that awaits: a
  synchronous call that blocks holds the event loop, and nothing here can
  interrupt it.

Modes. Demo mode (``TRACKER_DEMO_MODE=1``) loads no extension at all and lists
each as ``skipped``: the demo must never change data, and the application cannot
tell whether an extension's code, its GET routes included, only reads.
Lab-only mode (``TRACKER_LAB_ONLY=1``) loads them and mounts their routers, as it
mounts the built-in ones, but starts no background task and calls no
``on_close_report``: it starts no background task of its own either.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
import importlib
import logging
import os
import re
import sys
import traceback
from typing import Any, Callable, Deque, List, Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

ENV = "TRACKER_EXTENSIONS"
# Seconds a background task of an extension may take to start, and to stop.
START_SECONDS = 10.0
# Runtime failures kept per extension for the status (the newest).
MAX_ERRORS = 5
_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


def _frames(exc: BaseException) -> str:
    """For the log, after the exception's type: the missing module of an import, then the stack frames.

    Never the exception's message: it can quote data or credentials.
    """
    missing = f" (module {exc.name})" if isinstance(exc, ImportError) and exc.name else ""
    return missing + "\n" + "".join(traceback.format_tb(exc.__traceback__)).rstrip()


class Refused(Exception):
    """A definition the loader refuses on purpose; ``reason`` is a fixed text for the status."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


ROUTER_LIFESPAN = "a router has its own lifespan or start-up/shutdown handlers; put start-up work in background_tasks()"


def _error(exc: BaseException) -> str:
    """What the status shows of an exception: its type, and the missing module of an import."""
    if isinstance(exc, Refused):
        return exc.reason
    kind = type(exc).__name__
    return f"{kind} ({exc.name})" if isinstance(exc, ImportError) and exc.name else kind


@dataclass
class Extension:
    name: str
    status: str                        # 'loaded' | 'failed' | 'skipped'
    stage: Optional[str] = None        # failed: what failed (name, import, routers, background_tasks, ...)
    error: Optional[str] = None        # failed: the exception; skipped: why
    routers: List[APIRouter] = field(default_factory=list)
    tasks: List[Callable] = field(default_factory=list)
    on_close_report: Optional[Callable] = None
    tools: List[Callable] = field(default_factory=list)        # tool factories it registered
    deliverers: List[Callable] = field(default_factory=list)   # deliverers it registered
    started: bool = False
    errors: Deque[dict] = field(default_factory=lambda: deque(maxlen=MAX_ERRORS))

    def provides(self) -> List[str]:
        return [label for label, present in (
            ("routers", self.routers), ("background_tasks", self.tasks), ("agent_tools", self.tools),
            ("deliverers", self.deliverers), ("on_close_report", self.on_close_report is not None)) if present]

    def failure(self, stage: str, exc: BaseException, **context) -> None:
        """Record (and log) a runtime failure; the extension stays loaded."""
        from core import desk_store
        self.errors.append({"stage": stage, "error": _error(exc), "at": desk_store.now(), **context})
        where = "".join(f" {key}={value}" for key, value in context.items())
        logger.warning("extension %s: %s failed%s: %s%s", self.name, stage, where, type(exc).__name__, _frames(exc))

    def view(self) -> dict:
        if self.status == "skipped":
            return {"name": self.name, "status": "skipped", "reason": self.error}
        if self.status == "failed":
            return {"name": self.name, "status": "failed", "stage": self.stage, "error": self.error}
        item = {"name": self.name, "status": "loaded", "provides": self.provides()}
        if self.tasks or self.on_close_report is not None:
            item["started"] = self.started
        item["errors"] = list(self.errors)
        return item


_extensions: List[Extension] = []
# Whether ``load()`` was given at least one module name (the status lists extensions only then).
_configured = False


def _demo() -> bool:
    return os.environ.get("TRACKER_DEMO_MODE") == "1"


def configured() -> List[str]:
    """The module names in ``TRACKER_EXTENSIONS``, each once, in order."""
    names = (part.strip() for part in os.environ.get(ENV, "").split(","))
    return list(dict.fromkeys(name for name in names if name))


def loaded() -> List[Extension]:
    return [extension for extension in _extensions if extension.status == "loaded"]


def load() -> List[Extension]:
    """Import the modules named in ``TRACKER_EXTENSIONS``; ``backend/main.py`` calls it once, at import."""
    global _extensions, _configured
    names = configured()
    _configured = bool(names)
    if names and _demo():
        logger.warning("demo mode: %s extension(s) named in %s not loaded", len(names), ENV)
        _extensions = [Extension(name[:200], "skipped", error="demo") for name in names]
        return list(_extensions)
    if names:
        # The application's own tool modules first: what registers during an extension's import is its own.
        from core import agent_tools
        agent_tools.discover()
    _extensions = [_load(name, [other for other in names if other != name]) for name in names]
    return list(_extensions)


def _defined_in(function: Any, name: str) -> bool:
    """Whether ``function`` was defined in module ``name`` or in a module inside it."""
    owner = getattr(function, "__module__", None) or type(function).__module__ or ""
    return owner == name or owner.startswith(name + ".")


def _attributed(name: str, others: List[str], before: list, after: list) -> list:
    """What extension ``name`` registered, between ``before`` and ``after`` (a registry's contents).

    Registrations count as the extension's whose module defines them: what was
    added while it was loaded, except what another named extension defined (it
    imported that one, which stays loaded and keeps them), and what its own
    module added earlier (another extension imported it first).
    """
    def another(item):
        return any(_defined_in(item, other) and other in sys.modules for other in others)

    earlier = [item for item in before if _defined_in(item, name)]
    return earlier + [item for item in after if item not in before and not another(item)]


def _load(name: str, others: List[str]) -> Extension:
    if not _NAME.fullmatch(name):
        logger.error("extension %r not loaded: not a Python module name", name[:200])
        return Extension(name[:200], "failed", "name", "invalid module name")
    from core import agent_tools, notify
    tools, deliverers = agent_tools.registered(), notify.registered_deliverers()

    def registered():
        return (_attributed(name, others, tools, agent_tools.registered()),
                _attributed(name, others, deliverers, notify.registered_deliverers()))

    stage = "import"
    try:
        module = importlib.import_module(name)
        stage = "routers"
        entries = _router_entries(module)
        stage = "router_lifespan"
        _refuse_lifespans(entries)
        stage = "routers"
        routers = _gather(entries)
        stage = "background_tasks"
        tasks = _tasks(module)
        stage = "on_close_report"
        hook = getattr(module, "on_close_report", None)
        if hook is not None and not callable(hook):
            raise TypeError("on_close_report must be callable")
    except (Exception, SystemExit) as exc:
        _unregister(*registered())
        logger.error("extension %s not loaded: %s failed: %s%s", name, stage, type(exc).__name__, _frames(exc))
        return Extension(name, "failed", stage, _error(exc))
    own_tools, own_deliverers = registered()
    extension = Extension(name, "loaded", routers=routers, tasks=tasks, on_close_report=hook,
                          tools=own_tools, deliverers=own_deliverers)
    logger.info("extension %s loaded: %s", name, ", ".join(extension.provides()) or "nothing to add")
    return extension


def _unregister(tools: list, deliverers: list) -> None:
    from core import agent_tools, notify
    for factory in tools:
        agent_tools.unregister(factory)
    for deliverer in deliverers:
        notify.unregister_deliverer(deliverer)


def _router_entries(module) -> list:
    """The ``(router, prefix, tags)`` entries of the module's ``routers()``, checked."""
    function = getattr(module, "routers", None)
    if function is None:
        return []
    entries = []
    for entry in function():
        router, prefix, tags = entry
        if (not isinstance(router, APIRouter) or not isinstance(prefix, str)
                or isinstance(tags, str) or not all(isinstance(tag, str) for tag in tags)):
            raise TypeError("routers() must return (APIRouter, prefix, tags) entries")
        entries.append((router, prefix, list(tags)))
    return entries


def _runs_nothing(lifespan: Any, default: type) -> bool:
    """Whether a router's lifespan only runs (empty) start-up and shutdown handlers.

    A router without a lifespan has ``default``'s. FastAPI merges the lifespan of
    every router it includes into the including router's
    (``fastapi.routing._merge_lifespan_context``), so a merge of such lifespans
    runs nothing either. Anything else, including a merge in a shape this does
    not know, counts as a lifespan of its own.
    """
    if isinstance(lifespan, default):
        router = getattr(lifespan, "_router", None)
        return not (getattr(router, "on_startup", None) or getattr(router, "on_shutdown", None))
    merged = getattr(lifespan, "__wrapped__", None)
    code = getattr(merged, "__code__", None)
    if code is None or code.co_name != "merged_lifespan" or not merged.__closure__:
        return False
    parts = dict(zip(code.co_freevars, (cell.cell_contents for cell in merged.__closure__)))
    return all(name in parts and _runs_nothing(parts[name], default) for name in ("original_context", "nested_context"))


def _refuse_lifespans(entries: list) -> None:
    """Refuse a router with its own lifespan or start-up and shutdown handlers (``Refused``).

    FastAPI runs them inside the application's start-up, where a failure stops
    the whole API; an extension's start-up work belongs in ``background_tasks()``.
    """
    default = type(APIRouter().lifespan_context)
    for router, _prefix, _tags in entries:
        if (getattr(router, "on_startup", None) or getattr(router, "on_shutdown", None)
                or not _runs_nothing(router.lifespan_context, default)):
            raise Refused(ROUTER_LIFESPAN)


def _gather(entries: list) -> List[APIRouter]:
    """The routers in one router, so that mounting them is all or nothing.

    Its lifespan and handlers are replaced by empty ones, so that nothing of a
    router's start-up that ``_refuse_lifespans`` did not recognise reaches the
    application's start-up either.
    """
    if not entries:
        return []
    gathered = APIRouter()
    for router, prefix, tags in entries:
        gathered.include_router(router, prefix=prefix, tags=tags)   # FastAPI checks the prefix here
    gathered.lifespan_context = APIRouter().lifespan_context
    for handlers in ("on_startup", "on_shutdown"):
        if isinstance(getattr(gathered, handlers, None), list):
            setattr(gathered, handlers, [])
    return [gathered]


def _tasks(module) -> List[Callable]:
    function = getattr(module, "background_tasks", None)
    if function is None:
        return []
    factories = list(function())
    if not all(callable(factory) for factory in factories):
        raise TypeError("background_tasks() must return callables f(app)")
    return factories


def mount(app) -> None:
    """Include the loaded extensions' routers in ``app``, after the built-in ones.

    The router takes the first route that matches a request's method and path,
    so a built-in route is never replaced by an extension's route of the same
    method and path (that one is never reached).
    """
    for extension in loaded():
        try:
            for router in extension.routers:
                app.include_router(router)
        except Exception as exc:   # checked when it was loaded; not expected
            _unregister(extension.tools, extension.deliverers)
            extension.status, extension.stage, extension.error = "failed", "routers", _error(exc)
            extension.tasks, extension.on_close_report = [], None
            logger.error("extension %s not mounted: %s%s", extension.name, type(exc).__name__, _frames(exc))


async def _start(stack: AsyncExitStack, extension: Extension, factory: Callable, app) -> bool:
    try:
        manager = factory(app)
        await asyncio.wait_for(manager.__aenter__(), START_SECONDS)
    except Exception as exc:   # also the timeout
        extension.failure("start", exc)
        return False

    async def stop():
        try:
            await asyncio.wait_for(manager.__aexit__(None, None, None), START_SECONDS)
        except Exception as exc:
            extension.failure("stop", exc)

    stack.push_async_callback(stop)
    return True


@asynccontextmanager
async def background(app):
    """``features.background_tasks()`` factory: the extensions' background tasks and report listeners.

    Registered only when ``TRACKER_EXTENSIONS`` names a module. Never in demo mode
    (no extension is loaded there); lab-only mode runs no background task at all
    (``backend/main.py``).
    """
    extensions = loaded()
    if _demo() or not extensions:
        yield
        return
    async with AsyncExitStack() as stack:
        for extension in extensions:
            results = [await _start(stack, extension, factory, app) for factory in extension.tasks]
            extension.started = all(results)
        listeners = {extension.name: extension.on_close_report for extension in extensions
                     if extension.on_close_report is not None}
        if listeners:
            from core.close_reports import Watcher
            by_name = {extension.name: extension for extension in extensions}

            def failed(name, report, exc):
                by_name[name].failure("on_close_report", exc, trading_date=report["trading_date"])

            watcher = Watcher(listeners, failed=failed)
            watcher.start()
            stack.push_async_callback(watcher.stop)
        stack.callback(_stopped, extensions)
        yield


def _stopped(extensions: List[Extension]) -> None:
    for extension in extensions:
        extension.started = False


def status() -> Optional[List[dict]]:
    """For ``GET /api/app/status``: every named extension, or None when ``TRACKER_EXTENSIONS`` named none."""
    if not _configured:
        return None
    return [extension.view() for extension in _extensions]
