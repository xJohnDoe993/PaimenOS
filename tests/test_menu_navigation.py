"""Keyboard navigation of the kids menu, run headless with the offscreen Qt platform."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication
except ImportError:  # PyQt5 is a device dependency, not a requirement of the offline checks
    QApplication = None


def write_config(folder, app_count, category_tabs=False):
    settings = folder / 'settings.json'
    settings.write_text(json.dumps({
        'pin': '1234', 'bg_color': '#FF9F00', 'daily_limit_minutes': 0, 'bonus_minutes': 0,
        'today_used_seconds': 0, 'last_used_date': '', 'disabled_apps': [], 'category_tabs': category_tabs,
    }))
    apps = folder / 'apps.json'
    apps.write_text(json.dumps([
        {'id': f'app{i}', 'title': f'App {i}', 'type': 'native', 'command': 'true'} for i in range(app_count)
    ]))
    return settings, apps


@unittest.skipIf(QApplication is None, 'PyQt5 not installed')
class MenuNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_menu(self, app_count=10, columns=4, category_tabs=False):
        from paimenos import menu, menu_tiles, state
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        settings, apps = write_config(folder, app_count, category_tabs)
        for target, name, value in (
                (menu, 'STATE_DIR', folder), (menu, 'SETTINGS_FILE', str(settings)),
                (menu, 'APPS_FILE', str(apps)), (state, 'SETTINGS_FILE', str(settings)),
                (menu, 'has_media_attached', lambda: False), (menu, 'child_access_blocked', lambda: False),
                (menu_tiles, 'child_access_blocked', lambda: False)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('NetworkStatus', 'ControllerReader', 'log_event'):
            patcher = patch.object(menu, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        window = menu.PaimenOSMenu()
        window.resize(1100, 700)
        window.show()
        self.addCleanup(window.close)
        window.grid_columns = columns
        window.focus_tile(0)
        return window

    def press(self, window, key, modifier=Qt.NoModifier):
        QTest.keyClick(window.focusWidget() or window, key, modifier)

    def focused_index(self, window):
        return window.app_buttons.index(window.focusWidget())

    def test_up_down_change_row_and_left_right_change_column(self):
        window = self.make_menu(10, columns=4)
        self.press(window, Qt.Key_Right)
        self.assertEqual(self.focused_index(window), 1)
        self.press(window, Qt.Key_Down)
        self.assertEqual(self.focused_index(window), 5)
        self.press(window, Qt.Key_Left)
        self.assertEqual(self.focused_index(window), 4)
        self.press(window, Qt.Key_Up)
        self.assertEqual(self.focused_index(window), 0)

    def test_down_stops_at_last_tile(self):
        window = self.make_menu(10, columns=4)
        window.focus_tile(9)
        self.press(window, Qt.Key_Down)
        self.assertEqual(self.focused_index(window), 9)

    def test_up_from_first_row_reaches_toolbar_and_down_returns(self):
        window = self.make_menu(10, columns=4)
        window.focus_tile(2)
        self.press(window, Qt.Key_Up)
        self.assertIs(window.focusWidget(), window.toolbar_buttons[0])
        self.press(window, Qt.Key_Right)
        self.assertIs(window.focusWidget(), window.toolbar_buttons[1])
        self.press(window, Qt.Key_Down)
        self.assertEqual(self.focused_index(window), 2)

    def test_arrows_do_not_scroll_the_tile_area(self):
        window = self.make_menu(40, columns=4)
        bar = window.app_scroll.verticalScrollBar()
        before = bar.value()
        window.focus_tile(0)
        self.press(window, Qt.Key_Left)
        self.assertEqual(bar.value(), before)

    def test_home_and_end_jump_to_first_and_last_tile(self):
        window = self.make_menu(10)
        self.press(window, Qt.Key_End)
        self.assertEqual(self.focused_index(window), 9)
        self.press(window, Qt.Key_Home)
        self.assertEqual(self.focused_index(window), 0)

    def test_tab_switches_category_when_tabs_are_enabled(self):
        window = self.make_menu(10, category_tabs=True)
        if len(window.visible_categories) < 2:
            self.skipTest('only one category available in the test environment')
        first = window.selected_category
        self.press(window, Qt.Key_Tab)
        self.assertNotEqual(window.selected_category, first)
        self.press(window, Qt.Key_Backtab, Qt.ShiftModifier)
        self.assertEqual(window.selected_category, first)


if __name__ == '__main__':
    unittest.main()
