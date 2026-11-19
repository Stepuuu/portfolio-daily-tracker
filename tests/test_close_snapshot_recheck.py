"""While the close snapshot waits for a closing print, it looks again every five minutes.

The reviewer's case: with Yahoo's usual 15-minute delay the source still shows Hong Kong's 15:55 trade at
16:10; the closing auction's print (16:08:02) reaches it at 16:23:02, and Today shows the close from then on.
At 40868ac the snapshot, retried at 15:55 and 16:25, was made at 16:25 (at 16:23 on a manual run). Round 4
waited for the print plus the sources' usual delay (16:28) in one sleep, also after a manual request had
been accepted. Now, while a price waits for its closing print, the run looks again at the scheduler's tick on
the clock's five-minute marks (16:15, 16:20, 16:25) and books the close as soon as it can be verified; a
manual run during any wait looks at once and keeps the same row waiting if the close cannot be verified yet.
The print plus 20 minutes (16:28) stays the moment after which the day's last price is booked as an
estimate (含估算). A wait uses up no retry and notifies nothing. The wait for a close that is not final yet
(Hong Kong at 15:40 until 16:10, New York until 16:30 New York time) and an A-share-only portfolio are as
before.

The engine's own price check and exit text run in-process at the scheduler's (fake) clock; the source is
scripted. Fictional prices.
"""
import asyncio
import functools
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest

import portfolio_snapshot as snapshot
from core import notify
from core.routines import close_snapshot, scheduler
from core.routines.close_snapshot import EngineResult
from core.routines.scheduler import Routine, Scheduler
from tests.engine_fakes import position
from tests.fakes import FakeClock, shanghai
from tests.test_close_snapshot import complete_day, environment, fake_engine, portfolio  # noqa: F401  (fixtures)

DAY = '2026-11-17'                      # Tuesday
NEXT = '2026-11-18'
UTC = timezone.utc
HK, NY, CN = 'HKG:00003', 'NASDAQ:EXMP', 'SHA:600001'
CURRENCY = {HK: 'HKD', NY: 'USD', CN: 'CNY'}


class Response:
    def __init__(self, meta):
        self.meta = meta

    def raise_for_status(self):
        pass

    def json(self):
        return {'chart': {'result': [{'meta': self.meta}]}}


class EngineAtTheClock:
    """The snapshot engine's price check and exit text, run in-process at the scheduler's clock.

    ``shown(now)`` is what the source shows at ``now``: ``(price, exchange time)``, or None when it cannot be
    reached. A run whose prices are all booked leaves the day as the pipeline does (``complete_day``).
    """

    def __init__(self, monkeypatch, clock, portfolio, ticker, shown):
        self.clock, self.portfolio, self.ticker, self.shown = clock, portfolio, ticker, shown
        self.calls, self.booked = [], []
        monkeypatch.setattr(snapshot, '_now', lambda: clock.now().astimezone(UTC))
        monkeypatch.setattr(snapshot.requests, 'get', self.source)
        monkeypatch.setattr(snapshot, 'fetch_fx_rates', lambda config, estimated=None, needed=None: {
            'CNY': 1.0, 'HKD': 0.9, 'USD': 7.0})

    def source(self, url, **options):
        seen = self.shown(self.clock.now())
        if seen is None:
            raise snapshot.requests.ConnectionError('the source cannot be reached in this test')
        price, stamp = seen
        return Response({'regularMarketPrice': price, 'currency': CURRENCY[self.ticker],
                         'regularMarketTime': int(stamp.timestamp())})

    async def __call__(self, command, *, env, cwd, timeout):
        moment = f'{self.clock.now():%H:%M:%S}'
        self.calls.append(moment)
        holdings = {'date': DAY, 'groups': {'E': {'cash': 0, 'fund': 0, 'cost_basis': 0,
                                                  'positions': [position(self.ticker)]}}}
        quotes = snapshot._fetch_quotes(holdings, {}, DAY)
        prices, estimated = quotes[0], quotes[4]
        if snapshot.missing_prices(holdings, prices):
            with pytest.raises(SystemExit) as stopped:
                snapshot._generate(SimpleNamespace(date=DAY, dry_run=True), {}, holdings, quotes)
            return EngineResult(1, f'  ❌ 快照生成失败: {stopped.value.code}\n')
        self.booked.append((moment, dict(estimated)))
        complete_day(self.portfolio, cwd, DAY)
        return EngineResult(0, 'ok')


def scheduled(monkeypatch, fake_engine, portfolio, ticker, shown):
    clock = FakeClock()
    engine = EngineAtTheClock(monkeypatch, clock, portfolio, ticker, shown)
    routine = Routine(close_snapshot.KIND, close_snapshot.due,
                      functools.partial(close_snapshot.run, engine_dir=fake_engine, portfolio_dir=portfolio,
                                        runner=engine, now=clock),
                      close_snapshot.TITLE, retry_until=close_snapshot.retry_until)
    return Scheduler([routine], now=clock), clock, engine


