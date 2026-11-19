"""Local ledger: every financial write requires an explicit preview and confirmation.

Trade plans only comment on the ledger, they never decide for it. Once a plan
has been saved, a pending proposal (``POST /proposals``, ``/opening-preview``,
``/csv-preview`` and ``GET /proposals/{id}``) carries ``plan_check``: one
item per buy or sell, ``{event_ref, index, kind, date, ticker, account,
quantity, price, plan_id, relation, deviations: [{code, level, message,
data}]}`` (``core.plans.check_events``), counted at the ledger revision the
proposal was previewed against, the one its ``before`` and ``after`` show
(also when it is reloaded after the ledger moved on). Before any plan exists
the key is absent. Right before the ledger confirms, the check its links
are to record is stored (``core.plans.prepare_links``); after it, the links
are stored once per event (``core.plans.record_links``), so a repeated
confirmation that repairs an interrupted link write records the plans as
they were when the trade was confirmed. Neither requests quotes, so the
receipt never waits for the quote service; a planned weight is valued at
the quotes the preview read. A failure in any of them is logged and never
changes the ledger's answer: a proposal then carries ``plan_check_error``
instead, and a confirmation returns its receipt as always.

Reads (every ``GET``) open an existing ``ledger.sqlite3`` read-only and never
create it or add a table; without one they answer as an empty ledger (or 404
for a proposal, a backup). ``GET ''`` reads the state, its revision and the
pending proposals in one transaction, so a pending proposal is outdated
exactly when its ``revision`` is below the returned one.

Confirmation is idempotent: confirming a confirmed id returns its receipt
again. ``DELETE /proposals/{id}`` discards a pending proposal (an unknown or
already discarded id is not an error) and answers 409 for a confirmed one.

Manual previews (``POST /proposals`` with ``transient: true``: the 记一笔
dialog, a reversal from 流水) expire ``MANUAL_PREVIEW_LIFETIME`` (2 hours)
after they were made: they carry ``expires_at``, an expired one is left out of
``pending``, reads mark it ``expired: true``, confirming it answers 409, and
the next preview deletes it. At most ``TRANSIENT_LIMIT`` of them are listed,
on top of the ``PENDING_LIMIT`` genuine proposals (assistant, import, CSV,
opening), so abandoned previews never push a genuine one out of the list.

For the form: ``GET /calendar`` names today (Asia/Shanghai) and each
market's latest trading session the calendar has verified (``session`` is
null when the calendar does not cover the day); ``GET /as-of?date=`` is the
confirmed ledger at the end of that day as an entry dated that day is checked
against it (``core.ledger.replay_for_entry``: a later reversal already counts).

``POST /opening-preview`` answers 422 with ``missing_contributions`` (the
accounts) when the holdings file has no ``cost_basis`` for some accounts; the
page asks for them and sends ``contributions`` ({account: CNY}) with the date.
"""
import csv
import io
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from core.ledger import (Ledger, LedgerBusy, LedgerError, Conflict, MISSING_CONTRIBUTION, MissingContribution,
                         portfolio_directory, opening_events, csv_events, CSV_COLUMNS, instrument, valid_date, encode,
                         read_only_connection, replay, replay_for_entry)

router = APIRouter()
logger = logging.getLogger(__name__)
PLAN_CHECK_FAILED = '交易计划检查暂时不可用，本次没有对照计划。入账不受影响。'
MANUAL_PREVIEW_LIFETIME = timedelta(hours=2)
PENDING_LIMIT = 30
TRANSIENT_LIMIT = 10
EXPIRED_PREVIEW = '这份预览超过 2 小时没有确认，已失效，没有入账。请重新预览。'
ALREADY_BOOKED = '这份方案已经入账，不能放弃；如需更正，请在流水中冲销。'
NOT_FOUND = '方案不存在或已被放弃。请打开待确认方案列表。'
NO_LEDGER_BACKUP = '还没有账本，无需备份。'


def utc_now():
    return datetime.now(timezone.utc)


def ledger_path():
    return portfolio_directory() / 'ledger.sqlite3'


def get_ledger():
    """The ledger for a write (creates the file and its tables when missing)."""
    return Ledger(ledger_path())


