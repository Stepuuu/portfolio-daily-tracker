"""A close snapshot waits for a close that is not final yet; it does not fail.

Hong Kong's close is final at 16:10 (the closing auction), after the snapshot's 15:40. When the only prices
the engine lacks are of such sessions, it says when the last of them becomes final
(``CLOSE_NOT_FINAL_UNTIL=<time> MARKETS=<markets>``, from ``SessionClock.close_settled``); the run is then
recorded as waiting (``pending``, category ``waiting``) with a fixed reason such as
等待港股收盘价确定，16:10 后记入, sends no notification, does not use up a retry, and runs again at the first
scheduler tick at or after that moment (at once on a manual request, on the same row). An A-share-only
portfolio is made at 15:40 as before. The engine is
scripted here (its own side is in ``tests/test_snapshot_session.py``); the clock is fixed.
"""
import asyncio
import functools
from datetime import datetime, timezone

import pytest

from core import notify
from core.routines import close_snapshot, scheduler
from core.routines.close_snapshot import EngineResult
from core.routines.scheduler import Routine, Scheduler
from tests.fakes import FakeClock
from tests.test_close_snapshot import FakeRunner, environment, fake_engine, portfolio  # noqa: F401  (fixtures)

TUESDAY = '2026-11-17'
WAIT_OUTPUT = ('  ⚠️ 获取 HKG:00003 (00003.HK) 价格失败: a price of the 2026-11-17 session, which is still trading or '
               'whose closing price is not final yet (final at 2026-11-17T16:10+08:00): not a close\n'
               '  ❌ 快照生成失败: ⏳ 未保存 2026-11-17 快照：等待港股收盘价确定，16:10 后记入（HKG:00003）。\n'
               'CLOSE_NOT_FINAL_UNTIL=2026-11-17T16:10:00+08:00 MARKETS=HK\n')
FAILED_OUTPUT = '  ❌ 快照生成失败: 网络错误'


def wait_output(until, markets='HK'):
    return f'  ❌ 快照生成失败: ⏳ 未保存 {TUESDAY} 快照：等待。\nCLOSE_NOT_FINAL_UNTIL={until} MARKETS={markets}\n'


def routine_with(fake_engine, portfolio, runner, clock):
    return Routine(close_snapshot.KIND, close_snapshot.due,
                   functools.partial(close_snapshot.run, engine_dir=fake_engine, portfolio_dir=portfolio,
                                     runner=runner, now=clock),
                   close_snapshot.TITLE, retry_until=close_snapshot.retry_until)


def ticks(s, clock, *moments, day=TUESDAY):
    async def go():
        for moment in moments:
            clock.set(f'{day} {moment}')
            await s.tick()
            await s.settle()
    asyncio.run(go())


def test_hong_kong_not_final_at_15_40_waits_and_is_made_at_the_first_tick_after_16_10(fake_engine, portfolio):
    runner = FakeRunner(EngineResult(1, WAIT_OUTPUT), EngineResult(0, 'ok'), write_snapshot=portfolio)
    clock = FakeClock()
    routine = routine_with(fake_engine, portfolio, runner, clock)
    s = Scheduler([routine], now=clock)

    ticks(s, clock, '15:40')
    waiting = scheduler.run_for(close_snapshot.KIND, TUESDAY)
    assert (waiting['status'], waiting['status_text'], waiting['error_category']) == ('pending', '等待中', 'waiting')
    assert waiting['summary'] == '等待港股收盘价确定，16:10 后记入'
    assert waiting['next_attempt_at'] == '2026-11-17T16:10:00+08:00'
    assert waiting['attempts'] == 0, 'waiting is not an attempt that used up a retry'
    assert s.routine_status(routine)['next_due_at'] == '2026-11-17T16:10:00+08:00'
    assert notify.list() == [], 'a normal day records no failure and notifies nothing'

    ticks(s, clock, '15:55', '16:09')
    assert len(runner.calls) == 1, 'no run before the close is final (not the 15:55 retry slot either)'
    ticks(s, clock, '16:10', '16:11')
    done = scheduler.run_for(close_snapshot.KIND, TUESDAY)
    assert len(runner.calls) == 2 and done['status'] == 'ok' and done['attempts'] == 1
    assert notify.list() == []


def test_a_wait_leaves_every_retry_for_real_failures(fake_engine, portfolio):
    runner = FakeRunner(EngineResult(1, WAIT_OUTPUT), EngineResult(1, FAILED_OUTPUT))
    clock = FakeClock()
    s = Scheduler([routine_with(fake_engine, portfolio, runner, clock)], now=clock)
    ticks(s, clock, '15:40', '16:10', '16:25', '16:55', '17:30')
    saved = scheduler.run_for(close_snapshot.KIND, TUESDAY)
    assert len(runner.calls) == 4, 'the wait, then the first run and its two retries'
    assert saved['status'] == 'failed' and saved['attempts'] == 3 and saved['next_attempt_at'] is None
    [message] = notify.list()
    assert '尝试 3 次' in message['body']


