"""PaimenOS-Guard: root-Dienst für Eltern-PIN und Einschränkungen.

Kinderprozesse sprechen über /run/paimenos-guard/guard.sock mit ihm (nur uid 0 und kids). Änderungen brauchen einen
Token aus `login` (richtige PIN); die Prüfung mit Sperrzeit nach Fehlversuchen steckt in guard_store.
Beim ersten Start übernimmt er die bisherige Einstellungsdatei von kids und entfernt deren PIN erst nach einer Wartezeit,
damit ein automatischer Rollback auf eine ältere Version weiter funktioniert (siehe docs/guard.md).
"""
from paimenos.i18n import t
import argparse
import getpass
import json
import os
import pwd
import secrets
import socketserver
import stat
import struct
import socket
import subprocess
import sys
import threading
import time

from paimenos import state
from paimenos.guard_store import GuardError, GuardStore, valid_pin

SOCKET_DIR = '/run/paimenos-guard'
SOCKET = SOCKET_DIR + '/guard.sock'
MAX_REQUEST = 256 * 1024
TOKEN_IDLE = 30 * 60
DELEGATE_TTL = 6 * 3600
MAX_TOKENS = 32
LEGACY_HOME = '/home/kids'
LEGACY_PATH = ('.config', 'paimenos')
LEGACY_NAME = 'settings.json'
LEGACY_LIMIT = 64 * 1024
STRIP_DELAY = 15 * 60
MAINTENANCE_SECONDS = 30


def log(message):
    print(message, file=sys.stderr, flush=True)


# --- Tokens (nur im Arbeitsspeicher) ---------------------------------------------------------------------------------
class Tokens:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock, self.items = clock, threading.Lock(), {}

    def issue(self, scope='full'):
        with self.lock:
            now = self.clock()
            self.items = {k: v for k, v in self.items.items() if v['expires'] > now}
            while len(self.items) >= MAX_TOKENS:
                del self.items[min(self.items, key=lambda k: self.items[k]['expires'])]
            token = secrets.token_hex(24)
            self.items[token] = {'scope': scope, 'expires': now + (TOKEN_IDLE if scope == 'full' else DELEGATE_TTL)}
            return token

    def touch(self, token, op=None):
        """Gibt den Geltungsbereich zurück (oder None). Volle Token verlängern sich bei jeder Nutzung."""
        with self.lock:
            entry = self.items.get(token) if isinstance(token, str) else None
            now = self.clock()
            if entry is None or entry['expires'] <= now:
                self.items.pop(token, None) if isinstance(token, str) else None
                return None
            if entry['scope'] == 'full':
                entry['expires'] = now + TOKEN_IDLE
            return entry['scope']

    def allows(self, token, op):
        scope = self.touch(token)
        return scope == 'full' or (scope == 'backup' and op == 'set_many')

    def revoke(self, token):
        with self.lock:
            self.items.pop(token, None)

    def revoke_others(self, keep):
        with self.lock:
            self.items = {k: v for k, v in self.items.items() if k == keep}


# --- Anfragen --------------------------------------------------------------------------------------------------------
def dispatch(store, tokens, request):
    if not isinstance(request, dict) or not isinstance(request.get('op'), str):
        raise GuardError(t('Ungültige Anfrage.'))
    op, args, token = request['op'], request.get('args', {}), request.get('token')
    if not isinstance(args, dict):
        raise GuardError(t('Ungültige Anfrage.'))
    if op == 'login':
        result = store.call('verify', args)
        return {'token': tokens.issue(), 'epoch': result['epoch']}
    if op == 'logout':
        tokens.revoke(token)
        return {}
    if op == 'whoami':
        return {'valid': tokens.touch(token) == 'full', 'epoch': store.call('epoch', {})['epoch']}
    if op == 'delegate':
        if tokens.touch(token) != 'full':
            raise GuardError(t('Anmeldung mit der Eltern-PIN erforderlich.'), 'auth')
        return {'token': tokens.issue('backup')}
    authenticated = tokens.allows(token, op)
    result = store.call(op, args, authenticated)
    if op == 'save_preferences' and args.get('new_pin') is not None:
        tokens.revoke_others(token)
    return result


def respond(store, tokens, raw):
    try:
        return {'ok': True, 'result': dispatch(store, tokens, json.loads(raw))}
    except GuardError as exc:
        return {'ok': False, 'code': exc.code, 'message': str(exc), 'wait': exc.wait}
    except (ValueError, KeyError, TypeError) as exc:
        return {'ok': False, 'code': 'invalid', 'message': str(exc)}
    except Exception as exc:  # noqa: BLE001 - nie mit Details aus dem Speicher antworten
        log('guard: ' + repr(exc))
        return {'ok': False, 'code': 'error', 'message': t('Interner Fehler im PaimenOS-Guard.')}


