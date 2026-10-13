import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paimenos import flatpak_store as store
from paimenos import flatpak_service as service


def hit(app_id, **fields):
    return {'app_id': app_id, 'name': 'Name ' + app_id, 'summary': 'Zusammenfassung', 'type': 'desktop-application',
            'icon': 'https://dl.flathub.org/media/x.png', **fields}


class Response:
    def __init__(self, payload, url='https://flathub.org/api/v2/search'):
        self.payload, self.url = payload, url
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit=-1): return self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode()


class StoreTests(unittest.TestCase):
    def test_app_id_validation(self):
        for good in ('org.tuxpaint.Tuxpaint', 'net.supertuxkart.SuperTuxKart', 'io.github.a-b.App_1'):
            self.assertTrue(store.valid_app_id(good), good)
        for bad in ('', 'tuxpaint', 'a.b', '../org.x.y', 'org.x.y;rm -rf /', 'org.x.y/branch', 'org..x', None, 5, 'org.x.' + 'a' * 300):
            self.assertFalse(store.valid_app_id(bad), bad)

    def test_search_filters_and_limits_results(self):
        hits = [hit('org.a.One'), hit('bad id'), hit('org.b.Lib', type='runtime'), hit('org.c.Two', icon='http://evil.example/x.png'),
                *[hit('org.many.App%d' % n) for n in range(40)]]
        results = store.search('tux', opener=lambda *a, **k: Response({'hits': hits}))
        self.assertEqual(results[0]['app_id'], 'org.a.One')
        self.assertNotIn('bad id', [r['app_id'] for r in results])
        self.assertNotIn('org.b.Lib', [r['app_id'] for r in results])
        self.assertEqual(next(r for r in results if r['app_id'] == 'org.c.Two')['icon'], '')
        self.assertEqual(len(results), store.MAX_RESULTS)

    def test_search_rejects_bad_terms_and_bad_answers(self):
        for term in ('', 'a', 'x' * 81, 'tux\x00'):
            with self.assertRaises(store.StoreError):
                store.search(term, opener=lambda *a, **k: Response({'hits': []}))
        with self.assertRaises(store.StoreError):  # Umleitung auf einen fremden Host
            store.search('tux', opener=lambda *a, **k: Response({'hits': []}, url='https://evil.example/'))
        with self.assertRaises(store.StoreError):
            store.search('tux', opener=lambda *a, **k: Response(b'kein json'))
        with self.assertRaises(store.StoreError):
            store.search('tux', opener=lambda *a, **k: Response({'x': 1}))
        def offline(*args, **kwargs): raise urllib.error.URLError('kein Netz')
        with self.assertRaises(store.StoreError) as raised:
            store.search('tux', opener=offline)
        self.assertIn('Flathub', str(raised.exception))

    def test_search_sends_only_the_term_to_flathub(self):
        seen = []
        def opener(request, timeout):
            seen.append((request.full_url, request.data))
            return Response({'hits': []})
        store.search('  tux   paint ', opener=opener)
        self.assertEqual(seen, [('https://flathub.org/api/v2/search', json.dumps({'query': 'tux paint'}).encode())])

    def test_installed_lists_only_managed_apps(self):
        with tempfile.TemporaryDirectory() as folder:
            managed = Path(folder) / 'apps.list'
            managed.write_text('org.a.One\nnot valid\norg.c.Gone\n')
            run = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout='org.a.One\tEins\t1.2\norg.z.Other\tFremd\t9\n'))
            self.assertEqual(store.installed(run=run, path=managed), [{'app_id': 'org.a.One', 'name': 'Eins', 'version': '1.2'}])
            self.assertEqual(store.managed_ids(Path(folder) / 'fehlt'), [])

    def test_tile_id_is_safe_for_file_names(self):
        self.assertEqual(store.tile_id('org.Tux_Paint.Tuxpaint'), 'fp-org-tux-paint-tuxpaint')


