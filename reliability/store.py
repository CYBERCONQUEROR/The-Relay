"""SQLite queue with atomic admission, fenced leases and restart-safe events.

Each operation opens its own connection. BEGIN IMMEDIATE serializes mutations
across threads/processes; the lease token fences stale workers from writes.
"""
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Protocol
import uuid

from reliability.telemetry import sanitize

STATUSES = ('pending', 'processing', 'waiting_for_quota', 'retry_scheduled', 'completed', 'failed', 'cancelled')
ACTIVE = ('pending', 'processing', 'waiting_for_quota', 'retry_scheduled')


class CapacityError(Exception):
    pass


class ConflictError(Exception):
    pass


class QueueBackend(Protocol):
    def claim(self, owner: str, lease_seconds: float, concurrency: int) -> dict | None: ...
    def get(self, job_id: str) -> dict | None: ...
    def event(self, job_id: str, event: dict, lease: str | None = None) -> bool: ...


class SQLiteQueue:
    def __init__(self, path: str, capacity: int = 200):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path, self.capacity = path, capacity
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, question TEXT NOT NULL, status TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_run REAL NOT NULL, error_category TEXT, error_message TEXT,
                    result TEXT, metadata TEXT NOT NULL, idempotency_key TEXT UNIQUE,
                    lease TEXT, lease_until REAL, owner TEXT, started REAL, completed REAL,
                    reset_estimated INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS eligible_jobs ON jobs(status, next_run);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    timestamp REAL NOT NULL, stage TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS job_events ON events(job_id, id);
                CREATE TABLE IF NOT EXISTS controls (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, heartbeat REAL NOT NULL);
            ''')

    @contextmanager
    def connection(self, write: bool = False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=15000')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        job = dict(row)
        for key in ('result', 'metadata'):
            job[key] = json.loads(job[key]) if job[key] else None
        for key in ('lease', 'lease_until', 'owner', 'idempotency_key'):
            job.pop(key, None)
        return job

    def _event(self, db, job_id, stage, state='completed', data=None, timestamp=None):
        recorded_at = timestamp if timestamp is not None else time.time()
        db.execute('INSERT INTO events(job_id,timestamp,stage,state,data) VALUES(?,?,?,?,?)',
                   (job_id, recorded_at, stage, state, json.dumps(data or {})))
        logging.getLogger('relay').info(json.dumps({'job_id':job_id,'timestamp':recorded_at,'stage':stage,'state':state,'data':data or {}}, default=str))

    def submit(self, question: str, metadata: dict, idempotency_key: str | None = None):
        now = time.time()
        with self.connection(True) as db:
            if idempotency_key:
                previous = db.execute('SELECT * FROM jobs WHERE idempotency_key=?', (idempotency_key,)).fetchone()
                if previous:
                    if previous['question'] != question or json.loads(previous['metadata']) != metadata:
                        raise ConflictError('Idempotency key already used for a different request')
                    return self.decode(previous)
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN (?,?,?,?)", ACTIVE).fetchone()[0]
            if count >= self.capacity:
                raise CapacityError('The persistent queue is full. Please try later.')
            job_id = str(uuid.uuid4())
            db.execute('INSERT INTO jobs(id,question,status,created,updated,next_run,metadata,idempotency_key) VALUES(?,?,?,?,?,?,?,?)',
                       (job_id, question, 'pending', now, now, now, json.dumps(metadata), idempotency_key))
            self._event(db, job_id, 'request_received')
            self._event(db, job_id, 'job_created')
            self._event(db, job_id, 'queue_admission')
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def get(self, job_id):
        with self.connection() as db:
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def list(self, status=None, limit=25, offset=0, search=''):
        if status is not None and status not in STATUSES:
            raise ValueError('Unknown job status')
        where, params = 'WHERE 1=1', []
        if status:
            where += ' AND status=?'
            params.append(status)
        if search:
            where += ' AND instr(id,?)>0'
            params.append(search)
        with self.connection() as db:
            count = db.execute(f'SELECT COUNT(*) FROM jobs {where}', params).fetchone()[0]
            rows = db.execute(f'SELECT * FROM jobs {where} ORDER BY created DESC LIMIT ? OFFSET ?', (*params, limit, offset)).fetchall()
            return {'items': [self.decode(row) for row in rows], 'total': count, 'limit': limit, 'offset': offset}

    def heartbeat(self, owner):
        with self.connection(True) as db:
            db.execute('INSERT OR REPLACE INTO workers VALUES(?,?)', (owner, time.time()))

    def claim(self, owner: str, lease_seconds: float, concurrency: int = 1):
        now = time.time()
        with self.connection(True) as db:
            stale = db.execute("SELECT id FROM jobs WHERE status='processing' AND lease_until<?", (now,)).fetchall()
            for row in stale:
                db.execute("UPDATE jobs SET status='retry_scheduled', next_run=?, updated=?, lease=NULL, owner=NULL WHERE id=?", (now, now, row['id']))
                self._event(db, row['id'], 'worker_recovery', data={'reason': 'Expired processing lease; provider call may have completed'})
            # API load experiments and queued RAG jobs share the real provider.
            # Never let a newly submitted job compete with an admitted experiment.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='experiments'").fetchone():
                if db.execute("SELECT 1 FROM experiments WHERE status='running' AND deadline>? AND json_extract(config,'$.provider')='groq'", (now,)).fetchone():
                    return None
            gate = db.execute("SELECT value FROM controls WHERE key='provider_not_before'").fetchone()
            if gate and float(gate[0]) > now:
                return None
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status='processing'").fetchone()[0] >= concurrency:
                return None
            row = db.execute("SELECT * FROM jobs WHERE status IN ('pending','retry_scheduled','waiting_for_quota') AND next_run<=? ORDER BY next_run,created LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            lease = str(uuid.uuid4())
            db.execute("UPDATE jobs SET status='processing', attempts=attempts+1, updated=?, started=COALESCE(started,?), lease=?, lease_until=?, owner=? WHERE id=?",
                       (now, now, lease, now + lease_seconds, owner, row['id']))
            self._event(db, row['id'], 'processing', 'running', {'attempt': row['attempts'] + 1, 'resumed': row['status'] == 'waiting_for_quota'})
            job = self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
            job['_lease'] = lease
            return job

    def renew(self, job_id, lease, seconds):
        with self.connection(True) as db:
            return db.execute("UPDATE jobs SET lease_until=? WHERE id=? AND lease=? AND status='processing'", (time.time() + seconds, job_id, lease)).rowcount == 1

    def event(self, job_id, event, lease=None):
        with self.connection(True) as db:
            if lease and not db.execute("SELECT 1 FROM jobs WHERE id=? AND lease=? AND status='processing'", (job_id, lease)).fetchone():
                return False
            self._event(db, job_id, event['stage'], event['state'], event.get('data'), event.get('timestamp'))
            return True

    def events(self, job_id, after=0, limit=500):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT ?', (job_id, after, limit)).fetchall()
            return [{**dict(row), 'data': json.loads(row['data'])} for row in rows]

    def finish(self, job_id, lease, status, result=None, category=None, message=None, next_run=None, estimated=False, gate=False):
        now = time.time()
        with self.connection(True) as db:
            changed = db.execute('''UPDATE jobs SET status=?, updated=?, result=?, error_category=?, error_message=?,
                next_run=?, reset_estimated=?, completed=?, lease=NULL, lease_until=NULL, owner=NULL
                WHERE id=? AND lease=? AND status='processing' ''',
                (status, now, json.dumps(result) if result is not None else None, category, sanitize(message) if message else None,
                 next_run or now, int(estimated), now if status in ('completed', 'failed') else None, job_id, lease)).rowcount
            if changed:
                self._event(db, job_id, status, 'failed' if status == 'failed' else 'completed', {'category': category, 'next_run': next_run, 'reset_estimated': estimated})
                if gate and next_run:
                    old = db.execute("SELECT value FROM controls WHERE key='provider_not_before'").fetchone()
                    db.execute("INSERT OR REPLACE INTO controls VALUES('provider_not_before',?)", (str(max(next_run, float(old[0]) if old else 0)),))
            return bool(changed)

    def cancel(self, job_id):
        with self.connection(True) as db:
            row = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row['status'] not in ('pending', 'retry_scheduled', 'waiting_for_quota'):
                raise ConflictError('Only pending or scheduled jobs can be cancelled; an active provider call cannot be cancelled.')
            now = time.time()
            db.execute("UPDATE jobs SET status='cancelled',updated=?,completed=? WHERE id=?", (now, now, job_id))
            self._event(db, job_id, 'cancelled')
        return self.get(job_id)

    def metrics(self):
        now = time.time()
        with self.connection() as db:
            counts = {status: 0 for status in STATUSES}
            counts.update({row[0]: row[1] for row in db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status')})
            oldest = db.execute("SELECT MIN(created) FROM jobs WHERE status IN ('pending','retry_scheduled','waiting_for_quota')").fetchone()[0]
            wait = db.execute('SELECT AVG(started-created) FROM jobs WHERE started IS NOT NULL').fetchone()[0]
            retry = db.execute("SELECT MIN(next_run) FROM jobs WHERE status IN ('retry_scheduled','waiting_for_quota')").fetchone()[0]
            workers = db.execute('SELECT COUNT(*) FROM workers WHERE heartbeat>?', (now - 15,)).fetchone()[0]
            # Only actual provider observations; do not infer calls from job results.
            calls = [json.loads(row[0]) for row in db.execute("SELECT data FROM events WHERE stage='groq_request' AND state IN ('completed','failed') ORDER BY id")]
            success = [c for c in calls if c.get('success')]
            latencies = sorted(c['latency_ms'] for c in calls if c.get('latency_ms') is not None)
            def percentile(p):
                if not latencies:
                    return None
                index = (len(latencies) - 1) * p
                lo = int(index)
                return latencies[lo] + (latencies[min(lo + 1, len(latencies)-1)] - latencies[lo]) * (index-lo)
            return {
                **counts, 'total': sum(counts.values()), 'capacity': self.capacity,
                'queue_depth': sum(counts[s] for s in ('pending','retry_scheduled','waiting_for_quota')),
                'oldest_pending_age_seconds': now-oldest if oldest else None,
                'average_queue_wait_seconds': wait, 'next_scheduled_retry': retry,
                'active_workers': counts['processing'], 'worker_processes': workers,
                'worker_status': 'online' if workers else 'offline', 'provider': 'groq',
                'api_requests': len(calls), 'api_successes': len(success), 'api_failures': len(calls)-len(success),
                'api_success_rate': len(success)/len(calls) if calls else None,
                'rate_limit_count': sum(c.get('http_status') == 429 for c in calls),
                'retry_count': sum(c.get('attempt', 1) > 1 for c in calls),
                'job_retry_count': db.execute('SELECT COALESCE(SUM(MAX(attempts-1,0)),0) FROM jobs').fetchone()[0],
                'average_latency_ms': sum(latencies)/len(latencies) if latencies else None,
                'p50_latency_ms': percentile(.5), 'p95_latency_ms': percentile(.95),
                'total_tokens': sum(c.get('total_tokens') or 0 for c in calls),
                'last_successful_groq_request': success[-1].get('timestamp') if success else None,
                'quota': calls[-1].get('rate_limit') if calls else None,
                'observations': calls[-200:],
            }
