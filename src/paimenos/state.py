"""Gemeinsamer, gesperrter und atomarer Zugriff auf die Einstellungen."""
from paimenos.i18n import t
from paimenos.paths import CONFIG_DIR
import fcntl
import json
import os
import tempfile
import re
from datetime import date, datetime

SETTINGS_FILE = str(CONFIG_DIR / "settings.json")
DEFAULTS = {
    "pin": "", "bg_color": "#FF9F00", "daily_limit_minutes": 0,
    "bonus_minutes": 0, "today_used_seconds": 0,
    "last_used_date": "", "disabled_apps": [], "category_tabs": False,
    "speak_titles": False, "speak_voice": "male",
    "allowed_from": "", "allowed_until": "", "window_override_date": "",
}
CLOCK_PATTERN = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")


def clock_minutes(value):
    """'HH:MM' als Minuten seit Mitternacht; ValueError bei ungültigem Wert."""
    if not isinstance(value, str) or not CLOCK_PATTERN.fullmatch(value):
        raise ValueError(t('Ungültige Uhrzeit: ') + str(value))
    return int(value[:2]) * 60 + int(value[3:])


def usage_window(data):
    """(von, bis) in Minuten oder None ohne Nutzungszeitraum; ValueError bei ungültigen Werten."""
    start, end = data.get("allowed_from", ""), data.get("allowed_until", "")
    if start == "" and end == "":
        return None
    start, end = clock_minutes(start), clock_minutes(end)
    if start == end:
        raise ValueError(t('Ungültiger Nutzungszeitraum.'))
    return start, end


def in_usage_window(data, now=None):
    """None ohne Nutzungszeitraum, sonst ob die Uhrzeit darin liegt (auch über Mitternacht)."""
    window = usage_window(data)
    if window is None:
        return None
    now = now or datetime.now()
    # Mit dem Eltern-PIN für heute freigeschaltet.
    if data.get("window_override_date") == str(now.date()):
        return True
    minute = now.hour * 60 + now.minute
    start, end = window
    return start <= minute < end if start < end else minute >= start or minute < end


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


def normalize(saved, change=None, with_pin=True):
    """Gespeicherte Werte prüfen, Tageswechsel anwenden und eine Änderung einarbeiten.

    Gibt (Daten, geändert) zurück. Beschädigte Werte lösen ValueError aus statt still ersetzt zu werden.
    Ohne with_pin (Guard-Speicher) gehört die PIN nicht zu den Einstellungen.
    """
    data = dict(DEFAULTS)
    data.update(saved)
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
    # Auch ein beschädigter Nutzungszeitraum wird nicht still ersetzt.
    usage_window(data)
    if not isinstance(data["disabled_apps"], list):
        data["disabled_apps"] = []
    if with_pin:
        data["pin"] = str(data["pin"])
        if not data["pin"].isascii() or not data["pin"].isdigit() or not 4 <= len(data["pin"]) <= 12:
            raise ValueError(t('Die gespeicherte Eltern-PIN ist ungültig.'))
    else:
        data.pop("pin", None)
    if not isinstance(data.get("bg_color"), str):
        data["bg_color"] = DEFAULTS["bg_color"]
    today = str(date.today())
    if data["last_used_date"] != today:
        data.update(last_used_date=today, today_used_seconds=0, bonus_minutes=0)
    if callable(change):
        change(data)
    elif change is not None:
        data.update(change)
    return data, change is not None or json.dumps(data, sort_keys=True) != before


def update_settings(change=None):
    """Änderungen lesen und schreiben, ohne parallele Änderungen zu verlieren."""
    if guard_installed():
        return guard_update(change)
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE + ".lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as handle:
                saved = json.load(handle)
            if not isinstance(saved, dict):
                raise ValueError(t('Die Einstellungen müssen ein JSON-Objekt sein.'))
        except FileNotFoundError as exc:
            raise ValueError(t('Eltern-Einstellungen fehlen. Installation abschließen oder Sicherung wiederherstellen.')) from exc
        # Beschädigte Dateien nicht still durch Standardwerte überschreiben.
        data, changed = normalize(saved, change)
        if changed:
            atomic_json(SETTINGS_FILE, data)
        return data


def read_settings():
    if guard_installed():
        return run_op('public')
    return update_settings()


# --- Guard: PIN und Einschränkungen liegen root-eigen unter /var/lib/paimenos/guard -------------------------------
GUARD_DIR = "/var/lib/paimenos/guard"
# Für alle lesbar, aber nur root kann sie anlegen. Der Guard erstellt sie erst, wenn der Speicher bereit ist.
GUARD_MARKER = "/var/lib/paimenos/guard.enabled"


def guard_installed():
    """Sobald der Guard eingerichtet ist, lesen und ändern Kinderprozesse nie mehr die Datei im Home."""
    return os.path.exists(GUARD_MARKER)


def run_op(op, legacy=None, **args):
    """Benannte Operation beim Guard ausführen (root direkt am Speicher, alle anderen über den Socket).

    Ohne Guard (Altgeräte, Entwicklung, Tests) läuft legacy() auf der Einstellungsdatei.
    """
    if not guard_installed():
        if legacy is None:
            raise ValueError(t('Diese Funktion benötigt den PaimenOS-Guard.'))
        return legacy()
    if os.geteuid() == 0:
        from paimenos import guard_store
        return guard_store.GuardStore().call(op, args, authenticated=True)
    from paimenos import guard_client
    return guard_client.call(op, **args)


def guard_update(change):
    if change is None:
        return run_op('public')
    if isinstance(change, dict) and set(change) == {"bg_color"}:
        return run_op('set_color', bg_color=change["bg_color"])
    if os.geteuid() == 0:
        from paimenos import guard_store
        return guard_store.GuardStore().apply(change)
    raise PermissionError(t('Die Einstellungen werden vom Guard verwaltet und sind nur mit der Eltern-PIN änderbar.'))


def remaining_seconds(data, now=None):
    # Außerhalb des Nutzungszeitraums ist keine Zeit verfügbar, auch ohne Tageslimit.
    if in_usage_window(data, now) is False:
        return 0
    # 0 als Tageslimit bleibt unbegrenzt, auch wenn Bonuszeit eingetragen ist.
    if data["daily_limit_minutes"] <= 0:
        return None
    return max(0, (data["daily_limit_minutes"] + data["bonus_minutes"]) * 60
               - data["today_used_seconds"])
