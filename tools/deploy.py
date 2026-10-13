#!/usr/bin/python3
"""Stage local trusted source releases and atomically switch application code.

Mutable user data and system configuration are never part of code deployment.
Systemd units are applied only when the services component is selected.
Declared missing Debian dependencies are installed before live activation.
"""
from pathlib import Path as _Path
import sys as _sys
_release = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(_release / ('app' if (_release / 'app').is_dir() else 'src')))
from paimenos.i18n import t
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/usr/local/lib/paimenos')
API = 3

def package_tool():
    # This helper belongs to the trusted installed/local deployer, not the download.
    spec = importlib.util.spec_from_file_location('paimenos_update_packages', ROOT / 'tools/install-update-packages.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def ensure_packages(release, progress=print):
    return package_tool().ensure_release(release, progress)

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))

def destination(relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError(t('Ungültiger Manifest-Pfad: ') + relative)
    allowed = {'src', 'assets', 'data', 'tools', 'bin', 'sbin', 'systemd'}
    if path.parts[0] not in allowed and relative != 'run.py':
        raise ValueError(t('Nicht verwalteter Pfad: ') + relative)
    if path.parts[0] == 'src':
        if len(path.parts) < 3 or path.parts[1] != 'paimenos':
            raise ValueError(t('Ungültiges Python-Paket.'))
        return Path('app', *path.parts[1:])
    return Path(relative)

def validate_source(source):
    manifest = read_json(source / 'manifest.json')
    if manifest.get('schema') != 1 or manifest.get('runtime_api') != API:
        raise ValueError(t('Nicht unterstütztes Manifest/API.'))
    if manifest['version'] != (source / 'VERSION').read_text().strip():
        raise ValueError(t('VERSION und Manifest stimmen nicht überein.'))
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?', manifest['version']):
        raise ValueError(t('Ungültige Version.'))
    components = manifest['components']
    if not isinstance(components, dict) or not components:
        raise ValueError(t('Komponenten fehlen.'))
    package_tool().read_plan(source / 'data/update-packages.json', components)
    seen = set()
    for name, definition in components.items():
        if not re.fullmatch(r'[a-z][a-z0-9-]*', name):
            raise ValueError(t('Ungültiger Komponentenname.'))
        for relative in definition['files']:
            destination(relative)
            path = source / relative
            if relative in seen:
                raise ValueError(t('Doppelter Manifest-Pfad: ') + relative)
            seen.add(relative)
            if not path.is_file() or path.is_symlink() or source.resolve() not in path.resolve().parents:
                raise ValueError(t('Datei fehlt oder liegt außerhalb des Projekts: ') + relative)
            if path.suffix == '.py':
                compile(path.read_bytes(), str(path), 'exec')
            if relative.startswith(('bin/', 'sbin/')):
                subprocess.run(['bash', '-n', str(path)], check=True)
    source_modules = {str(p.relative_to(source)) for p in (source / 'src/paimenos').glob('*.py')}
    if not source_modules <= seen:
        raise ValueError(t('Python-Datei fehlt im Manifest; build-manifest.py ausführen.'))
    return manifest

def validate_stage(stage):
    for path in stage.rglob('*.py'):
        compile(path.read_bytes(), str(path), 'exec')
    # No optional GUI/system dependencies are imported in this smoke check.
    script = ('import sys; sys.dont_write_bytecode=True; sys.path.insert(0, sys.argv[1]); '
              'import paimenos.state, paimenos.emulator_catalog, paimenos.categories; '
              'assert paimenos.emulator_catalog.selection("none") == []')
    subprocess.run([sys.executable, '-I', '-c', script, str(stage / 'app')], check=True)

def stage_release(source, base, selected=None):
    manifest = validate_source(source)
    current = base / 'current'
    old = current.resolve() if current.is_symlink() else None
    names = list(manifest['components']) if selected is None else list(dict.fromkeys(selected))
    if not names or any(name not in manifest['components'] for name in names):
        raise ValueError(t('Unbekannte/leere Komponenten-Auswahl.'))
    if selected is not None and old is None:
        raise ValueError(t('Für Komponenten-Updates fehlt die Erstinstallation.'))
    installed = read_json(old / 'installed.json') if old else {'runtime_api': API, 'components': {}}
    incoming_requirements = package_tool().read_plan(source / 'data/update-packages.json', manifest['components'])
    requirements = package_tool().release_requirements(old) if old else {}
    if selected is None:
        requirements = incoming_requirements
    else:
        for name in names:
            requirements[name] = incoming_requirements.get(name, [])
    if installed['runtime_api'] != manifest['runtime_api'] and selected is not None:
        raise ValueError(t('Die Paket-API hat sich geändert; vollständiges Update erforderlich.'))
    releases = base / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=releases))
    try:
        if old:
            shutil.copytree(old, stage, dirs_exist_ok=True)
        records = installed['components'].copy()
        if selected is None:
            for name in set(records) - set(manifest['components']):
                for relative in records.pop(name)['files']:
                    (stage / destination(relative)).unlink(missing_ok=True)
        for name in names:
            previous_files = records.get(name, {}).get('files', {})
            for relative in previous_files:
                path = stage / destination(relative)
                if path.is_file():
                    path.unlink()
            receipts = {}
            for relative in manifest['components'][name]['files']:
                src = source / relative
                target = stage / destination(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
                target.chmod(0o755 if relative.startswith(('bin/', 'sbin/')) else 0o644)
                receipts[relative] = hashlib.sha256(src.read_bytes()).hexdigest()
            records[name] = {'version': manifest['version'], 'files': receipts}
        record = {'schema': 1, 'runtime_api': manifest['runtime_api'], 'source_version': manifest['version'], 'components': records}
        record['package_requirements'] = requirements
        (stage / 'installed.json').write_text(json.dumps(record, indent=2) + '\n')
        validate_stage(stage)
        digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:12]
        final = releases / (manifest['version'] + '-' + digest)
        if final.exists():
            shutil.rmtree(stage)
        else:
            # mkdtemp starts at 0700; the child user needs read/execute access.
            for path in stage.rglob('*'):
                if path.is_dir():
                    path.chmod(0o755)
            stage.chmod(0o755)
            stage.rename(final)
        return final, names
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

