"""Feature registry for the daily-desk features.

Each feature package adds its router and, if it has one, its background task
here, so that ``backend/main.py`` does not have to change again.

Imports happen inside the functions: a feature that fails to import must not
take the whole API down at module import time.
"""
from contextlib import AsyncExitStack, asynccontextmanager
import os
from typing import Callable, List, Tuple

from fastapi import APIRouter, FastAPI

RouterEntry = Tuple[APIRouter, str, List[str]]


def routers() -> List[RouterEntry]:
    """Routers to mount, as ``(router, prefix, tags)``."""
    entries: List[RouterEntry] = []

    # -- application status -------------------------------------------------
    from backend.api import app_status
    entries.append((app_status.router, "/api/app", ["应用状态"]))

    # -- market data and the Today page ------------------------------------
    from backend.api import today
    entries.append((today.router, "/api", ["今日"]))

    # -- notifications and confirmations -----------------------------------
    from backend.api import notifications, proposals
    entries.append((notifications.router, "/api/notifications", ["通知"]))
    entries.append((proposals.router, "/api/proposals", ["待确认方案"]))

    # -- accounts -----------------------------------------------------------
    from backend.api import accounts
    entries.append((accounts.router, "/api/accounts", ["账户费率"]))

    # -- watchlist ----------------------------------------------------------
    from backend.api import instruments, watchlist
    entries.append((watchlist.router, "/api/watchlist", ["自选"]))
    entries.append((instruments.router, "/api/instruments", ["代码搜索"]))

    # -- alerts -------------------------------------------------------------
    from backend.api import alerts
    entries.append((alerts.router, "/api/alerts", ["提醒"]))

    # -- trade plans --------------------------------------------------------
    from backend.api import plans
    entries.append((plans.router, "/api/plans", ["交易计划"]))

    # -- pre-market and post-market routines --------------------------------
    from backend.api import routines
    entries.append((routines.router, "/api/routines", ["例行任务"]))
    from backend.api import briefs
    entries.append((briefs.router, "/api/briefs", ["盘前盘后简报"]))

    # -- imports ------------------------------------------------------------
    from backend.api import imports
    entries.append((imports.router, "/api/imports", ["导入"]))

    # -- first-run setup wizard ----------------------------------------------
    from backend.api import setup
    entries.append((setup.router, "/api/setup", ["设置向导"]))

    # -- desk store backup (设置 › 备份与导出) --------------------------------
    from backend.api import desk
    entries.append((desk.router, "/api/desk", ["备份"]))

    return entries


def background_tasks() -> List[Callable]:
    """Factories ``f(app)`` returning an async context manager.

    Each one runs for the lifetime of the application. They are not started
    when ``TRACKER_LAB_ONLY=1``.
    """
    factories: List[Callable] = []

    # -- desk store -----------------------------------------------------------
    factories.append(desk_store_upgrade)

    # -- alerts -------------------------------------------------------------
    from core.alerts import monitor as alert_monitor
    factories.append(alert_monitor.background)

    # -- pre-market and post-market routines --------------------------------
    from core.routines.scheduler import background as routines_background
    factories.append(routines_background)

    return factories


@asynccontextmanager
async def desk_store_upgrade(app: FastAPI):
    """Upgrade the schemas of an existing ``desk.sqlite3`` once, at start-up.

    Requests that only read never migrate the store, so this is where an
    upgraded release brings an existing file up to date. Skipped in demo mode,
    which must not change any data, and when the file does not exist yet (the
    first write creates it).
    """
    if os.environ.get("TRACKER_DEMO_MODE") != "1":
        from core import desk_store
        desk_store.migrate_registered()
    yield


@asynccontextmanager
async def background(app: FastAPI):
    """Run every registered background task until the application stops."""
    async with AsyncExitStack() as stack:
        for factory in background_tasks():
            await stack.enter_async_context(factory(app))
        yield
