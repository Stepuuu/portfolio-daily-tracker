"""Run a blocking provider call off the event loop without letting it hang the app.

A synchronous request cannot be interrupted: ``asyncio.wait_for`` only stops
*waiting* for it. With a plain thread pool three hung requests would occupy
every worker for good and every later call would queue behind them forever.

``IsolatedCalls`` gives each provider function its own small budget instead:

* every call runs in its own daemon thread (a hung one can neither block a
  later call nor keep the process from exiting);
* a call that times out is *abandoned*: it is counted as ``stuck`` and keeps
  its slot until the underlying function finally returns;
* at most ``max_threads`` slots (running plus stuck) exist per function. At
  that limit new calls fail at once with ``CallBusy`` (a fixed "provider busy"
  error) instead of piling up, and as soon as a stuck call returns there is room
  again. Below the limit a healthy call is always served, however many earlier
  calls hung;
* a stuck call that has run for ``write_off_after`` seconds (default
  ``DEFAULT_WRITE_OFF_AFTER``, 10 minutes: far longer than any provider
  timeout) is *written off*: it gives its slot back so a new call can run,
  and its thread, which cannot be stopped, is only counted (``written_off``)
  until it returns;
* at most ``max_written_off`` threads (default ``max_threads``) are written
  off at a time. When every slot is held by an overdue call that can no
  longer be written off (one healthy or recently abandoned call is enough to
  keep it ``busy``: that slot comes back), the pool is ``restart_required``: calls fail with
  ``CallRestartRequired`` (a ``CallBusy``, so callers that only know that
  still catch it) and ``RESTART_TEXT`` says only a restart frees the
  threads. Should they return after all, the pool recovers by itself.

So a function whose calls never return costs at most ``max_threads +
max_written_off`` threads, and every state is reported (``state``,
``status()``) instead of looking like a slow provider.

``run`` is for coroutines; ``call`` is its blocking twin for plain threads,
with the same accounting.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable, Optional

BUSY_TEXT = '数据源繁忙：此前的请求仍未返回，请稍后再试'
RESTART_TEXT = '数据源的多次请求一直没有返回，已无法再自动恢复：请重启应用后再试'
DEFAULT_MAX_THREADS = 6
DEFAULT_WRITE_OFF_AFTER = 600.0     # seconds since a call started

OK, BUSY, RESTART_REQUIRED = 'ok', 'busy', 'restart_required'


class CallTimeout(Exception):
    """The call did not return in time (its thread may still be running)."""


class CallBusy(Exception):
    """Too many earlier calls to this function are still running or stuck."""


class CallRestartRequired(CallBusy):
    """Every slot is held by calls that never returned and none can be written off any more."""


class _Slot:
    __slots__ = ('started', 'finished', 'abandoned')

    def __init__(self, started: float):
        self.started = started
        self.finished = False
        self.abandoned = False


class IsolatedCalls:
    def __init__(self, name: str = 'provider', max_threads: int = DEFAULT_MAX_THREADS, *,
                 write_off_after: float = DEFAULT_WRITE_OFF_AFTER, max_written_off: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic, thread_name: Optional[str] = None):
        self.name = name
        self.max_threads = max(int(max_threads), 1)
        self.write_off_after = max(float(write_off_after), 0.0)
        self.max_written_off = max(int(self.max_threads if max_written_off is None else max_written_off), 0)
        self._clock = clock
        self._thread_name = thread_name or f'isolated-{name}'
        self._lock = threading.Lock()
        self._slots: set = set()           # running or stuck: they hold capacity
        self._written_off: set = set()     # given up on; their threads still run

    # -- what the pool looks like ---------------------------------------------

    @property
    def stuck(self) -> int:
        """Timed-out calls that still hold a slot (their thread has not returned)."""
        with self._lock:
            return sum(1 for slot in self._slots if slot.abandoned)

    @property
    def alive(self) -> int:
        """Slots in use: calls running or stuck (written-off threads not included)."""
        with self._lock:
            return len(self._slots)

    @property
    def written_off(self) -> int:
        """Threads given up on after ``write_off_after`` that have not returned yet."""
        with self._lock:
            return len(self._written_off)

    @property
    def state(self) -> str:
        """``ok``, ``busy`` (no free slot yet) or ``restart_required`` (none will come back by itself)."""
        with self._lock:
            now = self._clock()
            self._write_off(now)
            if len(self._slots) < self.max_threads:
                return OK
            return RESTART_REQUIRED if self._beyond_recovery(now) else BUSY

    def status(self) -> dict:
        state = self.state
        with self._lock:
            return {'state': state, 'alive': len(self._slots),
                    'stuck': sum(1 for slot in self._slots if slot.abandoned),
                    'written_off': len(self._written_off), 'max_threads': self.max_threads,
                    'max_written_off': self.max_written_off, 'write_off_after': self.write_off_after}

    # -- accounting (call with the lock held) ------------------------------------

    def _overdue(self, slot: _Slot, now: float) -> bool:
        return slot.abandoned and not slot.finished and now - slot.started >= self.write_off_after

    def _write_off(self, now: float) -> None:
        for slot in sorted(self._slots, key=lambda slot: slot.started):
            if len(self._written_off) >= self.max_written_off:
                return
            if self._overdue(slot, now):
                self._slots.discard(slot)
                self._written_off.add(slot)

    def _beyond_recovery(self, now: float) -> bool:
        # Only when no occupied slot can come back by itself: a running or a
        # recently abandoned call will still give its slot back.
        return (len(self._written_off) >= self.max_written_off and bool(self._slots)
                and all(self._overdue(slot, now) for slot in self._slots))

    def _reserve(self) -> _Slot:
        with self._lock:
            now = self._clock()
            self._write_off(now)
            if len(self._slots) >= self.max_threads:
                raise (CallRestartRequired if self._beyond_recovery(now) else CallBusy)(self.name)
            slot = _Slot(now)
            self._slots.add(slot)
            return slot

    def _finish(self, slot: _Slot) -> None:
        with self._lock:
            slot.finished = True
            self._slots.discard(slot)
            self._written_off.discard(slot)

    def _abandon(self, slot: _Slot) -> bool:
        """Mark a call the caller stopped waiting for; False when it has already returned."""
        with self._lock:
            if slot.finished:
                return False
            slot.abandoned = True
            return True

    def _start(self, slot: _Slot, work: Callable[[], None]) -> None:
        try:
            threading.Thread(target=work, name=self._thread_name, daemon=True).start()
        except BaseException:
            self._finish(slot)                    # no worker exists: give the reservation back
            raise

    # -- calls -----------------------------------------------------------------

    async def run(self, function: Callable[[], Any], timeout: float) -> Any:
        """``function()``'s result; ``CallTimeout`` / ``CallBusy`` / ``CallRestartRequired``, or what ``function`` raised."""
        slot = self._reserve()
        loop = asyncio.get_running_loop()
        future: 'asyncio.Future[Any]' = loop.create_future()

        def deliver(value: Any, failure: Any) -> None:
            if future.done():
                return
            if failure is not None:
                future.set_exception(failure)
            else:
                future.set_result(value)

        def work() -> None:
            value, failure = None, None
            try:
                value = function()
            except BaseException as exc:          # handed to the awaiting task, never lost
                failure = exc
            self._finish(slot)
            try:
                loop.call_soon_threadsafe(deliver, value, failure)
            except RuntimeError:                  # the loop is gone: nobody is waiting
                pass

        self._start(slot, work)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except BaseException as exc:
            if isinstance(exc, (asyncio.TimeoutError, asyncio.CancelledError)):
                self._abandon(slot)
            if isinstance(exc, asyncio.TimeoutError):
                raise CallTimeout(self.name) from None
            raise

    def call(self, function: Callable[[], Any], timeout: float) -> Any:
        """The blocking twin of ``run`` for a plain thread: the same accounting and errors."""
        slot = self._reserve()
        outcome: dict = {}
        done = threading.Event()

        def work() -> None:
            try:
                outcome['value'] = function()
            except BaseException as exc:
                outcome['failure'] = exc
            finally:
                self._finish(slot)
                done.set()

        self._start(slot, work)
        if not done.wait(timeout) and self._abandon(slot):
            raise CallTimeout(self.name)
        done.wait()                               # it returned just as the wait ran out
        if 'failure' in outcome:
            raise outcome['failure']
        return outcome.get('value')
