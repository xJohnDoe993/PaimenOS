"""Screen-time enforcement uses saved state, independently of window stacking."""
from contextlib import ExitStack
from datetime import date
import io
import json
import os
import signal
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paimenos import screen_guard, state, timer


class GuardFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT.parent)
        self.folder = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(screen_guard, 'STATE_DIR', self.folder))
        self.settings_file = self.folder / 'settings.json'
        self.stack.enter_context(patch.object(state, 'SETTINGS_FILE', str(self.settings_file)))
        self.settings = dict(state.DEFAULTS, pin='1234', last_used_date=str(date.today()),
                             daily_limit_minutes=1, today_used_seconds=0)
        self.save()

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def save(self, **changes):
        self.settings.update(changes)
        self.settings_file.write_text(json.dumps(self.settings))


class GuardTests(GuardFixture, unittest.TestCase):
    def test_expiry_bonus_unlimited_and_daily_rollover(self):
        self.assertFalse(screen_guard.child_access_blocked())
        self.save(today_used_seconds=60)
        self.assertTrue(screen_guard.child_access_blocked())
        self.save(bonus_minutes=5)
        self.assertFalse(screen_guard.child_access_blocked())
        self.save(daily_limit_minutes=0, today_used_seconds=100000)
        self.assertFalse(screen_guard.child_access_blocked())
        self.save(daily_limit_minutes=1, last_used_date='2000-01-01')
        self.assertFalse(screen_guard.child_access_blocked())

    def test_unreadable_settings_never_grant_child_access(self):
        for content in ('{broken', '[]'):
            self.settings_file.write_text(content)
            self.assertTrue(screen_guard.child_access_blocked())
        self.settings_file.unlink()
        self.assertTrue(screen_guard.child_access_blocked())

    def test_invalid_time_values_block_access_without_rewriting_settings(self):
        for key in ('daily_limit_minutes', 'bonus_minutes', 'today_used_seconds'):
            for value in (None, 'broken', -1, False, 0.5, float('inf'), [], {}):
                with self.subTest(key=key, value=value):
                    data = dict(self.settings, **{key: value})
                    original = json.dumps(data)
                    self.settings_file.write_text(original)
                    self.assertTrue(screen_guard.child_access_blocked())
                    self.assertEqual(self.settings_file.read_text(), original)
            data = dict(self.settings)
            del data[key]
            self.settings_file.write_text(json.dumps(data))
            self.assertTrue(screen_guard.child_access_blocked())

    def test_timer_keeps_running_and_opens_lock_when_settings_are_corrupt(self):
        self.settings_file.write_text('{broken')
        class EndLoop(Exception):
            pass
        with patch.object(timer.time, 'sleep', side_effect=[None, None, EndLoop]), \
                patch.object(timer.time, 'monotonic', side_effect=[0, 1, 2]), \
                patch.object(timer.subprocess, 'Popen') as spawn:
            spawn.return_value.poll.return_value = None
            with self.assertRaises(EndLoop):
                timer.run_timer()
            spawn.assert_called_once()
        self.assertEqual(self.settings_file.read_text(), '{broken')

    def test_overlapping_parent_views_hold_priority_until_last_close(self):
        first, second = screen_guard.ViewGuard(), screen_guard.ViewGuard()
        try:
            self.assertTrue(screen_guard.child_access_blocked())
            first.close()
            self.assertTrue(screen_guard.child_access_blocked())
        finally:
            first.close(); second.close()
        self.assertFalse(screen_guard.child_access_blocked())

    def test_crashed_view_releases_priority_without_deleting_lock_file(self):
        code = ('import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                'from paimenos import screen_guard as g;g.STATE_DIR=Path(sys.argv[2]);'
                'lease=g.ViewGuard();print("ready",flush=True);sys.stdin.read()')
        process = subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'src'), str(self.folder)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertTrue(screen_guard.protected_view_active())
            process.kill(); process.communicate(timeout=3)
            self.assertFalse(screen_guard.protected_view_active())
            self.assertTrue((self.folder / 'paimenos-protected-view.lock').exists())
        finally:
            if process.poll() is None:
                process.kill(); process.communicate(timeout=3)

    def test_timer_leaves_active_parent_view_in_front_then_starts_lock_on_close(self):
        self.save(today_used_seconds=60)
        class EndLoop(Exception):
            pass
        for parent_open in (True, False):
            lease = screen_guard.ViewGuard() if parent_open else None
            try:
                with patch.object(timer.os.path, 'expanduser', return_value=str(self.folder / 'timer.lock')), \
                        patch.object(timer.time, 'sleep', side_effect=[None, EndLoop]), \
                        patch.object(timer.time, 'monotonic', side_effect=[0, 1]), \
                        patch.object(timer.subprocess, 'Popen') as spawn:
                    with self.assertRaises(EndLoop):
                        timer.main()
                    if parent_open:
                        spawn.assert_not_called()
                    else:
                        spawn.assert_called_once_with([sys.executable, '-I', timer.LOCKSCREEN_SCRIPT, 'lockscreen'])
            finally:
                if lease is not None:
                    lease.close()


try:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt5.QtCore import QEvent, QRect, Qt, QTimer
    from PyQt5.QtGui import QCloseEvent, QKeyEvent
    from PyQt5.QtWidgets import QApplication, QDialog, QWidget
    from paimenos import close_overlay, foreground, lockscreen, menu, status_overlay
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, 'Qt screen-time tests require PyQt5 and python-xlib.')
class WidgetGuardTests(GuardFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        super().setUp()
        self.windows = []
        self.stack.enter_context(patch.object(menu, 'STATE_DIR', self.folder))
        self.stack.enter_context(patch.object(menu, 'SETTINGS_FILE', str(self.settings_file)))
        self.stack.enter_context(patch.object(menu, 'APPS_FILE', str(self.folder / 'apps.json')))
        self.stack.enter_context(patch.object(menu, 'has_media_attached', return_value=False))
        self.stack.enter_context(patch.object(menu, 'NetworkStatus'))
        self.stack.enter_context(patch.object(menu, 'ControllerReader'))
        self.stack.enter_context(patch.object(menu, 'log_event'))
        self.spawn = self.stack.enter_context(patch.object(menu.subprocess, 'Popen'))

    def tearDown(self):
        for window in self.windows:
            if isinstance(window, status_overlay.StatusOverlay):
                window.shutdown()
            window.hide()
            window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        super().tearDown()

    def child_menu(self):
        window = menu.PaimenOSMenu()
        self.windows.append(window)
        self.spawn.reset_mock()
        return window

    def test_shutdown_waits_for_supervisor_before_quitting_menu(self):
        window = self.child_menu()
        process = MagicMock()
        process.poll.return_value = None
        window.active_process = process
        calls = MagicMock()
        calls.attach_mock(process.terminate, 'terminate')
        calls.attach_mock(process.wait, 'wait')
        with patch.object(QApplication, 'quit') as quit_app:
            calls.attach_mock(quit_app, 'quit')
            window.stop_for_shutdown()
            window.stop_for_shutdown()
        from unittest.mock import call
        self.assertEqual(calls.mock_calls, [call.terminate(), call.wait(timeout=15), call.quit()])

    def test_shutdown_timeout_is_bounded_and_still_exits_menu(self):
        window = self.child_menu()
        process = MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('app', 15)
        window.active_process = process
        with patch.object(QApplication, 'quit') as quit_app:
            window.stop_for_shutdown()
            quit_app.assert_called_once()

    def test_corrupt_settings_keep_lock_visible_and_recover_after_repair(self):
        self.settings_file.write_text('{broken')
        window = lockscreen.LockScreen()
        self.windows.append(window)
        window.showFullScreen()
        with patch.object(QApplication, 'quit') as quit_app:
            window.check_unlocked()
            quit_app.assert_not_called()
            self.assertTrue(screen_guard.protected_view_active())
            self.assertTrue(window.isVisible())
            self.save()
            window.check_unlocked()
            quit_app.assert_called_once()

    def test_corrupt_settings_in_pin_dialog_restarts_refresh_without_auto_shutdown(self):
        window = lockscreen.LockScreen()
        self.windows.append(window)
        self.settings_file.write_text('{broken')
        with patch.object(lockscreen.QInputDialog, 'getText', return_value=('1234', True)), \
                patch.object(lockscreen.QMessageBox, 'warning') as warning, \
                patch.object(QApplication, 'quit') as quit_app:
            window.add_time_dialog()
            warning.assert_called_once()
            quit_app.assert_not_called()
        self.assertFalse(hasattr(window, 'auto_shutdown_timer'))
        self.assertTrue(window.refresh_timer.isActive())

    def test_corrupt_settings_hide_an_already_visible_child_menu(self):
        window = self.child_menu()
        window.showFullScreen()
        self.settings_file.write_text('{broken')
        window.update_clock_and_net()
        self.assertFalse(window.isVisible())

    def overlay(self):
        with patch.object(close_overlay, 'FullscreenTracker'), \
                patch.object(close_overlay.os, 'pidfd_open', side_effect=OSError):
            window = close_overlay.CloseButtonOverlay(os.getpid())
        self.windows.append(window)
        window.check_target = lambda: True
        window.fullscreen.is_fullscreen.return_value = False
        return window

    def test_expired_time_blocks_native_webapp_camera_and_queued_activation(self):
        window = self.child_menu()
        window.showFullScreen()
        self.save(today_used_seconds=60)
        for item in ({'command': '/usr/bin/true'}, {'url': 'https://example.test'},
                     {'command': '__CAMERA__'}, {'command': 'tuxpaint', 'id': 'tuxpaint'}):
            window.launch(item)
        self.spawn.assert_not_called()
        self.assertIsNone(window.camera_browser)
        self.assertFalse(window.controller_enabled())
        key = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
        window.keyPressEvent(key)
        self.assertTrue(key.isAccepted())
        self.assertIsNone(window.active_process)

    def test_controller_cannot_activate_visible_menu_after_expiry(self):
        window = self.child_menu()
        window.showFullScreen()
        self.save(today_used_seconds=60)
        with patch.object(QApplication, 'applicationState', return_value=Qt.ApplicationActive), \
                patch.object(QApplication, 'activeWindow', return_value=window):
            self.assertFalse(window.controller_enabled())
            window.controller_action('activate')
        self.spawn.assert_not_called()

    def test_shutdown_stays_available_when_time_expires(self):
        window = self.child_menu()
        self.save(today_used_seconds=60)
        window.launch({'command': '__POWEROFF__'})
        self.spawn.assert_called_once_with(['systemctl', 'poweroff'], start_new_session=True)

    def test_parent_priority_blocks_launch_even_with_remaining_time(self):
        window = self.child_menu()
        lease = screen_guard.ViewGuard()
        try:
            window.launch({'command': '/usr/bin/true'})
            self.spawn.assert_not_called()
        finally:
            lease.close()

    def test_bonus_restores_native_launch_without_restarting_menu(self):
        window = self.child_menu()
        self.save(today_used_seconds=60)
        window.launch({'command': '/usr/bin/true'})
        self.spawn.assert_not_called()
        self.save(bonus_minutes=5)
        window.launch({'command': '/usr/bin/true'})
        self.assertIsNotNone(window.active_process)
        self.assertEqual(self.spawn.call_args.args[0], ['/usr/bin/true'])
        window.application_log.close()

    def test_overlay_disappears_and_click_rechecks_time_before_signalling(self):
        overlay = self.overlay()
        overlay.update_overlay()
        self.assertTrue(overlay.isVisible())
        self.assertFalse(overlay.windowFlags() & Qt.X11BypassWindowManagerHint)
        # A queued click arrives before the overlay's next 250 ms update.
        self.save(today_used_seconds=60)
        with patch.object(close_overlay.os, 'kill') as kill, patch.object(QApplication, 'quit') as quit_app:
            overlay.close_target()
            kill.assert_not_called(); quit_app.assert_not_called()
        self.assertFalse(overlay.isVisible())
        self.assertFalse(overlay.button.isEnabled())
        self.save(bonus_minutes=5)
        overlay.update_overlay()
        self.assertTrue(overlay.isVisible())
        self.assertTrue(overlay.button.isEnabled())

    def test_overlay_stays_hidden_during_parent_view_and_video_fullscreen(self):
        overlay = self.overlay()
        lease = screen_guard.ViewGuard()
        try:
            overlay.update_overlay()
            self.assertFalse(overlay.isVisible())
        finally:
            lease.close()
        overlay.fullscreen.is_fullscreen.return_value = True
        overlay.update_overlay()
        self.assertFalse(overlay.isVisible())
        overlay.fullscreen.is_fullscreen.return_value = False
        overlay.update_overlay()
        self.assertTrue(overlay.isVisible())

    def test_app_exit_cannot_raise_menu_or_camera_above_expired_time(self):
        window = self.child_menu()
        target = QWidget(); self.windows.append(target); target.show()
        window.return_window = target
        window.active_process = MagicMock(returncode=0)
        window.active_process.poll.return_value = 0
        window.process_timer = QTimer(window)
        window.application_log = io.StringIO()
        window.expected_exit_codes = {0}
        self.save(today_used_seconds=60)
        window.check_process()
        self.assertFalse(window.isVisible())
        self.assertFalse(target.isVisible())
        self.assertIsNone(window.active_process)

    def test_hotkey_request_and_camera_close_do_not_restore_expired_menu(self):
        window = self.child_menu()
        self.save(today_used_seconds=60)
        request = self.folder / 'paimenos-menu.raise'; request.touch()
        window.update_clock_and_net()
        self.assertFalse(window.isVisible())
        self.assertFalse(request.exists())
        camera = QDialog(window); window.camera_browser = camera
        window.camera_finished(camera)
        self.assertFalse(window.isVisible())
        self.save(bonus_minutes=5)
        window.update_clock_and_net()
        self.assertTrue(window.isVisible())

    def test_parent_area_opens_the_web_backend_in_a_throwaway_browser_profile(self):
        window = self.child_menu()
        with patch.object(menu, 'parent_area_reachable', return_value=True):
            window.open_parent_menu()
        command = self.spawn.call_args.args[0]
        self.assertEqual(command[3:5], ['close_overlay', '--parent-browser'])
        self.assertTrue(command[5].endswith('parent-area'))
        self.assertEqual(command[-1], menu.PARENT_URL)
        self.assertTrue(window.active_is_webapp)
        window.application_log.close()

    def test_parent_area_reports_a_stopped_web_backend_instead_of_opening_a_browser(self):
        window = self.child_menu()
        with patch.object(menu, 'parent_area_reachable', return_value=False), \
                patch.object(menu.QMessageBox, 'warning') as warning:
            window.open_parent_menu()
        warning.assert_called_once()
        self.spawn.assert_not_called()

    def test_parent_area_is_not_reachable_without_a_listening_web_backend(self):
        with patch.object(menu.socket, 'create_connection', side_effect=OSError):
            self.assertFalse(menu.parent_area_reachable())
        with patch.object(menu.socket, 'create_connection'):
            self.assertTrue(menu.parent_area_reachable())

    def test_network_editor_request_opens_the_editor_once_and_not_for_a_blocked_child(self):
        window = self.child_menu()
        request = Path(menu.STATE_DIR) / 'paimenos-menu.network-editor'
        request.parent.mkdir(parents=True, exist_ok=True)
        with patch.object(menu.shutil, 'which', return_value='/usr/bin/nm-connection-editor'):
            request.touch()
            window.handle_network_editor_request()
            self.assertFalse(request.exists())
            self.assertEqual(self.spawn.call_args.args[0], ['/usr/bin/nm-connection-editor'])
            self.spawn.reset_mock()
            self.spawn.return_value.poll.return_value = None  # läuft noch: keine zweite Instanz
            request.touch()
            window.handle_network_editor_request()
            self.spawn.assert_not_called()
            self.save(today_used_seconds=60)
            self.spawn.return_value.poll.return_value = 0
            request.touch()
            window.handle_network_editor_request()
            self.spawn.assert_not_called()

    def test_secondary_display_is_covered_and_tracks_geometry_changes(self):
        window = QWidget(); self.windows.append(window)
        guard = foreground.ForegroundGuard(window)
        primary = self.app.primaryScreen()
        second = MagicMock()
        second.geometry.return_value = QRect(800, 0, 1280, 720)
        with patch.object(foreground.QApplication, 'screens', return_value=[primary, second]):
            window.showFullScreen()
            guard.sync_screens()
            cover = guard.covers[second]
            self.assertTrue(cover.isVisible())
            self.assertEqual(cover.geometry(), second.geometry())
            second.geometry.return_value = QRect(-1024, 0, 1024, 768)
            guard.sync_screens()
            self.assertEqual(cover.geometry(), second.geometry())
        window.hide()
        self.assertFalse(screen_guard.protected_view_active())

    def test_lock_rejects_escape_close_and_only_unlocks_after_saved_bonus(self):
        self.save(today_used_seconds=60)
        window = lockscreen.LockScreen(); self.windows.append(window); window.showFullScreen()
        close = QCloseEvent(); window.closeEvent(close)
        self.assertFalse(close.isAccepted())
        escape = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        window.keyPressEvent(escape)
        self.assertTrue(escape.isAccepted())
        with patch.object(QApplication, 'quit') as quit_app:
            window.check_unlocked(); quit_app.assert_not_called()
            self.save(bonus_minutes=5)
            window.check_unlocked(); quit_app.assert_called_once()

    def test_lock_never_automatically_shuts_down_and_requires_confirmation(self):
        self.save(today_used_seconds=60)
        window = lockscreen.LockScreen(); self.windows.append(window)
        self.assertFalse(hasattr(window, 'auto_shutdown_timer'))
        with patch.object(lockscreen.QMessageBox, 'question', return_value=lockscreen.QMessageBox.No), \
                patch.object(lockscreen.subprocess, 'run') as run:
            window.shutdown()
            run.assert_not_called()
        with patch.object(lockscreen.QMessageBox, 'question', return_value=lockscreen.QMessageBox.Yes), \
                patch.object(lockscreen.subprocess, 'run') as run:
            window.shutdown()
            run.assert_called_once_with(['systemctl', 'poweroff'])

    def test_volume_overlay_never_covers_parent_view_or_expired_lock(self):
        reader, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(reader.close); self.addCleanup(sender.close)
        reader.setblocking(False)
        overlay = status_overlay.StatusOverlay(reader); self.windows.append(overlay)
        overlay.updated('volume', (50, False))
        self.assertTrue(overlay.isVisible())
        # Managed windows end up below the focused fullscreen menu under Openbox.
        self.assertTrue(overlay.windowFlags() & Qt.X11BypassWindowManagerHint)
        lease = screen_guard.ViewGuard()
        try:
            overlay.check_guard()
            self.assertFalse(overlay.isVisible())
            overlay.updated('volume', (60, False))
            self.assertFalse(overlay.isVisible())
        finally:
            lease.close()
        self.save(today_used_seconds=60)
        overlay.updated('brightness', (80, False))
        self.assertFalse(overlay.isVisible())


@unittest.skipUnless(HAS_QT and os.environ.get('PAIMENOS_SCREEN_GUARD_INTEGRATION') == '1',
                     'Window stacking/input checks require Openbox and X11.')
class X11GuardTests(GuardFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from Xlib import display
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.x = display.Display()

    @classmethod
    def tearDownClass(cls):
        cls.x.close()

    def setUp(self):
        super().setUp()
        self.windows = []
        self.browser = QWidget()
        self.browser.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.browser.setWindowTitle('Screen guard test browser')
        self.windows.append(self.browser)
        # Firefox normally starts maximized; its video fullscreen is a different layer.
        self.browser.showMaximized()
        with patch.object(close_overlay, 'FullscreenTracker'), \
                patch.object(close_overlay.os, 'pidfd_open', side_effect=OSError):
            self.overlay = close_overlay.CloseButtonOverlay(os.getpid())
        self.windows.append(self.overlay)
        self.overlay.check_target = lambda: True
        self.overlay.fullscreen.is_fullscreen.return_value = False
        self.overlay.update_overlay()
        self.wait()

    def tearDown(self):
        for window in self.windows:
            window.hide(); window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.wait()
        super().tearDown()

    def wait(self):
        from PyQt5.QtTest import QTest
        QTest.qWait(400)
        self.x.sync()

    def topmost(self):
        from Xlib import Xatom
        prop = self.x.screen().root.get_full_property(
            self.x.intern_atom('_NET_CLIENT_LIST_STACKING'), Xatom.WINDOW)
        return int(prop.value[-1]) if prop is not None and len(prop.value) else None

    def click(self, point):
        from Xlib import X
        from Xlib.ext import xtest
        xtest.fake_input(self.x, X.MotionNotify, x=point.x(), y=point.y())
        xtest.fake_input(self.x, X.ButtonPress, detail=1)
        xtest.fake_input(self.x, X.ButtonRelease, detail=1)
        self.x.sync()
        self.wait()

    def test_managed_menu_button_keeps_its_size_position_and_normal_click(self):
        from paimenos.webapp import BAR_HEIGHT, MENU_WIDTH
        screen = self.app.primaryScreen().geometry()
        self.assertEqual(self.overlay.width(), MENU_WIDTH)
        self.assertEqual(self.overlay.height(), BAR_HEIGHT - 20)
        self.assertEqual(self.overlay.geometry().right(), screen.right() - 12)
        self.assertEqual(self.overlay.geometry().top(), screen.top() + 10)
        with patch.object(close_overlay.os, 'kill') as kill, patch.object(QApplication, 'quit') as quit_app:
            self.click(self.overlay.geometry().center())
            kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
            quit_app.assert_called_once()

    def test_expired_lock_covers_browser_button_and_keeps_parent_pin_above_it(self):
        point = self.overlay.geometry().center()
        self.browser.showFullScreen()
        self.wait()
        self.save(today_used_seconds=60)
        locked = lockscreen.LockScreen(); self.windows.append(locked); locked.showFullScreen()
        self.wait()
        self.assertFalse(self.overlay.isVisible())
        self.assertEqual(self.topmost(), int(locked.winId()))
        with patch.object(close_overlay.os, 'kill') as kill:
            self.click(point)
            kill.assert_not_called()
        from PyQt5.QtWidgets import QInputDialog
        pin = QInputDialog(locked)
        pin.setWindowModality(Qt.ApplicationModal)
        pin.setLabelText('Parent PIN')
        pin.show()
        self.wait()
        self.assertEqual(self.topmost(), int(pin.winId()))
        self.assertIs(self.app.activeWindow(), pin)
        pin.reject(); pin.deleteLater()


if __name__ == '__main__':
    unittest.main()
