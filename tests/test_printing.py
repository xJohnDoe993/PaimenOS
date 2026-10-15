"""Drucken mit Elternfreigabe: CUPS-Vorgaben, Freigabe, Eingaben und Elternbereich ohne echtes CUPS."""
import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paimenos import printing


class FakeDriver:
    def __init__(self):
        self.queues = {'Laser': dict(info='Laser Flur', model='Brother', uri='ipp://192.168.1.20/ipp/print', state=3, message='',
                                     accepting=True, shared=True, hold_default='no-hold')}
        self.active, self.done, self.calls, self.default_name = {}, {}, [], 'Laser'

    def printers(self):
        return {name: dict(value) for name, value in self.queues.items()}

    def default(self):
        return self.default_name

    def jobs(self, completed=False):
        return dict(self.done if completed else self.active)

    def job(self, number, user='kids', state=4):
        self.active[number] = dict(title='Bild', printer='Laser', user=user, state=state, created=1, completed=0,
                                   size_kb=12, pages=1, reason='')

    def accept(self, name, enabled):
        self.calls.append(('accept', name, enabled)); self.queues[name]['accepting'] = enabled

    def set_hold_default(self, name, value):
        self.calls.append(('hold_default', name, value)); self.queues[name]['hold_default'] = value

    def unshare(self, name):
        self.calls.append(('unshare', name)); self.queues[name]['shared'] = False

    def hold(self, number):
        self.calls.append(('hold', number)); self.active[number]['state'] = 4

    def release(self, number):
        self.calls.append(('release', number)); self.active[number]['state'] = 3

    def cancel(self, number):
        self.calls.append(('cancel', number)); del self.active[number]

    def has_document(self, number):
        return number in self.active

    def document(self, number):
        return b'%PDF-1.7 test', 'application/pdf'

    def add(self, name, title, uri, device=None):
        self.calls.append(('add', name, title, uri))
        self.queues[name] = dict(info=title, model='', uri=uri, state=3, message='', accepting=True, shared=False, hold_default='no-hold')

    def remove(self, name):
        self.calls.append(('remove', name)); del self.queues[name]

    def set_default(self, name):
        self.default_name = name

    def test_page(self, name):
        self.calls.append(('test', name))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'printing/settings.json'
        self.driver = FakeDriver()
        self.controller = printing.PrintController(self.driver, self.path)
        self.addCleanup(self.temp.cleanup)

    def test_printing_is_off_until_parents_allow_it(self):
        self.assertEqual(printing.print_mode(self.path), 'off')
        self.controller.enforce()
        self.assertIn(('accept', 'Laser', False), self.driver.calls)
        self.assertIn(('unshare', 'Laser'), self.driver.calls)

    def test_approval_mode_holds_new_jobs_in_cups_and_catches_jobs_that_bypass_it(self):
        self.controller.handle(dict(action='mode', mode='approval'))
        self.assertEqual(printing.print_mode(self.path), 'approval')
        self.assertEqual(self.driver.queues['Laser']['hold_default'], 'indefinite')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)
        self.driver.job(7, state=3)
        self.driver.job(8, user='root', state=3)  # Testseite der Eltern
        self.controller.enforce()
        self.assertIn(('hold', 7), self.driver.calls)
        self.assertNotIn(('hold', 8), self.driver.calls)

    def test_released_jobs_are_not_held_again_and_forgotten_when_finished(self):
        self.controller.handle(dict(action='mode', mode='approval'))
        self.driver.job(7)
        state = self.controller.handle(dict(action='release', job='7'))
        self.assertEqual(state['message'], printing.t('Druckauftrag freigegeben.'))
        self.controller.enforce()
        self.assertNotIn(('hold', 7), self.driver.calls)
        self.assertEqual(json.loads(self.path.read_text())['released'], [7])
        del self.driver.active[7]
        self.controller.enforce()
        self.assertEqual(json.loads(self.path.read_text())['released'], [])

    def test_direct_mode_prints_without_hold_and_jobs_stay_visible(self):
        self.controller.handle(dict(action='mode', mode='direct'))
        self.assertEqual(self.driver.queues['Laser']['hold_default'], 'no-hold')
        self.driver.job(9, state=3)
        self.controller.enforce()
        self.assertNotIn(('hold', 9), self.driver.calls)
        state = self.controller.snapshot()
        self.assertEqual([(job['id'], job['state'], job['printer_title']) for job in state['jobs']], [(9, 'pending', 'Laser Flur')])

    def test_invalid_requests_are_rejected(self):
        for data in (dict(action='mode', mode='always'), dict(action='release', job='1; rm'), dict(action='release', job=True),
                     dict(action='release', job='5'), dict(action='remove', printer='Other'), dict(action='shell')):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.controller.handle(data)

    def test_add_printer_derives_a_safe_queue_name_and_becomes_default_when_first(self):
        self.driver.default_name = ''
        self.controller.handle(dict(action='add', title='Drucker Küche', uri='192.168.1.30'))
        self.assertIn(('add', 'Drucker-Kuche', 'Drucker Küche', 'ipp://192.168.1.30/ipp/print'), self.driver.calls)
        self.assertEqual(self.driver.default_name, 'Drucker-Kuche')
        # Neue Warteschlangen bekommen sofort die Vorgabe des Modus.
        self.assertFalse(self.driver.queues['Drucker-Kuche']['accepting'])

    def test_test_page_needs_printing_allowed(self):
        with self.assertRaises(ValueError):
            self.controller.handle(dict(action='test', printer='Laser'))
        self.controller.handle(dict(action='mode', mode='approval'))
        self.controller.handle(dict(action='test', printer='Laser'))
        self.assertIn(('test', 'Laser'), self.driver.calls)

    def test_document_is_returned_for_parent_preview(self):
        self.driver.job(4)
        state = self.controller.handle(dict(action='document', job=4))
        self.assertEqual(base64.b64decode(state['data']), b'%PDF-1.7 test')
        self.assertEqual(state['format'], 'application/pdf')


