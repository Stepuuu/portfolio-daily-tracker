"""Production routes for the brief browser fixture: no assistant service, and the brief routines on the script's clock.

Like ``browser_app``, external services are not started. The pre- and post-market routines run on a
scheduler whose clock is the market runtime's (``fake_quotes.json``'s ``now``) and which looks at the
routines only at start and when asked (a manual run wakes it), so the smoke test decides when a brief
is generated. The close snapshot and the alert monitor do not run.
"""
from contextlib import asynccontextmanager

from backend.main import app
from core.market.runtime import get_market_runtime
from core.routines import briefs, load_kinds, scheduler
from core.routines.scheduler import Scheduler


@asynccontextmanager
async def fixture_lifespan(app):
    load_kinds()
    routines = [routine for routine in scheduler.registered() if routine.kind in briefs.KINDS]
    running = Scheduler(routines, now=lambda: get_market_runtime().clock.now(), interval=3600)
    running.start()
    try:
        yield
    finally:
        await running.stop()


app.router.lifespan_context = fixture_lifespan
