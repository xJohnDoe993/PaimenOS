import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def test_failed_logo_still_removes_marker(self):
        jobs, root = self.jobs(Fake(fail=('curl',)))
        with patch.object(oc.shutil, 'chown'):
            result = jobs.start()
        self.assertEqual(result['state'], 'done')
        self.assertFalse((root / 'marker').exists())
        self.assertFalse(result['steps']['logos'])
        self.assertIn('Logos', result['message'])

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


@unittest.skipUnless(importlib.util.find_spec('flask'), 'Flask required')
class NoticeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from paimenos import paths
        cls.tmp = tempfile.TemporaryDirectory()
        with patch.object(paths, 'CONFIG_DIR', Path(cls.tmp.name)):
            from paimenos import parent_web
        cls.web = parent_web

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def notice(self, job, online=True):
        with patch.object(self.web.offline_complete, 'pending', return_value=True), \
                patch.object(self.web, 'install_request', return_value={'complete_job': job}), \
                patch.object(self.web.NetworkStatus, 'probe', side_effect=AssertionError('blocking probe')), \
                patch.object(self.web.OFFLINE_NETWORK, 'value', return_value=online) as value:
            return self.web.offline_notice(), value

    def test_uses_the_background_network_status(self):
        notice, value = self.notice({'state': 'idle', 'message': ''})
        self.assertTrue(notice['online'])
        value.assert_called_once()

    def test_running_job_skips_the_network_check(self):
        notice, value = self.notice({'state': 'running', 'message': '', 'percent': 10})
        self.assertFalse(notice['online'])
        value.assert_not_called()

    def test_hidden_without_marker(self):
        with patch.object(self.web.offline_complete, 'pending', return_value=False):
            self.assertIsNone(self.web.offline_notice())


if __name__ == '__main__':
    unittest.main()
