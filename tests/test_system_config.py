import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paimenos import system_config as sc


class SystemConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.sources = self.base / 'sources'
        self.root = self.base / 'root'
        (self.sources / 'a').mkdir(parents=True)
        (self.sources / 'a/cfg').write_text('cfg v1')
        (self.sources / 'a/pref').write_text('pref v1')
        (self.root / 'usr/lib/ff').mkdir(parents=True)
        self.list = self.base / 'list.json'
        self.write_list([
            {'source': 'a/cfg', 'target': '/usr/lib/ff/cfg', 'requires': '/usr/lib/ff', 'group': 'ff'},
            {'source': 'a/pref', 'target': '/usr/lib/ff/pref', 'requires': '/usr/lib/ff', 'group': 'ff'},
            {'source': 'a/cfg', 'target': '/etc/new/dir/file'},
        ])

    def write_list(self, files):
        self.list.write_text(json.dumps({'schema': 1, 'files': files}))

    def run_sync(self):
        return sc.sync(self.list, self.sources, self.root)

    def test_missing_files_are_created_with_mode_and_parents(self):
        changed, failed = self.run_sync()
        self.assertEqual(failed, [])
        self.assertEqual(len(changed), 3)
        target = self.root / 'etc/new/dir/file'
        self.assertEqual(target.read_text(), 'cfg v1')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_changed_content_is_replaced_and_current_files_are_left_alone(self):
        self.run_sync()
        self.assertEqual(self.run_sync(), ([], []))
        (self.root / 'usr/lib/ff/cfg').write_text('manipuliert')
        (self.sources / 'a/pref').write_text('pref v2')
        changed, _ = self.run_sync()
        self.assertEqual(sorted(changed), ['/usr/lib/ff/cfg', '/usr/lib/ff/pref'])
        self.assertEqual((self.root / 'usr/lib/ff/cfg').read_text(), 'cfg v1')

    def test_group_is_skipped_when_firefox_is_not_installed(self):
        (self.root / 'usr/lib/ff').rmdir()
        changed, failed = self.run_sync()
        self.assertEqual(changed, ['/etc/new/dir/file'])
        self.assertFalse((self.root / 'usr/lib/ff').exists())

    def test_failure_of_first_group_file_skips_the_pref_file(self):
        (self.sources / 'a/cfg').unlink()
        changed, failed = self.run_sync()
        self.assertFalse((self.root / 'usr/lib/ff/pref').exists())
        self.assertEqual([target for target, _ in failed], ['/usr/lib/ff/cfg', '/etc/new/dir/file'])

    def test_unsafe_paths_are_rejected(self):
        for entry in ({'source': '../x', 'target': '/etc/x'}, {'source': 'a/cfg', 'target': 'relativ'},
                      {'source': 'a/cfg', 'target': '/etc/../x'}):
            self.write_list([entry])
            with self.assertRaises(ValueError):
                sc.load(self.list)

    def test_shipped_list_points_to_existing_files_and_is_in_the_manifest(self):
        manifest = json.loads((ROOT / 'manifest.json').read_text())['components']['system-config']['files']
        self.assertIn('data/system-config.json', manifest)
        for entry in sc.load(ROOT / 'data/system-config.json'):
            self.assertTrue((ROOT / 'data/system-config' / entry['source']).is_file(), entry['source'])
            self.assertIn('data/system-config/' + entry['source'], manifest)


if __name__ == '__main__':
    unittest.main()
