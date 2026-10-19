import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paimenos import guard, guard_client, guard_store as gs, state


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.now = [5000.0]
        self.store = gs.GuardStore(str(self.base / 'guard'), clock=lambda: self.now[0])
        self.tokens = guard.Tokens(clock=lambda: self.now[0])


class TokenTests(Base):
    def test_tokens_expire_idle_and_backup_tokens_only_set_many(self):
        token = self.tokens.issue()
        self.assertTrue(self.tokens.allows(token, 'set_limit'))
        self.now[0] += guard.TOKEN_IDLE - 1
        self.assertTrue(self.tokens.allows(token, 'set_limit'))  # Nutzung verlängert
        self.now[0] += guard.TOKEN_IDLE + 1
        self.assertFalse(self.tokens.allows(token, 'set_limit'))
        delegated = self.tokens.issue('backup')
        self.assertTrue(self.tokens.allows(delegated, 'set_many'))
        self.assertFalse(self.tokens.allows(delegated, 'set_limit'))
        self.assertFalse(self.tokens.allows(None, 'set_limit'))
        self.assertFalse(self.tokens.allows('erfunden', 'set_limit'))

    def test_token_table_is_bounded(self):
        for _ in range(guard.MAX_TOKENS + 10):
            self.tokens.issue()
        self.assertLessEqual(len(self.tokens.items), guard.MAX_TOKENS)


class DispatchTests(Base):
    def setUp(self):
        super().setUp()
        self.store.initialize('482913', {'daily_limit_minutes': 60})

    def ask(self, op, token=None, **args):
        return json.loads(json.dumps(guard.respond(self.store, self.tokens, json.dumps({'op': op, 'args': args, 'token': token}))))

    def test_login_then_changes_with_token(self):
        self.assertEqual(self.ask('set_limit', minutes=5)['code'], 'auth')
        self.assertEqual(self.ask('login', pin='000000')['code'], 'pin')
        token = self.ask('login', pin='482913')['result']['token']
        self.assertTrue(self.ask('set_limit', token, minutes=5)['ok'])
        self.assertEqual(self.ask('public')['result']['daily_limit_minutes'], 5)
        self.assertEqual(self.ask('whoami', token)['result']['valid'], True)
        self.ask('logout', token)
        self.assertEqual(self.ask('set_limit', token, minutes=9)['code'], 'auth')

    def test_pin_change_signs_out_other_sessions_but_not_the_caller(self):
        first = self.ask('login', pin='482913')['result']['token']
        second = self.ask('login', pin='482913')['result']['token']
        result = self.ask('save_preferences', first, bg_color='#123456', new_pin='7777')
        self.assertEqual(result['result']['epoch'], 2)
        self.assertTrue(self.ask('whoami', first)['result']['valid'])
        self.assertFalse(self.ask('whoami', second)['result']['valid'])

    def test_delegated_token_is_limited_to_settings_restore(self):
        full = self.ask('login', pin='482913')['result']['token']
        delegated = self.ask('delegate', full)['result']['token']
        self.assertTrue(self.ask('set_many', delegated, patch={'daily_limit_minutes': 20})['ok'])
        self.assertEqual(self.ask('set_limit', delegated, minutes=1)['code'], 'auth')
        self.assertEqual(self.ask('delegate', delegated)['code'], 'auth')

    def test_malformed_requests_never_crash(self):
        for raw in ('', 'nicht json', '[]', '{"op": 5}', '{"op": "public", "args": []}', '{"op": "gibt_es_nicht"}'):
            self.assertFalse(guard.respond(self.store, self.tokens, raw)['ok'])


