"""Local-only test identities; never a substitute for production authentication."""
import secrets
import time
from dataclasses import dataclass


@dataclass
class TestIdentity:
    customer_id: str
    role: str
    created_at: float
    restricted: bool


class TestSessions:
    def __init__(self):
        self.identities={}

    def open(self,role,restricted=False):
        if role not in {'guest','trader'}:
            raise ValueError('Unknown test role')
        token=secrets.token_urlsafe(32)
        identity=TestIdentity('test-'+secrets.token_hex(16),role,time.time(),restricted)
        self.identities[token]=identity
        return token,identity

    def get(self,token):
        identity=self.identities.get(token)
        if identity and time.time()-identity.created_at<86400:
            return identity
        return None

    def close(self,token,engine,brand):
        identity=self.identities.get(token)
        if identity is None:return
        owner=identity.customer_id+':'+brand
        # Delete only the generated test owner, never real business rows or other tests.
        with engine.support.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM conversation_locks WHERE owner=? AND expires>?',(owner,time.time())).fetchone():
                return
            self.identities.pop(token,None)
            db.execute('DELETE FROM events WHERE request_id IN (SELECT request_id FROM runs WHERE owner=?)',(owner,))
            for table in ['runs','conversation_state','conversation_locks','support_issues','support_events','handoff_sessions']:
                db.execute(f'DELETE FROM {table} WHERE owner=?',(owner,))

    def seed(self,identity,engine,brand):
        if not identity.restricted:return
        owner=identity.customer_id+':'+brand
        with engine.support.connect() as db:
            for i in range(engine.settings.handoff_history_limit):
                db.execute("INSERT INTO support_events VALUES(?,'handoff_request',?,'seed-history','',?,'test fixture')",('seed-'+secrets.token_hex(16),owner,time.time()-60))
