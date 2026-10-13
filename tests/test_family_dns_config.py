"""Familien-DNS: Systemresolver und Firefox-DoH zeigen auf filternde Server."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAMILY_SERVERS = {'1.1.1.3', '1.0.0.3', '94.140.14.15', '94.140.15.16'}


class FamilyDnsConfigTests(unittest.TestCase):
    def test_system_resolver_uses_only_family_servers_for_all_domains(self):
        text = (ROOT / 'config/resolved/paimenos-family-dns.conf').read_text()
        servers = set(re.search(r'^DNS=(.*)$', text, re.M).group(1).split())
        self.assertEqual(servers, FAMILY_SERVERS)
        self.assertIn('Domains=~.', text)
        self.assertRegex(text, r'(?m)^FallbackDNS=$')

    def test_firefox_uses_a_locked_family_doh_provider_with_fallback(self):
        doh = json.loads((ROOT / 'config/firefox/policies.json').read_text())['policies']['DNSOverHTTPS']
        self.assertTrue(doh['Enabled'])
        self.assertTrue(doh['Locked'])
        self.assertTrue(doh['Fallback'])
        self.assertIn(doh['ProviderURL'], {'https://family.adguard-dns.com/dns-query',
                                           'https://family.cloudflare-dns.com/dns-query'})


if __name__ == '__main__':
    unittest.main()
