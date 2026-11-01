import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from workspace.server import FamilyServer, backup, connect, create_access_link, initialize, restore, set_password


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = initialize(self.root / 'data')
        self.password = 'test-only-family-password'
        set_password(self.database, self.password)
        self.server = FamilyServer(('127.0.0.1', 0), self.database)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        request_headers = {}
        if self.cookie:
            request_headers['Cookie'] = self.cookie
        if data is not None or raw is not None:
            request_headers.update({'Content-Type': 'application/json', 'X-Family-Desk': '1'})
        request_headers.update(headers or {})
        body = raw if raw is not None else json.dumps(data) if data is not None else None
        connection.request('GET' if body is None else 'POST', path, body, request_headers)
        response = connection.getresponse()
        payload = response.read()
        if response.getheader('Set-Cookie'):
            self.cookie = response.getheader('Set-Cookie').split(';')[0]
        result = (response.status, payload, dict(response.getheaders()))
        connection.close()
        return result

    def login(self):
        response = self.request('/api/login', {'password': self.password})
        self.assertEqual(response[0], 200)
        self.assertIn('HttpOnly', response[2]['Set-Cookie'])
        self.assertIn('SameSite=Strict', response[2]['Set-Cookie'])

    def test_private_data_authentication_and_logout(self):
        self.assertEqual(self.request('/')[0], 200)
        self.assertEqual(self.request('/api/items')[0], 401)
        self.assertEqual(self.request('/api/login', {'password': 'wrong-password'})[0], 401)
        self.login()
        self.assertEqual(self.request('/api/items')[0], 200)
        old_cookie = self.cookie
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.cookie = old_cookie
        self.assertEqual(self.request('/api/items')[0], 401)

    def test_save_update_persistence_and_recovery(self):
        self.login()
        item = {'kind': 'task', 'title': 'Plan our weekend', 'body': 'Ask everyone for one idea.',
                'owner': 'Everyone', 'status': 'inbox'}
        response = self.request('/api/items', item)
        self.assertEqual(response[0], 200)
        item['id'] = json.loads(response[1])['id']
        item['status'] = 'done'
        self.assertEqual(self.request('/api/items', item)[0], 200)
        note = {'kind': 'note', 'title': '<script>not executable</script>', 'body': 'Æ Ø Å 🌱'}
        self.assertEqual(self.request('/api/items', note)[0], 200)
        current = json.loads(self.request('/api/items')[1])['items']
        self.assertEqual(len(current), 2)
        self.assertEqual(next(row for row in current if row['id'] == item['id'])['status'], 'done')
        snapshot = self.root / 'backup.sqlite3'
        backup(self.database, snapshot)
        with self.assertRaises(FileExistsError):
            backup(self.database, snapshot)
        restore(snapshot, self.root / 'restored')
        with self.assertRaises(ValueError):
            restore(snapshot, self.root / 'restored')
        restored_path = self.root / 'restored' / 'family.sqlite3'
        db = connect(restored_path)
        restored = [dict(row) for row in db.execute('SELECT * FROM items ORDER BY updated_at DESC, id DESC')]
        db.close()
        self.assertEqual(restored, current)
        # A new server uses only the restored data; no old process state is needed.
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.server = FamilyServer(('127.0.0.1', 0), restored_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.assertEqual(self.request('/api/items')[0], 401)
        self.login()
        self.assertEqual(json.loads(self.request('/api/items')[1])['items'], current)

    def test_origin_host_and_input_boundaries(self):
        self.assertEqual(self.request('/', headers={'Host': 'attacker.example'})[0], 403)
        self.assertEqual(self.request('/api/login', {'password': self.password}, {'Origin': 'https://attacker.example'})[0], 403)
        self.assertEqual(self.request('/api/login', {'password': self.password}, {'X-Family-Desk': ''})[0], 403)
        self.login()
        for data in [{}, {'title': 'x' * 161}, {'title': 'ok', 'status': 'unknown'},
                     {'title': 'ok', 'id': True}, {'title': 'ok', 'owner': []}, []]:
            self.assertEqual(self.request('/api/items', data)[0], 400)
        self.assertEqual(self.request('/api/items', raw='{invalid')[0], 400)
        self.assertEqual(self.request('/api/items', raw='x' * 32769)[0], 413)
        self.assertEqual(self.request('/api/items', {'title': 'ok', 'id': 123})[0], 404)
        self.assertEqual(self.request('/../.family-data/family.sqlite3')[0], 404)

    def test_failed_logins_are_throttled_and_sessions_expire(self):
        for _ in range(5):
            self.assertEqual(self.request('/api/login', {'password': 'wrong-password'})[0], 401)
        self.assertEqual(self.request('/api/login', {'password': self.password})[0], 429)
        self.server.failures.clear()
        self.login()
        token = self.cookie.split('=', 1)[1]
        self.server.sessions[token] = 0
        self.assertEqual(self.request('/api/items')[0], 401)

    def test_one_use_access_link_and_password_setup(self):
        link = create_access_link(self.database, self.server.server_port)
        token = link.split('#access=')[1]
        self.assertEqual(self.request('/api/access', {'token': 'wrong'})[0], 401)
        self.assertEqual(self.request('/api/access', {'token': token})[0], 200)
        original_cookie = self.cookie
        self.assertEqual(self.request('/api/access', {'token': token})[0], 401)
        self.assertEqual(self.request('/api/project')[0], 200)
        self.assertEqual(self.request('/api/password', {'password': 'new-test-password'})[0], 200)
        self.cookie = original_cookie
        self.assertEqual(self.request('/api/items')[0], 401)
        self.assertEqual(self.request('/api/login', {'password': self.password})[0], 401)
        self.assertEqual(self.request('/api/login', {'password': 'new-test-password'})[0], 200)

    def test_expired_access_link_is_rejected(self):
        link = create_access_link(self.database, self.server.server_port)
        token = link.split('#access=')[1]
        db = connect(self.database)
        stored = json.loads(db.execute("SELECT value FROM settings WHERE key='access_link'").fetchone()[0])
        stored['expires'] = 0
        db.execute("UPDATE settings SET value=? WHERE key='access_link'", (json.dumps(stored),))
        db.commit()
        db.close()
        self.assertEqual(self.request('/api/access', {'token': token})[0], 401)

    def test_recovery_does_not_revive_login_links(self):
        create_access_link(self.database, self.server.server_port)
        snapshot = self.root / 'with-link.sqlite3'
        backup(self.database, snapshot)
        restore(snapshot, self.root / 'safe-recovery')
        db = connect(self.root / 'safe-recovery' / 'family.sqlite3')
        self.assertIsNone(db.execute("SELECT value FROM settings WHERE key='access_link'").fetchone())
        self.assertIsNotNone(db.execute("SELECT value FROM settings WHERE key='password'").fetchone())
        db.close()


if __name__ == '__main__':
    unittest.main()