class Runner:
    """Ersetzt subprocess.run: merkt sich Befehle und liefert vorbereitete Antworten."""
    def __init__(self, fail=(), remotes='flathub\npaimenos-flathub\n', installed=None):
        self.calls, self.fail, self.remotes = [], fail, remotes
        self.installed = installed if installed is not None else {}
    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        code, out = 0, ''
        if args[1:3] == ['remote-list', '--system']:
            out = self.remotes
        elif args[1:3] == ['list', '--system']:
            out = ''.join(f'{i}\t{n}\n' for i, n in self.installed.items())
        elif args[1:3] == ['info', '--system']:
            code = 0 if args[3].split('//')[0] in self.installed else 1
        if any(word in args for word in self.fail):
            code = 1
            if 'stdout' in kwargs:
                kwargs['stdout'].write(b'Fehlerdetail vom Befehl')
        return subprocess.CompletedProcess(args, code, stdout=out, stderr='')
    def commands(self, word): return [c for c in self.calls if word in c]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ('bin', 'home/.var/app'):
            (base / name).mkdir(parents=True)
        self.base = base
        self.runner = Runner(installed={'org.a.One': 'Eins'})
        self.jobs = self.make(self.runner)
    def tearDown(self): self.temp.cleanup()
    def make(self, runner):
        return service.FlatpakJobs(runner=runner, state_path=self.base / 'state.json', bin_dir=self.base / 'bin',
                                   managed_file=self.base / 'managed.list', kids_home=self.base / 'home', run_in_thread=False,
                                   updates_marker=self.base / 'updates-wanted')

    def test_install_runs_flatpak_writes_launcher_and_menu_entry(self):
        with patch.object(service.Path, 'exists', lambda self_: True if str(self_) == service.FLATPAK else Path.is_file(self_)):
            job = self.jobs.start('install', 'org.a.One')
        self.assertEqual(job['state'], 'done', job)
        install = self.runner.commands('install')[0]
        self.assertEqual(install[:6], [service.FLATPAK, 'install', '--system', '--noninteractive', '-y', service.REMOTE])
        self.assertEqual(install[-1], 'org.a.One//stable')
        launcher = (self.base / 'bin/paimenos-app-fp-org-a-one').read_text()
        self.assertIn("cli_flatpak org.a.One", launcher)
        self.assertEqual(os.stat(self.base / 'bin/paimenos-app-fp-org-a-one').st_mode & 0o777, 0o755)
        self.assertEqual((self.base / 'managed.list').read_text(), 'org.a.One\n')
        menu = self.runner.commands('cli_flatpak_menu')[0]
        self.assertEqual(menu[0], '/usr/bin/systemd-run')  # eigene Unit, nicht im ProtectHome-Sandkasten des Dienstes
        self.assertIn('--uid=kids', menu)
        self.assertEqual(menu[-3:], ['add', 'org.a.One', 'Eins'])
        self.assertEqual(job['percent'], 100)

    def test_app_updates_timer_is_enabled_only_when_updates_are_wanted(self):
        with patch.object(service.Path, 'exists', lambda self_: True if str(self_) == service.FLATPAK else Path.is_file(self_)):
            self.jobs.start('install', 'org.a.One')
            self.assertEqual(self.runner.commands('paimenos-app-update.timer'), [])
            (self.base / 'updates-wanted').write_text('')
            self.jobs.job.update(state='idle')
            self.jobs.start('install', 'org.a.One')
        timer = self.runner.commands('paimenos-app-update.timer')[0]
        self.assertEqual(timer[:3], ['/usr/bin/systemctl', 'enable', '--now'])

    def test_install_failure_reports_the_end_of_the_output(self):
        runner = Runner(fail=('install',))
        jobs = self.make(runner)
        with patch.object(service.Path, 'exists', lambda self_: True if str(self_) == service.FLATPAK else Path.is_file(self_)):
            job = jobs.start('install', 'org.a.One')
        self.assertEqual(job['state'], 'error')
        self.assertIn('Fehlerdetail', job['message'])
        self.assertFalse((self.base / 'managed.list').exists())
        self.assertEqual(runner.commands('cli_flatpak_menu'), [])  # keine Kachel ohne installierte App

    def test_flathub_is_added_when_missing(self):
        runner = Runner(remotes='flathub\n')
        jobs = self.make(runner)
        with patch.object(service.Path, 'exists', lambda self_: True if str(self_) == service.FLATPAK else Path.is_file(self_)):
            jobs.start('install', 'org.a.One')
        add = runner.commands('remote-add')[0]
        self.assertEqual(add[-2:], [service.REMOTE, service.REMOTE_URL])

    def test_missing_flatpak_is_installed_with_apt_first(self):
        with patch.object(service.Path, 'exists', lambda self_: False):
            self.jobs.start('install', 'org.a.One')
        apt = self.runner.commands('/usr/bin/apt-get')[0]
        self.assertIn('flatpak', apt)
        self.assertLess(self.runner.calls.index(apt), self.runner.calls.index(self.runner.commands('install')[-1]))

    def test_remove_removes_tile_first_then_app_launcher_and_list_entry(self):
        (self.base / 'managed.list').write_text('org.a.One\norg.b.Two\n')
        (self.base / 'bin/paimenos-app-fp-org-a-one').write_text("exec x cli_flatpak org.a.One \"$@\"\n")
        (self.base / 'bin/paimenos-app-fp-org-b-two').write_text("exec x cli_flatpak org.b.Two \"$@\"\n")
        data = self.base / 'home/.var/app/org.a.One'
        data.mkdir(); (data / 'save').write_text('x')
        job = self.jobs.start('remove', 'org.a.One', delete_data=False)
        self.assertEqual(job['state'], 'done', job)
        order = [c for c in self.runner.calls if 'cli_flatpak_menu' in c or 'uninstall' in c]
        self.assertIn('cli_flatpak_menu', order[0])
        self.assertEqual(order[0][-2:], ['remove', 'org.a.One'])
        self.assertEqual(order[1][1], 'uninstall')
        self.assertFalse((self.base / 'bin/paimenos-app-fp-org-a-one').exists())
        self.assertTrue((self.base / 'bin/paimenos-app-fp-org-b-two').exists())
        self.assertEqual((self.base / 'managed.list').read_text(), 'org.b.Two\n')
        self.assertTrue(data.exists())  # Daten bleiben standardmäßig erhalten

    def test_remove_can_delete_the_app_data(self):
        (self.base / 'managed.list').write_text('org.a.One\n')
        data = self.base / 'home/.var/app/org.a.One'
        data.mkdir(); (data / 'save').write_text('x')
        self.assertEqual(self.jobs.start('remove', 'org.a.One', delete_data=True)['state'], 'done')
        self.assertFalse(data.exists())

    def test_remove_never_follows_a_symlinked_data_folder(self):
        (self.base / 'managed.list').write_text('org.a.One\n')
        outside = self.base / 'outside'; outside.mkdir(); (outside / 'wichtig').write_text('x')
        (self.base / 'home/.var/app/org.a.One').symlink_to(outside)
        self.jobs.start('remove', 'org.a.One', delete_data=True)
        self.assertTrue((outside / 'wichtig').exists())

    def test_requests_are_validated(self):
        for kind, app_id in (('install', 'nicht gültig'), ('install', '../x.y.z'), ('format', 'org.a.One'), ('remove', 'org.a.One')):
            with self.assertRaises(ValueError):
                self.jobs.start(kind, app_id)  # 'remove': nicht von PaimenOS verwaltet
        self.assertEqual(self.runner.calls, [])

    def test_second_job_is_rejected_while_one_runs(self):
        self.jobs.job.update(state='running')
        with self.assertRaises(ValueError):
            self.jobs.start('install', 'org.a.One')

    def test_interrupted_job_is_reported_after_restart(self):
        (self.base / 'state.json').write_text(json.dumps({'state': 'running', 'kind': 'install', 'app_id': 'org.a.One', 'percent': 40}))
        again = self.make(self.runner)
        self.assertEqual(again.snapshot()['state'], 'error')


