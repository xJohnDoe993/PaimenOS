"""Shared kids-design colours and their use in the Firefox webapp bar and the overlays."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from paimenos import theme, webapp


class ThemeColorTests(unittest.TestCase):
    def test_gradient_matches_the_menu_background(self):
        # Reference values of QColor.lighter(114) / darker(110) used by the kids menu.
        for color, expected in (('#FF9F00', ('#ffac24', '#e89100')), ('#34495E', ('#3b536b', '#2f4255')),
                                ('#FFCC00', ('#ffd324', '#e8b900')), ('#007AFF', ('#248dff', '#006fe8'))):
            self.assertEqual(theme.gradient(color), expected)

    def test_invalid_colors_fall_back_to_the_default(self):
        for value in (None, 'red', '#12', 42, '#GGGGGG'):
            self.assertEqual(theme.normalize(value), theme.DEFAULT_COLOR.lower())


class WebappBarColorTests(unittest.TestCase):
    def prepare(self, **patch_args):
        with tempfile.TemporaryDirectory() as folder, patch.object(webapp, 'read_settings', **patch_args):
            webapp.prepare_profile(folder, str(Path(folder) / 'missing-template.js'))
            return (Path(folder) / 'chrome/paimenos-webapp.css').read_text()

    def test_bar_uses_the_chosen_color(self):
        style = self.prepare(return_value={'bg_color': '#007AFF'})
        self.assertIn('--paimenos-bar-top: #248dff; --paimenos-bar-bottom: #006fe8;', style)
        self.assertTrue(style.endswith((ROOT / 'assets/webapp.css').read_text()))

    def test_bar_falls_back_to_orange_without_settings(self):
        style = self.prepare(side_effect=ValueError('missing'))
        self.assertIn('--paimenos-bar-top: #ffac24; --paimenos-bar-bottom: #e89100;', style)


try:
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QApplication
except ImportError:  # PyQt5 is a device dependency, not a requirement of the offline checks
    QApplication = None


@unittest.skipIf(QApplication is None, 'PyQt5 not installed')
class OverlayDesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_status_overlay_takes_the_chosen_color_and_renders(self):
        import socket
        from paimenos import status_overlay
        reader, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(reader.close); self.addCleanup(sender.close)
        overlay = status_overlay.StatusOverlay(reader)
        self.addCleanup(overlay.shutdown)
        with patch.object(status_overlay, 'read_settings', return_value={'bg_color': '#34C759'}):
            overlay.load_accent()
        self.assertEqual(overlay.accent.name(), '#34c759')
        overlay.level, overlay.kind = 40, 'brightness'
        image = overlay.grab().toImage()
        self.assertEqual(image.pixelColor(image.width() // 2, 2).name(), '#34c759')  # frame in the chosen colour
        self.assertGreater(image.pixelColor(image.width() // 2, image.height() // 2 - 15).lightness(), 235)  # light card

    def test_status_overlay_keeps_default_color_when_settings_are_missing(self):
        import socket
        from paimenos import status_overlay
        reader, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(reader.close); self.addCleanup(sender.close)
        overlay = status_overlay.StatusOverlay(reader)
        self.addCleanup(overlay.shutdown)
        with patch.object(status_overlay, 'read_settings', side_effect=ValueError('missing')):
            overlay.load_accent()
        self.assertEqual(overlay.accent.name(), theme.DEFAULT_COLOR.lower())

    def test_menu_button_corners_show_the_bar_gradient_not_black(self):
        import os
        from paimenos import close_overlay
        with patch.object(close_overlay, 'FullscreenTracker'), \
                patch.object(close_overlay.os, 'pidfd_open', side_effect=OSError), \
                patch.object(close_overlay, 'read_settings', return_value={'bg_color': '#007AFF'}):
            overlay = close_overlay.CloseButtonOverlay(os.getpid())
        self.addCleanup(overlay.close)
        self.addCleanup(overlay.monitor.stop)
        image = overlay.grab().toImage()
        corner = image.pixelColor(0, 0)
        self.assertGreater(corner.blue(), 180)
        self.assertGreater(corner.blue(), corner.red() + 60)


if __name__ == '__main__':
    unittest.main()
