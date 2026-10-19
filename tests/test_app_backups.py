import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unittest.mock import patch

from paimenos import backups as b


class AppBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'home'; self.home.mkdir()
        self.usb = self.base / 'usb'; self.usb.mkdir()
        self.state = self.base / 'state'; self.state.mkdir()
        self.apps = self.home / '.config/paimenos/apps.json'; self.apps.parent.mkdir(parents=True)
        self.apps.write_text('[]')
        self.running = 1
        for patcher in (patch.object(b.Path, 'home', return_value=self.home),
                        patch.object(b, 'ROOT', self.base / 'emulators'), patch.object(b, 'STATE_DIR', self.state),
                        patch.object(b, 'JOURNAL', self.state / 'restore.json'),
                        patch.object(b.parent, 'APPS_FILE', str(self.apps)),
                        patch.object(b, 'CHUNK', 4), patch.object(b.os.path, 'ismount', return_value=True),
                        patch.object(b.subprocess, 'run', side_effect=lambda *a, **k: type('R', (), {'returncode': self.running})()),
                        patch.object(b, 'drives', return_value=[{'id': 'usb', 'path': str(self.usb)}])):
            patcher.start(); self.addCleanup(patcher.stop)
        self.progress = lambda *args: None

    def put(self, rel, content):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def test_flatpak_app_round_trip_skips_cache_and_links(self):
        world = self.put('.var/app/org.luanti.luanti/.minetest/worlds/w1/map.sqlite', b'world-1')
        self.put('.var/app/org.luanti.luanti/cache/big.bin', b'cache')
        os.symlink('/etc/passwd', self.home / '.var/app/org.luanti.luanti/.minetest/link')
        skipped = []
        name = b.create('usb', ['app-minetest'], self.progress, skipped)
        manifest = json.loads((self.usb / 'PaimenOS-Backups' / name / 'manifest.json').read_text())
        self.assertEqual([f['path'] for f in manifest['files']], ['.minetest/worlds/w1/map.sqlite'])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(b.available('usb')[0]['groups'], ['app-minetest'])
        world.write_bytes(b'broken')
        b.restore('usb', name, ['app-minetest'], self.progress)
        self.assertEqual(world.read_bytes(), b'world-1')
        self.assertTrue((self.home / '.var/app/org.luanti.luanti/.minetest/link').is_symlink())
        # Locations without backed-up files are not created (Luanti would switch folders).
        self.assertFalse((self.home / '.luanti').exists())
        self.assertFalse(b.JOURNAL.exists())

    def test_running_app_blocks_backup_and_restore(self):
        self.put('.tuxpaint/saved/picture.png', b'png')
        name = b.create('usb', ['app-tuxpaint'], self.progress)
        self.running = 0
        with self.assertRaisesRegex(ValueError, 'Tux Paint'):
            b.create('usb', ['app-tuxpaint'], self.progress)
        with self.assertRaisesRegex(ValueError, 'Tux Paint'):
            b.restore('usb', name, ['app-tuxpaint'], self.progress)

    def test_personal_folders_from_user_dirs(self):
        self.put('.config/user-dirs.dirs', b'XDG_PICTURES_DIR="$HOME/Meine Bilder"\nXDG_MUSIC_DIR="$HOME/../evil"\n')
        self.assertEqual(b.user_folder('pictures'), self.home / 'Meine Bilder')
        (self.home / 'Musik').mkdir()
        self.assertEqual(b.user_folder('music'), self.home / 'Musik')
        self.assertEqual(b.user_folder('documents'), self.home / 'Documents')
        photo = self.put('Meine Bilder/kamera/foto.jpg', b'jpg')
        self.assertEqual(b.offered(), ['roms', 'saves', 'bios', 'files', 'settings'])
        name = b.create('usb', ['files'], self.progress)
        photo.unlink()
        b.restore('usb', name, ['files'], self.progress)
        self.assertEqual(photo.read_bytes(), b'jpg')

    def test_overlapping_or_outside_folders_are_refused(self):
        self.put('.config/user-dirs.dirs', b'XDG_DOCUMENTS_DIR="$HOME/.config/vlc"\n')
        with self.assertRaises(ValueError):
            b.create('usb', ['files', 'app-vlc'], self.progress)
        with patch.object(b, 'user_folder', return_value=self.base / 'outside'), self.assertRaises(ValueError):
            b.create('usb', ['files'], self.progress)

    def test_unknown_group_is_refused(self):
        with self.assertRaises(ValueError):
            b.start('create', 'usb', ['app-unknown'])


if __name__ == '__main__':
    unittest.main()
