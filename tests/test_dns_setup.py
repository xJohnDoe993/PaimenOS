import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dns_setup', ROOT / 'tools/configure-dns.py')
dns = importlib.util.module_from_spec(spec); spec.loader.exec_module(dns)


class DNSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT.parent)
        self.root = Path(self.temp.name)
        self.run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=''))
        self.setup = dns.DNSSetup(self.root, self.run)
        self.setup.resolv.parent.mkdir(parents=True)
        self.setup.resolv.write_text('nameserver 192.168.10.1\n')
        self.source = self.root / 'family.conf'
        self.source.write_text('[Resolve]\nDNS=185.228.168.168 185.228.169.168\n')
        stub = self.root / 'run/systemd/resolve/stub-resolv.conf'
        stub.parent.mkdir(parents=True); stub.write_text('nameserver 127.0.0.53\n')
    def tearDown(self): self.temp.cleanup()
    def test_prepare_seeds_existing_upstream_without_changing_resolv(self):
        self.run.return_value = subprocess.CompletedProcess([], 0, stdout='Global:\nLink 2 (wlp3s0): 1.1.1.1 9.9.9.9\n')
        self.setup.prepare()
        self.assertIn('DNS=1.1.1.1 9.9.9.9 192.168.10.1', self.setup.bootstrap.read_text())
        self.assertEqual(self.setup.resolv.read_text(), 'nameserver 192.168.10.1\n')
        self.assertEqual(self.setup.state.stat().st_mode & 0o777, 0o600)
    def test_recover_restores_original_file_after_package_changes_it(self):
        self.setup.prepare()
        self.setup.resolv.unlink(); self.setup.resolv.symlink_to('/run/systemd/resolve/stub-resolv.conf')
        self.setup.working = Mock(side_effect=[False, False, True])
        self.setup.recover()
        self.assertFalse(self.setup.resolv.is_symlink())
        self.assertEqual(self.setup.resolv.read_text(), 'nameserver 192.168.10.1\n')
    def test_success_activates_family_dns_and_removes_bootstrap(self):
        self.setup.prepare()
        self.setup.configure_family(self.source)
        self.assertEqual(self.setup.resolv.readlink(), Path('/run/systemd/resolve/stub-resolv.conf'))
        self.assertEqual(self.setup.family.read_text(), self.source.read_text())
        self.assertFalse(self.setup.bootstrap.exists())
    def test_unreachable_family_dns_restores_working_config(self):
        self.setup.prepare()
        self.setup.write(self.setup.family, '[Resolve]\nDNS=192.168.10.1\n')
        previous_bootstrap = self.setup.bootstrap.read_text()
        self.setup.working = Mock(side_effect=[True, False, True])
        self.setup.configure_family(self.source)
        self.assertFalse(self.setup.resolv.is_symlink())
        self.assertEqual(self.setup.resolv.read_text(), 'nameserver 192.168.10.1\n')
        self.assertEqual(self.setup.family.read_text(), '[Resolve]\nDNS=192.168.10.1\n')
        self.assertEqual(self.setup.bootstrap.read_text(), previous_bootstrap)
    def test_offline_setup_writes_family_dns_without_a_connection_check(self):
        self.setup.working = Mock(return_value=False)  # kein Netz: die Online-Wege würden hier abbrechen
        self.setup.prepare_offline()
        self.assertEqual(self.setup.state.stat().st_mode & 0o777, 0o600)
        self.setup.family_offline(self.source)
        self.assertEqual(self.setup.family.read_text(), self.source.read_text())
        self.assertEqual(self.setup.resolv.readlink(), Path('/run/systemd/resolve/stub-resolv.conf'))
        self.setup.recover_offline()
        self.setup.working.assert_not_called()
    def test_failed_resolver_start_does_not_leave_stub_link(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1 if command[:2] == ['systemctl', 'restart'] else 0, stdout='')
        self.setup.run = run
        self.setup.configure_family(self.source)
        self.assertFalse(self.setup.resolv.is_symlink())
        self.assertFalse(self.setup.family.exists())
    def test_missing_stub_is_detected_and_restored(self):
        (self.root / 'run/systemd/resolve/stub-resolv.conf').unlink()
        self.setup.configure_family(self.source)
        self.assertFalse(self.setup.resolv.is_symlink())
    def test_existing_broken_dns_is_rejected_before_modification(self):
        self.setup.working = Mock(return_value=False)
        with self.assertRaises(ValueError): self.setup.prepare()
        with self.assertRaises(ValueError): self.setup.configure_family(self.source)
        self.assertFalse(self.setup.state.exists())
        self.assertFalse(self.setup.family.exists())
    def test_failed_rollback_stops_installation_instead_of_hiding_error(self):
        self.setup.working = Mock(side_effect=[True, False, False])
        with self.assertRaisesRegex(ValueError, 'Verbindung weiterhin gestört'):
            self.setup.configure_family(self.source)
        self.assertFalse(self.setup.resolv.is_symlink())
    def test_keyboard_interrupt_restores_files_and_stops(self):
        self.setup.working = Mock(side_effect=[True, KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt): self.setup.configure_family(self.source)
        self.assertFalse(self.setup.resolv.is_symlink())
        self.assertFalse(self.setup.family.exists())


if __name__ == '__main__': unittest.main()
