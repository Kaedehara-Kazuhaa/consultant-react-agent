import json
import sqlite3
import time
from contextlib import contextmanager
from .trace import redact


class Sessions:
    """SQLite turn mutex shared by workers. Leases recover after bounded turns/crashes."""
    def __init__(self, path, ttl=None, lease=120):
        self.path, self.ttl, self.lease = path, ttl, lease
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS conversation_state (
                owner TEXT, session TEXT, updated REAL, payload TEXT,
                PRIMARY KEY(owner,session));
              CREATE TABLE IF NOT EXISTS conversation_locks (
                owner TEXT, session TEXT, request TEXT, expires REAL,
                PRIMARY KEY(owner,session));
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def turn(self, owner, session, request):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM conversation_locks WHERE expires < ?', (now,))
            try:
                db.execute('INSERT INTO conversation_locks VALUES (?,?,?,?)', (owner,session,request,now+self.lease))
            except sqlite3.IntegrityError as exc:
                raise RuntimeError('Session already processing; retry after current turn finishes') from exc
        try:
            yield self.load(owner,session)
        finally:
            with self.connect() as db:
                db.execute('DELETE FROM conversation_locks WHERE owner=? AND session=? AND request=?', (owner,session,request))

    def load(self, owner, session):
        with self.connect() as db:
            row=db.execute('SELECT updated,payload FROM conversation_state WHERE owner=? AND session=?',(owner,session)).fetchone()
        return json.loads(row[1]) if row and (self.ttl is None or time.time()-row[0] < self.ttl) else {}

    def save(self, owner, session, payload):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO conversation_state VALUES (?,?,?,?)', (owner,session,time.time(),json.dumps(redact(payload),ensure_ascii=False)))
