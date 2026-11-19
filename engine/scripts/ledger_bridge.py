"""Read confirmed ledger state from standalone accounting scripts.

Every function here only reads ``ledger.sqlite3`` (``core.ledger.read_confirmed``):
it never creates the file, initialises its tables or leaves ``-wal`` / ``-shm``
files, so a snapshot's ``--dry-run`` and every check before a write leave the
ledger as it is. A ledger that exists but cannot be read raises
(``core.ledger.LedgerUnavailable``): it is never taken for "no ledger", so its
holdings are never replaced by the legacy files.
"""
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = root.parent / 'dashboard' if root.name == 'engine' else root
sys.path.insert(0, str(app))
from core.ledger import LedgerUnavailable, holdings_of, read_confirmed

# Seconds a read waits for a commit in progress (as long as ``Ledger`` waits for its lock).
LEDGER_WAIT = 15.0


def confirmed_ledger(directory):
    """The confirmed ledger of ``directory`` read read-only, or ``None`` without one."""
    return read_confirmed(directory, timeout=LEDGER_WAIT)


def active_ledger(directory):
    """Whether ``directory`` has a confirmed ledger (truthy) or not (``None``); never writes."""
    return confirmed_ledger(directory)


def ledger_holdings(directory, day, *, exact=False):
    """The confirmed ledger's holdings on ``day`` in the legacy shape (floats), or ``None``.

    With ``exact``, they also carry ``exact``: the ledger's own decimal strings of what the floats round, by
    account (``cash_balances``, ``fund``, ``cost_basis``, and ``positions`` by ticker with ``quantity`` and
    ``cost_price``), the figures Today values (every digit the ledger holds).
    """
    ledger = confirmed_ledger(directory)
    if ledger:
        state = ledger.state(day)
        if state['date'] is not None:
            holdings = holdings_of(state, day)
            if exact:
                holdings['exact'] = {name: {
                    'cash_balances': dict(account['cash_balances']), 'fund': account['fund_cny'],
                    'cost_basis': account['contributed_cny'],
                    'positions': {ticker: {'quantity': p['quantity'], 'cost_price': p['cost_price']}
                                  for ticker, p in account['positions'].items()}}
                    for name, account in state['accounts'].items()}
            return holdings
    return None


def guard_legacy_write(directory):
    try:
        active = confirmed_ledger(directory)
    except LedgerUnavailable as exc:   # still a ledger: the legacy files beside it are never written
        raise ValueError(f'账本存在但暂时无法读取（{exc.reason}），不会改写旧持仓文件。') from None
    if active:
        raise ValueError('Ledger is active. Use /ledger to preview and confirm transactions instead of overwriting legacy holdings.')
