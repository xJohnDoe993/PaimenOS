"""Offline German/English regression tests; optional native and web integration."""
import ast
from contextlib import ExitStack
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import string
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paimenos import i18n, state, parent, webapp


class LocaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT.parent)
        self.root = Path(self.temp.name)
        self.locale = self.root / 'locale.conf'
        self.other = self.root / 'default-locale'
        # Keep the production priority so a reversed order fails these tests.
        replacements = {Path('/etc/locale.conf'): self.locale,
                        Path('/etc/default/locale'): self.other}
        self.paths = patch.object(i18n, 'LOCALE_FILES', tuple(replacements[path] for path in i18n.LOCALE_FILES))
        self.paths.start()
        self.environment = patch.dict(os.environ, {'LANG': 'C.UTF-8'}, clear=True)
        self.environment.start()
        i18n.configured_locale.cache_clear()
    def tearDown(self):
        i18n.configured_locale.cache_clear()
        self.environment.stop(); self.paths.stop(); self.temp.cleanup()
    def set_locale(self, content):
        self.locale.write_text(content)
        i18n.configured_locale.cache_clear()
    def bash_language(self):
        source = (ROOT / 'installer/language.sh').read_text().replace('/etc/locale.conf', str(self.locale)).replace('/etc/default/locale', str(self.other))
        wrapper = self.root / 'language.sh'
        wrapper.write_text('set -euo pipefail\nREPO_DIR=' + repr(str(ROOT)) + '\n' + source + '\nprintf "%s\\n" "$PAIMENOS_LANGUAGE"\npaimenos_text "Auswahl: "\n')
        result = subprocess.run(['bash', str(wrapper)], env=dict(os.environ), capture_output=True, text=True, check=True)
        return result.stdout.splitlines()
    def test_english_system_wins_over_c_locale_in_services_and_sudo(self):
        for value, region in [('en_US.UTF-8', 'en-US'), ('en_GB.UTF-8', 'en-GB'), ('en_AU.UTF-8', 'en-AU')]:
            self.set_locale('LANG="' + value + '"\n')
            with self.subTest(value=value), patch.dict(os.environ, {'LC_ALL': 'C.UTF-8'}):
                self.assertEqual(i18n.language(), 'en')
                self.assertEqual(i18n.regional_locale(), region)
                self.assertEqual(i18n.t('Ausschalten'), 'Shut down')
                env = i18n.application_environment()
                self.assertEqual(env['LANG'], value)
                self.assertEqual(env['LC_MESSAGES'], value)
                self.assertEqual(env['LANGUAGE'], 'en')
                self.assertNotIn('LC_ALL', env)
    def test_message_precedence_german_and_explicit_override(self):
        self.set_locale('LANG=en_US.UTF-8\nLC_MESSAGES="de_DE.UTF-8"\n')
        self.assertEqual(i18n.language(), 'de')
        with patch.dict(os.environ, {'PAIMENOS_LANGUAGE': 'en'}):
            self.assertEqual(i18n.language(), 'en')
        self.set_locale('LANG=de_DE.UTF-8\nLC_MESSAGES=de_DE.UTF-8\nLC_ALL=en_GB.UTF-8\n')
        self.assertEqual(i18n.language(), 'en')
    def test_alternative_file_and_environment_fallback(self):
        self.other.write_text("export LANG='en_GB.UTF-8' # comment\n")
        self.assertEqual(i18n.language(), 'en')
        self.other.unlink(); i18n.configured_locale.cache_clear()
        with patch.dict(os.environ, {'LANG': 'en_US.UTF-8'}):
            self.assertEqual(i18n.language(), 'en')
        self.assertEqual(i18n.language(), 'de')
    def test_installed_locale_wins_over_stale_live_image_locale(self):
        for installed, legacy, language, region in (
                ('LANG=en_GB.UTF-8\n', 'LANG=de_DE.UTF-8\n', 'en', 'en-GB'),
                ('LANG=de_DE.UTF-8\n', 'LANG=en_US.UTF-8\nLC_ALL=en_US.UTF-8\n', 'de', 'de-DE')):
            with self.subTest(installed=installed), patch.dict(os.environ, {'LC_ALL': 'C.UTF-8'}):
                self.other.write_text(legacy)
                self.set_locale(installed)
                self.assertEqual(i18n.language(), language)
                self.assertEqual(i18n.regional_locale(), region)
                self.assertEqual(i18n.application_environment()['LC_MESSAGES'], installed.split('=', 1)[1].strip())
                self.assertEqual(self.bash_language(), [language, 'Selection: ' if language == 'en' else 'Auswahl: '])
    def test_empty_modern_locale_file_falls_back_to_debian_12_locale(self):
        self.other.write_text('LANG=en_AU.UTF-8\n')
        self.set_locale('# No message locale configured here\nLANG=""\n')
        self.assertEqual(i18n.language(), 'en')
        self.assertEqual(self.bash_language(), ['en', 'Selection: '])
    def test_locale_file_is_data_and_never_executed(self):
        marker = self.root / 'must-not-exist'
        self.set_locale('LANG="$(touch ' + str(marker) + ')"\n')
        self.assertEqual(i18n.language(), 'de')
        self.assertFalse(marker.exists())
    def test_bash_and_python_choose_same_language_before_python_installation(self):
        for content in ('LANG=en_US.UTF-8\n', "LANG='en_GB.UTF-8' # comment\n", 'LANG=de_DE.UTF-8\n',
                        'LANG=de_DE.UTF-8\nLC_MESSAGES=en_AU.UTF-8\n', 'LANG=en_GB.UTF-8\nLC_ALL=de_DE.UTF-8\n'):
            self.set_locale(content)
            lines = self.bash_language()
            self.assertEqual(lines[0], i18n.language())
            self.assertEqual(lines[1], 'Selection: ' if i18n.language() == 'en' else 'Auswahl: ')
    def test_shell_translation_subset_is_current(self):
        subprocess.run([sys.executable, '-B', str(ROOT / 'tools/build-shell-translations.py'), '--check'], check=True)


