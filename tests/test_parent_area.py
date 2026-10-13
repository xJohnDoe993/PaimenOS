import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


@unittest.skipUnless(importlib.util.find_spec('flask'), 'Flask required')
class NetworkEditorRequestTests(unittest.TestCase):
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
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        for patcher in (patch.object(self.web, 'read_settings', return_value={'pin': '123456'}),
                        patch.object(self.web, 'STATE_DIR', Path(self.state.name))):
            patcher.start(); self.addCleanup(patcher.stop)
        self.request = Path(self.state.name) / self.web.NETWORK_EDITOR_REQUEST

    def auth(self):
        with self.client.session_transaction() as s:
            s.update(parent_authenticated=True, pin_fingerprint=self.web.pin_fingerprint(), csrf_token='csrf')

    def test_needs_login_and_csrf_token(self):
        with patch.object(self.web.shutil, 'which', return_value='/usr/bin/nm-connection-editor'):
            self.assertEqual(self.client.post('/wifi/advanced', data={'csrf_token': 'csrf'}).status_code, 400)  # keine Sitzung
            with self.client.session_transaction() as s:
                s['csrf_token'] = 'csrf'  # gültiges Token, aber nicht angemeldet
            self.assertEqual(self.client.post('/wifi/advanced', data={'csrf_token': 'csrf'}).status_code, 401)
            self.auth()
            self.assertEqual(self.client.post('/wifi/advanced').status_code, 400)  # angemeldet, aber ohne Token
        self.assertFalse(self.request.exists())

    def test_asks_the_menu_to_open_the_editor_on_the_device(self):
        self.auth()
        with patch.object(self.web.shutil, 'which', return_value='/usr/bin/nm-connection-editor'):
            response = self.client.post('/wifi/advanced', data={'csrf_token': 'csrf'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Netzwerkeditor', response.get_json()['message'])
        self.assertTrue(self.request.exists())

    def test_reports_a_missing_editor_without_leaving_a_request(self):
        self.auth()
        with patch.object(self.web.shutil, 'which', return_value=None):
            response = self.client.post('/wifi/advanced', data={'csrf_token': 'csrf'})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.request.exists())

    def test_wifi_page_offers_the_editor_button(self):
        self.auth()
        page = self.client.get('/wifi').get_data(as_text=True)
        self.assertIn('id="wifi-advanced"', page)


if __name__ == '__main__':
    unittest.main()
