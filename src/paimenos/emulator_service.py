"""Fehlende Cores und Controller-Profile aus dem offiziellen Libretro-Buildbot."""
from paimenos.i18n import t
import glob
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tempfile
import urllib.request
import zipfile
import argparse
import copy
import fcntl
import grp
import pwd
import socket
import socketserver
import sys
import threading
import time
from contextlib import contextmanager

ROOT = Path('/usr/local/lib/libretro')
PROFILES = Path('/usr/local/share/paimenos/retroarch-autoconfig')
from paimenos.emulator_catalog import CATALOG, core_path, selection, status, RECOMMENDED, SHARED_SYSTEM, psp_assets_ready, dolphin_assets_ready
CORES = {item['core'] + '_libretro.so': item['name'] for item in CATALOG.values()}
ARCHES = {'amd64': ('x86_64', 2, 62), 'arm64': ('aarch64', 2, 183)}

def download(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'PaimenOS-Setup/59'})
    with urllib.request.urlopen(request, timeout=40) as response:
        if not response.url.startswith('https://buildbot.libretro.com/'):
            raise ValueError(t('Download wurde auf eine unerwartete Adresse umgeleitet.'))
        data = response.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError(t('Download überschreitet das Größenlimit.'))
    return data

def validate_elf(data, architecture):
    _, elf_class, machine = ARCHES[architecture]
    if len(data) < 64 or data[:4] != b'\x7fELF' or data[4] != elf_class or data[5] != 1:
        raise ValueError(t('Core ist keine passende Linux-Bibliothek.'))
    if struct.unpack_from('<H', data, 18)[0] != machine or struct.unpack_from('<H', data, 16)[0] != 3:
        raise ValueError(t('Core passt nicht zur CPU-Architektur.'))

def existing_core(name):
    paths = glob.glob('/usr/lib/*/libretro/' + name) + glob.glob('/usr/lib/libretro/' + name) + [str(ROOT / name)]
    return next((p for p in paths if os.path.isfile(p)), None)

def install_core(name, architecture):
    if existing_core(name):
        print('  ✓ ' + CORES[name] + t(': Core bereits vorhanden.'))
        return
    if architecture not in ARCHES:
        raise ValueError(t('Direktdownload unterstützt hier amd64/arm64; Debian-Paket für ') + architecture + t(' verwenden.'))
    url = 'https://buildbot.libretro.com/nightly/linux/' + ARCHES[architecture][0] + '/latest/' + name + '.zip'
    archive = download(url)
    with zipfile.ZipFile(io.BytesIO(archive)) as zip_file:
        members = zip_file.infolist()
        if len(members) != 1 or members[0].filename != name or not 0 < members[0].file_size <= 64 * 1024 * 1024:
            raise ValueError(t('Core-Archiv hat einen unerwarteten Inhalt.'))
        data = zip_file.read(members[0])
    validate_elf(data, architecture)
    ROOT.mkdir(parents=True, exist_ok=True)
    ROOT.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix='.paimenos-core-', dir=ROOT) as temp:
        os.chmod(temp, 0o755)
        candidate = Path(temp) / name; candidate.write_bytes(data); candidate.chmod(0o644)
        # Bibliothek als kids testen, bevor sie als funktionierender Core angeboten wird.
        check = subprocess.run(['/usr/sbin/runuser', '-u', 'kids', '--', '/usr/bin/python3', '-c',
                                'import ctypes,sys; c=ctypes.CDLL(sys.argv[1]); assert c.retro_api_version()==1',
                                str(candidate)], capture_output=True, text=True, timeout=10)
        if check.returncode:
            raise ValueError(t('Core konnte nicht geladen werden: ') + (check.stderr.strip().splitlines()[-1] if check.stderr.strip() else t('ABI-Prüfung fehlgeschlagen')))
        receipt = {'url': url, 'sha256': hashlib.sha256(data).hexdigest(), 'architecture': architecture}
        os.replace(candidate, ROOT / name)
        (ROOT / (name + '.json')).write_text(json.dumps(receipt, indent=2) + '\n')
    print('  ✓ ' + CORES[name] + t(': offizieller Libretro-Core ergänzt und Ladefähigkeit geprüft.'))

