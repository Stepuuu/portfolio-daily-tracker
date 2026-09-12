"""Durable card sessions and commands; callbacks never execute financial writes."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid


class Rejected(ValueError):
    pass


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

    def observed_sessions(self):
        with self.db() as db:
            return [self.unpack(row) for row in db.execute("SELECT * FROM sessions WHERE view='research_status' AND busy=0 AND message!='' LIMIT 50")]

    def recent_reports(self, actor):
        with self.db() as db:
            return [self.unpack(row) for row in db.execute("SELECT c.* FROM commands c JOIN sessions s ON c.session=s.id WHERE c.action='daily.report' AND s.actor=? ORDER BY c.created DESC LIMIT 5", (actor,))]

    def retry_report(self, actor, identifier):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT c.* FROM commands c JOIN sessions s ON c.session=s.id WHERE c.id=? AND s.actor=? AND c.action='daily.report'", (identifier, actor)).fetchone()
            if row is None:
                raise Rejected('未找到这项日报任务。')
            if row['status'] == 'failed':
                db.execute("UPDATE commands SET status='queued',error='',attempts=0 WHERE id=?", (identifier,))

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
