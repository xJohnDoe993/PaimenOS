"""Kamera-Medien: Bildbetrachter und Galerie des Kinder-Menüs."""
from paimenos.i18n import t
from paimenos.paths import STATE_DIR
import json
import os
import subprocess
import fcntl
import threading
from collections import deque
from paimenos.images import (TaskSignals, submit_task, decode_image, thumbnail,
                            prepare_tuxpaint_image, select_tuxpaint_image)
from paimenos.media_files import scan_media, is_video
from paimenos.video import CameraVideoViewer
from paimenos.diagnostics import log_event
from paimenos.state import read_settings, atomic_json
from PyQt5 import sip
from PyQt5.QtCore import Qt, QTimer, QSize
from PyQt5.QtGui import QFont, QIcon, QPixmap, QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QMessageBox, QInputDialog, QDialog,
    QScrollArea, QStackedWidget, QShortcut, QToolButton
)
from paimenos.menu_support import find_program
from paimenos.menu_tiles import ARROW_KEYS, ArrowButton, ArrowToolButton

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
        self.header_buttons = []
        title = QLabel(t('📷 Bilder und Videos · SD-Karte / USB'), self)
        title.setFont(QFont("DejaVu Sans", 22, QFont.Bold))
        root.addWidget(title)
        header.addStretch()
        for text, callback in ((t('Aktualisieren'), self.reload_images),
                               (t('Medium auswerfen'), self.eject_media),
                               (t('Hauptmenü'), self.reject)):
            button = ArrowButton(text, self)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            header.addWidget(button)
            self.header_buttons.append(button)
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
        self.columns = 1
        self.generation = 0
        self.cancelled = threading.Event()
        self.task_signals = TaskSignals(self)
        self.task_signals.completed.connect(self.task_completed)
        self.image_buttons = {}
        self.thumbnail_queue = deque()
        self.thumbnail_running = 0
        self.scanning = False
        pages = QHBoxLayout()
        self.page_previous = ArrowButton(t('← Seite'), self)
        self.page_next = ArrowButton(t('Seite →'), self)
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
        columns = self.columns = max(1, (QApplication.primaryScreen().availableGeometry().width() - 80) // 225)
        start = self.page * self.PAGE_SIZE
        for index, path in enumerate(self.images[start:start + self.PAGE_SIZE]):
            name = os.path.basename(path)
            button = ArrowToolButton(self.container)
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
        if self.stack.currentWidget() is self.overview:
            (next(iter(self.image_buttons.values()), None) or self.header_buttons[0]).setFocus(Qt.OtherFocusReason)

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

    def navigate(self, action):
        """Arrow keys: up/down change the row, left/right the column; header and page rows at the ends."""
        if self.stack.currentWidget() is not self.overview:
            return
        tiles = list(self.image_buttons.values())
        pages = [button for button in (self.page_previous, self.page_next) if button.isEnabled()]
        focused = self.focusWidget()
        if focused in self.header_buttons:
            row, index = self.header_buttons, self.header_buttons.index(focused)
            first_down = tiles or pages
        elif focused in pages:
            row, index = pages, pages.index(focused)
            first_down = None
        else:
            row, index, first_down = None, 0, None
        if row is not None:
            if action in ('left', 'right'):
                target = row[max(0, min(len(row) - 1, index + (1 if action == 'right' else -1)))]
            elif action == 'down' and first_down:
                target = first_down[0]
            elif action == 'up' and row is pages:
                target = tiles[-1] if tiles else self.header_buttons[0]
            else:
                return
        else:
            index = tiles.index(focused) if focused in tiles else 0
            columns = self.columns
            if action == 'left':
                target = tiles[max(0, index - 1)] if tiles else None
            elif action == 'right':
                target = tiles[min(len(tiles) - 1, index + 1)] if tiles else None
            elif action == 'up':
                target = tiles[index - columns] if index - columns >= 0 else self.header_buttons[0]
            elif action == 'down':
                if not tiles:
                    target = pages[0] if pages else None
                elif index + columns < len(tiles):
                    target = tiles[index + columns]
                elif index // columns < (len(tiles) - 1) // columns:
                    target = tiles[-1]
                else:
                    target = pages[0] if pages else None
            else:
                return
        if target is not None:
            target.setFocus(Qt.OtherFocusReason)
            if target in tiles:
                self.scroll.ensureWidgetVisible(target)

    def keyPressEvent(self, event):
        key = event.key()
        if self.stack.currentWidget() is self.overview:
            if key in ARROW_KEYS:
                self.navigate(ARROW_KEYS[key])
                event.accept()
                return
            focused = self.focusWidget()
            if key in (Qt.Key_Return, Qt.Key_Enter) and isinstance(focused, (QPushButton, QToolButton)):
                focused.click()
                event.accept()
                return
        super().keyPressEvent(event)

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
