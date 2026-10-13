#!/usr/bin/python3
"""Keep working DNS during resolver installation and validate family DNS changes."""
from pathlib import Path as _Path
import sys as _sys
_release = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(_release / ('app' if (_release / 'app').is_dir() else 'src')))
from paimenos.i18n import t
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile


class DNSSetup:
    def __init__(self, root=Path('/'), run=subprocess.run):
        self.root, self.run = root, run
        self.resolv = root / 'etc/resolv.conf'
        self.dropins = root / 'etc/systemd/resolved.conf.d'
        self.bootstrap = self.dropins / 'paimenos-install-dns.conf'
        self.family = self.dropins / 'paimenos-family-dns.conf'
        self.state = root / 'var/lib/paimenos/install-dns.json'

    def command(self, *args):
        try:
            return self.run(list(args), check=False, capture_output=True, timeout=15).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def working(self):
        return self.command('getent', 'ahostsv4', 'deb.debian.org')

    def snapshot(self, path):
        if path.is_symlink():
            return {'link': os.readlink(path)}
        if path.exists():
            return {'text': path.read_text(), 'mode': path.stat().st_mode & 0o777}
        return {}

    def write(self, path, text, mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.paimenos-dns-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(text)
                os.fchmod(stream.fileno(), mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def restore(self, path, record):
        if 'link' in record:
            fd, temporary = tempfile.mkstemp(prefix='.paimenos-dns-', dir=path.parent)
            os.close(fd); os.unlink(temporary)
            try:
                os.symlink(record['link'], temporary)
                os.replace(temporary, path)
            finally:
                if os.path.lexists(temporary): os.unlink(temporary)
        elif 'text' in record:
            self.write(path, record['text'], record['mode'])
        else:
            path.unlink(missing_ok=True)

    def prepare(self):
        self.command('resolvectl', 'flush-caches')
        if not self.working():
            raise ValueError(t('DNS funktioniert bereits vor der Paketinstallation nicht. Erst die Netzwerk-/DNS-Verbindung reparieren.'))
        # APT's resolved postinst may replace resolv.conf before the next module.
        state = {'resolv': self.snapshot(self.resolv), 'bootstrap': self.snapshot(self.bootstrap)}
        self.write(self.state, json.dumps(state), 0o600)
        candidates = []
        if self.command('systemctl', 'is-active', '--quiet', 'systemd-resolved.service'):
            try:
                result = self.run(['resolvectl', 'dns'], check=False, capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    # Keep per-link upstreams too, including a temporary DNS repair.
                    candidates.extend(word for line in reversed(result.stdout.splitlines()) for word in line.split())
            except (OSError, subprocess.TimeoutExpired): pass
        servers = []
        for line in self.resolv.read_text().splitlines():
            words = line.split()
            if len(words) >= 2 and words[0] == 'nameserver':
                candidates.append(words[1])
        for candidate in candidates:
            try: address = ipaddress.ip_address(candidate)
            except ValueError: continue
            if not address.is_loopback and not address.is_unspecified:
                servers.append(str(address))
        if servers:
            self.write(self.bootstrap, '[Resolve]\nDNS=' + ' '.join(dict.fromkeys(servers)) + '\nDNSSEC=no\nDNSOverTLS=no\n')

    def recover(self):
        state = json.loads(self.state.read_text())
        if self.working():
            return
        self.command('systemctl', 'restart', 'systemd-resolved.service')
        self.command('resolvectl', 'flush-caches')
        if self.working():
            return
        self.restore(self.resolv, state['resolv'])
        if not self.working():
            raise ValueError(t('DNS nach Paketinstallation weiterhin defekt. Netzwerkverbindung prüfen; das vorherige resolv.conf wurde wiederhergestellt.'))
        print(t('Hinweis: DNS-Übernahme durch das Paket fehlgeschlagen; vorherige Namensauflösung wiederhergestellt.'))

    # Offline-Setup (PAIMENOS_OFFLINE=1): Ohne Netzwerk lässt sich DNS nicht prüfen. Die Konfiguration
    # wird nur geschrieben und aktiviert; die Verbindungsprüfung der Online-Wege entfällt.
    def prepare_offline(self):
        self.write(self.state, json.dumps({'resolv': self.snapshot(self.resolv)}), 0o600)

    def recover_offline(self):
        self.command('systemctl', 'restart', 'systemd-resolved.service')

    def family_offline(self, source):
        self.write(self.family, source.read_text())
        self.bootstrap.unlink(missing_ok=True)
        self.command('systemctl', 'enable', 'systemd-resolved.service')
        self.command('systemctl', 'restart', 'systemd-resolved.service')
        # Der Stub-Resolver entsteht beim Start des Dienstes; der Link gilt dann auch nach dem Neustart.
        self.restore(self.resolv, {'link': '/run/systemd/resolve/stub-resolv.conf'})

    def configure_family(self, source):
        if not self.working():
            raise ValueError(t('DNS ist vor der Familien-DNS-Einrichtung bereits defekt. Erst die Verbindung reparieren.'))
        old = [(path, self.snapshot(path)) for path in (self.resolv, self.family, self.bootstrap)]
        try:
            self.write(self.family, source.read_text())
            self.bootstrap.unlink(missing_ok=True)
            if not self.command('systemctl', 'enable', 'systemd-resolved.service') or not self.command('systemctl', 'restart', 'systemd-resolved.service'):
                raise ValueError(t('DNS-Dienst konnte nicht gestartet werden.'))
            stub = self.root / 'run/systemd/resolve/stub-resolv.conf'
            if not stub.is_file():
                raise ValueError(t('Lokale DNS-Resolver-Datei fehlt.'))
            self.restore(self.resolv, {'link': '/run/systemd/resolve/stub-resolv.conf'})
            if not self.command('resolvectl', 'flush-caches') or not self.working():
                raise ValueError(t('Namensauflösung mit der neuen DNS-Konfiguration fehlgeschlagen.'))
        except BaseException as exc:
            for path, record in old: self.restore(path, record)
            self.command('systemctl', 'restart', 'systemd-resolved.service')
            self.command('resolvectl', 'flush-caches')
            if not isinstance(exc, Exception):
                raise
            if not self.working():
                raise ValueError(t('DNS-Einrichtung fehlgeschlagen; vorherige Dateien wiederhergestellt, Verbindung weiterhin gestört: ') + str(exc)) from exc
            print(t('HINWEIS: Neue DNS-Konfiguration nicht nutzbar; funktionierende vorherige DNS-Konfiguration beibehalten. Familien-DNS wurde nicht aktiviert.'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'recover', 'family'])
    parser.add_argument('source', nargs='?', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0: parser.error(t('Bitte mit sudo ausführen.'))
    setup = DNSSetup()
    # live-build has no running resolver; preserve its build-time resolv.conf.
    if os.environ.get('PAIMENOS_IMAGE_BUILD') == '1':
        if args.action == 'prepare':
            setup.write(setup.state, json.dumps({'resolv': setup.snapshot(setup.resolv)}), 0o600)
        elif args.action == 'recover':
            setup.restore(setup.resolv, json.loads(setup.state.read_text())['resolv'])
        else:
            if args.source is None: parser.error(t('DNS-Konfigurationsdatei fehlt.'))
            setup.write(setup.family, args.source.read_text())
        return
    offline = os.environ.get('PAIMENOS_OFFLINE') == '1'
    if args.action == 'prepare': setup.prepare_offline() if offline else setup.prepare()
    elif args.action == 'recover': setup.recover_offline() if offline else setup.recover()
    else:
        if args.source is None: parser.error(t('DNS-Konfigurationsdatei fehlt.'))
        setup.family_offline(args.source) if offline else setup.configure_family(args.source)


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError) as exc:
        raise SystemExit(t('FEHLER: ') + str(exc))