def switch(base, release, link='current'):
    temp = base / ('.' + link + '-new')
    with contextlib.suppress(FileNotFoundError):
        temp.unlink()
    temp.symlink_to(release.relative_to(base))
    os.replace(temp, base / link)

def install_launchers(release, previous=None):
    launchers = []
    for folder in ('bin', 'sbin'):
        target_dir = Path('/usr/local') / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        for file in (release / folder).glob('*'):
            target = target_dir / file.name
            expected = BASE / 'current' / folder / file.name
            if (target.exists() or target.is_symlink()) and (not target.is_symlink() or target.readlink() != expected):
                raise ValueError(t('Startprogramm existiert bereits: ') + str(target))
            launchers.append((target, expected))
    for target, expected in launchers:
        if not target.is_symlink():
            target.symlink_to(expected)
    if previous:
        for folder in ('bin', 'sbin'):
            for file in (previous / folder).glob('*'):
                if not (release / folder / file.name).exists():
                    target = Path('/usr/local') / folder / file.name
                    expected = BASE / 'current' / folder / file.name
                    if target.is_symlink() and target.readlink() == expected:
                        target.unlink()

OPTIONAL_SYSTEM_UNITS = ('paimenos-updates.service', 'paimenos-power.service')
USER_UNITS = ('paimenos-menu.service', 'paimenos-timer.service', 'paimenos-media.service',
              'paimenos-osd.service', 'paimenos-cursor.service', 'paimenos-idle.service')

def apply_units(release, previous=None, unit_root=Path('/etc/systemd')):
    for scope in ('system', 'user'):
        destination_dir = unit_root / scope
        destination_dir.mkdir(parents=True, exist_ok=True)
        if previous:
            for file in (previous / 'systemd' / scope).glob('*'):
                if not (release / 'systemd' / scope / file.name).is_file():
                    command = ['systemctl', 'disable', '--now', file.name] if scope == 'system' else user_command('disable', '--now', file.name)
                    if command:
                        subprocess.run(command, check=True)
                    target = destination_dir / file.name
                    if target.is_file():
                        target.unlink()
        for file in (release / 'systemd' / scope).glob('*'):
            shutil.copyfile(file, destination_dir / file.name)
            (destination_dir / file.name).chmod(0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    # Später hinzugekommene Systemdienste auch bei Backend-Updates aktivieren.
    for unit in OPTIONAL_SYSTEM_UNITS:
        if (release / 'systemd/system' / unit).is_file():
            subprocess.run(['systemctl', 'enable', unit], check=True)

def user_command(*args):
    try:
        uid = pwd.getpwnam('kids').pw_uid
    except KeyError:
        return None
    runtime = Path('/run/user') / str(uid)
    if not (runtime / 'bus').exists():
        return None
    return ['runuser', '-u', 'kids', '--', 'env', 'XDG_RUNTIME_DIR=' + str(runtime),
            'DBUS_SESSION_BUS_ADDRESS=unix:path=' + str(runtime / 'bus'), 'systemctl', '--user', *args]

def restart_services():
    units = ['paimenos-wifi.service', 'paimenos-bluetooth.service', 'paimenos-emulators.service', 'paimenos-parent-web.service']
    units += [unit for unit in OPTIONAL_SYSTEM_UNITS if (BASE / 'current/systemd/system' / unit).is_file()]
    subprocess.run(['systemctl', 'restart', *units], check=True)
    command = user_command('daemon-reload')
    if command:
        subprocess.run(command, check=True)
        subprocess.run(user_command('restart', 'paimenos-session.target'), check=True)
    # Waitress/BlueZ start asynchronously: allow time, then detect crash loops.
    time.sleep(2)
    for unit in units:
        subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=True)
    if command:
        for unit in USER_UNITS:
            if not (BASE / 'current/systemd/user' / unit).is_file():
                continue
            subprocess.run(user_command('is-active', '--quiet', unit), check=True)