class MenuHelperTests(unittest.TestCase):
    def test_add_and_remove_tile(self):
        from paimenos import cli_flatpak_menu as menu
        from paimenos import parent
        with tempfile.TemporaryDirectory() as folder:
            apps = Path(folder) / 'apps.json'
            apps.write_text(json.dumps([{'id': 'xterm', 'title': 'Terminal', 'type': 'native', 'command': '/usr/bin/xterm'}]))
            location = Path(folder) / 'app'
            icon = location / 'files/share/icons/hicolor/128x128/apps/org.a.One.png'
            icon.parent.mkdir(parents=True); icon.write_bytes(b'png')
            info = subprocess.CompletedProcess([], 0, stdout=str(location) + '\n')
            with patch.object(parent, 'APPS_FILE', str(apps)), patch.object(parent, 'ICONS_DIR', str(Path(folder) / 'icons')), \
                    patch.object(parent, 'update_settings') as settings, patch.object(menu.subprocess, 'run', return_value=info):
                self.assertTrue(menu.add('org.a.One', '  Eins  '))
                self.assertFalse(menu.add('org.a.One', 'Eins'))  # Wiederholung legt nichts doppelt an
                items = json.loads(apps.read_text())
                tile = next(i for i in items if i.get('flatpak_id') == 'org.a.One')
                self.assertEqual((tile['id'], tile['title'], tile['install_source']), ('fp-org-a-one', 'Eins', 'flathub'))
                self.assertEqual(tile['command'], '/usr/local/bin/paimenos-app-fp-org-a-one')
                self.assertTrue((Path(folder) / 'icons' / tile['icon_file']).is_file())
                self.assertEqual(len(items), 2)
                self.assertTrue(menu.remove('org.a.One'))
                self.assertEqual([i['id'] for i in json.loads(apps.read_text())], ['xterm'])
                self.assertFalse((Path(folder) / 'icons' / tile['icon_file']).exists())
                settings.assert_called()
                self.assertFalse(menu.remove('org.a.One'))
            with self.assertRaises(ValueError):
                menu.add('kaputt', 'x')


