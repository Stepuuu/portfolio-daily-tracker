"""Import sessions in ``desk.sqlite3`` and the ledger proposals they create.

What is stored, and for how long
--------------------------------
The uploaded file itself is never stored: it is parsed in memory. A session (one
row of ``import_sessions`` in ``PORTFOLIO_DIR/desk.sqlite3``, a file only the
owner can read) stores:

* the file's name (last 200 characters), size and SHA-256;
* the parsed text cells of the sheet being imported, every row of it, including
  the title lines above the header (they can hold an account number or a name),
  and the cells of the workbook's other data sheets, so that ``sheet`` can switch
  without a new upload;
* the column mapping and the fields the person chose, the account choice, the
  person's row edits, the opening values for a holdings statement and the
  batch records (ledger proposal ids, row numbers, a content fingerprint).

A session is deleted, with all of the above, 30 days after the upload
(``KEEP_DAYS``; only the ``KEEP_SESSIONS`` most recent are kept at all). An
expired session answers 404 at once; its row is deleted by the next import write
of any kind (upload, PATCH, propose, DELETE), or by ``DELETE``. Deleted rows are
overwritten in the database file (SQLite ``secure_delete``); a copy can remain in
the store's write-ahead log until SQLite checkpoints it, and in any backup.

Kept until deleted by hand: the saved column mappings (``import_mappings``: the
ledger account name, a hash of the header layout, the mapping, the chosen
fields and the chosen 资金账号 / 股东代码 values) and ``instrument_aliases``
(a US symbol's exchange). They hold no rows of the file.

Statuses are recomputed from the current ledger on every read, so after a batch
is confirmed its rows show as 已导入.

Batches: a ledger proposal holds at most 1,000 events and the ledger revision
changes on every confirmation, so batch n+1 is created only after batch n is
confirmed. A row's events are never split across batches.

Consistency between the session (``desk.sqlite3``) and its proposals (``ledger.sqlite3``)
-------------------------------------------------------------------------------------
Every proposal the importer makes is *bound* to its session (``Ledger.propose(binding=...)``):
the ledger stores (session id, ``fingerprint(session)``) with it and keeps the batch
records (number, rows, the account identities of its rows) there, so ``propose`` writes
to the ledger file only. The fingerprint covers everything the events are built from
besides the ledger: the session content, the remembered US exchanges and the account's
chosen identities, read once per analysis (``snapshot``, so the events and the token
always come from the same snapshot), and ``INTERPRETATION_VERSION``.

Lock order: the desk store's write lock is always taken before the ledger's. PATCH and
DELETE, the only import writes that need both, hold the desk transaction and take the
ledger's write lock inside it; propose and confirmation take only the ledger's and only
read the desk store (WAL readers never wait). Other writers of both files follow the same
order (a desk proposal, such as a watchlist confirmation, reads the ledger inside its desk
transaction, and opening an existing ledger only reads it), so no two of them wait for
each other.

* propose: inside the ledger's write transaction the binding check reads the
  session's current fingerprint and the ledger revision; both must equal what the
  events were built from (a remembered exchange or identity changed meanwhile: 409).
  The session's other unconfirmed proposals are withdrawn in the same transaction and a
  unique index allows one pending proposal per session. Asking again with unchanged
  content and ledger returns the same proposal.
* PATCH and DELETE: take the desk store's write lock, check the session is still the
  version that was read (else 409), store or delete it, then take the ledger's write lock,
  withdraw the session's proposals when the content changes, and commit the ledger
  transaction before the desk one. A ledger that is busy, or whose commit fails (a reader
  holding it), refuses the request (503) and the desk transaction rolls back: nothing is
  stored or deleted.
* confirm (``POST /api/ledger/proposals/{id}/confirm``): ``Ledger.confirm`` runs the
  binding check inside its write transaction. A proposal whose session changed,
  expired or was deleted, or whose ledger revision passed, is refused (409) and
  withdrawn; confirming a withdrawn proposal answers 409.

A crash between the two commits (the ledger's comes first) leaves the proposals withdrawn
and the session as it was: the safe direction, the batch is prepared again. A crash before
the ledger commit changes neither file. ``propose`` and a confirmation are single ledger
transactions. So a stale proposal is never confirmable; at worst a proposal is lost and
is prepared again.

Upgrades: the fingerprint includes ``INTERPRETATION_VERSION``, bumped whenever the rules
change, so a proposal prepared by an earlier release (whose rules may give other events)
is stale: confirmation answers 409 and withdraws it. A proposal made before bindings
existed (``session['batches']``) has no binding: the ledger refuses to confirm any unbound
proposal of ``import:`` events (409, withdrawn), also after its session expired, and the
session's next PATCH, DELETE or propose withdraws it.
"""
from __future__ import annotations

from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re
import sqlite3
import uuid
from typing import Dict, List, Optional

from core import desk_store
from core.ledger import (Conflict, Ledger, LedgerError, StaleProposal, label, portfolio_directory, register_binding,
                         replay, valid_date)
from core.importers import (INTERPRETATION_VERSION, ImportFailure, accounts, aliases, codes, dedupe, holdings, mapping,
                            reconcile, sniff, transactions)
