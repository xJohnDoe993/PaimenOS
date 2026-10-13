"""Squid ist entfernt: keine Proxy-Einstellungen mehr, bestehende Geräte werden bereinigt."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def load_deploy():
    spec = importlib.util.spec_from_file_location('paimenos_deploy', ROOT / 'tools/deploy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SquidRemovalTests(unittest.TestCase):
    def test_no_squid_in_setup_packages_or_browser_template(self):
        for relative in ('installer/10-system.sh', 'installer/50-browser.sh', 'installer/offline-packages.txt'):
            self.assertNotIn('squid', (ROOT / relative).read_text().lower(), relative)
        self.assertFalse((ROOT / 'config/squid').exists())
        user_js = (ROOT / 'config/firefox/user.js').read_text()
        self.assertIn('user_pref("network.proxy.type", 0);', user_js)
        self.assertNotIn('3128', user_js)

    def test_retire_squid_stops_the_service_only_when_it_exists(self):
        deploy = load_deploy()
        run = MagicMock()
        with patch.object(deploy.Path, 'is_file', return_value=False):
            deploy.retire_squid(run)
        run.assert_not_called()
        with patch.object(deploy.Path, 'is_file', return_value=True):
            deploy.retire_squid(run)
        run.assert_called_once_with(['systemctl', 'disable', '--now', 'squid.service'], check=False, capture_output=True)

    def test_old_profile_templates_lose_the_proxy_and_turn_it_off(self):
        from paimenos import webapp
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            template = folder / 'paimenos-user.js'
            template.write_text('user_pref("network.proxy.type", 1);\nuser_pref("network.proxy.http", "127.0.0.1");\n'
                                'user_pref("network.proxy.http_port", 3128);\nuser_pref("network.proxy.ssl", "127.0.0.1");\n'
                                'user_pref("network.proxy.no_proxies_on", "localhost, 127.0.0.1");\n'
                                'user_pref("network.dnsCacheEntries", 1000);\n')
            profile = folder / 'profile'
            with patch.object(webapp, 'ASSETS_DIR', ROOT / 'assets'):
                webapp.prepare_profile(str(profile), str(template))
            text = (profile / 'user.js').read_text()
        self.assertNotIn('3128', text)
        self.assertNotIn('network.proxy.http', text)
        self.assertEqual(text.count('network.proxy.type'), 1)
        self.assertIn('user_pref("network.proxy.type", 0);', text)
        self.assertIn('network.dnsCacheEntries', text)


if __name__ == '__main__':
    unittest.main()
