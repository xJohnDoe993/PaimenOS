"""Gemeinsamer, gesperrter und atomarer Zugriff auf die Einstellungen."""
from paimenos.i18n import t
from paimenos.paths import CONFIG_DIR
import fcntl
import json
import os
import tempfile
from datetime import date

SETTINGS_FILE = str(CONFIG_DIR / "settings.json")
DEFAULTS = {
    "pin": "", "bg_color": "#FF9F00", "daily_limit_minutes": 0,
    "bonus_minutes": 0, "today_used_seconds": 0,
    "last_used_date": "", "disabled_apps": [], "category_tabs": False,
    "speak_titles": False, "speak_voice": "male",
}


def app_with_current_title(item):
    """Localise recognised built-in titles; preserve custom names and saved data."""
    if (item.get('id') == 'camera' and item.get('type') == 'camera'
            and item.get('command') == '__CAMERA__'
            and item.get('title') in ('Kamera / Bilder', 'Kamera & Bilder',
                'Kamera / Bilder / Videos', 'Camera / Pictures / Videos')):
        return dict(item, title=t('Kamera / Bilder / Videos'))
    if (item.get('id') == 'poweroff' and item.get('command') == '__POWEROFF__'
            and item.get('title') in ('Ausschalten', 'Shut down')):
        return dict(item, title=t('Ausschalten'))
    return item


def atomic_json(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".paimenos-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def update_settings(change=None):
    """Änderungen lesen und schreiben, ohne parallele Änderungen zu verlieren."""
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE + ".lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as handle:
                saved = json.load(handle)
            if not isinstance(saved, dict):
                raise ValueError(t('Die Einstellungen müssen ein JSON-Objekt sein.'))
            data.update(saved)
        except FileNotFoundError as exc:
            raise ValueError(t('Eltern-Einstellungen fehlen. Installation abschließen oder Sicherung wiederherstellen.')) from exc
        # Beschädigte Dateien nicht still durch Standardwerte überschreiben.
        before = json.dumps(data, sort_keys=True)
        for key in ("daily_limit_minutes", "bonus_minutes", "today_used_seconds"):
            value = saved.get(key)
            # Zero means unlimited. Never turn corrupt/missing limits or usage
            # into zero, truncate fractions, or accept bool as an integer.
            if isinstance(value, str) and value.isascii() and value.isdigit():
                value = int(value)
            if type(value) is not int or value < 0:
                raise ValueError(t('Ungültiger Bildschirmzeit-Wert: ') + key)
            data[key] = value
        if not isinstance(data["disabled_apps"], list):
            data["disabled_apps"] = []
        data["pin"] = str(data["pin"])
        if not data["pin"].isascii() or not data["pin"].isdigit() or not 4 <= len(data["pin"]) <= 12:
            raise ValueError(t('Die gespeicherte Eltern-PIN ist ungültig.'))
        if not isinstance(data.get("bg_color"), str):
            data["bg_color"] = DEFAULTS["bg_color"]
        today = str(date.today())
        if data["last_used_date"] != today:
            data.update(last_used_date=today, today_used_seconds=0, bonus_minutes=0)
        if callable(change):
            change(data)
        elif change is not None:
            data.update(change)
        if change is not None or json.dumps(data, sort_keys=True) != before:
            atomic_json(SETTINGS_FILE, data)
        return data


def read_settings():
    return update_settings()


def remaining_seconds(data):
    # 0 als Tageslimit bleibt unbegrenzt, auch wenn Bonuszeit eingetragen ist.
    if data["daily_limit_minutes"] <= 0:
        return None
    return max(0, (data["daily_limit_minutes"] + data["bonus_minutes"]) * 60
               - data["today_used_seconds"])