@contextmanager
def reading():
    """One read-only transaction on the existing ledger file, or ``None`` without one.

    Never creates the file or a table, never writes; tables a file lacks read as empty.
    """
    path = ledger_path()
    if not path.is_file():
        yield None
        return
    try:
        # Read-only, and no -wal / -shm file is left beside a WAL ledger (core.ledger.read_only_connection).
        with read_only_connection(path, timeout=15) as db:
            yield db
    except LedgerBusy:   # a writer checkpointed during the read: what was read may mix two states
        raise HTTPException(503, LedgerBusy.reason) from None


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _revision(db, tables):
    if 'ledger_meta' not in tables:
        return 0
    row = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()
    return row[0] if row else 0


def _events(db, tables):
    if 'events' not in tables:
        return []
    return [{**json.loads(row['payload']), '_sequence': row['sequence']} for row in db.execute('SELECT * FROM events ORDER BY sequence')]


def _expiry(db, tables):
    """``{proposal_id: expires_at}`` of the manual previews."""
    if 'proposal_expiry' not in tables:
        return {}
    return {row[0]: row[1] for row in db.execute('SELECT proposal_id, expires_at FROM proposal_expiry')}


def _expired(expires_at, now=None):
    return expires_at is not None and datetime.fromisoformat(expires_at) <= (now or utc_now())


def _proposal_row(row, expires_at, receipt=False):
    out = {'id': row['id'], 'revision': row['revision'], 'created_at': row['created_at'], **json.loads(row['payload'])}
    if expires_at is not None:
        out['expires_at'] = expires_at
        out['expired'] = row['receipt'] is None and _expired(expires_at)
    if receipt:
        out['receipt'] = json.loads(row['receipt']) if row['receipt'] else None
    return out


def _pending(db, tables):
    """Pending proposals, newest first: up to ``PENDING_LIMIT`` genuine ones and ``TRANSIENT_LIMIT`` unexpired manual previews."""
    if 'proposals' not in tables:
        return []
    expiry = _expiry(db, tables)
    now = utc_now()
    genuine, transient = [], []
    for row in db.execute('SELECT * FROM proposals WHERE receipt IS NULL ORDER BY created_at DESC'):
        expires_at = expiry.get(row['id'])
        if expires_at is None:
            if len(genuine) < PENDING_LIMIT:
                genuine.append(_proposal_row(row, None))
        elif not _expired(expires_at, now) and len(transient) < TRANSIENT_LIMIT:
            transient.append(_proposal_row(row, expires_at))
        if len(genuine) >= PENDING_LIMIT and len(transient) >= TRANSIENT_LIMIT:
            break
    return sorted(genuine + transient, key=lambda proposal: proposal['created_at'], reverse=True)


class ReadOnlyLedger:
    """The ledger file for the plan check of a read: ``path``, ``connect()`` and ``history()``, read-only."""

    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        # No -wal / -shm file is left beside a WAL ledger; a checkpoint during the read raises LedgerBusy,
        # which the plan check reports as unavailable (with_plan_check).
        with read_only_connection(self.path, timeout=15) as db:
            yield db

    def history(self):
        with self.connect() as db:
            return _events(db, _tables(db))


def with_plan_check(proposal, ledger):
    """The proposal with ``plan_check`` while plans are in use; never raises.

    ``ledger`` is the ledger the proposal belongs to: its history places the
    proposal's trades among the confirmed ones.
    """
    try:
        from core import plans
        check = plans.check_proposal(proposal, ledger=ledger)
    except Exception as exc:
        logger.warning('plan check of ledger proposal %s failed: %s', proposal.get('id'), type(exc).__name__)
        return {**proposal, 'plan_check_error': PLAN_CHECK_FAILED}
    return proposal if check is None else {**proposal, 'plan_check': check}


def prepare_plan_links(ledger, proposal_id):
    """Right before the ledger confirms: store the plan check the links will record; never raises."""
    try:
        from core import plans
        proposal = ledger.proposal(proposal_id)
        if proposal is not None:
            plans.prepare_links(proposal, ledger=ledger)
    except Exception as exc:
        logger.warning('plan links of ledger proposal %s were not prepared: %s', proposal_id, type(exc).__name__)


