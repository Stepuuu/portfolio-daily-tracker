"""Routine ``close_snapshot``: the daily close snapshot, made by the backend itself.

Due at 15:40 Asia/Shanghai on A-share trading days. It runs the existing engine
pipeline, ``engine/scripts/portfolio_daily_update.py --action auto-pipeline
--date <trading day>``, as a child process. That pipeline takes the portfolio
write lock, saves the snapshot, its ``history.csv`` row and the Markdown
report, and pushes the report to Feishu only when ``feishu_chat_id`` is
configured in the portfolio's ``config.json``. When the day's snapshot already
exists it does not make it again but completes the day from its checkpoint:
a history row that is missing or not the snapshot's is re-derived from the
saved snapshot (no prices are fetched), a report missing or made from
another snapshot is generated again, a push never started is sent, and an
``uncertain`` push is never resent; a snapshot stage that was interrupted
before it saved its JSON is run again. A held ticker without a price makes
it exit non-zero without saving a snapshot.

What a run reports:

* ``skipped``: demo mode (never runs the engine), scripted quotes
  (``TRACKER_QUOTE_SOURCE=fake``), switched off with ``TRACKER_CLOSE_SNAPSHOT=0``,
  or no ledger and no dated holdings file yet;
* ``unavailable``: the engine scripts are not part of this installation (the
  Docker backend image ships only ``dashboard/``), or the portfolio has no
  ``config.json`` (the row carries a documentation pointer); or there are no
  holdings in force on the day (category ``no_holdings_for_date``): the
  ledger has no confirmed event on or before it and no holdings file is
  dated on or before it. Holdings of a later day are never used for an
  earlier one, so such a day gets no number at all. The ledger is read
  read-only here (``core.ledger.confirmed_state``): deciding never creates,
  initialises or grows it;
* ``pending`` waiting (``scheduler.waiting``, category ``waiting``, shown as
  等待中): the only prices the snapshot lacks are closes not final yet, for
  example Hong Kong's at 15:40 (final at 16:10 after its closing auction), or
  New York's session of the day on a retry after 22:30. The engine says when
  the last of them is final (``CLOSE_NOT_FINAL_UNTIL=<time> MARKETS=<markets>``,
  ``portfolio_snapshot.close_final_at``, that is ``SessionClock.close_settled``);
  the run waits until then with a fixed reason (等待港股收盘价确定，16:10 后记入),
  is no failure, sends no notification, keeps its retries and runs at the
  first tick at or after that moment (at once on a manual request, on the
  same row, which waits on if the close is still not final). A price of the
  latest session stamped before its closing print (Hong Kong still at 15:55
  after 16:10; Today calls it stale) waits the same way, but the print may
  reach the source any minute (Yahoo's usual delay is 15 minutes: the 16:08
  print at 16:23): the engine adds ``CHECK_AT=<time>``, the clock's next
  five-minute mark, and the run looks again then (16:15, 16:20, 16:25) with
  the reason 等待港股收盘价确定，最晚 16:28 记入, booking the close as soon as it
  is verified. After the print plus the sources' usual delay (16:28) the
  day's last price is booked as an estimate (含估算) and the run ends ok. A
  wait that could only end after
  ``retry_until`` (or that the engine stated without a valid time) is a failure
  (category ``close_not_final``). A New York holding at 15:40 does not wait: its
  latest session, the night before, is final. An A-share-only portfolio is
  made at 15:40;
* ``ok`` / ``failed``: what the pipeline did. ``ok`` only when the snapshot,
  the day's ``history.csv`` row and the report are all there, the checkpoint
  says the row and the report were made from that very snapshot and the
  report file is still the one it made (``report_sha``; otherwise ``failed``
  with category ``incomplete``), and everything the run started is confirmed
  stopped (otherwise ``failed``, category ``cleanup_unconfirmed``). The
  pipeline itself refuses a snapshot JSON whose own ``date`` is not the day
  (another day's file under this name) and a quote without its own currency
  and session (``portfolio_snapshot.quote_problem``). Its summary says what
  this run repaired ("已补写当日历史记录", "已补生成日报"), whether the
  snapshot rests on an estimated input ("含估算：<the first reason>", from the
  snapshot's ``summary.estimated_reasons``: a holding that did not trade in its
  latest session booked at its last close, a day's last price that has no time
  after the closing print, a close the holiday table cannot
  verify, an exchange rate that is not current, holdings from the legacy
  files not yet confirmed into the ledger; the post-market brief shows this
  summary) and whether the
  holdings changed after the snapshot ("快照之后持仓有变化"). Failures are retried by the
  scheduler, also after midnight, until 09:00 the next morning
  (``retry_until``); an attempt that could only run later is given up and
  notified (category ``expired``). The run row keeps a bounded tail of the
  pipeline's output.

The child gets ``sys.executable``, ``PORTFOLIO_DIR``, its own process group and
a timeout. On a timeout or when the backend stops it is interrupted (SIGINT,
so the pipeline stops its own children and releases the lock), then killed.

No signal reaches a process that merely got a number the run's process once
had. Every process is held by identity (``_Process``): a pidfd on Linux where
the kernel offers one, else its start time (``/proc/<pid>/stat`` on Linux,
libproc on macOS), read when it is bound and compared again immediately
before every signal. A process group is signalled only while its leader,
held that way, still exists (also as a zombie: until it is reaped its number
cannot be reused). A ``run_child`` record names its group and the leader's
start time; a record whose number another process holds now is not the run's
any more and is left alone; one whose leader is gone while members remain is
not signalled (they are found by the run token on Linux) and counts as left
over until they end.

The pipeline starts the snapshot and the report with ``run_child``, each in a
session of its own, which a signal to the pipeline's group does not reach.
``run_engine`` returns, so a run is reported and a retry can take the
portfolio lock, only after it has killed and waited for (up to
``CHILD_STOP_SECONDS``) everything of the run it can find:

* the pipeline's own process group, while the pipeline runs (SIGINT, then
  SIGKILL after ``GRACE_SECONDS``);
* every group a ``run_child`` child recorded in the private directory passed
  in ``PORTFOLIO_CHILD_GROUPS_DIR``. The child records its group itself
  between fork and exec, so no child can run, or start anything, before its
  group is known, even when the pipeline is killed or suspended right after
  starting it. This holds on every POSIX platform;
* on Linux, every process whose environment carries the run's own
  ``PORTFOLIO_RUN_TOKEN`` (read from ``/proc/<pid>/environ``): all its
  descendants, whatever session or group they moved to and also after the
  pipeline exited normally, unless one cleared its environment or runs as
  another user. Each ``run_child`` child also gets SIGKILL when the pipeline
  dies (``PR_SET_PDEATHSIG``).

So on Linux nothing that kept the run's environment outlives the report. On
macOS (no ``/proc``, no parent-death signal) the guarantee is the first two
points: a descendant that left both the pipeline's group and the recorded
groups (it called ``setsid``/``setpgid`` itself), or that stayed in the
pipeline's group after the pipeline exited by itself, is not found. Whatever
outlives the wait is logged and counted in the run's detail and summary
(``leftover_groups``, ``leftover_processes``), and the run is not ``ok``.

Ownership: the run owns everything that carries its token or is in a group
recorded for it, and stops it before reporting, after a success too. A helper
meant to outlive the run must opt out when it is started: without the token
and the groups directory (``portfolio_write_lock.detached_environment``) and
in a session of its own, not through ``run_child``. Nothing else is ever
signalled.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
import time as time_module
from typing import Awaitable, Callable, Optional, Sequence, Tuple
import uuid

from core.ledger import LedgerError, LedgerUnavailable, confirmed_state, portfolio_directory
from core.market.calendar import SHANGHAI, SessionClock
from core.routines import scheduler

logger = logging.getLogger(__name__)

KIND = "close_snapshot"
TITLE = "收盘快照"
DUE_TIME = time(15, 40)
RETRY_UNTIL = time(9, 0)   # the next morning; see retry_until
TIMEOUT_SECONDS = 600
GRACE_SECONDS = 15
EXIT_POLL_SECONDS = 0.05   # how often the runner looks whether the pipeline has exited (_exited)
LOG_TAIL_CHARS = 4000
DOC = "docs/FULL_GUIDE_CN.md#收盘后自动快照"
DISABLE_ENV = "TRACKER_CLOSE_SNAPSHOT"
LOCK_CLAIM_ENV = "PORTFOLIO_WRITE_LOCK_INHERITED"
# Where the pipeline's run_child records its children's process groups
# (engine/scripts/portfolio_write_lock.py, CHILD_GROUPS_ENV).
CHILD_GROUPS_ENV = "PORTFOLIO_CHILD_GROUPS_DIR"
# A value unique to one run, inherited by everything the pipeline starts (Linux sweep).
RUN_TOKEN_ENV = "PORTFOLIO_RUN_TOKEN"
CHILD_STOP_SECONDS = 10
# Printed by portfolio_snapshot.py when a held ticker has no price.
MISSING_PRICE_MARKER = "以下持仓没有取得有效价格"
# Printed last by portfolio_snapshot.py when the only prices missing are closes not final yet
# (portfolio_snapshot.CLOSE_WAIT_MARKER): when the last of them is final, and their markets; when a closing
# print may still reach the source before then, the moment to look again (CLOSE_CHECK_MARKER).
CLOSE_WAIT = re.compile(r"CLOSE_NOT_FINAL_UNTIL=(\S*)(?: MARKETS=([A-Z,]+))?(?: CHECK_AT=(\S+))?")
MARKET_NAMES = {"CN": "A股", "HK": "港股", "US": "美股"}
# Printed by portfolio_daily_update.py when it completes a stage of an interrupted day.
HISTORY_REPAIRED_MARKER = "已补写当日历史记录"
REPORT_REPAIRED_MARKER = "已补生成日报"
HOLDINGS_CHANGED_MARKER = "当日快照之后持仓有变化"
# Printed by portfolio_daily_update.py when the day has no holdings in force (NO_HOLDINGS there).
NO_HOLDINGS_MARKER = "没有该日有效的持仓"
DATED_HOLDINGS = re.compile(r"\d{4}-\d{2}-\d{2}")
LEDGER_TIMEOUT = 5.0
# How much of an estimate's reason the run's summary quotes (the snapshot keeps all of them).
ESTIMATE_REASON_CHARS = 120


def due(clock: SessionClock) -> Optional[datetime]:
    """15:40 Shanghai time on an A-share trading day, otherwise None."""
    day = clock.local_date()
    if not clock.is_trading_day(day=day):
        return None
    return datetime.combine(day, DUE_TIME, tzinfo=SHANGHAI)


def retry_until(trading_date: str) -> datetime:
    """09:00 Shanghai the morning after the trading day.

    The engine prices a snapshot with the latest quotes it can download. A
    retry that runs before the next morning (a retry due after midnight, say)
    still sees the day's closes; one that could only run later (the backend was
    down overnight) could see the next session's prices, so it is given up and
    notified instead of saving a snapshot of the wrong day.
    """
    return datetime.combine(date.fromisoformat(trading_date) + timedelta(days=1), RETRY_UNTIL, tzinfo=SHANGHAI)


def default_engine_dir() -> Optional[Path]:
    """``engine/scripts`` next to ``dashboard/`` in a source checkout; None in the backend image."""
    dashboard = Path(__file__).resolve().parents[2]
    if dashboard.name != "dashboard":
        return None
    return dashboard.parent / "engine" / "scripts"


# -- the child process --------------------------------------------------------

@dataclass(frozen=True)
class EngineResult:
    returncode: Optional[int]
    output: str
    timed_out: bool = False
    # Process groups the pipeline started that still ran when the runner gave up waiting for them.
    leftover: Tuple[int, ...] = ()
    # Processes carrying the run's token that still ran then (Linux).
    stray: Tuple[int, ...] = ()


Runner = Callable[..., Awaitable[EngineResult]]


class _Tail:
    """The last ``limit`` characters of a byte stream, cut at a line start when possible."""

    def __init__(self, limit: int):
        self.limit = limit
        self.data = bytearray()

    def add(self, chunk: bytes) -> None:
        self.data += chunk
        excess = len(self.data) - self.limit * 4  # UTF-8 needs at most 4 bytes per character
        if excess > 0:
            del self.data[:excess]

    def text(self) -> str:
        text = self.data.decode("utf-8", errors="replace")
        if len(text) > self.limit:
            text = text[-self.limit:]
            newline = text.find("\n")
            if 0 <= newline < len(text) - 1:
                text = text[newline + 1:]
        return text


_PROC_PIDTBSDINFO = 3
_darwin_function = None


def _darwin_info(pid: int) -> Optional[Tuple[str, int, int, bool]]:
    """``(start, parent, group, zombie)`` through libproc on macOS; None for a zombie or a gone process."""
    global _darwin_function
    import ctypes

    class BsdInfo(ctypes.Structure):
        _fields_ = [("flags", ctypes.c_uint32), ("status", ctypes.c_uint32), ("xstatus", ctypes.c_uint32),
                    ("pid", ctypes.c_uint32), ("ppid", ctypes.c_uint32), ("uid", ctypes.c_uint32),
                    ("gid", ctypes.c_uint32), ("ruid", ctypes.c_uint32), ("rgid", ctypes.c_uint32),
                    ("svuid", ctypes.c_uint32), ("svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
                    ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32), ("nfiles", ctypes.c_uint32),
                    ("pgid", ctypes.c_uint32), ("pjobc", ctypes.c_uint32), ("tdev", ctypes.c_uint32),
                    ("tpgid", ctypes.c_uint32), ("nice", ctypes.c_int32), ("start_sec", ctypes.c_uint64),
                    ("start_usec", ctypes.c_uint64)]

    if _darwin_function is None:
        try:
            function = ctypes.CDLL(None, use_errno=True).proc_pidinfo
            function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
            function.restype = ctypes.c_int
            _darwin_function = function
        except (OSError, AttributeError):
            _darwin_function = False
    if not _darwin_function:
        return None
    info = BsdInfo()
    if _darwin_function(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
        return None
    return f"darwin:{info.start_sec}.{info.start_usec:06d}", info.ppid, info.pgid, info.status == 5


def _proc_info(pid: int) -> Optional[Tuple[str, int, int, bool]]:
    """``(start, parent, group, zombie)`` of process ``pid``, or None when it is gone or cannot be told.

    ``start`` is the identity ``portfolio_write_lock.process_start`` records:
    ``linux:<clock ticks after boot>`` or ``darwin:<seconds>.<microseconds>``.
    """
    if pid <= 0:
        return None
    if Path("/proc").is_dir():
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return f"linux:{fields[19]}", int(fields[1]), int(fields[2]), fields[0] in ("Z", "X")
        except (OSError, IndexError, ValueError):
            return None
    if sys.platform == "darwin":
        try:
            return _darwin_info(pid)
        except (OSError, ValueError, ImportError):
            return None
    return None


class _Process:
    """One process, bound by its identity: a signal never reaches another process given the same number.

    Held through a pidfd where the kernel offers one (Linux), else by its start
    time, read when it is bound and compared again immediately before every
    signal.
    """

    def __init__(self, pid: int, start: str, fd: Optional[int]):
        self.pid, self.start, self.fd = pid, start, fd

    @classmethod
    def bind(cls, pid: int, start: Optional[str] = None, parent: Optional[int] = None) -> Optional["_Process"]:
        """The process that has number ``pid`` now, when it is the one expected (``start``, ``parent``)."""
        fd = None
        opener = getattr(os, "pidfd_open", None)
        if opener is not None and hasattr(signal, "pidfd_send_signal"):
            try:
                fd = opener(pid)
            except ProcessLookupError:
                return None
            except OSError:
                fd = None   # no pidfds here (old kernel, sandbox): the start time decides
        # Read after the pidfd: when it matches, the pidfd is bound to this very process.
        info = _proc_info(pid)
        if info is None or (start is not None and info[0] != start) or (parent is not None and info[1] != parent):
            if fd is not None:
                os.close(fd)
            return None
        return cls(pid, info[0], fd)

    def _same(self) -> Optional[Tuple[str, int, int, bool]]:
        info = _proc_info(self.pid)
        return info if info is not None and info[0] == self.start else None

    def exists(self) -> bool:
        """Not reaped yet (alive or a zombie): its number cannot have been given to another process."""
        if self.fd is not None:
            try:
                signal.pidfd_send_signal(self.fd, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return self._same() is not None
        return self._same() is not None

    def alive(self) -> bool:
        """Exists and is not a zombie."""
        if not self.exists():
            return False
        info = self._same()
        return info is not None and not info[3]

    def kill(self, signum) -> bool:
        """Send ``signum`` to this process only; False when it is gone (nothing is sent then)."""
        if self.fd is not None:
            try:
                signal.pidfd_send_signal(self.fd, signum)
                return True
            except (ProcessLookupError, PermissionError):
                return False
        if self._same() is None:
            return False
        try:
            os.kill(self.pid, signum)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def close(self) -> None:
        if self.fd is not None:
            with suppress(OSError):
                os.close(self.fd)
            self.fd = None


def _signal_group(leader: Optional[_Process], group: int, signum) -> bool:
    """Signal process group ``group`` only while its leader ``leader`` still exists (also as a zombie).

    Until the leader is reaped its number cannot be given to another process,
    so the group with that number is the one it leads. False when nothing was sent.
    """
    if leader is None or not leader.exists():
        return False
    try:
        os.killpg(group, signum)
        return True
    except (ProcessLookupError, PermissionError):
        return False


async def _exited(process) -> int:
    """The child's return code, as soon as it has exited.

    Not ``process.wait()``: from Python 3.12 a wait that begins before the
    exit also waits until the child's pipes are closed, and a process the
    pipeline left behind can hold the output pipe open, so the run would be
    reported as timed out (and stopping it would wait out the grace). The
    return code is known once the child is reaped, whatever holds the pipe;
    what is left behind is then stopped by ``run_engine``.
    """
    while process.returncode is None:
        await asyncio.sleep(EXIT_POLL_SECONDS)
    return process.returncode


async def _stop(process, grace: float, leader: Optional[_Process] = None) -> None:
    """Interrupt the child's group (the pipeline then stops its own children), then kill it."""
    if process.returncode is not None:
        return
    _signal_group(leader, process.pid, signal.SIGINT)
    try:
        await asyncio.wait_for(_exited(process), grace)
    except asyncio.TimeoutError:
        _signal_group(leader, process.pid, signal.SIGKILL)
        await _exited(process)