class MaintenanceTests(Base):
    def setUp(self):
        super().setUp()
        self.home = self.base / 'home'
        (self.home / '.config/paimenos').mkdir(parents=True)
        self.legacy = self.home / '.config/paimenos/settings.json'
        self.marker = str(self.base / 'enabled')
        self.owners = {os.getuid()}

    def write_legacy(self, **changes):
        data = {'pin': '482913', 'daily_limit_minutes': 45, 'bonus_minutes': 0, 'today_used_seconds': 0,
                'last_used_date': '', 'disabled_apps': ['a'], 'bg_color': '#FF2D55'}
        data.update(changes)
        self.legacy.write_text(json.dumps(data))

    def run_maintain(self):
        return guard.maintain(self.store, clock=lambda: self.now[0], home=str(self.home), owners=self.owners,
                              marker=self.marker)

    def test_migration_keeps_the_legacy_file_until_the_delay_has_passed(self):
        self.write_legacy()
        self.assertTrue(self.run_maintain())
        self.assertTrue(os.path.exists(self.marker))
        self.assertEqual(self.store.call('public', {})['daily_limit_minutes'], 45)
        self.assertEqual(self.store.call('verify', {'pin': '482913'})['epoch'], 1)
        self.assertTrue(self.legacy.exists())  # Rollback bleibt möglich
        self.now[0] += guard.STRIP_DELAY - 1
        self.run_maintain()
        self.assertTrue(self.legacy.exists())
        self.now[0] += 2
        self.run_maintain()
        self.assertFalse(self.legacy.exists())
        self.assertFalse(self.run_maintain())

    def test_without_settings_nothing_happens_and_no_marker_is_set(self):
        self.assertFalse(self.run_maintain())
        self.assertFalse(os.path.exists(self.marker))

    def test_invalid_legacy_pin_or_settings_do_not_enable_the_guard(self):
        for changes in ({'pin': 'abc'}, {'daily_limit_minutes': -1}):
            with self.subTest(changes=changes):
                self.write_legacy(**changes)
                with self.assertRaises(ValueError):
                    self.run_maintain()
                self.assertFalse(os.path.exists(self.marker))
                self.assertFalse(self.store.exists())

    def test_symlinked_or_foreign_legacy_data_is_ignored(self):
        secret = self.base / 'secret.json'
        secret.write_text(json.dumps({'pin': '111111', 'daily_limit_minutes': 1, 'bonus_minutes': 0,
                                      'today_used_seconds': 0, 'last_used_date': '', 'disabled_apps': []}))
        self.legacy.symlink_to(secret)
        self.assertFalse(self.run_maintain())
        self.assertFalse(self.store.exists())
        self.legacy.unlink()
        # Verzeichnis ist eine Verknüpfung: wird nicht geöffnet.
        (self.home / '.config/paimenos').rename(self.base / 'real')
        (self.home / '.config/paimenos').symlink_to(self.base / 'real')
        (self.base / 'real/settings.json').write_text('{}')
        self.assertIsNone(guard.open_legacy_dir(str(self.home), self.owners))
        # Falscher Eigentümer
        self.assertIsNone(guard.open_legacy_dir(str(self.base), {99999}))

    def test_no_import_after_the_store_exists(self):
        self.write_legacy()
        self.run_maintain()
        self.write_legacy(pin='999999', daily_limit_minutes=1)  # Kind schreibt die Datei um
        self.run_maintain()
        self.assertEqual(self.store.call('public', {})['daily_limit_minutes'], 45)
        with self.assertRaises(gs.GuardError):
            self.store.call('verify', {'pin': '999999'})

    def test_export_for_rollback_and_reimport(self):
        self.write_legacy()
        self.run_maintain()
        self.store.call('set_limit', {'minutes': 20}, authenticated=True)
        # export_legacy fragt den Benutzer kids ab; hier ein Ersatz mit dem Testbenutzer.
        class Info:
            pw_uid, pw_gid = os.getuid(), os.getgid()
        with patch.object(guard.pwd, 'getpwnam', return_value=Info):
            guard.export_legacy(self.store, '7777', home=str(self.home), marker=self.marker)
        saved = json.loads(self.legacy.read_text())
        self.assertEqual((saved['pin'], saved['daily_limit_minutes']), ('7777', 20))
        self.assertEqual(stat.S_IMODE(self.legacy.stat().st_mode), 0o600)
        self.assertFalse(os.path.exists(self.marker))
        # Beim nächsten Start (Wiederaufstieg) übernimmt der Guard die Datei wieder.
        saved['daily_limit_minutes'] = 25
        self.legacy.write_text(json.dumps(saved))
        self.run_maintain()
        self.assertTrue(os.path.exists(self.marker))
        self.assertEqual(self.store.call('public', {})['daily_limit_minutes'], 25)
        self.assertEqual(self.store.call('verify', {'pin': '7777'})['epoch'], 2)


class SocketTests(Base):
    def test_client_and_server_round_trip_with_error_codes(self):
        self.store.initialize('482913')
        path = str(self.base / 'run/guard.sock')
        server = guard.build_server(self.store, self.tokens, path, os.getuid())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(guard_client, 'SOCKET', path):
            self.assertNotIn('pin', guard_client.call('public'))
            with self.assertRaises(gs.GuardError) as caught:
                guard_client.call('set_limit', minutes=5)
            self.assertEqual(caught.exception.code, 'auth')
            with self.assertRaises(gs.GuardError) as caught:
                guard_client.login('000000')
            self.assertEqual(caught.exception.code, 'pin')
            token = guard_client.login('482913')['token']
            with guard_client.use_token(token):
                self.assertEqual(guard_client.call('set_limit', minutes=7)['daily_limit_minutes'], 7)
            self.assertEqual(guard_client.whoami(token), (True, 1))
            token2 = guard_client.delegate(token)
            self.assertNotEqual(token, token2)
        with patch.object(guard_client, 'SOCKET', str(self.base / 'fehlt.sock')):
            with self.assertRaises(guard_client.GuardUnavailable):
                guard_client.call('public')


class FacadeTests(Base):
    def test_state_uses_the_guard_when_enabled_and_never_the_kids_file(self):
        self.store.initialize('482913', {'daily_limit_minutes': 33})
        legacy = self.base / 'settings.json'
        legacy.write_text(json.dumps({'pin': '000000'}))
        marker = self.base / 'enabled'
        marker.write_text('1')
        path = str(self.base / 's.sock')
        server = guard.build_server(self.store, self.tokens, path, os.getuid())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(state, 'GUARD_MARKER', str(marker)), patch.object(state, 'SETTINGS_FILE', str(legacy)), \
                patch.object(guard_client, 'SOCKET', path), patch.object(state.os, 'geteuid', return_value=1000):
            self.assertEqual(state.read_settings()['daily_limit_minutes'], 33)
            self.assertNotIn('pin', state.read_settings())
            self.assertEqual(state.update_settings({'bg_color': '#00FF00'})['bg_color'], '#00FF00')
            with self.assertRaises(PermissionError):
                state.update_settings({'daily_limit_minutes': 0})
            with self.assertRaises(PermissionError):
                state.update_settings(lambda data: data.update(daily_limit_minutes=0))
            self.assertEqual(json.loads(legacy.read_text()), {'pin': '000000'})
        with patch.object(state, 'GUARD_MARKER', str(marker)), patch.object(guard_client, 'SOCKET', str(self.base / 'weg.sock')), \
                patch.object(state.os, 'geteuid', return_value=1000):
            with self.assertRaises(guard_client.GuardUnavailable):
                state.read_settings()  # nicht auf die Kinder-Datei ausweichen


if __name__ == '__main__':
    unittest.main()
