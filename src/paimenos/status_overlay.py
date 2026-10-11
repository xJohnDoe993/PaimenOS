#!/usr/bin/python3
"""Helligkeit/Lautstärke anzeigen, ohne Fokus oder Eingaben zu übernehmen."""
from paimenos.i18n import t, setup_qt
import fcntl
import os
import socket
import sys
from PyQt5.QtCore import Qt, QTimer, QSocketNotifier, QThread, QObject, pyqtSignal, pyqtSlot, QRectF
from PyQt5.QtGui import QPainter, QColor, QFont, QPen, QPainterPath, QLinearGradient, QRegion
from PyQt5.QtWidgets import QApplication, QWidget
from paimenos.osd_state import KINDS, read_level, socket_folder
from paimenos.screen_guard import child_access_blocked
from paimenos.state import read_settings
from paimenos import theme

class LevelReader(QObject):
    finished = pyqtSignal(str, object)

    @pyqtSlot(str)
    def read(self, kind):
        try:
            result = read_level(kind)
        except Exception:
            result = None
        self.finished.emit(kind, result)

class StatusOverlay(QWidget):
    requested = pyqtSignal(str)

    def __init__(self, receiver):
        super().__init__()
        self.receiver = receiver
        self.kind = 'volume'
        self.level = 0
        self.muted = False
        self.pending = None
        self.busy = False
        self.accent = QColor(theme.DEFAULT_COLOR)
        self.setWindowTitle(t('PaimenOS Lautstärke / Helligkeit'))
        # Without the bypass hint Openbox keeps the focused fullscreen menu above this window.
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.X11BypassWindowManagerHint | Qt.WindowDoesNotAcceptFocus |
                            Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        # Deckende Fläche bleibt auch ohne X11-Compositor gut lesbar.
        self.setFixedSize(390, 110)
        self.apply_shape()
        self.dismiss = QTimer(self)
        self.dismiss.setSingleShot(True)
        self.dismiss.timeout.connect(self.hide)
        self.coalesce = QTimer(self)
        self.coalesce.setSingleShot(True)
        self.coalesce.timeout.connect(self.dispatch)
        self.notifier = QSocketNotifier(receiver.fileno(), QSocketNotifier.Read, self)
        self.notifier.activated.connect(self.receive)
        self.thread = QThread(self)
        self.reader = LevelReader()
        self.reader.moveToThread(self.thread)
        self.requested.connect(self.reader.read)
        self.reader.finished.connect(self.updated)
        self.thread.finished.connect(self.reader.deleteLater)
        self.thread.start()
        self.guard_timer = QTimer(self)
        self.guard_timer.timeout.connect(self.check_guard)
        self.guard_timer.start(250)

    RADIUS = 26

    def apply_shape(self):
        # A mask keeps the rounded corners clean without an X11 compositor.
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.RADIUS, self.RADIUS)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def load_accent(self):
        try:
            color = read_settings().get('bg_color')
        except (OSError, ValueError):
            color = None
        self.accent = QColor(theme.normalize(color))

    def check_guard(self):
        if self.isVisible() and child_access_blocked():
            self.hide()

    def receive(self, *args):
        for _ in range(256):
            try:
                data = self.receiver.recv(32)
            except BlockingIOError:
                break
            except OSError:
                return
            kind = data.decode('ascii', errors='ignore')
            if kind in KINDS:
                self.pending = kind
        if self.pending:
            if self.isVisible():
                self.dismiss.start(2000)
            self.coalesce.start(40)

    def dispatch(self):
        if self.busy or not self.pending:
            return
        kind, self.pending = self.pending, None
        self.busy = True
        self.requested.emit(kind)

    def updated(self, kind, result):
        self.busy = False
        # Bei raschem Wechsel von Lautstärke zu Helligkeit die neuere Anzeige bevorzugen.
        if (result is not None and not child_access_blocked()
                and (self.pending is None or self.pending == kind)):
            self.kind = kind
            self.level, self.muted = result
            self.setAccessibleName((t('Lautstärke') if kind == 'volume' else t('Helligkeit')) +
                                   (t(' stumm, ') if self.muted else ' ') + str(self.level) + t(' Prozent'))
            self.load_accent()
            screen = QApplication.primaryScreen().geometry()
            self.move(screen.x() + (screen.width() - self.width()) // 2,
                      screen.y() + screen.height() - self.height() - 60)
            self.show()
            self.raise_()
            self.update()
            self.dismiss.start(2000)
        if self.pending:
            self.coalesce.start(40)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        accent = self.accent
        text = QColor(theme.TEXT_COLOR)
        # White card like the menu tiles, framed in the chosen colour.
        card = QLinearGradient(0, 0, 0, self.height())
        card.setColorAt(0, QColor('#ffffff'))
        card.setColorAt(1, QColor('#f1f4fa'))
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(self.rect()), self.RADIUS, self.RADIUS)
        p.setBrush(card)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(4, 4, -4, -4), self.RADIUS - 4, self.RADIUS - 4)
        p.setPen(text)
        p.setFont(QFont('DejaVu Sans', 15, QFont.Bold))
        title = t('Helligkeit') if self.kind == 'brightness' else t('Lautstärke')
        p.drawText(QRectF(80, 17, 195, 30), Qt.AlignVCenter | Qt.AlignLeft, title)
        p.setPen(accent.darker(120))
        p.drawText(QRectF(270, 17, 95, 30), Qt.AlignVCenter | Qt.AlignRight, str(self.level) + ' %')
        # Symbole als Vektoren, unabhängig von installierten Emoji-Schriften.
        icon_pen = QPen(accent.darker(115), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(icon_pen)
        if self.kind == 'brightness':
            import math
            p.drawEllipse(QRectF(33, 26, 18, 18))
            for angle in range(0, 360, 45):
                a = math.radians(angle)
                p.drawLine(int(42 + 14 * math.cos(a)), int(35 + 14 * math.sin(a)),
                           int(42 + 20 * math.cos(a)), int(35 + 20 * math.sin(a)))
        else:
            p.drawLine(27, 28, 34, 28); p.drawLine(34, 28, 43, 21)
            p.drawLine(43, 21, 43, 49); p.drawLine(43, 49, 34, 42)
            p.drawLine(34, 42, 27, 42); p.drawLine(27, 42, 27, 28)
            if self.muted:
                p.drawLine(51, 28, 62, 42); p.drawLine(51, 42, 62, 28)
            else:
                p.drawArc(QRectF(41, 23, 19, 24), -65 * 16, 130 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#dde3ee'))
        p.drawRoundedRect(QRectF(28, 62, 334, 16), 8, 8)
        fraction = 0 if self.muted else self.level / 100
        if fraction > 0:
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(28, 62, max(16.0, 334 * fraction), 16), 8, 8)
        p.setPen(QColor('#6b7a90'))
        p.setFont(QFont('DejaVu Sans', 9))
        p.drawText(QRectF(28, 82, 334, 18), Qt.AlignLeft, t('Stumm') if self.muted else '0 %')
        p.drawText(QRectF(28, 82, 334, 18), Qt.AlignRight, '100 %')

    def shutdown(self):
        self.guard_timer.stop()
        self.notifier.setEnabled(False)
        self.thread.quit()
        self.thread.wait(4000)

def main():
    app = QApplication(sys.argv)
    setup_qt(app)
    app.setQuitOnLastWindowClosed(False)
    folder = socket_folder()
    lock = open(folder / 'daemon.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    path = folder / 'events.sock'
    try:
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            receiver.bind(str(path))
            os.chmod(path, 0o600)
            receiver.setblocking(False)
            overlay = StatusOverlay(receiver)
            app.aboutToQuit.connect(overlay.shutdown)
            return app.exec_()
    finally:
        path.unlink(missing_ok=True)
        lock.close()

if __name__ == '__main__':
    sys.exit(main())