from core.importers.transactions import BOOKABLE, STATUS_LABELS, Context
from core.importers.values import comma_convention, decimal, money, text

BATCH_LIMIT = 1000
KEEP_SESSIONS = 50
KEEP_DAYS = 30
STATEMENT_LABELS = {'transactions': '交割单 / 对账单', 'holdings': '持仓', 'template': '本系统模板'}

MIGRATIONS = [[
    """CREATE TABLE import_sessions (
        id TEXT PRIMARY KEY,
        account TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload TEXT NOT NULL)""",
    "CREATE INDEX import_sessions_updated ON import_sessions(updated_at)",
]]
SCHEMA = 'import_sessions'
desk_store.register_schema(SCHEMA, MIGRATIONS)


def get_ledger() -> Ledger:
    """The ledger for writing (``propose``); creates the file when it is missing."""
    return Ledger(portfolio_directory() / 'ledger.sqlite3')


def read_ledger() -> Optional[Ledger]:
    """The ledger for reading, or ``None`` while it does not exist: a read never creates it."""
    path = portfolio_directory() / 'ledger.sqlite3'
    return Ledger(path) if path.is_file() else None


def source_for(account: str) -> str:
    return f'import:{account}'[:240]


# -- storage -------------------------------------------------------------------

def _cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat(timespec='seconds')


def _purge(db) -> None:
    """Delete expired sessions (and their rows) and all but the most recent ``KEEP_SESSIONS``."""
    db.execute('PRAGMA secure_delete=ON')
    db.execute('DELETE FROM import_sessions WHERE created_at < ?', (_cutoff(),))
    db.execute('DELETE FROM import_sessions WHERE id NOT IN '
               '(SELECT id FROM import_sessions ORDER BY created_at DESC, updated_at DESC LIMIT ?)', (KEEP_SESSIONS,))


def _store(session: dict, *, new: bool = False, expected: Optional[dict] = None) -> None:
    """Write the session. ``expected``: the version it was read as; refused (409) when another request stored a
    different one meanwhile, so a concurrent PATCH is never lost."""
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    session['updated_at'] = desk_store.now()
    with desk_store.transaction() as db:
        if new:
            db.execute('INSERT INTO import_sessions(id, account, created_at, updated_at, payload) VALUES (?,?,?,?,?)',
                       (session['id'], session['account'], session['created_at'], session['updated_at'],
                        desk_store.encode(session)))
        else:
            if expected is not None:
                row = db.execute('SELECT payload FROM import_sessions WHERE id=? AND created_at >= ?',
                                 (session['id'], _cutoff())).fetchone()
                if row is None:
                    raise ImportFailure('导入记录不存在或已过期。请重新上传文件。', 404)
                if row['payload'] != desk_store.encode(expected):
                    raise ImportFailure('这条导入记录刚刚被另一个请求修改了，本次修改没有保存。请刷新后重试。', 409)
            db.execute('UPDATE import_sessions SET updated_at=?, payload=? WHERE id=?',
                       (session['updated_at'], desk_store.encode(session), session['id']))
        _purge(db)


GONE = '导入记录不存在或已过期。请重新上传文件。'


def load(session_id: str) -> dict:
    """A stored session; 404 when it does not exist or is older than ``KEEP_DAYS`` (a read deletes nothing)."""
    if not isinstance(session_id, str) or not re.fullmatch(r'[0-9a-f]{32}', session_id):
        raise ImportFailure(GONE, 404)
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        raise ImportFailure(GONE, 404)
    with desk_store.connect() as db:
        row = db.execute('SELECT payload FROM import_sessions WHERE id=? AND created_at >= ?',
                         (session_id, _cutoff())).fetchone()
    if row is None:
        raise ImportFailure(GONE, 404)
    return json.loads(row['payload'])


def delete(session_id: str) -> dict:
    """Delete the session and withdraw its unconfirmed proposals (they could no longer be confirmed anyway).
    Lock and commit order as in ``patch``; refused (409) when the session changed after it was read."""
    session = load(session_id)
    with desk_store.transaction() as db:
        row = db.execute('SELECT payload FROM import_sessions WHERE id=? AND created_at >= ?',
                         (session_id, _cutoff())).fetchone()
        if row is None:
            raise ImportFailure(GONE, 404)
        if row['payload'] != desk_store.encode(session):
            raise ImportFailure('这条导入记录刚刚被另一个请求修改了，没有删除。请刷新后重试。', 409)
        db.execute('PRAGMA secure_delete=ON')
        db.execute('DELETE FROM import_sessions WHERE id=?', (session_id,))
        _purge(db)
        with _ledger_lock() as ldb:   # commits before the desk transaction; a failure undoes the deletion
            if ldb is not None:
                _withdraw(ldb, session)
                _retire_orphans(ldb)
    return {'deleted': True}


# -- the binding between a session and its ledger proposals ----------------------------------

BINDING = 'import_session'
# Everything the events of a batch are built from, besides the ledger.
CONTENT_KEYS = ('statement', 'mapping', 'chosen_fields', 'edits', 'short_code_market', 'opening', 'account_filter',
                'sheet', 'signature', 'table')


def _content(session: dict) -> dict:
    return {key: session.get(key) for key in CONTENT_KEYS}


