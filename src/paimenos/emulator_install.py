"""Kurze Anfragen an den lokalen Emulator-Installationsdienst."""
from paimenos.i18n import t
import json
import socket

SOCKET_PATH = '/run/paimenos-emulators/control.sock'


class InstallError(ValueError):
    pass


def install_request(action='status', systems=None, **fields):
    request = {'action': action, **fields}
    if systems is not None:
        request['systems'] = systems
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(SOCKET_PATH)
            connection.sendall((json.dumps(request) + '\n').encode())
            data = bytearray()
            while not data.endswith(b'\n'):
                block = connection.recv(4096)
                if not block:
                    raise InstallError(t('Der Installationsdienst hat die Verbindung beendet.'))
                data.extend(block)
                if len(data) > 256 * 1024:
                    raise InstallError(t('Ungültige Antwort des Installationsdienstes.'))
        result = json.loads(data)
        if not isinstance(result, dict) or not result.get('ok'):
            raise InstallError(result.get('error', t('Installation konnte nicht gestartet werden.')) if isinstance(result, dict) else t('Ungültige Antwort.'))
        return result
    except (OSError, ValueError) as exc:
        if isinstance(exc, InstallError):
            raise
        raise InstallError(t('Installationsdienst nicht erreichbar. Setup v59 erneut ausführen oder Dienststatus prüfen.')) from exc

