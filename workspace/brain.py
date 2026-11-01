"""Portable project records and transactional task ownership; no model execution."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

DEFAULT_DB = Path(__file__).resolve().parent.parent / '.family-data' / 'family.sqlite3'
KINDS = ('goal', 'decision', 'preference', 'observation', 'checkpoint')


def initialize_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS brain_records (
            key TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL,
            source TEXT NOT NULL, actor TEXT NOT NULL, revision INTEGER NOT NULL,
            updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_jobs (
            id INTEGER PRIMARY KEY, task_key TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL, scope TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'queued', agent TEXT,
            token TEXT, lease_until INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3, result TEXT, evidence TEXT,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_events (
            id INTEGER PRIMARY KEY, at INTEGER NOT NULL, actor TEXT NOT NULL,
            action TEXT NOT NULL, subject TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS agent_job_claims ON agent_jobs(state, scope, lease_until);
    ''')


def required(value, name, limit=16000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1–{limit} characters.')
    return value.strip()


class Brain:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as db:
            initialize_schema(db)
        self.path.chmod(0o600)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def event(db, actor, action, subject, detail):
        db.execute('INSERT INTO agent_events(at,actor,action,subject,detail) VALUES(?,?,?,?,?)',
                   (int(time.time()), actor, action, str(subject), json.dumps(detail)))

    def remember(self, key, kind, content, source, actor, expected_revision=0):
        key = required(key, 'key', 160)
        content = required(content, 'content')
        source = required(source, 'source', 2000)
        actor = required(actor, 'actor', 160)
        if kind not in KINDS or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Invalid kind or expected revision.')
        with self.connection(write=True) as db:
            row = db.execute('SELECT revision FROM brain_records WHERE key=?', (key,)).fetchone()
            actual = row['revision'] if row else 0
            if actual != expected_revision:
                raise ValueError(f'Revision conflict: expected {expected_revision}, found {actual}. Re-read before updating.')
            revision = actual + 1
            db.execute('INSERT OR REPLACE INTO brain_records VALUES(?,?,?,?,?,?,?)',
                       (key, kind, content, source, actor, revision, int(time.time())))
            self.event(db, actor, 'remember', key, {'revision': revision, 'kind': kind,
                       'content': content, 'source': source})
        return {'key': key, 'revision': revision}

    def enqueue(self, key, title, scope, actor, max_attempts=3):
        for value, name, limit in [(key, 'key', 160), (title, 'title', 2000),
                                   (scope, 'scope', 160), (actor, 'actor', 160)]:
            required(value, name, limit)
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError('max_attempts must be between 1 and 10.')
        with self.connection(write=True) as db:
            row = db.execute('SELECT * FROM agent_jobs WHERE task_key=?', (key,)).fetchone()
            if row:
                if (row['title'], row['scope'], row['max_attempts']) != (title, scope, max_attempts):
                    raise ValueError('Task key already exists with different details.')
                return self.public_job(row)
            now = int(time.time())
            cursor = db.execute('INSERT INTO agent_jobs(task_key,title,scope,max_attempts,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                                (key, title, scope, max_attempts, now, now))
            self.event(db, actor, 'enqueue', cursor.lastrowid, {'key': key, 'title': title, 'scope': scope})
            return self.public_job(db.execute('SELECT * FROM agent_jobs WHERE id=?', (cursor.lastrowid,)).fetchone())

    @staticmethod
    def public_job(row):
        return {key: row[key] for key in row.keys() if key != 'token'}

    def expire(self, db, now):
        expired = db.execute("SELECT id,agent FROM agent_jobs WHERE state='running' AND lease_until<=?", (now,)).fetchall()
        for row in expired:
            db.execute("UPDATE agent_jobs SET state='blocked',token=NULL,lease_until=NULL,updated_at=?,result=? WHERE id=?",
                       (now, 'Ownership expired. Reconcile the outcome before retrying.', row['id']))
            self.event(db, 'coordinator', 'lease_expired', row['id'], {'previous_agent': row['agent']})

    def claim(self, agent, lease_seconds=300):
        agent = required(agent, 'agent', 160)
        self.validate_lease(lease_seconds)
        with self.connection(write=True) as db:
            now = int(time.time())
            self.expire(db, now)
            row = db.execute("""SELECT * FROM agent_jobs AS job
                WHERE job.state='queued' AND job.attempts<job.max_attempts
                AND NOT EXISTS (SELECT 1 FROM agent_jobs AS active
                    WHERE active.scope=job.scope AND active.state IN ('running','blocked'))
                ORDER BY job.id LIMIT 1""").fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(24)
            db.execute("UPDATE agent_jobs SET state='running',agent=?,token=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?",
                       (agent, token, now + lease_seconds, now, row['id']))
            self.event(db, agent, 'claim', row['id'], {'lease_until': now + lease_seconds})
            return dict(db.execute('SELECT * FROM agent_jobs WHERE id=?', (row['id'],)).fetchone())

    @staticmethod
    def validate_lease(seconds):
        if type(seconds) is not int or not 30 <= seconds <= 3600:
            raise ValueError('Lease must be between 30 and 3600 seconds.')

    def heartbeat(self, job_id, agent, token, lease_seconds=300):
        self.validate_lease(lease_seconds)
        with self.connection(write=True) as db:
            now = int(time.time())
            cursor = db.execute("""UPDATE agent_jobs SET lease_until=?,updated_at=?
                WHERE id=? AND state='running' AND agent=? AND token=? AND lease_until>?""",
                (now + lease_seconds, now, job_id, agent, token, now))
            if cursor.rowcount != 1:
                raise ValueError('Claim is expired or no longer owned by this agent.')
        return {'id': job_id, 'lease_until': now + lease_seconds}

    def finish(self, job_id, agent, token, result, evidence):
        result = required(result, 'result')
        evidence = required(evidence, 'evidence')
        with self.connection(write=True) as db:
            now = int(time.time())
            cursor = db.execute("""UPDATE agent_jobs SET state='done',result=?,evidence=?,token=NULL,lease_until=NULL,updated_at=?
                WHERE id=? AND state='running' AND agent=? AND token=? AND lease_until>?""",
                (result, evidence, now, job_id, agent, token, now))
            if cursor.rowcount != 1:
                raise ValueError('Claim is expired or no longer owned by this agent.')
            self.event(db, agent, 'finish', job_id, {'result': result, 'evidence': evidence})
        return {'id': job_id, 'state': 'done'}

    def reconcile(self, job_id, actor, action, evidence):
        actor = required(actor, 'actor', 160)
        evidence = required(evidence, 'evidence')
        if action not in ('requeue', 'done', 'cancel'):
            raise ValueError('Reconciliation action must be requeue, done, or cancel.')
        with self.connection(write=True) as db:
            now = int(time.time())
            self.expire(db, now)
            row = db.execute('SELECT * FROM agent_jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['state'] != 'blocked':
                raise ValueError('Only blocked work may be reconciled.')
            if action == 'requeue' and row['attempts'] >= row['max_attempts']:
                raise ValueError('Retry budget exhausted. Resolve or cancel this task.')
            state = {'requeue': 'queued', 'done': 'done', 'cancel': 'cancelled'}[action]
            db.execute('UPDATE agent_jobs SET state=?,agent=NULL,token=NULL,lease_until=NULL,result=?,evidence=?,updated_at=? WHERE id=?',
                       (state, f'Reconciled: {action}', evidence, now, job_id))
            self.event(db, actor, 'reconcile', job_id, {'action': action, 'evidence': evidence})
        return {'id': job_id, 'state': state}

    def snapshot(self):
        # One read transaction gives an internally consistent view without claiming work.
        with self.connection() as db:
            db.execute('BEGIN')
            records = [dict(row) for row in db.execute('SELECT * FROM brain_records ORDER BY key')]
            jobs = [self.public_job(row) for row in db.execute('SELECT * FROM agent_jobs ORDER BY id')]
            events = [dict(row) for row in db.execute('SELECT * FROM agent_events ORDER BY id DESC LIMIT 30')]
        now = int(time.time())
        for job in jobs:
            job['lease_expired'] = job['state'] == 'running' and job['lease_until'] <= now
        return {'records': records, 'jobs': jobs, 'recent_events': events}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('snapshot')
    remember = commands.add_parser('remember')
    for name in ('key', 'content', 'source', 'actor'):
        remember.add_argument('--' + name, required=True)
    remember.add_argument('--kind', choices=KINDS, required=True)
    remember.add_argument('--expected-revision', type=int, default=0)
    enqueue = commands.add_parser('enqueue')
    for name in ('key', 'title', 'scope', 'actor'):
        enqueue.add_argument('--' + name, required=True)
    enqueue.add_argument('--max-attempts', type=int, default=3)
    claim = commands.add_parser('claim')
    claim.add_argument('--agent', required=True)
    claim.add_argument('--lease-seconds', type=int, default=300)
    for command in ('heartbeat', 'finish'):
        sub = commands.add_parser(command)
        sub.add_argument('--job-id', type=int, required=True)
        sub.add_argument('--agent', required=True)
        sub.add_argument('--token', required=True)
        if command == 'heartbeat':
            sub.add_argument('--lease-seconds', type=int, default=300)
        else:
            sub.add_argument('--result', required=True)
            sub.add_argument('--evidence', required=True)
    reconcile = commands.add_parser('reconcile')
    reconcile.add_argument('--job-id', type=int, required=True)
    reconcile.add_argument('--actor', required=True)
    reconcile.add_argument('--action', choices=('requeue', 'done', 'cancel'), required=True)
    reconcile.add_argument('--evidence', required=True)
    args = vars(parser.parse_args())
    path = args.pop('db')
    command = args.pop('command')
    try:
        result = getattr(Brain(path), command)(**args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
