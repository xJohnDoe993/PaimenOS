"""Tile title handling of the kids menu, run headless with the offscreen Qt platform."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication
except ImportError:  # PyQt5 is a device dependency, not a requirement of the offline checks
    QApplication = None


@unittest.skipIf(QApplication is None, 'PyQt5 not installed')
class TileTitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_tile(self, title):
        from paimenos.menu_tiles import PaimenOSTile
        tile = PaimenOSTile({'id': 'x', 'title': title, 'type': 'native', 'command': 'true'})
        tile.show()
        tile.activateWindow()
        QTest.qWaitForWindowActive(tile)
        self.addCleanup(tile.close)
        return tile

    def test_short_title_is_shown_unchanged(self):
        label = self.make_tile('Der Elefant').title_label
        self.assertFalse(label.overflows())
        self.assertEqual(label.display_text(), 'Der Elefant')

    def test_long_title_is_shortened_at_the_end_not_cut_on_both_sides(self):
        label = self.make_tile('Zelda Twilight Princess HD Remake').title_label
        self.assertTrue(label.overflows())
        shown = label.display_text()
        self.assertTrue(shown.startswith('Zelda'))
        self.assertTrue(shown.endswith('…'))
        self.assertLessEqual(label.fontMetrics().horizontalAdvance(shown), label.contentsRect().width())

    def test_focus_scrolls_the_full_title_and_blur_stops_it(self):
        tile = self.make_tile('Zelda Twilight Princess HD Remake')
        label = tile.title_label
        tile.clearFocus()  # activating the window already focuses the only tile
        self.assertFalse(label.timer.isActive())
        tile.setFocus(Qt.OtherFocusReason)
        self.assertTrue(label.scrolling)
        self.assertTrue(label.timer.isActive())
        for _ in range(label.PAUSE_TICKS + 10):
            label.advance()
        self.assertGreater(label.offset, 0)
        last = label.text_width() - label.contentsRect().width()
        for _ in range(400):
            label.advance()
            self.assertLessEqual(label.offset, last)
        tile.clearFocus()
        self.assertFalse(label.scrolling)
        self.assertEqual(label.offset, 0)
        self.assertFalse(label.timer.isActive())

    def test_short_title_never_starts_the_timer(self):
        tile = self.make_tile('Der Elefant')
        tile.clearFocus()
        tile.setFocus(Qt.OtherFocusReason)
        self.assertFalse(tile.title_label.timer.isActive())


if __name__ == '__main__':
    unittest.main()
