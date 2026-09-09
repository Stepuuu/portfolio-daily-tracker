"""Local ledger: every financial write requires an explicit preview and confirmation."""
import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.ledger import (Ledger, LedgerError, Conflict, portfolio_directory, opening_events,
                         csv_events, CSV_COLUMNS, instrument, valid_date, encode)

router = APIRouter()


def get_ledger():
    return Ledger(portfolio_directory() / 'ledger.sqlite3')


def attempt(operation):
    try:
        return operation()
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except (LedgerError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc)) from None


class ProposalRequest(BaseModel):
    events: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class OpeningRequest(BaseModel):
    date: str


class CSVRequest(BaseModel):
    content: str = Field(max_length=2_000_000)
    source: str = Field(min_length=1, max_length=240)


@router.get('')
def state():
    ledger = get_ledger()
    return {**ledger.state(), 'pending': ledger.proposals()}


@router.get('/events')
def events():
    return {'events': get_ledger().history()}


@router.post('/proposals')
def propose(request: ProposalRequest):
    return attempt(lambda: get_ledger().propose(request.events))


@router.post('/opening-preview')
def opening(request: OpeningRequest):
    return attempt(lambda: get_ledger().propose(opening_events(portfolio_directory(), request.date)))


@router.post('/csv-preview')
def import_csv(request: CSVRequest):
    return attempt(lambda: get_ledger().propose(csv_events(request.content, request.source)))


@router.post('/proposals/{proposal_id}/confirm')
def confirm(proposal_id: str):
    return attempt(lambda: get_ledger().confirm(proposal_id))


@router.delete('/proposals/{proposal_id}')
def discard(proposal_id: str):
    with get_ledger().connect() as db:
        db.execute('DELETE FROM proposals WHERE id=? AND receipt IS NULL', (proposal_id,))
    return {'discarded': True}


@router.get('/template.csv')
def template():
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    return Response(output.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="transactions-template.csv"'})


@router.get('/export.json')
def export():
    ledger = get_ledger()
    return Response(encode({'format': 'portfolio-ledger-v1', 'events': ledger.history()}), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="transactions.json"'})


@router.get('/backup.sqlite3')
def backup():
    return Response(get_ledger().backup(), media_type='application/vnd.sqlite3',
                    headers={'Content-Disposition': 'attachment; filename="ledger-backup.sqlite3"'})


class ResearchEntry(BaseModel):
    ticker: str
    thesis: str = Field(min_length=1, max_length=4000)
    invalidation: str = Field(min_length=1, max_length=4000)
    review_on: str


def journal_db():
    ledger = get_ledger()
    with ledger.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS journal (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)')
    return ledger


@router.get('/journal')
def journal():
    with journal_db().connect() as db:
        return {'entries': [{'id': r['id'], 'created_at': r['created_at'], **json.loads(r['payload'])}
                            for r in db.execute('SELECT * FROM journal ORDER BY created_at DESC')]}


@router.post('/journal')
def add_journal(request: ResearchEntry):
    ticker = attempt(lambda: instrument(request.ticker))
    review_on = attempt(lambda: valid_date(request.review_on))
    payload = {'ticker': ticker, 'thesis': request.thesis.strip(),
               'invalidation': request.invalidation.strip(), 'review_on': review_on}
    if not payload['thesis'] or not payload['invalidation']:
        raise HTTPException(422, 'Write both the thesis and the conditions that would invalidate it')
    entry_id = uuid.uuid4().hex
    with journal_db().connect() as db:
        db.execute('INSERT INTO journal VALUES (?,?,?)', (entry_id, datetime.now(timezone.utc).isoformat(), encode(payload)))
    return {'id': entry_id, **payload}
