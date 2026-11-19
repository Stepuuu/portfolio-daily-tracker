"""Propose-then-confirm for writes that are not ledger events.

The AI agent and the scheduled routines may suggest an alert rule, a lesson, a
trade plan or a watchlist change. They can only ever *propose*: a proposal is a
stored description of a write. Nothing is written until a person confirms it
in the web app. ``confirm()`` is the only function in this module that calls a
kind's ``apply``, and nothing here calls ``confirm()``. (Ledger events keep
their own flow in ``core/ledger.py``.)

A feature registers its kind once, at import time::

    from core import proposals

    def validate(payload: dict) -> dict:
        # Check and normalise. Raise proposals.ProposalError("中文说明") when the
        # payload is not acceptable. Must not write anything, and must accept
        # its own output unchanged: it runs again when the user confirms.
        return {"ticker": instrument(payload.get("ticker")), ...}

    def describe(payload: dict) -> str:
        # One line the user reads before deciding, built by code from the payload.
        return f"为 {payload['ticker']} 添加止损提醒：{payload['threshold']}"

    def apply(payload: dict, proposal: dict):
        # The real write. Runs only on confirm, at most once per proposal.
        # ``proposal`` has ``id``, ``kind``, ``source``, ``created_at``.
        # Return a JSON value (or None); it is stored as the proposal's result.
        return {"rule_id": rules.create(payload, source=proposal["source"])}

    # Bump version when a release changes what apply does with a payload:
    # proposals stored under the old tag can then no longer be confirmed.
    proposals.register_kind("alert_rule", validate, apply, describe, version="1")

An agent tool or a routine then calls::

    proposal = proposals.propose("alert_rule", {...}, source="agent:chat")
    # -> {"id", "kind", "status": "pending", "source", "summary", "payload",
    #     "created_at", "expires_at", "resolved_at", "result", "error", "attempts"}

and tells the user to review it; it must never claim the change was made.

What ``confirm(id)`` guarantees:

* ``apply`` receives exactly the payload that was stored and shown.
* ``validate`` runs again first, because the world may have changed since the
  proposal was made. If it refuses, the proposal stays pending with ``error``.
* ``apply`` runs inside a ``desk_store.transaction()`` together with the status
  change. Writes it makes through ``desk_store`` (directly, or through
  functions that open their own ``desk_store.transaction()``) are committed
  with the status or not at all. Two confirms of the same proposal, from two
  clicks, threads or processes, apply once; the second returns the stored
  result.
* ``apply`` never runs while a confirm of the same proposal is in flight.
  Another thread or process waits for the desk store's write lock, which the
  first confirm holds from its status check to its commit, and then sees the
  result. A confirm of the same proposal started from inside ``apply`` (also
  from an asyncio task or a thread that carries a copy of its context) would
  wait for itself; it raises ``Conflict`` at once instead.
* If ``apply`` raises, its desk-store writes are undone, the proposal stays
  pending and the error is recorded on it.
* An ``apply`` that writes somewhere else (a file, another database, a network
  service) is outside that transaction; see "External effects" below. It
  should be quick: the desk store is locked meanwhile.

External effects
----------------
No write outside ``desk.sqlite3`` can commit together with the status, so for
those there is no exactly-once. The process can die after the write and
before the status commit, and ``apply`` can raise after the write; in both
cases the proposal stays pending and the next confirm runs ``apply`` again.
What this module offers is **at-least-once execution of an idempotent write**,
which gives the effect once. Wrap each such write in ``external_effect``::

    def apply(payload, proposal):
        rule_id = rules.create(payload)              # desk-store writes first
        proposals.external_effect(proposal, "lesson_file",
                                  lambda: lessons.append_once(proposal["id"], payload["text"]))
        return {"rule_id": rule_id}

It follows one pattern, check -> write -> record:

1. check: if an earlier attempt of this proposal recorded the effect ``name``,
   its stored result is returned and the write is not called again;
2. write: otherwise the write runs. It **must be idempotent, keyed by the
   proposal id**: a crash between the write and step 3 runs it again, so it
   must recognise its own earlier output (for example a line or file named
   after ``proposal["id"]``, or the id as the remote request's idempotency
   key) and not repeat it;
3. record: ``(proposal id, name, result)`` is committed at once in the
   ``effects`` table of ``desk-effects.sqlite3`` (a separate private file,
   because the desk store is locked by the confirm in progress).

Put external writes last, after everything that can still refuse: an effect
is never undone. If ``apply`` fails after it, or the user rejects the proposal
instead of confirming it again, the external write remains.

Retention
---------
Every ``propose``, ``confirm`` and ``reject`` also deletes up to
``PRUNE_BATCH`` proposals that were applied, rejected or expired more than
``RETENTION_DAYS`` days ago, with their recorded effects. A proposal that can
still be confirmed is never deleted, and neither is an applied or rejected
proposal of a kind in ``KEEP_DECIDED_KINDS`` (the assistant's memory
proposals): it is how the person's decision is remembered, so that the same
lesson or profile change is not proposed to them again (``core/lessons.py``).
``forget(id)`` deletes one such rejected proposal when the person takes their
"no" back (设置 › 交易偏好).

Demo mode
---------
With ``TRACKER_DEMO_MODE=1`` nothing here writes: ``propose``, ``confirm``,
``reject`` and ``forget`` raise ``ReadOnlyDemo`` (a ``ProposalError`` with a fixed
Chinese text) before touching the store, whoever calls them (an agent tool,
the assistant's extraction, a routine), so the demo's data does not depend on
the HTTP middleware that refuses write requests.

Messages of ``ProposalError`` are shown to the user and are written in Chinese.
Log lines carry ids, kinds and exception types only, never payloads.
"""
from __future__ import annotations