def _group_alive(group: int) -> bool:
    """Whether a process other than a zombie is left in ``group`` (one this process may signal)."""
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False   # another user's group: not one the pipeline started
    proc = Path("/proc")
    if not proc.is_dir():
        return True   # zombies cannot be told apart here; the caller's deadline applies
    for status in proc.glob("[0-9]*/stat"):
        try:
            state, _parent, process_group = status.read_text().rsplit(")", 1)[1].split()[:3]
        except (OSError, ValueError):
            continue
        if process_group == str(group) and state not in ("Z", "X"):
            return True
    return False


def _token_processes(token: str) -> Tuple[int, ...]:
    """Processes other than zombies whose environment carries ``RUN_TOKEN_ENV=token``; ``()`` without ``/proc``."""
    proc = Path("/proc")
    if not proc.is_dir():
        return ()
    own, found = os.getpid(), []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == own:
            continue
        if _carries(int(entry.name), token):
            found.append(int(entry.name))
    return tuple(found)


def _carries(pid: int, token: str) -> bool:
    """Whether process ``pid``'s environment carries the run's token (empty for a zombie)."""
    try:
        environment = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return False   # gone, or another user's
    return f"{RUN_TOKEN_ENV}={token}".encode() in environment.split(b"\0")


def _token_handles(token: str) -> list:
    """The run's processes found by its token, each bound by identity and checked to carry it once bound."""
    handles = []
    for pid in _token_processes(token):
        handle = _Process.bind(pid)
        if handle is None:
            continue
        # The environment is read again after binding: the bound process itself carries the token.
        if _carries(pid, token) and handle.alive():
            handles.append(handle)
        else:
            handle.close()
    return handles