def install_profiles():
    if (PROFILES / '.paimenos-linux-profiles-v55').is_file() and any((PROFILES / 'udev').glob('*.cfg')):
        print(t('  ✓ Controller-Profile bereits eingerichtet.')); return
    archive = download('https://buildbot.libretro.com/assets/frontend/autoconfig.zip')
    PROFILES.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.paimenos-profiles-', dir=PROFILES.parent) as temp:
        stage = Path(temp); count = 0; skipped = 0
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            members = z.infolist()
            if len(members) > 10000 or sum(m.file_size for m in members) > 64 * 1024 * 1024:
                raise ValueError(t('Controller-Profilarchiv ist zu groß.'))
            for member in members:
                if member.is_dir():
                    continue
                path = PurePosixPath(member.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in member.filename or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(t('Ungültiger Pfad im Controller-Profilarchiv.'))
                parts = list(path.parts)
                if parts and parts[0] == 'autoconfig':
                    parts.pop(0)
                # Nur Profile für die hier verwendeten Linux-Treiber installieren.
                # sdl3/gamecontrollerdb.cfg ist eine große Datenbank, kein einzelnes udev-Profil.
                if len(parts) < 2 or parts[0] not in ('udev', 'sdl2') or not parts[-1].endswith('.cfg'):
                    continue
                if not 0 < member.file_size <= 128 * 1024:
                    skipped += 1
                    continue
                target = stage.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(member)); count += 1
        if count == 0 or not (stage / 'udev').is_dir():
            raise ValueError(t('Keine Linux-Controller-Profile gefunden.'))
        PROFILES.mkdir(parents=True, exist_ok=True)
        PROFILES.chmod(0o755)
        # Zusätzliche eigene Profile behalten; offizielle Profile ergänzen.
        shutil.copytree(stage, PROFILES, dirs_exist_ok=True)
        for folder in PROFILES.rglob('*'):
            folder.chmod(0o755 if folder.is_dir() else 0o644)
        (PROFILES / '.paimenos-linux-profiles-v55').write_text(str(count) + '\n')
    print('  ✓ ' + str(count) + t(' offizielle Linux-Controller-Profile ergänzt.'))
    if skipped:
        print(t('  Hinweis: ') + str(skipped) + t(' leere oder übergroße Einzelprofile übersprungen.'))


