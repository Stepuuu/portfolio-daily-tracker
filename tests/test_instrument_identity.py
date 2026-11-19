"""One listing, one key: ``identity_key`` and the read-only ``identity`` field the pages match by.

The vectors in ``tests/fixtures/instrument_identity.json`` are shared with the frontend's fallback copy
of the rule (``dashboard/frontend/src/lib/instrument.ts`` ``identityKey``, node test
``dashboard/frontend/tests/instrument.test.mjs``): both must give the same answer for every pair.
Fictional data only.
"""
import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from core import notify, watchlist
from core.alerts import rules
from core.market import search
from core.market.runtime import set_market_runtime
from core.market.search import identity_key
from tests.fakes import FakeClock, ScriptedSource, build_ledger, fake_runtime, fx_rate, opening, position, quote
from tests.test_alert_monitor import Market

VECTORS = json.loads((Path(__file__).parent / 'fixtures' / 'instrument_identity.json').read_text(encoding='utf-8'))


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    monkeypatch.delenv('TRACKER_ALERTS_ENABLED', raising=False)
    monkeypatch.setattr(notify, '_deliverers', [])
    set_market_runtime(None)
    search.set_symbol_index(search.SymbolIndex(fetchers={}))
    yield tmp_path
    set_market_runtime(None)
    search.set_symbol_index(None)


@pytest.fixture
def client():
    app = FastAPI()
    for router, prefix, tags in features.routers():
        app.include_router(router, prefix=prefix, tags=tags)
    return TestClient(app)


def test_the_shared_vectors():
    assert len(VECTORS['vectors']) >= 20
    for given, expected in VECTORS['vectors']:
        assert identity_key(given) == expected, given


def test_today_rows_carry_the_listing_identity(desk, client):
    build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, date='2026-11-13', positions=[
        position('NYSE:BRK-B', '10', '400'), position('HKG:0700', '100', '300')])])
    clock = FakeClock('2026-11-17 10:00')
    now = clock.now()
    source = ScriptedSource({'HKG:0700': quote('HKG:0700', '310', prev_close='300', at=now - timedelta(seconds=3))},
                            {'USD': fx_rate('USD', '7', at=now), 'HKD': fx_rate('HKD', '0.9', at=now)})
    set_market_runtime(fake_runtime(clock, source))
    rows = {row['ticker']: row for account in client.get('/api/today').json()['accounts'] for row in account['positions']}
    assert rows['NYSE:BRK-B']['identity'] == 'NYSE:BRK.B'
    assert rows['HKG:0700']['identity'] == 'HKG:00700'


def test_watchlist_items_carry_the_listing_identity(desk, client):
    set_market_runtime(fake_runtime(FakeClock('2026-11-17 10:00'), ScriptedSource({}, {})))
    watchlist.add('HKG:700')
    watchlist.add('NYSE:BRK-B')
    items = client.get('/api/watchlist', params={'quotes': 0}).json()['items']
    assert {item['ticker']: item['identity'] for item in items} == {
        item['ticker']: identity_key(item['ticker']) for item in items}
    assert {item['identity'] for item in items} == {'HKG:00700', 'NYSE:BRK.B'}


def test_alert_rules_carry_the_listing_identity(desk, client):
    set_market_runtime(Market().runtime)
    created = client.post('/api/alerts/rules', json={'ticker': 'NYSE:BRK-B', 'kind': 'stop_loss', 'threshold': '300'})
    assert created.status_code == 201 and created.json()['identity'] == 'NYSE:BRK.B'
    listed = client.get('/api/alerts/rules', params={'quotes': 0}).json()['items']
    assert [rule['identity'] for rule in listed] == ['NYSE:BRK.B']
    assert client.get(f"/api/alerts/rules/{created.json()['id']}").json()['identity'] == 'NYSE:BRK.B'


def test_a_suppressed_fire_says_whether_a_published_digest_covers_it(desk, client):
    market = Market('2026-11-17 14:59:30')
    set_market_runtime(market.runtime)
    tickers = [f'SHA:6000{index:02d}' for index in range(1, 26)]
    for ticker in tickers:
        rules.create({'ticker': ticker, 'kind': 'stop_loss', 'threshold': '10', 'cooldown_seconds': 0})
        market.price(ticker, '9.9', name=None)
    watcher = market.monitor()
    asyncio.run(watcher.run_cycle())                    # 20 pushed, 5 held back and summed up at once
    first = client.get('/api/alerts/events', params={'limit': 200}).json()['items']
    assert {event['digest_sent'] for event in first if event['delivery'] == 'suppressed'} == {True}
    assert {event['digest_sent'] for event in first if event['delivery'] == 'notified'} == {None}
    for price in ('10.2', '9.9'):                       # a second burst inside the same ten minutes
        market.tick()
        for ticker in tickers:
            market.price(ticker, price, name=None)
        asyncio.run(watcher.run_cycle())
    later = client.get('/api/alerts/events', params={'limit': 200}).json()['items']
    waiting = [event for event in later if event['delivery'] == 'suppressed' and not event['digest_sent']]
    assert len(waiting) == 25, 'held back after the digest: waiting for the next one'
    assert all(event['digest_sent'] is False for event in waiting)
