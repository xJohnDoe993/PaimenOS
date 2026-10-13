import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('iso_apt', ROOT / 'iso/prepare-apt.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OfflineTests(unittest.TestCase):
    def test_offline_config_uses_only_the_signed_medium_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'apt.conf'
            module.offline_config(config, '/run/paimenos-medium/paimenos-repo', state=root / 'state')
            text = config.read_text()
            self.assertIn('Dir::Etc::sourcelist "/dev/null";', text)
            self.assertIn(f'Dir::Etc::sourceparts "{root}/state/sources.list.d";', text)
            self.assertIn(f'Dir::State::lists "{root}/state/lists";', text)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            source = (root / 'state/sources.list.d/paimenos-offline.sources').read_text()
            self.assertIn('URIs: file:/run/paimenos-medium/paimenos-repo', source)
            self.assertIn('Suites: ./', source)
            self.assertNotIn('Components', source)  # flaches Repo: apt-cdrom des Installers darf kein dists/ finden
            self.assertIn('Signed-By: /run/paimenos-medium/paimenos-repo/paimenos-repo.gpg', source)
            self.assertNotIn('http', source)
            self.assertTrue((root / 'state/lists/partial').is_dir())


class AptTests(unittest.TestCase):
    def test_cdrom_only_system_gets_main_and_security_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apt = root / 'etc/apt'
            apt.mkdir(parents=True)
            source = apt / 'sources.list'
            original = 'deb cdrom:[Debian 13 LIVE/INSTALL]/ trixie main\n'
            source.write_text(original)
            module.configure(root)
            self.assertTrue(source.read_text().startswith('#'))
            self.assertEqual(source.with_name('sources.list.before-paimenos-iso').read_text(), original)
            online = apt / 'sources.list.d/paimenos-iso.sources'
            first = online.read_text()
            self.assertIn('Components: main contrib non-free non-free-firmware', first)
            self.assertIn('Suites: trixie-security', first)
            module.configure(root)
            self.assertEqual(online.read_text(), first)

    def test_deb822_media_disabled_online_source_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'etc/apt/sources.list.d'
            folder.mkdir(parents=True)
            source = folder / 'debian.sources'
            online = 'Types: deb\nURIs: https://deb.debian.org/debian\nSuites: trixie\nComponents: main\n'
            source.write_text('Types: deb\nURIs: cdrom:Debian\nSuites: trixie\nComponents: main\n\n' + online)
            module.configure(root)
            self.assertIn('Enabled: no', source.read_text())
            self.assertIn(online, source.read_text())
            entries = list(module.components.entries((folder / 'paimenos-iso.sources').read_text(), '.sources'))
            self.assertNotIn('main', next(areas for suite, areas in entries if suite == 'trixie'))


class TransportTests(unittest.TestCase):
    def test_ipv4_fallback_requires_working_ipv4_and_broken_ipv6(self):
        for ipv4, ipv6 in ((True, False), (True, True), (False, True), (False, False)):
            with self.subTest(ipv4=ipv4, ipv6=ipv6), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / 'apt.conf'
                probe = lambda family: ipv4 if family == socket.AF_INET else ipv6
                module.session_config(config, probe=probe)
                content = config.read_text()
                self.assertEqual('Acquire::ForceIPv4 "true";' in content, ipv4 and not ipv6)
                self.assertIn('APT::Update::Error-Mode "any";', content)
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_network_change_removes_previous_session_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'apt.conf'
            module.session_config(config, probe=lambda family: family == socket.AF_INET)
            module.session_config(config, probe=lambda family: True)
            self.assertNotIn('ForceIPv4', config.read_text())

    def test_inherited_proxy_configuration_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            config, previous = Path(directory) / 'apt.conf', Path(directory) / 'previous.conf'
            previous.write_text('Acquire::https::Proxy "http://proxy.example:3128";\n')
            module.session_config(config, probe=lambda family: False, previous_config=previous)
            self.assertIn(previous.read_text(), config.read_text())

    @unittest.skipUnless(shutil.which('apt-config'), 'APT unavailable')
    def test_real_apt_reads_session_options(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'apt.conf'
            module.session_config(config, probe=lambda family: family == socket.AF_INET)
            result = subprocess.run(['apt-config', 'shell', 'IPV4', 'Acquire::ForceIPv4',
                                     'STRICT', 'APT::Update::Error-Mode', 'TIMEOUT', 'Acquire::https::Timeout'],
                                    env=dict(os.environ, APT_CONFIG=str(config)),
                                    text=True, capture_output=True, check=True)
            self.assertIn("IPV4='true'", result.stdout)
            self.assertIn("STRICT='any'", result.stdout)
            self.assertIn("TIMEOUT='15'", result.stdout)

    def test_ipv6_network_unreachable_is_detected_without_touching_services(self):
        connection = MagicMock()
        connection.__enter__.return_value.connect.side_effect = OSError(101, 'Network is unreachable')
        addresses = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0))]
        with patch.object(module.socket, 'getaddrinfo', return_value=addresses), patch.object(module.socket, 'socket', return_value=connection):
            self.assertFalse(module.can_connect(socket.AF_INET6))
            connection.__enter__.return_value.settimeout.assert_called_with(3)
            connection.__exit__.assert_called_once()

    def test_probe_can_use_another_address_of_the_same_family(self):
        connection = MagicMock()
        connection.__enter__.return_value.connect.side_effect = [OSError('unreachable'), None]
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))
                     for address in ('192.0.2.1', '192.0.2.2')]
        with patch.object(module.socket, 'getaddrinfo', return_value=addresses), patch.object(module.socket, 'socket', return_value=connection):
            self.assertTrue(module.can_connect(socket.AF_INET))

    def test_dns_failure_does_not_claim_connectivity(self):
        with patch.object(module.socket, 'getaddrinfo', side_effect=socket.gaierror('DNS failed')):
            self.assertFalse(module.can_connect(socket.AF_INET))

    def test_diagnostics_report_ipv4_http_and_https_separately(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout='test routes', stderr=''))
        probe = lambda family, port: family == socket.AF_INET and port == 80
        with patch.dict(os.environ, {'PAIMENOS_LANGUAGE': 'en'}), patch('builtins.print') as output:
            module.diagnose(run=runner, probe=probe)
        lines = [str(call.args[0]) for call in output.call_args_list]
        self.assertIn('deb.debian.org TCP/80 IPv4: reachable', lines)
        self.assertIn('deb.debian.org TCP/443 IPv4: unreachable', lines)
        self.assertEqual([call.args[0] for call in runner.call_args_list],
                         [['ip', '-4', '-brief', 'address'], ['ip', '-4', 'route'],
                          ['ip', '-6', 'route'], ['getent', 'ahostsv4', 'deb.debian.org']])
