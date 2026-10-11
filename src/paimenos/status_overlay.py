#!/usr/bin/python3
"""Helligkeit/Lautstärke anzeigen, ohne Fokus oder Eingaben zu übernehmen."""
from paimenos.i18n import t, setup_qt
import fcntl
import os
import socket
import sys
from PyQt5.QtCore import Qt, QTimer, QSocketNotifier, QThread, QObject, pyqtSignal, pyqtSlot, QRectF
from PyQt5.QtGui import QPainter, QColor, QFont, QPen
from PyQt5.QtWidgets import QApplication, QWidget
from paimenos.osd_state import KINDS, read_level, socket_folder
from paimenos.screen_guard import child_access_blocked

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
        p.fillRect(self.rect(), QColor('#172335'))
        p.setPen(QPen(QColor('#41536d'), 2))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        p.setPen(QColor('#ffffff'))
        p.setFont(QFont('DejaVu Sans', 15, QFont.Bold))
        title = t('Helligkeit') if self.kind == 'brightness' else t('Lautstärke')
        p.drawText(QRectF(70, 15, 205, 30), Qt.AlignVCenter | Qt.AlignLeft, title)
        p.setPen(QColor('#ffb347'))
        p.drawText(QRectF(275, 15, 90, 30), Qt.AlignVCenter | Qt.AlignRight, str(self.level) + ' %')
        # Symbole als Vektoren, unabhängig von installierten Emoji-Schriften.
        p.setPen(QPen(QColor('#ffb347'), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self.kind == 'brightness':
            import math
            p.drawEllipse(QRectF(29, 23, 18, 18))
            for angle in range(0, 360, 45):
                a = math.radians(angle)
                p.drawLine(int(38 + 14 * math.cos(a)), int(32 + 14 * math.sin(a)),
                           int(38 + 20 * math.cos(a)), int(32 + 20 * math.sin(a)))
        else:
            p.drawLine(23, 25, 30, 25); p.drawLine(30, 25, 39, 18)
            p.drawLine(39, 18, 39, 46); p.drawLine(39, 46, 30, 39)
            p.drawLine(30, 39, 23, 39); p.drawLine(23, 39, 23, 25)
            if self.muted:
                p.drawLine(47, 25, 58, 39); p.drawLine(47, 39, 58, 25)
            else:
                p.drawArc(QRectF(37, 20, 19, 24), -65 * 16, 130 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#34445b'))
        p.drawRoundedRect(QRectF(24, 61, 342, 15), 7, 7)
        fraction = 0 if self.muted else self.level / 100
        if fraction > 0:
            p.setBrush(QColor('#ffb347'))
            p.drawRoundedRect(QRectF(24, 61, 342 * fraction, 15), 7, 7)
        p.setPen(QColor('#b4c0d1'))
        p.setFont(QFont('DejaVu Sans', 9))
        p.drawText(QRectF(24, 82, 342, 20), Qt.AlignLeft, t('Stumm') if self.muted else '0 %' )
        p.drawText(QRectF(24, 82, 342, 20), Qt.AlignRight, '100 %')

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
