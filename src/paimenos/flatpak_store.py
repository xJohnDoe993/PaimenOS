"""Flathub-Suche und Liste zusätzlich installierter Flatpak-Apps (Webbereich, läuft als kids)."""
from paimenos.i18n import t
import json
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

FLATHUB_SEARCH = 'https://flathub.org/api/v2/search'
ICON_PREFIX = 'https://dl.flathub.org/'
MANAGED_FILE = Path('/var/lib/paimenos/flatpak-apps.list')
APP_ID = re.compile(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_-]+){2,}')
MAX_RESULTS = 24
MAX_RESPONSE = 2 * 1024 * 1024


class StoreError(ValueError):
    pass


def valid_app_id(value):
    return isinstance(value, str) and len(value) <= 255 and APP_ID.fullmatch(value) is not None


def tile_id(app_id):
    """Kennung der Menü-Kachel und des Startskripts /usr/local/bin/paimenos-app-<Kennung>."""
    return 'fp-' + re.sub(r'[^a-z0-9]+', '-', app_id.lower()).strip('-')


def clean_term(value):
    term = ' '.join(str(value or '').split())
    if not 2 <= len(term) <= 80 or any(ord(c) < 32 for c in term):
        raise StoreError(t('Bitte einen Suchbegriff mit 2 bis 80 Zeichen eingeben.'))
    return term


def parse_hits(payload):
    """Nur Desktop-Anwendungen mit gültiger App-ID; Texte und Symbol-Adressen werden begrenzt."""
    hits = payload.get('hits') if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        raise StoreError(t('Unerwartete Antwort von Flathub.'))
    results = []
    for hit in hits:
        if not isinstance(hit, dict) or hit.get('type', 'desktop-application') != 'desktop-application':
            continue
        app_id = hit.get('app_id')
        if not valid_app_id(app_id):
            continue
        icon = hit.get('icon')
        results.append({
            'app_id': app_id,
            'name': str(hit.get('name') or app_id)[:80],
            'summary': str(hit.get('summary') or '')[:200],
            'icon': icon if isinstance(icon, str) and icon.startswith(ICON_PREFIX) and len(icon) <= 500 else '',
        })
        if len(results) >= MAX_RESULTS:
            break
    return results


def search(term, opener=urllib.request.urlopen):
    term = clean_term(term)
    request = urllib.request.Request(
        FLATHUB_SEARCH, data=json.dumps({'query': term}).encode(),
        headers={'Content-Type': 'application/json', 'User-Agent': 'PaimenOS-Store/1'})
    try:
        with opener(request, timeout=15) as response:
            if not response.url.startswith('https://flathub.org/'):
                raise StoreError(t('Unerwartete Antwort von Flathub.'))
            data = response.read(MAX_RESPONSE + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise StoreError(t('Flathub ist nicht erreichbar. Internetverbindung prüfen.')) from exc
    if len(data) > MAX_RESPONSE:
        raise StoreError(t('Unerwartete Antwort von Flathub.'))
    try:
        return parse_hits(json.loads(data))
    except ValueError as exc:
        if isinstance(exc, StoreError):
            raise
        raise StoreError(t('Unerwartete Antwort von Flathub.')) from exc


def managed_ids(path=MANAGED_FILE):
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    return sorted({line.strip() for line in lines if valid_app_id(line.strip())})


def installed(run=subprocess.run, path=MANAGED_FILE):
    """Von PaimenOS verwaltete Flatpak-Apps mit Anzeigename und Version."""
    ids = managed_ids(path)
    names = {}
    try:
        result = run(['/usr/bin/flatpak', 'list', '--system', '--app', '--columns=application,name,version'],
                     capture_output=True, text=True, timeout=20, check=False)
        for line in result.stdout.splitlines():
            parts = line.split('\t')
            if len(parts) >= 2:
                names[parts[0].strip()] = (parts[1].strip(), parts[2].strip() if len(parts) > 2 else '')
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [{'app_id': app_id, 'name': names[app_id][0] or app_id, 'version': names[app_id][1]}
            for app_id in ids if app_id in names]
