#!/usr/bin/env python3
"""Menü-Knopf im freigehaltenen Bereich der Firefox-Leiste."""
from paimenos.i18n import t, setup_qt
import os
import signal
import sys
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter
from PyQt5.QtWidgets import QApplication, QPushButton, QWidget
from paimenos import theme
from paimenos.state import read_settings
from paimenos.webapp import BAR_HEIGHT, MENU_WIDTH
from paimenos.fullscreen import FullscreenTracker
from paimenos.screen_guard import child_access_blocked, network_editor_open


class CloseButtonOverlay(QWidget):
    def __init__(self, target_pid, close_pid=None):
        super().__init__()
        self.target_pid = target_pid
        self.close_pid = close_pid or target_pid
        self.fullscreen = FullscreenTracker(target_pid)
        self.pidfd = None
        try:
            if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
                self.pidfd = os.pidfd_open(self.close_pid)
        except OSError:
            pass
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                            Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.bar_top, self.bar_bottom = (QColor(c) for c in theme.gradient(None))
        self.button = QPushButton(t('⌂ Zum Menü'), self)
        self.button.setFocusPolicy(Qt.NoFocus)
        self.button.setAccessibleName(t('Webapp schließen und zum Kinder-Menü zurückkehren'))
        self.button.setToolTip(t('Zurück zu deinen Apps und Spielen'))
        self.button.setStyleSheet('''QPushButton {
            background:#ffffff;color:#1d2b3a;font-family:"DejaVu Sans";
            font-size:18px;font-weight:bold;border-radius:22px;
            border:2px solid rgba(255,255,255,0.8);
        } QPushButton:hover {background:#fff3d1;}
        QPushButton:pressed {background:#e6ebf4;}
        QPushButton:disabled {background:rgba(255,255,255,0.35);color:rgba(29,43,58,0.55);}''')
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(self.close_target)
        self.load_colors()
        self.place_button()
        QApplication.primaryScreen().geometryChanged.connect(self.place_button)
        self.monitor = QTimer(self)
        self.monitor.timeout.connect(self.update_overlay)
        self.monitor.start(250)

    def load_colors(self):
        try:
            color = read_settings().get('bg_color')
        except (OSError, ValueError):
            color = None
        self.bar_top, self.bar_bottom = (QColor(c) for c in theme.gradient(color))
        self.update()

    def paintEvent(self, event):
        # Same gradient as the Firefox bar behind this window; without an X11 compositor a
        # translucent window would show black corners around the rounded button.
        bar = BAR_HEIGHT
        top, bottom = self.bar_top, self.bar_bottom
        def at(y):
            f = y / bar
            return QColor(*(round(a + (b - a) * f) for a, b in
                            zip((top.red(), top.green(), top.blue()), (bottom.red(), bottom.green(), bottom.blue()))))
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, at(10))
        gradient.setColorAt(1, at(bar - 10))
        QPainter(self).fillRect(self.rect(), gradient)

    def button_width(self):
        """Breite aus dem Text (englisch ist länger als deutsch), mindestens MENU_WIDTH."""
        font = QFont('DejaVu Sans')
        font.setPixelSize(18)
        font.setBold(True)
        return max(MENU_WIDTH, QFontMetrics(font).horizontalAdvance(self.button.text()) + 48)

    def place_button(self, *args):
        screen = QApplication.primaryScreen().geometry()
        height = BAR_HEIGHT - 20
        # Openbox's global maximize rule must not enlarge this managed tool window.
        width = self.button_width()
        self.setFixedSize(width, height)
        self.setGeometry(screen.right() - width - 11, screen.top() + 10, width, height)
        self.button.setGeometry(0, 0, width, height)

    def close_target(self):
        # Recheck on click too: expiry can occur between two monitor ticks.
        if child_access_blocked():
            self.button.setEnabled(False)
            self.hide()
            return
        self.button.setEnabled(False)
        try:
            if self.pidfd is not None:
                signal.pidfd_send_signal(self.pidfd, signal.SIGTERM)
            else:
                os.kill(self.close_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            self.button.setEnabled(True)
            self.button.setToolTip(t('Schließen fehlgeschlagen: ') + str(exc))
            return
        QApplication.quit()

    def check_target(self):
        try:
            if self.pidfd is not None:
                import select
                if not select.select([self.pidfd], [], [], 0)[0]:
                    return True
            else:
                os.kill(self.target_pid, 0)
                return True
        except ProcessLookupError:
            pass
        except OSError:
            return True
        QApplication.quit()
        return False

    def update_overlay(self):
        if not self.check_target():
            return
        blocked = child_access_blocked()
        self.button.setEnabled(not blocked)
        if blocked or self.fullscreen.is_fullscreen() or network_editor_open():
            self.hide()
        elif not self.isVisible():
            # WA_ShowWithoutActivating bleibt gesetzt: das Video verliert
            # beim Verlassen des Vollbilds weder Fokus noch Tastaturbedienung.
            self.load_colors()
            self.place_button()
            self.show()

    def closeEvent(self, event):
        self.monitor.stop()
        self.fullscreen.close()
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None
        super().closeEvent(event)


if __name__ == '__main__':
    # Reuse the established launcher target so desktop-only updates work with
    # the previous shared run.py too.
    if len(sys.argv) > 1 and sys.argv[1] in ('--browser', '--parent-browser'):
        from paimenos.browser import run_browser
        if len(sys.argv) != 5:
            raise SystemExit(t('Aufruf: close_overlay --browser PROFIL VORLAGE URL'))
        try:
            raise SystemExit(run_browser(*sys.argv[2:], ephemeral=sys.argv[1] == '--parent-browser'))
        except OSError as exc:
            print(t('Webapp konnte nicht gestartet werden: ') + str(exc), file=sys.stderr, flush=True)
            raise SystemExit(1)
    app = QApplication(sys.argv)
    setup_qt(app)
    if len(sys.argv) > 1:
        pid = int(sys.argv[1])
        if pid <= 1:
            raise ValueError(t('Ungültiger Webapp-Prozess.'))
        overlay = CloseButtonOverlay(pid, int(sys.argv[2]) if len(sys.argv) > 2 else None)
        overlay.update_overlay()
        sys.exit(app.exec_())
