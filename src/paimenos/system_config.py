"""Verwaltete Systemdateien (Firefox-Richtlinien, Autoconfig, polkit, journald, Touchpad) mit dem Release abgleichen.

Updates ersetzen nur Code unter /usr/local/lib/paimenos. Diese Dateien liegen im System; der Abgleich läuft als root
beim Start von paimenos-emulators.service (wird bei jedem Update neu gestartet) und bei der Installation.
Nur Dateien aus data/system-config.json werden überschrieben, alles andere bleibt unberührt.
"""
from paimenos.i18n import t
import json
import os
from pathlib import Path
import sys

from paimenos.paths import DATA_DIR

LIST = DATA_DIR / 'system-config.json'
SOURCES = DATA_DIR / 'system-config'
MODE = 0o644


def load(path=LIST):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 1 or not isinstance(data.get('files'), list):
        raise ValueError(t('Ungültige Liste der Systemdateien.'))
    for entry in data['files']:
        source, target = Path(entry['source']), Path(entry['target'])
        if source.is_absolute() or '..' in source.parts or not target.is_absolute() or '..' in target.parts:
            raise ValueError(t('Ungültiger Pfad in der Liste der Systemdateien: ') + entry['source'])
    return data['files']


def _write(target, content):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.' + target.name + '.paimenos')
    try:
        temporary.write_bytes(content)
        os.chmod(temporary, MODE)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def sync(list_path=LIST, sources=SOURCES, root=Path('/')):
    """Gibt (geändert, fehlgeschlagen) zurück. Ein Fehler stoppt nur den Rest seiner Gruppe."""
    changed, failed, broken_groups = [], [], set()
    for entry in load(list_path):
        group = entry.get('group')
        target = root / Path(entry['target']).relative_to('/')
        if group in broken_groups:
            continue
        if entry.get('requires') and not (root / Path(entry['requires']).relative_to('/')).is_dir():
            if group:
                broken_groups.add(group)
            continue
        try:
            source = sources / entry['source']
            if source.is_symlink() or not source.is_file():
                raise OSError(t('Quelldatei fehlt: ') + entry['source'])
            content = source.read_bytes()
            if target.is_file() and not target.is_symlink() and target.read_bytes() == content \
                    and (target.stat().st_mode & 0o777) == MODE:
                continue
            _write(target, content)
            changed.append(entry['target'])
        except OSError as exc:
            failed.append((entry['target'], str(exc)))
            if group:
                broken_groups.add(group)
    return changed, failed


def main():
    if os.geteuid() != 0:
        raise SystemExit(t('Bitte als root ausführen.'))
    changed, failed = sync()
    for target in changed:
        print(t('Systemdatei aktualisiert: ') + target)
    for target, reason in failed:
        print(t('Systemdatei nicht aktualisiert: ') + target + ' (' + reason + ')', file=sys.stderr)
    if not changed and not failed:
        print(t('Systemdateien sind aktuell.'))
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