def allowed_peer(connection, kids_uid):
    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
    return uid in (0, kids_uid)


# --- Alte Einstellungsdatei von kids: nur über Verzeichnis-Deskriptoren, nie Verknüpfungen folgen ----------------------
def open_legacy_dir(home=LEGACY_HOME, owners=None):
    """Öffnet ~kids/.config/paimenos als Verzeichnis-Deskriptor oder gibt None zurück (fehlt oder unsicher)."""
    owners = owners if owners is not None else {0, pwd.getpwnam('kids').pw_uid}
    try:
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        for name in LEGACY_PATH:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if os.fstat(fd).st_uid not in owners:
            raise OSError('owner')
        return fd
    except OSError:
        os.close(fd)
        return None


def read_legacy(fd, owners=None):
    owners = owners if owners is not None else {0, pwd.getpwnam('kids').pw_uid}
    try:
        handle = os.open(LEGACY_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError:
        return None
    with os.fdopen(handle, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_size > LEGACY_LIMIT:
            return None
        data = json.loads(source.read(LEGACY_LIMIT + 1))
    return data if isinstance(data, dict) else None


def delete_legacy(fd):
    try:
        os.unlink(LEGACY_NAME, dir_fd=fd)
        return True
    except FileNotFoundError:
        return False


def write_legacy(fd, content, uid=None, gid=None):
    handle = os.open(LEGACY_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(handle, 'wb') as target:
        if uid is not None:
            os.fchown(target.fileno(), uid, gid)
        target.write(content)
        target.flush()
        os.fsync(target.fileno())


def import_legacy(store, fd, clock=time.time, owners=None):
    """Alte Einstellungen samt PIN in den Speicher übernehmen. Die Datei bleibt zunächst bestehen (Rollback)."""
    saved = read_legacy(fd, owners)
    if saved is None:
        raise ValueError(t('Keine Einstellungsdatei zum Übernehmen gefunden.'))
    pin = str(saved.get('pin', ''))
    if not valid_pin(pin):
        raise ValueError(t('Die gespeicherte Eltern-PIN ist ungültig.'))
    settings = {key: value for key, value in saved.items() if key != 'pin'}
    if store.exists():
        with store.transaction() as doc:
            from paimenos import guard_store
            data, _ = state.normalize(settings, with_pin=False)
            doc.update(pin=guard_store.hash_pin(pin), failures=0, locked_until=0, settings=data,
                       auth_epoch=doc['auth_epoch'] + 1)
            doc['legacy'] = {'strip_after': clock() + STRIP_DELAY}
    else:
        store.initialize(pin, settings)
        with store.transaction() as doc:
            doc['legacy'] = {'strip_after': clock() + STRIP_DELAY}


def maintain(store, clock=time.time, home=LEGACY_HOME, owners=None, marker=None, create_marker=True):
    """Übernahme, Marker und das späte Entfernen der alten PIN. Gibt zurück, ob etwas getan wurde."""
    marker = marker or state.GUARD_MARKER
    did = False
    fd = open_legacy_dir(home, owners)
    try:
        if not store.exists() and fd is not None and read_legacy(fd, owners) is not None:
            import_legacy(store, fd, clock, owners)
            did = True
        if not store.exists():
            return did
        with store.transaction() as doc:
            legacy = dict(doc.get('legacy') or {})
        if legacy.get('reimport') and fd is not None:
            import_legacy(store, fd, clock, owners)
            did = True
            with store.transaction() as doc:
                doc['legacy'].pop('reimport', None)
            legacy = {'strip_after': clock() + STRIP_DELAY}
        if create_marker and not legacy.get('reimport') and not os.path.exists(marker):
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, 'w', encoding='utf-8') as handle:
                handle.write('1\n')
            os.chmod(marker, 0o644)
            did = True
        if fd is not None and legacy.get('strip_after') and clock() >= legacy['strip_after']:
            delete_legacy(fd)
            with store.transaction() as doc:
                doc['legacy'] = {'stripped': True}
            did = True
    finally:
        if fd is not None:
            os.close(fd)
    return did


def export_legacy(store, new_pin, home=LEGACY_HOME, marker=None):
    """Für das manuelle Zurückrollen auf eine Version ohne Guard: Einstellungsdatei mit neuer PIN schreiben."""
    marker = marker or state.GUARD_MARKER
    fd = open_legacy_dir(home)
    if fd is None:
        raise ValueError(t('Das Einstellungsverzeichnis von kids fehlt oder ist unsicher.'))
    try:
        if not valid_pin(new_pin):
            raise ValueError(t('Eltern-PIN: 4 bis 12 Ziffern erforderlich.'))
        with store.transaction() as doc:
            content = dict(doc['settings'], pin=new_pin)
            doc['legacy'] = {'reimport': True}
        delete_legacy(fd)
        info = pwd.getpwnam('kids')
        write_legacy(fd, (json.dumps(content, ensure_ascii=False, indent=2) + '\n').encode(), info.pw_uid, info.pw_gid)
    finally:
        os.close(fd)
    if os.path.exists(marker):
        os.unlink(marker)


# --- Dienst ----------------------------------------------------------------------------------------------------------
def build_server(store, tokens, path, kids_uid, kids_gid=None):
    class Handler(socketserver.StreamRequestHandler):
        timeout = 5

        def handle(self):
            try:
                if not allowed_peer(self.request, kids_uid):
                    return
                raw = self.rfile.readline(MAX_REQUEST + 1)
                if len(raw) > MAX_REQUEST or not raw.endswith(b'\n'):
                    response = {'ok': False, 'code': 'invalid', 'message': t('Ungültige Anfrage.')}
                else:
                    response = respond(store, tokens, raw)
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode() + b'\n')
            except (OSError, socket.timeout):
                pass

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.unlink(path)
    server = Server(path, Handler)
    if kids_gid is not None:
        os.chown(path, 0, kids_gid)
        os.chmod(path, 0o660)
    return server


def serve():
    store, tokens = GuardStore(), Tokens()
    info = pwd.getpwnam('kids')
    reported = []

    def maintenance():
        while True:
            try:
                maintain(store)
            except Exception as exc:  # noqa: BLE001 - der Dienst muss weiterlaufen
                if repr(exc) not in reported:
                    reported.append(repr(exc))
                    log(t('Guard-Wartung fehlgeschlagen: ') + str(exc))
            time.sleep(MAINTENANCE_SECONDS)

    # Erst den Speicher übernehmen, dann lauschen, dann (in maintain) den Marker setzen: kein Fenster ohne Dienst.
    try:
        maintain(store, create_marker=False)
    except Exception as exc:  # noqa: BLE001
        log(t('Guard-Übernahme fehlgeschlagen: ') + str(exc))
    server = build_server(store, tokens, SOCKET, info.pw_uid, info.pw_gid)
    threading.Thread(target=maintenance, daemon=True).start()
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=t('PaimenOS-Guard: Eltern-PIN und Einschränkungen.'))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--serve', action='store_true')
    group.add_argument('--set-pin', action='store_true', help=t('Eltern-PIN neu setzen (Konsole des Administrators)'))
    group.add_argument('--init', action='store_true', help=argparse.SUPPRESS)
    group.add_argument('--export-legacy', action='store_true',
                       help=t('Vor dem Zurückrollen auf eine Version ohne Guard: Einstellungsdatei mit neuer PIN schreiben'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error(t('Bitte mit sudo ausführen.'))
    if args.serve:
        serve()
        return
    pin = os.environ.get('PAIMENOS_PARENT_PIN') or getpass.getpass(t('Neue Eltern-PIN (4 bis 12 Ziffern): '))
    if not valid_pin(pin):
        raise SystemExit(t('Eltern-PIN: 4 bis 12 Ziffern erforderlich.'))
    store = GuardStore()
    if args.init:
        store.initialize(pin)
        os.makedirs(os.path.dirname(state.GUARD_MARKER), exist_ok=True)
        with open(state.GUARD_MARKER, 'w', encoding='utf-8') as handle:
            handle.write('1\n')
        os.chmod(state.GUARD_MARKER, 0o644)
    elif args.set_pin:
        if not store.exists():
            raise SystemExit(t('Der PaimenOS-Guard ist nicht eingerichtet.'))
        store.reset_pin(pin)
        print(t('Eltern-PIN gesetzt. Angemeldete Web-Sitzungen sind beendet.'))
    else:
        # Der Dienst darf die Datei nicht sofort wieder einlesen; beim nächsten Start übernimmt er sie.
        subprocess.run(['systemctl', 'stop', 'paimenos-guard.service'], check=False)
        export_legacy(store, pin)
        print(t('Einstellungsdatei für die ältere Version geschrieben. Danach das Zurückrollen ausführen.'))


if __name__ == '__main__':
    main()
