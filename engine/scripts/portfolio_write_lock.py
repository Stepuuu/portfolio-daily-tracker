"""Serialize complete portfolio read/modify/write operations across entrypoints.

The lock is reentrant in one thread and shared with the Feishu portfolio adapter.
Do not hold it while waiting for a child CLI that acquires the same lock. Call
non-entrypoint pipeline functions directly when the parent already owns it.

A child process cannot re-enter its parent's flock, so a lock owner that waits
for a child writer starts it with ``run_child``. Inheritance is checked, never
assumed:

* ``run_child`` sets ``PORTFOLIO_WRITE_LOCK_INHERITED`` to the absolute lock-file
  path only when the calling thread holds that lock (``holds_lock``); otherwise
  it removes any such variable from the child's environment.
* The child enters ``portfolio_write_lock(directory, accept_inherited=True)``. A
  variable naming another lock file is ignored. For its own lock file the child
  probes with a non-blocking flock through a fresh open: if that fails, the lock
  is held and the child works under it without locking; if it succeeds, nobody
  held the lock, the claim was false, and the child keeps the lock it has just
  taken, like any other holder.
* The Feishu adapter's pipeline child receives the adapter's locked descriptor
  and registers it with ``adopt_descriptor``, which checks that the descriptor
  holds this portfolio's lock. While that child delivers a report it unlocks the
  descriptor; a snapshot started then finds the lock free and takes it itself
  (a claim it carries is then false), and the adapter's child locks again only
  after that snapshot has finished.

The lock uses ``fcntl.flock``. This module imports without it (native Windows),
so entrypoints that write nothing still work there; taking the lock raises
``LockUnavailable``.

What a run owns. When the backend runs the pipeline (``core.routines.close_snapshot``)
it sets ``PORTFOLIO_RUN_TOKEN`` and ``PORTFOLIO_CHILD_GROUPS_DIR``; every process
that inherits that token, and every process group a ``run_child`` child records,
belongs to that run and is stopped before the run is reported, also when the run
succeeded. A helper meant to outlive the run (a long-lived server the pipeline
starts or reuses) must opt out: start it with ``detached_environment()`` (no run
token, no groups directory, no lock claim) and in a session of its own, not with
``run_child``. Nothing else is ever signalled: a recorded group carries its
leader's start time (``process_start``), and the runner signals it only while that
very process still holds the number.
"""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading


INHERITED_ENV = 'PORTFOLIO_WRITE_LOCK_INHERITED'

_registry = {}
_registry_guard = threading.Lock()
# Lock paths this process works under while an ancestor holds the lock: the
# adopted descriptor, or None after a verified inherited claim.
_borrowed = {}


class LockUnavailable(RuntimeError):
    """This platform cannot take the portfolio write lock."""


def _fcntl():
    try:
        import fcntl
    except ImportError:
        raise LockUnavailable('portfolio write lock unavailable: this platform has no fcntl '
                              '(native Windows); run portfolio writes on Linux, macOS or WSL') from None
    return fcntl


def _lock_path(portfolio_dir):
    return Path(portfolio_dir).resolve() / '.portfolio-update.lock'


def inherited_lock_environment(portfolio_dir, environment=None):
    """Environment that claims this lock for a child; it proves nothing (see ``run_child``)."""
    base = os.environ if environment is None else environment
    return {**base, INHERITED_ENV: str(_lock_path(portfolio_dir))}


def lock_inherited(portfolio_dir):
    """Whether the environment claims this portfolio's lock file.

    The claim alone does not show that anyone holds the lock;
    ``portfolio_write_lock(..., accept_inherited=True)`` verifies it.
    """
    return os.environ.get(INHERITED_ENV) == str(_lock_path(portfolio_dir))


def holds_lock(portfolio_dir):
    """Whether this thread holds the lock, or this process works under an ancestor's."""
    key = str(_lock_path(portfolio_dir))
    if key in _borrowed:
        return True
    entry = _registry.get(key)
    return bool(entry and entry['depth'] and entry['owner'] == threading.get_ident())


