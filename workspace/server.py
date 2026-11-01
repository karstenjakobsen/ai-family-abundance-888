"""Local family desk. Python standard library only; deliberately loopback-only."""
import argparse
from contextlib import closing
import getpass
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

if __package__:
    from . import brain
else:
    import brain

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT.parent / '.family-data'
STATUSES = ('inbox', 'next', 'active', 'done')


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def initialize(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / 'family.sqlite3'
    with closing(connect(path)) as db, db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, owner TEXT NOT NULL, status TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        ''')
        brain.initialize_schema(db)
    path.chmod(0o600)
    return path


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def set_password(path, password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Use a password between 12 and 256 characters.')
    salt = secrets.token_hex(16)
    value = json.dumps({'salt': salt, 'hash': password_hash(password, salt)})
    with closing(connect(path)) as db, db:
        db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', ('password', value))


def create_access_link(path, port):
    """One-use login link; only its digest is retained in the database."""
    with closing(connect(path)) as db:
        configured = db.execute("SELECT 1 FROM settings WHERE key='password'").fetchone()
    if not configured:
        set_password(path, secrets.token_urlsafe(48))
    token = secrets.token_urlsafe(32)
    value = json.dumps({'digest': hashlib.sha256(token.encode()).hexdigest(),
                        'expires': int(time.time()) + 3600})
    with closing(connect(path)) as db, db:
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('access_link', value))
    return f'http://127.0.0.1:{port}/#access={token}'


def backup(path, destination):
    # Exclusive creation prevents accidentally replacing an earlier backup.
    with destination.open('xb'):
        pass
    destination.chmod(0o600)
    with closing(connect(path)) as source, closing(connect(destination)) as target:
        source.backup(target)


def restore(source, directory):
    if directory.exists():
        raise ValueError('Restore into a new directory to preserve existing data.')
    if not source.is_file():
        raise ValueError('Backup file does not exist.')
    with closing(connect(source)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup integrity check failed.')
        db.execute('SELECT id, kind, title, body, owner, status, created_at, updated_at FROM items LIMIT 1')
        if not db.execute("SELECT value FROM settings WHERE key='password'").fetchone():
            raise ValueError('Backup has no workspace password.')
        directory.mkdir(parents=True, mode=0o700)
        target = directory / 'family.sqlite3'
        with closing(connect(target)) as restored:
            db.backup(restored)
            with restored:
                # A recovery copy must not revive an already-used login capability.
                restored.execute("DELETE FROM settings WHERE key='access_link'")
        target.chmod(0o600)


def validate_item(value):
    if not isinstance(value, dict):
        raise ValueError('Expected an object.')
    result = {}
    for key, maximum in [('title', 160), ('body', 10000), ('owner', 80)]:
        field = value.get(key, '')
        if not isinstance(field, str) or len(field) > maximum:
            raise ValueError(f'{key.capitalize()} must be text, at most {maximum} characters.')
        result[key] = field.strip()
    if not result['title']:
        raise ValueError('Please add a title.')
    result['kind'] = value.get('kind', 'task')
    result['status'] = value.get('status', 'inbox')
    if result['kind'] not in ('task', 'note') or result['status'] not in STATUSES:
        raise ValueError('Unknown item type or status.')
    return result


class FamilyServer(HTTPServer):
    def __init__(self, address, database):
        if address[0] != '127.0.0.1':
            raise ValueError('This version supports local access only.')
        self.database = database
        self.sessions = {}
        self.failures = []
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'FamilyDesk'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_args):
        pass  # Do not log family content, cookies, or credentials.

    def respond(self, status, data, content_type='application/json', cookie=None):
        if content_type == 'application/json':
            data = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(data)

    def allowed(self, mutation=False):
        port = self.server.server_port
        host = self.headers.get('Host', '')
        if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            self.respond(403, {'error': 'Local access only.'})
            return False
        if mutation and (self.headers.get('Origin', f'http://{host}') != f'http://{host}'
                         or self.headers.get('X-Family-Desk') != '1'):
            self.respond(403, {'error': 'Request origin rejected.'})
            return False
        return True

    def session_token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            return cookie['family_session'].value if 'family_session' in cookie else ''
        except Exception:
            return ''

    def authenticated(self):
        now = time.time()
        self.server.sessions = {key: expiry for key, expiry in self.server.sessions.items() if expiry > now}
        return self.session_token() in self.server.sessions

    def require_auth(self):
        if self.authenticated():
            return True
        self.respond(401, {'error': 'Please sign in.'})
        return False

    def do_GET(self):
        if not self.allowed():
            return
        route = urlsplit(self.path).path
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if route in assets:
            name, content_type = assets[route]
            return self.respond(200, (ROOT / 'static' / name).read_bytes(), content_type)
        if route == '/api/session':
            return self.respond(200, {'authenticated': self.authenticated()})
        if not self.require_auth():
            return
        if route == '/api/items':
            with closing(connect(self.server.database)) as db:
                rows = db.execute('SELECT * FROM items ORDER BY updated_at DESC, id DESC').fetchall()
            return self.respond(200, {'items': [dict(row) for row in rows]})
        if route == '/api/project':
            return self.respond(200, brain.Brain(self.server.database).snapshot())
        self.respond(404, {'error': 'Not found.'})

    def do_POST(self):
        if not self.allowed(mutation=True):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768:
                return self.respond(413, {'error': 'Request is too large or empty.'})
            if self.headers.get('Content-Type') != 'application/json':
                return self.respond(415, {'error': 'Expected JSON.'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected an object.')
            route = urlsplit(self.path).path
            if route == '/api/login':
                return self.login(data)
            if route == '/api/access':
                return self.access(data)
            if not self.require_auth():
                return
            if route == '/api/logout':
                self.server.sessions.pop(self.session_token(), None)
                return self.respond(200, {'ok': True}, cookie='family_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
            if route == '/api/password':
                set_password(self.server.database, data.get('password', ''))
                with closing(connect(self.server.database)) as db, db:
                    db.execute("DELETE FROM settings WHERE key='access_link'")
                self.server.sessions.clear()
                return self.start_session()
            if route == '/api/items':
                item = validate_item(data)
                item_id = data.get('id')
                if item_id is not None and (type(item_id) is not int or item_id < 1):
                    raise ValueError('Invalid item ID.')
                now = int(time.time())
                with closing(connect(self.server.database)) as db, db:
                    values = (item['kind'], item['title'], item['body'], item['owner'], item['status'], now)
                    if item_id is None:
                        cursor = db.execute('INSERT INTO items (kind,title,body,owner,status,updated_at,created_at) VALUES (?,?,?,?,?,?,?)', (*values, now))
                        item_id = cursor.lastrowid
                    else:
                        cursor = db.execute('UPDATE items SET kind=?,title=?,body=?,owner=?,status=?,updated_at=? WHERE id=?', (*values, item_id))
                        if not cursor.rowcount:
                            return self.respond(404, {'error': 'Item no longer exists.'})
                return self.respond(200, {'id': item_id})
            self.respond(404, {'error': 'Not found.'})
        except (ValueError, UnicodeDecodeError):
            self.respond(400, {'error': 'Check the fields: title is required (160 characters max), name 80, and notes 10,000.'})
        except sqlite3.Error:
            self.respond(503, {'error': 'Could not save or load data. Please try again.'})

    def login(self, data):
        now = time.time()
        self.server.failures = [when for when in self.server.failures if when > now - 60]
        if len(self.server.failures) >= 5:
            return self.respond(429, {'error': 'Too many attempts. Wait a minute and try again.'})
        password = data.get('password', '')
        with closing(connect(self.server.database)) as db:
            row = db.execute("SELECT value FROM settings WHERE key='password'").fetchone()
        stored = json.loads(row['value']) if row else None
        valid = (isinstance(password, str) and 12 <= len(password) <= 256 and stored
                 and hmac.compare_digest(password_hash(password, stored['salt']), stored['hash']))
        if not valid:
            self.server.failures.append(now)
            return self.respond(401, {'error': 'That password did not match.'})
        self.server.failures.clear()
        self.start_session()

    def access(self, data):
        token = data.get('token', '')
        if not isinstance(token, str) or len(token) > 128:
            return self.respond(401, {'error': 'This access link is invalid or expired.'})
        with closing(connect(self.server.database)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM settings WHERE key='access_link'").fetchone()
            stored = json.loads(row['value']) if row else None
            if (not stored or stored['expires'] <= time.time()
                    or not hmac.compare_digest(stored['digest'], hashlib.sha256(token.encode()).hexdigest())):
                return self.respond(401, {'error': 'This access link is invalid or expired.'})
            db.execute("DELETE FROM settings WHERE key='access_link'")
        self.start_session()

    def start_session(self):
        now = time.time()
        self.server.sessions.pop(self.session_token(), None)
        self.server.sessions = {key: expiry for key, expiry in self.server.sessions.items() if expiry > now}
        token = secrets.token_urlsafe(32)
        self.server.sessions[token] = now + 43200
        self.respond(200, {'ok': True}, cookie=f'family_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--port', type=int, default=8765)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--set-password', action='store_true')
    action.add_argument('--backup', type=Path)
    action.add_argument('--restore', type=Path)
    action.add_argument('--access-link', action='store_true', help='Create a one-use login link, valid for one hour; does not start a server.')
    args = parser.parse_args()
    try:
        if args.restore:
            restore(args.restore, args.data_dir)
            print('Backup restored. Start with the same --data-dir to verify it.')
            return
        path = initialize(args.data_dir)
        if args.access_link:
            if not 1 <= args.port <= 65535:
                raise ValueError('Port must be between 1 and 65535.')
            print(create_access_link(path, args.port))
            return
        if args.backup:
            backup(path, args.backup)
            print('Backup created. Store a copy outside this machine.')
            return
        with closing(connect(path)) as db:
            configured = db.execute("SELECT 1 FROM settings WHERE key='password'").fetchone()
        if args.set_password or not configured:
            password = getpass.getpass('Choose a family workspace password (12+ characters): ')
            if password != getpass.getpass('Repeat password: '):
                raise ValueError('Passwords did not match.')
            set_password(path, password)
            if args.set_password:
                print('Password updated. Restart any running server to invalidate existing sessions.')
                return
        with FamilyServer(('127.0.0.1', args.port), path) as server:
            print(f'Family desk: http://127.0.0.1:{server.server_port} — press Ctrl+C to stop.', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except (ValueError, OSError, sqlite3.Error, EOFError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