from contextvars import ContextVar
import copy
from datetime import datetime, timedelta, timezone
import inspect
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from core import desk_store

logger = logging.getLogger(__name__)

STATUSES = ("pending", "applied", "rejected", "expired")
DEFAULT_TTL_SECONDS = 7 * 24 * 3600
MAX_TTL_SECONDS = 366 * 24 * 3600
MAX_PENDING = 200
MAX_PAYLOAD = 100000
MAX_SUMMARY = 500
MAX_LIST = 200
RETENTION_DAYS = 90
PRUNE_BATCH = 200
# Kinds whose applied and rejected proposals are kept past RETENTION_DAYS: the person's decision about them
# is remembered (core/lessons.py: a lesson or profile change already decided is not proposed again).
KEEP_DECIDED_KINDS = frozenset({"lesson", "profile"})
DEMO_REFUSAL = "演示模式只读：助手的方案不会保存，也不能确认或放弃。"

MIGRATIONS = [[
    """CREATE TABLE proposals (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT UNIQUE NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        summary TEXT NOT NULL,
        source TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        expires_ts REAL NOT NULL,
        resolved_at TEXT,
        result TEXT,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX proposals_by_status ON proposals(status, seq)",
], [
    # The version tag of the kind registration that stored the proposal.
    # Proposals stored before the tag existed were made by version "1".
    "ALTER TABLE proposals ADD COLUMN kind_version TEXT NOT NULL DEFAULT '1'",
], [
    # For pruning old resolved and long expired proposals.
    "CREATE INDEX proposals_resolved ON proposals(resolved_at, seq) WHERE status != 'pending'",
    "CREATE INDEX proposals_pending_expiry ON proposals(expires_ts, seq) WHERE status = 'pending'",
]]
SCHEMA = "proposals"
desk_store.register_schema(SCHEMA, MIGRATIONS)


class ProposalError(ValueError):
    """Base class. The message is user-facing Chinese text."""


class UnknownKind(ProposalError):
    pass


class InvalidProposal(ProposalError):
    """The payload did not pass the kind's ``validate``."""


class NotFound(ProposalError):
    pass


class Conflict(ProposalError):
    """The proposal is in a state that does not allow the request."""


class ReadOnlyDemo(ProposalError):
    """Demo mode: no proposal is stored, confirmed or rejected."""


def refuse_in_demo() -> None:
    """Raise ``ReadOnlyDemo`` in demo mode (``TRACKER_DEMO_MODE=1``)."""
    if os.environ.get("TRACKER_DEMO_MODE") == "1":
        raise ReadOnlyDemo(DEMO_REFUSAL)


class ApplyFailed(ProposalError):
    """``apply`` raised; the proposal is still pending.

    ``expected`` is True when ``apply`` refused with a ``ValueError`` (a
    business rule), False when it failed in a way nobody planned for.
    """

    def __init__(self, message: str, expected: bool):
        super().__init__(message)
        self.expected = expected


class _Kind(NamedTuple):
    validate: Callable[[dict], Optional[dict]]
    apply: Callable[[dict, dict], Any]
    describe: Callable[[dict], str]
    version: str


_kinds: Dict[str, _Kind] = {}


class _InFlight:
    """One running ``confirm``; ``active`` turns false when it returns."""

    __slots__ = ("proposal_id", "active")

    def __init__(self, proposal_id):
        self.proposal_id = proposal_id
        self.active = True


# The confirms running in this context. Tasks and copied contexts inherit the
# tuple, which is how a confirm started from inside ``apply`` is recognised.
_confirming: ContextVar[Tuple[_InFlight, ...]] = ContextVar("proposals_confirming", default=())


def _now() -> float:
    return time.time()


# -- registration -------------------------------------------------------------

def register_kind(kind: str, validate: Callable[[dict], Optional[dict]],
                  apply: Callable[[dict, dict], Any], describe: Callable[[dict], str],
                  version: str = "1") -> None:
    """Teach the module one kind of proposal. See the module docstring.

    ``version`` is a short tag (letters, digits and ``._-``, at most 32
    characters) stored with every proposal of this kind. Change it whenever a
    release changes what ``apply`` does with a payload: ``confirm`` refuses a
    proposal that was stored under another tag, because the user approved a
    summary written for the old behaviour.

    Registering the same functions and tag again changes nothing. Registering
    other functions or another tag for a kind that is already registered
    raises ``ValueError``: a kind has exactly one implementation per process.
    """
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind):
        raise ValueError("proposal kind must be a short lowercase name")
    for function in (validate, apply, describe):
        if not callable(function) or inspect.iscoroutinefunction(function):
            raise TypeError("validate, apply and describe must be plain (not async) callables")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", version):
        raise ValueError("proposal kind version must be a short tag such as '1' or '2026.11'")
    entry = _Kind(validate, apply, describe, version)
    known = _kinds.get(kind)
    if known is not None and known != entry:
        raise ValueError(f"proposal kind '{kind}' is already registered with other functions or another version")
    _kinds[kind] = entry


def kinds() -> List[str]:
    return sorted(_kinds)


# -- proposing ----------------------------------------------------------------

def propose(kind: str, payload: dict, source: str, ttl_seconds: Optional[float] = None) -> dict:
    """Validate and store a proposal; nothing is applied. Returns the proposal.

    source       who is asking, e.g. ``agent:chat`` or ``routine:postmarket``
                 (lowercase letters, digits and ``_.:-``). Shown to the user.
    ttl_seconds  how long the proposal can still be confirmed
                 (default ``DEFAULT_TTL_SECONDS``).

    Proposing the same kind and payload again while the first is still pending
    returns the first one instead of storing a copy.

    Raises ``UnknownKind``, ``InvalidProposal``, ``ReadOnlyDemo`` or
    ``ProposalError`` (too many pending proposals).
    """
    refuse_in_demo()
    entry = _kind(kind)
    if not isinstance(source, str) or not re.fullmatch(r"[a-z][a-z0-9_.:\-]{0,63}", source):
        raise ValueError("proposal source must be a short lowercase name such as 'agent:chat'")
    ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not 0 < ttl <= MAX_TTL_SECONDS:
        raise ValueError("ttl_seconds must be a positive number of at most MAX_TTL_SECONDS")
    payload, encoded = _validated(kind, entry, payload)
    try:
        summary = entry.describe(copy.deepcopy(payload))
    except Exception as exc:
        logger.warning("proposal kind=%s describe failed: %s", kind, type(exc).__name__)
        raise InvalidProposal("无法生成这项方案的说明，方案未保存。") from None
    if not isinstance(summary, str) or not summary.strip():
        raise InvalidProposal("无法生成这项方案的说明，方案未保存。")
    summary = summary.strip()
    if len(summary) > MAX_SUMMARY:
        summary = summary[:MAX_SUMMARY - 1] + "…"

    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        _prune(db)
        now = _now()
        same = db.execute("SELECT * FROM proposals WHERE status='pending' AND expires_ts>? AND kind=? "
                          "AND kind_version=? AND payload=? ORDER BY seq LIMIT 1",
                          (now, kind, entry.version, encoded)).fetchone()
        if same is not None:
            return _decode(same, now)
        pending = db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending' AND expires_ts>?",
                             (now,)).fetchone()[0]
        if pending >= MAX_PENDING:
            raise ProposalError(f"待确认的方案已有 {pending} 条。请先在页面上确认或放弃一部分，再提出新的方案。")
        identifier = uuid.uuid4().hex
        db.execute("INSERT INTO proposals(id, kind, kind_version, payload, summary, source, created_at, expires_ts) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (identifier, kind, entry.version, encoded, summary, source, desk_store.now(), now + ttl))
        return _decode(_row(db, identifier), now)


# -- deciding -----------------------------------------------------------------

def confirm(proposal_id: str) -> dict:
    """Apply a pending proposal, once. Returns the proposal with its ``result``.

    Confirming an already applied proposal returns it again without applying.

    Raises ``NotFound``; ``Conflict`` (rejected or expired; stored under
    another version tag of its kind, which also records the message in
    ``error``; or called from inside the ``apply`` of this same proposal);
    ``UnknownKind`` (the feature that owns this kind is not loaded);
    ``InvalidProposal`` (it no longer passes validation); ``ApplyFailed``. In
    the last two cases the proposal stays pending and carries the message in
    ``error``.
    """
    refuse_in_demo()
    if any(flight.active and flight.proposal_id == proposal_id for flight in _confirming.get()):
        raise Conflict("这项方案正在应用中，不能在应用过程中再次确认。")
    flight = _InFlight(proposal_id)
    token = _confirming.set(_confirming.get() + (flight,))
    try:
        return _confirm(proposal_id)
    finally:
        flight.active = False
        _confirming.reset(token)


def _confirm(proposal_id: str) -> dict:
    _require_store()
    failure: Optional[ProposalError] = None
    with desk_store.transaction() as db:
        _prune(db, keep=proposal_id)
        now = _now()
        row = _row(db, proposal_id)
        proposal = _decode(row, now)
        if proposal["status"] == "applied":
            return proposal
        if proposal["status"] == "rejected":
            raise Conflict("这项方案已被放弃，不能再确认。如仍需要，请重新提出。")
        if proposal["status"] == "expired":
            db.execute("UPDATE proposals SET status='expired', resolved_at=? WHERE id=? AND status='pending'",
                       (desk_store.now(), proposal_id))
            failure = Conflict("这项方案已过期，未做任何修改。如仍需要，请重新提出。")
        elif _kind(proposal["kind"]).version != row["kind_version"]:
            failure = Conflict("这项方案是由该功能的旧版本生成的，现在的版本可能会做不同的修改，因此不能确认。"
                               "请放弃这项方案，需要时重新提出。")
            db.execute("UPDATE proposals SET error=? WHERE id=?", (str(failure), proposal_id))
        else:
            entry = _kind(proposal["kind"])
            try:
                _validated(proposal["kind"], entry, proposal["payload"])
                try:
                    with desk_store.transaction():
                        result = entry.apply(copy.deepcopy(proposal["payload"]), copy.deepcopy(proposal))
                        try:
                            encoded = desk_store.encode(result)
                        except (TypeError, ValueError):
                            raise RuntimeError("apply must return a JSON value or None") from None
                except ValueError as exc:  # includes ProposalError: the kind refused on purpose
                    raise ApplyFailed(str(exc) or "这项方案未能应用。", True) from None
                except Exception as exc:
                    logger.warning("proposal %s kind=%s apply failed: %s",
                                   proposal_id, proposal["kind"], type(exc).__name__)
                    raise ApplyFailed("应用这项方案时出错，未做任何修改。方案仍在待确认列表中。", False) from None
            except (InvalidProposal, ApplyFailed) as exc:
                failure = exc
                db.execute("UPDATE proposals SET error=?, attempts=attempts+1 WHERE id=?", (str(exc), proposal_id))
            else:
                db.execute("UPDATE proposals SET status='applied', result=?, error=NULL, resolved_at=? WHERE id=?",
                           (encoded, desk_store.now(), proposal_id))
        proposal = _decode(_row(db, proposal_id), now)
    if failure is not None:
        raise failure
    return proposal


def reject(proposal_id: str) -> dict:
    """Discard a pending proposal. Returns the proposal.

    Rejecting twice, or rejecting an expired proposal, changes nothing. An
    applied proposal cannot be rejected (``Conflict``).
    """
    refuse_in_demo()
    _require_store()
    with desk_store.transaction() as db:
        _prune(db, keep=proposal_id)
        now = _now()
        proposal = _decode(_row(db, proposal_id), now)
        if proposal["status"] == "applied":
            raise Conflict("这项方案已经应用，不能放弃。请在对应页面直接修改。")
        if proposal["status"] == "pending":
            db.execute("UPDATE proposals SET status='rejected', resolved_at=? WHERE id=?",
                       (desk_store.now(), proposal_id))
        return _decode(_row(db, proposal_id), now)


FORGET_ONLY_REJECTED = "只能删除已放弃的方案：待确认的请先确认或放弃，已确认的不能删除。"
FORGET_ONLY_MEMORY = "只有已放弃的教训和偏好方案需要删除，其他方案会按期自动清理。"


def forget(proposal_id: str) -> dict:
    """Delete a rejected proposal of a kind in ``KEEP_DECIDED_KINDS``. Returns it as it was.

    Those are kept indefinitely so that the same lesson or profile change is
    not proposed again (``core/lessons.py``); deleting one takes the person's
    "no" back, and the assistant may propose that text again. Only rejected
    ones: a pending proposal is decided with ``confirm`` or ``reject``, and an
    applied one records what was written. Any other kind is pruned by
    retention anyway (``Conflict`` for both).
    """
    refuse_in_demo()
    _require_store()
    with desk_store.transaction() as db:
        proposal = _decode(_row(db, proposal_id), _now())
        if proposal["kind"] not in KEEP_DECIDED_KINDS:
            raise Conflict(FORGET_ONLY_MEMORY)
        if proposal["status"] != "rejected":
            raise Conflict(FORGET_ONLY_REJECTED)
        db.execute("DELETE FROM proposals WHERE id=? AND status='rejected'", (proposal_id,))
        desk_store.after_commit(lambda: _forget_effects([proposal_id]))
        return proposal


# -- external effects ---------------------------------------------------------

EFFECTS_FILE = "desk-effects.sqlite3"
_EFFECTS_TABLE = ("CREATE TABLE IF NOT EXISTS effects (proposal_id TEXT NOT NULL, name TEXT NOT NULL, "
                  "result TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY (proposal_id, name))")


def external_effect(proposal: dict, name: str, write: Callable[[], Any]) -> Any:
    """Run one write outside ``desk.sqlite3`` for the proposal being applied.

    Check, write, record; see "External effects" in the module docstring.
    ``write()`` takes no arguments, must be idempotent keyed by
    ``proposal["id"]`` and returns a JSON value (or None), which is recorded
    and returned. If the effect ``name`` was recorded by an earlier attempt of
    the same proposal, that result is returned and ``write`` is not called.

    Only for the ``apply`` of a proposal that ``confirm`` is applying right
    now; anywhere else it raises ``RuntimeError``.
    """
    proposal_id = proposal.get("id") if isinstance(proposal, dict) else None
    if not any(flight.active and flight.proposal_id == proposal_id for flight in _confirming.get()):
        raise RuntimeError("external_effect() runs only inside the apply of a proposal being confirmed")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_.:\-]{0,63}", name):
        raise ValueError("effect name must be a short lowercase name")
    key = (proposal_id, name)
    with desk_store.side_database(EFFECTS_FILE) as db:
        db.execute(_EFFECTS_TABLE)
        row = db.execute("SELECT result FROM effects WHERE proposal_id=? AND name=?", key).fetchone()
    if row is not None:
        return json.loads(row["result"])
    result = write()
    try:
        encoded = desk_store.encode(result)
    except (TypeError, ValueError):
        raise RuntimeError("an external effect must return a JSON value or None") from None
    with desk_store.side_database(EFFECTS_FILE) as db:
        db.execute("INSERT OR IGNORE INTO effects(proposal_id, name, result, recorded_at) VALUES (?,?,?,?)",
                   (*key, encoded, desk_store.now()))
    return json.loads(encoded)


