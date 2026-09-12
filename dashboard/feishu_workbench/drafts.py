"""Private, per-user daily drafts with revision checks and recoverable commits."""
import json

from .store import Rejected, encode


class Drafts:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS daily_drafts (
                    actor TEXT PRIMARY KEY, date TEXT NOT NULL, items TEXT NOT NULL DEFAULT '[]',
                    revision INTEGER NOT NULL DEFAULT 0, locked_by TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS daily_edits (
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS daily_commits (
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, revision INTEGER NOT NULL,
                    preview TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
            ''')

    @staticmethod
    def unpack(row):
        result = dict(row)
        result['items'] = json.loads(result['items'])
        return result

    def get(self, actor, day):
        with self.store.db() as db:
            db.execute('INSERT OR IGNORE INTO daily_drafts(actor,date) VALUES (?,?)', (actor, day))
            return self.unpack(db.execute('SELECT * FROM daily_drafts WHERE actor=?', (actor,)).fetchone())

    def edited(self, actor, key):
        with self.store.db() as db:
            row = db.execute('SELECT result FROM daily_edits WHERE id=? AND actor=?', (key, actor)).fetchone()
            return json.loads(row['result']) if row else None

    def save(self, actor, revision, key, day, items):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT result FROM daily_edits WHERE id=? AND actor=?', (key, actor)).fetchone()
            if existing:
                return json.loads(existing['result'])
            row = db.execute('SELECT * FROM daily_drafts WHERE actor=?', (actor,)).fetchone()
            if not row or row['revision'] != revision or row['locked_by']:
                raise Rejected('清单已在其他卡片中更新，请重新打开变动清单。')
            db.execute('UPDATE daily_drafts SET date=?,items=?,revision=revision+1 WHERE actor=?',
                       (day, encode(items), actor))
            result = self.unpack(db.execute('SELECT * FROM daily_drafts WHERE actor=?', (actor,)).fetchone())
            db.execute('INSERT INTO daily_edits VALUES (?,?,?)', (key, actor, encode(result)))
            return result

    def commit(self, actor, key):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM daily_commits WHERE id=? AND actor=?', (key, actor)).fetchone()
            return {**dict(row), 'preview': json.loads(row['preview'])} if row else None

    def begin(self, actor, revision, key, preview):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM daily_commits WHERE id=? AND actor=?', (key, actor)).fetchone()
            if previous:
                return {**dict(previous), 'preview': json.loads(previous['preview'])}
            row = db.execute('SELECT * FROM daily_drafts WHERE actor=?', (actor,)).fetchone()
            if not row or row['revision'] != revision or row['locked_by']:
                raise Rejected('变动清单已改变，请重新核对后确认。')
            db.execute('UPDATE daily_drafts SET locked_by=? WHERE actor=?', (key, actor))
            db.execute('INSERT INTO daily_commits(id,actor,revision,preview) VALUES (?,?,?,?)',
                       (key, actor, revision, encode(preview)))
            return {'id': key, 'actor': actor, 'revision': revision, 'preview': preview, 'status': 'pending'}

    def complete(self, actor, key):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE daily_drafts SET items='[]',revision=revision+1,locked_by='' WHERE actor=? AND locked_by=?",
                       (actor, key))
            db.execute("UPDATE daily_commits SET status='complete' WHERE actor=? AND id=?", (actor, key))

    def release_unwritten(self, actor, key):
        """Only called after the financial adapter confirms no write was prepared."""
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE daily_drafts SET locked_by='' WHERE actor=? AND locked_by=?", (actor, key))
            db.execute("UPDATE daily_commits SET status='rejected' WHERE actor=? AND id=?", (actor, key))
