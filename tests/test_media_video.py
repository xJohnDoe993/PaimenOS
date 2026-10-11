import os
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paimenos.media_files import scan_media, is_video, IMAGE_EXTS


class MediaDiscoveryTests(unittest.TestCase):
    def test_mixed_media_case_nested_names_and_hidden_directories(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as folder:
            root = Path(folder)
            names = ['DCIM/ä Urlaub.MOV', 'DCIM/b.MP4', 'a.JPG', 'b.txt',
                     '.cache/hidden.mp4', 'System Volume Information/hidden.jpg']
            for name in names:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            result = scan_media([root, root], threading.Event())
            self.assertEqual([Path(p).name for p in result], ['a.JPG', 'b.MP4', 'ä Urlaub.MOV'])
            self.assertEqual(scan_media([root], threading.Event(), IMAGE_EXTS), [str(root / 'a.JPG')])
            self.assertTrue(is_video(result[-1])); self.assertFalse(is_video(result[0]))

    def test_cancelled_scan_and_links_do_not_open_other_files(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as folder:
            root = Path(folder)
            (root / 'clip.mp4').touch()
            (root / 'linked.mp4').symlink_to(root / 'clip.mp4')
            (root / 'directory.mp4').mkdir()
            self.assertEqual(scan_media([root], threading.Event()), [str(root / 'clip.mp4')])
            cancelled = threading.Event(); cancelled.set()
            self.assertEqual(scan_media([root], cancelled), [])


try:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt5.QtCore import QObject, QUrl, pyqtSignal
    from PyQt5.QtWidgets import QApplication, QDialog, QWidget
    from paimenos import video
    HAS_QT = True
except ImportError:
    HAS_QT = False

try:
    from PyQt5.QtMultimediaWidgets import QVideoWidget
    HAS_VIDEO_WIDGET = True
except ImportError:
    HAS_VIDEO_WIDGET = False


if HAS_QT:
    class BackendSurface(QWidget):
        """A native backend child can accept events without passing them upward."""
        def event(self, event):
            from PyQt5.QtCore import QEvent
            if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                                QEvent.MouseButtonDblClick, QEvent.ShortcutOverride, QEvent.KeyPress):
                event.accept()
                return True
            return super().event(event)

    class FakeContent:
        def __init__(self, url=None): self.url = url

    class FakePlayer(QObject):
        positionChanged = pyqtSignal(int)
        durationChanged = pyqtSignal(int)
        seekableChanged = pyqtSignal(bool)
        stateChanged = pyqtSignal(int)
        mediaStatusChanged = pyqtSignal(int)
        error = pyqtSignal(int)
        NoError, ResourceError, FormatError = 0, 1, 2
        StoppedState, PlayingState, PausedState = 0, 1, 2
        LoadedMedia, BufferedMedia, EndOfMedia, InvalidMedia = 3, 6, 7, 8

        def __init__(self, parent):
            super().__init__(parent)
            self.content = FakeContent()
            self.current_state, self.current_status, self.current_position = 0, 3, 0
            self.volume, self.stops = 0, 0
        def setVideoOutput(self, output): self.output = output
        def setVolume(self, volume): self.volume = volume
        def setMedia(self, content): self.content = content
        def state(self): return self.current_state
        def mediaStatus(self): return self.current_status
        def position(self): return self.current_position
        def isSeekable(self): return True
        def errorString(self): return 'Test decoder error'
        def play(self):
            self.current_state = self.PlayingState
            self.stateChanged.emit(self.current_state)
        def pause(self):
            self.current_state = self.PausedState
            self.stateChanged.emit(self.current_state)
        def stop(self):
            self.stops += 1
            self.current_state = self.StoppedState
            self.stateChanged.emit(self.current_state)
        def setPosition(self, position):
            self.current_position = position
            self.positionChanged.emit(position)


@unittest.skipUnless(HAS_QT, 'Optional Qt widget tests require PyQt5.')
class VideoWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        try:
            from paimenos import menu, menu_camera
        except ImportError as exc:
            raise unittest.SkipTest('Camera widget tests need menu dependencies: ' + str(exc))
        cls.menu = menu
        cls.camera = menu_camera

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT.parent)
        self.root = Path(self.temp.name)
        self.clip = str(self.root / 'Urlaub #1 ä.MP4'); Path(self.clip).touch()
        self.second = str(self.root / 'second.mov'); Path(self.second).touch()
        self.photo = str(self.root / 'photo.jpg'); Path(self.photo).touch()
        self.patches = [patch.object(video, 'load_backend', return_value=(FakePlayer, FakeContent, QWidget)),
                        patch.object(video, 'log_event'),
                        patch.object(self.camera, 'read_settings', return_value={'bg_color': '#FF9F00'}),
                        patch.object(self.camera, 'media_roots', return_value=[]),
                        patch.object(self.camera, 'submit_task')]
        for item in self.patches: item.start()
        self.browser = self.menu.CameraBrowser()
        self.viewer = self.browser.video_viewer
        self.browser.images = [self.clip, self.photo, self.second]
    def tearDown(self):
        self.browser.done(QDialog.Rejected)
        self.browser.deleteLater(); self.app.processEvents()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def test_video_local_url_and_photo_navigation_stay_separate(self):
        self.browser.preview(self.clip)
        self.assertIs(self.browser.stack.currentWidget(), self.viewer)
        self.assertEqual(self.viewer.paths, [self.clip, self.second])
        self.assertEqual(self.viewer.player.content.url.toLocalFile(), self.clip)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PlayingState)
        self.browser.preview(self.photo)
        self.assertEqual(self.browser.viewer.images, [self.photo])
        self.assertIsNone(self.viewer.player.content.url)

    def test_menu_reads_updated_camera_title_and_preserves_custom_title(self):
        camera = {'id': 'camera', 'type': 'camera', 'command': '__CAMERA__',
                  'title': 'Kamera / Bilder', 'enabled': False}
        path = str(self.root / 'apps.json')
        Path(path).write_text(json.dumps([camera, dict(camera, title='Meine Videos')]))
        with patch.object(self.menu, 'APPS_FILE', path):
            items = self.menu.load_json(path)
        self.assertEqual(items[0], dict(camera, title='Kamera / Bilder / Videos'))
        self.assertEqual(items[1]['title'], 'Meine Videos')

    def test_double_click_fills_screen_without_restarting_video_and_restores_view(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
        self.browser.preview(self.clip)
        self.browser.resize(800, 600)
        self.browser.show(); self.browser.activateWindow(); self.app.processEvents()
        self.viewer.player.setPosition(1234)
        self.viewer.player.pause()
        content, stops = self.viewer.player.content, self.viewer.player.stops
        margins = self.viewer.layout().contentsMargins()
        geometry = self.browser.geometry()
        QTest.mouseDClick(self.viewer.surface, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.viewer.video_fullscreen)
        self.assertTrue(self.browser.isFullScreen())
        self.assertTrue(self.viewer.play_button.isHidden())
        self.assertTrue(self.viewer.name_label.isHidden())
        self.assertEqual(self.viewer.surface.size(), self.browser.size())
        self.assertIs(self.viewer.player.content, content)
        self.assertEqual(self.viewer.player.stops, stops)
        self.assertEqual(self.viewer.player.position(), 1234)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PausedState)
        QTest.mouseDClick(self.viewer.surface, Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(self.viewer.video_fullscreen)
        self.assertFalse(self.browser.isFullScreen())
        self.assertFalse(self.viewer.play_button.isHidden())
        self.assertEqual(self.viewer.layout().contentsMargins(), margins)
        self.assertEqual(self.browser.geometry(), geometry)
        self.assertEqual(self.viewer.player.stops, stops)
        self.assertEqual(self.viewer.player.position(), 1234)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PausedState)

    def test_fullscreen_shortcuts_preserve_browser_fullscreen_and_playback(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
        self.browser.preview(self.clip)
        self.browser.showFullScreen(); self.browser.activateWindow(); self.viewer.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.viewer, Qt.Key_F11)
        self.assertTrue(self.viewer.video_fullscreen)
        QTest.keyClick(self.viewer.surface, Qt.Key_Space)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PausedState)
        QTest.keyClick(self.viewer.surface, Qt.Key_Space)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PlayingState)
        QTest.keyClick(self.viewer.surface, Qt.Key_Escape)
        self.assertFalse(self.viewer.video_fullscreen)
        self.assertTrue(self.browser.isFullScreen())
        self.assertIs(self.browser.stack.currentWidget(), self.viewer)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PlayingState)
        self.viewer.fullscreen_button.click()
        self.assertTrue(self.viewer.video_fullscreen)
        self.viewer.fullscreen_button.click()
        QTest.keyClick(self.viewer, Qt.Key_Escape)
        self.assertIs(self.browser.stack.currentWidget(), self.browser.overview)
        self.assertIsNone(self.viewer.player.content.url)

    def check_backend_child_input(self):
        from PyQt5.QtCore import Qt, QEvent
        from PyQt5.QtGui import QKeyEvent
        from PyQt5.QtTest import QTest
        from PyQt5.QtWidgets import QVBoxLayout
        self.browser.preview(self.clip)
        self.browser.showFullScreen(); self.browser.activateWindow(); self.app.processEvents()
        # Created after playback starts, just like a backend-provided widget.
        child = BackendSurface(self.viewer.surface)
        child.setAttribute(Qt.WA_NativeWindow)
        child.setAttribute(Qt.WA_NoMousePropagation)
        child.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self.viewer.surface)
        layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(child)
        child.show(); child.setFocus(); self.app.processEvents()
        self.viewer.player.setPosition(3210)
        stops, content = self.viewer.player.stops, self.viewer.player.content
        QTest.mouseClick(child, Qt.LeftButton)
        QTest.mouseDClick(child, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.viewer.video_fullscreen)
        self.assertEqual(child.size(), self.browser.size())
        QTest.mouseDClick(child, Qt.LeftButton)
        self.assertFalse(self.viewer.video_fullscreen)
        QTest.mouseDClick(child, Qt.LeftButton)
        child.setFocus(); QTest.keyClick(child, Qt.Key_Space)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PausedState)
        QTest.keyClick(child, Qt.Key_Space)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PlayingState)
        QTest.keyClick(child, Qt.Key_F11)
        self.assertFalse(self.viewer.video_fullscreen)
        QTest.keyClick(child, Qt.Key_F11)
        QApplication.sendEvent(child, QKeyEvent(QEvent.KeyPress, Qt.Key_F11, Qt.NoModifier, '', True))
        self.assertTrue(self.viewer.video_fullscreen)
        QTest.keyClick(child, Qt.Key_Escape)
        self.assertFalse(self.viewer.video_fullscreen)
        self.assertIs(self.browser.stack.currentWidget(), self.viewer)
        self.assertIs(self.viewer.player.content, content)
        self.assertEqual(self.viewer.player.position(), 3210)
        self.assertEqual(self.viewer.player.stops, stops)
        self.assertFalse(self.viewer.play_button.isHidden())
        QTest.keyClick(self.browser, Qt.Key_F11)
        self.assertTrue(self.viewer.video_fullscreen)
        QTest.keyClick(self.browser, Qt.Key_Escape)
        self.assertFalse(self.viewer.video_fullscreen)
        QTest.keyClick(self.browser, Qt.Key_Escape)
        self.assertIs(self.browser.stack.currentWidget(), self.browser.overview)
        self.assertFalse(self.viewer.input_filter_installed)
        self.assertIsNone(self.viewer.player.content.url)

    def test_backend_child_cannot_swallow_double_click_and_keyboard(self):
        self.check_backend_child_input()

    @unittest.skipUnless(HAS_VIDEO_WIDGET, 'Real video widget input tests require Qt Multimedia.')
    def test_real_video_widget_with_native_backend_child_input(self):
        self.browser.done(QDialog.Rejected); self.browser.deleteLater(); self.app.processEvents()
        with patch.object(video, 'load_backend', return_value=(FakePlayer, FakeContent, QVideoWidget)):
            self.browser = self.menu.CameraBrowser()
        self.viewer = self.browser.video_viewer
        self.browser.images = [self.clip, self.photo, self.second]
        self.assertIsInstance(self.viewer.surface, QVideoWidget)
        self.check_backend_child_input()

    def test_input_filter_leaves_other_windows_controls_and_photos_alone(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
        self.browser.preview(self.clip)
        self.browser.showFullScreen(); self.browser.activateWindow(); self.app.processEvents()
        QTest.mouseDClick(self.viewer.play_button, Qt.LeftButton)
        QTest.mouseDClick(self.viewer.surface, Qt.RightButton)
        self.assertFalse(self.viewer.video_fullscreen)
        QTest.keyClick(self.viewer.surface, Qt.Key_F11, Qt.ControlModifier)
        self.assertFalse(self.viewer.video_fullscreen)
        other = QDialog(self.browser)
        other.show(); other.activateWindow(); self.app.processEvents()
        QTest.keyClick(other, Qt.Key_Escape)
        self.assertFalse(other.isVisible())
        self.assertIs(self.browser.stack.currentWidget(), self.viewer)
        self.browser.preview(self.photo)
        self.assertFalse(self.viewer.input_filter_installed)
        self.browser.activateWindow(); self.app.processEvents()
        QTest.keyClick(self.browser.viewer, Qt.Key_Escape)
        self.assertIs(self.browser.stack.currentWidget(), self.browser.overview)
        other.deleteLater()

    def test_dialog_reject_fallback_only_exits_fullscreen_before_overview(self):
        self.browser.preview(self.clip); self.viewer.toggle_fullscreen()
        self.browser.reject()
        self.assertFalse(self.viewer.video_fullscreen)
        self.assertIs(self.browser.stack.currentWidget(), self.viewer)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PlayingState)
        self.browser.reject()
        self.assertIs(self.browser.stack.currentWidget(), self.browser.overview)

    def test_pause_seek_volume_and_replay_at_end(self):
        self.browser.preview(self.clip)
        self.viewer.play_button.click()
        self.assertEqual(self.viewer.player.state(), FakePlayer.PausedState)
        self.viewer.duration_changed(8000)
        self.viewer.position_slider.setValue(500)
        self.assertEqual(self.viewer.player.position(), 4000)
        self.assertEqual(self.viewer.time_label.text(), '0:04 / 0:08')
        self.viewer.volume_slider.setValue(30)
        self.assertEqual(self.viewer.player.volume, 30)
        self.viewer.player.current_status = FakePlayer.EndOfMedia
        self.viewer.status_changed(FakePlayer.EndOfMedia)
        self.viewer.play_button.click()
        self.assertEqual(self.viewer.player.position(), 0)
        self.assertEqual(self.viewer.player.state(), FakePlayer.PlayingState)

    def test_back_and_close_release_file_and_ignore_late_errors(self):
        self.browser.preview(self.clip)
        self.viewer.toggle_fullscreen()
        self.browser.close()
        self.assertFalse(self.viewer.video_fullscreen)
        self.assertIs(self.browser.stack.currentWidget(), self.browser.overview)
        self.assertFalse(self.viewer.active)
        self.assertFalse(self.viewer.input_filter_installed)
        self.assertIsNone(self.viewer.player.content.url)
        video.log_event.reset_mock()
        self.viewer.player.error.emit(FakePlayer.FormatError)
        video.log_event.assert_not_called()
        self.browser.preview(self.second)
        self.viewer.toggle_fullscreen()
        self.browser.done(QDialog.Accepted)
        self.assertFalse(self.viewer.video_fullscreen)
        self.assertIsNone(self.viewer.player.content.url)
        self.assertFalse(self.browser.media_timer.isActive())

    def test_unplug_stops_playback_and_clears_overview(self):
        self.browser.preview(self.clip)
        self.viewer.toggle_fullscreen()
        self.browser.roots_signature = ('/media/kids/camera',)
        self.browser.check_media()
        self.assertIsNone(self.viewer.player.content.url)
        self.assertFalse(self.viewer.active)
        self.assertFalse(self.viewer.video_fullscreen)
        self.assertEqual(self.browser.images, [])
        self.assertIs(self.browser.stack.currentWidget(), self.browser.overview)

    def test_decode_failure_does_not_prevent_next_clip(self):
        self.browser.preview(self.clip)
        self.viewer.toggle_fullscreen()
        self.viewer.player.error.emit(FakePlayer.FormatError)
        self.assertFalse(self.viewer.video_fullscreen)
        self.assertFalse(self.viewer.status_label.isHidden())
        self.assertFalse(self.viewer.fullscreen_button.isEnabled())
        self.assertFalse(self.viewer.play_button.isEnabled())
        self.assertIn('nicht abgespielt', self.viewer.status_label.text())
        self.viewer.next_button.click()
        self.assertEqual(self.viewer.path, self.second)
        self.assertTrue(self.viewer.play_button.isEnabled())
        self.assertEqual(self.viewer.player.state(), FakePlayer.PlayingState)

    def test_eject_releases_video_before_unmount(self):
        self.browser.preview(self.clip)
        self.viewer.toggle_fullscreen()
        def unmount(command, **kwargs):
            self.assertEqual(command[:2], ['udisksctl', 'unmount'])
            self.assertIsNone(self.viewer.player.content.url)
            self.assertFalse(self.viewer.active)
            self.assertFalse(self.viewer.video_fullscreen)
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')
        with patch.object(self.camera, 'media_roots', return_value=['/media/kids/camera']), \
                patch.object(self.camera, 'STATE_DIR', self.root), \
                patch.object(self.camera.subprocess, 'check_output', return_value='/dev/test1\n'), \
                patch.object(self.camera.subprocess, 'run', side_effect=unmount):
            self.browser.eject_media()

    def test_escape_returns_to_overview_then_closes(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
        self.browser.preview(self.clip)
        self.browser.show(); self.browser.activateWindow(); self.viewer.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.viewer, Qt.Key_Escape)
        self.assertIs(self.browser.stack.currentWidget(), self.browser.overview)
        self.assertIsNone(self.viewer.player.content.url)
        QTest.keyClick(self.browser, Qt.Key_Escape)
        self.assertFalse(self.browser.isVisible())

    def test_missing_multimedia_keeps_photos_and_overview_available(self):
        with patch.object(video, 'load_backend', side_effect=ImportError('missing multimedia')):
            viewer = video.CameraVideoViewer(self.browser)
        viewer.open_videos([self.clip], self.clip)
        self.assertIsNone(viewer.player)
        self.assertFalse(viewer.play_button.isEnabled())
        self.assertFalse(viewer.fullscreen_button.isEnabled())
        viewer.toggle_fullscreen()
        self.assertFalse(viewer.video_fullscreen)
        self.assertIn('Eltern', viewer.status_label.text())
        self.browser.preview(self.photo)
        self.assertIs(self.browser.stack.currentWidget(), self.browser.viewer)
        viewer.stop(); viewer.deleteLater()


if __name__ == '__main__': unittest.main()