# -- reading ------------------------------------------------------------------

def get(proposal_id: str) -> dict:
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        raise _not_found()
    with desk_store.connect() as db:
        return _decode(_row(db, proposal_id), _now())


def list_proposals(status: Optional[str] = "pending", limit: int = 100,
                   kinds: Optional[Sequence[str]] = None) -> List[dict]:
    """Proposals with the given status (one of ``STATUSES``; ``None`` or ``"all"`` for every one), newest first.

    ``kinds`` keeps only proposals of those kinds (none when it is empty); ``None`` keeps every kind.
    """
    if status in (None, "all"):
        where, parameters = "1", ()
    elif status == "pending":
        where, parameters = "status='pending' AND expires_ts>?", (_now(),)
    elif status == "expired":
        where, parameters = "(status='expired' OR (status='pending' AND expires_ts<=?))", (_now(),)
    elif status in STATUSES:
        where, parameters = "status=?", (status,)
    else:
        raise ProposalError("未知的方案状态。可用：pending、applied、rejected、expired、all。")
    if kinds is not None:
        wanted = sorted({kind for kind in kinds if isinstance(kind, str)})
        if not wanted:
            return []
        where += f" AND kind IN ({', '.join('?' for _ in wanted)})"
        parameters = (*parameters, *wanted)
    limit = max(1, min(int(limit), MAX_LIST))
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return []
    with desk_store.connect() as db:
        now = _now()
        return [_decode(row, now) for row in db.execute(
            f"SELECT * FROM proposals WHERE {where} ORDER BY seq DESC LIMIT ?", (*parameters, limit))]