def adopt_descriptor(portfolio_dir, descriptor):
    """Work under the lock that ``descriptor``, inherited from the parent, holds.

    Returns False and records nothing unless the descriptor is open on this
    portfolio's lock file and its open file description holds the lock (a flock
    by the description that already holds it succeeds at once).
    """
    fcntl = _fcntl()
    path = _lock_path(portfolio_dir)
    try:
        held, own = os.fstat(descriptor), os.stat(path)
    except OSError:
        return False
    if (held.st_dev, held.st_ino) != (own.st_dev, own.st_ino):
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    _borrowed[str(path)] = descriptor
    return True


# Descriptors that the calling thread's run_child is passing to its child. A
# ``preexec_fn`` makes ``subprocess`` run the at-fork handlers in the child, which
# must not close the descriptor that child is about to inherit.
_spawning = threading.local()


def _after_fork():
    global _registry, _registry_guard, _borrowed
    # Closing the child's duplicate descriptor preserves the parent's flock.
    # Explicit LOCK_UN here would instead unlock the shared open description.
    keep = getattr(_spawning, 'descriptors', ())
    for entry in _registry.values():
        if entry['fd'] is not None and entry['fd'] not in keep:
            os.close(entry['fd'])
    _registry = {}
    _registry_guard = threading.Lock()
    _borrowed = {}


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)


def _open(path):
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Portfolio lock must be a regular file')
        os.fchmod(descriptor, 0o600)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def portfolio_write_lock(portfolio_dir, *, accept_inherited=False):
    """Hold the portfolio lock; yields True when working under an ancestor's lock instead.

    ``accept_inherited`` is for a process started by ``run_child``: it then
    verifies an inherited claim as described in the module notes.
    """
    fcntl = _fcntl()
    path = _lock_path(portfolio_dir)
    key = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _registry_guard:
        entry = _registry.setdefault(key, {'lock': threading.RLock(), 'depth': 0, 'fd': None, 'owner': None})
    owner_pid = os.getpid()
    with entry['lock']:
        verified = False
        if entry['depth'] == 0 and key not in _borrowed:
            descriptor = _open(path)
            try:
                if accept_inherited and os.environ.get(INHERITED_ENV) == key:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        verified = True
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
            except BaseException:
                os.close(descriptor)
                raise
            if verified:
                os.close(descriptor)
                _borrowed[key] = None
            else:
                entry['fd'] = descriptor
        if entry['depth'] == 0:
            entry['owner'] = threading.get_ident()
        entry['depth'] += 1
        try:
            yield key in _borrowed
        finally:
            if os.getpid() == owner_pid:
                entry['depth'] -= 1
                if entry['depth'] == 0:
                    entry['owner'] = None
                    if verified:
                        _borrowed.pop(key, None)
                    descriptor, entry['fd'] = entry['fd'], None
                    if descriptor is not None:
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                        finally:
                            os.close(descriptor)


def _group_running(group):
    """Whether any process other than a zombie is left in process group ``group``."""
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    proc = Path('/proc')
    if not proc.is_dir():
        return True  # zombies cannot be told apart here; the caller's deadline applies
    for status in proc.glob('[0-9]*/stat'):
        try:
            state, _parent, process_group = status.read_text().rsplit(')', 1)[1].split()[:3]
        except (OSError, ValueError):
            continue
        if process_group == str(group) and state not in ('Z', 'X'):
            return True
    return False


def _stop_session(process):
    """Kill the child's whole session and wait until none of it runs any more."""
    import signal
    import time
    if not hasattr(os, 'killpg'):
        process.kill()
        process.wait()
        return
    try:
        # start_new_session made the child the leader of its own process group,
        # which also holds what it started: a conda wrapper's Python, for example.
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    process.wait()
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    deadline = time.monotonic() + 10
    while _group_running(process.pid) and time.monotonic() < deadline:
        time.sleep(.02)


# Set by whoever runs a whole pipeline (the backend's close snapshot runner):
# a private directory where each child started by ``run_child`` leaves one file,
# named after its process group, before it executes its command. A runner that
# has to kill the pipeline kills these groups too, because they live in sessions
# of their own.
CHILD_GROUPS_ENV = 'PORTFOLIO_CHILD_GROUPS_DIR'
PR_SET_PDEATHSIG = 1
_prctl = None


def _linux_prctl():
    """libc's ``prctl`` through ctypes, or None (not Linux, or not found)."""
    global _prctl
    if _prctl is None:
        _prctl = False
        if sys.platform.startswith('linux'):
            try:
                import ctypes
                function = ctypes.CDLL(None, use_errno=True).prctl
                function.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
                function.restype = ctypes.c_int
                _prctl = function
            except (ImportError, OSError, AttributeError):
                _prctl = False
    return _prctl or None


