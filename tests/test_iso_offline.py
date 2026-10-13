import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def offline_groups():
    groups, current = {}, None
    for line in (ROOT / 'installer/offline-packages.txt').read_text().splitlines():
        if line.startswith('['):
            current = line[1:line.index(']')]
            groups[current] = set()
        elif line.strip() and not line.startswith('#'):
            groups[current].add(line.strip())
    return groups


def shell_packages(path, start):
    """Paketnamen hinter 'apt-get install -y' (bis zum ersten 'then' bzw. Zeilenende ohne Fortsetzung)."""
    text = (ROOT / path).read_text()
    block = text[text.index(start):]
    block = block[len(start):block.index('; then')]
    return {word for word in re.split(r'[\s\\]+', block) if re.fullmatch(r'[a-z0-9][a-z0-9+.-]+', word)}


class OfflinePackageTests(unittest.TestCase):
    def test_system_packages_are_complete(self):
        wanted = shell_packages('installer/10-system.sh', 'if apt-get install -y')
        self.assertGreater(len(wanted), 40)
        self.assertEqual(wanted - offline_groups()['system'], set())

    def test_hardware_packages_are_complete(self):
        text = (ROOT / 'installer/20-hardware.sh').read_text()
        wanted = set()
        for match in re.finditer(r'(?:apt-get install -y --no-install-recommends|install_available_firmware)((?:[ \t]*\\?\n?[ \t]+[a-z0-9][a-z0-9+.-]*)+)', text):
            wanted.update(re.findall(r'[a-z0-9][a-z0-9+.-]+', match.group(1).replace('\\', ' ')))
        wanted -= {'apt', 'get', 'install', 'y', 'no', 'recommends'}
        for name in ('tlp', 'tlp-rdw', 'zram-tools', 'intel-microcode', 'amd64-microcode'):
            self.assertIn(name, offline_groups()['hardware'])
        self.assertEqual({w for w in wanted if w in ('upower', 'acpi', 'fwupd', 'firmware-iwlwifi')} - offline_groups()['hardware'], set())

    def test_offline_apps_exist_in_the_app_menu(self):
        apps = (ROOT / 'installer/60-apps.sh').read_text()
        for package in offline_groups()['apps'] - {'tuxpaint-plugins', 'netpbm'}:
            self.assertIn('"' + package + '"', apps, package)


class RepoLayoutTests(unittest.TestCase):
    def test_repo_is_hidden_from_installer_apt_cdrom(self):
        script = (ROOT / 'iso/build-offline-repo.sh').read_text()
        self.assertIn('.aptignr', script)           # sonst bricht der Installer "Paketmanager konfigurieren" ab
        self.assertNotIn('dists/$CODENAME', script)  # flaches Repo


if __name__ == '__main__':
    unittest.main()
