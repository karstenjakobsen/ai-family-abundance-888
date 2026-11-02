"""One local SQLite system of record. Mutations and their journal commit together."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

KINDS = {"request", "goal", "event", "job", "finance", "note"}


def uid():
    return uuid.uuid4().hex


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ":" + digest


def password_valid(password, encoded):
    try:
        return hmac.compare_digest(password_hash(password, encoded.split(":")[0]), encoded)
    except (ValueError, TypeError):
        return False


class Brain:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
              INSERT OR IGNORE INTO schema_version VALUES(1);
              CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','member')), password TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, status TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL, metadata TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS journal(seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,
                actor TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
                allowance INTEGER NOT NULL, actual_tokens INTEGER, due REAL NOT NULL,
                started REAL, ended REAL, result TEXT, thread_id TEXT);
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status,approved,due);
              CREATE UNIQUE INDEX IF NOT EXISTS guardian_active ON jobs(title)
                WHERE actor='guardian' AND status IN ('pending','running','outcome_unknown');
              CREATE INDEX IF NOT EXISTS journal_time ON journal(at);
              INSERT OR IGNORE INTO settings VALUES('funded_tokens','0');
              INSERT OR IGNORE INTO settings VALUES('reserve_tokens','10000');
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def log(self, db, actor, action, subject=None, data=None):
        db.execute("INSERT INTO journal(at,actor,action,subject,data) VALUES(?,?,?,?,?)",
                   (time.time(), actor, action, subject, json.dumps(data or {}, ensure_ascii=False)))

    def add_user(self, name, password, role="member"):
        if role not in ("admin", "member") or len(password) < 12 or not 1 <= len(name) <= 80:
            raise ValueError("Use admin/member role, a name up to 80 characters, and a password of at least 12 characters")
        with self.connect() as db:
            user_id = uid()
            db.execute("INSERT INTO users VALUES(?,?,?,?)", (user_id, name, role, password_hash(password)))
            self.log(db, "local-admin", "user.created", user_id, {"name": name, "role": role})
        return user_id

    def authenticate(self, name, password, ttl):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
            # Run a scrypt even for unknown users to avoid a cheap username timing oracle.
            encoded = row["password"] if row else "00" * 16 + ":" + "00" * 64
            if not password_valid(password, encoded) or row is None:
                return None
            token = secrets.token_urlsafe(32)
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], time.time() + ttl))
            self.log(db, row["id"], "session.created")
            return token

    def user_for(self, token):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute("SELECT u.id,u.name,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?",
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def record(self, actor, kind, title, body="", metadata=None):
        if kind not in KINDS or not title.strip() or len(title) > 200 or len(body) > 20000:
            raise ValueError("Invalid record type, title, or body")
        ident, now = uid(), time.time()
        with self.connect() as db:
            db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)",
                       (ident, kind, title.strip(), body, "open", actor, now, now, json.dumps(metadata or {})))
            self.log(db, actor, "record.created", ident, {"kind": kind, "title": title})
        return ident

    def records(self, query="", limit=100):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM records WHERE title LIKE ? OR body LIKE ? ORDER BY updated DESC LIMIT ?",
                                              ("%" + query + "%", "%" + query + "%", min(200, max(1, limit))))]

    def set_status(self, actor, ident, status):
        if status not in ("open", "active", "done", "blocked"):
            raise ValueError("Invalid status")
        with self.connect() as db:
            if db.execute("UPDATE records SET status=?,updated=? WHERE id=?", (status, time.time(), ident)).rowcount != 1:
                raise ValueError("Unknown record")
            self.log(db, actor, "record.status", ident, {"status": status})

    def journal(self, limit=100):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM journal ORDER BY seq DESC LIMIT ?", (min(200, max(1, limit)),))]

    def append(self, actor, action, data=None, subject=None):
        with self.connect() as db:
            self.log(db, actor, action, subject, data)

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value, actor="local-admin", audit=True):
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
            if audit:
                self.log(db, actor, "setting.changed", key, {"value": value})

    def create_job(self, actor, title, prompt, allowance, due=None):
        if not title.strip() or len(title) > 200 or not prompt.strip() or len(prompt) > 20000 or not 100 <= allowance <= 1_000_000:
            raise ValueError("Job requires title, instructions, and an allowance from 100 to 1,000,000 tokens")
        ident = uid()
        with self.connect() as db:
            db.execute("INSERT INTO jobs(id,title,prompt,actor,allowance,due) VALUES(?,?,?,?,?,?)", (ident, title, prompt, actor, allowance, due or time.time()))
            self.log(db, actor, "job.proposed", ident, {"title": title, "allowance": allowance})
        return ident

    def jobs(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY due DESC LIMIT 100")]

    def approve(self, actor, ident):
        with self.connect() as db:
            if db.execute("UPDATE jobs SET approved=1 WHERE id=? AND status='pending' AND approved=0", (ident,)).rowcount != 1:
                raise ValueError("Job is missing or already approved/dispatched")
            self.log(db, actor, "job.approved", ident)

    def _budget(self, db):
        settings = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,value FROM settings WHERE key IN ('funded_tokens','reserve_tokens')")}
        used = db.execute("SELECT COALESCE(SUM(CASE WHEN status IN ('running','outcome_unknown') THEN MAX(allowance,COALESCE(actual_tokens,0)) ELSE COALESCE(actual_tokens,0) END),0) FROM jobs").fetchone()[0]
        return {"funded": settings["funded_tokens"], "reserve": settings["reserve_tokens"], "used_or_reserved": used,
                "dispatch_available": max(0, settings["funded_tokens"] - settings["reserve_tokens"] - used)}

    def budget(self):
        with self.connect() as db:
            return self._budget(db)

    def claim_job(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('running','outcome_unknown') LIMIT 1").fetchone():
                return None
            available = self._budget(db)["dispatch_available"]
            row = db.execute("SELECT * FROM jobs WHERE status='pending' AND approved=1 AND due<=? AND allowance<=? ORDER BY due LIMIT 1", (time.time(), available)).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (time.time(), row["id"]))
            self.log(db, "worker", "job.dispatched", row["id"], {"allowance": row["allowance"]})
            return dict(row)

    def finish_job(self, ident, status, result, actual=None, thread_id=None):
        if status not in ("done", "failed", "outcome_unknown") or actual is not None and actual < 0:
            raise ValueError("Invalid job outcome")
        with self.connect() as db:
            if db.execute("UPDATE jobs SET status=?,result=?,actual_tokens=?,thread_id=?,ended=? WHERE id=? AND status='running'",
                          (status, result[:20000], actual, thread_id, time.time(), ident)).rowcount != 1:
                raise ValueError("Job is not running")
            self.log(db, "worker", "job." + status, ident, {"actual_tokens": actual, "thread_id": thread_id})

    def recover_jobs(self):
        with self.connect() as db:
            for row in db.execute("SELECT id FROM jobs WHERE status='running'").fetchall():
                db.execute("UPDATE jobs SET status='outcome_unknown',ended=? WHERE id=?", (time.time(), row["id"]))
                self.log(db, "worker", "job.outcome_unknown", row["id"], {"reason": "Worker restarted; reconcile effects before retry"})

    def resolve_job(self, actor, ident, actual_tokens):
        if actual_tokens < 0:
            raise ValueError("Actual tokens must be nonnegative")
        with self.connect() as db:
            if db.execute("UPDATE jobs SET status='failed',actual_tokens=? WHERE id=? AND status='outcome_unknown'", (actual_tokens, ident)).rowcount != 1:
                raise ValueError("Only an outcome-unknown job can be reconciled")
            self.log(db, actor, "job.reconciled", ident, {"actual_tokens": actual_tokens, "retry": "New proposal required"})

    def backup(self, destination):
        destination = Path(destination)
        if destination.exists():
            raise ValueError("Backup destination already exists")
        with self.connect() as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
        destination.chmod(0o600)
        return str(destination)