class TranslationTests(unittest.TestCase):
    def test_every_literal_translation_call_has_an_english_entry(self):
        catalog = i18n.english_catalog()
        for path in [*ROOT.joinpath('src/paimenos').glob('*.py'), *ROOT.joinpath('tools').glob('*.py'), ROOT / 'run.py', ROOT / 'iso/prepare-apt.py']:
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 't'
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    self.assertIn(node.args[0].value, catalog, str(path) + ':' + str(node.lineno))
    def test_format_placeholders_and_specs_match(self):
        formatter = string.Formatter()
        for source, translated in i18n.english_catalog().items():
            fields = lambda value: sorted((name, spec, conversion) for _, name, spec, conversion in formatter.parse(value) if name is not None)
            self.assertEqual(fields(source), fields(translated), source)
    def test_translations_keep_dynamic_values_and_custom_titles(self):
        with patch.dict(os.environ, {'PAIMENOS_LANGUAGE': 'en'}):
            self.assertEqual(i18n.t('Seite {value0} / {value1}', value0=2, value1=4), 'Page 2 / 4')
            self.assertEqual(parent.duration(65), '1 min 05 sec')
            camera = dict(id='camera', type='camera', command='__CAMERA__', title='Kamera / Bilder / Videos', enabled=False)
            self.assertEqual(state.app_with_current_title(camera)['title'], 'Camera / Pictures / Videos')
            self.assertEqual(state.app_with_current_title(dict(camera, title='Meine Videos'))['title'], 'Meine Videos')
            self.assertEqual(camera['title'], 'Kamera / Bilder / Videos')
            power = dict(id='poweroff', command='__POWEROFF__', title='Ausschalten')
            self.assertEqual(state.app_with_current_title(power)['title'], 'Shut down')
    def test_new_default_webapps_are_language_specific_and_have_offline_icons(self):
        lists = {}
        for language in ('de', 'en'):
            env = dict(os.environ, PAIMENOS_LANGUAGE=language)
            output = subprocess.check_output([sys.executable, str(ROOT / 'tools/default-webapps.py')], env=env, text=True)
            apps = json.loads('[' + output + ']'); lists[language] = apps
            self.assertEqual(len({a['id'] for a in apps}), len(apps))
        self.assertEqual(lists['de'], json.loads((ROOT / 'data/default-webapps.json').read_text()))
        self.assertEqual({a['id'] for a in lists['en']}, {'pbs-kids','learnenglish-kids','nasa-space-place','blockly-games','scratch-web'})
        for app in lists['en']:
            self.assertTrue(app['url'].startswith('https://'))
            self.assertTrue((ROOT / 'assets/icons' / app['icon_file']).is_file())
            self.assertEqual(app['profile'], app['id'])
    def test_firefox_language_changes_without_losing_profile_data(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as folder:
            profile = Path(folder)
            (profile / 'user.js').write_text('user_pref("example", true);\n')
            saved = profile / 'cookies.sqlite'; saved.write_bytes(b'saved login data')
            for language in ('en', 'de', 'en'):
                with patch.dict(os.environ, {'PAIMENOS_LANGUAGE': language}):
                    webapp.prepare_profile(folder, str(profile / 'missing-template'))
                content = (profile / 'user.js').read_text()
                self.assertIn('user_pref("intl.locale.requested", "' + language + '");', content)
                self.assertEqual(content.count('intl.locale.requested'), 1)
                self.assertIn('user_pref("example", true);', content)
                self.assertEqual(saved.read_bytes(), b'saved login data')
                style = (profile / 'chrome/paimenos-webapp.css').read_text()
                self.assertIn('--paimenos-back-label: "' + ('Back' if language == 'en' else 'Zurück') + '"', style)
                self.assertIn('--paimenos-forward-label: "' + ('Forward' if language == 'en' else 'Weiter') + '"', style)


class PageText(HTMLParser):
    def __init__(self): super().__init__(); self.depth = 0; self.text = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.depth += 1
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.depth -= 1
    def handle_data(self, data):
        if not self.depth: self.text.append(data)


@unittest.skipUnless(importlib.util.find_spec('flask'), 'Flask unavailable')
class BackendLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT.parent)
        from paimenos import paths
        with patch.object(paths, 'CONFIG_DIR', Path(cls.temp.name)):
            from paimenos import parent_web
        cls.web = parent_web
        cls.web.app.config['TESTING'] = True
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    def test_template_and_browser_translation_calls_have_english_entries(self):
        from jinja2 import Environment, nodes
        catalog = i18n.english_catalog()
        for path in (ROOT / 'assets/parent-web').glob('*.html'):
            source = path.read_text()
            for node in Environment().parse(source).find_all(nodes.Call):
                if isinstance(node.node, nodes.Name) and node.node.name == 't' and node.args and isinstance(node.args[0], nodes.Const):
                    self.assertIn(node.args[0].value, catalog, str(path))
            for match in re.finditer(r'''PaimenTranslate\(((?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"))''', source):
                self.assertIn(ast.literal_eval(match[1]), catalog, str(path))
    def test_pages_render_and_javascript_parses_in_both_languages(self):
        data = dict(state.DEFAULTS, pin='456789', daily_limit_minutes=30, today_used_seconds=65)
        apps = [dict(id='camera',type='camera',command='__CAMERA__',title='My camera'),
                dict(id='test',type='webapp',title='My app',url='https://example.org/')]
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.web, 'read_settings', return_value=data))
            stack.enter_context(patch.object(parent, 'read_settings', return_value=data))
            stack.enter_context(patch.object(parent, 'read_apps', return_value=apps))
            stack.enter_context(patch.object(self.web, 'collect_diagnostics', return_value={}))
            stack.enter_context(patch.object(self.web, 'diagnostics_text', return_value='test report'))
            stack.enter_context(patch.object(self.web.emulators, 'emulator_log', return_value='test log'))
            client = self.web.app.test_client()
            for language in ('de','en'):
                with patch.dict(os.environ, {'PAIMENOS_LANGUAGE': language}):
                    login = client.get('/login')
                    parser = PageText(); parser.feed(login.get_data(as_text=True))
                    self.assertIn('Parent area' if language == 'en' else 'Elternbereich', ''.join(parser.text))
                    with client.session_transaction() as session:
                        session.update(parent_authenticated=True, pin_fingerprint=self.web.pin_fingerprint(), csrf_token='test-token')
                    for route in ('/','/apps','/apps/new','/apps/camera/edit','/apps/test/delete','/time','/settings','/wifi','/bluetooth','/controllers','/updates','/emulators','/diagnostics','/emulators/diagnostics'):
                        with self.subTest(language=language,route=route):
                            response = client.get(route)
                            self.assertEqual(response.status_code, 200)
                            html = response.get_data(as_text=True)
                            self.assertIn('lang="' + language + '"', html)
                            self.assertNotIn('{{', html)
                            parser = PageText(); parser.feed(html); visible = ''.join(parser.text)
                            if language == 'en':
                                self.assertNotRegex(visible, r'Elternbereich|Bildschirmzeit|Einstellungen|Gespeicherte|Geräte|Übersicht|Abmelden|Zeit verfügbar|Min\.|Sek\.')
                            if shutil.which('node'):
                                for script in re.findall(r'<script[^>]*>(.*?)</script>', html, re.S):
                                    with tempfile.NamedTemporaryFile(mode='w',suffix='.js',dir=ROOT.parent) as file:
                                        file.write(script);file.flush()
                                        subprocess.run(['node','--check',file.name],check=True,capture_output=True)
                    self.assertEqual(client.post('/updates/check',data={}).status_code, 400)
                    self.assertEqual(client.get('/missing-route').status_code, 404)
            with patch.dict(os.environ, {'PAIMENOS_LANGUAGE':'en'}):
                client.post('/logout',data={'csrf_token':'test-token'})
                denied=client.get('/wifi/status')
                self.assertEqual(denied.status_code,401)
                self.assertEqual(denied.get_json()['error'],'Please sign in to the parent area.')


@unittest.skipUnless(importlib.util.find_spec('PyQt5'), 'Qt unavailable')
class NativeLanguageTests(unittest.TestCase):
    def test_native_qt_locale_uses_system_selection(self):
        # No X server, device access or application services are needed.
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt5.QtCore import QLocale
        from PyQt5.QtWidgets import QApplication, QDialogButtonBox
        app = QApplication.instance() or QApplication([])
        for language, cancel in (('de', 'Abbrechen'), ('en', 'Cancel'), ('de', 'Abbrechen')):
            with patch.dict(os.environ, {'PAIMENOS_LANGUAGE': language}), self.subTest(language=language):
                i18n.setup_qt(app)
                self.assertTrue(QLocale().name().startswith(language + '_'))
                buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
                self.assertEqual(buttons.button(QDialogButtonBox.Cancel).text(), cancel)