def _sweep(token: str, deadline: float) -> Tuple[int, ...]:
    """Kill every process of the run found by its token until none is left or ``deadline`` (monotonic)."""
    left = _token_handles(token)
    while left:
        for handle in left:
            handle.kill(signal.SIGKILL)
            handle.close()
        if time_module.monotonic() >= deadline:
            break
        time_module.sleep(0.02)
        left = _token_handles(token)
    for handle in left:
        handle.close()
    return tuple(handle.pid for handle in left)


def _stop_descendants(folder: str, token: str,
                      seconds: float = CHILD_STOP_SECONDS) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """``_stop_children`` and then the token sweep; ``(groups left, processes left)``."""
    deadline = time_module.monotonic() + seconds
    groups = _stop_children(folder, seconds)
    return groups, _sweep(token, max(deadline, time_module.monotonic() + 1))


def _records(folder: str) -> list:
    """``[(group, leader start or None)]`` recorded by ``run_child`` children in ``folder``."""
    found = []
    with suppress(OSError):
        for entry in os.scandir(folder):
            try:
                group = int(entry.name)
            except ValueError:
                continue
            if group <= 1 or group in (os.getpid(), os.getpgrp()):
                continue
            try:
                parts = Path(entry.path).read_text().split()
            except OSError:
                parts = []
            found.append((group, parts[1] if len(parts) > 1 and parts[0] == str(group) else None))
    return found


