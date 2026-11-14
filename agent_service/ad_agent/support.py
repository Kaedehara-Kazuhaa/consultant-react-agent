"""Persistent issue lifecycle and handoff policy, independent of model output."""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager


class SupportLedger:
    def __init__(self, path, settings):
        self.path, self.settings = path, settings
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS support_issues (
              id TEXT PRIMARY KEY, owner TEXT, session TEXT, topic TEXT,
              status TEXT, answered_at REAL, updated_at REAL, resolution_kind TEXT,
              attempts INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS support_events (
              request_id TEXT, kind TEXT, owner TEXT, session TEXT, issue_id TEXT,
              created_at REAL, detail TEXT, PRIMARY KEY(request_id,kind));
            CREATE TABLE IF NOT EXISTS handoff_sessions (
              owner TEXT, session TEXT, historical_count INTEGER, eligible INTEGER,
              started_at REAL, PRIMARY KEY(owner,session));
            CREATE INDEX IF NOT EXISTS support_event_history ON support_events(owner,kind,created_at);
            CREATE INDEX IF NOT EXISTS support_issue_timer ON support_issues(status,answered_at);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def reconcile(self):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            due=db.execute("SELECT * FROM support_issues WHERE status='ANSWERED_WAITING' AND answered_at<=?",(now-self.settings.silence_seconds,)).fetchall()
            for row in due:
                event_id='silence-'+hashlib.sha256(f"{row['id']}|{row['answered_at']}".encode()).hexdigest()
                db.execute("INSERT OR IGNORE INTO support_events VALUES(?,'silence_resolution',?,?,?,?,?)",(event_id,row['owner'],row['session'],row['id'],now,json.dumps({'answered_at':row['answered_at'],'silence_seconds':self.settings.silence_seconds})))
                db.execute("UPDATE support_issues SET status='CONFIRMED_RESOLVED',resolution_kind='silence',updated_at=? WHERE id=?",(now,row['id']))

    def issue(self, owner, session, turn, previous):
        """Follow-up feedback refers to the last issue; explicit changed scope creates one."""
        slots = turn['slots']
        linked = turn.get('feedback') or turn['intent'] in {'feedback', 'handoff'}
        # Corrections, including changed dates, remain part of the issue being corrected.
        linked = linked or turn.get('inherited')
        old_id = previous.get('issue_id')
        with self.connect() as db:
            if linked and old_id:
                row = db.execute('SELECT * FROM support_issues WHERE id=? AND owner=? AND session=?', (old_id,owner,session)).fetchone()
                if row:
                    return dict(row)
            topic = json.dumps({'intent':turn['intent'],'slots':slots},sort_keys=True,ensure_ascii=False)
            issue_id = hashlib.sha256(f'{owner}|{session}|{topic}'.encode()).hexdigest()
            db.execute("INSERT OR IGNORE INTO support_issues VALUES(?,?,?,?,'COLLECTING',NULL,?,NULL,0)", (issue_id,owner,session,topic,time.time()))
            return dict(db.execute('SELECT * FROM support_issues WHERE id=?',(issue_id,)).fetchone())

    def update(self, issue_id, request_id, status, feedback=None, effective=False, signature=''):
        now=time.time()
        with self.connect() as db:
            row=db.execute('SELECT * FROM support_issues WHERE id=?',(issue_id,)).fetchone()
            if not row:
                return
            if feedback=='negative':
                db.execute("UPDATE support_issues SET status='UNRESOLVED',resolution_kind=NULL,answered_at=NULL,updated_at=? WHERE id=?",(now,issue_id))
                db.execute("INSERT OR IGNORE INTO support_events VALUES(?,'negative_feedback',?,?,?,?,?)",(request_id,row['owner'],row['session'],issue_id,now,''))
            if effective:
                # Retrying an identical answer is not a new effective attempt.
                detail=signature or request_id
                duplicate=db.execute("SELECT 1 FROM support_events WHERE issue_id=? AND kind='attempt' AND detail=?",(issue_id,detail)).fetchone()
                if not duplicate:
                    inserted=db.execute("INSERT OR IGNORE INTO support_events VALUES(?,'attempt',?,?,?,?,?)",(request_id,row['owner'],row['session'],issue_id,now,detail)).rowcount
                    if inserted:
                        db.execute('UPDATE support_issues SET attempts=attempts+1 WHERE id=?',(issue_id,))
                if status in {'ANSWERED_WAITING','UNRESOLVED'}:
                    db.execute("INSERT OR IGNORE INTO support_events VALUES(?,'business_answer',?,?,?,?,?)",(request_id,row['owner'],row['session'],issue_id,now,''))
            # A rejection or unrelated clarification must not silently restart the timer.
            if status=='ANSWERED_WAITING':
                db.execute("UPDATE support_issues SET status=?,answered_at=?,updated_at=?,resolution_kind=NULL WHERE id=?",(status,now,now,issue_id))
            elif status in {'CONFIRMED_RESOLVED','UNRESOLVED','HANDOFF_PENDING'}:
                db.execute('UPDATE support_issues SET status=?,resolution_kind=?,answered_at=NULL,updated_at=? WHERE id=?',(status,'explicit' if status=='CONFIRMED_RESOLVED' else None,now,issue_id))
                if status in {'CONFIRMED_RESOLVED','HANDOFF_PENDING'}:
                    db.execute('INSERT OR IGNORE INTO support_events VALUES(?,?,?,?,?,?,?)',(request_id,'explicit_resolution' if status=='CONFIRMED_RESOLVED' else 'handoff_notice',row['owner'],row['session'],issue_id,now,''))

    def has_answer(self, issue_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM support_events WHERE issue_id=? AND kind='business_answer'",(issue_id,)).fetchone() is not None

    def handoff_request(self, owner, session, request_id, issue_id, record=True):
        now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            frozen=db.execute('SELECT * FROM handoff_sessions WHERE owner=? AND session=?',(owner,session)).fetchone()
            if frozen is None:
                count=db.execute("SELECT COUNT(*) FROM support_events WHERE owner=? AND kind='handoff_request' AND session<>? AND created_at>=?",(owner,session,now-self.settings.handoff_history_days*86400)).fetchone()[0]
                db.execute('INSERT INTO handoff_sessions VALUES(?,?,?,?,?)',(owner,session,count,int(count<self.settings.handoff_history_limit),now))
                frozen=db.execute('SELECT * FROM handoff_sessions WHERE owner=? AND session=?',(owner,session)).fetchone()
            if record:
                db.execute("INSERT OR IGNORE INTO support_events VALUES(?,'handoff_request',?,?,?,?,?)",(request_id,owner,session,issue_id,now,''))
            attempts=db.execute('SELECT attempts FROM support_issues WHERE id=?',(issue_id,)).fetchone()[0]
            current=db.execute("SELECT COUNT(*) FROM support_events WHERE owner=? AND session=? AND kind='handoff_request'",(owner,session)).fetchone()[0]
            return {'historical_count':frozen['historical_count'],'session_request_count':current,'eligible':bool(frozen['eligible']),'attempts':attempts,'allow':bool(frozen['eligible']) and attempts>=self.settings.handoff_attempts}

    def state(self, owner, session):
        self.reconcile()
        with self.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT id,status,answered_at,updated_at,resolution_kind,attempts FROM support_issues WHERE owner=? AND session=? ORDER BY updated_at DESC',(owner,session))]
            count=db.execute("SELECT COUNT(*) FROM support_events WHERE owner=? AND kind='handoff_request'",(owner,)).fetchone()[0]
            events=[dict(r) for r in db.execute('SELECT kind,issue_id,created_at,detail FROM support_events WHERE owner=? AND session=? ORDER BY created_at DESC LIMIT 50',(owner,session))]
            return {'issues':rows,'handoff_request_count':count,'events':events}
