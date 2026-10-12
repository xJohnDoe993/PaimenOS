"""Lokaler Elternbereich mit denselben Verwaltungsfunktionen wie im Web."""
from paimenos.i18n import t
import os
import shlex
import shutil
import subprocess
from paimenos.foreground import ForegroundGuard
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox, QTabWidget, QScrollArea, QCheckBox,
    QComboBox, QMessageBox, QColorDialog, QPlainTextEdit, QFileDialog, QProgressBar)
import paimenos.parent as parents
import paimenos.emulators as emulators
from paimenos.pronunciation import default_pronunciation
from paimenos.state import read_settings
from paimenos.paths import ASSETS_DIR
from paimenos.images import TaskSignals, submit_task
from paimenos.diagnostics import collect_diagnostics, diagnostics_text

STYLE = '''
QDialog,QWidget {background:#f7f3e8;color:#102c40;font-size:14px;}
QLabel {background:transparent;} QLabel#heading {font-size:25px;font-weight:bold;}
QLabel#muted {color:#526459;} QLabel#metric {font-size:23px;font-weight:bold;color:#254c42;}
QWidget#card {background:#fffdf6;border:1px solid #c7d3c0;border-radius:12px;}
QPushButton {background:#254c42;color:#fffdf6;border:1px solid transparent;border-radius:8px;padding:10px 14px;font-weight:bold;min-height:22px;}
QPushButton:hover {background:#356354;} QPushButton:focus {border:2px solid #102c40;}
QPushButton[secondary="true"] {background:#e6eddf;color:#102c40;border-color:#bacdb5;}
QPushButton[danger="true"] {background:#f9e7e4;color:#923c42;border-color:#d8a5a0;}
QPushButton:disabled {background:#e3e6dc;color:#68756b;}
QComboBox QAbstractItemView {background:#fffdf6;color:#102c40;selection-background-color:#d7e3cd;}
QLineEdit,QSpinBox,QComboBox,QPlainTextEdit {background:#fffdf6;color:#102c40;border:1px solid #9bab95;border-radius:7px;padding:8px;selection-background-color:#d7e3cd;}
QLineEdit:focus,QSpinBox:focus,QComboBox:focus {border:2px solid #254c42;}
QCheckBox {spacing:10px;background:transparent;} QCheckBox::indicator {width:22px;height:22px;border:1px solid #9bab95;border-radius:5px;background:#f7f3e8;}
QCheckBox::indicator:checked {background:#254c42;border:2px solid #7b9971;}
QTabWidget::pane {border:0;} QTabBar::tab {background:#fffdf6;color:#526459;padding:12px 16px;margin:0 4px 12px 0;border-radius:8px;}
QTabBar::tab:selected {background:#d7e3cd;color:#102c40;} QScrollArea {border:0;}
QScrollBar:vertical {background:#f7f3e8;width:10px;margin:0;} QScrollBar::handle:vertical {background:#9bab95;min-height:24px;border-radius:5px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;} QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:none;}
QProgressBar {border:0;background:#d6e0cd;border-radius:5px;height:10px;}
QProgressBar::chunk {background:#254c42;border-radius:5px;}
'''


def label(text, muted=False):
    widget = QLabel(text)
    widget.setWordWrap(True)
    if muted:
        widget.setObjectName('muted')
    return widget


def button(text, callback, secondary=False, danger=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.PointingHandCursor)
    widget.setProperty('secondary', secondary)
    widget.setProperty('danger', danger)
    widget.clicked.connect(callback)
    return widget


def scroll_page():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(16)
    scroll.setWidget(content)
    return scroll, layout


def card(layout, title):
    widget = QWidget()
    widget.setObjectName('card')
    inner = QVBoxLayout(widget)
    inner.setContentsMargins(18, 16, 18, 16)
    heading = label(title)
    heading.setStyleSheet('font-size:18px;font-weight:bold;')
    inner.addWidget(heading)
    layout.addWidget(widget)
    return inner