def test_a_manual_run_during_the_wait_looks_at_once_and_keeps_the_same_row_waiting(fake_engine, portfolio):
    runner = FakeRunner(EngineResult(1, WAIT_OUTPUT), EngineResult(1, WAIT_OUTPUT), EngineResult(0, 'ok'),
                        write_snapshot=portfolio)
    clock = FakeClock()
    s = Scheduler([routine_with(fake_engine, portfolio, runner, clock)], now=clock)
    ticks(s, clock, '15:40')
    waiting = scheduler.run_for(close_snapshot.KIND, TUESDAY)
    clock.set(f'{TUESDAY} 15:50')
    row = s.request_run(close_snapshot.KIND)
    assert (row['id'], row['status'], row['status_text']) == (waiting['id'], 'pending', '等待中')
    assert row['next_attempt_at'] == '2026-11-17T15:50:00+08:00'
    ticks(s, clock, '15:50', '15:51')
    assert len(runner.calls) == 2, 'run at once; the close is not final yet, so it waits on'
    still = scheduler.run_for(close_snapshot.KIND, TUESDAY)
    assert (still['id'], still['status'], still['error_category'], still['attempts']) == (
        waiting['id'], 'pending', 'waiting', 0)
    assert still['next_attempt_at'] == '2026-11-17T16:10:00+08:00' and notify.list() == []
    ticks(s, clock, '16:10')
    assert scheduler.run_for(close_snapshot.KIND, TUESDAY)['status'] == 'ok'


def test_a_new_york_close_settling_after_midnight_waits_until_then(fake_engine, portfolio):
    """A retry after 22:30 meets New York's session of the day: it waits for 05:30, before the 09:00 deadline."""
    runner = FakeRunner(EngineResult(1, FAILED_OUTPUT), EngineResult(1, wait_output('2026-11-18T05:30:00+08:00', 'US')),
                        EngineResult(0, 'ok'), write_snapshot=portfolio)
    clock = FakeClock()
    s = Scheduler([routine_with(fake_engine, portfolio, runner, clock)], now=clock)
    ticks(s, clock, '15:40')
    ticks(s, clock, '23:05')            # the retry, due at 15:55, runs late (the backend was busy or asleep)
    waiting = scheduler.run_for(close_snapshot.KIND, TUESDAY)
    assert waiting['summary'] == '等待美股收盘价确定，次日 05:30 后记入'
    assert waiting['next_attempt_at'] == '2026-11-18T05:30:00+08:00' and waiting['attempts'] == 1
    ticks(s, clock, '05:29', day='2026-11-18')
    assert len(runner.calls) == 2
    ticks(s, clock, '05:30', day='2026-11-18')
    assert scheduler.run_for(close_snapshot.KIND, TUESDAY)['status'] == 'ok' and notify.list() == []


@pytest.mark.parametrize('until', ['2026-11-18T09:30:00+08:00', 'not a time', '2026-11-17T16:10:00'])
def test_a_wait_that_cannot_end_before_the_deadline_or_is_garbled_is_a_failure(fake_engine, portfolio, until):
    runner = FakeRunner(EngineResult(1, wait_output(until)))
    clock = FakeClock(f'{TUESDAY} 15:40')
    outcome = asyncio.run(close_snapshot.run(TUESDAY, engine_dir=fake_engine, portfolio_dir=portfolio,
                                             runner=runner, now=clock))
    assert outcome.status == 'failed' and outcome.error_category == 'close_not_final'


def test_the_engines_own_words_are_understood(fake_engine, portfolio):
    """The contract between the engine's exit text and the routine, with the text the engine really prints."""
    from tests.test_snapshot_session import generated
    text = generated(['HKG:00003'], {'HKG:00003': datetime(2026, 11, 17, 8, 10, tzinfo=timezone.utc)})
    runner = FakeRunner(EngineResult(1, f'  ❌ 快照生成失败: {text[-300:]}'))
    outcome = asyncio.run(close_snapshot.run(TUESDAY, engine_dir=fake_engine, portfolio_dir=portfolio,
                                             runner=runner, now=FakeClock(f'{TUESDAY} 15:40')))
    assert (outcome.status, outcome.summary) == ('pending', '等待港股收盘价确定，16:10 后记入')
    assert outcome.not_before.isoformat() == '2026-11-17T16:10:00+08:00'


def test_an_a_share_only_portfolio_is_made_at_15_40(fake_engine, portfolio):
    runner = FakeRunner(EngineResult(0, 'ok'), write_snapshot=portfolio)
    clock = FakeClock()
    s = Scheduler([routine_with(fake_engine, portfolio, runner, clock)], now=clock)
    ticks(s, clock, '15:40')
    saved = scheduler.run_for(close_snapshot.KIND, TUESDAY)
    assert saved['status'] == 'ok' and saved['attempts'] == 1 and len(runner.calls) == 1


def test_on_a_day_shanghai_is_closed_and_hong_kong_trades_no_snapshot_is_due(fake_engine, portfolio):
    """2026-10-02: the mainland's National Day holiday, Hong Kong open. Snapshots follow the mainland calendar."""
    assert close_snapshot.due(FakeClock('2026-10-02 16:30').session()) is None
    runner = FakeRunner(EngineResult(0, 'ok'))
    clock = FakeClock()
    s = Scheduler([routine_with(fake_engine, portfolio, runner, clock)], now=clock)
    ticks(s, clock, '15:40', '16:10', '16:30', day='2026-10-02')
    assert runner.calls == [] and scheduler.run_for(close_snapshot.KIND, '2026-10-02') is None
