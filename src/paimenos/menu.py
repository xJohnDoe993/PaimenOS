#!/usr/bin/env python3
from paimenos.i18n import t, setup_qt, language, application_environment
from paimenos.paths import CONFIG_DIR, USER_DATA_DIR, ASSETS_DIR
from paimenos.paths import STATE_DIR
import json, os, shlex, shutil, socket, subprocess, sys, fcntl, traceback, threading, signal
from collections import deque
from paimenos.images import (TaskSignals, submit_task, decode_image, thumbnail,
                            prepare_tuxpaint_image, select_tuxpaint_image, tuxpaint_command)
from paimenos.media_files import scan_media, is_video
from paimenos.video import CameraVideoViewer
from paimenos.diagnostics import collect_diagnostics, diagnostics_text, log_event
from paimenos.state import read_settings, update_settings, atomic_json, remaining_seconds, app_with_current_title
from paimenos.parent_ui import ParentDialog
from paimenos.foreground import ForegroundGuard
from paimenos.screen_guard import child_access_blocked, screen_time_expired, protected_view_active
from paimenos.categories import CATEGORIES, available_categories, filter_category
from paimenos.controller import ControllerReader
from paimenos.network_status import NetworkStatus
from paimenos import hardware_profile
from datetime import date
from PyQt5 import sip
from PyQt5.QtCore import QLocale, Qt, QTimer, QTime, QDate, QSize, QPointF, QRectF
from PyQt5.QtGui import QFont, QIcon, QPixmap, QColor, QPainter, QImageReader, QKeySequence, QLinearGradient, QPen
from PyQt5.QtWidgets import (
    QApplication, QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QMessageBox, QInputDialog, QDialog,
    QCheckBox, QSpinBox, QFormLayout, QLineEdit, QScrollArea,
    QAbstractScrollArea, QStackedWidget, QShortcut, QPlainTextEdit, QToolButton, QStyle, QStyleOption
)

APPS_FILE = str(CONFIG_DIR / "apps.json")
SETTINGS_FILE = str(CONFIG_DIR / "settings.json")
ICONS_DIR = str(USER_DATA_DIR / "icons")
WEBAPP_BASE = os.path.expanduser("~/.mozilla/paimenos-webapps")
OVERLAY_SCRIPT = "/usr/local/lib/paimenos/current/run.py"

GRID_SPACING = 14
COLOR_PALETTE = [
    ("#FF9F00", t('🍊 Orange')),
    ("#34C759", t('🍏 Grün')),
    ("#007AFF", t('🌊 Blau')),
    ("#AF52DE", t('🍇 Violett')),
    ("#FF2D55", t('🍓 Pink')),
    ("#FFCC00", t('🍌 Gelb')),
    ("#5AC8FA", t('🩵 Hellblau')),
    ("#34495E", t('🩶 Graphit'))
]

def find_program(name):
    for candidate in ([name, "luanti"] if name == "minetest" else [name]):
        found = shutil.which(candidate)
        if found:
            return found
        for directory in ("/usr/games", "/usr/local/games", "/usr/bin"):
            path = os.path.join(directory, candidate)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return None

def is_online():
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=0.5):
            return True
    except OSError:
        return False

def media_roots():
    roots = []
    for base in ("/run/media/kids", "/media/kids"):
        try:
            for name in os.listdir(base):
                path = os.path.join(base, name)
                if os.path.isdir(path) and os.path.ismount(path):
                    roots.append(path)
        except OSError:
            pass
    return sorted(set(roots))

def has_media_attached():
    return bool(media_roots())

def get_battery_status():
    try:
        power_supply = "/sys/class/power_supply"
        if not os.path.isdir(power_supply):
            return None

        batteries = []
        for name in sorted(os.listdir(power_supply)):
            path = os.path.join(power_supply, name)
            if not os.path.isdir(path):
                continue
            try:
                with open(os.path.join(path, "type"), "r", encoding="utf-8") as f:
                    supply_type = f.read().strip().lower()
            except OSError:
                continue
            if supply_type != "battery":
                continue

            capacity = None
            try:
                with open(os.path.join(path, "capacity"), "r", encoding="utf-8") as f:
                    value = int(f.read().strip())
                if 0 <= value <= 100:
                    capacity = value
            except (OSError, ValueError):
                pass

            try:
                with open(os.path.join(path, "status"), "r", encoding="utf-8") as f:
                    status = f.read().strip().lower()
            except OSError:
                status = ""

            if capacity is not None:
                batteries.append((capacity, status))

        if not batteries:
            return None

        capacity = round(sum(item[0] for item in batteries) / len(batteries))
        statuses = {item[1] for item in batteries}
        charging = bool(statuses & {"charging", "full"})
        return capacity, charging
    except Exception:
        return None

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

