#!/usr/bin/env python3
from paimenos.i18n import t, setup_qt
from paimenos.paths import CONFIG_DIR
import sys, os, json, subprocess
from datetime import date
from paimenos.state import read_settings, update_settings, run_op, guard_installed, remaining_seconds, in_usage_window
from paimenos.guard_client import GuardUnavailable
from paimenos.guard_store import GuardError
from paimenos.foreground import ForegroundGuard
from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QFont, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QInputDialog, QMessageBox, QLineEdit
)

SETTINGS_FILE = str(CONFIG_DIR / "settings.json")

def load_settings():
    return read_settings()

# Wie assets/icons/poweroff.svg, aber weiß für den roten Knopf.
POWER_SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<g fill="none" stroke="#FFFFFF" stroke-width="24" stroke-linecap="round">
<path d="M 74 59 A 88 88 0 1 0 182 59"/><path d="M 128 28 V 108"/></g></svg>'''

def power_icon():
    pixmap = QPixmap()
    pixmap.loadFromData(POWER_SVG, 'SVG')
    return QIcon(pixmap)

class LockScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(t('Zeit abgelaufen'))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        
        try:
            self.settings = load_settings()
        except (OSError, ValueError, KeyError, TypeError):
            # A broken settings file must not expose a running child app.
            self.settings = {}
        bg_color = self.settings.get("bg_color", "#FF9F00")

        self.setStyleSheet(f"""
            QWidget {{ background-color: {bg_color}; }}
            QLabel {{ color: white; font-family: 'DejaVu Sans'; }}
            QPushButton {{
                background: rgba(255, 255, 255, 0.25);
                color: white;
                border: 4px solid white;
                border-radius: 20px;
                padding: 15px 25px;
                font-size: 22px;
                font-weight: bold;
                font-family: 'DejaVu Sans';
            }}
            QPushButton:hover {{ background: rgba(255, 255, 255, 0.45); color: #333; }}
            QPushButton#shutdownBtn {{
                background: #FF3B30;
                border-color: white;
            }}
            QPushButton#shutdownBtn:hover {{ background: #E02B20; color: white; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.addStretch()

        icon_label = QLabel("⏳", self)
        icon_label.setFont(QFont("DejaVu Sans", 80))
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        try:
            outside = in_usage_window(self.settings) is False
        except ValueError:
            outside = False
        if outside:
            title = t('Jetzt ist keine Nutzungszeit!')
            hint = t('Du kannst den Computer von {value0} bis {value1} Uhr nutzen.',
                     value0=self.settings["allowed_from"], value1=self.settings["allowed_until"])
        else:
            title = t('Die Bildschirmzeit ist für heute abgelaufen!')
            hint = t('Deine Apps bleiben geöffnet. Rufe deine Eltern zum Fortsetzen oder Speichern.')
        title_label = QLabel(title, self)
        title_label.setFont(QFont("DejaVu Sans", 32, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        sub_label = QLabel(hint, self)
        sub_label.setWordWrap(True)
        sub_label.setFont(QFont("DejaVu Sans", 20))
        sub_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(sub_label)

        layout.addSpacing(40)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        add_time_btn = QPushButton(t('➕ Zeit hinzufügen (Eltern)'), self)
        add_time_btn.setCursor(Qt.PointingHandCursor)
        add_time_btn.clicked.connect(self.add_time_dialog)
        if outside:
            # Bonuszeit hebt den Zeitraum nicht auf; der Eltern-PIN schaltet für heute frei.
            add_time_btn.setText(t('🔓 Mit Eltern-PIN freischalten'))
            add_time_btn.clicked.disconnect()
            add_time_btn.clicked.connect(self.unlock_window_dialog)
        btn_layout.addWidget(add_time_btn)
        btn_layout.addSpacing(30)

        shutdown_btn = QPushButton(t('Herunterfahren'), self)
        shutdown_btn.setObjectName("shutdownBtn")
        # Die Standardschriften enthalten kein ⏻; das Projektsymbol weiß auf dem roten Knopf.
        shutdown_btn.setIcon(power_icon())
        shutdown_btn.setIconSize(QSize(36, 36))
        shutdown_btn.setCursor(Qt.PointingHandCursor)
        shutdown_btn.clicked.connect(self.shutdown)
        btn_layout.addWidget(shutdown_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addStretch()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.check_unlocked)
        self.refresh_timer.start(1000)
        self.foreground = ForegroundGuard(self)

    def closeEvent(self, event):
        event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def check_unlocked(self):
        try:
            current = read_settings()
            left = remaining_seconds(current)
        except (OSError, ValueError, KeyError, TypeError):
            return
        if left is None or left > 0:
            QApplication.quit()

    def add_time_dialog(self):
        self.refresh_timer.stop()
        try:
            self.request_bonus()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, t('Datenfehler'), str(exc))
        finally:
            self.refresh_timer.start(1000)

    def unlock_window_dialog(self):
        self.refresh_timer.stop()
        try:
            pin, ok_pin = QInputDialog.getText(
                self, t('🔒 Eltern-PIN'), t('Bitte Eltern-PIN eingeben:'), QLineEdit.Password)
            if ok_pin and self.pin_action('unlock_today', pin, lambda data: data.update(window_override_date=str(date.today()))):
                QMessageBox.information(self, t('Erfolg'), t('Das Gerät ist für heute freigeschaltet.'))
                QApplication.quit()
                return
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, t('Datenfehler'), str(exc))
        finally:
            self.refresh_timer.start(1000)

    def pin_action(self, op, pin, legacy_change, **args):
        """PIN-geprüfte Änderung. Mit Guard prüft dieser die PIN (mit Sperrzeit); ohne Guard wie bisher die Datei."""
        if not guard_installed():
            if pin != load_settings()["pin"]:
                QMessageBox.warning(self, t('Falsch'), t('Falscher PIN!'))
                return False
            self.settings = update_settings(legacy_change)
            return True
        try:
            self.settings = run_op(op, pin=pin, **args)
            return True
        except GuardError as exc:
            QMessageBox.warning(self, t('Falsch'), str(exc) if exc.code == 'locked' else t('Falscher PIN!'))
        except GuardUnavailable:
            QMessageBox.warning(self, t('Datenfehler'), t('Der PaimenOS-Guard ist nicht erreichbar. Bitte später erneut versuchen.'))
        return False

    def verify_pin(self, pin):
        if not guard_installed():
            if pin != load_settings()["pin"]:
                QMessageBox.warning(self, t('Falsch'), t('Falscher PIN!'))
                return False
            return True
        try:
            run_op('verify', pin=pin)
            return True
        except GuardError as exc:
            QMessageBox.warning(self, t('Falsch'), str(exc) if exc.code == 'locked' else t('Falscher PIN!'))
        except GuardUnavailable:
            QMessageBox.warning(self, t('Datenfehler'), t('Der PaimenOS-Guard ist nicht erreichbar. Bitte später erneut versuchen.'))
        return False

    def request_bonus(self):
        pin, ok_pin = QInputDialog.getText(
            self, t('🔒 Eltern-PIN'), t('Bitte Eltern-PIN eingeben:'), QLineEdit.Password
        )
        # Falsche PIN sofort melden, nicht erst nach der Minutenauswahl.
        if not ok_pin or not self.verify_pin(pin):
            return
        minutes, ok_time = QInputDialog.getInt(
            self, t('⏱ Zeit hinzufügen'), t('Wie viele Minuten möchtest du hinzufügen?'), 15, 5, 180, 5
        )
        if not ok_time or minutes <= 0:
            return
        def add_bonus(data):
            required = data["today_used_seconds"] + minutes * 60
            data["bonus_minutes"] = max(0, (required + 59) // 60 - data["daily_limit_minutes"])
        if self.pin_action('bonus_until', pin, add_bonus, minutes=minutes):
            QMessageBox.information(self, t('Erfolg'), t('Es wurden {value0} Minuten für heute hinzugefügt!', value0=minutes))
            QApplication.quit()

    def shutdown(self):
        answer = QMessageBox.question(
            self, t('Herunterfahren?'),
            t('Ungespeicherte Arbeiten können beim Ausschalten verloren gehen. '
              'Zum Speichern zuerst Elternzeit hinzufügen. Trotzdem ausschalten?'),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            subprocess.run(["systemctl", "poweroff"])

if __name__ == "__main__":
    app = QApplication(sys.argv)
    setup_qt(app)
    window = LockScreen()
    window.showFullScreen()
    sys.exit(app.exec_())
