import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paimenos import guard_store as gs


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = os.path.join(self.tmp.name, 'guard')
        self.now = [1000.0]
        self.store = gs.GuardStore(self.directory, clock=lambda: self.now[0])
        self.store.initialize('482913', {'daily_limit_minutes': 60, 'bg_color': '#FF2D55'})

    def call(self, op, authenticated=False, **args):
        return self.store.call(op, args, authenticated)

    def test_pin_is_hashed_and_file_is_private(self):
        text = Path(self.store.path).read_text()
        self.assertNotIn('482913', text)
        self.assertEqual(json.loads(text)['pin']['algo'], 'scrypt')
        self.assertEqual(stat.S_IMODE(os.stat(self.store.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.directory).st_mode), 0o700)
        with self.assertRaises(ValueError):
            self.store.initialize('111111')

    def test_public_view_has_no_pin_and_matches_the_old_shape(self):
        view = self.call('public')
        self.assertNotIn('pin', view)
        self.assertEqual(view['daily_limit_minutes'], 60)
        for key in ('bonus_minutes', 'today_used_seconds', 'disabled_apps', 'allowed_from', 'window_override_date'):
            self.assertIn(key, view)

    def test_changes_need_authentication_but_reading_ticking_and_colour_do_not(self):
        for op, args in (('set_limit', {'minutes': 0}), ('add_bonus', {'minutes': 5}), ('reset_today', {}),
                         ('set_window', {'start': '', 'end': ''}), ('apps_active', {'ids': ['a'], 'active': False}),
                         ('save_preferences', {'bg_color': '#000000'}), ('set_many', {'patch': {}})):
            with self.subTest(op=op), self.assertRaises(gs.GuardError) as caught:
                self.call(op, **args)
            self.assertEqual(caught.exception.code, 'auth')
        self.assertEqual(self.call('tick', seconds=30)['today_used_seconds'], 30)
        self.assertEqual(self.call('set_color', bg_color='#00ff00')['bg_color'], '#00FF00')
        self.assertEqual(self.call('set_limit', authenticated=True, minutes=5)['daily_limit_minutes'], 5)

    def test_tick_only_accepts_small_positive_integers(self):
        for value in (0, -5, 301, 1.5, True, '10', None):
            with self.subTest(value=value), self.assertRaises(gs.GuardError):
                self.call('tick', seconds=value)

    def test_wrong_pins_lock_globally_and_survive_a_restart(self):
        for _ in range(4):
            with self.assertRaises(gs.GuardError) as caught:
                self.call('verify', pin='000000')
            self.assertEqual(caught.exception.code, 'pin')
        with self.assertRaises(gs.GuardError):
            self.call('verify', pin='000000')
        # Auch die richtige PIN wird während der Sperre abgelehnt.
        with self.assertRaises(gs.GuardError) as caught:
            gs.GuardStore(self.directory, clock=lambda: self.now[0]).call('verify', {'pin': '482913'})
        self.assertEqual(caught.exception.code, 'locked')
        self.assertGreater(caught.exception.wait, 0)
        self.now[0] += 31
        self.assertEqual(self.call('verify', pin='482913')['epoch'], 1)
        # Erfolg setzt den Zähler zurück.
        with self.assertRaises(gs.GuardError) as caught:
            self.call('verify', pin='1')
        self.assertEqual(caught.exception.wait, 0)

    def test_lock_time_grows_and_is_capped(self):
        waits = []
        for _ in range(14):
            with self.assertRaises(gs.GuardError) as caught:
                self.call('verify', pin='000000')
            waits.append(caught.exception.wait)
            self.now[0] += 2000
        self.assertEqual(waits[:4], [0, 0, 0, 0])
        self.assertEqual(waits[4:7], [30, 60, 120])
        self.assertEqual(max(waits), gs.MAX_LOCK_SECONDS)

    def test_lockscreen_operations_check_the_pin_in_the_same_request(self):
        with self.assertRaises(gs.GuardError):
            self.call('unlock_today', pin='000000')
        self.assertEqual(self.call('public')['window_override_date'], '')
        self.assertEqual(self.call('unlock_today', pin='482913')['window_override_date'], str(date.today()))
        self.call('tick', seconds=120)
        result = self.call('bonus_until', pin='482913', minutes=15)
        self.assertEqual(result['bonus_minutes'], 0)  # 2 + 15 Minuten liegen noch unter dem Limit von 60
        self.call('set_limit', authenticated=True, minutes=1)
        self.assertEqual(self.call('bonus_until', pin='482913', minutes=15)['bonus_minutes'], 16)
        for minutes in (4, 181, True, '5'):
            with self.assertRaises(gs.GuardError):
                self.call('bonus_until', pin='482913', minutes=minutes)

    def test_pin_change_bumps_the_epoch_and_clears_the_lock(self):
        for _ in range(5):
            with self.assertRaises(gs.GuardError):
                self.call('verify', pin='000000')
        result = self.call('save_preferences', authenticated=True, bg_color='#112233', new_pin='7777')
        self.assertEqual(result['epoch'], 2)
        self.assertEqual(self.call('verify', pin='7777')['epoch'], 2)
        with self.assertRaises(gs.GuardError):
            self.call('save_preferences', authenticated=True, bg_color='#112233', new_pin='12')

    def test_root_reset_pin(self):
        self.store.reset_pin('55555')
        self.assertEqual(self.call('verify', pin='55555')['epoch'], 2)
        with self.assertRaises(ValueError):
            self.store.reset_pin('abc')

    def test_backup_may_only_set_whitelisted_valid_values(self):
        ok = {'daily_limit_minutes': 30, 'allowed_from': '10:00', 'allowed_until': '18:00', 'disabled_apps': ['x']}
        self.assertEqual(self.call('set_many', authenticated=True, patch=ok)['allowed_from'], '10:00')
        for patch in ({'pin': '1234'}, {'today_used_seconds': 0}, {'bonus_minutes': 600}, {'daily_limit_minutes': -1},
                      {'bg_color': 'red'}, {'allowed_from': '10:00', 'allowed_until': '10:00'},
                      {'disabled_apps': 'x'}, {'speak_voice': 'robot'}, {'category_tabs': 1}):
            with self.subTest(patch=patch), self.assertRaises(gs.GuardError):
                self.call('set_many', authenticated=True, patch=patch)

    def test_corrupt_store_is_refused_not_repaired(self):
        doc = json.loads(Path(self.store.path).read_text())
        doc['settings']['daily_limit_minutes'] = -5
        Path(self.store.path).write_text(json.dumps(doc))
        with self.assertRaises(ValueError):
            self.call('public')
        self.assertEqual(json.loads(Path(self.store.path).read_text())['settings']['daily_limit_minutes'], -5)

    def test_apps_toggle_and_bounds(self):
        self.call('apps_active', authenticated=True, ids=['b', 'a'], active=False)
        self.assertEqual(self.call('public')['disabled_apps'], ['a', 'b'])
        self.call('toggle_app', authenticated=True, id='a')
        self.call('apps_active', authenticated=True, ids=['b'], active=True)
        self.assertEqual(self.call('public')['disabled_apps'], [])
        with self.assertRaises(gs.GuardError):
            self.call('apps_active', authenticated=True, ids=['x'] * 1001, active=False)

    def test_root_tools_can_change_settings_directly(self):
        result = self.store.apply(lambda data: data.update(disabled_apps=['z']))
        self.assertEqual(result['disabled_apps'], ['z'])
        self.assertNotIn('pin', result)


if __name__ == '__main__':
    unittest.main()