def ticks(s, clock, *moments, day=DAY):
    async def go():
        for moment in moments:
            clock.set(f'{day} {moment}')
            await s.tick()
            await s.settle()
    asyncio.run(go())


def minutes(first, last, step=1):
    """Every ``step`` minutes from ``first`` to ``last`` (``HH:MM``, the same day)."""
    start, end = shanghai(f'{DAY} {first}'), shanghai(f'{DAY} {last}')
    return [f'{start + timedelta(minutes=offset):%H:%M}'
            for offset in range(0, int((end - start).total_seconds()) // 60 + 1, step)]


def row():
    return scheduler.run_for(close_snapshot.KIND, DAY)


def delayed_hong_kong(now):
    """Yahoo's usual 15 minutes: the trade of 15 minutes before; the continuous session's last trade is at
    15:59:58, the closing auction prints at 16:08:02 and that print reaches the source at 16:23:02."""
    if now >= shanghai(f'{DAY} 16:23:02'):
        return 10.6, shanghai(f'{DAY} 16:08:02')
    return 10.5, min(now - timedelta(minutes=15), shanghai(f'{DAY} 15:59:58'))


def last_trade_at_15_55_12(now):
    """A thinly traded stock: its last trade of the day was at 15:55:12, none in the closing auction."""
    return 10.5, min(now - timedelta(minutes=15), shanghai(f'{DAY} 15:55:12'))


# -- Hong Kong: the closing print reaches the source at 16:23 ----------------------------------------

def test_a_close_that_reaches_the_source_at_16_23_is_booked_unmarked_at_the_16_25_check(monkeypatch, fake_engine,
                                                                                     portfolio):
    """The reviewer's case; at 40868ac the snapshot was made at 16:25, in round 4 only at 16:28."""
    s, clock, engine = scheduled(monkeypatch, fake_engine, portfolio, HK, delayed_hong_kong)
    ticks(s, clock, *minutes('15:40', '16:14'))
    assert engine.calls == ['15:40:00', '16:10:00'], 'nothing is looked at before the close is final at 16:10'
    waiting = row()
    assert (waiting['status'], waiting['status_text'], waiting['error_category']) == ('pending', '等待中', 'waiting')
    assert waiting['summary'] == '等待港股收盘价确定，最晚 16:28 记入'
    assert waiting['next_attempt_at'] == '2026-11-17T16:15:00+08:00' and waiting['attempts'] == 0

    ticks(s, clock, *minutes('16:15', '16:30'))
    assert engine.calls == ['15:40:00', '16:10:00', '16:15:00', '16:20:00', '16:25:00']
    assert engine.booked == [('16:25:00', {})], 'the close, verified: no estimate'
    done = row()
    assert (done['status'], done['finished_at'], done['attempts']) == ('ok', '2026-11-17T16:25:00+08:00', 1)
    assert notify.list() == []


def test_a_manual_run_at_16_23_books_the_close_at_once(monkeypatch, fake_engine, portfolio):
    """At 40868ac a manual run at 16:23 made the snapshot then; in round 4 it stayed waiting until 16:28."""
    s, clock, engine = scheduled(monkeypatch, fake_engine, portfolio, HK, delayed_hong_kong)
    ticks(s, clock, *minutes('15:40', '16:23'))
    waiting = row()
    assert waiting['next_attempt_at'] == '2026-11-17T16:25:00+08:00'
    clock.set(f'{DAY} 16:23:30')
    requested = s.request_run(close_snapshot.KIND)
    assert requested['id'] == waiting['id'] and requested['status'] == 'pending'
    assert requested['next_attempt_at'] == '2026-11-17T16:23:30+08:00'
    ticks(s, clock, '16:23:30')
    assert engine.booked == [('16:23:30', {})]
    done = row()
    assert (done['id'], done['status'], done['attempts']) == (waiting['id'], 'ok', 1)
    assert notify.list() == []


def test_a_manual_run_before_the_close_reached_the_source_keeps_the_same_row_waiting(monkeypatch, fake_engine,
                                                                                    portfolio):
    s, clock, engine = scheduled(monkeypatch, fake_engine, portfolio, HK, delayed_hong_kong)
    ticks(s, clock, *minutes('15:40', '16:17'))
    waiting = row()
    clock.set(f'{DAY} 16:17:30')
    s.request_run(close_snapshot.KIND)
    ticks(s, clock, '16:17:30')
    assert engine.calls[-1] == '16:17:30' and engine.booked == []
    still = row()
    assert (still['id'], still['status'], still['error_category'], still['attempts']) == (
        waiting['id'], 'pending', 'waiting', 0), 'the same row, waiting; no retry used up'
    assert still['next_attempt_at'] == '2026-11-17T16:20:00+08:00'
    assert still['summary'] == '等待港股收盘价确定，最晚 16:28 记入'
    ticks(s, clock, *minutes('16:18', '16:30'))
    assert engine.booked == [('16:25:00', {})] and row()['status'] == 'ok'
    assert notify.list() == []


def test_a_manual_run_while_hong_kong_still_trades_keeps_waiting_until_16_10(monkeypatch, fake_engine, portfolio):
    """It looks at once; a close that is not final yet cannot be verified, so the row waits as before."""
    s, clock, engine = scheduled(monkeypatch, fake_engine, portfolio, HK, delayed_hong_kong)
    ticks(s, clock, '15:40')
    waiting = row()
    clock.set(f'{DAY} 15:50')
    s.request_run(close_snapshot.KIND)
    ticks(s, clock, *minutes('15:50', '16:09'))
    assert engine.calls == ['15:40:00', '15:50:00']
    still = row()
    assert (still['id'], still['status'], still['error_category'], still['attempts']) == (
        waiting['id'], 'pending', 'waiting', 0)
    assert (still['summary'], still['next_attempt_at']) == ('等待港股收盘价确定，16:10 后记入', '2026-11-17T16:10:00+08:00')
    assert notify.list() == []


# -- Hong Kong: no closing print (both ways) -----------------------------------------------------------

def test_a_price_stamped_15_55_12_waits_and_is_booked_marked_at_16_28(monkeypatch, fake_engine, portfolio):
    s, clock, engine = scheduled(monkeypatch, fake_engine, portfolio, HK, last_trade_at_15_55_12)
    ticks(s, clock, *minutes('15:40', '16:27'))
    assert engine.calls == ['15:40:00', '16:10:00', '16:15:00', '16:20:00', '16:25:00'] and engine.booked == []
    waiting = row()
    assert (waiting['status_text'], waiting['attempts']) == ('等待中', 0)
    assert waiting['summary'] == '等待港股收盘价确定，最晚 16:28 记入'
    assert waiting['next_attempt_at'] == '2026-11-17T16:28:00+08:00', 'the last look is the print plus 20 minutes'
    ticks(s, clock, *minutes('16:28', '16:40'))
    assert engine.booked == [('16:28:00', {HK: snapshot.NO_CLOSING_PRINT})], 'the day\'s last price, 含估算'
    done = row()
    assert (done['status'], done['finished_at'], done['attempts']) == ('ok', '2026-11-17T16:28:00+08:00', 1)
    assert notify.list() == []


# -- unchanged: New York and the mainland -------------------------------------------------------------

def test_new_york_still_waits_for_its_settled_close_without_looking_in_between(monkeypatch, fake_engine, portfolio):
    """A retry after New York opens waits for 16:30 New York time (05:30 Shanghai) in one wait, as before."""
    def new_york(now):
        if now < shanghai(f'{DAY} 22:30'):
            return None                                    # offline at 15:40: the run fails, a retry follows
        return 98.0, min(now, shanghai(f'{NEXT} 05:00'))   # 16:00:00 New York, the regular session's close

    s, clock, engine = scheduled(monkeypatch, fake_engine, portfolio, NY, new_york)
    ticks(s, clock, '15:40')
    assert row()['status'] == 'failed'
    ticks(s, clock, '23:05', *minutes('23:10', '23:55', 5))        # the 15:55 retry runs late, at 23:05
    waiting = row()
    assert waiting['summary'] == '等待美股收盘价确定，次日 05:30 后记入'
    assert waiting['next_attempt_at'] == '2026-11-18T05:30:00+08:00' and waiting['attempts'] == 1
    ticks(s, clock, *minutes('00:00', '05:30', 5), day=NEXT)
    assert engine.calls == ['15:40:00', '23:05:00', '05:30:00']
    assert engine.booked == [('05:30:00', {})]
    assert row()['status'] == 'ok' and notify.list() == []


@pytest.mark.parametrize('stamp, estimated', [
    ('15:00:00', {}),                                     # the close: booked
    ('14:59:00', {CN: snapshot.NO_CLOSING_PRINT}),        # before the print, past 15:20: the last price, marked
])
def test_an_a_share_only_portfolio_is_made_at_15_40(monkeypatch, fake_engine, portfolio, stamp, estimated):
    s, clock, engine = scheduled(monkeypatch, fake_engine, portfolio, CN, lambda now: (10.5, shanghai(f'{DAY} {stamp}')))
    ticks(s, clock, *minutes('15:40', '15:50'))
    assert engine.calls == ['15:40:00'] and engine.booked == [('15:40:00', estimated)]
    assert row()['status'] == 'ok' and notify.list() == []