def link_plans(ledger, proposal_id):
    """Store the plan links of a confirmed proposal; never raises."""
    try:
        from core import plans
        plans.record_links(ledger.proposal(proposal_id), ledger=ledger)
    except Exception as exc:
        logger.warning('plan links of ledger proposal %s were not written: %s', proposal_id, type(exc).__name__)


def attempt(operation):
    try:
        return operation()
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except LedgerError as exc:
        raise HTTPException(422, str(exc)) from None


def purge_expired(ledger):
    """Delete the manual previews whose lifetime ended (a write; failures only leave them for later)."""
    try:
        with ledger.writing() as db:
            if 'proposal_expiry' not in _tables(db):
                return
            now = utc_now()
            for proposal_id, expires_at in db.execute('SELECT proposal_id, expires_at FROM proposal_expiry').fetchall():
                if _expired(expires_at, now):
                    db.execute('DELETE FROM proposals WHERE id=? AND receipt IS NULL', (proposal_id,))
            db.execute('DELETE FROM proposal_expiry WHERE proposal_id NOT IN (SELECT id FROM proposals WHERE receipt IS NULL)')
    except sqlite3.Error as exc:
        logger.warning('expired ledger previews were not removed: %s', type(exc).__name__)


def mark_transient(ledger, proposal_id):
    """Give a manual preview its lifetime; returns ``expires_at``."""
    expires_at = (utc_now() + MANUAL_PREVIEW_LIFETIME).isoformat()
    with ledger.writing() as db:
        db.execute('CREATE TABLE IF NOT EXISTS proposal_expiry (proposal_id TEXT PRIMARY KEY, expires_at TEXT NOT NULL)')
        db.execute('INSERT OR REPLACE INTO proposal_expiry VALUES (?,?)', (proposal_id, expires_at))
    return expires_at


class ProposalRequest(BaseModel):
    events: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
    # A preview made while a person types (记一笔, 冲销): it expires when abandoned.
    transient: bool = False


class OpeningRequest(BaseModel):
    date: str
    # 累计净投入 (CNY) of the accounts whose cost_basis the holdings file lacks (``missing_contributions``).
    contributions: dict[str, str] = Field(default_factory=dict, max_length=1000)


class CSVRequest(BaseModel):
    content: str = Field(max_length=2_000_000)
    source: str = Field(min_length=1, max_length=240)


@router.get('')
def state():
    with reading() as db:
        if db is None:
            return {'revision': 0, 'accounts': {}, 'date': None, 'pending': []}
        tables = _tables(db)
        revision, events = _revision(db, tables), _events(db, tables)
        return {'revision': revision, **replay(events), 'pending': _pending(db, tables)}


@router.get('/events')
def events():
    with reading() as db:
        return {'events': [] if db is None else _events(db, _tables(db))}


@router.get('/as-of')
def as_of(date: str = ''):
    """The confirmed ledger at the end of ``date``, as a back-dated entry is checked against it."""
    day = attempt(lambda: valid_date(date))
    with reading() as db:
        if db is None:
            return {'revision': 0, 'as_of': day, 'accounts': {}, 'date': None}
        tables = _tables(db)
        revision, events = _revision(db, tables), _events(db, tables)
    return {'revision': revision, 'as_of': day, **attempt(lambda: replay_for_entry(events, day))}


@router.get('/calendar')
def calendar():
    """Today in Asia/Shanghai and, per market, the latest trading session the calendar has verified."""
    markets = {market: {'session': None, 'verified': False} for market in ('CN', 'HK', 'US')}
    try:
        from core.market.runtime import get_market_runtime
        clock = get_market_runtime().clock
        today = clock.local_date('CN').isoformat()
    except Exception as exc:
        logger.warning('the trading calendar is unavailable: %s', type(exc).__name__)
        return {'today': None, 'markets': markets}
    for market in markets:
        try:
            day = clock.session_of(market)
            if clock.confidence(market) == 'calendar' and clock.confidence(market, day) == 'calendar':
                markets[market] = {'session': day.isoformat(), 'verified': True}
        except Exception as exc:
            logger.warning('the %s trading calendar is unavailable: %s', market, type(exc).__name__)
    return {'today': today, 'markets': markets}


