"""A demo made for an earlier day stamps its ledger proposals on the days it booked them, not today.

The seed books every ledger entry through the ledger's own preview and confirm, inside
``desk_store.pinned_now`` (the demo's history clock). The ledger's ``proposals`` and
``proposal_bindings`` rows record when they were made (``created_at``); in a demo they carry the
seed's moment, so nothing in the demo is dated after the day it is made for. Outside a pinned
block the ledger stamps the real clock as before. Fictional holdings only.
"""
import sqlite3
from datetime import date, datetime, timezone

import pytest

from core import desk_store
from core.market.calendar import SHANGHAI

STAMPED = ('proposals', 'proposal_bindings')


def stamps(path):
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        return {table: [row[0] for row in db.execute(f'SELECT created_at FROM {table}')] for table in STAMPED}


def day_of(stamp):
    return datetime.fromisoformat(stamp).astimezone(SHANGHAI).date()


@pytest.mark.parametrize('today', [date(2026, 1, 5), date(2026, 10, 8)])
def test_a_demo_for_an_earlier_day_stamps_its_ledger_proposals_on_their_own_days(tmp_path, today):
    from create_demo import create_demo
    directory = tmp_path / 'portfolio'
    create_demo(directory, ledger=True, today=today)
    found = stamps(directory / 'ledger.sqlite3')
    assert found['proposals'], 'the seed booked entries through proposals'
    late = [f'{table}.created_at = {stamp}' for table, values in found.items() for stamp in values
            if day_of(stamp) > today]
    assert late == []
    assert len({day_of(stamp) for stamp in found['proposals']}) > 1, 'each entry on its own day'


def test_the_ledger_follows_the_pinned_clock_only_inside_the_block(tmp_path):
    from core.ledger import Ledger
    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    opening = {'kind': 'opening', 'date': '2026-01-02', 'account': '示例账户', 'source': 'test',
               'external_id': 'stamp-test:opening', 'cash_balances': {'CNY': '1000'}, 'positions': [],
               'contributed_cny': '1000', 'fund_cny': '0'}
    moment = datetime(2026, 1, 2, 9, 30, tzinfo=SHANGHAI)
    with desk_store.pinned_now(moment):
        pinned = ledger.propose([opening])['id']
    before = datetime.now(timezone.utc)
    later = ledger.propose([{**opening, 'external_id': 'stamp-test:again'}])['id']
    with sqlite3.connect(tmp_path / 'ledger.sqlite3') as db:
        created = dict(db.execute('SELECT id, created_at FROM proposals'))
    assert datetime.fromisoformat(created[pinned]) == moment
    assert datetime.fromisoformat(created[later]) >= before.replace(microsecond=0)