def of_kind(kind: str, status: str) -> List[dict]:
    """Every proposal of ``kind`` with ``status`` (as ``list_proposals`` reads it), oldest first, without a limit.

    For a kind's own duplicate checks (``core/lessons.py``). Never creates the store.
    """
    if status == "pending":
        where, parameters = "status='pending' AND expires_ts>?", (_now(),)
    elif status == "expired":
        where, parameters = "(status='expired' OR (status='pending' AND expires_ts<=?))", (_now(),)
    elif status in STATUSES:
        where, parameters = "status=?", (status,)
    else:
        raise ProposalError("未知的方案状态。可用：pending、applied、rejected、expired。")
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return []
    with desk_store.connect() as db:
        now = _now()
        return [_decode(row, now) for row in db.execute(
            f"SELECT * FROM proposals WHERE kind=? AND {where} ORDER BY seq", (kind, *parameters))]


def pending_count() -> int:
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return 0
    with desk_store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending' AND expires_ts>?",
                          (_now(),)).fetchone()[0]


# -- helpers ------------------------------------------------------------------

def _prune(db, keep: Optional[str] = None) -> None:
    """Delete a bounded batch of proposals resolved or expired more than ``RETENTION_DAYS`` ago."""
    resolved_before = (datetime.fromisoformat(desk_store.now())
                       - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    expired_before = _now() - RETENTION_DAYS * 24 * 3600
    kept = sorted(KEEP_DECIDED_KINDS)
    ids = [row[0] for row in db.execute(
        "SELECT id FROM proposals WHERE status != 'pending' AND resolved_at < ? "
        f"AND NOT (status IN ('applied', 'rejected') AND kind IN ({', '.join('?' for _ in kept)})) "
        "ORDER BY resolved_at, seq LIMIT ?",
        (resolved_before, *kept, PRUNE_BATCH))]
    if len(ids) < PRUNE_BATCH:  # expired by time, never marked as expired
        ids += [row[0] for row in db.execute(
            "SELECT id FROM proposals WHERE status = 'pending' AND expires_ts < ? ORDER BY expires_ts, seq LIMIT ?",
            (expired_before, PRUNE_BATCH - len(ids)))]
    ids = [identifier for identifier in ids if identifier != keep]
    if ids:
        db.executemany("DELETE FROM proposals WHERE id=?", [(identifier,) for identifier in ids])
        desk_store.after_commit(lambda: _forget_effects(ids))


def _forget_effects(ids: List[str]) -> None:
    with desk_store.side_database(EFFECTS_FILE, create=False) as db:
        if db is not None:
            db.execute(_EFFECTS_TABLE)
            db.executemany("DELETE FROM effects WHERE proposal_id=?", [(identifier,) for identifier in ids])


def _require_store() -> None:
    """Upgrade the schema before a change to an existing proposal; ``NotFound`` when none was ever stored."""
    if desk_store.schema_version(SCHEMA) == 0:
        raise _not_found()  # without creating the store
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)


