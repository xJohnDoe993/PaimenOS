#!/usr/bin/python3
"""Install declared missing Debian dependencies; no scripts or repositories in metadata."""
from pathlib import Path as _Path
import sys as _sys
_release = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(_release / ('app' if (_release / 'app').is_dir() else 'src')))
from paimenos.i18n import t
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess

PACKAGE_NAME = re.compile(r'[a-z0-9][a-z0-9+.-]{1,127}')
PACKAGE_VERSION = re.compile(r'[0-9][A-Za-z0-9.+:~\-]*')
MAX_PACKAGES = 128


def validate_requirements(payload, components):
    if (not isinstance(payload, dict) or set(payload) != {'schema', 'components'}
            or type(payload['schema']) is not int or payload['schema'] != 1
            or not isinstance(payload['components'], dict)):
        raise ValueError(t('Ungültige Paketanforderungen.'))
    result = {}
    for name, packages in payload['components'].items():
        if name not in components or not isinstance(packages, list):
            raise ValueError(t('Unbekannte Komponente oder ungültige Paketliste: ') + str(name))
        if any(not isinstance(package, str) or not PACKAGE_NAME.fullmatch(package) for package in packages):
            raise ValueError(t('Nur Debian-Paketnamen sind als Update-Abhängigkeiten erlaubt.'))
        result[name] = sorted(set(packages))
    if sum(map(len, result.values())) > MAX_PACKAGES:
        raise ValueError(t('Zu viele Paketanforderungen.'))
    return result


def read_plan(path, components):
    if path.is_symlink():
        raise ValueError(t('Ungültige Paketanforderungsdatei.'))
    if not path.exists():
        return {}
    if path.stat().st_size > 65536:
        raise ValueError(t('Ungültige Paketanforderungsdatei.'))
    return validate_requirements(json.loads(path.read_text()), components)


def release_requirements(release):
    record = json.loads((release / 'installed.json').read_text())
    # Old deployers copy data files but do not yet compile component requirements.
    if 'package_requirements' not in record:
        return read_plan(release / 'data/update-packages.json', record['components'])
    return validate_requirements({'schema': 1, 'components': record['package_requirements']}, record['components'])


def installed_packages(packages, run):
    if not packages:
        return set()
    native = run(['/usr/bin/dpkg', '--print-architecture'], capture_output=True, text=True, check=True).stdout.strip()
    result = run(['/usr/bin/dpkg-query', '-W',
                  '-f=${db:Status-Status}\t${Package}\t${db:Status-Eflag}\t${Architecture}\n', *packages],
                 capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise ValueError(t('Installierte Debian-Pakete konnten nicht geprüft werden.'))
    return {parts[1] for line in result.stdout.splitlines()
            if len(parts := line.split('\t')) == 4 and parts[0] == 'installed'
            and parts[2] == 'ok' and parts[3] in (native, 'all')}


def candidates(packages, run, environment):
    result = run(['/usr/bin/apt-cache', 'policy', *packages],
                 capture_output=True, text=True, check=True, env=environment)
    versions, current = {}, None
    for line in result.stdout.splitlines():
        if line and not line[0].isspace():
            current = line.removesuffix(':') if line.endswith(':') else None
        elif current in packages and line.strip().startswith('Candidate:'):
            version = line.strip().removeprefix('Candidate:').strip()
            if PACKAGE_VERSION.fullmatch(version):
                versions[current] = version
    unavailable = sorted(set(packages) - versions.keys())
    if unavailable:
        raise ValueError(t('Benötigte Pakete fehlen in den konfigurierten Paketquellen: ') + ', '.join(unavailable))
    # Exact candidate versions prevent APT interpreting names as patterns or +/- actions.
    return [package + '=' + versions[package] for package in packages]


def ensure_release(release, progress=print, run=subprocess.run,
                   lock_path=Path('/run/paimenos-package-install.lock')):
    requirements = release_requirements(release)
    packages = sorted({package for values in requirements.values() for package in values})
    if not packages:
        return []
    with os.fdopen(os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'a') as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        missing = sorted(set(packages) - installed_packages(packages, run))
        if not missing:
            progress(t('Benötigte Debian-Pakete sind bereits installiert.'))
            return []
        # Leave PaimenOS restarts to the activation step; avoid a dependency/lock cycle
        # if a locally installed needrestart hook would restart our services inside APT.
        environment = dict(os.environ, DEBIAN_FRONTEND='noninteractive', LC_ALL='C', NEEDRESTART_MODE='l')
        options = ['-o', 'DPkg::Lock::Timeout=120', '-o', 'Acquire::Retries=2',
                   '-o', 'Acquire::http::Timeout=30', '-o', 'Acquire::https::Timeout=30']
        try:
            progress(t('Paketlisten werden aktualisiert.'))
            run(['/usr/bin/apt-get', *options, '-o', 'APT::Update::Error-Mode=any', 'update'],
                check=True, capture_output=True, text=True, env=environment)
            targets = candidates(missing, run, environment)
            progress(t('Fehlende Debian-Pakete werden installiert: ') + ', '.join(missing))
            run(['/usr/bin/apt-get', *options, '-o', 'Dpkg::Options::=--force-confdef',
                 '-o', 'Dpkg::Options::=--force-confold', 'install', '-y', '--no-remove',
                 '--no-install-recommends', *targets],
                check=True, capture_output=True, text=True, env=environment)
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or '').strip()[-2000:]
            raise ValueError(t('Paket-Nachinstallation fehlgeschlagen. APT/DNS und laufende Paketwartung prüfen. Bereits installierte Pakete bleiben erhalten. ') + details) from exc
        remaining = sorted(set(packages) - installed_packages(packages, run))
        if remaining:
            raise ValueError(t('Paket-Nachinstallation unvollständig: ') + ', '.join(remaining))
        progress(t('Benötigte Debian-Pakete sind installiert.'))
        return missing


def start_speech_install(release, settings=Path('/home/kids/.config/paimenos/settings.json'), run=subprocess.run):
    """Piper auf Bestandsgeräten nachholen: nur wenn die Sprachausgabe an ist und Piper fehlt (Updates liefern nur Debian-Pakete)."""
    try:
        # Als root direkt am Guard-Speicher; ohne Guard (ältere Installation) aus der Datei von kids.
        _sys.path.insert(0, str(release / ('app' if (release / 'app').is_dir() else 'src')))
        from paimenos import state
        if state.guard_installed():
            enabled = state.read_settings().get('speak_titles') is True
        else:
            enabled = json.loads(settings.read_text(encoding='utf-8')).get('speak_titles') is True
    except (OSError, ValueError, AttributeError, ImportError):
        return False
    script = release / 'tools/install-paimenos-speech.py'
    if not enabled or not script.is_file():
        return False
    if run(['/usr/bin/python3', '-I', str(script), '--check'], capture_output=True, check=False).returncode == 0:
        return False
    # Eigene Unit im Hintergrund: Der Download (rund 320 MB) darf Boot und Webbackend nicht aufhalten.
    run(['/usr/bin/systemd-run', '--quiet', '--no-block', '--unit=paimenos-speech-install', '--property=Nice=15',
         '/usr/bin/python3', '-I', str(script)], check=False)
    return True


def main():
    parser = argparse.ArgumentParser(description=t('Fehlende PaimenOS-Update-Abhängigkeiten installieren.'))
    parser.add_argument('--release', type=Path, default=Path('/usr/local/lib/paimenos/current'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error(t('Bitte mit sudo ausführen.'))
    ensure_release(args.release)
    start_speech_install(args.release)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(t('FEHLER: ') + str(exc))
