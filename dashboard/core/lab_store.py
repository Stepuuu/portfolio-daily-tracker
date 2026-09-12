"""Durable research state. SQLite transactions are the authority for job ownership."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Conflict(ValueError):
    pass


class LostLease(RuntimeError):
    pass


class LabStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "research.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, metadata TEXT NOT NULL, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS connections (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    request TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    stage TEXT NOT NULL DEFAULT 'queued', checkpoint TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    owner TEXT, lease_until REAL, cancelled INTEGER NOT NULL DEFAULT 0,
                    error TEXT, result TEXT, attempt INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS runs_queue ON runs(status, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS run_events ON events(run_id, seq);
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, next_due REAL NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_dataset(self, metadata, records):
        content = encode(records)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        key = hashlib.sha256(encode({"content_hash": content_hash, "provenance": metadata}).encode()).hexdigest()
        identifier = "ds_" + key[:24]
        metadata = {**metadata, "id": identifier, "hash": key, "content_hash": content_hash, "created_at": now()}
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO datasets VALUES(?,?,?)", (identifier, encode(metadata), content))
        return self.dataset(identifier)[0]

    def dataset(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT * FROM datasets WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise KeyError("Dataset not found")
        metadata = json.loads(row["metadata"])
        if hashlib.sha256(row["content"].encode()).hexdigest() != metadata.get("content_hash", metadata["hash"]):
            raise ValueError("Dataset integrity check failed")
        if "content_hash" in metadata:
            provenance = {k: v for k, v in metadata.items() if k not in {"id", "hash", "content_hash", "created_at"}}
            identity = hashlib.sha256(encode({"content_hash": metadata["content_hash"], "provenance": provenance}).encode()).hexdigest()
            if identity != metadata["hash"] or identifier != "ds_" + identity[:24]:
                raise ValueError("Dataset provenance integrity check failed")
        return metadata, json.loads(row["content"])

    def datasets(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT metadata FROM datasets ORDER BY rowid DESC")]

    def save_connection(self, config):
        with self.connect() as db:
            db.execute("INSERT INTO connections VALUES(?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                       (config["id"], encode(config)))
        return config

    def connections(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT config FROM connections ORDER BY id")]

    def connection(self, identifier):
        for connection in self.connections():
            if connection["id"] == identifier:
                return connection
        raise KeyError("Model connection not found")

    @staticmethod
    def _event(db, identifier, kind, message):
        db.execute("INSERT INTO events(run_id,kind,message,created_at) VALUES(?,?,?,?)",
                   (identifier, kind, message, now()))

    def submit(self, request, key=None, *, db=None):
        if db is None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                return self.submit(request, key, db=db)
        key = key or uuid.uuid4().hex
        serialized = encode(request)
        existing = db.execute("SELECT id,request FROM runs WHERE request_key=?", (key,)).fetchone()
        if existing:
            if existing["request"] != serialized:
                raise Conflict("This request identifier was already used for different research")
            return existing["id"]
        identifier = "run_" + uuid.uuid4().hex
        db.execute("INSERT INTO runs(id,request_key,request,created_at,updated_at) VALUES(?,?,?,?,?)",
                   (identifier, key, serialized, now(), now()))
        self._event(db, identifier, "queued", "研究已加入队列")
        return identifier

    def claim(self, owner, lease_seconds=60):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT * FROM runs WHERE cancelled=0 AND
                (status='queued' OR (status='running' AND lease_until<?))
                ORDER BY created_at,rowid LIMIT 1""", (time.time(),)).fetchone()
            if not row:
                return None
            recovered = row["status"] == "running"
            db.execute("UPDATE runs SET owner=?,lease_until=?,status='running',attempt=attempt+1,updated_at=? WHERE id=?",
                       (owner, time.time() + lease_seconds, now(), row["id"]))
            self._event(db, row["id"], "recovered" if recovered else "started",
                        "从最近保存的阶段继续研究" if recovered else "开始研究")
        return self.get(row["id"])

    def heartbeat(self, identifier, owner, lease_seconds=60):
        with self.connect() as db:
            cursor = db.execute("UPDATE runs SET lease_until=? WHERE id=? AND owner=? AND status='running' AND cancelled=0",
                                (time.time() + lease_seconds, identifier, owner))
            return cursor.rowcount == 1

    def checkpoint(self, identifier, owner, stage, checkpoint, message):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE runs SET stage=?,checkpoint=?,updated_at=?
                WHERE id=? AND owner=? AND status='running' AND cancelled=0 AND lease_until>=?""",
                (stage, encode(checkpoint), now(), identifier, owner, time.time())).rowcount
            if not changed:
                raise LostLease("Research was cancelled or claimed by another worker")
            self._event(db, identifier, stage, message)

    def finish(self, identifier, owner, *, result=None, error=None):
        status = "failed" if error else "completed"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE runs SET status=?,stage=?,result=?,error=?,updated_at=?,owner=NULL,lease_until=NULL
                WHERE id=? AND owner=? AND status='running' AND cancelled=0 AND lease_until>=?""",
                (status, status, encode(result) if result is not None else None, error, now(), identifier, owner, time.time())).rowcount
            if not changed:
                raise LostLease("Research is no longer owned by this worker")
            self._event(db, identifier, status, error or "研究完成，结果和方法已保存")

    def cancel(self, identifier):
        self.get(identifier, events=False)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE runs SET status='cancelled',stage='cancelled',cancelled=1,updated_at=?
                WHERE id=? AND status IN ('queued','running')""", (now(), identifier)).rowcount
            if changed:
                self._event(db, identifier, "cancelled", "研究已取消")
        return self.get(identifier)

    def retry(self, identifier):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM runs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError("Research not found")
            if row["status"] not in {"failed", "cancelled"}:
                raise Conflict("Only failed or cancelled research can be retried")
            # A user retry gets a fresh time/call budget and a new immutable run identity.
            old = db.execute("SELECT request FROM runs WHERE id=?", (identifier,)).fetchone()
            request = json.loads(old[0])
            request["parent_run_id"] = identifier
            return self.submit(request, db=db)

    @staticmethod
    def _decode(row, detail=True):
        result = {key: row[key] for key in ("id", "status", "stage", "created_at", "updated_at", "error", "attempt")}
        result["request"] = json.loads(row["request"])
        if detail:
            result["checkpoint"] = json.loads(row["checkpoint"])
            result["result"] = json.loads(row["result"]) if row["result"] else None
        return result

    def get(self, identifier, events=True):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError("Research not found")
            result = self._decode(row)
            if events:
                result["events"] = [dict(r) for r in db.execute("SELECT seq,kind,message,created_at FROM events WHERE run_id=? ORDER BY seq", (identifier,))]
        return result

    def runs(self, limit=100):
        with self.connect() as db:
            return [self._decode(r, False) for r in db.execute("SELECT * FROM runs ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,))]

    def release(self, owner):
        with self.connect() as db:
            db.execute("UPDATE runs SET lease_until=0 WHERE owner=? AND status='running'", (owner,))

    def schedules(self):
        with self.connect() as db:
            return [{**json.loads(r["config"]), "id": r["id"], "enabled": bool(r["enabled"]),
                     "next_due": r["next_due"], "completed": r["completed"]}
                    for r in db.execute("SELECT * FROM schedules ORDER BY rowid DESC")]

    def schedule(self, config):
        identifier = config.get("id") or "sch_" + uuid.uuid4().hex
        with self.connect() as db:
            db.execute("""INSERT INTO schedules(id,config,next_due,enabled) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET config=excluded.config,enabled=excluded.enabled""",
                (identifier, encode(config), time.time() + config["interval_seconds"], int(config.get("enabled", False))))
        return next(s for s in self.schedules() if s["id"] == identifier)

    def enqueue_due(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_due<=?", (time.time(),)).fetchall():
                config = json.loads(row["config"])
                if row["completed"] >= config["max_runs"]:
                    db.execute("UPDATE schedules SET enabled=0 WHERE id=?", (row["id"],))
                    continue
                self.submit(config["request"], f"schedule:{row['id']}:{row['completed']}", db=db)
                count = row["completed"] + 1
                db.execute("UPDATE schedules SET next_due=?,completed=?,enabled=? WHERE id=?",
                           (time.time() + config["interval_seconds"], count, int(count < config["max_runs"]), row["id"]))
