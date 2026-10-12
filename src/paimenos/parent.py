"""Gemeinsame Verwaltung für den lokalen und den Web-Elternbereich."""
from paimenos.i18n import t
from paimenos.paths import CONFIG_DIR
import fcntl
import json
import ipaddress
import socket
import tempfile
import urllib.parse
import urllib.request
import os
import re
import secrets
import shlex
import shutil
import subprocess
from contextlib import contextmanager
from paimenos.categories import app_category
from paimenos.state import atomic_json, read_settings, update_settings, remaining_seconds, app_with_current_title

APPS_FILE = str(CONFIG_DIR / "apps.json")
ICONS_DIR = os.path.expanduser('~/.local/share/paimenos/icons')
MAX_ICON_BYTES = 4 * 1024 * 1024


@contextmanager
def app_transaction():
    os.makedirs(os.path.dirname(APPS_FILE), exist_ok=True)
    with open(APPS_FILE + '.lock', 'a', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_apps():
    try:
        with open(APPS_FILE, encoding='utf-8') as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    if not isinstance(data, list) or any(not isinstance(a, dict) for a in data):
        raise ValueError(t('Die App-Liste ist beschädigt. Bitte Geräte-Diagnose öffnen.'))
    return [app_with_current_title(item) for item in data]


def managed_apps():
    return sorted([a for a in read_apps() if a.get('id') and a['id'] != 'poweroff'],
                  key=lambda a: str(a.get('title', '')).casefold())


def app_source_label(item):
    if item.get('emulator'):
        return t('Emulator · ') + item['emulator'].upper()
    command = str(item.get('command', ''))
    if command.startswith('/usr/local/bin/paimenos-app-') and item.get('flatpak_id'):
        return t('Flathub · stabile Flatpak-App')
    if item.get('type') == 'native' and item.get('install_source') == 'debian':
        return t('Debian-Paket')
    return ''


def app_active(item, data):
    return item.get('enabled') is not False and item['id'] not in data['disabled_apps']


def set_apps_active(ids, active):
    # Immer dieselbe Sperrreihenfolge: Apps, dann Einstellungen.
    with app_transaction():
        items = read_apps()
        selected = [a for a in items if a.get('id') in ids and a['id'] != 'poweroff']
        if len({a['id'] for a in selected}) != len(set(ids)):
            raise ValueError(t('Eine der ausgewählten Apps existiert nicht mehr.'))
        changed = False
        for item in selected:
            if active and item.get('enabled') is False:
                item['enabled'] = True
                changed = True
        if changed:
            atomic_json(APPS_FILE, items)
        def update(data):
            disabled = set(data['disabled_apps'])
            if active:
                disabled.difference_update(ids)
            else:
                disabled.update(ids)
            data['disabled_apps'] = sorted(disabled)
        update_settings(update)


def toggle_app(app_id):
    with app_transaction():
        items = read_apps()
        item = next((a for a in items if a.get('id') == app_id and app_id != 'poweroff'), None)
        if item is None:
            raise ValueError(t('Diese App existiert nicht mehr.'))
        # Statusentscheidung unter der Einstellungssperre treffen.
        if item.get('enabled') is False:
            item['enabled'] = True
            atomic_json(APPS_FILE, items)
            update_settings(lambda st: st.update(disabled_apps=[i for i in st['disabled_apps'] if i != app_id]))
        else:
            def toggle(st):
                disabled = set(st['disabled_apps'])
                disabled.symmetric_difference_update({app_id})
                st['disabled_apps'] = sorted(disabled)
            update_settings(toggle)


def minute_value(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise ValueError(t('Bitte ganze Minuten zwischen 0 und 600 eingeben.')) from None
    if not 0 <= minutes <= 600:
        raise ValueError(t('Bitte ganze Minuten zwischen 0 und 600 eingeben.'))
    return minutes


def set_time(limit):
    return update_settings({'daily_limit_minutes': minute_value(limit)})


def add_bonus(value):
    minutes = minute_value(value)
    if minutes == 0:
        raise ValueError(t('Die Bonuszeit muss mindestens eine Minute betragen.'))
    result = [0]
    def add(st):
        before = st['bonus_minutes']
        st['bonus_minutes'] = min(600, before + minutes)
        result[0] = st['bonus_minutes'] - before
    update_settings(add)
    return result[0]


def clear_bonus():
    update_settings({'bonus_minutes': 0})


def reset_today():
    update_settings({'today_used_seconds': 0, 'bonus_minutes': 0})


def usage(data=None):
    st = data if data is not None else read_settings()
    remaining = remaining_seconds(st)
    total = (st['daily_limit_minutes'] + st['bonus_minutes']) * 60
    return {'used': st['today_used_seconds'], 'remaining': remaining,
            'limit': st['daily_limit_minutes'], 'bonus': st['bonus_minutes'],
            'percent': min(100, int(st['today_used_seconds'] / total * 100)) if remaining is not None else 0}


def duration(seconds):
    if seconds is None:
        return t('Unbegrenzt')
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return t('{value0} Min. {value1:02d} Sek.', value0=minutes, value1=seconds)


def save_preferences(pin, confirmation, color, category_tabs=None, speak_titles=None, speak_voice=None):
    patch = {}
    if pin:
        if not re.fullmatch(r'[0-9]{4,12}', pin):
            raise ValueError(t('Die PIN muss aus 4 bis 12 Ziffern bestehen.'))
        if pin != confirmation:
            raise ValueError(t('Die beiden PIN-Eingaben stimmen nicht überein.'))
        patch['pin'] = pin
    elif confirmation:
        raise ValueError(t('Bitte die neue PIN in beiden Feldern eingeben.'))
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError(t('Bitte eine gültige Hintergrundfarbe auswählen.'))
    if category_tabs is not None:
        patch['category_tabs'] = bool(category_tabs)
    if speak_titles is not None:
        patch['speak_titles'] = bool(speak_titles)
    if speak_voice is not None:
        if speak_voice not in ('female', 'male'):
            raise ValueError(t('Ungültige Stimme.'))
        patch['speak_voice'] = speak_voice
    patch['bg_color'] = color.upper()
    return update_settings(patch)


def editable_app(item):
    return item.get('id') != 'poweroff' and item.get('type', 'native') in ('native', 'webapp', 'camera')


def deletable_app(item):
    return item.get('id') != 'poweroff' and (item.get('type') == 'webapp' or
                                            (item.get('type') == 'native' and item.get('custom') is True))


def app_launch_fields(item):
    if item.get('type') == 'webapp':
        return item.get('url', ''), ''
    if item.get('type') == 'camera' or item.get('emulator'):
        return '', ''
    try:
        parts = shlex.split(item.get('command', ''))
    except ValueError:
        return item.get('command', ''), ''
    return (parts[0], shlex.join(parts[1:])) if parts else ('', '')


def native_command(target, arguments):
    # Windows-artige Pfade akzeptieren; Linux-Argumente behalten ihre Quotes.
    if target.startswith('\\'):
        target = target.replace('\\', '/')
    if len(target) + len(arguments) > 8192 or any(ord(c) < 32 for c in target + arguments):
        raise ValueError(t('Startbefehl oder Parameter sind zu lang oder enthalten Steuerzeichen.'))
    try:
        expanded = os.path.expanduser(target)
        parts = [expanded] if os.path.isfile(expanded) else shlex.split(target)
        extra = shlex.split(arguments)
    except ValueError:
        raise ValueError(t('Anführungszeichen im Startbefehl oder in den Parametern sind nicht geschlossen.')) from None
    if not parts or parts[0].startswith('__'):
        raise ValueError(t('Bitte ein installiertes Programm oder einen ausführbaren Linux-Pfad angeben.'))
    program = os.path.expanduser(parts[0])
    if '/' in program:
        if not os.path.isabs(program):
            raise ValueError(t('Bitte einen absoluten Linux-Pfad verwenden, z. B. /usr/bin/vlc.'))
        resolved = os.path.normpath(program)
    else:
        path = os.environ.get('PATH', os.defpath) + ':/usr/local/bin:/usr/bin:/bin:/usr/games'
        resolved = shutil.which(program, path=path)
    if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        raise ValueError(t('Das Programm wurde nicht gefunden oder ist für den Benutzer kids nicht ausführbar. Bitte zuerst installieren und den Pfad prüfen.'))
    # Die Kachel startet als kids, ohne Shell; Quotes erhalten einzelne Argumente.
    return shlex.join([resolved] + parts[1:] + extra)


def save_app(title, target, arguments='', icon_url='', app_id=None, icon_data=None, reset_icon=False, category=None, pronunciation=None):
    title, target, arguments, icon_url = (value.strip() for value in (title, target, arguments, icon_url))
    if not title or len(title) > 80 or any(ord(c) < 32 for c in title):
        raise ValueError(t('Bitte einen Namen mit höchstens 80 Zeichen ohne Steuerzeichen eingeben.'))
    if pronunciation is not None:
        pronunciation = ' '.join(pronunciation.split())
        if len(pronunciation) > 80 or any(ord(c) < 32 for c in pronunciation):
            raise ValueError(t('Die Aussprache darf höchstens 80 Zeichen ohne Steuerzeichen enthalten.'))
    previous = {}
    target_id = safe_id(title) + '-' + secrets.token_hex(4) if app_id is None else app_id
    if app_id is not None:
        previous = next((a for a in read_apps() if a.get('id') == app_id), None)
        if previous is None or not editable_app(previous):
            raise ValueError(t('Diese App existiert nicht mehr oder kann nicht bearbeitet werden.'))
    if previous.get('emulator'):
        # ROM-Pfad, Core und Startbefehl behalten; nur die Kachel bearbeiten.
        patch = {'title': title}
    elif previous.get('type') == 'camera':
        if target or arguments:
            raise ValueError(t('Die Kamera verwendet einen fest eingebauten Startbefehl.'))
        patch = {'title': title}
    elif valid_http_url(target):
        if arguments:
            raise ValueError(t('Zusätzliche Parameter sind für lokale Programme vorgesehen. URL-Parameter bitte direkt an die Webadresse anhängen.'))
        patch = {'title': title, 'type': 'webapp', 'url': target,
                 'profile': previous.get('profile') or target_id}
    else:
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', target):
            raise ValueError(t('Webapps benötigen eine gültige HTTP/HTTPS-Webadresse.'))
        patch = {'title': title, 'type': 'native', 'command': native_command(target, arguments)}
    if category is not None:
        if category not in ('auto', 'webapps', 'games', 'productive'):
            raise ValueError(t('Ungültige Kategorie.'))
        patch['category'] = category
    if reset_icon and (icon_data is not None or icon_url):
        raise ValueError(t('Bitte entweder ein eigenes Bild wählen oder das Bild zurücksetzen.'))
    if icon_data is not None and icon_url:
        raise ValueError(t('Bitte entweder eine Bilddatei oder eine Bildadresse verwenden.'))
    new_icon = None
    if icon_data is not None:
        new_icon = store_uploaded_icon(icon_data, target_id)
        patch.update(icon_file=new_icon, icon_url='')
    elif icon_url and (icon_url != previous.get('icon_url') or not previous.get('icon_file')):
        new_icon = download_icon(icon_url, target_id)
        patch.update(icon_file=new_icon, icon_url=icon_url)
    elif icon_url:
        patch['icon_url'] = icon_url
    try:
        with app_transaction():
            items = read_apps()
            if app_id is None:
                item = dict(id=target_id, custom=True, **patch)
                items.append(item)
            else:
                item = next((a for a in items if a.get('id') == app_id), None)
                if item is None or not editable_app(item) or item.get('type') != previous.get('type'):
                    raise ValueError(t('Diese App wurde während der Bearbeitung gelöscht oder geändert. Bitte erneut öffnen.'))
            old_icon = item.get('icon_file')
            item.update(patch)
            item['user_modified'] = True
            if pronunciation:
                item['pronunciation'] = pronunciation
            elif pronunciation is not None:
                item.pop('pronunciation', None)
            if patch.get('type') == 'webapp':
                item.pop('command', None)
            elif patch.get('type') == 'native':
                item.pop('url', None)
                if app_launch_fields(patch)[0] != app_launch_fields(previous)[0]:
                    item.pop('install_source', None)
                    item.pop('flatpak_id', None)
            if reset_icon:
                item.pop('icon_file', None)
                item.pop('icon_url', None)
            atomic_json(APPS_FILE, items)
            if (new_icon or reset_icon) and not any(a.get('icon_file') == old_icon for a in items):
                remove_icon(old_icon)
    except Exception:
        if new_icon:
            remove_icon(new_icon)
        raise
    return target_id


def save_webapp(title, url, icon_url='', app_id=None):
    # Kompatibilität für bisherige Aufrufer.
    if not valid_http_url(url.strip()):
        raise ValueError(t('Bitte eine gültige HTTP/HTTPS-Webadresse eingeben.'))
    return save_app(title, url, icon_url=icon_url, app_id=app_id)


def remove_icon(name):
    if name and os.path.basename(name) == name and not name.startswith('.'):
        try:
            os.unlink(os.path.join(ICONS_DIR, name))
        except OSError:
            pass


def delete_app(app_id):
    with app_transaction():
        items = read_apps()
        item = next((a for a in items if a.get('id') == app_id and deletable_app(a)), None)
        if item is None:
            raise ValueError(t('Diese App existiert nicht mehr oder kann nur gesperrt werden.'))
        atomic_json(APPS_FILE, [a for a in items if a.get('id') != app_id])
        update_settings(lambda st: st.update(disabled_apps=[i for i in st['disabled_apps'] if i != app_id]))
        # Spiel-Dateien entfernen; Spielstände bleiben erhalten.
        if item.get('emulator') and re.fullmatch(r'rom-[0-9a-f]{24}', app_id):
            shutil.rmtree(os.path.expanduser('~/.local/share/paimenos/emulators/roms/') + app_id, ignore_errors=True)
        # Ein eventuell von anderen Apps verwendetes Symbol bleibt erhalten.
        if not any(a.get('icon_file') == item.get('icon_file') for a in items if a.get('id') != app_id):
            remove_icon(item.get('icon_file'))


def delete_webapp(app_id):
    delete_app(app_id)


def safe_id(value):
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return value[:48] or "webapp"


def valid_http_url(value):
    if not value or re.search(r"\s", value):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        return parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname) and parsed.username is None and (parsed.port is None or 1 <= parsed.port <= 65535)
    except ValueError:
        return False


def public_image_url(value):
    if not valid_http_url(value):
        return False
    try:
        host = urllib.parse.urlparse(value).hostname
    except Exception:
        return False
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if not addr.is_global:
                return False
    except Exception:
        return False
    return True


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_image_url(newurl):
            raise ValueError(t('Die Bild-Weiterleitung ist keine öffentliche Adresse.'))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def convert_icon(data, app_id):
    if not data or len(data) > MAX_ICON_BYTES:
        raise ValueError(t('Bitte eine Bilddatei mit höchstens 4 MB verwenden.'))
    os.makedirs(ICONS_DIR, exist_ok=True)
    # Der Zufallsteil bleibt auch bei langen App-IDs erhalten.
    filename = safe_id(app_id)[:40] + '-' + secrets.token_hex(8) + '.png'
    destination = os.path.join(ICONS_DIR, filename)
    fd, tmp = tempfile.mkstemp(prefix='.icon-', dir=ICONS_DIR)
    png_tmp = tmp + '.png'
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        try:
            result = subprocess.run(
                ['convert', '-limit', 'memory', '128MiB', '-limit', 'map', '256MiB',
                 '-limit', 'disk', '64MiB', '-limit', 'time', '20',
                 tmp + '[0]', '-background', 'none', '-auto-orient', '-thumbnail', '256x256>',
                 '-gravity', 'center', '-extent', '256x256', png_tmp],
                capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            raise ValueError(t('Das Verarbeiten des Bildes dauert zu lange. Bitte ein kleineres Bild verwenden.')) from None
        if result.returncode != 0 or not os.path.exists(png_tmp):
            raise ValueError(t('Das Bild konnte nicht verarbeitet werden. Bitte PNG, JPEG, GIF oder WebP verwenden.'))
        os.replace(png_tmp, destination)
        os.chmod(destination, 0o644)
        return filename
    finally:
        for temporary in (tmp, png_tmp):
            if os.path.exists(temporary):
                os.unlink(temporary)


def store_uploaded_icon(data, app_id):
    if not isinstance(data, bytes):
        raise ValueError(t('Ungültige Bilddatei.'))
    # Nur Rasterformate, keine aktiven Dokumente oder lokalen Dateireferenzen.
    raster = (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff') or
              data.startswith((b'GIF87a', b'GIF89a')) or
              (data.startswith(b'RIFF') and data[8:12] == b'WEBP'))
    if not raster:
        raise ValueError(t('Bitte eine PNG-, JPEG-, GIF- oder WebP-Bilddatei auswählen.'))
    return convert_icon(data, app_id)


def download_icon(icon_url, app_id):
    if not public_image_url(icon_url):
        raise ValueError(t('Die Bild-URL muss eine öffentliche HTTP/HTTPS-Adresse sein.'))
    req = urllib.request.Request(icon_url, headers={'User-Agent': 'PaimenOS-Parent-Web/1.0'})
    with urllib.request.build_opener(PublicRedirectHandler()).open(req, timeout=10) as response:
        content_type = (response.headers.get('Content-Type') or '').lower()
        data = response.read(MAX_ICON_BYTES + 1)
    if content_type and not content_type.startswith('image/'):
        raise ValueError(t('Die Bild-URL liefert keine Bilddatei.'))
    return convert_icon(data, app_id)
