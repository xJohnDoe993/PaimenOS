#!/usr/bin/env python3
from paimenos.i18n import t, setup_qt, language, application_environment
from paimenos.paths import CONFIG_DIR, ASSETS_DIR
from paimenos.paths import STATE_DIR
import json
import os
import shlex
import shutil
import subprocess
import sys
import fcntl
import traceback
import signal
import socket
from paimenos.images import (TaskSignals, submit_task, tuxpaint_command)
from paimenos.diagnostics import collect_diagnostics, diagnostics_text, log_event
from paimenos.state import read_settings, update_settings, atomic_json, app_with_current_title
from paimenos.screen_guard import ActiveAppLease, network_editor_marker, child_access_blocked, screen_time_expired, protected_view_active
from paimenos.categories import CATEGORIES, available_categories, filter_category
from paimenos.controller import ControllerReader
from paimenos.network_status import NetworkStatus
from paimenos.speech import TitleSpeaker, spoken_source
from paimenos import hardware_profile
from PyQt5 import sip
from paimenos.menu_support import find_program, get_battery_status
from paimenos.printing import queue_status
from paimenos.menu_tiles import ARROW_KEYS, COLOR_PALETTE, ArrowButton, PaimenOSTile
from paimenos.menu_camera import CameraBrowser, has_media_attached
from PyQt5.QtCore import QLocale, Qt, QTimer, QTime, QDate, QSize, QPointF
from PyQt5.QtGui import QFont, QIcon, QColor, QPainter
from PyQt5.QtWidgets import (
    QApplication, QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QMessageBox, QDialog,
    QScrollArea, QAbstractScrollArea, QPlainTextEdit, QStyle,
    QStyleOption
)

APPS_FILE = str(CONFIG_DIR / "apps.json")
SETTINGS_FILE = str(CONFIG_DIR / "settings.json")
WEBAPP_BASE = os.path.expanduser("~/.mozilla/paimenos-webapps")
PARENT_URL = "http://localhost/"


def parent_area_reachable(host="127.0.0.1", port=80, timeout=1.0):
    """Läuft der Webbereich? Sonst bekommt die Kachel eine Meldung statt eines leeren Browserfensters."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
OVERLAY_SCRIPT = "/usr/local/lib/paimenos/current/run.py"

GRID_SPACING = 14


def load_json(path):
    if path == SETTINGS_FILE:
        return read_settings()
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        if path == APPS_FILE:
            return [app_with_current_title(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
        return value
    except (OSError, ValueError):
        return [] if path == APPS_FILE else {}

def save_json(path, data):
    try:
        if path == SETTINGS_FILE:
            update_settings(data)
        else:
            atomic_json(path, data)
        return True
    except Exception as exc:
        QMessageBox.critical(None, t('Fehler'), t('Speichern fehlgeschlagen: {value0}', value0=exc))
        return False




class ColorPickerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t('🎨 Hintergrundfarbe wählen'))
        self.setFixedSize(420, 260)
        self.selected_color = None

        settings = load_json(SETTINGS_FILE)
        current_bg = settings.get("bg_color", "#FF9F00")

        self.setStyleSheet("""
            QDialog { background: #222; }
            QLabel { color: white; font-size: 18px; font-weight: bold; }
        """)

        layout = QVBoxLayout(self)
        label = QLabel(t('Wähle deine Lieblingsfarbe:'), self)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        grid = QGridLayout()
        grid.setSpacing(15)
        self.color_buttons = []

        for i, (hex_code, name) in enumerate(COLOR_PALETTE):
            btn = ArrowButton(name, self)
            btn.setMinimumSize(85, 60)
            btn.setCursor(Qt.PointingHandCursor)
            
            border_style = "4px solid white" if hex_code.lower() == current_bg.lower() else "2px solid rgba(255,255,255,0.5)"
            
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {hex_code};
                    color: white; font-size: 14px; font-weight: bold;
                    border: {border_style}; border-radius: 22px;
                }}
                QPushButton:hover, QPushButton:focus {{ border: 4px solid #FFF; }}
            """)
            btn.setFocusPolicy(Qt.StrongFocus)
            btn.clicked.connect(lambda checked=False, col=hex_code: self.choose_color(col))
            grid.addWidget(btn, i // 4, i % 4)
            self.color_buttons.append(btn)

        layout.addLayout(grid)
        self.color_buttons[0].setFocus(Qt.OtherFocusReason)

    def navigate(self, action):
        self.controller_action(action)

    def controller_action(self, action):
        if action == 'back':
            self.reject()
            return
        focused = self.focusWidget()
        index = self.color_buttons.index(focused) if focused in self.color_buttons else 0
        if action == 'activate':
            self.color_buttons[index].click()
            return
        step = {'left': -1, 'right': 1, 'up': -4, 'down': 4}.get(action, 0)
        target = max(0, min(len(self.color_buttons) - 1, index + step))
        self.color_buttons[target].setFocus(Qt.OtherFocusReason)

    def choose_color(self, hex_code):
        self.selected_color = hex_code
        self.accept()


class DiagnosticsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t('Geräte-Diagnose'))
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton(t('Aktualisieren'), self)
        self.refresh_button.clicked.connect(self.refresh)
        close = QPushButton(t('Zurück'), self)
        close.clicked.connect(self.reject)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.signals = TaskSignals(self)
        self.signals.completed.connect(self.completed)
        self.refresh()

    def refresh(self):
        self.text.setPlainText(t('Diagnose wird gesammelt …'))
        self.refresh_button.setEnabled(False)
        submit_task(collect_diagnostics, "diagnostics", self.signals)

    def completed(self, token, value, error):
        self.refresh_button.setEnabled(True)
        self.text.setPlainText(error if error else diagnostics_text(value))



