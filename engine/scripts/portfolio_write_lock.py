"""Serialize complete portfolio read/modify/write operations across entrypoints.

The lock is reentrant in one thread and shared with the Feishu portfolio adapter.
Do not hold it while waiting for a child CLI that acquires the same lock. Call
non-entrypoint pipeline functions directly when the parent already owns it.
"""
from contextlib import contextmanager
import fcntl
from functools import wraps
import os
from pathlib import Path
import stat
import threading


_registry = {}
_registry_guard = threading.Lock()


def _after_fork():
    global _registry, _registry_guard
    # Closing the child's duplicate descriptor preserves the parent's flock.
    # Explicit LOCK_UN here would instead unlock the shared open description.
    for entry in _registry.values():
        if entry['fd'] is not None:
            os.close(entry['fd'])
    _registry = {}
    _registry_guard = threading.Lock()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)


@contextmanager
def portfolio_write_lock(portfolio_dir):
    directory = Path(portfolio_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / '.portfolio-update.lock'
    with _registry_guard:
        entry = _registry.setdefault(str(path), {'lock': threading.RLock(), 'depth': 0, 'fd': None})
    owner_pid = os.getpid()
    with entry['lock']:
        if entry['depth'] == 0:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Portfolio lock must be a regular file')
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except BaseException:
                os.close(descriptor)
                raise
            entry['fd'] = descriptor
        entry['depth'] += 1
        try:
            yield
        finally:
            if os.getpid() == owner_pid:
                entry['depth'] -= 1
                if entry['depth'] == 0:
                    descriptor, entry['fd'] = entry['fd'], None
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)


def portfolio_write_locked(directory):
    """Wrap a synchronous entrypoint; resolve its directory at call time."""
    def decorate(function):
        @wraps(function)
        def locked(*args, **kwargs):
            with portfolio_write_lock(directory() if callable(directory) else directory):
                return function(*args, **kwargs)
        return locked
    return decorate
