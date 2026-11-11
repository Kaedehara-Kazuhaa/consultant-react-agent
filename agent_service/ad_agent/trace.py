import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SENSITIVE = re.compile(r"token|secret|password|api_key|authorization|cookie", re.I)

def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SENSITIVE.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+\S+|sk-[A-Za-z0-9_-]+", "[REDACTED]", value)
        return value[:12000]
    return value

class TraceStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs (
              request_id TEXT PRIMARY KEY, owner TEXT, fingerprint TEXT,
              status TEXT, result TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS events (
              request_id TEXT, seq INTEGER, type TEXT, timestamp TEXT, payload TEXT,
              PRIMARY KEY(request_id,seq));
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def claim(self, request_id, owner, fingerprint):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT owner,fingerprint,status,result FROM runs WHERE request_id=?", (request_id,)).fetchone()
            if row:
                if row[0] != owner or row[1] != fingerprint:
                    raise ValueError("Request ID reused with different owner or input")
                if row[2] == "RUNNING":
                    raise RuntimeError("Request already running or interrupted; inspect trace and use a new ID")
                return json.loads(row[3])
            db.execute("INSERT INTO runs VALUES (?,?,?,'RUNNING',NULL,?)", (request_id, owner, fingerprint, datetime.now(timezone.utc).isoformat()))
        return None

    def emit(self, request_id, event_type, payload):
        event = {"type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), "payload": redact(payload)}
        with self.lock, self.connect() as db:
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE request_id=?", (request_id,)).fetchone()[0]
            event["seq"] = seq
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (request_id, seq, event_type, event["timestamp"], json.dumps(event["payload"], ensure_ascii=False)))
        return event

    def finish(self, request_id, result):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='DONE',result=? WHERE request_id=?", (json.dumps(redact(result), ensure_ascii=False), request_id))

    def events(self, request_id, owner):
        with self.connect() as db:
            row = db.execute("SELECT owner FROM runs WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row[0] != owner:
                raise KeyError("Trace not found")
            return [{"seq": s, "type": t, "timestamp": ts, "payload": json.loads(p)} for s,t,ts,p in db.execute("SELECT seq,type,timestamp,payload FROM events WHERE request_id=? ORDER BY seq", (request_id,))]
