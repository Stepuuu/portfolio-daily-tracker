"""OpenClaw tools: read confirmed records and prepare proposals for human review.

No confirmation, shell execution or notification tool is registered here.
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'dashboard'))
from core.ledger import Ledger, portfolio_directory, valid_date


def get_tracker_snapshot_tool(portfolio_dir: str = '') -> Dict:
    async def read_snapshot(date: str = '') -> Dict:
        directory = Path(portfolio_dir) if portfolio_dir else portfolio_directory()
        if date:
            valid_date(date)
            paths = [directory / 'snapshots' / f'{date}.json']
        else:
            paths = []
            for path in (directory / 'snapshots').glob('*.json'):
                try:
                    valid_date(path.stem)
                    paths.append(path)
                except ValueError:
                    continue
        if not paths or not max(paths).is_file():
            return {'error': 'No snapshot found; generate a dated snapshot first'}
        return json.loads(max(paths).read_text())

    return {'name': 'get_tracker_snapshot', 'description': 'Read a dated valuation snapshot; this is not a live ledger balance.',
            'function': read_snapshot,
            'parameters': {'date': {'type': 'string', 'description': 'YYYY-MM-DD; omit for latest', 'required': False}}}


def get_propose_events_tool(portfolio_dir: str = '') -> Dict:
    async def propose(events: List[Dict]) -> Dict:
        directory = Path(portfolio_dir) if portfolio_dir else portfolio_directory()
        proposal = Ledger(directory / 'ledger.sqlite3').propose(events)
        return {**proposal, 'status': 'awaiting_user_confirmation', 'review_url': '/ledger'}

    return {'name': 'propose_ledger_events',
            'description': 'Prepare a transaction preview. No balances change until the user confirms on /ledger. Use explicit dates, accounts, currencies, quantities, prices and fees; ask for missing values. See docs/LEDGER.md for event fields.',
            'function': propose,
            'parameters': {'events': {'type': 'array', 'items': {'type': 'object'}, 'required': True}}}


def get_all_tools(portfolio_dir: str = '', scripts_dir: str = '') -> List[Dict]:
    return [get_tracker_snapshot_tool(portfolio_dir), get_propose_events_tool(portfolio_dir)]


get_tools = get_all_tools