def _group_left(group: int, start: Optional[str]) -> bool:
    """Whether the recorded group may still have a member of the run.

    Not when another process holds the number now: the recorded leader is
    gone and so is its group (a number in use as a group cannot be given to a
    new process). Otherwise whenever a process other than a zombie is in it.
    """
    info = _proc_info(group)
    if info is not None and start is not None and info[0] != start:
        return False
    return _group_alive(group)


def _stop_children(folder: str, seconds: float = CHILD_STOP_SECONDS) -> Tuple[int, ...]:
    """Kill every process group the pipeline recorded in ``folder``, wait until none runs, remove ``folder``.

    The pipeline's children run in sessions of their own (``run_child``), so
    signalling the pipeline's group does not reach them. A group is signalled
    only while its recorded leader (same number, same start time) still
    exists. Returns the groups that may still hold a process of the run at
    the deadline (normally none).
    """
    try:
        records = _records(folder)
        for group, start in records:
            leader = _Process.bind(group, start) if start is not None else None
            if leader is not None:
                _signal_group(leader, group, signal.SIGKILL)
                leader.close()
        deadline = time_module.monotonic() + seconds
        left = [(group, start) for group, start in records if _group_left(group, start)]
        while left and time_module.monotonic() < deadline:
            time_module.sleep(0.02)
            left = [(group, start) for group, start in left if _group_left(group, start)]
        return tuple(group for group, _start in left)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