class AppEditor(QDialog):
    def __init__(self, item=None, parent=None):
        super().__init__(parent)
        self.item = item or {}
        self.busy = False
        self.icon_path = ''
        self.setWindowTitle(t('App bearbeiten') if item else t('App hinzufügen'))
        self.resize(690, 590)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        heading = label(self.windowTitle()); heading.setObjectName('heading')
        layout.addWidget(heading)
        form = QFormLayout()
        target, arguments = parents.app_launch_fields(self.item)
        self.title_input = QLineEdit(self.item.get('title', ''))
        self.title_input.setMaxLength(80)
        self.pronunciation_input = QLineEdit(self.item.get('pronunciation', ''))
        self.pronunciation_input.setMaxLength(80)
        self.pronunciation_input.setPlaceholderText(default_pronunciation(self.item.get('title', '')) or t('z. B. Dschieni statt Geany'))
        self.target_input = QLineEdit(target)
        self.target_input.setPlaceholderText(t('https://… oder /usr/bin/vlc'))
        self.arguments_input = QLineEdit(arguments)
        self.arguments_input.setPlaceholderText(t('Zum Beispiel: --fullscreen "/home/kids/Mein Video.mp4"'))
        self.category_input = QComboBox()
        for text, value in [(t('Automatisch'), 'auto'), ('Webapps', 'webapps'), (t('Spiele'), 'games'), (t('Produktiv'), 'productive')]:
            self.category_input.addItem(text, value)
        self.category_input.setCurrentIndex(max(0, self.category_input.findData(self.item.get('category', 'auto'))))
        form.addRow(t('Kategorie'), self.category_input)
        form.addRow(t('Name'), self.title_input)
        form.addRow(t('Aussprache'), self.pronunciation_input)
        form.addRow(t('Webadresse / Programm'), self.target_input)
        form.addRow(t('Programmparameter'), self.arguments_input)
        self.icon_input = QLineEdit(self.item.get('icon_url', ''))
        self.icon_input.setPlaceholderText('Optional: https://…/logo.png')
        form.addRow(t('Bildadresse'), self.icon_input)
        layout.addLayout(form)
        file_row = QHBoxLayout()
        self.file_label = label(t('Keine neue Bilddatei gewählt'), True)
        self.choose_button = button(t('Bilddatei wählen …'), self.choose_icon, secondary=True)
        self.clear_button = button(t('Auswahl entfernen'), self.clear_icon, secondary=True)
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(self.choose_button); file_row.addWidget(self.clear_button)
        layout.addLayout(file_row)
        self.reset_input = QCheckBox(t('Eigenes Bild entfernen / Standardsymbol verwenden'))
        self.reset_input.toggled.connect(self.reset_changed)
        layout.addWidget(self.reset_input)
        layout.addWidget(label(t('HTTP/HTTPS öffnet eine Webapp. Ein Programmpfad oder Programmname startet eine bereits installierte Anwendung als kids. Parameter mit Leerzeichen in Anführungszeichen setzen. Ohne neue Bildauswahl bleibt das vorhandene Bild erhalten. PNG, JPEG, GIF oder WebP bis 4 MB.'), True))
        if self.item.get('type') == 'camera' or self.item.get('emulator'):
            self.target_input.setEnabled(False)
            self.arguments_input.setEnabled(False)
            layout.addWidget(label(t('Bei Kamera und Emulator-Spielen lassen sich Name und Bild ändern; der Start bleibt festgelegt.'), True))
        self.message = label(''); layout.addWidget(self.message)
        row = QHBoxLayout()
        self.save_button = button(t('App speichern'), self.save)
        self.cancel_button = button(t('Abbrechen'), self.reject, secondary=True)
        row.addWidget(self.save_button); row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.signals = TaskSignals(self)
        self.signals.completed.connect(self.completed)

    def choose_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, t('Kachelbild auswählen'), os.path.expanduser('~'),
                                             t('Bilder (*.png *.jpg *.jpeg *.gif *.webp)'))
        if path:
            try:
                if os.path.getsize(path) > parents.MAX_ICON_BYTES:
                    raise ValueError(t('Das Bild ist größer als 4 MB.'))
            except (OSError, ValueError) as exc:
                self.message.setText(str(exc)); return
            self.icon_path = path
            self.icon_input.clear()
            self.file_label.setText(os.path.basename(path))

    def clear_icon(self):
        self.icon_path = ''
        self.file_label.setText(t('Keine neue Bilddatei gewählt'))

    def reset_changed(self, reset):
        if reset:
            self.clear_icon()
            self.icon_input.clear()
        for widget in (self.icon_input, self.choose_button, self.clear_button):
            widget.setEnabled(not reset)

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.save_button, self.cancel_button, self.title_input, self.pronunciation_input, self.target_input,
                       self.arguments_input, self.icon_input, self.choose_button, self.clear_button, self.reset_input, self.category_input):
            widget.setEnabled(not busy)
        if not busy:
            self.reset_changed(self.reset_input.isChecked())
            if self.item.get('type') == 'camera' or self.item.get('emulator'):
                self.target_input.setEnabled(False)
                self.arguments_input.setEnabled(False)

    def save(self):
        values = dict(title=self.title_input.text(), target=self.target_input.text(),
                      arguments=self.arguments_input.text(), icon_url=self.icon_input.text(),
                      app_id=self.item.get('id'), reset_icon=self.reset_input.isChecked(), category=self.category_input.currentData(),
                      pronunciation=self.pronunciation_input.text())
        path = self.icon_path
        def operation():
            if path:
                with open(path, 'rb') as handle:
                    values['icon_data'] = handle.read(parents.MAX_ICON_BYTES + 1)
            return parents.save_app(**values)
        self.set_busy(True)
        self.message.setText(t('App wird gespeichert …'))
        submit_task(operation, 'app', self.signals)

    def completed(self, token, value, error):
        self.set_busy(False)
        if not error:
            self.accept()
            return
        self.message.setText(error)

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)


