import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paimenos import browser, webapp


class BrowserTests(unittest.TestCase):
    def test_crash_recovery_dialog_is_disabled_for_kids(self):
        with tempfile.TemporaryDirectory() as folder:
            webapp.prepare_profile(folder, str(Path(folder) / 'missing-template.js'))
            self.assertIn('user_pref("toolkit.startup.max_resumed_crashes", -1);', (Path(folder) / 'user.js').read_text())

    def test_context_menu_is_hidden_for_kids_but_not_for_parents(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed), tempfile.TemporaryDirectory() as folder:
                webapp.prepare_profile(folder, str(Path(folder) / 'missing-template.js'), context_menu=allowed)
                style = (Path(folder) / 'chrome/paimenos-webapp.css').read_text()
                self.assertEqual('#contentAreaContextMenu { display: none !important; }' in style, not allowed)

    def test_existing_profiles_disable_restore_suggestion_without_removing_session_data(self):
        pref = 'browser.startup.couldRestoreSession.count'
        for managed in (False, True):
            with self.subTest(managed=managed), tempfile.TemporaryDirectory() as folder:
                profile = Path(folder)
                old = f'user_pref("{pref}", 1);\n'
                if managed:
                    old = webapp.BEGIN + '\n' + old + webapp.END + '\n'
                (profile / 'user.js').write_text(old)
                (profile / 'prefs.js').write_text(f'user_pref("{pref}", 1);\n')
                (profile / 'sessionstore.jsonlz4').write_bytes(b'existing session')
                webapp.prepare_profile(folder, str(profile / 'missing-template.js'))
                config = (profile / 'user.js').read_text()
                self.assertIn(f'user_pref("{pref}", -1);', config)
                self.assertGreater(config.rfind(f'user_pref("{pref}", -1);'),
                                   config.rfind(f'user_pref("{pref}", 1);'))
                self.assertEqual((profile / 'sessionstore.jsonlz4').read_bytes(), b'existing session')
                self.assertEqual((profile / 'prefs.js').read_text(), f'user_pref("{pref}", 1);\n')

    def test_profile_preserves_site_data_and_skips_unchanged_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            for name in ('cookies.sqlite', 'sessionstore.jsonlz4', 'cache2/entries/site'):
                path = profile / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'keep')
            template = profile / 'template.js'
            template.write_text('user_pref("browser.startup.page", 3);\n')
            webapp.prepare_profile(folder, str(template))
            config = (profile / 'user.js').read_text()
            self.assertGreater(config.rfind('user_pref("browser.startup.page", 0)'),
                               config.find('user_pref("browser.startup.page", 3)'))
            for pref in ('resume_from_crash', 'resume_session_once', 'resuming_after_os_restart'):
                self.assertIn(f'user_pref("browser.sessionstore.{pref}", false);', config)
            # Irritierende Tastenkürzel (F12, F7, Alt, Strg+P, Strg+Umschalt+S) bleiben wirkungslos.
            for line in ('"devtools.policy.disabled", true', '"accessibility.browsewithcaret_shortcut.enabled", false',
                         '"ui.key.menuAccessKeyFocuses", false', '"print.enabled", false',
                         '"screenshots.browser.component.enabled", false'):
                self.assertIn('user_pref(' + line + ');', config)
            style = (profile / 'chrome/paimenos-webapp.css').read_text()
            self.assertNotIn(':root[inFullscreen] #navigator-toolbox', style)
            self.assertIn(':root[inDOMFullscreen] #navigator-toolbox', style)
            for name in ('cookies.sqlite', 'sessionstore.jsonlz4', 'cache2/entries/site'):
                self.assertEqual((profile / name).read_bytes(), b'keep')
            paths = [profile / 'user.js', profile / 'chrome/userChrome.css', profile / 'chrome/paimenos-webapp.css']
            inodes = [path.stat().st_ino for path in paths]
            webapp.prepare_profile(folder, str(template))
            self.assertEqual(inodes, [path.stat().st_ino for path in paths])

    def test_live_firefox_lock_is_respected_and_inode_never_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.parentlock'
            code = 'import fcntl,sys; f=open(sys.argv[1],"a"); fcntl.lockf(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()'
            child = subprocess.Popen([sys.executable, '-c', code, str(path)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                inode = path.stat().st_ino
                self.assertFalse(browser.profile_available(folder))
                self.assertFalse(browser.wait_for_profile(folder, timeout=.01))
                self.assertEqual(path.stat().st_ino, inode)
                child.communicate('done', timeout=3)
                self.assertTrue(browser.profile_available(folder))
                self.assertEqual(path.stat().st_ino, inode)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
                child.stdout.close()

    def test_duplicate_launch_does_not_spawn_or_modify_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(Path(folder) / '.paimenos-launch.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with patch.object(browser.subprocess, 'Popen') as spawn, patch.object(browser, 'prepare_profile') as prepare:
                    self.assertEqual(browser.run_browser(folder, '', 'https://example.test'), browser.PROFILE_BUSY)
                    spawn.assert_not_called()
                    prepare.assert_not_called()

    def test_busy_unknown_profile_does_not_start_firefox(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(browser, 'wait_for_profile', return_value=False), \
                patch.object(browser, 'recover_profile', return_value=False), patch.object(browser.subprocess, 'Popen') as spawn:
            self.assertEqual(browser.run_browser(folder, '', 'https://example.test'), browser.PROFILE_BUSY)
            spawn.assert_not_called()

    def test_orphan_matching_requires_uid_executable_and_exact_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            entry = Path(folder) / '123'
            entry.mkdir()
            (entry / 'exe').symlink_to('/usr/lib/firefox-esr/firefox-esr')
            (entry / 'cmdline').write_bytes(b'firefox-esr\0--profile\0/tmp/ours\0')
            self.assertTrue(browser.owns_profile(123, '/tmp/ours', Path(folder)))
            self.assertFalse(browser.owns_profile(123, '/tmp/ours-other', Path(folder)))
            (entry / 'exe').unlink()
            (entry / 'exe').symlink_to('/usr/bin/python3')
            self.assertFalse(browser.owns_profile(123, '/tmp/ours', Path(folder)))

    def test_normal_window_close_does_not_send_termination_signal(self):
        with tempfile.TemporaryDirectory() as folder:
            child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
            try:
                def close(pid):
                    child.terminate()
                    return True
                with patch.object(browser, 'close_windows', side_effect=close) as windows, \
                        patch.object(browser.os, 'killpg') as kill:
                    browser.stop_browser(child, timeout=2)
                    windows.assert_called_once_with(child.pid)
                    kill.assert_not_called()
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()

    def test_hung_browser_recovery_only_signals_its_own_group(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
        try:
            with patch.object(browser, 'close_windows', return_value=False), \
                    patch.object(browser.os, 'killpg', wraps=os.killpg) as kill:
                browser.stop_browser(child, timeout=.01)
                kill.assert_called_once_with(child.pid, signal.SIGTERM)
                self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()

    def test_failed_overlay_closes_browser_and_returns_to_menu_with_error(self):
        spawn = subprocess.Popen
        children = []
        def launch(command, **kwargs):
            if children:
                command = [sys.executable, '-c', 'raise SystemExit(3)']
            child = spawn(command, **kwargs)
            children.append(child)
            return child
        def close(pid):
            os.kill(pid, signal.SIGTERM)
            return True
        with tempfile.TemporaryDirectory() as folder, patch.object(browser.subprocess, 'Popen', side_effect=launch), \
                patch.object(browser, 'close_windows', side_effect=close):
            result = browser.run_browser(folder, '', 'https://example.test',
                                         command=[sys.executable, '-c', 'import time;time.sleep(30)'])
            self.assertEqual(result, 1)
            self.assertTrue(all(child.poll() is not None for child in children))

    def test_parent_area_profile_is_wiped_before_and_after_and_never_saves_the_pin(self):
        # Das Skript steht für Firefox: Es meldet, was beim Start im Profil liegt, und hinterlässt eine "Anmeldung".
        fake = ('import json, sys\nfrom pathlib import Path\n'
                'p = Path(sys.argv[1]); out = Path(sys.argv[2])\n'
                'out.write_text(json.dumps({"before": sorted(x.name for x in p.iterdir()), "user_js": (p / "user.js").read_text()}))\n'
                '(p / "cookies.sqlite").write_text("Anmeldung des Elternbereichs")\n')
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            profile, report = Path(folder), Path(outside) / 'seen.json'
            (profile / 'cookies.sqlite').write_text('Rest einer früheren Sitzung')
            (profile / 'storage').mkdir(); (profile / 'storage' / 'x').write_text('x')
            result = browser.run_browser(folder, '', 'http://localhost/', with_overlay=False, ephemeral=True,
                                         command=[sys.executable, '-c', fake, folder, str(report)])
            self.assertEqual(result, 0)
            seen = __import__('json').loads(report.read_text())
            self.assertNotIn('cookies.sqlite', seen['before'])   # alte Anmeldung vor dem Start entfernt
            self.assertNotIn('storage', seen['before'])
            self.assertIn('user_pref("signon.rememberSignons", false);', seen['user_js'])
            self.assertIn('user_pref("privacy.clearOnShutdown.cookies", true);', seen['user_js'])
            remaining = {p.name for p in profile.iterdir()}
            self.assertNotIn('cookies.sqlite', remaining)         # und nach dem Schließen erneut
            self.assertLessEqual(remaining, {'.paimenos-launch.lock', '.parentlock'})

    def test_normal_webapp_profile_keeps_its_data(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            (profile / 'cookies.sqlite').write_text('Anmeldung einer Webapp')
            command = [sys.executable, '-c', 'pass']
            result = browser.run_browser(folder, '', 'https://example.test', command=command, with_overlay=False)
            self.assertEqual(result, 0)
            self.assertTrue((profile / 'cookies.sqlite').exists())

    def test_supervisor_signal_allows_flush_then_immediate_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            fake = profile / 'fake.py'
            fake.write_text('''import fcntl, os, signal, sys, time
from pathlib import Path
p = Path(sys.argv[1])
f = open(p / '.parentlock', 'a')
fcntl.lockf(f, fcntl.LOCK_EX)
def close(*args):
    (p / 'flushed').write_text('cache saved')
    sys.exit(0)
signal.signal(signal.SIGUSR1, close)
(p / 'ready').write_text(str(os.getpid()))
while True: time.sleep(.05)
''')
            harness = ('import sys,os,signal; sys.path.insert(0,sys.argv[1]); '
                       'from paimenos import browser; '
                       'browser.close_windows=lambda pid: os.kill(pid,signal.SIGUSR1); '
                       'sys.exit(browser.run_browser(sys.argv[2],"","https://example.test",'
                       'command=[sys.executable,sys.argv[3],sys.argv[2]],with_overlay=False))')
            for attempt in range(2):
                (profile / 'ready').unlink(missing_ok=True)
                process = subprocess.Popen([sys.executable, '-c', harness, str(ROOT / 'src'), folder, str(fake)])
                child_pid = None
                try:
                    deadline = time.monotonic() + 5
                    while not (profile / 'ready').exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue((profile / 'ready').exists())
                    child_pid = int((profile / 'ready').read_text())
                    process.terminate()
                    self.assertEqual(process.wait(timeout=5), 0)
                    self.assertEqual((profile / 'flushed').read_text(), 'cache saved')
                    self.assertTrue(browser.profile_available(folder))
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child_pid, 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    if child_pid is not None:
                        try:
                            os.kill(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == '__main__':
    unittest.main()