async def run_engine(command: Sequence[str], *, env: dict, cwd: Path, timeout: float,
                     grace: float = GRACE_SECONDS, tail_chars: int = LOG_TAIL_CHARS) -> EngineResult:
    """Run ``command`` in its own process group; keep only the end of its output.

    Returns only after the command and every child it recorded through
    ``run_child`` (``CHILD_GROUPS_ENV``) have stopped, also after a timeout or
    a cancellation: a termination is reported, and a retry can take the
    portfolio lock, only once nothing of the run can still write.
    """
    children = tempfile.mkdtemp(prefix="portfolio-children-")
    token = uuid.uuid4().hex
    env = {**env, CHILD_GROUPS_ENV: children, RUN_TOKEN_ENV: token}
    try:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    except BaseException:
        shutil.rmtree(children, ignore_errors=True)
        raise
    # Our own child, so not reaped before this: bound to it, its group is signalled only while it exists.
    leader = _Process.bind(process.pid, parent=os.getpid())
    leftover: Tuple[int, ...] = ()
    stray: Tuple[int, ...] = ()
    try:
        result = await _supervise(process, timeout, grace, tail_chars, leader)
    finally:
        if leader is not None:
            leader.close()
        # Also when cancelled: the worker thread kills them even if nobody waits for it.
        leftover, stray = await asyncio.to_thread(_stop_descendants, children, token)
        if leftover or stray:
            logger.warning("close snapshot: %s process group(s) and %s process(es) of the pipeline did not stop",
                           len(leftover), len(stray))
    return EngineResult(result.returncode, result.output, result.timed_out, leftover, stray)