def install_psp_assets():
    if psp_assets_ready():
        return
    archive = download('https://buildbot.libretro.com/assets/system/PPSSPP.zip')
    SHARED_SYSTEM.mkdir(parents=True, exist_ok=True)
    SHARED_SYSTEM.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix='.ppsspp-', dir=SHARED_SYSTEM) as temp:
        stage = Path(temp) / 'PPSSPP'; stage.mkdir()
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            members = z.infolist()
            if len(members) > 10000 or sum(m.file_size for m in members) > 128 * 1024 * 1024:
                raise ValueError(t('PSP-Zusatzdateien überschreiten das Größenlimit.'))
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in member.filename or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(t('Ungültiger Pfad in den PSP-Zusatzdateien.'))
                parts = list(path.parts)
                if not parts or parts[0] != 'PPSSPP':
                    raise ValueError(t('Unerwarteter Inhalt der PSP-Zusatzdateien.'))
                target = stage.joinpath(*parts[1:])
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as source, target.open('xb') as dest:
                        shutil.copyfileobj(source, dest)
        if not (stage / 'ppge_atlas.zim').is_file() or not (stage / 'lang').is_dir() or not (stage / 'flash0/font').is_dir():
            raise ValueError(t('PSP-Zusatzdateien sind unvollständig.'))
        for path in stage.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        stage.chmod(0o755)
        target = SHARED_SYSTEM / 'PPSSPP'
        # Ein unvollständiger früherer Download wird ersetzt, Spiele liegen separat.
        backup = SHARED_SYSTEM / '.PPSSPP-previous'
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(stage, target)
        except Exception:
            if backup.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def install_dolphin_assets():
    if dolphin_assets_ready():
        return
    archive = download('https://buildbot.libretro.com/assets/system/Dolphin.zip')
    SHARED_SYSTEM.mkdir(parents=True, exist_ok=True)
    SHARED_SYSTEM.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix='.dolphin-', dir=SHARED_SYSTEM) as temp:
        stage = Path(temp) / 'dolphin-emu'; stage.mkdir()
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            members = z.infolist()
            if len(members) > 10000 or sum(m.file_size for m in members) > 128 * 1024 * 1024:
                raise ValueError(t('Dolphin-Zusatzdateien überschreiten das Größenlimit.'))
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in member.filename or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(t('Ungültiger Pfad in den Dolphin-Zusatzdateien.'))
                parts = list(path.parts)
                if not parts or parts[0] != 'dolphin-emu':
                    raise ValueError(t('Unerwarteter Inhalt der Dolphin-Zusatzdateien.'))
                target = stage.joinpath(*parts[1:])
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as source, target.open('xb') as dest:
                        shutil.copyfileobj(source, dest)
        if not (stage / 'Sys/GC/dsp_coef.bin').is_file() or not (stage / 'Sys/GC/dsp_rom.bin').is_file() or not any((stage / 'Sys/GameSettings').glob('*.ini')):
            raise ValueError(t('Dolphin-Zusatzdateien sind unvollständig.'))
        for path in stage.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        stage.chmod(0o755)
        target = SHARED_SYSTEM / 'dolphin-emu'
        # Ein unvollständiger früherer Download wird ersetzt, Spiele liegen separat.
        backup = SHARED_SYSTEM / '.dolphin-emu-previous'
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(stage, target)
        except Exception:
            if backup.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            shutil.rmtree(backup)


@contextmanager
def installation_lock():
    fd = os.open('/run/paimenos-emulator-install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(t('Eine Emulator-Installation läuft bereits. Bitte später erneut versuchen.')) from None
        yield
    finally:
        os.close(fd)


def command(args, timeout=1200):
    # Ausgabe landet auf dem Gerät; nur eine kurze Fehlermeldung geht zum Webbackend.
    with tempfile.TemporaryFile() as log:
        run = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             timeout=timeout, env=dict(os.environ, DEBIAN_FRONTEND='noninteractive', LC_ALL='C'))
        if run.returncode:
            log.seek(0, 2); log.seek(max(0, log.tell() - 4096))
            detail = log.read().decode('utf-8', errors='replace').strip()
            raise ValueError(t('Paketinstallation fehlgeschlagen: ') + detail[-1500:])


def package_available(name):
    run = subprocess.run(['/usr/bin/apt-cache', 'policy', name], capture_output=True, text=True, timeout=20)
    return any(line.strip().startswith('Candidate:') and line.split(':', 1)[1].strip() not in ('', '(none)') for line in run.stdout.splitlines())


