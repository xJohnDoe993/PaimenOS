"""Kachel und Tastatur-Weiterleitung des Kinder-Menüs."""
from paimenos.i18n import t
from paimenos.paths import USER_DATA_DIR
import os
from paimenos.screen_guard import child_access_blocked
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QFont, QIcon, QPixmap, QColor, QPainter, QLinearGradient, QPen
from PyQt5.QtWidgets import (
    QVBoxLayout, QPushButton, QLabel
)

ICONS_DIR = str(USER_DATA_DIR / "icons")

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

ARROW_KEYS = {Qt.Key_Left: 'left', Qt.Key_Right: 'right', Qt.Key_Up: 'up', Qt.Key_Down: 'down'}


class ArrowPassThrough:
    """QAbstractButton treats arrows as plain focus next/previous and the surrounding
    QScrollArea would scroll; route them to the window's grid navigation instead."""

    def keyPressEvent(self, event):
        action = ARROW_KEYS.get(event.key())
        window = self.window()
        if action and hasattr(window, 'navigate'):
            if not child_access_blocked():
                window.navigate(action)
            event.accept()
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