def retire_squid(run=subprocess.run):
    """Der frühere lokale Squid-Proxy wird nicht mehr genutzt: Dienst auf bestehenden Geräten stoppen.

    Best effort, ein Fehler hier bricht kein Update ab. Das Paket bleibt installiert (apt autoremove/purge
    entfernt es später); die Firefox-Einstellungen bereinigt prepare_profile beim nächsten Start.
    """
    if not any(Path(folder, 'squid.service').is_file() for folder in ('/usr/lib/systemd/system', '/lib/systemd/system', '/etc/systemd/system')):
        return
    run(['systemctl', 'disable', '--now', 'squid.service'], check=False, capture_output=True)

def activate(base, release, *, initial=False, units=False, live=True, package_progress=print, packages_prepared=False):
    current = base / 'current'
    old = current.resolve() if current.is_symlink() else None
    if live and not initial and not packages_prepared:
        ensure_packages(release, package_progress)
    switch(base, release)
    try:
        if live:
            install_launchers(release, previous=old)
            if not initial:
                retire_squid()
                if units:
                    apply_units(release, previous=old)
                restart_services()
    except BaseException:
        if old:
            switch(base, old)
            if live and not initial:
                install_launchers(old, previous=release)
                if units:
                    apply_units(old, previous=release)
                restart_services()
        else:
            current.unlink()
        raise
    if old and old != release:
        switch(base, old, 'previous')

def main():
    parser = argparse.ArgumentParser(description=t('PaimenOS-Code aus diesem lokalen Projekt aktualisieren. Eltern-Daten bleiben erhalten.'))
    parser.add_argument('--component', action='append', help=t('z.B. controller; mehrfach verwendbar'))
    parser.add_argument('--check', action='store_true', help=t('Quellen prüfen, nichts installieren'))
    parser.add_argument('--list', action='store_true', help=t('Komponenten anzeigen'))
    parser.add_argument('--rollback', action='store_true', help=t('Vorherigen Code aktivieren'))
    parser.add_argument('--initial', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.rollback and (args.component or args.initial):
        parser.error(t('--rollback darf nicht mit --component/--initial kombiniert werden.'))
    if args.check or args.list:
        manifest = validate_source(ROOT)
        if args.component and any(name not in manifest['components'] for name in args.component):
            parser.error(t('Unbekannte Komponente.'))
        for name, definition in manifest['components'].items():
            print(t('{value0}: {value1} Dateien', value0=name, value1=len(definition['files'])))
        print(t('Quellen gültig. Keine Änderungen vorgenommen.'))
        return
    if os.geteuid() != 0:
        parser.error(t('Bitte mit sudo ausführen.'))
    if not args.initial and not (BASE / 'current').is_symlink():
        parser.error(t('Modulare Erstinstallation fehlt. Zuerst install.sh auf einem frischen System ausführen.'))
    lock_path = '/run/paimenos-maintenance.lock'
    with open(lock_path, 'a') as lock:
        if not args.initial:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.error(t('Eine andere PaimenOS-Wartung läuft bereits.'))
        # Do not interrupt a privileged backend apt/dpkg transaction.
        with open('/run/paimenos-emulator-install.lock', 'a') as emulator_lock:
            try:
                fcntl.flock(emulator_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.error(t('Eine Emulator-Installation läuft. Bitte deren Abschluss abwarten.'))
            if args.rollback:
                previous = BASE / 'previous'
                if not previous.is_symlink():
                    parser.error(t('Keine vorherige Code-Version vorhanden.'))
                activate(BASE, previous.resolve(), units=True)
                print(t('Vorherige Code-Version aktiviert.'))
                return
            release, names = stage_release(ROOT, BASE, args.component)
            activate(BASE, release, initial=args.initial, units='services' in names)
        print(t('Code aktiviert: ') + release.name + ' · ' + ', '.join(names))

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as exc:
        print(t('PaimenOS-Update fehlgeschlagen: ') + str(exc), file=sys.stderr)
        raise SystemExit(1)
