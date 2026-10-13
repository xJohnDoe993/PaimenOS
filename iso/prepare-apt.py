#!/usr/bin/python3
"""Prepare online sources and a temporary APT configuration for ISO setup."""
from pathlib import Path as _Path
import sys as _sys
_release = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(_release / ('app' if (_release / 'app').is_dir() else 'src')))
from paimenos.i18n import t
import argparse
import importlib.util
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess

spec = importlib.util.spec_from_file_location('components', Path(__file__).resolve().parents[1] / 'tools/configure-debian-components.py')
components = importlib.util.module_from_spec(spec)
spec.loader.exec_module(components)


def can_connect(family, port=443):
    """Check Debian's TCP endpoint independently for IPv4 and IPv6."""
    try:
        addresses = socket.getaddrinfo('deb.debian.org', port, family, socket.SOCK_STREAM)
    except OSError:
        return False
    # Bound connection attempts even if DNS returns many unreachable addresses.
    for address in addresses[:2]:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as connection:
                connection.settimeout(3)
                connection.connect(address[4])
            return True
        except OSError:
            continue
    return False


def session_config(path, probe=can_connect, previous_config=None):
    """Use IPv4 only when it works and IPv6 does not; leave system APT untouched."""
    content = Path(previous_config).read_text() + '\n' if previous_config else ''
    content += ('APT::Update::Error-Mode "any";\n'
                'Acquire::Retries "1";\n'
                'Acquire::http::Timeout "15";\n'
                'Acquire::https::Timeout "15";\n')
    ipv4, ipv6 = probe(socket.AF_INET), probe(socket.AF_INET6)
    if ipv4 and not ipv6:
        content += 'Acquire::ForceIPv4 "true";\nAcquire::ForceIPv6 "false";\n'
        print(t('IPv6-Verbindung zum Debian-Server nicht nutzbar; das Setup lädt Pakete über IPv4.'))
    Path(path).write_text(content)
    # Inherited APT_CONFIG may contain proxy credentials. The caller uses mktemp.
    Path(path).chmod(0o600)


OFFLINE_STATE = Path('/run/paimenos-offline')


def offline_config(path, repo, state=OFFLINE_STATE, codename='trixie'):
    """APT-Sitzung nur mit der signierten Offline-Quelle des Installationsmediums (ohne Netzwerk)."""
    repo, state = Path(repo), Path(state)
    parts = state / 'sources.list.d'
    parts.mkdir(parents=True, exist_ok=True)
    (state / 'lists/partial').mkdir(parents=True, exist_ok=True)
    (parts / 'paimenos-offline.sources').write_text(
        f'Types: deb\nURIs: file:{repo}\nSuites: {codename}\nComponents: main\n'
        f'Signed-By: {repo}/paimenos-repo.gpg\n')
    # Eigene Paketlisten, damit die Online-Listen des Systems unberührt bleiben.
    Path(path).write_text(f'Dir::Etc::sourcelist "/dev/null";\nDir::Etc::sourceparts "{parts}";\n'
                          f'Dir::State::lists "{state}/lists";\nAPT::Update::Error-Mode "any";\n')
    Path(path).chmod(0o600)


def diagnose(run=subprocess.run, probe=can_connect):
    """Read-only routing and TCP diagnostics; no service or DNS changes."""
    for command in (['ip', '-4', '-brief', 'address'], ['ip', '-4', 'route'],
                    ['ip', '-6', 'route'], ['getent', 'ahostsv4', 'deb.debian.org']):
        print('$ ' + ' '.join(command), flush=True)
        try:
            result = run(command, capture_output=True, text=True, timeout=10, check=False)
            print(result.stdout.strip() or result.stderr.strip() or '-', flush=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(str(exc), flush=True)
    for port in (80, 443):
        for family, name in ((socket.AF_INET, 'IPv4'), (socket.AF_INET6, 'IPv6')):
            status = t('erreichbar') if probe(family, port) else t('nicht erreichbar')
            print(f'deb.debian.org TCP/{port} {name}: {status}', flush=True)


def configure(root=Path('/')):
    root = Path(root)
    folder = root / 'etc/apt/sources.list.d'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'paimenos-iso.sources'
    files = [root / 'etc/apt/sources.list', *sorted(folder.glob('*.list')), *sorted(folder.glob('*.sources'))]
    for path in files:
        if not path.is_file() or path == target:
            continue
        old = path.read_text()
        if path.suffix == '.sources':
            stanzas = re.split(r'\n[ \t]*\n', old)
            for index, stanza in enumerate(stanzas):
                if re.search(r'^URIs:\s*cdrom:', stanza, re.M | re.I):
                    stanza = re.sub(r'^Enabled:.*\n?', '', stanza, flags=re.M | re.I)
                    stanzas[index] = stanza.rstrip() + '\nEnabled: no\n'
            new = '\n\n'.join(stanzas)
        else:
            new = re.sub(r'^(\s*deb(?:-src)?\s+(?:\[[^\]]*\]\s+)?cdrom:.*)$', r'# PaimenOS: Installationsmedium deaktiviert\n# \1', old, flags=re.M)
        if old != new:
            backup = path.with_name(path.name + '.before-paimenos-iso')
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(new)
    suites = ('trixie', 'trixie-updates', 'trixie-security')
    existing = {suite: set() for suite in suites}
    signing = {}
    for path in files:
        if not path.is_file() or path == target:
            continue
        for suite, areas, uri, key in components.entries(path.read_text(), path.suffix, with_signing=True):
            if suite in existing:
                existing[suite].update(areas)
            if uri in signing and signing[uri] != key:
                raise ValueError(t('Widersprüchliche Signed-By-Angaben: ') + uri)
            signing[uri] = key
    blocks = []
    for suite in suites:
        missing = [area for area in ('main', 'contrib', 'non-free', 'non-free-firmware') if area not in existing[suite]]
        if not missing:
            continue
        uri = 'https://deb.debian.org/' + ('debian-security' if suite.endswith('-security') else 'debian')
        key = signing.get(uri, '/usr/share/keyrings/debian-archive-keyring.gpg')
        block = f'Types: deb\nURIs: {uri}\nSuites: {suite}\nComponents: {" ".join(missing)}\n'
        if key:
            block += 'Signed-By: ' + key + '\n'
        blocks.append(block)
    target.write_text('# PaimenOS ISO: Debian-Onlinequellen\n\n' + '\n'.join(blocks))
    target.chmod(0o644)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apt-config', type=Path)
    parser.add_argument('--offline-repo', type=Path, help=t('Offline-Paketquelle des Installationsmediums statt Internet verwenden'))
    parser.add_argument('--diagnose', action='store_true')
    args = parser.parse_args()
    if args.diagnose:
        diagnose()
        raise SystemExit(0)
    if os.geteuid() != 0:
        raise SystemExit(t('Bitte mit sudo starten.'))
    configure()
    if args.apt_config and args.offline_repo:
        offline_config(args.apt_config, args.offline_repo)
    elif args.apt_config:
        session_config(args.apt_config, previous_config=os.environ.get('APT_CONFIG'))
