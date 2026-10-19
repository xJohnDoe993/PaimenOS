import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


@unittest.skipUnless(importlib.util.find_spec('flask'), 'Flask required')
class GuardWebTests(unittest.TestCase):
    """Eltern-Web mit echtem Guard-Socket: Anmeldung, Token im Prozess, gefälschte Cookies."""

    @classmethod
    def setUpClass(cls):
        from paimenos import paths
        cls.tmp = tempfile.TemporaryDirectory()
        with patch.object(paths, 'CONFIG_DIR', Path(cls.tmp.name)):
            from paimenos import parent_web
        cls.web = parent_web
        cls.web.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        from paimenos import guard, guard_client, guard_store, state
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)
        base = Path(self.base.name)
        self.store = guard_store.GuardStore(str(base / 'guard'))
        self.store.initialize('482913', {'daily_limit_minutes': 60})
        path = str(base / 'g.sock')
        server = guard.build_server(self.store, guard.Tokens(), path, os.getuid())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        (base / 'enabled').write_text('1')
        for patcher in (patch.object(state, 'GUARD_MARKER', str(base / 'enabled')),
                        patch.object(guard_client, 'SOCKET', path),
                        patch.object(state.os, 'geteuid', return_value=1000)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.web.guard_sessions.clear()
        self.client = self.web.app.test_client()

    def login(self, pin, client=None):
        client = client or self.client
        with client.session_transaction() as s:
            s['csrf_token'] = 'csrf'
        response = client.post('/login', data={'pin': pin, 'csrf_token': 'csrf', 'next': '/time'})
        with client.session_transaction() as s:
            s['csrf_token'] = 'csrf'  # der Login leert die Sitzung; Formulare holen sich ein neues Token
        return response

    def test_login_keeps_the_token_out_of_the_cookie_and_allows_changes(self):
        response = self.login('482913')
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as s:
            self.assertNotIn('pin_fingerprint', s)
            sid = s['sid']
        token = self.web.guard_sessions[sid]
        self.assertNotIn(token, str(dict(s)))
        self.assertEqual(self.client.get('/time').status_code, 200)
        self.client.post('/time', data={'daily_limit_minutes': '45', 'csrf_token': 'csrf'})
        self.assertEqual(self.store.call('public', {})['daily_limit_minutes'], 45)

    def test_wrong_pin_is_refused_and_backoff_returns_429(self):
        for _ in range(4):
            self.assertEqual(self.login('000000').status_code, 200)
        locked = self.login('000000')
        self.assertEqual(locked.status_code, 429)
        self.assertGreaterEqual(int(locked.headers['Retry-After']), 1)
        self.assertEqual(self.login('482913').status_code, 429)  # auch die richtige PIN während der Sperre
        self.assertEqual(self.client.get('/time').status_code, 302)

    def test_forged_session_cookie_gets_no_token(self):
        with self.client.session_transaction() as s:
            s.update(parent_authenticated=True, sid='erfunden')
        response = self.client.get('/time')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        self.assertEqual(self.store.call('public', {})['daily_limit_minutes'], 60)

    def test_logout_revokes_the_token_and_pin_change_ends_other_sessions(self):
        self.login('482913')
        with self.client.session_transaction() as s:
            sid = s['sid']
        token = self.web.guard_sessions[sid]
        other = self.web.app.test_client()
        self.login('482913', other)
        self.client.post('/settings', data={'pin': '7777', 'pin_confirm': '7777', 'bg_color': '#112233', 'csrf_token': 'csrf'})
        self.assertEqual(self.client.get('/time').status_code, 200)  # eigene Sitzung bleibt
        self.assertEqual(other.get('/time').status_code, 302)        # andere Sitzung muss neu anmelden
        self.client.post('/logout', data={'csrf_token': 'csrf'})
        from paimenos import guard_client
        self.assertFalse(guard_client.whoami(token)[0])


if __name__ == '__main__':
    unittest.main()