def install_selected(keys, report=lambda message, **fields: print(message, flush=True)):
    # Nicht aus dem Client übernommene Paketnamen, URLs oder Befehle ausführen.
    keys = selection(','.join(keys) if keys else 'none')
    results = {}; notices = []
    if not keys:
        report(t('Keine zusätzlichen Emulatoren ausgewählt.'), percent=100)
        return results
    with installation_lock():
        architecture = subprocess.check_output(['/usr/bin/dpkg', '--print-architecture'], text=True, timeout=10).strip()
        missing = not shutil.which('retroarch') or any(not core_path(key) for key in keys)
        if missing:
            report(t('Paketlisten werden aktualisiert …'), percent=2)
            command(['/usr/bin/apt-get', '-o', 'DPkg::Lock::Timeout=120', 'update'], timeout=300)
            report(t('Emulator-Grundprogramm wird eingerichtet …'), percent=8)
            command(['/usr/bin/apt-get', '-o', 'DPkg::Lock::Timeout=120', 'install', '-y', '--no-install-recommends', 'retroarch', 'libretro-core-info'])
        for index, key in enumerate(keys):
            item = CATALOG[key]
            report(item['name'] + t(' wird eingerichtet …'), percent=10 + int(index / len(keys) * 80), current=key)
            try:
                if not core_path(key):
                    candidates = [name for name in item['packages'] if package_available(name)]
                    # Bei SNES zuerst Snes9x verwenden, bsnes nur als Paket-Fallback.
                    for name in candidates:
                        try:
                            command(['/usr/bin/apt-get', '-o', 'DPkg::Lock::Timeout=120', 'install', '-y', '--no-install-recommends', name])
                        except (ValueError, subprocess.TimeoutExpired) as exc:
                            report(str(exc))
                        if core_path(key):
                            break
                    if not core_path(key):
                        install_core(item['core'] + '_libretro.so', architecture)
                if key == 'psp':
                    report(t('PSP-Zusatzdateien werden eingerichtet …'))
                    install_psp_assets()
                if key == 'dolphin':
                    report(t('Dolphin-Zusatzdateien werden eingerichtet …'))
                    install_dolphin_assets()
                results[key] = {'ok': True, 'message': t('Installiert')}
                report(item['name'] + t(' ist bereit.'))
            except Exception as exc:
                results[key] = {'ok': False, 'message': str(exc)[:2000]}
                report(item['name'] + ': ' + str(exc)[:2000])
        report(t('Controller-Profile werden geprüft …'), percent=95, current='')
        try:
            install_profiles()
        except Exception as exc:
            notices.append(t('Controller-Profile: ') + str(exc)[:1500])
            report(notices[-1])
        report(t('Installation abgeschlossen.') if all(result['ok'] for result in results.values()) else t('Installation mit Fehlern beendet.'),
               percent=100, current='', warnings=notices)
    return results


def choose_setup(value=None, tty=None):
    if value is not None:
        return selection(value)
    existing = [key for key in CATALOG if core_path(key)]
    defaults = existing or list(RECOMMENDED)
    own_tty = tty is None
    if own_tty:
        try:
            tty = open('/dev/tty', 'r+')
        except OSError:
            print(t('Kein Terminal: vorhandene bzw. empfohlene Emulatoren ausgewählt. PAIMENOS_EMULATORS=none/all/nes,gb,... setzt die Auswahl.'), file=sys.stderr)
            return defaults
    try:
        tty.write(t('\nEmulatoren auswählen (vorhandene werden nie entfernt)\n'))
        for index, (key, item) in enumerate(CATALOG.items(), 1):
            tty.write(f" {index:2d}) {'[x]' if key in defaults else '[ ]'} {item['name']}" + (t(' – Leistung spielabhängig') if key in ('n64', 'psp', 'dolphin') else '') + '\n')
        while True:
            tty.write(t('Nummern durch Leerzeichen trennen; Enter = markierte Auswahl, alle / keine / empfohlen: ')); tty.flush()
            answer = tty.readline()
            if not answer:
                return defaults
            answer = answer.strip()
            if not answer:
                return defaults
            try:
                tokens = answer.replace(',', ' ').split()
                if all(token.isdigit() and 1 <= int(token) <= len(CATALOG) for token in tokens):
                    keys = list(CATALOG)
                    return list(dict.fromkeys(keys[int(token)-1] for token in tokens))
                return selection(answer)
            except ValueError as exc:
                tty.write(str(exc) + '\n')
    finally:
        if own_tty:
            tty.close()


