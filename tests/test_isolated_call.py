"""``IsolatedCalls``: capacity accounting, error delivery and caller isolation."""
import asyncio
import random
import threading
import time
import types

import pytest

from core import isolated_call
from core.isolated_call import CallBusy, CallTimeout, IsolatedCalls


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def gates():
    made = []

    def make():
        event = threading.Event()
        made.append(event)
        return event

    yield make
    for event in made:
        event.set()
    time.sleep(0.05)


def _wait_idle(pool, seconds=2.0):
    deadline = time.monotonic() + seconds
    while (pool.alive or pool.stuck) and time.monotonic() < deadline:
        time.sleep(0.01)


class _FailingThread(threading.Thread):
    def start(self):
        raise RuntimeError("can't start new thread")


def test_a_failed_thread_start_gives_its_capacity_back(monkeypatch):
    pool = IsolatedCalls('x', max_threads=6)
    real = isolated_call.threading
    monkeypatch.setattr(isolated_call, 'threading',
                        types.SimpleNamespace(Lock=real.Lock, Thread=_FailingThread))
    for _ in range(6):
        with pytest.raises(RuntimeError):
            run(pool.run(lambda: 1, 1))
        assert pool.alive == 0 and pool.stuck == 0
    monkeypatch.setattr(isolated_call, 'threading', real)
    assert run(pool.run(lambda: 'ok', 1)) == 'ok', 'no capacity may have leaked'
    _wait_idle(pool)
    assert pool.alive == 0 and pool.stuck == 0


def test_counters_never_go_negative_and_return_to_zero(gates):
    pool = IsolatedCalls('x', max_threads=4)
    gate = gates()
    seen = []

    async def scenario():
        for _ in range(3):
            with pytest.raises(CallTimeout):
                await pool.run(lambda: gate.wait(5), 0.02)
            seen.append((pool.alive, pool.stuck))
        task = asyncio.ensure_future(pool.run(lambda: gate.wait(5), 5))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        seen.append((pool.alive, pool.stuck))

    run(scenario())
    gate.set()
    _wait_idle(pool)
    assert all(a >= 0 and s >= 0 and s <= a for a, s in seen), seen
    assert pool.alive == 0 and pool.stuck == 0


def test_an_exception_in_the_worker_reaches_the_caller_and_frees_the_slot():
    class Boom(Exception):
        pass

    pool = IsolatedCalls('x', max_threads=1)
    problem = Boom('details')

    def fail():
        raise problem

    for _ in range(3):                       # one slot only: a leak would make the 2nd call busy
        with pytest.raises(Boom) as caught:
            run(pool.run(fail, 1))
        assert caught.value is problem
        _wait_idle(pool)
    assert pool.alive == 0 and pool.stuck == 0


def test_results_never_reach_the_wrong_caller_under_concurrency():
    pool = IsolatedCalls('x', max_threads=64)
    rng = random.Random(7)

    def job(i, delay):
        time.sleep(delay)
        if i % 5 == 0:
            raise ValueError(i)
        return i

    async def scenario():
        delays = [rng.random() * 0.03 for _ in range(40)]
        return await asyncio.gather(*[pool.run((lambda i=i, d=d: job(i, d)), 2) for i, d in enumerate(delays)],
                                    return_exceptions=True)

    out = run(scenario())
    for i, item in enumerate(out):
        if i % 5 == 0:
            assert isinstance(item, ValueError) and item.args == (i,)
        else:
            assert item == i
    _wait_idle(pool)
    assert pool.alive == 0 and pool.stuck == 0


def test_busy_at_the_limit_and_recovery(gates):
    pool = IsolatedCalls('x', max_threads=2)
    gate = gates()
    for _ in range(2):
        with pytest.raises(CallTimeout):
            run(pool.run(lambda: gate.wait(5), 0.02))
    with pytest.raises(CallBusy):
        run(pool.run(lambda: 1, 1))
    gate.set()
    _wait_idle(pool)
    assert run(pool.run(lambda: 1, 1)) == 1


# -- recovery from calls that never return -----------------------------------

