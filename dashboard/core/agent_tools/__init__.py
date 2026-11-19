"""Registry of agent tools contributed by feature packages.

A feature adds tools for the chat agent by putting a module in this package.
Nobody has to edit ``backend/services/agent_service.py`` for that.

Adding tools (for example ``core/agent_tools/watchlist_tools.py``)::

    from core import agent_tools
    from core.tools import Tool, ToolParameter, ToolParameterType

    @agent_tools.register
    def watchlist_tools(runtime):
        async def get_watchlist() -> dict:          # tool functions must be async
            return {"tickers": watchlist.tickers()}

        return [Tool(name="get_watchlist", description="读取自选股列表",
                     parameters=[], function=get_watchlist)]

Tools are ordinary ``core.tools.Tool`` objects, the same ones ``ToolExecutor``
runs today: ``function`` is awaited with the model's arguments as keyword
arguments and returns a JSON-serialisable value. A factory receives the
``runtime`` given to ``build_all`` and returns one ``Tool``, an iterable of
them, or ``None`` when its feature is unavailable.

A tool that changes something must not do the change. It calls
``core.proposals.propose(...)`` and tells the model that the user has to
confirm on the page (see ``propose_ledger_tool`` in ``core/tools.py``).

The one call the agent service makes, in ``AgentService._init_agent``::

    from core import agent_tools

    tools = [
        get_stock_quote_tool(self.market_provider),
        ...                                   # the built-in tools, as today
    ]
    tools += agent_tools.build_all(self, exclude=[tool.name for tool in tools])
    tool_executor = ToolExecutor(tools)

``runtime`` is the ``AgentService`` instance. Factories may read its
``market_provider``, ``portfolio_provider``, ``news_provider`` and
``memory_manager`` with ``getattr(runtime, name, None)`` and must cope with
``None``; services that have their own accessor (the market runtime, the desk
store) should be reached through that accessor instead.

``build_all`` imports every module of this package first, so dropping a file
here is enough. One broken module, factory or tool is logged and skipped; it
never takes the other tools or the agent down.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import re
from typing import Any, Callable, Iterable, List

from core.tools import Tool, ToolParameter

logger = logging.getLogger(__name__)

Factory = Callable[[Any], Any]

_factories: List[Factory] = []


def register(factory: Factory) -> Factory:
    """Add a tool factory ``f(runtime) -> Tool | iterable of Tool | None``.

    Returns the factory, so it works as a decorator. Registering the same
    factory again has no further effect.
    """
    if not callable(factory):
        raise TypeError("an agent tool factory must be callable")
    if factory not in _factories:
        _factories.append(factory)
    return factory


def unregister(factory: Factory) -> None:
    """Remove a registered factory; tools already built from it are not affected."""
    if factory in _factories:
        _factories.remove(factory)


def registered() -> List[Factory]:
    """The registered factories, in registration order (a copy)."""
    return list(_factories)


def discover() -> None:
    """Import every module of this package so that its factories register."""
    for module in sorted(pkgutil.iter_modules(__path__), key=lambda found: found.name):
        try:
            importlib.import_module(f"{__name__}.{module.name}")
        except Exception as exc:
            logger.warning("agent tool module %s could not be imported: %s", module.name, type(exc).__name__)


def build_all(runtime: Any = None, exclude: Iterable[str] = ()) -> List[Tool]:
    """Build the tools of every registered factory, in registration order.

    ``exclude`` lists tool names that are already taken (the caller's built-in
    tools). A registered tool with such a name, or with the name of an earlier
    registered tool, is skipped and logged: ``ToolExecutor`` keys tools by
    name, so a duplicate would silently replace the other tool.
    """
    discover()
    taken = set(exclude)
    tools: List[Tool] = []
    for factory in tuple(_factories):
        label = getattr(factory, "__name__", type(factory).__name__)
        try:
            produced = factory(runtime)
            if produced is None:
                continue
            produced = [produced] if isinstance(produced, Tool) else [*produced]
        except Exception as exc:
            logger.warning("agent tool factory %s failed: %s", label, type(exc).__name__)
            continue
        for tool in produced:
            problem = _problem(tool, taken)
            if problem:
                logger.warning("agent tool from factory %s skipped: %s", label, problem)
                continue
            taken.add(tool.name)
            tools.append(tool)
    return tools


def _problem(tool, taken) -> str:
    if not isinstance(tool, Tool):
        return "not a core.tools.Tool"
    if not isinstance(tool.name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool.name):
        return "tool name must be 1-64 letters, digits, underscores or hyphens"
    if tool.name in taken:
        return f"the name {tool.name} is already taken"
    if not isinstance(tool.description, str) or not tool.description.strip():
        return f"{tool.name} has no description"
    if not isinstance(tool.parameters, (list, tuple)) or not all(isinstance(p, ToolParameter) for p in tool.parameters):
        return f"{tool.name} parameters must be ToolParameter objects"
    if not inspect.iscoroutinefunction(tool.function):
        return f"{tool.name} function must be async"
    return ""