def snapshot(account: str) -> dict:
    """What an analysis reads from the desk store besides the session, read once: the remembered US exchanges
    and the ledger account's chosen identities. The events and the fingerprint of one analysis use one snapshot."""
    return {'aliases': aliases.lookup(), 'identities': accounts.chosen_identities(account)}


def fingerprint(session: dict, taken: Optional[dict] = None) -> str:
    """What a proposal's events are built from besides the ledger: the session content, the desk store's
    ``snapshot`` (read now unless ``taken`` is given) and the version of the interpretation rules
    (``INTERPRETATION_VERSION``). See the module docstring."""
    taken = taken if taken is not None else snapshot(session['account'])
    return hashlib.sha256(desk_store.encode([INTERPRETATION_VERSION, session['id'], session['account'],
                                             _content(session), taken['aliases'],
                                             taken['identities']]).encode()).hexdigest()


def _binding_check(subject: str, token: str) -> Optional[str]:
    """``Ledger.confirm`` / ``Ledger.propose``: None while the session still holds exactly the content ``token``."""
    try:
        stored = load(subject)
    except ImportFailure:
        return ('这份入账预览来自的导入记录已删除或已过期（上传 30 天后删除），入账预览已作废，不能确认。'
                '请重新上传文件，再生成入账预览。')
    if fingerprint(stored) != token:
        return ('导入的内容（或记住的美股交易所、账户识别）在生成这份入账预览之后修改过，或导入规则已随程序更新，'
                '这份入账预览已作废，不能确认。请重新生成入账预览。')
    return None


register_binding(BINDING, _binding_check)


class _NoLedger:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def _ledger_lock():
    """The ledger's write lock while the ledger file exists (no file: no proposals, nothing to lock)."""
    ledger = read_ledger()
    return ledger.writing() if ledger is not None else _NoLedger()


def _withdraw(ldb, session: dict) -> None:
    """Inside the ledger lock: withdraw every unconfirmed proposal of the session (bound, and legacy records)."""
    Ledger.withdraw_bound(ldb, BINDING, session['id'])
    for record in session.get('batches') or []:
        Ledger.withdraw(ldb, record.get('proposal_id'))


def _retire_orphans(ldb) -> None:
    """Inside the ledger lock: withdraw unconfirmed proposals whose session expired or was deleted."""
    subjects = Ledger.bound_subjects(ldb, BINDING)
    if not subjects:
        return
    live = set()
    if desk_store.ready(SCHEMA, MIGRATIONS):
        with desk_store.connect() as db:
            marks = ','.join('?' * len(subjects))
            live = {row['id'] for row in db.execute(
                f'SELECT id FROM import_sessions WHERE created_at >= ? AND id IN ({marks})', (_cutoff(), *subjects))}
    for subject in subjects:
        if subject not in live:
            Ledger.withdraw_bound(ldb, BINDING, subject)


# -- create --------------------------------------------------------------------

def _account(value) -> str:
    try:
        return label(value, 'account')
    except LedgerError:
        raise ImportFailure('请选择账户（账户名不能为空，最多 240 个字符）。') from None


def _use_table(session: dict, table: sniff.Table, statement: Optional[str]) -> None:
    """Put ``table`` in the session with its mapping: the one saved for (account, header layout) if it fits,
    otherwise detected. Clears what belonged to another table (row edits, chosen fields)."""
    header = table.header
    header_signature = mapping.signature(header)
    remembered = mapping.saved(session['account'], header_signature)
    chosen = statement if statement in mapping.STATEMENTS else None
    columns, options = None, {}
    if remembered and (chosen is None or chosen == remembered['statement']):
        try:
            columns = mapping.validate(remembered['mapping'], len(header), remembered['statement'])
            chosen, source = remembered['statement'], 'saved'
            options = remembered.get('options') or {}
        except ImportFailure:
            columns = None
    if columns is None:
        chosen = chosen or mapping.detect_statement(header)
        columns = mapping.detect(header, chosen)
        source = 'preset' if mapping.identify(header, chosen)['id'] != 'generic' else 'synonyms'
    session.update({
        'table': table.to_json(), 'signature': header_signature, 'statement': chosen, 'mapping': columns,
        'mapping_source': source, 'edits': {},
        'chosen_fields': [f for f in options.get('chosen', []) if isinstance(f, str)][:50],
        'account_filter': accounts.saved_choice(options),
    })


