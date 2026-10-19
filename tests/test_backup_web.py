import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('flask'), 'Flask required')
class BackupWebTests(unittest.TestCase):
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
        from paimenos import backups
        self.b = backups
        self.client = self.web.app.test_client()
        p = patch.object(self.web, 'read_settings', return_value={'pin': '123456'})
        p.start(); self.addCleanup(p.stop)

    def auth(self):
        with self.client.session_transaction() as s:
            s.update(parent_authenticated=True, pin_fingerprint=self.web.pin_fingerprint(), csrf_token='csrf')

    def test_parent_auth_csrf_and_restore_confirmation(self):
        with patch.object(self.b, 'start') as start:
            self.assertEqual(self.client.get('/backups').status_code, 302)
            self.assertEqual(self.client.get('/backups/status').status_code, 302)
            self.auth()
            self.assertEqual(self.client.post('/backups', data={'action':'create'}).status_code, 400)
            self.client.post('/backups', data={'csrf_token':'csrf','action':'restore','groups':'saves'})
            start.assert_not_called()
            self.client.post('/backups', data={'csrf_token':'csrf','action':'restore','groups':'saves','confirm':'yes','device':'usb','backup':'backup-id'})
            start.assert_called_once_with('restore', 'usb', ['saves'], 'backup-id', None)

    def test_page_exposes_selection_and_escapes_device_label(self):
        self.auth()
        with patch.object(self.b, 'drives', return_value=[{'id':'usb','label':'<script>bad</script>','free':1024}]), patch.object(self.b, 'available', return_value=[{'id':'backup-id','date':'today','groups':['saves'],'size':1}]):
            page = self.client.get('/backups').get_data(as_text=True)
        self.assertIn('name="groups"', page)
        self.assertIn('name="confirm"', page)
        self.assertIn('name="csrf_token"', page)
        self.assertNotIn('<script>bad</script>', page)

    def test_page_offers_app_groups_and_personal_files_unchecked(self):
        self.auth()
        with patch.object(self.b, 'drives', return_value=[{'id':'usb','label':'Stick','free':1024}]), \
                patch.object(self.b, 'available', return_value=[{'id':'backup-id','date':'today','groups':['app-minetest'],'size':1}]), \
                patch.object(self.b, 'offered', return_value=['roms', 'saves', 'bios', 'app-tuxpaint', 'files']):
            page = self.client.get('/backups').get_data(as_text=True)
        self.assertIn('value="app-tuxpaint" checked>Tux Paint', page)
        self.assertIn('value="app-minetest" checked>Luanti (Minetest)', page)
        self.assertIn('value="files">', page)
        self.assertNotIn('value="app-gimp"', page)
