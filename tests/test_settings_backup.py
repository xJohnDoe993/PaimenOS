import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paimenos import backups as b
from paimenos import settings_backup as sb
from paimenos import state

SETTINGS = {'pin': '482913', 'bg_color': '#ff2d55', 'daily_limit_minutes': 90, 'bonus_minutes': 30,
            'today_used_seconds': 1200, 'last_used_date': '', 'disabled_apps': ['tuxpaint', 'weg'],
            'category_tabs': True, 'speak_titles': True, 'speak_voice': 'female',
            'allowed_from': '10:00', 'allowed_until': '18:00', 'window_override_date': '2099-01-01'}
APPS = [
    {'id': 'pbs-kids', 'type': 'webapp', 'title': 'PBS', 'url': 'https://pbskids.org/', 'profile': 'pbs-kids'},
    {'id': 'blockly', 'type': 'webapp', 'title': 'Blockly', 'url': 'https://blockly.games/', 'category': 'games',
     'pronunciation': 'Blockly', 'profile': 'blockly', 'icon_file': 'x.png'},
    {'id': 'tuxpaint', 'type': 'native', 'title': 'Tux Paint', 'command': '/usr/bin/tuxpaint'},
    {'id': 'evil', 'type': 'webapp', 'title': 'Evil', 'url': 'javascript:alert(1)'},
    {'id': 'poweroff', 'type': 'system', 'title': 'Aus', 'command': 'poweroff'},
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.settings = self.base / 'settings.json'
        self.apps = self.base / 'apps.json'
        self.settings.write_text(json.dumps(SETTINGS))
        self.apps.write_text(json.dumps(APPS))
        for patcher in (patch.object(state, 'SETTINGS_FILE', str(self.settings)),
                        patch.object(sb.parent, 'APPS_FILE', str(self.apps))):
            patcher.start()
            self.addCleanup(patcher.stop)


class SnapshotTests(Base):
    def test_pin_and_usage_are_never_saved(self):
        raw = sb.snapshot()
        text = raw.decode()
        for secret in ('482913', 'pin', 'bonus_minutes', 'today_used_seconds', 'window_override_date', '2099'):
            self.assertNotIn(secret, text)
        data = json.loads(raw)
        self.assertEqual(data['settings']['daily_limit_minutes'], 90)

    def test_only_valid_webapps_are_saved_without_commands(self):
        data = json.loads(sb.snapshot())
        self.assertEqual([w['id'] for w in data['webapps']], ['pbs-kids', 'blockly'])
        self.assertNotIn('command', json.dumps(data))


class ParseTests(Base):
    def plan(self, **changes):
        data = json.loads(sb.snapshot())
        for key, value in changes.items():
            data[key] = value
        return json.dumps(data).encode()

    def test_round_trip_is_accepted(self):
        plan = sb.parse(sb.snapshot())
        self.assertEqual(plan['settings']['bg_color'], '#FF2D55')
        self.assertEqual(len(plan['webapps']), 2)

    def test_dangerous_or_malformed_content_is_rejected(self):
        good = json.loads(sb.snapshot())
        def broken(mutate):
            data = json.loads(json.dumps(good))
            mutate(data)
            return json.dumps(data).encode()
        cases = [
            lambda d: d['webapps'].append({'id': 'x', 'title': 'x', 'url': 'file:///etc/passwd'}),
            lambda d: d['webapps'].append({'id': 'x', 'title': 'x', 'url': 'javascript:alert(1)'}),
            lambda d: d['webapps'].append({'id': '../x', 'title': 'x', 'url': 'https://a.example/'}),
            lambda d: d['webapps'].append({'id': 'pbs-kids', 'title': 'x', 'url': 'https://a.example/'}),
            lambda d: d['webapps'][0].update(category='root'),
            lambda d: d['webapps'][0].update(title='x\nbefehl'),
            lambda d: d['settings'].update(daily_limit_minutes=-1),
            lambda d: d['settings'].update(daily_limit_minutes=True),
            lambda d: d['settings'].update(bg_color='red'),
            lambda d: d['settings'].update(speak_voice='robot'),
            lambda d: d['settings'].update(allowed_from='25:00'),
            lambda d: d['settings'].update(allowed_from='10:00', allowed_until='10:00'),
            lambda d: d.update(format='other'),
        ]
        for index, mutate in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                sb.parse(broken(mutate))
        for raw in (b'', b'not json', b'[]', b'\xff\xfe'):
            with self.assertRaises(ValueError):
                sb.parse(raw)


class ApplyTests(Base):
    def test_apply_keeps_pin_and_usage_and_never_overwrites_other_apps(self):
        plan = sb.parse(json.dumps({
            'format': sb.FORMAT,
            'settings': {'bg_color': '#00ff00', 'daily_limit_minutes': 30, 'category_tabs': False,
                         'speak_titles': False, 'speak_voice': 'male', 'allowed_from': '', 'allowed_until': '',
                         'disabled_apps': ['tuxpaint', 'unbekannt', 'poweroff']},
            'webapps': [{'id': 'neu', 'title': 'Neu', 'url': 'https://neu.example/', 'category': 'games'},
                        {'id': 'pbs-kids', 'title': 'PBS 2', 'url': 'https://pbskids.org/games/'},
                        {'id': 'tuxpaint', 'title': 'Kaputt', 'url': 'https://tux.example/'}],
        }).encode())
        self.assertEqual(sb.apply(plan), (2, 1))
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved['pin'], '482913')
        self.assertEqual(saved['daily_limit_minutes'], 30)
        self.assertEqual(saved['bg_color'], '#00FF00')
        self.assertEqual(saved['disabled_apps'], ['tuxpaint'])
        apps = {a['id']: a for a in json.loads(self.apps.read_text())}
        self.assertEqual(apps['neu']['profile'], 'neu')
        self.assertEqual(apps['neu']['type'], 'webapp')
        self.assertNotIn('command', apps['neu'])
        self.assertEqual(apps['pbs-kids']['url'], 'https://pbskids.org/games/')
        self.assertEqual(apps['tuxpaint']['command'], '/usr/bin/tuxpaint')


