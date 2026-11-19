"""Production routes for the backtest browser fixture: bars without the network, a slow rule-based reading.

Like ``browser_app``, external services are not started. Outside the demo the backtest service loads
bars through ``FixtureLoader``: the demo's deterministic random walk (backtesting.demo.synthetic_bars)
for any stock and index, except that ``IDX:HSI`` has none (a Hong Kong run's index comparison is then
unavailable) and ``US:MSFT`` (NASDAQ:MSFT) takes ``BACKTEST_FIXTURE_SLOW_SECONDS`` to arrive (a run in progress can
be watched and left). In the demo the service keeps its own synthetic loader. The AI reading is the
rule-based reflector (no model, no network), written after ``BACKTEST_FIXTURE_REFLECT_SECONDS`` so the
page shows it pending first.
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager

from backend.main import app
from backend.api import backtest
from backtesting.demo import synthetic_bars
from backtesting.reflection import BacktestReflector

NO_BARS = {"IDX:HSI"}
SLOW = {"US:MSFT"}                  # NASDAQ:MSFT as the service names it


class FixtureLoader:
    """Stands in for DataLoader: synthetic daily bars, no network."""

    source = "fixture.synthetic"

    def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
        ticker = getattr(symbol, "ticker", None) or str(symbol).strip().upper()
        if ticker in NO_BARS:
            raise RuntimeError(f"fixture: no bars for {ticker}")
        if ticker in SLOW:
            time.sleep(float(os.environ.get("BACKTEST_FIXTURE_SLOW_SECONDS", "4")))
        frame = synthetic_bars(ticker, start_date, end_date)
        frame.attrs["source"] = self.source
        return frame


class SlowReflector:
    """The rule-based reading, after a pause."""

    async def reflect_facts(self, payload):
        await asyncio.sleep(float(os.environ.get("BACKTEST_FIXTURE_REFLECT_SECONDS", "3")))
        return await BacktestReflector(use_llm=False).reflect_facts(payload)


@asynccontextmanager
async def fixture_lifespan(app):
    yield


app.router.lifespan_context = fixture_lifespan
backtest.SERVICE.reflector_factory = lambda use_llm: SlowReflector()
if os.environ.get("TRACKER_DEMO_MODE") != "1":
    backtest.SERVICE.loader_factory = lambda plan: FixtureLoader()