async def _supervise(process, timeout: float, grace: float, tail_chars: int,
                     leader: Optional[_Process] = None) -> EngineResult:
    tail = _Tail(tail_chars)

    async def collect():
        while True:
            chunk = await process.stdout.read(65536)
            if not chunk:
                return
            tail.add(chunk)

    reader = asyncio.ensure_future(collect())
    timed_out = False
    try:
        try:
            await asyncio.wait_for(_exited(process), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await _stop(process, grace, leader)
        # The rest of the output is still in the pipe; never wait long for it.
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(reader), 5)
    except BaseException:
        # Cancelled because the backend stops: stop the engine before giving up.
        await _stop(process, grace, leader)
        raise
    finally:
        if not reader.done():
            reader.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await reader
    return EngineResult(process.returncode, tail.text(), timed_out)


_SECRETS = (
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1[redacted]@"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+"), r"\1[redacted]"),
    (re.compile(r"(?i)\bbearer\s+[^\s,;]+"), "Bearer [redacted]"),
)


def redact(text: str) -> str:
    """Remove credentials that a proxy URL or an error message might carry."""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


# -- the routine --------------------------------------------------------------

def _has_dated_holdings(portfolio: Path, on_or_before: Optional[str] = None) -> bool:
    """Whether ``holdings/`` has a dated file (dated on or before ``on_or_before`` when given)."""
    folder = portfolio / "holdings"
    return folder.is_dir() and any(DATED_HOLDINGS.fullmatch(path.stem) and path.is_file()
                                   and (on_or_before is None or path.stem <= on_or_before)
                                   for path in folder.glob("*.json"))


def _ledger_state(portfolio: Path, trading_date: str):
    """The confirmed ledger as of the day, read-only: a state, ``None`` without a ledger, ``...`` when unreadable."""
    try:
        return confirmed_state(portfolio, timeout=LEDGER_TIMEOUT, as_of=trading_date)
    except (LedgerUnavailable, LedgerError, sqlite3.Error, OSError, ValueError) as exc:
        logger.warning("close snapshot: the ledger could not be read: %s", type(exc).__name__)
        return ...