ARROW_KEYS = {Qt.Key_Left: 'left', Qt.Key_Right: 'right', Qt.Key_Up: 'up', Qt.Key_Down: 'down'}


class ArrowPassThrough:
    """QAbstractButton treats arrows as plain focus next/previous; hand them to the window instead."""

    def keyPressEvent(self, event):
        if event.key() in ARROW_KEYS:
            event.ignore()
            return
        super().keyPressEvent(event)


class ToolbarButton(ArrowPassThrough, QPushButton):
    pass


class PaimenOSTile(ArrowPassThrough, QPushButton):
    # Fixed size including room for the painted shadow, lift and focus ring around the card.
    WIDTH, HEIGHT, RADIUS = 232, 190, 30
    LIGHT = False
    CARD_MARGINS = (9, 10, 9, 14)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)

        layout = QVBoxLayout(self)
        left, top, right, bottom = self.CARD_MARGINS
        layout.setContentsMargins(left + 12, top + 14, right + 12, bottom + 10)
        layout.setSpacing(6)

        self.icon_label = QLabel(self)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent; border: none;")
        self.icon_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        pixmap = self.load_tile_pixmap(item)
        if not pixmap.isNull():
            self.icon_label.setPixmap(pixmap.scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.title_label = QLabel(item.get("title", ""), self)
        self.title_label.setFont(QFont("DejaVu Sans", 15, QFont.Bold))
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("color: #1d2b3a; background: transparent; border: none;")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        layout.addWidget(self.icon_label, 1)
        layout.addWidget(self.title_label, 0)
        self.setStyleSheet("PaimenOSTile { background: transparent; border: none; }")

    def paintEvent(self, event):
        # Painted instead of QGraphicsDropShadowEffect: cheap enough for low-end devices.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        focused, hovered, pressed = self.hasFocus(), self.underMouse(), self.isDown()
        lift = 0 if pressed else 3 if (focused or hovered) else 0
        left, top, right, bottom = self.CARD_MARGINS
        card = QRectF(self.rect()).adjusted(left, top - lift, -right, -bottom - lift)
        radius = self.RADIUS
        painter.setPen(Qt.NoPen)
        # Ultra-low devices get one shadow layer instead of four.
        layers = ((2, 40),) if PaimenOSTile.LIGHT else ((7, 10), (5, 14), (3, 20), (1, 28))
        for spread, alpha in layers:
            painter.setBrush(QColor(20, 30, 50, alpha + (6 if lift else 0)))
            painter.drawRoundedRect(card.adjusted(-spread, -spread + 4 + lift, spread, spread + 2 + lift),
                                    radius + spread, radius + spread)
        fill = QLinearGradient(card.topLeft(), card.bottomLeft())
        fill.setColorAt(0, QColor(255, 255, 255))
        fill.setColorAt(1, QColor(236, 240, 246) if pressed else QColor(244, 246, 250))
        painter.setBrush(fill)
        painter.drawRoundedRect(card, radius, radius)
        if focused:
            # Inside the card so the ring contrasts on every background colour.
            painter.setPen(QPen(QColor("#172335"), 5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(card.adjusted(2.5, 2.5, -2.5, -2.5), radius - 2.5, radius - 2.5)
        painter.end()

    def load_tile_pixmap(self, item):
        icon_file = item.get("icon_file")
        if icon_file:
            path = os.path.join(ICONS_DIR, icon_file)
            candidates = [path]
            stem, ext = os.path.splitext(path)
            if ext.lower() != ".svg":
                candidates.append(stem + ".svg")
            for candidate in candidates:
                if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                    pix = QPixmap(candidate)
                    if not pix.isNull():
                        return pix

        icon_theme = item.get("icon_theme")
        if icon_theme and QIcon.hasThemeIcon(icon_theme):
            return QIcon.fromTheme(icon_theme).pixmap(128, 128)

        icon_text = item.get("icon_text")
        if icon_text:
            fallback = QPixmap(100, 100)
            fallback.fill(Qt.transparent)
            painter = QPainter(fallback)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QColor("#1d2b3a"))
            painter.setFont(QFont("DejaVu Sans", 48))
            painter.drawText(fallback.rect(), Qt.AlignCenter, icon_text)
            painter.end()
            return fallback

        fallback = QPixmap(100, 100)
        fallback.fill(Qt.transparent)
        painter = QPainter(fallback)
        painter.setRenderHint(QPainter.Antialiasing)
        title = item.get("title") or "?"
        bubble = QColor(COLOR_PALETTE[sum(map(ord, title)) % len(COLOR_PALETTE)][0])
        painter.setBrush(bubble)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, 100, 100, 30, 30)
        painter.setPen(QColor("white"))
        painter.setFont(QFont("DejaVu Sans", 42, QFont.Bold))
        char = title[0].upper()
        painter.drawText(fallback.rect(), Qt.AlignCenter, char)
        painter.end()
        return fallback


class CameraImageViewer(QWidget):
    """Bildbetrachter im Kamerafenster: keine verschachtelte Dialog-Kette."""
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.images = []
        self.index = 0
        self.zoom = 1.0
        self.pixmap = QPixmap()
        self.shortcuts = []
        self.load_generation = 0
        self.load_cancelled = threading.Event()
        self.task_signals = TaskSignals(self)
        self.task_signals.completed.connect(self.task_completed)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.image_label)
        layout.addWidget(self.scroll, 1)
        self.name_label = QLabel(self)
        self.name_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.name_label)
        controls = QGridLayout()
        for index, (text, callback) in enumerate((
            (t('← Vorheriges'), lambda: self.step(-1)),
            (t('Nächstes →'), lambda: self.step(1)),
            ("−", lambda: self.change_zoom(1 / 1.25)),
            ("+", lambda: self.change_zoom(1.25)),
            (t('Einpassen'), self.reset_zoom),
            ("🎨 Tux Paint", self.open_tuxpaint),
            (t('Zur Übersicht'), browser.show_overview),
        )):
            button = QPushButton(text, self)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.clicked.connect(callback)
            controls.addWidget(button, index // 4, index % 4)
            if text == "🎨 Tux Paint":
                self.paint_button = button
        layout.addLayout(controls)
        help_label = QLabel(t('← / ↑ vorheriges Bild · → / ↓ nächstes Bild · + / − Zoom · Esc Übersicht'), self)
        help_label.setAlignment(Qt.AlignCenter)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        for key, callback in (
            (Qt.Key_Left, lambda: self.step(-1)),
            (Qt.Key_Up, lambda: self.step(-1)),
            (Qt.Key_Right, lambda: self.step(1)),
            (Qt.Key_Down, lambda: self.step(1)),
            (Qt.Key_Plus, lambda: self.change_zoom(1.25)),
            (Qt.Key_Equal, lambda: self.change_zoom(1.25)),
            (Qt.Key_Minus, lambda: self.change_zoom(1 / 1.25)),
            (Qt.Key_Escape, browser.show_overview),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        self.setFocusPolicy(Qt.StrongFocus)

    def open_images(self, paths, path):
        self.images = list(paths)
        self.index = self.images.index(path)
        self.load_image()
        self.setFocus(Qt.OtherFocusReason)

    def cancel_pending(self):
        self.load_cancelled.set()
        self.load_cancelled = threading.Event()
        self.load_generation += 1
        self.paint_button.setEnabled(True)
        self.paint_button.setText("🎨 Tux Paint")

    def load_image(self):
        self.cancel_pending()
        self.zoom = 1.0
        self.pixmap = QPixmap()
        self.image_label.clear()
        self.image_label.setText(t('Bild wird geladen …'))
        self.image_label.resize(400, 80)
        path = self.images[self.index]
        self.update_name()
        submit_task(lambda: decode_image(path), ("image", self.load_generation, path),
                    self.task_signals, self.load_cancelled)
        self.scroll.horizontalScrollBar().setValue(0)
        self.scroll.verticalScrollBar().setValue(0)

    def update_name(self):
        if self.images:
            self.name_label.setText(f"{self.index + 1} / {len(self.images)} · "
                                   f"{os.path.basename(self.images[self.index])} · {round(self.zoom * 100)} %")

    def task_completed(self, token, value, error):
        kind, generation, path = token
        if generation != self.load_generation:
            return
        if kind == "image":
            if error:
                self.image_label.setText(error)
                log_event(t('Bilder'), f"{os.path.basename(path)}: {error}")
                return
            self.pixmap = QPixmap.fromImage(value)
            self.render_image()
        elif kind == "paint":
            self.paint_button.setEnabled(True)
            self.paint_button.setText("🎨 Tux Paint")
            if error:
                log_event("Tux Paint", error)
                QMessageBox.warning(self, "Tux Paint", t('Arbeitskopie konnte nicht erstellt werden:\n{value0}', value0=error))
                return
            menu = self.browser.parent()
            if not menu or getattr(menu, "active_process", None) is not None:
                return
            if "tuxpaint" in read_settings().get("disabled_apps", []):
                return
            try:
                select_tuxpaint_image(value)
                log_event("Tux Paint", t('Arbeitskopie vorbereitet: ') + os.path.basename(value["file"]))
                menu.launch({"command": find_program("tuxpaint"), "id": "tuxpaint"}, return_window=self.browser)
            except Exception as exc:
                log_event("Tux Paint", str(exc))
                QMessageBox.warning(self, "Tux Paint", t('Bild konnte nicht geöffnet werden:\n{value0}', value0=exc))

    def step(self, offset):
        target = min(max(0, self.index + offset), len(self.images) - 1)
        if target != self.index:
            self.index = target
            self.load_image()

    def reset_zoom(self):
        self.zoom = 1.0
        self.render_image()

    def change_zoom(self, factor):
        self.zoom = min(5.0, max(0.25, self.zoom * factor))
        self.render_image()

    def render_image(self):
        if sip.isdeleted(self):
            return
        if not self.images:
            return
        self.update_name()
        if self.pixmap.isNull():
            return
        viewport = self.scroll.viewport().size()
        fitted = self.pixmap.size().scaled(viewport, Qt.KeepAspectRatio)
        target = QSize(max(1, round(fitted.width() * self.zoom)),
                       max(1, round(fitted.height() * self.zoom)))
        # Die Anzeige nicht über die Auflösung der eingelesenen Vorschau vergrößern.
        if max(target.width(), target.height()) > 4096:
            target.scale(4096, 4096, Qt.KeepAspectRatio)
        self.image_label.setPixmap(self.pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.image_label.resize(target)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.render_image)

    def open_tuxpaint(self):
        if not self.images or not self.paint_button.isEnabled():
            return
        if not find_program("tuxpaint"):
            QMessageBox.information(self, "Tux Paint", t('Tux Paint ist nicht installiert.'))
            return
        if "tuxpaint" in read_settings().get("disabled_apps", []):
            QMessageBox.information(self, "Tux Paint", t('Tux Paint wurde im Elternbereich deaktiviert.'))
            return
        if getattr(self.browser.parent(), "active_process", None) is not None:
            return
        path = self.images[self.index]
        self.paint_button.setEnabled(False)
        self.paint_button.setText(t('Bild vorbereiten …'))
        submit_task(lambda: prepare_tuxpaint_image(path), ("paint", self.load_generation, path),
                    self.task_signals, self.load_cancelled)


class CameraBrowser(QDialog):
    PAGE_SIZE = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t('📷 Kamera – Bilder und Videos'))
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        bg = read_settings().get("bg_color", "#FF9F00")
        self.setStyleSheet(f"""
            QDialog, QWidget {{ background: {bg}; }}
            QLabel {{ color: white; font-size: 16px; }}
            QPushButton, QToolButton {{ background: rgba(255,255,255,0.25); color: white;
                border: 2px solid white; border-radius: 12px; padding: 10px;
                font-size: 16px; font-weight: bold; }}
            QPushButton:hover, QToolButton:hover {{ background: rgba(255,255,255,0.45); }}
        """)
        layout = QVBoxLayout(self)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack)
        self.overview = QWidget(self)
        root = QVBoxLayout(self.overview)
        header = QHBoxLayout()
        title = QLabel(t('📷 Bilder und Videos · SD-Karte / USB'), self)
        title.setFont(QFont("DejaVu Sans", 22, QFont.Bold))
        root.addWidget(title)
        header.addStretch()
        for text, callback in ((t('Aktualisieren'), self.reload_images),
                               (t('Medium auswerfen'), self.eject_media),
                               (t('Hauptmenü'), self.reject)):
            button = QPushButton(text, self)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            header.addWidget(button)
        root.addLayout(header)
        self.info = QLabel(self)
        root.addWidget(self.info)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(15)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)
        self.stack.addWidget(self.overview)
        self.viewer = CameraImageViewer(self)
        self.stack.addWidget(self.viewer)
        self.video_viewer = CameraVideoViewer(self)
        self.stack.addWidget(self.video_viewer)
        self.images = []
        self.last_path = None
        self.page = 0
        self.generation = 0
        self.cancelled = threading.Event()
        self.task_signals = TaskSignals(self)
        self.task_signals.completed.connect(self.task_completed)
        self.image_buttons = {}
        self.thumbnail_queue = deque()
        self.thumbnail_running = 0
        self.scanning = False
        pages = QHBoxLayout()
        self.page_previous = QPushButton(t('← Seite'), self)
        self.page_next = QPushButton(t('Seite →'), self)
        self.page_previous.clicked.connect(lambda: self.change_page(-1))
        self.page_next.clicked.connect(lambda: self.change_page(1))
        self.page_label = QLabel(self)
        self.page_label.setAlignment(Qt.AlignCenter)
        for button in (self.page_previous, self.page_next):
            button.setAutoDefault(False)
        pages.addWidget(self.page_previous)
        pages.addWidget(self.page_label, 1)
        pages.addWidget(self.page_next)
        root.addLayout(pages)
        self.roots_signature = tuple(self.media_roots())
        self.media_timer = QTimer(self)
        self.media_timer.timeout.connect(self.check_media)
        self.media_timer.start(1500)
        self.reload_images()

    def media_roots(self):
        return media_roots()

    def check_media(self):
        roots = tuple(self.media_roots())
        if roots != self.roots_signature:
            self.roots_signature = roots
            self.show_overview()
            self.reload_images()

    def reset_jobs(self):
        self.cancelled.set()
        self.cancelled = threading.Event()
        self.generation += 1
        self.thumbnail_queue.clear()
        self.thumbnail_running = 0

    def clear_grid(self):
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        self.image_buttons = {}

    def reload_images(self):
        self.video_viewer.stop()
        self.reset_jobs()
        self.clear_grid()
        self.images = []
        self.page = 0
        roots = self.media_roots()
        self.roots_signature = tuple(roots)
        self.page_previous.setEnabled(False)
        self.page_next.setEnabled(False)
        self.page_label.clear()
        if not roots:
            self.scanning = False
            self.info.setText(t('Bitte Kamera-Speicherkarte oder USB-Medium einstecken.'))
            return
        self.info.setText(t('Bilder und Videos werden gesucht … Du kannst jederzeit zurückgehen.'))
        self.scanning = True
        cancel = self.cancelled
        submit_task(lambda: scan_media(roots, cancel), ("scan", self.generation, ""),
                    self.task_signals, cancel)

    def task_completed(self, token, value, error):
        kind, generation, path = token
        if generation != self.generation:
            return
        if kind == "scan":
            self.scanning = False
            if error:
                self.info.setText(t('Bilder und Videos konnten nicht gesucht werden: ') + error)
                log_event(t('Medien'), error)
                return
            self.images = value
            self.render_page()
        elif kind == "thumb":
            self.thumbnail_running -= 1
            button = self.image_buttons.get(path)
            if button:
                if error:
                    button.setText(t('Nicht lesbar\n') + os.path.basename(path))
                    button.setToolTip(path + "\n" + error)
                else:
                    button.setIcon(QIcon(QPixmap.fromImage(value)))
                    button.setIconSize(QSize(180, 135))
            self.schedule_thumbnails()

    def change_page(self, offset):
        maximum = max(0, (len(self.images) - 1) // self.PAGE_SIZE)
        target = min(maximum, max(0, self.page + offset))
        if target != self.page:
            self.page = target
            self.render_page()

    def render_page(self):
        self.reset_jobs()
        self.clear_grid()
        videos = sum(is_video(path) for path in self.images)
        self.info.setText(t('{value0} Bild(er) und {value1} Video(s) gefunden.', value0=len(self.images) - videos, value1=videos))
        pages = max(1, (len(self.images) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page_label.setText(t('Seite {value0} / {value1}', value0=self.page + 1, value1=pages))
        self.page_previous.setEnabled(self.page > 0)
        self.page_next.setEnabled(self.page + 1 < pages)
        columns = max(1, (QApplication.primaryScreen().availableGeometry().width() - 80) // 225)
        start = self.page * self.PAGE_SIZE
        for index, path in enumerate(self.images[start:start + self.PAGE_SIZE]):
            name = os.path.basename(path)
            button = QToolButton(self.container)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setText(name if len(name) <= 23 else name[:20] + "…")
            if is_video(path):
                button.setText("▶ Video\n" + button.text())
            button.setFixedSize(210, 190)
            button.setToolTip(path)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda checked=False, p=path: self.preview(p))
            self.grid.addWidget(button, index // columns, index % columns)
            self.image_buttons[path] = button
            if not is_video(path):
                self.thumbnail_queue.append(path)
        if not self.images:
            self.grid.addWidget(QLabel(t('Keine unterstützten Bilder oder Videos gefunden.'), self), 0, 0)
        self.schedule_thumbnails()

    def schedule_thumbnails(self):
        while self.thumbnail_queue and self.thumbnail_running < 2:
            path = self.thumbnail_queue.popleft()
            self.thumbnail_running += 1
            submit_task(lambda p=path: thumbnail(p), ("thumb", self.generation, path),
                        self.task_signals, self.cancelled)

    def preview(self, path):
        if path not in self.images:
            return
        self.last_path = path
        if is_video(path):
            self.viewer.cancel_pending()
            self.stack.setCurrentWidget(self.video_viewer)
            self.video_viewer.open_videos([p for p in self.images if is_video(p)], path)
            return
        self.video_viewer.stop()
        self.stack.setCurrentWidget(self.viewer)
        self.viewer.open_images([p for p in self.images if not is_video(p)], path)
        QTimer.singleShot(0, self.viewer.render_image)

    def show_overview(self):
        if self.stack.currentWidget() is self.video_viewer and self.video_viewer.path:
            self.last_path = self.video_viewer.path
        elif self.stack.currentWidget() is self.viewer and self.viewer.images:
            self.last_path = self.viewer.images[self.viewer.index]
        self.video_viewer.stop()
        self.stack.setCurrentWidget(self.overview)
        self.viewer.cancel_pending()
        if self.last_path in self.images:
            wanted = self.images.index(self.last_path) // self.PAGE_SIZE
            if wanted != self.page:
                self.page = wanted
                self.render_page()
        button = self.image_buttons.get(self.last_path)
        if button:
            button.setFocus(Qt.OtherFocusReason)
            self.scroll.ensureWidgetVisible(button)

    def reject(self):
        if self.stack.currentWidget() is self.video_viewer and self.video_viewer.video_fullscreen:
            self.video_viewer.escape()
            return
        # Auch Esc/Alt-F4 im Bildbetrachter kehrt zuerst zur Übersicht zurück.
        if self.stack.currentWidget() in (self.viewer, self.video_viewer):
            self.show_overview()
        else:
            self.reset_jobs()
            self.viewer.cancel_pending()
            self.media_timer.stop()
            super().reject()

    def done(self, result):
        self.video_viewer.stop()
        self.reset_jobs()
        self.viewer.cancel_pending()
        self.media_timer.stop()
        super().done(result)

    def closeEvent(self, event):
        # Fenster-Schließen gibt den Clip auch im Vollbild sofort frei.
        event.ignore()
        if self.stack.currentWidget() is self.video_viewer:
            self.show_overview()
        else:
            self.reject()

    def eject_media(self):
        roots = self.media_roots()
        if not roots:
            QMessageBox.information(self, t('Kamera'), t('Kein Medium ist eingehängt.'))
            return
        root = roots[0]
        if len(roots) > 1:
            choice, ok = QInputDialog.getItem(self, t('Auswerfen'), t('Welches Medium?'), roots, 0, False)
            if not ok:
                return
            root = choice
        self.show_overview()
        try:
            source = subprocess.check_output(["findmnt", "--mountpoint", root, "-o", "SOURCE", "-n"],
                                             text=True, timeout=5).strip()
            marker = os.path.join(str(STATE_DIR), "paimenos-ejected.json")
            lock_path = os.path.join(str(STATE_DIR), "paimenos-media-operation.lock")
            with open(lock_path, "a", encoding="utf-8") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    with open(marker, encoding="utf-8") as handle:
                        ejected = json.load(handle)
                except (OSError, ValueError):
                    ejected = []
                result = subprocess.run(["udisksctl", "unmount", "-b", source, "--no-user-interaction"],
                                        capture_output=True, text=True, timeout=20)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip() or t('Auswerfen fehlgeschlagen.'))
                atomic_json(marker, sorted(set(ejected + [source])))
            self.reload_images()
        except Exception as exc:
            QMessageBox.warning(self, t('Auswerfen'), t('Medium konnte nicht ausgeworfen werden:\n{value0}', value0=exc))



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
            btn = QPushButton(name, self)
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

        top_layout.addStretch()

        self.timer_info_label = QLabel()
        self.timer_info_label.setFont(QFont("DejaVu Sans", 16, QFont.Bold))
        self.timer_info_label.setObjectName('chip')
        top_layout.addWidget(self.timer_info_label, 0, Qt.AlignVCenter)
        top_layout.addSpacing(6)

        color_btn = ToolbarButton(t('🎨 Farbe'))
        color_btn.setObjectName("colorBtn")
        color_btn.setMinimumSize(120, 50)
        color_btn.setCursor(Qt.PointingHandCursor)
        color_btn.clicked.connect(self.open_color_picker)
        top_layout.addWidget(color_btn)

        parent_btn = ToolbarButton(t('🔒 Eltern-Bereich'))
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
        self.controller.available_changed.connect(lambda count: self.controller_hint.setVisible(count > 0))
        self.controller_hint.setVisible(bool(self.controller.devices))

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
        self.category_bar.setVisible(tabs_enabled)
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
        settings = load_json(SETTINGS_FILE)
        correct_pin = settings["pin"]

        # Protect the PIN prompt as well as the authenticated parent view.
        shield = QWidget()
        shield.setObjectName('parentShield')
        shield.setStyleSheet('QWidget#parentShield {background:#172335;}')
        foreground = ForegroundGuard(shield)
        shield.showFullScreen()
        try:
            pin, ok = QInputDialog.getText(
                shield, t('🔒 Eltern-PIN'), t('Bitte Eltern-PIN eingeben:'), QLineEdit.Password
            )
            if ok and pin != correct_pin:
                QMessageBox.warning(shield, t('Falsch'), t('Falscher PIN!'))
        finally:
            shield.hide()
            foreground.release()
            shield.deleteLater()

        if ok and pin == correct_pin:
            dlg = ParentDialog(self)
            try:
                if dlg.exec_() == QDialog.Accepted:
                    self.reload_apps()
            finally:
                dlg.deleteLater()
        if screen_time_expired():
            self.hide_child_views()

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
            cmd = [sys.executable, '-I', OVERLAY_SCRIPT, 'close_overlay', '--browser', profile_dir, user_js, url]
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
        self.active_is_webapp = bool(url)
        self.overlay_proc = None
        try:
            log_path = os.path.join(str(STATE_DIR), "paimenos-application.log")
            # Pro Start begrenzt: kein über Monate wachsendes gemeinsames Log.
            self.application_log = open(log_path, "w", encoding="utf-8")
            log_event(t('Anwendung'), "Start: " + os.path.basename(cmd[0]))
            self.active_process = subprocess.Popen(cmd, start_new_session=True, env=application_environment(),
                                                   stdout=self.application_log, stderr=self.application_log)
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

        request_file = os.path.join(str(STATE_DIR), "paimenos-menu.raise")
        if os.path.exists(request_file):
            os.unlink(request_file)
            if getattr(self, "active_process", None) is None and not child_access_blocked():
                target = QApplication.activeModalWidget() or self
                target.showFullScreen()
                target.raise_()
                target.activateWindow()
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
                     tuple(settings.get("disabled_apps", [])), settings.get("bg_color"), settings.get("category_tabs", False))
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
