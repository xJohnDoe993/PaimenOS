"""Eltern-Einstellungen und eigene Webapps für das USB-Backup: sichern, prüfen, wiederherstellen.

Nicht enthalten: Eltern-PIN, Bonuszeit, verbrauchte Zeit und Tagesfreigabe. Apps mit Startbefehl (native Programme,
Spiele) werden nie übernommen, weil ihr Startbefehl als Kind läuft; Webapps nur mit geprüfter http(s)-Adresse.
Der Aufrufer hält die Sperre der App-Liste (backups.start), deshalb hier kein app_transaction().
"""
from paimenos.i18n import t
import json
import re

from paimenos import parent, state

FORMAT = 'paimenos-settings-1'
LIMIT = 1024 * 1024
MAX_WEBAPPS = 200
CATEGORIES = ('auto', 'webapps', 'games', 'productive')
ID_PATTERN = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
COLOR_PATTERN = re.compile(r'#[0-9A-Fa-f]{6}')


def _text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 for c in value)


def snapshot():
    settings = state.read_settings()
    webapps = []
    for item in parent.read_apps():
        if (item.get('type') != 'webapp' or not ID_PATTERN.fullmatch(str(item.get('id', '')))
                or not parent.valid_http_url(str(item.get('url', '')))):
            continue
        entry = {'id': item['id'], 'title': item.get('title', ''), 'url': item['url']}
        if item.get('category') in CATEGORIES:
            entry['category'] = item['category']
        if item.get('pronunciation'):
            entry['pronunciation'] = item['pronunciation']
        webapps.append(entry)
    keys = ('bg_color', 'daily_limit_minutes', 'category_tabs', 'speak_titles', 'speak_voice',
            'allowed_from', 'allowed_until', 'disabled_apps')
    return json.dumps({'format': FORMAT, 'settings': {key: settings[key] for key in keys}, 'webapps': webapps},
                      ensure_ascii=False).encode()


def parse(raw):
    """Prüft ein Backup vollständig, bevor etwas auf dem Gerät verändert wird."""
    bad = ValueError(t('Ungültige Einstellungen im Backup.'))
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise bad from None
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        raise bad
    saved, webapps = data.get('settings'), data.get('webapps')
    if not isinstance(saved, dict) or not isinstance(webapps, list) or len(webapps) > MAX_WEBAPPS:
        raise bad
    limit = saved.get('daily_limit_minutes')
    if type(limit) is not int or not 0 <= limit <= 24 * 60:
        raise bad
    if not isinstance(saved.get('bg_color'), str) or not COLOR_PATTERN.fullmatch(saved['bg_color']):
        raise bad
    if any(type(saved.get(key)) is not bool for key in ('category_tabs', 'speak_titles')):
        raise bad
    if saved.get('speak_voice') not in ('male', 'female'):
        raise bad
    window = {'allowed_from': saved.get('allowed_from', ''), 'allowed_until': saved.get('allowed_until', '')}
    try:
        state.usage_window(window)
    except ValueError:
        raise bad from None
    disabled = saved.get('disabled_apps')
    if not isinstance(disabled, list) or len(disabled) > 1000 or any(not _text(i, 64) for i in disabled):
        raise bad
    clean, seen = [], set()
    for item in webapps:
        if not isinstance(item, dict) or not ID_PATTERN.fullmatch(str(item.get('id', ''))) or item['id'] in seen:
            raise bad
        seen.add(item['id'])
        url = item.get('url')
        if not _text(url, 2048) or not parent.valid_http_url(url) or not _text(item.get('title'), 80):
            raise bad
        entry = {'id': item['id'], 'title': item['title'], 'url': url}
        if 'category' in item:
            if item['category'] not in CATEGORIES:
                raise bad
            entry['category'] = item['category']
        if 'pronunciation' in item:
            if not _text(item['pronunciation'], 80):
                raise bad
            entry['pronunciation'] = item['pronunciation']
        clean.append(entry)
    patch = {key: saved[key] for key in ('bg_color', 'daily_limit_minutes', 'category_tabs', 'speak_titles', 'speak_voice')}
    patch.update(window, bg_color=saved['bg_color'].upper())
    return {'settings': patch, 'disabled': disabled, 'webapps': clean}


def apply(plan):
    """Übernimmt ein geprüftes Backup. Gibt (Webapps übernommen, Webapps übersprungen) zurück."""
    items = parent.read_apps()
    by_id = {item.get('id'): item for item in items}
    restored = skipped = 0
    for web in plan['webapps']:
        existing = by_id.get(web['id'])
        if existing is None:
            existing = {'id': web['id'], 'custom': True, 'type': 'webapp', 'profile': web['id']}
            items.append(existing)
            by_id[web['id']] = existing
        elif existing.get('type') != 'webapp':
            skipped += 1  # gleiche ID, aber ein anderes Programm: nie überschreiben
            continue
        existing.update({key: value for key, value in web.items() if key != 'id'})
        existing['user_modified'] = True
        restored += 1
    parent.atomic_json(parent.APPS_FILE, items)
    known = {item.get('id') for item in items}
    patch = dict(plan['settings'], disabled_apps=[i for i in plan['disabled'] if i in known and i != 'poweroff'])
    state.run_op('set_many', legacy=lambda: state.update_settings(patch), patch=patch)
    return restored, skipped
