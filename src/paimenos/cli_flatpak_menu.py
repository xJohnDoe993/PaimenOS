"""Kachel einer Flatpak-App im Kinder-Menü anlegen oder entfernen.

Läuft als Benutzer kids (nie als root) und wird vom Installationsdienst aufgerufen:
    run.py cli_flatpak_menu add APP_ID TITEL
    run.py cli_flatpak_menu remove APP_ID
"""
from paimenos.i18n import t
from paimenos import parent
from paimenos.flatpak_store import tile_id, valid_app_id
import os
import shutil
import subprocess
import sys

LAUNCHER_PREFIX = '/usr/local/bin/paimenos-app-'


def copy_icon(app_id, tid):
    """Das von der App exportierte Symbol ins Menü kopieren; ohne Symbol zeigt das Menü den Standard."""
    try:
        location = subprocess.run(['/usr/bin/flatpak', 'info', '--system', '--show-location', app_id + '//stable'],
                                  capture_output=True, text=True, timeout=20, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ''
    if not location:
        return ''
    for relative in (f'256x256/apps/{app_id}.png', f'128x128/apps/{app_id}.png', f'scalable/apps/{app_id}.svg'):
        source = os.path.join(location, 'files/share/icons/hicolor', relative)
        if os.path.isfile(source) and not os.path.islink(source) and os.path.getsize(source) <= parent.MAX_ICON_BYTES:
            name = f'flatpak-{tid}.' + source.rsplit('.', 1)[1]
            os.makedirs(parent.ICONS_DIR, exist_ok=True)
            shutil.copyfile(source, os.path.join(parent.ICONS_DIR, name))
            return name
    return ''


def add(app_id, title):
    if not valid_app_id(app_id):
        raise ValueError(t('Ungültige Flatpak-App-ID.'))
    title = ' '.join(str(title).split())[:80] or app_id
    tid = tile_id(app_id)
    icon_file = copy_icon(app_id, tid)
    item = {'id': tid, 'title': title, 'type': 'native', 'icon_theme': app_id, 'command': LAUNCHER_PREFIX + tid,
            'install_source': 'flathub', 'package': '', 'flatpak_id': app_id}
    if icon_file:
        item['icon_file'] = icon_file
    with parent.app_transaction():
        items = parent.read_apps()
        if any(a.get('flatpak_id') == app_id for a in items):
            return False  # Kachel ist schon da (zum Beispiel nach einer Wiederholung)
        parent.atomic_json(parent.APPS_FILE, items + [item])
    return True


def remove(app_id):
    if not valid_app_id(app_id):
        raise ValueError(t('Ungültige Flatpak-App-ID.'))
    with parent.app_transaction():
        items = parent.read_apps()
        gone = [a for a in items if a.get('flatpak_id') == app_id]
        if not gone:
            return False
        rest = [a for a in items if a.get('flatpak_id') != app_id]
        parent.atomic_json(parent.APPS_FILE, rest)
        ids = {a['id'] for a in gone if a.get('id')}
        parent.update_settings(lambda st: st.update(disabled_apps=[i for i in st['disabled_apps'] if i not in ids]))
        for item in gone:
            icon = item.get('icon_file')
            if icon and not any(a.get('icon_file') == icon for a in rest):
                parent.remove_icon(icon)
    return True


def main(argv):
    if len(argv) == 3 and argv[0] == 'add':
        add(argv[1], argv[2])
    elif len(argv) == 2 and argv[0] == 'remove':
        remove(argv[1])
    else:
        raise SystemExit(t('Aufruf: cli_flatpak_menu add APP_ID TITEL | remove APP_ID'))


if __name__ == '__main__':
    main(sys.argv[1:])