class PaimenOSMenu(QWidget):
    def __init__(self):
        super().__init__()
        self.active_process = None
        self.return_window = None
        self.camera_browser = None
        self.overlay_proc = None
        self.time_blocked = False
        self.speaker = TitleSpeaker(self)
        self.setWindowTitle(t('PaimenOS Kinder-Menü'))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName('kidsMenu')
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(40, 20, 40, 30)

        top_layout = QHBoxLayout()
        time_box = QVBoxLayout()
        self.time_label = QLabel()
        self.time_label.setFont(QFont("DejaVu Sans", 38, QFont.Bold))
        self.date_label = QLabel()
        self.date_label.setFont(QFont("DejaVu Sans", 16))

        time_box.addWidget(self.time_label)
        time_box.addWidget(self.date_label)
        top_layout.addLayout(time_box)

        self.battery_label = QLabel()
        self.battery_label.setFont(QFont("DejaVu Sans", 18, QFont.Bold))
        self.battery_label.setAlignment(Qt.AlignCenter)
        self.battery_label.setToolTip(t('Akku'))
        self.battery_label.setObjectName('chip')
        top_layout.addSpacing(18)
        top_layout.addWidget(self.battery_label, 0, Qt.AlignVCenter)

        # Offene Druckaufträge; nur sichtbar, solange etwas in der Warteschlange ist.
        self.print_label = QLabel()
        self.print_label.setFont(QFont("DejaVu Sans", 18, QFont.Bold))
        self.print_label.setAlignment(Qt.AlignCenter)
        self.print_label.setObjectName('chip')
        self.print_label.hide()
        top_layout.addSpacing(10)
        top_layout.addWidget(self.print_label, 0, Qt.AlignVCenter)

        top_layout.addStretch()

        self.timer_info_label = QLabel()
        self.timer_info_label.setFont(QFont("DejaVu Sans", 16, QFont.Bold))
        self.timer_info_label.setObjectName('chip')
        top_layout.addWidget(self.timer_info_label, 0, Qt.AlignVCenter)
        top_layout.addSpacing(6)

        color_btn = ArrowButton(t('🎨 Farbe'))
        color_btn.setObjectName("colorBtn")
        color_btn.setMinimumSize(120, 50)
        color_btn.setCursor(Qt.PointingHandCursor)
        color_btn.clicked.connect(self.open_color_picker)
        top_layout.addWidget(color_btn)

        parent_btn = ArrowButton(t('🔒 Eltern-Bereich'))
        parent_btn.setObjectName("parentBtn")
        parent_btn.setMinimumSize(180, 50)
        parent_btn.setCursor(Qt.PointingHandCursor)
        parent_btn.clicked.connect(self.open_parent_menu)
        top_layout.addWidget(parent_btn)
        self.toolbar_buttons = [color_btn, parent_btn]
        for btn in self.toolbar_buttons:
            btn.setFocusPolicy(Qt.StrongFocus)
        self.last_tile_index = 0

        self.main_layout.addLayout(top_layout)

        self.selected_category = 'all'
        self.visible_categories = ['all']
        self.category_bar = QWidget()
        self.category_bar.setObjectName('categoryBar')
        category_layout = QHBoxLayout(self.category_bar)
        category_layout.setContentsMargins(0, 8, 0, 6)
        category_layout.setSpacing(10)
        category_layout.addStretch()
        self.category_buttons = {}
        for key, title in CATEGORIES:
            btn = QPushButton()
            btn.setIcon(QIcon(os.path.expanduser('~/.local/share/paimenos/icons/category-' + key + '.svg')))
            btn.setIconSize(QSize(36, 36))
            btn.setFixedSize(84, 60)
            btn.setToolTip(title)
            btn.setAccessibleName(title)
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, category=key: self.select_category(category))
            category_layout.addWidget(btn)
            self.category_buttons[key] = btn
        category_layout.addStretch()
        self.main_layout.addWidget(self.category_bar)

        self.app_scroll = QScrollArea(self)
        self.app_scroll.setWidgetResizable(True)
        self.app_scroll.setFrameShape(QAbstractScrollArea.NoFrame)
        self.app_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.app_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.app_scroll.setFocusPolicy(Qt.StrongFocus)
        self.app_scroll.viewport().setObjectName('appViewport')
        self.app_scroll.setStyleSheet("""
            QScrollArea, QWidget#appViewport { background: transparent; border: none; }
            QScrollBar:vertical {
                background: rgba(255,255,255,0.18); width: 18px; margin: 4px 0 4px 8px; border-radius: 9px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.85); min-height: 55px; border-radius: 9px;
            }
            QScrollBar::handle:vertical:hover { background: white; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)

        self.app_container = QWidget()
        self.app_container.setObjectName('appContainer')
        self.grid_layout = QGridLayout(self.app_container)
        # Tiles carry their own shadow margin, so the visible gap is larger than the spacing.
        self.grid_layout.setContentsMargins(0, 4, 8, 8)
        self.grid_layout.setHorizontalSpacing(GRID_SPACING)
        self.grid_layout.setVerticalSpacing(GRID_SPACING)
        self.app_scroll.setWidget(self.app_container)
        self.main_layout.addWidget(self.app_scroll, 1)
        self.app_buttons = []
        self.controller_hint = QLabel(t('Steuerkreuz / linker Stick: wählen · Bestätigungstaste: öffnen · Schultertasten: Kategorie · Start: Farbe / Eltern · Rechte Taste: zurück'))
        self.controller_hint.setFont(QFont('DejaVu Sans', 12))
        self.controller_hint.setWordWrap(True)
        self.controller_hint.setAlignment(Qt.AlignCenter)
        self.controller_hint.hide()
        self.main_layout.addWidget(self.controller_hint)
        self.keyboard_hint = QLabel()
        self.keyboard_hint.setFont(QFont('DejaVu Sans', 12))
        self.keyboard_hint.setWordWrap(True)
        self.keyboard_hint.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(self.keyboard_hint)

        self.network_status = NetworkStatus()
        self.last_online_state = None
        self.last_media_state = None
        self.config_signature = None

        self.apply_theme()
        self.reload_apps()

        timer = QTimer(self)
        timer.timeout.connect(self.update_clock_and_net)
        timer.start(3000)
        self.update_clock_and_net()
        self.controller = ControllerReader(self, self.controller_enabled)
        self.controller.action.connect(self.controller_action)
        self.controller.available_changed.connect(lambda count: self.show_input_hint(count > 0))
        self.show_input_hint(bool(self.controller.devices))

    def show_input_hint(self, controller_connected):
        """Show the controller help when one is connected, otherwise the keyboard help."""
        self.controller_hint.setVisible(controller_connected)
        text = t('Pfeiltasten: wählen · Enter: öffnen')
        if not self.category_bar.isHidden():
            text += ' · ' + t('Tab: Kategorie wechseln')
        self.keyboard_hint.setText(text)
        self.keyboard_hint.setVisible(not controller_connected)

    def apply_theme(self):
        settings = load_json(SETTINGS_FILE)
        bg_color = settings.get("bg_color", "#FF9F00")
        base = QColor(bg_color)
        if not base.isValid():
            base = QColor("#FF9F00")
        top, bottom = base.lighter(114).name(), base.darker(110).name()
        PaimenOSTile.LIGHT = hardware_profile.current() == 'ultra-low'

        # Dialogs opened from the menu keep the plain colour; only the menu gets the gradient.
        self.setStyleSheet(f"""
            QWidget {{ background: {bg_color}; }}
            QWidget#kidsMenu {{ background: qlineargradient(x1:0, y1:0, x2:0.35, y2:1, stop:0 {top}, stop:1 {bottom}); }}
            QWidget#kidsMenu > QLabel, QWidget#categoryBar, QWidget#appContainer {{ background: transparent; }}
            QLabel {{ color: white; }}
            QLabel#chip {{
                background: rgba(255, 255, 255, 0.24); border: 2px solid rgba(255, 255, 255, 0.45);
                border-radius: 23px; padding: 6px 18px; min-height: 30px;
            }}
            QPushButton#parentBtn {{
                background: rgba(0, 0, 0, 0.28); color: white; border: 2px solid rgba(255, 255, 255, 0.8);
                border-radius: 25px; padding: 0 20px; font-size: 18px; font-weight: bold;
            }}
            QPushButton#parentBtn:hover {{ background: rgba(0, 0, 0, 0.45); }}
            QPushButton#colorBtn {{
                background: rgba(255, 255, 255, 0.3); color: white; border: 2px solid rgba(255, 255, 255, 0.8);
                border-radius: 25px; padding: 0 20px; font-size: 18px; font-weight: bold;
            }}
            QPushButton#colorBtn:hover {{ background: rgba(255, 255, 255, 0.5); color: #333; }}
            QPushButton#parentBtn:focus, QPushButton#colorBtn:focus {{ border: 4px solid #FFE66D; }}
        """)
        self.update()

        try:
            subprocess.Popen(["/usr/bin/xsetroot", "-solid", bg_color])
        except Exception:
            pass

    def paintEvent(self, event):
        option = QStyleOption()
        option.initFrom(self)
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)
        if PaimenOSTile.LIGHT:
            painter.end()
            return
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        for x, y, size, alpha in ((0.93, 0.06, 0.20, 30), (0.02, 0.98, 0.26, 24), (0.62, 1.04, 0.14, 18)):
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(QPointF(w * x, h * y), w * size, w * size)
        painter.end()

    def open_color_picker(self):
        dlg = ColorPickerDialog(self)
        if dlg.exec_() == QDialog.Accepted and dlg.selected_color:
            settings = load_json(SETTINGS_FILE)
            save_json(SETTINGS_FILE, {"bg_color": dlg.selected_color})
            self.apply_theme()

    def reload_apps(self):
        focused = self.focusWidget()
        focused_id = focused.item.get('id') if focused in self.app_buttons else None
        toolbar_focus = focused if focused in self.toolbar_buttons else None
        online = self.network_status.value()
        media_present = has_media_attached()

        self.last_online_state = online
        self.last_media_state = media_present

        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.app_buttons = []

        all_apps = load_json(APPS_FILE)
        settings = load_json(SETTINGS_FILE)
        disabled = set(settings.get("disabled_apps", []))

        visible_apps = []
        for app in all_apps:
            app_id = app.get("id")
            app_type = app.get("type")

            if app_id in disabled:
                continue

            if app.get("enabled") is False:
                continue

            if not online and app_type == "webapp":
                continue

            if app_type == "camera" and not media_present:
                continue

            visible_apps.append(app)

        tabs_enabled = settings.get('category_tabs') is True
        self.speaker.set_gender(settings.get('speak_voice'))
        self.speaker.set_enabled(settings.get('speak_titles') is True)
        # Auch aktuell ausgeblendete Apps (offline, ohne Medium) bekommen ihre Aufnahme schon jetzt.
        self.speaker.ensure_recordings(spoken_source(app) for app in all_apps
                                       if app.get('id') not in disabled and app.get('enabled') is not False)
        self.category_bar.setVisible(tabs_enabled)
        self.show_input_hint(self.controller_hint.isVisibleTo(self))
        self.visible_categories = available_categories(visible_apps)
        if not tabs_enabled or self.selected_category not in self.visible_categories:
            self.selected_category = 'all'
        for key, btn in self.category_buttons.items():
            btn.setVisible(key in self.visible_categories)
            btn.setChecked(key == self.selected_category)
            btn.setStyleSheet('background:white;border:4px solid #172335;border-radius:30px;' if key == self.selected_category else 'background:rgba(255,255,255,0.6);border:2px solid rgba(255,255,255,0.75);border-radius:30px;')
        visible_apps = filter_category(visible_apps, self.selected_category)

        visible_apps.sort(key=lambda item: (
            2 if item.get("id") == "poweroff" else 1 if item.get("type") == "camera" else 0,
            str(item.get("title", "")).casefold()
        ))
        for row in range(self.grid_layout.rowCount()):
            self.grid_layout.setRowStretch(row, 0)
        available_width = QApplication.primaryScreen().availableGeometry().width() - 100
        self.grid_columns = min(4, max(1, (available_width + GRID_SPACING) // (PaimenOSTile.WIDTH + GRID_SPACING)))
        for i, item in enumerate(visible_apps):
            tile = PaimenOSTile(item)
            tile.clicked.connect(lambda checked=False, x=item: self.launch(x))

            row, col = divmod(i, self.grid_columns)
            self.grid_layout.addWidget(tile, row, col)
            self.app_buttons.append(tile)

        for col in range(4):
            self.grid_layout.setColumnStretch(col, 0)
        self.grid_layout.setRowStretch((len(visible_apps) + self.grid_columns - 1) // self.grid_columns, 1)

        for current, nxt in zip(self.app_buttons, self.app_buttons[1:]):
            QWidget.setTabOrder(current, nxt)

        if self.app_buttons:
            self.last_tile_index = next((i for i, btn in enumerate(self.app_buttons)
                                         if btn.item.get('id') == focused_id), 0)
            self.focus_tile(self.last_tile_index)
        if toolbar_focus is not None:
            toolbar_focus.setFocus(Qt.OtherFocusReason)

    def select_category(self, category):
        if category not in self.visible_categories or not self.category_bar.isVisible():
            return
        self.selected_category = category
        self.reload_apps()
        self.app_scroll.verticalScrollBar().setValue(0)
        self.focus_tile(0)

    def cycle_category(self, direction):
        if self.category_bar.isVisible() and self.visible_categories:
            index = self.visible_categories.index(self.selected_category)
            self.select_category(self.visible_categories[(index + direction) % len(self.visible_categories)])

    def focusNextPrevChild(self, forward):
        # Tab / Shift+Tab switch the category tab instead of walking the focus chain.
        if self.category_bar.isVisible() and QApplication.activeModalWidget() is None:
            if not child_access_blocked():
                self.cycle_category(1 if forward else -1)
            return True
        return super().focusNextPrevChild(forward)

    def speak_title(self, title):
        self.speaker.say(title)

    def focus_tile(self, index):
        if self.app_buttons:
            self.last_tile_index = max(0, min(len(self.app_buttons) - 1, index))
            tile = self.app_buttons[self.last_tile_index]
            tile.setFocus(Qt.OtherFocusReason)
            self.app_scroll.ensureWidgetVisible(tile, 30, 30)

    def controller_enabled(self):
        if self.active_process is not None or not self.isVisible():
            return False
        if QApplication.applicationState() != Qt.ApplicationActive:
            return False
        if child_access_blocked():
            return False
        modal = QApplication.activeModalWidget()
        # Nur der Kinder-Farbwähler ist per Controller bedienbar, keine PIN/Eltern-Dialoge.
        if modal is not None:
            return isinstance(modal, ColorPickerDialog) and modal.parent() is self
        return QApplication.activeWindow() is self

    def controller_action(self, action):
        if not self.controller_enabled():
            return
        modal = QApplication.activeModalWidget()
        if modal is not None:
            modal.controller_action(action)
            return
        if action in ('category_prev', 'category_next'):
            self.cycle_category(-1 if action == 'category_prev' else 1)
            return
        self.navigate(action)

    def navigate(self, action):
        """Shared by keyboard and controller: up/down change the row, left/right the column."""
        focused = self.focusWidget()
        if action == 'toolbar':
            self.toolbar_buttons[0].setFocus(Qt.OtherFocusReason)
            return
        if focused in self.toolbar_buttons:
            index = self.toolbar_buttons.index(focused)
            if action == 'activate':
                focused.click()
            elif action in ('back', 'down'):
                self.focus_tile(self.last_tile_index)
            elif action in ('left', 'right'):
                target = max(0, min(1, index + (1 if action == 'right' else -1)))
                self.toolbar_buttons[target].setFocus(Qt.OtherFocusReason)
            return
        if not self.app_buttons:
            self.toolbar_buttons[0].setFocus(Qt.OtherFocusReason)
            return
        index = self.app_buttons.index(focused) if focused in self.app_buttons else self.last_tile_index
        if action == 'activate':
            self.app_buttons[index].click()
        elif action == 'back':
            self.focus_tile(0)
        elif action == 'up' and index < self.grid_columns:
            self.last_tile_index = index
            self.toolbar_buttons[0].setFocus(Qt.OtherFocusReason)
        else:
            step = {'left': -1, 'right': 1, 'up': -self.grid_columns, 'down': self.grid_columns}.get(action, 0)
            self.focus_tile(index + step)

    def closeEvent(self, event):
        self.controller.close()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if child_access_blocked():
            event.accept()
            return
        key = event.key()

        if key in (Qt.Key_Tab, Qt.Key_Backtab):
            super().keyPressEvent(event)
            return

        if key in ARROW_KEYS:
            self.navigate(ARROW_KEYS[key])
            event.accept()
            return

        focused = self.focusWidget()
        if focused in self.toolbar_buttons or not self.app_buttons:
            super().keyPressEvent(event)
            return

        try:
            index = self.app_buttons.index(focused)
        except ValueError:
            index = 0

        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.app_buttons[index].click()
        elif key == Qt.Key_Home:
            self.focus_tile(0)
        elif key == Qt.Key_End:
            self.focus_tile(len(self.app_buttons) - 1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def open_parent_menu(self):
        """Der Eltern-Bereich ist der Webbereich (Port 80) in einem Browserfenster; die PIN gibt es dort."""
        if not parent_area_reachable():
            QMessageBox.warning(self, t('Eltern-Bereich'), t('Der Eltern-Bereich startet gerade nicht.\n'
                                'Bitte kurz warten und erneut versuchen. Wenn es bleibt: Gerät neu starten.'))
            return
        self.launch({'id': 'parent-area', 'title': t('Eltern-Bereich'), 'url': PARENT_URL,
                     'profile': 'parent-area', 'parent_area': True})

    def handle_network_editor_request(self):
        """Der Webbereich (Eltern-Bereich) bittet um den Netzwerkeditor; das Fenster öffnet sich über dem Browser."""
        request_file = os.path.join(str(STATE_DIR), "paimenos-menu.network-editor")
        finished = getattr(self, "network_editor", None)
        if finished is not None and finished.poll() is not None:
            # Vom Elternteil geschlossen: Zombie einsammeln und den "Zum Menü"-Knopf wieder freigeben.
            self.network_editor = None
            try:
                os.unlink(network_editor_marker())
            except OSError:
                pass
        if not os.path.exists(request_file):
            return
        os.unlink(request_file)
        running = getattr(self, "network_editor", None)
        editor = shutil.which("nm-connection-editor")
        if not editor or child_access_blocked() or (running is not None and running.poll() is None):
            return
        try:
            self.network_editor = subprocess.Popen([editor], start_new_session=True, env=application_environment(),
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Der "Zum Menü"-Knopf bleibt sonst über dem Editor stehen und verdeckt dessen Fensterleiste.
            with open(network_editor_marker(), 'w', encoding='utf-8') as marker:
                marker.write(str(self.network_editor.pid))
        except OSError as exc:
            log_event(t('Anwendung'), str(exc))

    def close_network_editor(self):
        """Der Netzwerkeditor gehört zum Eltern-Bereich: Er bleibt nicht über dem Kinder-Menü stehen."""
        editor = getattr(self, "network_editor", None)
        try:
            os.unlink(network_editor_marker())
        except OSError:
            pass
        if editor is not None and editor.poll() is None:
            editor.terminate()
            # nm-connection-editor ignoriert SIGTERM gelegentlich: nach kurzer Frist erzwingen.
            QTimer.singleShot(2000, lambda: editor.poll() is None and editor.kill())
        self.network_editor = None

    def hide_child_views(self):
        self.time_blocked = True
        browser = getattr(self, 'camera_browser', None)
        if browser is not None and not sip.isdeleted(browser):
            browser.hide()
        self.hide()

    def restore_child_view(self, target=None):
        if child_access_blocked():
            self.hide_child_views()
            if target is not None and not sip.isdeleted(target):
                target.hide()
            return False
        if target is None or sip.isdeleted(target):
            target = self
        self.time_blocked = False
        self.showFullScreen()
        target.showFullScreen()
        target.raise_()
        target.activateWindow()
        return True

    def showEvent(self, event):
        super().showEvent(event)
        if screen_time_expired() and not protected_view_active():
            QTimer.singleShot(0, self.hide_child_views)

    def launch(self, item, return_window=None):
        self.speaker.stop()
        if getattr(self, "active_process", None) is not None:
            return
        if item.get("command") == "__POWEROFF__":
            subprocess.Popen(["systemctl", "poweroff"], start_new_session=True)
            return
        if child_access_blocked():
            if screen_time_expired() and not protected_view_active():
                self.hide_child_views()
            return
        if item.get("command") == "__CAMERA__":
            dialog = getattr(self, "camera_browser", None)
            if dialog is None or sip.isdeleted(dialog):
                dialog = CameraBrowser(self)
                self.camera_browser = dialog
                dialog.finished.connect(lambda result, browser=dialog: self.camera_finished(browser))
            # Kein exec_(): hide() beim Start von Tux Paint würde dessen
            # verschachtelte Ereignisschleife beenden und das Fenster löschen.
            dialog.showFullScreen()
            dialog.raise_()
            dialog.activateWindow()
            return
        url = item.get("url")
        if url:
            # Profilname darf das Profilverzeichnis nicht verlassen.
            profile = os.path.basename(item.get("profile") or "webapp")
            if profile in (".", ".."):
                profile = "webapp"
            profile_dir = os.path.join(WEBAPP_BASE, profile)
            user_js = os.path.expanduser("~/.mozilla/firefox/paimenos-user.js")
            mode = '--parent-browser' if item.get('parent_area') else '--browser'
            cmd = [sys.executable, '-I', OVERLAY_SCRIPT, 'close_overlay', mode, profile_dir, user_js, url]
        else:
            cmd = shlex.split(item.get("command", ""))
            if cmd:
                cmd[0] = find_program(cmd[0]) or cmd[0]
        if cmd and (item.get("id") == "tuxpaint" or os.path.basename(cmd[0]) == "tuxpaint"):
            cmd = tuxpaint_command(cmd[0], cmd[1:])
        if cmd and item.get("id") == "supertuxkart":
            # STK creates its config on the first run; apply the hardware profile once afterwards.
            try:
                hardware_profile.apply_stk(hardware_profile.current(), only_pending=True)
            except (OSError, ValueError) as exc:
                log_event(t('Anwendung'), 'SuperTuxKart: ' + str(exc))
        if not cmd:
            return
        self.return_window = return_window
        self.expected_exit_codes = {0}
        self.release_app_lease()
        self.active_is_webapp = bool(url)
        self.overlay_proc = None
        try:
            log_path = os.path.join(str(STATE_DIR), "paimenos-application.log")
            # Pro Start begrenzt: kein über Monate wachsendes gemeinsames Log.
            self.application_log = open(log_path, "w", encoding="utf-8")
            log_event(t('Anwendung'), "Start: " + os.path.basename(cmd[0]))
            self.active_process = subprocess.Popen(cmd, start_new_session=True, env=application_environment(),
                                                   stdout=self.application_log, stderr=self.application_log)
            if not item.get('parent_area'):
                # Aktive Bildschirmzeit: gezählt wird nur, solange diese Lease gehalten wird.
                self.app_lease = ActiveAppLease()
        except Exception as exc:
            if getattr(self, "application_log", None):
                self.application_log.close()
            log_event(t('Anwendung'), str(exc))
            QMessageBox.critical(self, t('Fehler'), t('Fehler beim Starten:\n{value0}', value0=exc))
            return
        self.hide()
        if return_window:
            return_window.hide()
        # The browser supervisor owns the overlay and waits for profile release.
        self.process_timer = QTimer(self)
        self.process_timer.timeout.connect(self.check_process)
        self.process_timer.start(250)

    def check_process(self):
        if self.active_process is None or self.active_process.poll() is None:
            return
        self.process_timer.stop()
        self.process_timer.deleteLater()
        if self.overlay_proc and self.overlay_proc.poll() is None:
            self.overlay_proc.terminate()
        self.close_network_editor()
        self.release_app_lease()
        code = self.active_process.returncode
        self.application_log.close()
        self.active_process = None
        target = self.return_window
        self.return_window = None
        if not self.restore_child_view(target):
            return
        if target is None or sip.isdeleted(target):
            target = self
        if code not in self.expected_exit_codes:
            log_event(t('Anwendung'), t('Programm mit Fehlercode {value0} beendet.', value0=code))
            if code == 75 and self.active_is_webapp:
                QMessageBox.warning(target, "Webapp", t('Diese Webapp ist noch geöffnet oder wird gerade geschlossen.\nBitte kurz warten und erneut öffnen.'))
                return
            QMessageBox.warning(target, t('Programmstart'), t('Das Programm wurde mit Fehlercode {value0} beendet.\nDetails stehen im Elternbereich unter Geräte-Diagnose.', value0=code))

    def release_app_lease(self):
        lease = getattr(self, 'app_lease', None)
        self.app_lease = None
        if lease is not None:
            lease.close()

    def stop_for_shutdown(self):
        # KillMode=mixed sends SIGTERM only to this menu. Give the supervisor
        # time to resume a stopped emulator and request RetroArch's normal QUIT
        # (which writes the .state.auto used by savestate_auto_load).
        if getattr(self, '_shutting_down', False):
            return
        self._shutting_down = True
        process = self.active_process
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    print('Application did not finish saving before shutdown timeout.',
                          file=sys.stderr, flush=True)
        except ProcessLookupError:
            pass
        finally:
            QApplication.quit()

    def camera_finished(self, browser):
        if getattr(self, "camera_browser", None) is browser:
            self.camera_browser = None
        if getattr(self, "return_window", None) is browser:
            self.return_window = None
        browser.deleteLater()
        if getattr(self, "active_process", None) is None:
            self.restore_child_view()

    def update_print_queue(self):
        queue = queue_status()
        if not queue or not queue[0]:
            self.print_label.hide()
            return
        count, held = queue
        self.print_label.setText(f"🖨️ {count}")
        self.print_label.setToolTip(t('Druckaufträge: {value0}', value0=count)
                                    + (t(' · {value0} warten auf die Eltern', value0=held) if held else ''))
        self.print_label.show()

    def update_clock_and_net(self):
        self.time_label.setText(QTime.currentTime().toString("HH:mm"))
        self.date_label.setText(QLocale().toString(QDate.currentDate(), "dddd, d MMMM yyyy" if language() == "en" else "dddd, d. MMMM yyyy"))

        battery = get_battery_status()
        if battery is None:
            self.battery_label.clear()
            self.battery_label.setToolTip("")
            self.battery_label.hide()
        else:
            capacity, charging = battery
            if capacity >= 80:
                icon = "🔋"
            elif capacity >= 50:
                icon = "🔋"
            elif capacity >= 20:
                icon = "🪫"
            else:
                icon = "🪫"
            prefix = "⚡ " if charging else ""
            self.battery_label.setText(f"{prefix}{icon} {capacity}%")
            self.battery_label.show()
            self.battery_label.setToolTip(
                t('Akku: {value0}%', value0=capacity) + (t(' – wird geladen') if charging else "")
            )

        self.update_print_queue()

        request_file = os.path.join(str(STATE_DIR), "paimenos-menu.raise")
        if os.path.exists(request_file):
            os.unlink(request_file)
            if getattr(self, "active_process", None) is None and not child_access_blocked():
                target = QApplication.activeModalWidget() or self
                target.showFullScreen()
                target.raise_()
                target.activateWindow()
        self.handle_network_editor_request()
        current_online = self.network_status.value()
        current_media = has_media_attached()

        try:
            settings = load_json(SETTINGS_FILE)
        except (OSError, ValueError, KeyError, TypeError):
            self.hide_child_views()
            return
        if screen_time_expired() and not protected_view_active():
            self.hide_child_views()
        elif (self.time_blocked and not child_access_blocked()
              and self.active_process is None and not self.isVisible()):
            # A parent can grant bonus time remotely while the menu is hidden.
            self.restore_child_view()
        signature = (os.stat(APPS_FILE).st_mtime_ns if os.path.exists(APPS_FILE) else 0,
                     tuple(settings.get("disabled_apps", [])), settings.get("bg_color"), settings.get("category_tabs", False),
                     settings.get("speak_titles", False), settings.get("speak_voice"))
        config_changed = signature != self.config_signature
        if config_changed:
            self.config_signature = signature
            self.apply_theme()
        if config_changed or current_online != self.last_online_state or current_media != self.last_media_state:
            self.reload_apps()

        settings = load_json(SETTINGS_FILE)
        limit_min = settings.get("daily_limit_minutes", 0)
        bonus_min = settings.get("bonus_minutes", 0)
        total_limit_min = limit_min + bonus_min

        if limit_min > 0:
            used_sec = settings.get("today_used_seconds", 0)
            rem_sec = max(0, (total_limit_min * 60) - used_sec)
            rem_min = rem_sec // 60
            self.timer_info_label.setText(t('⏳ Restzeit: {value0} Min.', value0=rem_min))
        else:
            self.timer_info_label.setText("")
        if settings.get("allowed_from") and settings.get("allowed_until"):
            window = t('🕒 Nutzungszeit: {value0}–{value1} Uhr', value0=settings["allowed_from"], value1=settings["allowed_until"])
            self.timer_info_label.setText((self.timer_info_label.text() + "  ·  " + window).lstrip(" ·"))
        self.timer_info_label.setVisible(bool(self.timer_info_label.text()))

def report_exception(kind, value, trace):
    # Qt-Slot-Ausnahmen nachvollziehbar machen, statt das Menü still zu beenden.
    details = "".join(traceback.format_exception(kind, value, trace))
    print(details, file=sys.stderr)
    try:
        with open(os.path.join(str(STATE_DIR), "paimenos-errors.log"), "a", encoding="utf-8") as handle:
            handle.write(details + "\n")
    except OSError:
        pass
    QMessageBox.critical(QApplication.activeWindow(), t('PaimenOS-Fehler'), str(value))

if __name__ == "__main__":
    instance = open(os.path.join(str(STATE_DIR), "paimenos-menu.lock"), "a")
    try:
        fcntl.flock(instance, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        Path = os.path.join(str(STATE_DIR), "paimenos-menu.raise")
        with open(Path, "w", encoding="utf-8"):
            pass
        sys.exit(0)
    sys.excepthook = report_exception
    app = QApplication(sys.argv)
    setup_qt(app)
    app.setWindowIcon(QIcon(str(ASSETS_DIR / 'branding/paimenos-logo.png')))
    window = PaimenOSMenu()
    signal.signal(signal.SIGTERM, lambda signum, frame: window.stop_for_shutdown())
    signal.signal(signal.SIGINT, lambda signum, frame: window.stop_for_shutdown())
    window.showFullScreen()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec_())