SOCKET_PATH = '/run/paimenos-emulators/control.sock'
STATE_PATH = Path('/var/lib/paimenos/emulator-install.json')


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.job = {'state': 'idle', 'percent': 0, 'log': [], 'results': {}, 'systems': [], 'current': '', 'warnings': []}
        try:
            previous = json.loads(STATE_PATH.read_text())
            if isinstance(previous, dict) and previous.get('state') in ('done', 'error', 'running'):
                self.job = previous
                if previous['state'] == 'running':
                    self.job.update(state='error', message=t('Die Installation wurde durch einen Neustart unterbrochen. Bitte erneut installieren.'), current='')
        except (OSError, ValueError):
            pass

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.job)

    def save_locked(self):
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=STATE_PATH.parent, delete=False) as stream:
            json.dump(self.job, stream, ensure_ascii=False)
            path = Path(stream.name)
        path.chmod(0o600)
        os.replace(path, STATE_PATH)

    def report(self, message, **fields):
        with self.lock:
            self.job.update(fields, message=message)
            self.job['log'] = (self.job['log'] + [message])[-60:]
            self.save_locked()

    def start(self, keys):
        if not isinstance(keys, list) or not 1 <= len(keys) <= len(CATALOG) or any(not isinstance(key, str) or key not in CATALOG for key in keys):
            raise ValueError(t('Bitte gültige Emulatoren auswählen.'))
        keys = list(dict.fromkeys(keys))
        with self.lock:
            if self.job['state'] == 'running':
                raise ValueError(t('Eine Installation läuft bereits.'))
            self.job = {'state': 'running', 'percent': 0, 'log': [], 'results': {}, 'systems': keys, 'current': '', 'warnings': [], 'message': t('Installation wird gestartet …')}
            self.save_locked()
        threading.Thread(target=self.worker, args=(keys,), daemon=True).start()
        return self.snapshot()

    def worker(self, keys):
        try:
            results = install_selected(keys, self.report)
            with self.lock:
                self.job.update(results=results, state='done' if all(result['ok'] for result in results.values()) else 'error', current='')
                self.save_locked()
        except Exception as exc:
            self.report(str(exc)[:2000], state='error', current='')


def allowed_peer(connection, kids_uid):
    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
    return uid in (0, kids_uid)


def serve():
    from paimenos.flatpak_service import FlatpakJobs
    kids_uid = pwd.getpwnam('kids').pw_uid
    jobs = Jobs()
    flatpaks = FlatpakJobs(lock=installation_lock)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            try:
                if not allowed_peer(self.request, kids_uid):
                    raise ValueError(t('Zugriff verweigert.'))
                raw = self.rfile.readline(4097)
                if len(raw) > 4096 or not raw.endswith(b'\n'):
                    raise ValueError(t('Ungültige Anfrage.'))
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError(t('Ungültige Anfrage.'))
                if data.get('action') == 'status':
                    result = {'job': jobs.snapshot(), 'systems': status()}
                elif data.get('action') == 'install':
                    result = {'job': jobs.start(data.get('systems'))}
                elif data.get('action') == 'flatpak_status':
                    result = {'flatpak_job': flatpaks.snapshot()}
                elif data.get('action') in ('flatpak_install', 'flatpak_remove'):
                    result = {'flatpak_job': flatpaks.start(data['action'].split('_', 1)[1], data.get('app_id'),
                                                            data.get('delete_data') is True)}
                else:
                    raise ValueError(t('Unbekannte Aktion.'))
                response = {'ok': True, **result}
            except Exception as exc:
                response = {'ok': False, 'error': str(exc)[:2000]}
            try:
                self.wfile.write((json.dumps(response, ensure_ascii=False) + '\n').encode())
            except OSError:
                pass

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    Path(SOCKET_PATH).parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(Path(SOCKET_PATH).parent, 0, grp.getgrnam('kids').gr_gid)
    Path(SOCKET_PATH).parent.chmod(0o750)
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass
    # Während bind/chown darf kein anderer Benutzer auf den Socket zugreifen.
    old_umask = os.umask(0o077)
    try:
        server = Server(SOCKET_PATH, Handler)
        os.chown(SOCKET_PATH, 0, grp.getgrnam('kids').gr_gid)
        os.chmod(SOCKET_PATH, 0o660)
    finally:
        os.umask(old_umask)
    with server:
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--select', action='store_true')
    group.add_argument('--install')
    group.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    if args.select:
        print(','.join(choose_setup(os.environ.get('PAIMENOS_EMULATORS'))) or 'none')
    elif os.geteuid() != 0:
        parser.error(t('Installationen erfordern root.'))
    elif args.serve:
        serve()
    else:
        results = install_selected(selection(args.install))
        sys.exit(0 if all(result['ok'] for result in results.values()) else 1)


if __name__ == '__main__':
    main()

