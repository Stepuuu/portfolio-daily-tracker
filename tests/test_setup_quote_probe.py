"""The setup page's quote probe never holds the lock the other quote readers need, and never waits for it.

The probe asks a source for one quote with a time budget. While it waits for a slow source, the Today page
and the alert monitor still get their quotes at once; while one of them waits for a slow source, the probe
still answers within its own budget. Scripted sources only.
"""
import threading
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core import setup_checks
from core.market.runtime import get_market_runtime, set_market_runtime
from tests.fakes import FakeClock, ScriptedSource, fake_runtime, quote

PROBED, OTHER = setup_checks.PROBE_TICKER, 'SHA:600001'
NO_LEDGER = SimpleNamespace(state=None, error=None)


class Gate(ScriptedSource):
    """Holds a request for one of ``held`` until ``release`` is set."""

    def __init__(self, quotes, held):
        super().__init__(quotes, name='eastmoney')
        self.held = set(held)
        self.entered = threading.Event()
        self.release = threading.Event()

    def fetch(self, tickers):
        if self.held & set(tickers):
            self.entered.set()
            self.release.wait(10)
        return super().fetch(tickers)


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_LAB_ONLY', raising=False)
    clock = FakeClock('2026-11-17 10:00')

    def make(held):
        source = Gate({ticker: quote(ticker, '10', prev_close='9.9', at=clock.now(), source='eastmoney')
                       for ticker in (PROBED, OTHER)}, held)
        set_market_runtime(fake_runtime(clock, source))
        return source
    yield make
    set_market_runtime(None)


def test_a_slow_probe_does_not_make_other_readers_wait(gate):
    source = gate({PROBED})
    probe = threading.Thread(target=setup_checks.quotes_item, args=(NO_LEDGER, True), daemon=True)
    probe.start()
    try:
        assert source.entered.wait(5), 'the probe asked the source'
        started = time.monotonic()
        quotes = get_market_runtime().quotes.get([OTHER])
        waited = time.monotonic() - started
    finally:
        source.release.set()
        probe.join(10)
    assert quotes[OTHER].price == Decimal('10')
    assert waited < 0.5, f'a quote reader waited {waited:.1f} s for the setup probe'


def test_the_probe_answers_within_its_budget_while_another_reader_waits_for_a_source(gate):
    source = gate({OTHER})
    reader = threading.Thread(target=get_market_runtime().quotes.get, args=([OTHER],), daemon=True)
    reader.start()
    try:
        assert source.entered.wait(5), 'the other reader asked the source'
        started = time.monotonic()
        entry = setup_checks.quotes_item(NO_LEDGER, True)
        waited = time.monotonic() - started
    finally:
        source.release.set()
        reader.join(10)
    assert waited < 1.0, f'the probe waited {waited:.1f} s for another reader'
    assert entry['state'] == 'done' and PROBED in entry['detail']