def create(data: bytes, filename: str, account, statement: Optional[str] = None, *, analysed: bool = False):
    """Parse an upload into a new session. It is stored only after it has been analysed once,
    so a file the importer cannot compute is refused instead of leaving a session that fails.
    Returns the session, or ``(session, analysis)`` with ``analysed=True``."""
    account = _account(account)
    if statement not in (None, '', 'auto') and statement not in mapping.STATEMENTS:
        raise ImportFailure('文件类型只能是 transactions、holdings、template 或 auto。')
    table, tables = sniff.sniff_all(data, filename or '')
    if desk_store.path().is_file():
        # A write: bring an existing store's saved mappings up to date before reading them.
        desk_store.ensure_schema(mapping.SCHEMA, mapping.MIGRATIONS)
    now = desk_store.now()
    session = {
        'id': uuid.uuid4().hex, 'account': account, 'created_at': now, 'updated_at': now,
        'file': {'name': (filename or '')[-200:], 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
        'short_code_market': None, 'opening': None, 'batches': [], 'sheet': None,
        # The other data sheets of a workbook, so the person can switch without uploading again.
        'tables': {t.sheet: t.to_json() for t in tables if t is not table and t.sheet},
    }
    _use_table(session, table, statement)
    analysis = analyse(session)
    _store(session, new=True)
    _tidy_ledger()
    return (session, analysis) if analysed else session


def _tidy_ledger() -> None:
    """Withdraw the proposals of sessions the upload just deleted (expired ones). Best effort: they can no
    longer be confirmed anyway, and a busy ledger must not fail an upload that is already stored."""
    try:
        with _ledger_lock() as ldb:
            if ldb is not None:
                _retire_orphans(ldb)
    except sqlite3.OperationalError:
        pass


def _switch_sheet(session: dict, name) -> None:
    current = sniff.Table.from_json(session['table'])
    others = session.get('tables') or {}
    if not isinstance(name, str) or name != current.sheet and name not in others:
        names = [n for n in [current.sheet, *others] if n]
        raise ImportFailure('没有这个工作表。可以选择：' + ('、'.join(f'“{n}”' for n in names) or '（只有一个表格）'))
    if name != current.sheet:
        others = dict(others)
        others[current.sheet] = current.to_json()
        _use_table(session, sniff.Table.from_json(others.pop(name)), None)
        session['tables'] = others
    session['sheet'] = name


# -- analysis ------------------------------------------------------------------

def _known_tickers(events: List[dict]) -> List[str]:
    found = set()
    for event in events:
        if event.get('ticker'):
            found.add(event['ticker'])
        for position in event.get('positions', []) or []:
            found.add(position['ticker'])
    return sorted(found)


def _confirmed_identities(ledger: Optional[Ledger], account: str) -> List[list]:
    """The account identities of the rows earlier imports confirmed into ``account`` (kept with their bindings)."""
    if ledger is None:
        return []
    out = []
    for bound in ledger.bindings(BINDING):
        info = bound['info'] if isinstance(bound['info'], dict) else {}
        if bound['state'] == 'confirmed' and info.get('account') == account:
            out += [pair for pair in info.get('identities') or [] if isinstance(pair, list)]
    return out


def _lot_of(columns: dict, numeric_columns: List[int]):
    """Shares per 手 of a row's security when certain (``codes.lot_size``), for comparing quantity columns."""
    code = columns.get('code')

    def lot(cells) -> Optional[int]:
        if code is None or code >= len(cells):
            return None
        result = codes.resolve(cells[code], numeric_cell=code in numeric_columns)
        return codes.lot_size(result.ticker) if result.status == 'ok' else None
    return lot


def analyse(session: dict, ledger: Optional[Ledger] = None, taken: Optional[dict] = None) -> dict:
    """Rows, statuses and checks of a session against the current ledger. ``result['snapshot']`` is the desk
    store snapshot it read (``taken``, or read now), for the fingerprint of a proposal made from it."""
    ledger = ledger or read_ledger()
    revision, existing = 0, []
    if ledger is not None:
        with ledger.connect() as db:
            revision = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()[0]
        existing = ledger.history()
    account = session['account']
    taken = taken if taken is not None else snapshot(account)
    source = source_for(account)
    table = sniff.Table.from_json(session['table'])
    try:
        state = replay(existing)
    except LedgerError:
        state = {'accounts': {}}
    account_exists = account in state['accounts']
    ctx = Context(account=account, source=source, known_tickers=_known_tickers(existing), aliases=taken['aliases'],
                  short_code_market=session.get('short_code_market'), numeric_columns=table.numeric_columns,
                  whitespace=table.delimiter == 'whitespace', account_choice=session.get('account_filter'),
                  identities=accounts.memory(_confirmed_identities(ledger, account), taken['identities']),
                  comma=comma_convention(([c for i, c in enumerate(cells) if i not in table.native(number)]
                                          for number, cells in table.data()), table.delimiter))
    statement = session['statement']
    edits = session.get('edits', {})
    blockers, warnings = [], []
    conflicts = mapping.column_conflicts(table.header, table.data(), session['mapping'], statement,
                                         session.get('chosen_fields') or [],
                                         {'action': transactions.direction, 'market': lambda cell: codes.market_hint(market=cell)},
                                         _lot_of(session['mapping'], table.numeric_columns), ctx.comma, table.native)
    result = {'revision': revision, 'account_exists': account_exists, 'table': table, 'blockers': blockers,
              'warnings': warnings, 'reconciliation': None, 'holdings': None, 'existing': existing,
              'column_conflicts': conflicts, 'snapshot': taken}
    blockers += [mapping.conflict_message(c, table.header) for c in conflicts if not c['resolved']]
    if table.sheet_ambiguous and not session.get('sheet'):
        blockers.append(_sheet_blocker(table))
    if statement == 'holdings':
        rows = holdings.interpret(table, session['mapping'], ctx, edits)
        _account_view(result, ctx, account)
        _apply_includes(rows)
        result['rows'] = rows
        result['holdings'] = _holdings_view(session, rows, state, account_exists, blockers)
        return result
    if statement == 'template':
        rows = transactions.interpret_template(table, session['mapping'], ctx, edits)
    else:
        rows = transactions.interpret(table, session['mapping'], ctx, edits)
    _account_view(result, ctx, account)
    # Every id this file could produce: existing events with these ids are this file's own, not duplicates.
    claimed = {event['id'] for row in rows for event in row.events}
    claimed |= {dedupe.ledger_id(source, row.external_id + suffix) for row in rows if row.external_id
                for suffix in dedupe.SUFFIXES}
    dedupe.compare(rows, existing, source)
    dedupe.fuzzy(rows, existing, account, claimed)
    _apply_includes(rows)
    final = None
    if account_exists:
        final = reconcile.simulate(rows, existing, account, ctx)
    else:
        blockers.append(f'账户“{account}”还没有期初余额。请先建立期初（可以导入这个账户的持仓文件），再导入交割单。')
    result['rows'] = rows
    result['reconciliation'] = reconcile.balances(rows, final, existing, account)
    if any(r.category == 'repo' for r in rows):
        warnings.append('逆回购按“借出 + 购回”配对后，只把净收益记为利息；跨文件的配对需要手工处理。')
    return result


def _sheet_blocker(table: sniff.Table) -> str:
    options = table.sheet_options
    quoted = lambda names: '、'.join('“' + name + '”' for name in names)
    if all(option['hidden'] for option in options):
        return (f'这个工作簿只有隐藏的工作表（{quoted(o["name"] for o in options)}）包含数据，'
                f'现在显示的是“{table.sheet}”。请确认要导入的工作表。')
    visible = [o['name'] for o in options if not o['hidden']]
    return (f'工作簿里有 {len(visible)} 个工作表看起来都是数据（{quoted(visible)}），'
            f'现在显示的是“{table.sheet}”。请选择要导入的工作表。')


def _account_view(result: dict, ctx: Context, account: str) -> None:
    result['accounts'] = ctx.accounts
    message = accounts.blocker(ctx.accounts, account)
    if message:
        result['blockers'].append(message)


def _apply_includes(rows):
    for row in rows:
        if row.status in BOOKABLE and 'include' in row.edits:
            row.include = bool(row.edits['include'])


def _holdings_view(session, rows, state, account_exists, blockers) -> dict:
    table = sniff.Table.from_json(session['table'])
    account = session['account']
    view = {'mode': 'reconcile' if account_exists else 'opening', 'summary': holdings.summary(table.title_rows),
            'cost_note': holdings.COST_NOTE, 'opening': session.get('opening'),
            'positions': holdings.positions(rows)}
    if account_exists:
        view['reconciliation'] = holdings.compare(rows, state['accounts'][account], account)
        blockers.append('这个账户已经有期初余额：持仓文件只用于对账，不会生成交易。差异请用“记一笔”逐笔补录。')
        return view
    stored_problem = _stored_opening_problem(session.get('opening'))
    view['suggested_contributed_cny'] = holdings.suggested_contributions(rows, None if stored_problem
                                                                           else session.get('opening'))
    pending = [r.row for r in rows if r.status == 'review' and r.edits.get('include') is not False]
    if pending:
        blockers.append(f'还有 {len(pending)} 行需处理：请选择代码、填写成本价，或把它们排除。')
    if stored_problem:
        blockers.append(stored_problem)
    opening = session.get('opening') or {}
    if not (opening.get('date') and opening.get('cash_balances') is not None and opening.get('contributed_cny') is not None):
        blockers.append('请填写期初日期、各币种现金和累计净投入（持仓文件里没有这些数字）。')
    return view


def _stored_opening_problem(opening) -> Optional[str]:
    """Opening values stored with the session, checked again with today's PATCH rules (they may predate them)."""
    if not opening:
        return None
    try:
        _opening(opening)
    except ImportFailure as exc:
        return f'之前保存的期初信息无法使用：{exc}（保存的是 {desk_store.encode(opening)[:160]}）请重新填写期初信息。'
    return None


# -- view ------------------------------------------------------------------------

def _groups(rows) -> List[List]:
    pending = sorted((r for r in rows if r.status in BOOKABLE and r.include and r.events), key=lambda r: r.order)
    groups, current, size = [], [], 0
    for row in pending:
        if current and size + len(row.events) > BATCH_LIMIT:
            groups.append(current)
            current, size = [], 0
        current.append(row)
        size += len(row.events)
    if current:
        groups.append(current)
    return groups


def _records(session: dict, ledger: Optional[Ledger]) -> List[dict]:
    """The session's batches, oldest first: ``{number, proposal_id, events, rows, created_at, state}`` plus, for a
    pending one, ``token``, ``revision`` and ``proposal_events``. Kept with the proposals in the ledger file;
    ``session['batches']`` holds the records of proposals made before bindings existed."""
    if ledger is None:
        return []
    out = []
    for record in session.get('batches') or []:   # legacy
        proposal = ledger.proposal(record['proposal_id'])
        state = 'discarded' if proposal is None else ('confirmed' if proposal['receipt'] else 'pending')
        out.append({**{k: v for k, v in record.items() if k != 'fingerprint'}, 'state': state, 'token': None,
                    'revision': proposal['revision'] if proposal else None,
                    'proposal_events': proposal['events'] if state == 'pending' else None})
    for bound in ledger.bindings(BINDING, session['id']):
        info = bound['info']
        out.append({'number': info.get('number'), 'proposal_id': bound['proposal_id'], 'events': info.get('events'),
                    'rows': info.get('rows'), 'created_at': bound['created_at'], 'state': bound['state'],
                    'token': bound['token'], 'revision': bound['revision'], 'proposal_events': bound['events']})
    return out


_PUBLIC_RECORD = ('number', 'proposal_id', 'events', 'rows', 'created_at', 'state')


def _next_batch(session: dict, analysis: dict):
    """(events, row numbers) of the batch propose would create now, or (None, None) when it cannot."""
    rows = analysis['rows']
    if analysis['blockers']:
        return None, None
    if session['statement'] == 'holdings':
        event = holdings.opening_event(rows, session['opening'], session['account'], source_for(session['account']))
        return [event], [r.row for r in rows if r.status in BOOKABLE and r.include]
    groups = _groups(rows)
    if not groups:
        return None, None
    return [event for row in groups[0] for event in row.events], [row.row for row in groups[0]]


def _batches(session, analysis, ledger, revision) -> dict:
    rows = analysis['rows']
    records = _records(session, ledger)
    confirmed = sum(1 for record in records if record['state'] == 'confirmed')
    history = [{key: record[key] for key in _PUBLIC_RECORD} for record in records]
    pending = None
    waiting = [record for record in records if record['state'] == 'pending']
    if waiting:
        last = waiting[-1]
        events, _numbers = _next_batch(session, analysis)
        # A legacy proposal (no token) is always prepared again.
        stale = (last['token'] is None or last['token'] != fingerprint(session, analysis.get('snapshot'))
                 or last['revision'] != revision
                 or events is None or desk_store.encode(last['proposal_events']) != desk_store.encode(events))
        pending = {**{key: last[key] for key in _PUBLIC_RECORD}, 'stale': stale}
    groups = _groups(rows)
    remaining = sum(len(r.events) for group in groups for r in group)
    return {'confirmed': confirmed, 'pending': pending, 'remaining_events': remaining,
            'remaining_batches': len(groups), 'total': confirmed + len(groups),
            'next': confirmed + 1 if groups else None, 'limit': BATCH_LIMIT, 'history': history}


def _skipped_summary(rows) -> List[dict]:
    groups: Dict[tuple, dict] = {}
    for row in rows:
        if row.status in ('skipped', 'unsupported'):
            key = (row.status, row.messages[0] if row.messages else STATUS_LABELS[row.status])
            group = groups.setdefault(key, {'status': row.status, 'label': key[1], 'count': 0, 'rows': []})
            group['count'] += 1
            group['rows'].append(row.row)
    merged = [r for r in rows if r.status == 'merged']
    out = list(groups.values())
    if merged:
        pairs = {r.merged_into for r in merged}
        interest = sum((Decimal(e['amount']) for r in rows if r.row in pairs
                        for e in r.events if e['kind'] == 'interest'), Decimal(0))
        out.append({'status': 'merged', 'label': f'逆回购 {len(merged) * 2} 行合并为利息 {money(interest)}',
                    'count': len(merged), 'rows': sorted([r.row for r in merged] + list(pairs)),
                    'amount': text(interest)})
    return out


def view(session: dict, analysis: Optional[dict] = None, *, status: Optional[str] = None, offset: int = 0,
         limit: Optional[int] = None, include_rows: bool = True) -> dict:
    ledger = read_ledger()
    analysis = analysis or analyse(session, ledger)
    rows, table = analysis['rows'], analysis['table']
    header = table.header
    statement = session['statement']
    counts = Counter(r.status for r in rows)
    included = [r for r in rows if r.status in BOOKABLE and r.include]
    dates = sorted(r.date for r in rows if r.date)
    out = {
        'id': session['id'], 'account': session['account'], 'account_exists': analysis['account_exists'],
        'created_at': session['created_at'], 'updated_at': session['updated_at'], 'file': session['file'],
        'format': {'type': table.kind, 'label': table.label, 'encoding': table.encoding, 'delimiter': table.delimiter,
                   'sheet': table.sheet, 'sheets': table.sheets, 'header_row': table.header_index + 1,
                   'sheet_options': table.sheet_options, 'sheet_chosen': bool(session.get('sheet')),
                   'extension': table.extension, 'title_lines': [' '.join(c for c in r if c) for r in table.title_rows][:10]},
        'statement': statement, 'statement_label': STATEMENT_LABELS[statement],
        'preset': mapping.identify(header, statement),
        'columns': mapping.describe(header, session['mapping'], statement),
        'mapping': session['mapping'], 'mapping_source': session.get('mapping_source'),
        'fields': mapping.catalog(statement), 'short_code_market': session.get('short_code_market'),
        'date_range': {'from': dates[0], 'to': dates[-1]} if dates else None,
        'counts': {**{key: counts.get(key, 0) for key in STATUS_LABELS}, 'total': len(rows),
                   'included_rows': len(included), 'included_events': sum(len(r.events) for r in included)},
        'status_labels': STATUS_LABELS,
        'skipped': _skipped_summary(rows),
        'reconciliation': analysis['reconciliation'],
        'holdings': analysis['holdings'],
        'batches': _batches(session, analysis, ledger, analysis['revision']),
        'blockers': analysis['blockers'], 'warnings': analysis['warnings'],
        'column_conflicts': analysis.get('column_conflicts', []), 'accounts': analysis.get('accounts'),
        'ledger_revision': analysis['revision'],
    }
    if include_rows:
        selected = [r for r in rows if status is None or r.status == status]
        out['rows_total'] = len(selected)
        selected = selected[offset:offset + limit if limit is not None else None]
        out['rows'] = [r.to_json() for r in selected]
        out['rows_offset'] = offset
    return out


# -- edits -----------------------------------------------------------------------

def _row_numbers(session) -> set:
    table = sniff.Table.from_json(session['table'])
    return {number for number, _cells in table.data()}


def _opening(raw: dict) -> Optional[dict]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ImportFailure('期初信息必须是一个对象。')
    out = {}
    if raw.get('date') is not None:
        try:
            out['date'] = valid_date(raw['date'])
        except LedgerError:
            raise ImportFailure('期初日期的格式应为 2026-11-14。') from None
    if raw.get('cash_balances') is not None:
        balances = raw['cash_balances']
        if not isinstance(balances, dict) or set(balances) - {'CNY', 'HKD', 'USD'}:
            raise ImportFailure('期初现金只能包含 CNY、HKD、USD。')
        out['cash_balances'] = {}
        for unit, value in balances.items():
            try:
                number = decimal(value) if isinstance(value, str) else None
            except ValueError:
                number = None
            if number is None:
                raise ImportFailure(f'{unit} 期初现金必须是数字字符串。')
            out['cash_balances'][unit] = text(number)
    for key, name in (('contributed_cny', '累计净投入'), ('fund_cny', '基金市值')):
        if raw.get(key) is not None:
            try:
                number = decimal(raw[key]) if isinstance(raw[key], str) else None
            except ValueError:
                number = None
            if number is None or (key == 'fund_cny' and number < 0):
                raise ImportFailure(f'{name}必须是数字字符串。')
            out[key] = text(number)
    return out


def patch(session_id: str, body: dict) -> dict:
    """Apply a PATCH atomically: everything is validated and analysed on a copy first.

    A request that is refused (any part invalid, or the result cannot be
    computed) changes neither the stored session nor the saved mapping or
    aliases. The writes happen together in one desk transaction.
    """
    stored = load(session_id)
    session = copy.deepcopy(stored)
    if 'sheet' in body:
        _switch_sheet(session, body['sheet'])
    table = sniff.Table.from_json(session['table'])
    header = table.header
    save_mapping = False
    if body.get('statement') is not None:
        if body['statement'] not in mapping.STATEMENTS:
            raise ImportFailure('文件类型只能是交割单 / 对账单、持仓或导入模板。')
        if body['statement'] != session['statement']:
            session['statement'] = body['statement']
            session['mapping'] = mapping.detect(header, body['statement'])
            session['mapping_source'] = 'synonyms'
            session['edits'] = {}
            session['chosen_fields'] = []
    if body.get('mapping') is not None:
        merged = {**session['mapping'], **body['mapping']}
        merged = {k: v for k, v in merged.items() if v is not None}
        session['mapping'] = mapping.validate(merged, len(header), session['statement'])
        session['mapping_source'] = 'edited'
        # Every field named in the request is a choice the person made (also a null: "not this column").
        session['chosen_fields'] = sorted(set(session.get('chosen_fields') or []) | set(body['mapping']))[:50]
        save_mapping = True
    if 'account_filter' in body:
        requested = body['account_filter']
        session['account_filter'] = None if requested is None else accounts.choice(table, session['mapping'], requested)
        save_mapping = True
    if 'short_code_market' in body:
        value = body['short_code_market']
        if value not in (None, '', 'HKG', 'A'):
            raise ImportFailure('短代码只能按港股或 A 股读取，或不指定。')
        session['short_code_market'] = value or None
    edits = body.get('rows') or []
    if edits:
        numbers = _row_numbers(session)
        for raw in edits:
            row = raw.get('row')
            if isinstance(row, bool) or not isinstance(row, int) or row not in numbers:
                raise ImportFailure(f'没有第 {row} 行。')
            updated = transactions.validate_edit(row, raw, session['edits'].get(str(row), {}), session['statement'])
            if updated:
                session['edits'][str(row)] = updated
            else:
                session['edits'].pop(str(row), None)
    if body.get('opening') is not None:
        session['opening'] = {**(session.get('opening') or {}), **_opening(body['opening'])}
    taken = snapshot(session['account'])
    analysis = analyse(session, taken=taken)
    changed = fingerprint(session, taken) != fingerprint(stored, taken)
    # Lock order (module docstring): the desk store's write lock, then the ledger's inside it. The ledger
    # transaction commits first; when the ledger is busy or its commit fails, the desk transaction rolls back
    # and nothing of the PATCH is stored.
    with desk_store.transaction():
        _store(session, expected=stored)
        if save_mapping:
            remembered = session.get('account_filter')
            mapping.save(session['account'], session['signature'], session['statement'], session['mapping'],
                         {'chosen': session.get('chosen_fields') or [],
                          'account_filter': {'pairs': remembered['pairs'], 'blank': remembered['blank']}
                          if remembered else None})
        if body.get('account_filter') is not None and session.get('account_filter'):
            accounts.remember_choice(session['account'], table, session['mapping'], session['account_filter'])
        for row in analysis['rows']:
            chosen = row.edits.get('ticker')
            symbol = codes.bare(row.code)
            if chosen and re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', symbol) and chosen in (f'NASDAQ:{symbol}', f'NYSE:{symbol}'):
                aliases.remember(f'US:{symbol}', chosen, row.name or None, 'import')
        with _ledger_lock() as ldb:
            if ldb is not None:
                if changed:
                    _withdraw(ldb, session)
                _retire_orphans(ldb)
    return session, analysis


# -- propose -----------------------------------------------------------------------

_LEDGER_MESSAGES = [
    ('Insufficient native cash', '现金不足：有买入超过了账本当时的现金。请检查需处理的行。'),
    ('Cannot sell more shares than held', '有卖出超过了账本当时的持仓。请检查需处理的行。'),
    ('Create an opening balance', '账户还没有期初余额。'),
    ('Account already exists', '账户已经有期初余额。'),
    ('Duplicate opening position', '期初中同一代码出现了两次。'),
]


def _ledger_message(exc: Exception) -> str:
    message = str(exc)
    for needle, chinese in _LEDGER_MESSAGES:
        if needle in message:
            return chinese
    return f'账本拒绝了这批记录：{transactions.ledger_reason(exc)}。'


def _discard(ledger: Ledger, proposal_id: str) -> None:
    with ledger.writing() as db:
        Ledger.withdraw(db, proposal_id)


def propose(session_id: str) -> dict:
    """Prepare the next batch as a ledger proposal bound to the session (see the module docstring)."""
    session = load(session_id)
    ledger = get_ledger()
    analysis = analyse(session, ledger)
    revision = analysis['revision']
    events, batch_rows = _next_batch(session, analysis)
    if analysis['blockers']:
        raise ImportFailure(analysis['blockers'][0], 409)
    if events is None:
        raise ImportFailure('没有需要入账的新记录：全部已导入、未勾选或需处理。', 409)
    if session['statement'] == 'holdings' and not events[0]['positions'] and not any(events[0]['cash_balances'].values()):
        raise ImportFailure('期初没有持仓也没有现金。', 422)
    with ledger.writing() as ldb:
        for record in session.get('batches') or []:   # proposals made before bindings existed
            Ledger.withdraw(ldb, record.get('proposal_id'))
        _retire_orphans(ldb)
    number = sum(1 for record in _records(session, ledger) if record['state'] == 'confirmed') + 1
    batch = set(batch_rows)
    identities = sorted({tuple(pair) for row in analysis['rows'] if row.row in batch for pair in row.account_ids})
    binding = {'scope': BINDING, 'subject': session['id'], 'token': fingerprint(session, analysis['snapshot']),
               'info': {'number': number, 'rows': batch_rows, 'events': len(events), 'account': session['account'],
                        'identities': [list(pair) for pair in identities]}}
    try:
        # Rows hold normalized events (with the ledger id, for dedupe); the ledger normalizes again.
        proposal = ledger.propose([{k: v for k, v in event.items() if k != 'id'} for event in events],
                                  binding=binding, expect_revision=revision)
    except StaleProposal as exc:
        message = str(exc)
        raise ImportFailure(message if '过期' in message else STALE_MESSAGE, 409) from None
    except Conflict as exc:
        raise ImportFailure(f'与已入账记录冲突：{exc}', 409) from None
    except LedgerError as exc:
        raise ImportFailure(_ledger_message(exc)) from None
    reused = bool(proposal.pop('reused', False))
    if len(proposal['events']) != len(events) or proposal['duplicates']:
        # The ledger dropped or merged events the preview shows: never let that proposal be confirmed.
        _discard(ledger, proposal['id'])
        raise ImportFailure(f'预览有 {len(events)} 条记录，账本收下的只有 {len(proposal["events"])} 条，两者不一致。'
                            '没有生成入账预览；请刷新预览后重试，或检查是否有重复的行。', 409)
    record = {'number': number, 'proposal_id': proposal['id'], 'events': len(events), 'rows': batch_rows}
    return {'proposal': proposal, 'batch': {**_batch_label(record, analysis, session, ledger), 'reused': reused},
            'import': view(session, analysis, include_rows=False)}


STALE_MESSAGE = '账本或导入内容在准备这批记录时发生了变化。请刷新预览后重试。'


def _batch_label(record, analysis, session, ledger) -> dict:
    groups = _groups(analysis['rows'])
    confirmed = sum(1 for r in _records(session, ledger) if r['state'] == 'confirmed')
    total = max(confirmed + len(groups), record['number'])
    return {'number': record['number'], 'total': total, 'events': record['events'], 'rows': record['rows'],
            'label': f"第 {record['number']}/{total} 批"}
