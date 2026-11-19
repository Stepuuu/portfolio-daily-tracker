"""Shared helpers for the trade plan tests. Fictional data only.

The plan module stamps status changes with ``plans._now``; ``pin`` sets it,
so a test decides on which (Shanghai) day a plan was activated or closed and
the trade dates compare against that, not against the real clock.
"""
from core import plans

CN = 'SHA:600001'
OTHER = 'SHA:600002'
HK = 'HKG:00001'

# 10:00 in Shanghai on Monday 2026-11-16 (tests.fakes.DAY), in UTC as stored.
MONDAY = '2026-11-16T02:00:00+00:00'


def at(day: str, hour: int = 10) -> str:
    """A stored UTC stamp for ``hour`` o'clock Shanghai time on ``day``."""
    return f'{day}T{hour - 8:02d}:00:00+00:00'


def pin(monkeypatch, stamp: str = MONDAY) -> None:
    monkeypatch.setattr(plans, '_now', lambda: stamp)


def payload(ticker: str = CN, **fields) -> dict:
    """A valid long plan; ``fields`` override or add (``None`` removes a default)."""
    plan = {'ticker': ticker, 'thesis': '示例理由：业绩改善', 'invalidation': '示例条件：跌破年线'}
    plan.update(fields)
    return {key: value for key, value in plan.items() if value is not None}


def active(ticker: str = CN, **fields) -> dict:
    """Create and activate a plan; returns it."""
    return plans.activate(plans.create(payload(ticker, **fields))['id'])
