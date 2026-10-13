#!/usr/bin/env python3
"""Erzeugt iso/grub/splash.png (800x600): helles Logo-Bild als Hintergrund des GRUB-Menüs."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CREAM = (253, 246, 234)


def main():
    logo = Image.open(ROOT / 'assets/branding/paimenos-logo.png').convert('RGBA')
    height = 300
    logo = logo.resize((round(logo.width * height / logo.height), height), Image.LANCZOS)
    canvas = Image.new('RGB', (800, 600), CREAM)
    canvas.paste(logo, ((800 - logo.width) // 2, 24), logo)
    canvas.save(ROOT / 'iso/grub/splash.png', optimize=True)


if __name__ == '__main__':
    main()