class UsbRoundTripTests(Base):
    def setUp(self):
        super().setUp()
        self.usb = self.base / 'usb'
        self.usb.mkdir()
        self.state_dir = self.base / 'state'
        self.state_dir.mkdir()
        self.mapping = {key: self.base / key for key in ('roms', 'saves', 'states', 'bios', 'legacy')}
        self.mapping.update(legacy_states=self.base / 'legacy', apps=self.apps)
        for patcher in (patch.object(b, 'STATE_DIR', self.state_dir), patch.object(b, 'JOURNAL', self.state_dir / 'j.json'),
                        patch.object(b, 'targets', return_value=self.mapping), patch.object(b, 'CHUNK', 64),
                        patch.object(b.os.path, 'ismount', return_value=True),
                        patch.object(b, 'drives', return_value=[{'id': 'usb', 'path': str(self.usb)}])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_settings_only_backup_and_restore(self):
        name = b.create('usb', ['settings'], lambda *a: None)
        self.assertEqual(b.available('usb')[0]['groups'], ['settings'])
        self.settings.write_text(json.dumps(dict(SETTINGS, daily_limit_minutes=5, pin='999999')))
        b.restore('usb', name, ['settings'], lambda *a: None)
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved['daily_limit_minutes'], 90)
        self.assertEqual(saved['pin'], '999999')

    def test_tampered_settings_change_nothing(self):
        name = b.create('usb', ['settings'], lambda *a: None)
        folder = self.usb / 'PaimenOS-Backups' / name
        manifest = json.loads((folder / 'manifest.json').read_text())
        entry = next(f for f in manifest['files'] if f['root'] == 'settings')
        (folder / entry['chunks'][0]).write_bytes(b'x' * len((folder / entry['chunks'][0]).read_bytes()))
        before = self.settings.read_text()
        with self.assertRaises(ValueError):
            b.restore('usb', name, ['settings'], lambda *a: None)
        self.assertEqual(self.settings.read_text(), before)


if __name__ == '__main__':
    unittest.main()