class Ticker:
    """A settable monotonic clock for the write-off timing."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_six_never_returning_calls_are_written_off_after_the_long_timeout(gates):
    # The reviewer's reproduction: six calls that never return take all six
    # slots and a healthy call got CallBusy until the application restarted.
    clock = Ticker()
    pool = IsolatedCalls('x', clock=clock)
    gate = gates()
    for _ in range(6):
        with pytest.raises(CallTimeout):
            run(pool.run(lambda: gate.wait(30), 0.02))
    with pytest.raises(CallBusy) as busy:
        run(pool.run(lambda: 'healthy', 1))
    assert not isinstance(busy.value, isolated_call.CallRestartRequired)
    assert pool.state == 'busy' and pool.stuck == 6
    clock.now += isolated_call.DEFAULT_WRITE_OFF_AFTER
    assert run(pool.run(lambda: 'healthy', 1)) == 'healthy', 'stuck calls past the long timeout are written off'
    assert pool.written_off == 6 and pool.stuck == 0 and pool.state == 'ok'
    gate.set()
    deadline = time.monotonic() + 3
    while pool.written_off and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pool.written_off == 0, 'a written-off thread that returns leaves no trace'


def test_at_the_cap_of_written_off_threads_the_pool_says_a_restart_is_needed(gates):
    clock = Ticker()
    pool = IsolatedCalls('x', max_threads=1, write_off_after=60, max_written_off=1, clock=clock)
    gate = gates()
    with pytest.raises(CallTimeout):
        run(pool.run(lambda: gate.wait(30), 0.02))
    clock.now += 60
    with pytest.raises(CallTimeout):                       # the first is written off, the second hangs too
        run(pool.run(lambda: gate.wait(30), 0.02))
    assert (pool.written_off, pool.stuck) == (1, 1)
    with pytest.raises(CallBusy) as busy:
        run(pool.run(lambda: 1, 1))
    assert not isinstance(busy.value, isolated_call.CallRestartRequired), 'not overdue yet: only busy'
    clock.now += 60
    with pytest.raises(isolated_call.CallRestartRequired) as stuck:
        run(pool.run(lambda: 1, 1))
    assert isinstance(stuck.value, CallBusy), 'callers that only know CallBusy still catch it'
    assert pool.state == 'restart_required' and pool.status()['state'] == 'restart_required'
    assert isolated_call.RESTART_TEXT and isolated_call.RESTART_TEXT != isolated_call.BUSY_TEXT
    gate.set()                                             # the threads return after all
    deadline = time.monotonic() + 3
    while (pool.written_off or pool.alive) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pool.state == 'ok' and run(pool.run(lambda: 2, 1)) == 2


def test_the_blocking_twin_keeps_the_same_accounting(gates):
    clock = Ticker()
    pool = IsolatedCalls('x', max_threads=1, write_off_after=60, max_written_off=1, clock=clock)
    gate = gates()
    assert pool.call(lambda: 'ok', 1) == 'ok'
    with pytest.raises(ValueError):
        pool.call(lambda: (_ for _ in ()).throw(ValueError('x')), 1)
    with pytest.raises(CallTimeout):
        pool.call(lambda: gate.wait(30), 0.02)
    with pytest.raises(CallBusy):
        pool.call(lambda: 1, 1)
    clock.now += 60
    assert pool.call(lambda: 'again', 1) == 'again'


def test_the_tools_say_a_restart_is_needed_in_fixed_words(monkeypatch):
    from core.agent_tools import market_tools, news_tools
    from core.market.bars import LoaderBarSource, set_bar_source
    from providers.news.cn_news_provider import CnNewsProvider, set_cn_news_provider

    async def exhausted(self, function, timeout):
        raise isolated_call.CallRestartRequired(self.name)
    monkeypatch.setattr(IsolatedCalls, 'run', exhausted)
    monkeypatch.setitem(__import__('sys').modules, 'akshare', types.SimpleNamespace(stock_news_em=lambda **kw: None))
    set_bar_source(LoaderBarSource(lambda: None))
    set_cn_news_provider(CnNewsProvider())
    try:
        bars = run(market_tools.get_price_history('SHA:600519', 5))
        news = run(news_tools.get_stock_news('SHA:600519', 3))
    finally:
        set_bar_source(None)
        set_cn_news_provider(None)
    assert bars['error'] == isolated_call.RESTART_TEXT, bars
    assert news['error'] == isolated_call.RESTART_TEXT, news


def test_a_healthy_occupied_slot_keeps_the_pool_busy_not_restart_required(gates):
    # The reviewer's reproduction: one written-off thread, one overdue slot and one
    # healthy occupied slot said restart_required, then ok as soon as the healthy work finished.
    clock = Ticker()
    pool = IsolatedCalls('x', max_threads=2, write_off_after=60, max_written_off=1, clock=clock)
    stuck, healthy = gates(), gates()
    with pytest.raises(CallTimeout):
        run(pool.run(lambda: stuck.wait(30), 0.02))
    clock.now += 60
    with pytest.raises(CallTimeout):                       # the first is written off, the second hangs too
        run(pool.run(lambda: stuck.wait(30), 0.02))
    started = threading.Event()
    worker = threading.Thread(target=lambda: pool.call(lambda: (started.set(), healthy.wait(30)), 30))
    worker.start()
    assert started.wait(2)
    clock.now += 60                                        # the second stuck call is overdue now
    assert (pool.written_off, pool.stuck, pool.alive) == (1, 1, 2)
    assert pool.state == 'busy', 'a healthy call will give its slot back'
    with pytest.raises(CallBusy) as busy:
        run(pool.run(lambda: 1, 1))
    assert not isinstance(busy.value, isolated_call.CallRestartRequired)
    healthy.set()
    worker.join(5)
    assert pool.state == 'ok' and run(pool.run(lambda: 2, 1)) == 2