@unittest.skipUnless(importlib.util.find_spec('flask'), 'Flask required')
class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from paimenos import paths
        cls.tmp = tempfile.TemporaryDirectory()
        with patch.object(paths, 'CONFIG_DIR', Path(cls.tmp.name)):
            from paimenos import parent_web
        cls.web = parent_web
        cls.web.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def setUp(self):
        self.client = self.web.app.test_client()
        p = patch.object(self.web, 'read_settings', return_value={'pin': '123456'})
        p.start(); self.addCleanup(p.stop)

    def auth(self):
        with self.client.session_transaction() as s:
            s.update(parent_authenticated=True, pin_fingerprint=self.web.pin_fingerprint(), csrf_token='csrf')

    def test_everything_needs_the_parent_login(self):
        self.assertEqual(self.client.get('/flatpaks').status_code, 302)
        self.assertEqual(self.client.get('/flatpaks/search?q=tux').status_code, 401)
        self.assertEqual(self.client.get('/flatpaks/status').status_code, 401)
        self.assertEqual(self.client.post('/flatpaks/install', data={'app_id': 'org.a.One'}).status_code, 400)  # kein CSRF
        self.auth()
        with patch.object(self.web.flatpak_store, 'installed', return_value=[]):
            page = self.client.get('/flatpaks')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Flatpak-Apps', page.get_data(as_text=True))
        self.assertIn('name="csrf_token"', page.get_data(as_text=True))

    def test_search_marks_installed_apps_and_reports_errors(self):
        self.auth()
        found = [{'app_id': 'org.a.One', 'name': 'Eins', 'summary': '', 'icon': ''}, {'app_id': 'org.b.Two', 'name': 'Zwei', 'summary': '', 'icon': ''}]
        with patch.object(self.web.flatpak_store, 'search', return_value=found), \
                patch.object(self.web.flatpak_store, 'installed', return_value=[{'app_id': 'org.a.One', 'name': 'Eins', 'version': ''}]):
            data = self.client.get('/flatpaks/search?q=tux').get_json()
        self.assertEqual([(r['app_id'], r['installed']) for r in data['results']], [('org.a.One', True), ('org.b.Two', False)])
        with patch.object(self.web.flatpak_store, 'search', side_effect=store.StoreError('Flathub ist nicht erreichbar.')):
            response = self.client.get('/flatpaks/search?q=tux')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Flathub', response.get_json()['error'])

    def test_install_and_remove_forward_only_valid_ids_to_the_service(self):
        self.auth()
        with patch.object(self.web, 'install_request', return_value={'ok': True, 'flatpak_job': {'state': 'running'}}) as request:
            self.assertEqual(self.client.post('/flatpaks/install', data={'csrf_token': 'csrf', 'app_id': 'org.a.One'}).status_code, 200)
            request.assert_called_once_with('flatpak_install', app_id='org.a.One')
            request.reset_mock()
            self.assertEqual(self.client.post('/flatpaks/install', data={'csrf_token': 'csrf', 'app_id': 'org.a.One; reboot'}).status_code, 400)
            request.assert_not_called()
            self.client.post('/flatpaks/remove', data={'csrf_token': 'csrf', 'app_id': 'org.a.One', 'delete_data': '1'})
            request.assert_called_once_with('flatpak_remove', app_id='org.a.One', delete_data=True)

    def test_status_combines_job_and_installed_list(self):
        self.auth()
        job = {'ok': True, 'flatpak_job': {'state': 'idle'}}
        with patch.object(self.web, 'install_request', return_value=job), patch.object(self.web.flatpak_store, 'installed', return_value=[]):
            data = self.client.get('/flatpaks/status').get_json()
        self.assertEqual((data['flatpak_job']['state'], data['installed']), ('idle', []))


if __name__ == '__main__':
    unittest.main()