def _kind(kind) -> _Kind:
    entry = _kinds.get(kind) if isinstance(kind, str) else None
    if entry is None:
        raise UnknownKind("未知的方案类型，系统无法处理这项方案。")
    return entry


def _validated(kind, entry, payload):
    """Run the kind's ``validate``; return ``(payload, canonical JSON)``."""
    if not isinstance(payload, dict):
        raise InvalidProposal("方案内容必须是一个对象。")
    try:
        result = entry.validate(copy.deepcopy(payload))
    except ValueError as exc:  # includes ProposalError
        raise InvalidProposal(str(exc) or "方案内容未通过校验。") from None
    except Exception as exc:
        logger.warning("proposal kind=%s validate failed: %s", kind, type(exc).__name__)
        raise InvalidProposal("方案内容未通过校验。") from None
    if result is None:
        result = payload
    if not isinstance(result, dict):
        raise InvalidProposal("方案内容未通过校验。")
    try:
        encoded = desk_store.encode(result)
    except (TypeError, ValueError):
        raise InvalidProposal("方案内容包含无法保存的数据。") from None
    if len(encoded) > MAX_PAYLOAD:
        raise InvalidProposal("方案内容过大。")
    return json.loads(encoded), encoded


def _row(db, proposal_id):
    row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone() \
        if isinstance(proposal_id, str) else None
    if row is None:
        raise _not_found()
    return row


def _not_found() -> NotFound:
    return NotFound("方案不存在或已被清除。请刷新待确认列表。")


def _decode(row, now: float) -> dict:
    status = row["status"]
    if status == "pending" and row["expires_ts"] <= now:
        status = "expired"
    return {
        "id": row["id"], "kind": row["kind"], "status": status, "source": row["source"],
        "summary": row["summary"], "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
        "expires_at": datetime.fromtimestamp(row["expires_ts"], timezone.utc).isoformat(timespec="seconds"),
        "resolved_at": row["resolved_at"],
        "result": json.loads(row["result"]) if row["result"] is not None else None,
        "error": row["error"], "attempts": row["attempts"],
    }


# The public name asked for by callers is ``proposals.list``. It is bound last,
# and nothing in this module may use the built-in of the same name.
list = list_proposals