def _checkpoint(portfolio: Path, trading_date: str) -> dict:
    try:
        state = json.loads((portfolio / "pipeline-state" / f"{trading_date}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _history_has_row(portfolio: Path, trading_date: str) -> bool:
    try:
        lines = (portfolio / "history.csv").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    return any(line.startswith(f"{trading_date},") for line in lines[1:])


# Moves the days' reports out of the code checkout (``portfolio_daily_update.REPORTS_DIR``).
REPORTS_DIR_ENV = "TRACKER_REPORTS_DIR"


def reports_directory(engine: Path) -> Path:
    """Where the pipeline saves the days' reports: ``TRACKER_REPORTS_DIR`` when set (a relative one from this
    process's directory), else ``engine/reports`` next to its scripts, the engine's own rule."""
    configured = os.environ.get(REPORTS_DIR_ENV, "").strip()
    return Path(configured).expanduser().absolute() if configured else Path(engine).parent / "reports"


def _report_file(engine: Path, trading_date: str) -> Path:
    """Where the pipeline saves the day's report (``reports_directory``)."""
    return reports_directory(engine) / f"portfolio-{trading_date.replace('-', '')}.md"


def _file_sha(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _estimate_note(snapshot: Path) -> Optional[str]:
    """``含估算：<first reason>[ 等 N 项]`` when the day's snapshot rests on an estimated input, else None.

    The engine records it in the snapshot (``summary.estimated``, ``summary.estimated_reasons``,
    ``portfolio_snapshot.calculate_snapshot``); the post-market brief shows this run's summary.
    """
    try:
        summary = json.loads(snapshot.read_text(encoding="utf-8")).get("summary")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(summary, dict) or summary.get("estimated") is not True:
        return None
    reasons = summary.get("estimated_reasons")
    reasons = [str(reason)[:ESTIMATE_REASON_CHARS] for reason in reasons if reason] if isinstance(reasons, list) else []
    if not reasons:
        return "含估算"
    return f"含估算：{reasons[0]}" + (f" 等 {len(reasons)} 项" if len(reasons) > 1 else "")


def _succeeded(portfolio: Path, engine: Path, trading_date: str, output: str, detail: dict) -> scheduler.Outcome:
    """``ok`` only when the snapshot, the day's history row and the report are all there and belong together.

    The pipeline's checkpoint names the snapshot (by content hash) that the
    history row and the report were made from; a stage made from another
    snapshot of the day is not done. A checkpoint of the previous release
    (without those names), or none (the snapshot came by another route), is
    judged by the files alone.
    """
    snapshot = portfolio / "snapshots" / f"{trading_date}.json"
    if not snapshot.is_file():
        return scheduler.failed("引擎报告成功，但没有找到当日快照文件", "no_snapshot", detail)
    state = _checkpoint(portfolio, trading_date)
    current = _file_sha(snapshot) if "snapshot_sha" in state else None
    report = _report_file(engine, trading_date)
    # A report replaced or changed after the pipeline made it is not the day's report.
    report_made = "report_sha" not in state or state.get("report_sha") == _file_sha(report)
    missing = [label for label, done, stage in (
        ("历史记录（history.csv）", _history_has_row(portfolio, trading_date), "history_snapshot"),
        ("日报", report.is_file() and report_made, "report_snapshot"))
        if not done or (current is not None and state.get(stage) != current)]
    if missing:
        return scheduler.failed(f"引擎报告成功，但当日{'和'.join(missing)}没有完成；重试时会补齐", "incomplete", detail)
    repaired = [text for marker, text in ((HISTORY_REPAIRED_MARKER, "已补写当日历史记录"),
                                          (REPORT_REPAIRED_MARKER, "已补生成日报")) if marker in output]
    if "今日快照已存在" in output or not state:   # without a checkpoint: saved by another route earlier
        parts = ["当日快照已存在" if repaired else "当日快照已存在，未重复生成"]
    else:
        parts = ["快照已生成"]
    parts += repaired
    if REPORT_REPAIRED_MARKER not in output and state:
        parts.append("日报已保存")
    estimate = _estimate_note(snapshot)
    if estimate:
        parts.append(estimate)
    if HOLDINGS_CHANGED_MARKER in output:
        parts.append("快照之后持仓有变化，未自动重做（可手动运行流水线）")
    push = state.get("push")
    if push == "complete":
        parts.append("日报已推送飞书")
    elif push == "uncertain":
        parts.append("飞书推送结果未确认，不会自动重发")
    return scheduler.ok("；".join(parts), detail)


def wait_reason(markets: Sequence[str], until: datetime, trading_date: str, latest: bool = False) -> str:
    """``等待港股收盘价确定，16:10 后记入`` (Shanghai time; ``次日`` after the trading day): the engine's words too.

    With ``latest`` (the run looks again before ``until``): ``等待港股收盘价确定，最晚 16:28 记入``.
    """
    local = until.astimezone(SHANGHAI)
    names = "、".join(MARKET_NAMES[market] for market in ("CN", "HK", "US") if market in markets)
    day = "次日 " if local.date().isoformat() > trading_date else ""
    if latest:
        return f"等待{names}收盘价确定，最晚 {day}{local:%H:%M} 记入"
    return f"等待{names}收盘价确定，{day}{local:%H:%M} 后记入"


def _close_wait(output: str, trading_date: str, now: datetime, detail: dict) -> Optional[scheduler.Outcome]:
    """The wait the engine asked for (``CLOSE_WAIT``), a failure when it cannot be one, or None without it.

    With ``CHECK_AT`` (a closing print that may still reach the source) the run waits only until that moment,
    the reason naming the latest one (最晚 16:28 记入); without it, until the close is final.
    """
    found = CLOSE_WAIT.findall(output)
    if not found:
        return None
    text, named, look = found[-1]
    markets = [market for market in (named or "").split(",") if market in MARKET_NAMES]
    try:
        until = datetime.fromisoformat(text)
    except ValueError:
        until = None
    if until is None or until.tzinfo is None or not markets:
        return scheduler.failed("快照引擎说收盘价尚未确定，但没有给出有效的确定时间；未生成快照", "close_not_final", detail)
    deadline = retry_until(trading_date)
    if until > deadline:
        return scheduler.failed(f"收盘价要到补做期限（{deadline:%m-%d %H:%M}）之后才确定，未生成快照",
                                "close_not_final", detail)
    until = max(until.astimezone(SHANGHAI), now)
    try:
        check = datetime.fromisoformat(look) if look else None
    except ValueError:
        check = None   # not understood: wait until the close is final, as without it
    if check is None or check.tzinfo is None or check > until:
        return scheduler.waiting(wait_reason(markets, until, trading_date), until,
                                 {**detail, "waiting_for": markets, "not_before": until.isoformat(timespec="seconds")})
    check = max(check.astimezone(SHANGHAI), now)
    return scheduler.waiting(wait_reason(markets, until, trading_date, latest=True), check,
                             {**detail, "waiting_for": markets, "not_before": check.isoformat(timespec="seconds"),
                              "book_by": until.isoformat(timespec="seconds")})


async def run(trading_date: str, *, engine_dir: Optional[Path] = None, portfolio_dir: Optional[Path] = None,
              runner: Optional[Runner] = None, timeout: float = TIMEOUT_SECONDS,
              now: Optional[Callable[[], datetime]] = None) -> scheduler.Outcome:
    """Make the close snapshot for ``trading_date`` (YYYY-MM-DD) with the engine pipeline.

    ``now`` (default: the scheduler's clock, ``scheduler.wall_clock``) dates a wait.
    """
    if os.environ.get("TRACKER_DEMO_MODE") == "1":
        return scheduler.skipped("演示模式", "demo")
    if os.environ.get("TRACKER_QUOTE_SOURCE", "").strip().lower() == "fake":
        return scheduler.skipped("模拟行情模式（TRACKER_QUOTE_SOURCE=fake），不运行快照引擎", "fake_quotes")
    if os.environ.get(DISABLE_ENV, "").strip().lower() in {"0", "off", "false", "no"}:
        return scheduler.skipped(f"已关闭（{DISABLE_ENV}=0）", "disabled")

    engine = engine_dir if engine_dir is not None else default_engine_dir()
    script = None if engine is None else Path(engine) / "portfolio_daily_update.py"
    if script is None or not script.is_file():
        return scheduler.unavailable(f"此安装不含快照引擎（engine/scripts），例如 Docker 后端镜像。见 {DOC}",
                                     "engine_missing", {"doc": DOC})

    portfolio = Path(portfolio_dir if portfolio_dir is not None else portfolio_directory())
    ledger = await asyncio.to_thread(_ledger_state, portfolio, trading_date)   # may wait for a writer
    if ledger is None and not _has_dated_holdings(portfolio):
        return scheduler.skipped("还没有账本，也没有持仓文件，无需快照", "no_data")
    in_force = ledger is ... or (ledger is not None and ledger.get("date") is not None)
    if not in_force and not _has_dated_holdings(portfolio, trading_date):
        return scheduler.unavailable(f"{trading_date} 没有生效的持仓：账本在该日及之前没有确认记录，"
                                     "持仓文件也都晚于该日；不会用之后日期的持仓计算", "no_holdings_for_date")
    if not (portfolio / "config.json").is_file():
        return scheduler.unavailable(f"数据目录中没有引擎配置 config.json。见 {DOC}", "config_missing", {"doc": DOC})

    command = [sys.executable, str(script), "--action", "auto-pipeline", "--date", trading_date]
    env = {**os.environ, "PORTFOLIO_DIR": str(portfolio.resolve()), "PYTHONUNBUFFERED": "1"}
    env.pop(LOCK_CLAIM_ENV, None)  # the backend holds no portfolio lock; the pipeline takes its own
    if os.environ.get(REPORTS_DIR_ENV, "").strip():
        env[REPORTS_DIR_ENV] = str(reports_directory(engine))   # the directory this routine reads, absolute
    try:
        result = await (runner or run_engine)(command, env=env, cwd=script.parent, timeout=timeout)
    except OSError as exc:
        logger.warning("close snapshot: the engine could not be started: %s", type(exc).__name__)
        return scheduler.failed("无法启动快照引擎（详情见服务器日志）", "launch_failed")

    output = redact(result.output)
    detail = {"log_tail": output, "exit_code": result.returncode}
    if result.leftover:
        detail["leftover_groups"] = len(result.leftover)
    if result.stray:
        detail["leftover_processes"] = len(result.stray)
    stray = (f"有 {len(result.leftover)} 组子进程、{len(result.stray)} 个进程未能确认停止"
             if result.leftover or result.stray else "")
    note = f"；{stray}" if stray else ""
    if result.timed_out:
        return scheduler.failed(f"超过 {int(timeout // 60) or 1} 分钟未完成，已终止{note}", "timeout", detail)
    if stray and (result.returncode == 0 or NO_HOLDINGS_MARKER in output):
        # Nothing is reported done (or final) while something the run started may still write.
        return scheduler.failed(f"快照引擎已结束，但{stray}；重试前请检查这些进程", "cleanup_unconfirmed", detail)
    if result.returncode == 0:
        return _succeeded(portfolio, script.parent, trading_date, output, detail)
    if not stray:   # a wait only once nothing of the run is left that could still write
        moment = SessionClock((now or scheduler.wall_clock)).now()
        wait = _close_wait(output, trading_date, moment, detail)
        if wait is not None:
            return wait
    if NO_HOLDINGS_MARKER in output:
        return scheduler.unavailable(f"{trading_date} 没有生效的持仓，未生成快照（不会用其他日期的持仓计算）",
                                     "no_holdings_for_date", detail)
    if MISSING_PRICE_MARKER in output:
        return scheduler.failed(f"部分持仓没有取得有效价格，未生成快照（不会按 0 计算）{note}", "missing_price", detail)
    return scheduler.failed(f"快照引擎失败（退出码 {result.returncode}）{note}", "engine_error", detail)


scheduler.register(KIND, due, run, title=TITLE, retry_until=retry_until)