class QueueTests(unittest.TestCase):
    def test_menu_reads_open_jobs_without_asking_the_service(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'queue.json'
            self.assertIsNone(printing.queue_status(path))
            driver = FakeDriver()
            controller = printing.PrintController(driver, Path(folder) / 'settings.json', path)
            driver.job(3, state=4); driver.job(4, state=5)
            controller.enforce()
            self.assertEqual(printing.queue_status(path), (2, 1))
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            driver.active.clear()
            controller.enforce()
            self.assertEqual(printing.queue_status(path), (0, 0))
            path.write_text('{"open": -1, "held": 0}')
            self.assertIsNone(printing.queue_status(path))


class InputTests(unittest.TestCase):
    def test_only_printer_addresses_are_accepted(self):
        self.assertEqual(printing.validate_uri('drucker.local'), 'ipp://drucker.local/ipp/print')
        for uri in ('ipps://10.0.0.5/ipp/print', 'dnssd://Brother%20HL._ipp._tcp.local/?uuid=1', 'usb://HP/DeskJet?serial=1',
                    'socket://10.0.0.5:9100'):
            self.assertEqual(printing.validate_uri(uri), uri)
        for uri in ('file:///etc/shadow', 'cups-pdf:/', 'ipp://', "ipp://x/$(touch y)'", 'ipp://host/ a', 'smb://server/printer', 7):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                printing.validate_uri(uri)

    def test_queue_names_are_unique(self):
        self.assertEqual(printing.queue_name('Laser', ['laser', 'Laser-2']), 'Laser-3')
        self.assertEqual(printing.queue_name('???'), 'Drucker')

    def test_credentials_in_addresses_are_hidden(self):
        self.assertEqual(printing.display_uri('ipp://user:secret@host/ipp/print'), 'ipp://host/ipp/print')

    def test_document_formats_are_detected_from_content(self):
        self.assertEqual(printing.sniff_format(b'%PDF-1.4'), 'application/pdf')
        self.assertEqual(printing.sniff_format(b'%!PS-Adobe-3.0'), 'application/postscript')
        self.assertEqual(printing.sniff_format(b'<html>'), 'application/octet-stream')


class FirefoxTests(unittest.TestCase):
    def test_child_profile_offers_printing_only_when_allowed(self):
        from paimenos import webapp
        for mode, expected in (('off', False), ('approval', True), ('direct', True)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder, patch.object(webapp, 'print_mode', return_value=mode):
                webapp.prepare_profile(folder, str(Path(folder) / 'missing-template'))
                config = (Path(folder) / 'user.js').read_text()
                self.assertEqual('user_pref("print.enabled", true);' in config, expected)

    def test_policy_no_longer_locks_printing_and_old_devices_are_migrated(self):
        self.assertNotIn('PrintingEnabled', json.loads((ROOT / 'config/firefox/policies.json').read_text())['policies'])
        spec = importlib.util.spec_from_file_location('paimenos_deploy', ROOT / 'tools/deploy.py')
        deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'policies.json'
            path.write_text(json.dumps({'policies': {'PrintingEnabled': False, 'BlockAboutConfig': True}}))
            deploy.allow_firefox_printing(path)
            self.assertEqual(json.loads(path.read_text()), {'policies': {'BlockAboutConfig': True}})
            deploy.allow_firefox_printing(Path(folder) / 'missing.json')


class SetupTests(unittest.TestCase):
    def test_service_packages_and_launcher_are_wired(self):
        self.assertIn('cups', (ROOT / 'installer/10-system.sh').read_text())
        self.assertIn('python3-cups', json.loads((ROOT / 'data/update-packages.json').read_text())['components']['shared'])
        self.assertIn('paimenos-print.service', (ROOT / 'installer/80-services.sh').read_text())
        unit = (ROOT / 'systemd/system/paimenos-print.service').read_text()
        self.assertIn('run.py printing', unit)
        self.assertIn('StateDirectory=paimenos/printing', unit)


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
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.client = self.web.app.test_client()
        p = patch.object(self.web, 'read_settings', return_value={'pin': '123456'})
        p.start(); self.addCleanup(p.stop)

    def login(self):
        with self.client.session_transaction() as s:
            s.update(parent_authenticated=True, pin_fingerprint=self.web.pin_fingerprint(), csrf_token='csrf')

    def test_requires_login(self):
        self.assertEqual(self.client.get('/printing/status').status_code, 401)
        self.assertEqual(self.client.get('/printing').status_code, 302)
        self.assertEqual(self.client.get('/printing/jobs/3/document').status_code, 302)

    def test_actions_forward_only_their_own_fields(self):
        self.login()
        with patch.object(self.web, 'print_request', return_value={'mode': 'approval'}) as request:
            response = self.client.post('/printing/action', data={'csrf_token': 'csrf', 'action': 'release', 'job': '3', 'mode': 'direct'})
            self.assertEqual(response.status_code, 200)
            request.assert_called_once_with('release', job='3')
            self.assertEqual(self.client.post('/printing/action', data={'csrf_token': 'csrf', 'action': 'lpadmin'}).status_code, 400)
            self.assertEqual(self.client.post('/printing/action', data={'action': 'release', 'job': '3'}).status_code, 400)

    def test_document_is_shown_inline_only_for_safe_formats(self):
        self.login()
        with patch.object(self.web, 'print_document', return_value=(b'%PDF', 'application/pdf', 'Bild.pdf', True)):
            response = self.client.get('/printing/jobs/3/document')
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertTrue(response.headers['Content-Disposition'].startswith('inline'))
        with patch.object(self.web, 'print_document', side_effect=printing.PrintError('weg')):
            self.assertEqual(self.client.get('/printing/jobs/3/document').status_code, 404)

    def test_print_document_names_and_types_come_from_the_service(self):
        state = dict(data=base64.b64encode(b'%!PS').decode(), format='application/postscript', title='Mein Bild "x".png')
        with patch.object(printing, 'print_request', return_value=state):
            self.assertEqual(printing.print_document(3), (b'%!PS', 'application/postscript', 'Mein-Bild-x-.png.ps', False))
        with patch.object(printing, 'print_request', return_value=dict(state, format='text/html')):
            self.assertEqual(printing.print_document(3)[1], 'application/octet-stream')


if __name__ == '__main__':
    unittest.main()
