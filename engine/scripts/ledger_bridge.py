"""Read confirmed ledger state from standalone accounting scripts."""
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = root.parent / 'dashboard' if root.name == 'engine' else root
sys.path.insert(0, str(app))
from core.ledger import active_ledger


def ledger_holdings(directory, day):
    ledger = active_ledger(directory)
    if ledger:
        state = ledger.holdings(day)
        if ledger.state(day)['date'] is not None:
            return state
    return None


def guard_legacy_write(directory):
    if active_ledger(directory):
        raise ValueError('Ledger is active. Use /ledger to preview and confirm transactions instead of overwriting legacy holdings.')
