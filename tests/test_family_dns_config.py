"""Familien-DNS: Systemresolver und Firefox-DoH zeigen auf filternde Server."""
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
FAMILY_SERVERS = {'1.1.1.3', '1.0.0.3', '94.140.14.15', '94.140.15.16'}


class FamilyDnsConfigTests(unittest.TestCase):
    def test_system_resolver_uses_only_family_servers_for_all_domains(self):
        text = (ROOT / 'config/resolved/paimenos-family-dns.conf').read_text()
        servers = set(re.search(r'^DNS=(.*)$', text, re.M).group(1).split())
        self.assertEqual(servers, FAMILY_SERVERS)
        self.assertIn('Domains=~.', text)
        self.assertRegex(text, r'(?m)^FallbackDNS=$')

    def test_firefox_uses_a_locked_family_doh_provider_with_fallback(self):
        doh = json.loads((ROOT / 'data/system-config/firefox/policies.json').read_text())['policies']['DNSOverHTTPS']
        self.assertTrue(doh['Enabled'])
        self.assertTrue(doh['Locked'])
        self.assertTrue(doh['Fallback'])
        self.assertIn(doh['ProviderURL'], {'https://family.adguard-dns.com/dns-query',
                                           'https://family.cloudflare-dns.com/dns-query'})

    def test_cookie_banners_are_handled_by_ublock_lists_and_firefox(self):
        policies = json.loads((ROOT / 'data/system-config/firefox/policies.json').read_text())['policies']
        lists = policies['3rdparty']['Extensions']['uBlock0@raymondhill.net']['toOverwrite']['filterLists']
        self.assertIn('fanboy-cookiemonster', lists)
        self.assertIn('ublock-cookies-easylist', lists)
        self.assertIn('ublock-filters', lists)  # Standardlisten bleiben aktiv
        self.assertIn('user_pref("cookiebanners.service.mode", 1);', (ROOT / 'config/firefox/user.js').read_text())
        from paimenos import webapp
        self.assertIn('cookiebanners.service.mode", 1', webapp.PREFERENCES)


if __name__ == '__main__':
    unittest.main()


class FirefoxPolicyHardeningTests(unittest.TestCase):
    def test_kids_cannot_open_local_files_or_store_logins(self):
        policies = json.loads((ROOT / 'data/system-config/firefox/policies.json').read_text())['policies']
        self.assertIn('file:///*', policies['WebsiteFilter']['Block'])
        for key in ('BlockAboutAddons', 'BlockAboutSupport', 'DisableAccounts', 'DisableFormHistory'):
            self.assertIs(policies[key], True)
        for key in ('PasswordManagerEnabled', 'OfferToSaveLogins'):
            self.assertIs(policies[key], False)


class FirefoxAutoconfigTests(unittest.TestCase):
    def test_autoconfig_and_cfg_are_synced_together(self):
        pref = (ROOT / 'data/system-config/firefox/paimenos-autoconfig.js').read_text()
        cfg = (ROOT / 'data/system-config/firefox/paimenos.cfg').read_text()
        files = json.loads((ROOT / 'data/system-config.json').read_text())['files']
        # Zeigt die Pref auf eine fehlende Datei, startet Firefox nur mit "Configuration Error".
        self.assertIn('pref("general.config.filename", "paimenos.cfg");', pref)
        self.assertTrue(cfg.startswith('//'))
        pair = [entry for entry in files if entry['source'].startswith('firefox/paimenos')]
        self.assertEqual([entry['source'] for entry in pair], ['firefox/paimenos.cfg', 'firefox/paimenos-autoconfig.js'])
        self.assertEqual({entry['group'] for entry in pair}, {'autoconfig'})

    def test_parent_profile_keeps_its_shortcuts(self):
        self.assertIn("'parent-area'", (ROOT / 'data/system-config/firefox/paimenos.cfg').read_text())