@router.post('/proposals')
def propose(request: ProposalRequest):
    ledger = get_ledger()
    purge_expired(ledger)
    proposal = attempt(lambda: ledger.propose(request.events))
    if request.transient:
        proposal = {**proposal, 'expires_at': mark_transient(ledger, proposal['id']), 'expired': False}
    return with_plan_check(proposal, ledger)


@router.get('/proposals/{proposal_id}')
def proposal(proposal_id: str):
    with reading() as db:
        tables = _tables(db) if db is not None else set()
        row = db.execute('SELECT * FROM proposals WHERE id=?', (proposal_id,)).fetchone() if 'proposals' in tables else None
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        result = _proposal_row(row, _expiry(db, tables).get(proposal_id), receipt=True)
    # The page reloads a pending preview from here. A confirmed one is returned
    # as stored; what its trades were linked to is on the plans.
    return with_plan_check(result, ReadOnlyLedger(ledger_path())) if result['receipt'] is None else result


@router.post('/opening-preview')
def opening(request: OpeningRequest):
    ledger = get_ledger()
    try:
        rows = opening_events(portfolio_directory(), request.date, request.contributions)
    except MissingContribution as exc:
        return JSONResponse(status_code=422, content={'detail': MISSING_CONTRIBUTION, 'missing_contributions': exc.accounts})
    except LedgerError as exc:
        raise HTTPException(422, str(exc)) from None
    return with_plan_check(attempt(lambda: ledger.propose(rows)), ledger)


@router.post('/csv-preview')
def import_csv(request: CSVRequest):
    ledger = get_ledger()
    return with_plan_check(attempt(lambda: ledger.propose(csv_events(request.content, request.source))), ledger)


@router.post('/proposals/{proposal_id}/confirm')
def confirm(proposal_id: str):
    with reading() as db:
        tables = _tables(db) if db is not None else set()
        expires_at = _expiry(db, tables).get(proposal_id) if db is not None else None
        row = (db.execute('SELECT receipt FROM proposals WHERE id=?', (proposal_id,)).fetchone()
               if expires_at is not None and 'proposals' in tables else None)
    if row is not None and row['receipt'] is None and _expired(expires_at):
        raise HTTPException(409, EXPIRED_PREVIEW)
    ledger = get_ledger()
    prepare_plan_links(ledger, proposal_id)
    receipt = attempt(lambda: ledger.confirm(proposal_id))
    link_plans(ledger, proposal_id)
    return receipt


@router.delete('/proposals/{proposal_id}')
def discard(proposal_id: str):
    with get_ledger().writing() as db:
        row = db.execute('SELECT receipt FROM proposals WHERE id=?', (proposal_id,)).fetchone()
        if row is not None and row['receipt']:
            raise HTTPException(409, ALREADY_BOOKED)
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
    with reading() as db:
        history = [] if db is None else _events(db, _tables(db))
    return Response(encode({'format': 'portfolio-ledger-v1', 'events': history}), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="transactions.json"'})


@router.get('/backup.sqlite3')
def backup():
    import tempfile
    with reading() as db:
        if db is None:
            raise HTTPException(404, NO_LEDGER_BACKUP)
        db.rollback()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'ledger.sqlite3'
            destination = sqlite3.connect(target)
            try:
                db.backup(destination)
            finally:
                destination.close()
            content = target.read_bytes()
    return Response(content, media_type='application/vnd.sqlite3',
                    headers={'Content-Disposition': 'attachment; filename="ledger-backup.sqlite3"'})


class ResearchEntry(BaseModel):
    ticker: str
    thesis: str = Field(min_length=1, max_length=4000)
    invalidation: str = Field(min_length=1, max_length=4000)
    review_on: str


@router.get('/journal')
def journal():
    with reading() as db:
        if db is None or 'journal' not in _tables(db):
            return {'entries': []}
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
    with get_ledger().connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS journal (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)')
        db.execute('INSERT INTO journal VALUES (?,?,?)', (entry_id, datetime.now(timezone.utc).isoformat(), encode(payload)))
    return {'id': entry_id, **payload}
