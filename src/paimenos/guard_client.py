"""Client für den PaimenOS-Guard (Unix-Socket, eine JSON-Zeile je Anfrage und Antwort)."""
from paimenos.i18n import t
from contextlib import contextmanager
import json
import socket
import threading

from paimenos.guard_store import GuardError

SOCKET = '/run/paimenos-guard/guard.sock'
TIMEOUT = 10
MAX_RESPONSE = 1024 * 1024
_local = threading.local()


class GuardUnavailable(ConnectionError):
    """Der Guard läuft nicht oder antwortet nicht. Aufrufer wiederholen später; nie auf die Kinder-Datei ausweichen."""


def current_token():
    return getattr(_local, 'token', None)


@contextmanager
def use_token(token):
    previous = current_token()
    _local.token = token
    try:
        yield
    finally:
        _local.token = previous


def call(op, _token=None, **args):
    request = json.dumps({'op': op, 'args': args, 'token': _token or current_token()}).encode() + b'\n'
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(TIMEOUT)
            connection.connect(SOCKET)
            connection.sendall(request)
            data = b''
            while not data.endswith(b'\n'):
                block = connection.recv(65536)
                if not block:
                    break
                data += block
                if len(data) > MAX_RESPONSE:
                    raise GuardUnavailable(t('Ungültige Antwort des Guards.'))
    except (OSError, socket.timeout) as exc:
        if isinstance(exc, GuardUnavailable):
            raise
        raise GuardUnavailable(t('Der PaimenOS-Guard ist nicht erreichbar.')) from exc
    try:
        response = json.loads(data)
    except ValueError:
        raise GuardUnavailable(t('Ungültige Antwort des Guards.')) from None
    if response.get('ok') is True:
        return response.get('result')
    raise GuardError(response.get('message') or t('Der PaimenOS-Guard hat die Anfrage abgelehnt.'),
                     response.get('code') or 'invalid', int(response.get('wait') or 0))


# Sitzungen der Eltern-Weboberfläche -------------------------------------------------------------------------------
def login(pin):
    return call('login', pin=pin)


def logout(token):
    try:
        call('logout', _token=token)
    except (GuardError, GuardUnavailable):
        pass


def whoami(token):
    """(gültig, Epoche). Eine Epoche steigt bei jeder PIN-Änderung."""
    result = call('whoami', _token=token)
    return bool(result.get('valid')), result.get('epoch')


def delegate(token):
    """Eingeschränkter Token für lange Aufträge (Backup-Wiederherstellung): nur Einstellungen setzen, mehrere Stunden gültig."""
    return call('delegate', _token=token)['token']
