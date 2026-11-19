"""Durable card sessions and commands; callbacks never execute financial writes."""
from contextlib import contextmanager
from itertools import islice
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid


class Rejected(ValueError):
    pass


# How long an expired card, with its commands and deliveries, is still kept.
RETENTION = 30 * 86400
# A research card in one of these states will never change again.
FINISHED_RUNS = frozenset({'completed', 'failed', 'cancelled'})
# The daily reports that the reports card lists and can retry.
_RECENT_REPORTS = ("SELECT c.* FROM commands c JOIN sessions s ON c.session=s.id "
                   "WHERE c.action='daily.report' AND s.actor=? ORDER BY c.created DESC LIMIT 5")


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, chat TEXT NOT NULL,
                    message TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    view TEXT NOT NULL DEFAULT 'home', model TEXT NOT NULL DEFAULT '{}',
                    nonces TEXT NOT NULL DEFAULT '{}', expires REAL NOT NULL DEFAULT 0,
                    busy INTEGER NOT NULL DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS session_message ON sessions(message) WHERE message != '';
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, action TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, prepared TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending');
                CREATE INDEX IF NOT EXISTS command_session ON commands(session);
                CREATE INDEX IF NOT EXISTS delivery_session ON deliveries(session);
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def unpack(row):
        if row is None:
            raise Rejected('卡片不存在，请发送“工作台”重新打开。')
        result = dict(row)
        for key in ('model', 'nonces', 'payload'):
            if key in result:
                result[key] = json.loads(result[key])
        return result

    def session(self, identifier):
        with self.db() as db:
            return self.unpack(db.execute('SELECT * FROM sessions WHERE id=?', (identifier,)).fetchone())

    def open(self, actor, chat='', action='home', request_id=None):
        identifier = uuid.uuid4().hex
        key = request_id or uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT session FROM commands WHERE id=?', (key,)).fetchone()
            if existing:
                return existing['session']
            db.execute('INSERT INTO sessions(id,actor,chat,message,busy) VALUES (?,?,?,?,1)',
                       (identifier, actor, chat, ''))
            db.execute('INSERT INTO commands(id,session,action,payload,created) VALUES (?,?,?,?,?)',
                       (key, identifier, action, '{}', time.time()))
        return identifier

    def accept(self, actor, chat, message, nonce, fields):
        """Claim one current action atomically with its durable command."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            session = self.unpack(db.execute('SELECT * FROM sessions WHERE message=?', (message,)).fetchone())
            if session['actor'] != actor or session['chat'] != chat:
                raise Rejected('这张卡片不属于当前会话。')
            # The nonce is an idempotency key as well as a session-bound capability.
            if db.execute('SELECT 1 FROM commands WHERE id=?', (nonce,)).fetchone():
                return False
            if session['busy']:
                raise Rejected('上一项操作正在处理，请稍候。')
            action = next((action for action, value in session['nonces'].items() if value == nonce), None)
            if not action or session['expires'] < time.time():
                raise Rejected('卡片已更新或过期，请发送“工作台”重新打开。')
            db.execute('INSERT INTO commands(id,session,action,payload,created) VALUES (?,?,?,?,?)',
                       (nonce, session['id'], action,
                        encode({'fields': fields, 'view': session['view'], 'model': session['model']}), time.time()))
            db.execute('UPDATE sessions SET busy=1 WHERE id=?', (session['id'],))
        return True

    def prepare_render(self, key, identifier, view, model, actions, expected_revision=None):
        """Persist exact card content before contacting Feishu; retries reuse it."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if expected_revision is not None:
                current = db.execute('SELECT revision,busy FROM sessions WHERE id=?', (identifier,)).fetchone()
                if current is None or current['busy'] or current['revision'] != expected_revision:
                    return None
            previous = db.execute('SELECT * FROM deliveries WHERE id=?', (key,)).fetchone()
            if previous:
                return json.loads(previous['prepared'])
            session = self.unpack(db.execute('SELECT * FROM sessions WHERE id=?', (identifier,)).fetchone())
            if expected_revision is not None and (session['busy'] or session['revision'] != expected_revision):
                return None
            lifetime = 900 if view.endswith('_preview') else 7 * 86400
            prepared = {**session, 'view': view, 'model': model, 'revision': session['revision'] + 1,
                        'nonces': {action: secrets.token_hex(16) for action in actions},
                        'expires': time.time() + lifetime}
            db.execute('INSERT INTO deliveries(id,session,prepared) VALUES (?,?,?)',
                       (key, identifier, encode(prepared)))
            return prepared

    def delivered(self, key, message, chat):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM deliveries WHERE id=?', (key,)).fetchone()
            if row['status'] == 'delivered':
                return
            prepared = json.loads(row['prepared'])
            db.execute('UPDATE sessions SET revision=?,view=?,model=?,nonces=?,expires=?,message=?,chat=? WHERE id=? AND revision<?',
                       (prepared['revision'], prepared['view'], encode(prepared['model']), encode(prepared['nonces']),
                        prepared['expires'], message, chat, prepared['id'], prepared['revision']))
            db.execute("UPDATE deliveries SET status='delivered' WHERE id=?", (key,))

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE commands SET status='queued' WHERE status='running'")

    def claim(self, reports=False):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM commands WHERE status='queued' AND (action='daily.report')=? ORDER BY created LIMIT 1", (int(reports),)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE commands SET status='running',attempts=attempts+1 WHERE id=?", (row['id'],))
            return self.unpack(row)

    def finish(self, command, error=''):
        with self.db() as db:
            db.execute('UPDATE commands SET status=?,error=? WHERE id=?',
                       ('failed' if error else 'completed', error, command['id']))
            if command['action'] != 'daily.report':
                db.execute('UPDATE sessions SET busy=0 WHERE id=?', (command['session'],))

    def observed_sessions(self, limit=50):
        """Valid cards of unfinished research, most recently updated first.

        A failed or cancelled run keeps its status card; following those as
        well would crowd out the runs that can still change.
        """
        with self.db() as db:
            rows = db.execute("SELECT * FROM sessions WHERE view='research_status' AND busy=0 AND message!='' "
                              "AND expires>=? ORDER BY expires DESC, rowid DESC", (time.time(),))
            sessions = (self.unpack(row) for row in rows)
            return list(islice((s for s in sessions if s['model'].get('status') not in FINISHED_RUNS), limit))

    def prune(self, now=None, limit=200):
        """Forget up to ``limit`` cards that can no longer act; return how many.

        Commands are idempotency keys and deliveries are retry state, so both
        go only together with their session. A session stays while its card is
        valid or expired less than RETENTION ago, while it is busy or has a
        queued, running or recent command, while it holds one of its user's
        listed daily reports or one that a valid card offers to retry, and while it is the
        latest card of its chat (entering that chat must not send another).
        """
        cutoff = (time.time() if now is None else now) - RETENTION
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            listed = set()
            for actor in [row['actor'] for row in db.execute('SELECT DISTINCT actor FROM sessions')]:
                listed.update(row['session'] for row in db.execute(_RECENT_REPORTS, (actor,)))
            # A valid card names the report its retry button acts on.
            for row in db.execute("SELECT model FROM sessions WHERE expires>=? AND model LIKE '%_report_command%'",
                                  (time.time() if now is None else now,)).fetchall():
                target = json.loads(row['model']).get('_report_command')
                if target:
                    listed.update(r['session'] for r in db.execute('SELECT session FROM commands WHERE id=?', (target,)))
            rows = db.execute("""
                SELECT id FROM sessions AS s WHERE busy=0 AND expires<?
                AND NOT EXISTS (SELECT 1 FROM commands WHERE session=s.id
                                AND (status IN ('queued','running') OR created>=?))
                AND rowid NOT IN (SELECT max(rowid) FROM sessions WHERE message!='' GROUP BY actor,chat)
                ORDER BY expires, rowid""", (cutoff, cutoff))
            expired = list(islice((row['id'] for row in rows if row['id'] not in listed), limit))
            for identifier in expired:
                db.execute('DELETE FROM deliveries WHERE session=?', (identifier,))
                db.execute('DELETE FROM commands WHERE session=?', (identifier,))
                db.execute('DELETE FROM sessions WHERE id=?', (identifier,))
            return len(expired)

    def recent_reports(self, actor):
        with self.db() as db:
            return [self.unpack(row) for row in db.execute(_RECENT_REPORTS, (actor,))]

    def retry_report(self, actor, identifier, finished=False):
        """Queue a failed report job again; with ``finished``, a completed one too (a report waiting for the closes)."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT c.* FROM commands c JOIN sessions s ON c.session=s.id WHERE c.id=? AND s.actor=? AND c.action='daily.report'", (identifier, actor)).fetchone()
            if row is None:
                raise Rejected('未找到这项日报任务。')
            if row['status'] == 'failed' or (finished and row['status'] == 'completed'):
                db.execute("UPDATE commands SET status='queued',error='',attempts=0 WHERE id=?", (identifier,))

    def requeue_report(self, identifier):
        """Queue a finished report job again (its report waited for a time that has come); one queued or
        running is left as it is. Returns 1 when it was queued."""
        with self.db() as db:
            return db.execute("UPDATE commands SET status='queued',error='',attempts=0 WHERE id=? "
                              "AND action='daily.report' AND status IN ('completed','failed')", (identifier,)).rowcount

    def retry(self, command):
        with self.db() as db:
            db.execute("UPDATE commands SET status='queued' WHERE id=?", (command['id'],))

    def enqueue_report(self, session, receipt, operation_id):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO commands(id,session,action,payload,created) VALUES (?,?,?,?,?)',
                       (operation_id + '_report', session, 'daily.report', encode({'receipt': receipt}), time.time()))

    def counts(self):
        with self.db() as db:
            return {r['status']: r['n'] for r in db.execute('SELECT status,count(*) AS n FROM commands GROUP BY status')}
