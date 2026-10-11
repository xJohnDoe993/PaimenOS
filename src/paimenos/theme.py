"""Gemeinsame Farben des Kinder-Designs, ohne Qt-Abhängigkeit (Menü, Webapp-Leiste, Overlays)."""
import colorsys
import re

DEFAULT_COLOR = '#FF9F00'
TEXT_COLOR = '#1d2b3a'


def normalize(color):
    """Valid '#rrggbb' in lower case, otherwise the default colour."""
    if isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        return color.lower()
    return DEFAULT_COLOR.lower()


def adjust_value(color, factor):
    """Like QColor.lighter/darker: scale the HSV value, spill the excess into lower saturation."""
    color = normalize(color)
    red, green, blue = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    value *= factor
    if value > 1:
        saturation = max(0.0, saturation - (value - 1))
        value = 1.0
    return '#%02x%02x%02x' % tuple(round(part * 255) for part in colorsys.hsv_to_rgb(hue, saturation, value))


def gradient(color):
    """Top and bottom colour of the menu background gradient (QColor.lighter(114) / darker(110))."""
    return adjust_value(color, 1.14), adjust_value(color, 100 / 110)
