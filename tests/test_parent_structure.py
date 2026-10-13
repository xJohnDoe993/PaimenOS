import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unittest.mock import patch

from paimenos.bluetooth import BluetoothController, ADAPTER, DEVICE



class FakeDriver:
    def __init__(self, objects):
        self._objects = objects

    def objects(self):
        return self._objects


class BluetoothNameTests(unittest.TestCase):
    def test_devices_report_whether_bluez_knows_a_real_name(self):
        objects = {
            '/org/bluez/hci0': {ADAPTER: {'Alias': 'Laptop', 'Powered': True}},
            '/org/bluez/hci0/dev_1': {DEVICE: {'Adapter': '/org/bluez/hci0', 'Address': 'AA:BB:CC:DD:EE:01',
                                               'Alias': 'Xbox Wireless Controller', 'Name': 'Xbox Wireless Controller'}},
            '/org/bluez/hci0/dev_2': {DEVICE: {'Adapter': '/org/bluez/hci0', 'Address': 'AA:BB:CC:DD:EE:02',
                                               'Alias': 'AA-BB-CC-DD-EE-02'}},
        }
        devices = {d['address']: d for d in BluetoothController(FakeDriver(objects)).snapshot()['devices']}
        self.assertTrue(devices['AA:BB:CC:DD:EE:01']['named'])
        self.assertFalse(devices['AA:BB:CC:DD:EE:02']['named'])
        self.assertEqual(devices['AA:BB:CC:DD:EE:02']['name'], 'AA-BB-CC-DD-EE-02')


@unittest.skipUnless(importlib.util.find_spec('flask'), 'Flask required')
class WebStructureTests(unittest.TestCase):
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
        with self.client.session_transaction() as s:
            s.update(parent_authenticated=True, pin_fingerprint=self.web.pin_fingerprint(), csrf_token='csrf')

    def test_navigation_is_grouped_child_connections_system(self):
        page = self.client.get('/bluetooth').get_data(as_text=True)
        nav = page[page.index('<nav'):page.index('</nav>')]
        order = [nav.index(href) for href in ('href="/"', '/time', '/apps', '/emulators', '/wifi', '/bluetooth',
                                               '/controllers', '/backups', '/updates', '/settings', '/diagnostics')]
        self.assertEqual(order, sorted(order))
        self.assertEqual(nav.count('class="nav-sep"'), 2)
        self.assertIn('id="bt-hide-unnamed" checked', page)


if __name__ == '__main__':
    unittest.main()
