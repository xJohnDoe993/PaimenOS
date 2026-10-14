from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paimenos import offline_complete as oc


class Fake:
    def __init__(self, fail=()):
        self.calls, self.fail = [], fail

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        name = Path(args[0]).name
        if name in self.fail or (name == '/usr/bin/python3' and 'python3' in self.fail):
            return subprocess.CompletedProcess(args, 1)
        if name == 'wget':
            Path(args[args.index('-O') + 1]).write_bytes(b'x')
        if name == 'tar':
            (Path(args[args.index('-C') + 1]) / oc.CURSOR_THEME).mkdir(exist_ok=True)
        if name == 'curl':
            Path(args[args.index('-o') + 1]).write_bytes(b'png')
        if name == 'convert':
            Path(args[-1][4:]).write_bytes(b'png')
        return subprocess.CompletedProcess(args, 0)


class CompleteTests(unittest.TestCase):
    def jobs(self, runner, marker=True, lang='de'):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'icons').mkdir()
        if marker:
            (root / 'marker').touch()
        self.addCleanup(self.temp.cleanup)
        jobs = oc.CompleteJobs(runner=runner, state_path=root / 'state.json', marker=root / 'marker', icon_dir=root / 'icons',
                               kids_home=root / 'home', release=root / 'release', run_in_thread=False, lang=lambda: lang)
        return jobs, root

    def test_nothing_to_do_without_marker(self):
        jobs, _ = self.jobs(Fake(), marker=False)
        with self.assertRaises(ValueError):
            jobs.start()
        self.assertFalse(jobs.snapshot()['pending'])

    def test_success_removes_marker(self):
        import shutil
        original = shutil.chown
        shutil.chown = lambda *args, **kwargs: None
        self.addCleanup(setattr, shutil, 'chown', original)
        jobs, root = self.jobs(Fake())
        result = jobs.start()
        self.assertEqual(result['state'], 'done')
        self.assertFalse((root / 'marker').exists())
        self.assertEqual(result['steps'], {'cursor': True, 'speech': True, 'logos': True})
        self.assertTrue((root / 'home/.local/share/paimenos/icons/maus.png').is_file())

    def test_failed_step_keeps_marker(self):
        import shutil
        original = shutil.chown
        shutil.chown = lambda *args, **kwargs: None
        self.addCleanup(setattr, shutil, 'chown', original)
        jobs, root = self.jobs(Fake(fail=('python3',)))
        result = jobs.start()
        self.assertEqual(result['state'], 'error')
        self.assertTrue((root / 'marker').exists())
        self.assertFalse(result['steps']['speech'])
        self.assertTrue(result['steps']['cursor'])

    def test_english_has_no_logo_downloads(self):
        fake = Fake()
        jobs, root = self.jobs(fake, lang='en')
        jobs.start()
        self.assertFalse(any(call[0] == 'curl' for call in fake.calls))

    def test_existing_cursor_is_not_downloaded_again(self):
        fake = Fake()
        jobs, root = self.jobs(fake, lang='en')
        (root / 'icons' / oc.CURSOR_THEME).mkdir()
        jobs.start()
        self.assertFalse(any(call[0] == 'wget' for call in fake.calls))

    def test_setup_script_writes_marker_only_for_offline_runs(self):
        text = (ROOT / 'iso/setup-after-install.sh').read_text()
        self.assertIn('offline-install-pending', text)
        self.assertLess(text.index('install -D -m 0644 /dev/null "$OFFLINE_MARKER"'), text.index('bash "$SOURCE/install.sh"'))


if __name__ == '__main__':
    unittest.main()