class ParentDiagnostics(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t('Geräte-Diagnose'))
        self.resize(760, 540)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)
        row = QHBoxLayout()
        self.refresh_button = button(t('Aktualisieren'), self.refresh, secondary=True)
        self.export_button = button(t('Bericht speichern'), self.export)
        row.addWidget(self.refresh_button); row.addWidget(self.export_button)
        row.addWidget(button(t('Zurück'), self.reject, secondary=True))
        layout.addLayout(row)
        self.signals = TaskSignals(self)
        self.signals.completed.connect(self.completed)
        self.refresh()

    def refresh(self):
        self.refresh_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.text.setPlainText(t('Diagnose wird gesammelt …'))
        submit_task(collect_diagnostics, 'diagnostics', self.signals)

    def completed(self, token, value, error):
        self.refresh_button.setEnabled(True)
        self.export_button.setEnabled(not bool(error))
        self.text.setPlainText(error if error else diagnostics_text(value))

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, t('Bericht speichern'), os.path.expanduser('~/PaimenOS-Diagnose.txt'), t('Textdateien (*.txt)'))
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as handle:
                    handle.write(self.text.toPlainText())
            except OSError as exc:
                QMessageBox.warning(self, t('Speichern'), str(exc))


class ParentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t('PaimenOS · Elternbereich'))
        self.setWindowIcon(QIcon(str(ASSETS_DIR / 'branding/paimenos-logo.png')))
        self.setStyleSheet(STYLE)
        self.resize(860, 650)
        if self.screen():
            available = self.screen().availableGeometry()
            self.resize(min(860, available.width()-32), min(650, available.height()-32))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)
        brand_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(ASSETS_DIR / 'branding/paimenos-logo.png')).scaled(
            80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.setAccessibleName('PaimenOS Logo')
        brand_row.addWidget(logo)
        heading = label(t('Elternbereich')); heading.setObjectName('heading')
        brand_row.addWidget(heading, 1)
        layout.addLayout(brand_row)
        layout.addWidget(label(t('Apps freigeben, Zeit verwalten und das Gerät einrichten.'), True))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.status = label(t('Änderungen werden direkt auf diesem Gerät gespeichert.'), True)
        row = QHBoxLayout(); row.addWidget(self.status, 1)
        row.addWidget(button(t('Schließen'), self.accept, secondary=True))
        layout.addLayout(row)
        self.st = read_settings()
        self.limit_dirty = False
        self.color_dirty = False
        self.app_signature = None
        self.app_rows = {}
        # Same order as the web navigation: child, connections, system (no emulators here).
        self.build_overview()
        self.build_time()
        self.build_apps()
        self.build_connections()
        self.build_system()
        self.build_settings()
        self.build_diagnostics()
        self.refresh()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(3000)
        self.foreground = ForegroundGuard(self)

    def exec_(self):
        self.showFullScreen()
        return super().exec_()

    def perform(self, operation, message):
        try:
            operation()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            QMessageBox.warning(self, t('Änderung nicht gespeichert'), str(exc))
            self.refresh()
            return False
        self.status.setText(message)
        self.refresh()
        return True

    def bonus_buttons(self, layout):
        row = QHBoxLayout()
        for minutes in (5, 15, 30):
            row.addWidget(button(t('+{value0} Min.', value0=minutes), lambda checked=False, m=minutes: self.give_bonus(m), secondary=True))
        layout.addLayout(row)

    def give_bonus(self, minutes):
        try:
            added = parents.add_bonus(minutes)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, t('Bonuszeit'), str(exc)); return
        self.status.setText(t('{value0} Minuten Bonus hinzugefügt.', value0=added) if added else t('Die maximale Bonuszeit ist erreicht.'))
        self.refresh()

    def build_overview(self):
        page, layout = scroll_page()
        self.tabs.addTab(page, t('Übersicht'))
        time_card = card(layout, t('Bildschirmzeit heute'))
        self.summary = label(''); self.summary.setObjectName('metric')
        self.summary_detail = label('', True)
        time_card.addWidget(self.summary); time_card.addWidget(self.summary_detail)
        self.progress = QProgressBar(); self.progress.setTextVisible(False)
        time_card.addWidget(self.progress)
        self.bonus_buttons(time_card)
        time_card.addWidget(label(t('Bonuszeit wirkt nur bei einem gesetzten Tageslimit.'), True))
        apps_card = card(layout, t('Apps für den Kinderbereich'))
        self.apps_summary = label(''); apps_card.addWidget(self.apps_summary)
        apps_card.addWidget(button(t('Apps verwalten'), lambda: self.tabs.setCurrentWidget(self.apps_page), secondary=True))
        tools = card(layout, t('Gerät & Hilfe'))
        row = QHBoxLayout()
        row.addWidget(button(t('WLAN / Netzwerke'), self.open_wifi, secondary=True))
        row.addWidget(button(t('Geräte-Diagnose'), self.open_diagnostics, secondary=True))
        tools.addLayout(row)
        self.web_address = label(t('Web-Elternbereich: Adresse wird ermittelt …'), True)
        self.web_address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tools.addWidget(self.web_address)
        self.address_signals = TaskSignals(self)
        self.address_signals.completed.connect(self.address_completed)
        submit_task(self.find_addresses, 'address', self.address_signals)
        layout.addStretch()

    @staticmethod
    def find_addresses():
        try:
            import ipaddress
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=3)
            addresses = []
            for raw in result.stdout.split():
                try:
                    address = ipaddress.ip_address(raw)
                except ValueError:
                    continue
                if address.version == 4 and not address.is_loopback and not address.is_link_local:
                    addresses.append('http://' + str(address))
            return addresses
        except (OSError, subprocess.TimeoutExpired):
            return []

    def address_completed(self, token, value, error):
        self.web_address.setText(t('Web-Elternbereich im selben Netzwerk: ') + ' · '.join(value) if value and not error else t('Web-Elternbereich: http://localhost (auf diesem Gerät)'))

    def build_apps(self):
        page = QWidget(); layout = QVBoxLayout(page)
        self.tabs.addTab(page, 'Apps')
        self.apps_page = page
        row = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText(t('Name oder Startbefehl suchen …'))
        self.search.setAccessibleName(t('Apps durchsuchen')); self.search.textChanged.connect(self.filter_apps)
        self.type_filter = QComboBox()
        for title, value in [(t('Alle Typen'),'all'),(t('Programme & Spiele'),'native'),('Webapps','webapp'),(t('Kamera, Bilder & Videos'),'camera')]:
            self.type_filter.addItem(title, value)
        self.type_filter.currentIndexChanged.connect(self.filter_apps)
        row.addWidget(self.search, 1); row.addWidget(self.type_filter)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button('+ App', self.new_app))
        row.addWidget(button(t('Alle freigeben'), lambda: self.bulk(True), secondary=True))
        row.addWidget(button(t('Alle sperren'), lambda: self.bulk(False), danger=True))
        layout.addLayout(row)
        self.app_count = label('', True); layout.addWidget(self.app_count)
        self.apps_scroll = QScrollArea(); self.apps_scroll.setWidgetResizable(True)
        layout.addWidget(self.apps_scroll, 1)
        layout.addWidget(label(t('Freigegebene Webapps erscheinen bei Internetverbindung; die Kamera bei angeschlossenem Medium.'), True))

    def render_apps(self, items, st):
        scroll_position = self.apps_scroll.verticalScrollBar().value()
        content = QWidget(); layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(10)
        self.app_rows = {}
        for item in items:
            widget = QWidget(); widget.setObjectName('card')
            inner = QVBoxLayout(widget)
            row = QHBoxLayout()
            checkbox = QCheckBox(item.get('title', item['id']))
            checkbox.setChecked(parents.app_active(item, st))
            checkbox.setToolTip(t('Freigabe im Kinderbereich'))
            checkbox.toggled.connect(lambda active, i=item['id']: self.set_active(i, active))
            row.addWidget(checkbox, 1)
            if parents.editable_app(item):
                row.addWidget(button(t('Bearbeiten'), lambda checked=False, i=dict(item): self.edit_app(i), secondary=True))
            if parents.deletable_app(item):
                row.addWidget(button(t('Löschen'), lambda checked=False, i=dict(item): self.delete_app(i), danger=True))
            inner.addLayout(row)
            inner.addWidget(label(item.get('url', '') if item.get('type') == 'webapp' else t('Kamera, Bilder & Videos') if item.get('type') == 'camera' else item.get('command', t('Programm / Spiel')), True))
            if parents.app_source_label(item):
                inner.addWidget(label(parents.app_source_label(item), True))
            layout.addWidget(widget)
            self.app_rows[item['id']] = (widget, item)
        layout.addStretch()
        old = self.apps_scroll.takeWidget()
        if old:
            old.deleteLater()
        self.apps_scroll.setWidget(content)
        self.filter_apps()
        QTimer.singleShot(0, lambda: self.apps_scroll.verticalScrollBar().setValue(scroll_position))

    def filter_apps(self, *args):
        query, kind, count = self.search.text().casefold(), self.type_filter.currentData(), 0
        for widget, item in self.app_rows.values():
            visible = query in (item.get('title','') + ' ' + item.get('url','') + ' ' + item.get('command','')).casefold() and (kind=='all' or item.get('type','native')==kind)
            widget.setVisible(visible)
            count += visible
        self.app_count.setText(f'{count} Apps' if count else t('Keine passenden Apps gefunden.'))

    def set_active(self, app_id, active):
        self.perform(lambda: parents.set_apps_active([app_id], active), t('App-Freigabe gespeichert.'))

    def bulk(self, active):
        if not active and QMessageBox.question(self, t('Alle Apps sperren?'), t('Alle Apps für den Kinderbereich sperren?'), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.perform(lambda: parents.set_apps_active([a['id'] for a in parents.managed_apps()], active), t('App-Freigaben gespeichert.'))

    def new_app(self):
        self.edit_app(None)

    def edit_app(self, item):
        editor = AppEditor(item, self)
        if editor.exec_() == QDialog.Accepted:
            self.status.setText(t('App gespeichert.'))
            self.refresh()
        editor.deleteLater()

    def delete_app(self, item):
        if QMessageBox.question(self, t('App löschen?'), t('„{value0}“ aus dem Kinder-Menü entfernen? Bei Emulator-Spielen werden auch die hochgeladenen ROM-Dateien entfernt; Spielstände bleiben erhalten. Installierte Programme und Browserprofile bleiben erhalten.', value0=item['title']), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.perform(lambda: parents.delete_app(item['id']), t('App aus dem Menü entfernt.'))

    def build_time(self):
        page, layout = scroll_page(); self.tabs.addTab(page, t('Bildschirmzeit'))
        settings = card(layout, t('Tageslimit'))
        settings.addWidget(label(t('Das Limit gilt jeden Tag. Verbrauch und Bonus werden um Mitternacht zurückgesetzt.'), True))
        self.limit_spin = QSpinBox(); self.limit_spin.setRange(0, 600)
        self.limit_spin.setSuffix(t(' Min.')); self.limit_spin.setSpecialValueText(t('Unbegrenzt'))
        self.limit_spin.setValue(min(600, self.st['daily_limit_minutes']))
        self.limit_spin.valueChanged.connect(lambda: setattr(self, 'limit_dirty', True))
        form = QFormLayout(); form.addRow(t('Minuten pro Tag'), self.limit_spin); settings.addLayout(form)
        presets = QHBoxLayout()
        for minutes in (30, 60, 90, 120):
            presets.addWidget(button(str(minutes) + t(' Min.'), lambda checked=False, m=minutes: self.limit_spin.setValue(m), secondary=True))
        settings.addLayout(presets)
        settings.addWidget(button(t('Tageslimit speichern'), self.save_time))
        bonus_card = card(layout, t('Bonuszeit heute'))
        self.bonus_label = label(''); bonus_card.addWidget(self.bonus_label)
        self.bonus_buttons(bonus_card)
        row = QHBoxLayout()
        self.bonus_spin = QSpinBox(); self.bonus_spin.setRange(1, 600); self.bonus_spin.setValue(10); self.bonus_spin.setSuffix(t(' Min.'))
        row.addWidget(self.bonus_spin)
        row.addWidget(button(t('Bonus hinzufügen'), lambda: self.give_bonus(self.bonus_spin.value()), secondary=True))
        row.addWidget(button(t('Bonus entfernen'), self.remove_bonus, danger=True))
        bonus_card.addLayout(row)
        reset = card(layout, t('Heutigen Verbrauch zurücksetzen'))
        reset.addWidget(label(t('Setzt Verbrauch und Bonus auf 0. Das Tageslimit bleibt erhalten.'), True))
        reset.addWidget(button(t('Heute zurücksetzen'), self.reset_usage, danger=True))
        layout.addStretch()

    def save_time(self):
        if self.perform(lambda: parents.set_time(self.limit_spin.value()), t('Tageslimit gespeichert.')):
            self.limit_dirty = False

    def remove_bonus(self):
        if QMessageBox.question(self, t('Bonus entfernen?'), t('Die Bonuszeit entfernen? Das Gerät kann dadurch gesperrt werden.'), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.perform(parents.clear_bonus, t('Bonuszeit entfernt.'))

    def reset_usage(self):
        if QMessageBox.question(self, t('Heute zurücksetzen?'), t('Heutigen Verbrauch und Bonus wirklich zurücksetzen?'), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.perform(parents.reset_today, t('Heutiger Verbrauch und Bonus zurückgesetzt.'))

    def build_settings(self):
        page, layout = scroll_page(); self.tabs.addTab(page, t('Einstellungen'))
        pin_card = card(layout, t('Eltern-PIN ändern'))
        pin_card.addWidget(label(t('Die PIN gilt am Gerät und im Web. Beide Felder leer lassen, um sie beizubehalten.'), True))
        self.pin_input, self.pin_confirm = QLineEdit(), QLineEdit()
        for field in (self.pin_input, self.pin_confirm):
            field.setEchoMode(QLineEdit.Password); field.setMaxLength(12)
            field.setPlaceholderText(t('4 bis 12 Ziffern'))
        form = QFormLayout(); form.addRow(t('Neue PIN'), self.pin_input); form.addRow(t('PIN wiederholen'), self.pin_confirm)
        pin_card.addLayout(form)
        color_card = card(layout, t('Farbe im Kinder-Menü'))
        self.color = self.st['bg_color']
        self.color_button = button(t('Farbe auswählen'), self.choose_color, secondary=True)
        color_card.addWidget(self.color_button)
        self.update_color_button()
        self.category_tabs_input = QCheckBox(t('Kategorien im Kinder-Menü anzeigen (Symbole)'))
        self.category_tabs_input.setChecked(bool(self.st.get('category_tabs', False)))
        color_card.addWidget(self.category_tabs_input)
        color_card.addWidget(label(t('Alles, Webapps, Spiele und Produktiv. Leere Kategorien verschwinden; Schultertasten wechseln die Ansicht.'), True))
        self.speak_titles_input = QCheckBox(t('Namen der Apps beim Anwählen vorlesen'))
        self.speak_titles_input.setChecked(bool(self.st.get('speak_titles', False)))
        color_card.addWidget(self.speak_titles_input)
        self.speak_voice_input = QComboBox()
        self.speak_voice_input.addItem(t('Frauenstimme'), 'female')
        self.speak_voice_input.addItem(t('Männerstimme'), 'male')
        self.speak_voice_input.setCurrentIndex(1 if self.st.get('speak_voice') == 'male' else 0)
        color_card.addWidget(self.speak_voice_input)
        color_card.addWidget(label(t('Im Kinder-Menü wird der Name der gewählten Kachel vorgelesen. Neue Namen werden einmalig im Hintergrund als Sprachaufnahme erzeugt.'), True))
        layout.addWidget(button(t('Einstellungen speichern'), self.save_settings))
        layout.addStretch()

    def build_connections(self):
        page, layout = scroll_page(); self.tabs.addTab(page, t('Verbindungen'))
        wifi_card = card(layout, t('WLAN & Internet'))
        wifi_card.addWidget(label(t('Netzwerke suchen, verbinden und gespeicherte WLANs verwalten.'), True))
        wifi_card.addWidget(button(t('WLAN verwalten'), self.open_wifi, secondary=True))
        bluetooth_card = card(layout, t('Bluetooth-Geräte'))
        bluetooth_card.addWidget(label(t('Controller, Kopfhörer und Tastaturen suchen, koppeln und verbinden.'), True))
        bluetooth_card.addWidget(label(t('Nach dem Verbinden: Steuerkreuz oder linker Stick zum Wählen, Bestätigungstaste zum Öffnen. Start öffnet Farbe / Eltern, die rechte Taste geht zurück. Der Elternbereich bleibt PIN-geschützt.'), True))
        bluetooth_card.addWidget(button(t('Bluetooth verwalten'), self.open_bluetooth, secondary=True))
        controller_card = card(layout, 'Controller')
        # Calibration reads the controller the menu itself is driven by; it stays in the web area.
        controller_card.addWidget(label(t('Controller testen und kalibrieren: im Web-Elternbereich unter „Controller“.'), True))
        layout.addStretch()

    def build_system(self):
        from paimenos.parent_ui_system import BackupsTab, UpdatesTab
        self.backups_tab, self.updates_tab = BackupsTab(self), UpdatesTab(self)
        self.tabs.addTab(self.backups_tab, t('USB-Backups'))
        self.tabs.addTab(self.updates_tab, 'Updates')

    def build_diagnostics(self):
        page, layout = scroll_page(); self.tabs.addTab(page, t('Diagnose'))
        diag = card(layout, t('Geräte-Diagnose'))
        diag.addWidget(label(t('Systeminformationen ansehen und für die Fehlersuche exportieren.'), True))
        diag.addWidget(button(t('Geräte-Diagnose'), self.open_diagnostics, secondary=True))
        layout.addStretch()

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.color), self, t('Hintergrundfarbe'))
        if color.isValid():
            self.color = color.name().upper(); self.color_dirty = True
            self.update_color_button()

    def update_color_button(self):
        self.color_button.setText(t('Farbe auswählen · ') + self.color)
        self.color_button.setStyleSheet(f'border:3px solid {self.color};')

    def save_settings(self):
        if self.perform(lambda: parents.save_preferences(self.pin_input.text().strip(), self.pin_confirm.text().strip(), self.color, self.category_tabs_input.isChecked(), self.speak_titles_input.isChecked(), self.speak_voice_input.currentData()), t('Einstellungen gespeichert.')):
            self.pin_input.clear(); self.pin_confirm.clear(); self.color_dirty = False

    def refresh(self):
        try:
            st, items = read_settings(), parents.managed_apps()
            info = parents.usage(st)
            self.summary.setText(t('Heute genutzt: ') + parents.duration(info['used']))
            self.summary_detail.setText(t('Noch verfügbar: ') + parents.duration(info['remaining']) + t(' · Bonus: {value0} Min.', value0=info['bonus']))
            self.progress.setVisible(info['limit'] > 0)
            self.progress.setValue(info['percent'])
            self.apps_summary.setText(t('{value0} von {value1} Apps sind freigegeben.', value0=sum((parents.app_active(a, st) for a in items)), value1=len(items)))
            self.bonus_label.setText(t('Heute zusätzlich: {value0} Minuten', value0=info['bonus']))
            if not self.limit_dirty:
                self.limit_spin.blockSignals(True); self.limit_spin.setValue(min(600, info['limit'])); self.limit_spin.blockSignals(False)
            if not self.color_dirty:
                self.color = st['bg_color']; self.update_color_button()
            signature = repr(items)
            if signature != self.app_signature:
                self.render_apps(items, st)
                self.app_signature = signature
            else:
                # Checkbox-Änderungen sollen weder Scrollposition noch Fokus verlieren.
                for widget, item in self.app_rows.values():
                    checkbox = widget.findChild(QCheckBox)
                    checkbox.blockSignals(True)
                    checkbox.setChecked(parents.app_active(item, st))
                    checkbox.blockSignals(False)
        except (ValueError, OSError) as exc:
            self.status.setText(t('Daten konnten nicht geladen werden: ') + str(exc))

    def open_diagnostics(self):
        dialog = ParentDiagnostics(self); dialog.exec_(); dialog.deleteLater()

    def open_bluetooth(self):
        from paimenos.bluetooth_ui import ParentBluetooth
        dialog = ParentBluetooth(self); dialog.exec_(); dialog.deleteLater()

    def open_wifi(self):
        from paimenos.wifi_ui import ParentWifi
        dialog = ParentWifi(self)
        dialog.exec_()
        command = dialog.advanced_command
        dialog.deleteLater()
        if command and self.parent() and hasattr(self.parent(), 'launch'):
            menu = self.parent()
            self.accept()
            # Beide modalen Schleifen erst verlassen, dann das Vollbildmenü ausblenden.
            QTimer.singleShot(0, lambda: menu.launch({'command': shlex.join(command)}))

    def done(self, result):
        if hasattr(self, 'refresh_timer'):
            self.refresh_timer.stop()
        for tab in (getattr(self, 'backups_tab', None), getattr(self, 'updates_tab', None)):
            if tab is not None:
                tab.stop()
        super().done(result)