def _die_with_parent():
    """A ``preexec_fn`` that has the child killed when the thread that started it exits; None off Linux.

    Built before the fork, so the child only calls into libc between fork and exec.
    """
    import signal
    prctl = _linux_prctl()
    if prctl is None:
        return None
    parent = os.getpid()

    def preexec():
        prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        if os.getppid() != parent:   # the parent died before the call: no signal will come
            os.kill(os.getpid(), signal.SIGKILL)
    return preexec


# Set by the runner: a value unique to one run, inherited by everything the pipeline starts.
RUN_TOKEN_ENV = 'PORTFOLIO_RUN_TOKEN'


def detached_environment(environment=None):
    """``environment`` (``os.environ`` by default) for a helper that must outlive the run.

    Without the run token, the groups directory and any lock claim, so the
    runner neither finds nor stops it; start it in a session of its own
    (``start_new_session=True``) and not with ``run_child``.
    """
    base = dict(os.environ if environment is None else environment)
    for name in (RUN_TOKEN_ENV, CHILD_GROUPS_ENV, INHERITED_ENV):
        base.pop(name, None)
    return base


_PROC_PIDTBSDINFO = 3


def _darwin_reader():
    """A function ``pid -> (start text, parent, group, zombie)`` through libproc, or None (not macOS).

    Built before a fork: the child only calls into libc with a buffer that already exists.
    """
    if sys.platform != 'darwin':
        return None
    try:
        import ctypes

        class BsdInfo(ctypes.Structure):
            _fields_ = [('flags', ctypes.c_uint32), ('status', ctypes.c_uint32), ('xstatus', ctypes.c_uint32),
                        ('pid', ctypes.c_uint32), ('ppid', ctypes.c_uint32), ('uid', ctypes.c_uint32),
                        ('gid', ctypes.c_uint32), ('ruid', ctypes.c_uint32), ('rgid', ctypes.c_uint32),
                        ('svuid', ctypes.c_uint32), ('svgid', ctypes.c_uint32), ('rfu_1', ctypes.c_uint32),
                        ('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32), ('nfiles', ctypes.c_uint32),
                        ('pgid', ctypes.c_uint32), ('pjobc', ctypes.c_uint32), ('tdev', ctypes.c_uint32),
                        ('tpgid', ctypes.c_uint32), ('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64),
                        ('start_usec', ctypes.c_uint64)]

        function = ctypes.CDLL(None, use_errno=True).proc_pidinfo
        function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        function.restype = ctypes.c_int
    except (ImportError, OSError, AttributeError):
        return None
    info = BsdInfo()
    size = ctypes.sizeof(info)
    pointer = ctypes.byref(info)

    def read(pid):
        if function(pid, _PROC_PIDTBSDINFO, 0, pointer, size) != size:
            return None   # gone, a zombie (libproc answers nothing for one), or another user's
        return f'darwin:{info.start_sec}.{info.start_usec:06d}', info.ppid, info.pgid, info.status == 5
    return read


def _linux_read(pid):
    """``(start text, parent, group, zombie)`` from ``/proc/<pid>/stat``, or None."""
    try:
        descriptor = os.open(f'/proc/{pid}/stat', os.O_RDONLY)
    except OSError:
        return None
    try:
        text = os.read(descriptor, 4096).decode('ascii', 'replace')
    except OSError:
        return None
    finally:
        os.close(descriptor)
    fields = text.rsplit(')', 1)[-1].split()
    if len(fields) < 20 or not fields[19].isdigit():
        return None
    try:
        return f'linux:{fields[19]}', int(fields[1]), int(fields[2]), fields[0] in ('Z', 'X')
    except ValueError:
        return None


def process_start(pid, _reader=None):
    """What tells process ``pid`` from a later process given the same number: its start time, as text.

    ``linux:<clock ticks after boot>`` from ``/proc``, ``darwin:<seconds>.<microseconds>``
    from libproc; None when the process is gone or this platform cannot tell.
    """
    if os.path.isdir('/proc'):
        info = _linux_read(pid)
    else:
        reader = _reader or _darwin_reader()
        info = reader(pid) if reader is not None else None
    return info[0] if info else None


def _record_own_group(folder, reader=None):
    """In the child, between fork and exec: leave its process group where the runner finds it.

    ``start_new_session`` has already made the child a session leader, so its
    group is its pid. The record is ``"<group> <start>"`` (``process_start``),
    so the runner can tell this process from a later one given the same number.
    Raises ``OSError`` when the record cannot be written: the child must then
    not run at all.
    """
    group = os.getpgrp()
    start = process_start(os.getpid(), reader) or ''
    descriptor = os.open(os.path.join(folder, str(group)), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                         0o600)
    try:
        os.write(descriptor, f'{group} {start}'.strip().encode())
    finally:
        os.close(descriptor)


def _child_setup(folder):
    """The ``preexec_fn`` of ``run_child``, or None when there is nothing to do.

    In the child, before the command runs: the parent-death signal on Linux
    (``_die_with_parent``), then, when ``folder`` is set, the record of the
    child's process group. So the group is known before the child can start
    anything, whatever happens to the parent meanwhile (killed, or suspended
    right after the fork).
    """
    die = _die_with_parent()
    if die is None and not folder:
        return None
    reader = _darwin_reader() if folder else None

    def preexec():
        if die is not None:
            die()
        if folder:
            _record_own_group(folder, reader)
    return preexec


def _forget_dead_records(folder):
    """Remove records of groups that no longer exist (a child whose exec failed after recording itself)."""
    if not folder:
        return
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        try:
            os.killpg(int(name), 0)
        except ProcessLookupError:
            try:
                os.unlink(os.path.join(folder, name))
            except OSError:
                pass
        except (ValueError, OSError):
            pass


def run_child(command, *, timeout, portfolio_dir=None, env=None, **options):
    """Run ``command`` to completion and capture its text output.

    The child is told that it inherits ``portfolio_dir``'s lock only when the
    calling thread holds that lock; any claim this process inherited itself is
    not passed on otherwise. It then also receives the locked descriptor, so the
    lock stays taken while the child runs even if this process dies first.

    The child leads its own session. On a timeout or an interruption the whole
    session is killed and waited for before the exception propagates: when the
    caller releases the lock, nothing it started can still write.

    So that a forced end of this process takes the child down too: on Linux the
    child gets SIGKILL when the calling thread exits (``PR_SET_PDEATHSIG``), and
    on every platform, when ``PORTFOLIO_CHILD_GROUPS_DIR`` is set, the child
    records its process group there itself, before it executes the command
    (a child that cannot record itself does not run: ``OSError``); the record
    is removed once the child has finished. The runner kills what is recorded.
    """
    environment = dict(os.environ if env is None else env)
    environment.pop(INHERITED_ENV, None)
    descriptors = ()
    if portfolio_dir is not None and holds_lock(portfolio_dir):
        key = str(_lock_path(portfolio_dir))
        environment[INHERITED_ENV] = key
        descriptor = _borrowed[key] if key in _borrowed else _registry[key]['fd']
        descriptors = () if descriptor is None else (descriptor,)
    folder = environment.get(CHILD_GROUPS_ENV) or None
    if 'preexec_fn' not in options:
        options['preexec_fn'] = _child_setup(folder)
    _spawning.descriptors = descriptors
    try:
        process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True, pass_fds=descriptors, **options)
    except subprocess.SubprocessError as error:
        _forget_dead_records(folder)
        raise OSError(f'{command[0]} was not started: {type(error).__name__}') from None
    except BaseException:
        _forget_dead_records(folder)
        raise
    finally:
        _spawning.descriptors = ()
    # start_new_session: the child's pid is its process group (and session) id; the child recorded it.
    record = os.path.join(folder, str(process.pid)) if folder else None
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            _stop_session(process)
            raise
    finally:
        if record is not None:
            try:
                os.unlink(record)
            except OSError:
                pass
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def portfolio_write_locked(directory):
    """Wrap a synchronous entrypoint; resolve its directory at call time.

    Without a usable lock the entrypoint exits with a one-line message.
    """
    def decorate(function):
        @wraps(function)
        def locked(*args, **kwargs):
            try:
                with portfolio_write_lock(directory() if callable(directory) else directory):
                    return function(*args, **kwargs)
            except LockUnavailable as error:
                raise SystemExit(str(error)) from None
        return locked
    return decorate
