"""Drucken mit Elternfreigabe; CUPS-Zugriff nur im lokalen root-Dienst."""
from paimenos.i18n import t
import base64
import grp
import gzip
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import struct
import subprocess
import threading
import time
import unicodedata
import urllib.parse

SOCKET_PATH = '/run/paimenos-print/api.sock'
SETTINGS_PATH = Path('/var/lib/paimenos/printing/settings.json')
SPOOL_DIR = Path('/var/spool/cups')
KIDS_USER = 'kids'
# off: Kinder können nicht drucken; approval: Aufträge warten auf die Eltern; direct: ohne Freigabe.
MODES = ('off', 'approval', 'direct')
MAX_REQUEST = 4096
MAX_DOCUMENT = 24 * 1024 * 1024
MAX_MESSAGE = MAX_DOCUMENT * 4 // 3 + 64 * 1024
HISTORY = 30
URI_SCHEMES = ('ipp', 'ipps', 'dnssd', 'socket', 'lpd', 'usb', 'http', 'https')
JOB_STATES = {3: 'pending', 4: 'held', 5: 'printing', 6: 'stopped', 7: 'canceled', 8: 'aborted', 9: 'completed'}
JOB_ATTRIBUTES = ['job-id', 'job-name', 'job-originating-user-name', 'job-state', 'job-printer-uri', 'time-at-creation',
                  'time-at-completed', 'job-k-octets', 'job-impressions', 'job-impressions-completed', 'document-format',
                  'job-state-reasons', 'job-printer-state-message']
# Diese Formate zeigt der Browser selbst an; alles andere wird heruntergeladen.
INLINE_FORMATS = {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg'}
DOWNLOAD_FORMATS = {'application/postscript': '.ps', 'image/pwg-raster': '.pwg', 'image/urf': '.urf', 'text/plain': '.txt'}


class PrintError(Exception):
    def __init__(self, message, unavailable=False):
        super().__init__(message)
        self.unavailable = unavailable


def read_config(path=SETTINGS_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    mode = data.get('mode') if data.get('mode') in MODES else 'off'
    released = [value for value in data.get('released', []) if type(value) is int and value > 0][-500:]
    return dict(mode=mode, released=released)


def print_mode(path=SETTINGS_PATH):
    """Für Firefox-Profile des Kindes: Drucken nur anbieten, wenn die Eltern es erlauben."""
    return read_config(path)['mode']


def write_config(config, path=SETTINGS_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.tmp')
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(dict(mode=config['mode'], released=sorted(config['released'])), handle)
        handle.flush()
        os.fsync(handle.fileno())
    # Firefox-Profile des Kindes lesen den Modus; Schreiben darf nur der Dienst.
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def print_request(action='status', **values):
    payload = json.dumps(dict(values, action=action), ensure_ascii=False).encode() + b'\n'
    if len(payload) > MAX_REQUEST:
        raise PrintError(t('Druckanfrage ist zu groß.'))
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(100)
            connection.connect(SOCKET_PATH)
            connection.sendall(payload)
            with connection.makefile('rb') as stream:
                raw = stream.readline(MAX_MESSAGE + 1)
        if not raw.endswith(b'\n') or len(raw) > MAX_MESSAGE:
            raise ValueError('response')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('response')
    except (OSError, ValueError) as exc:
        raise PrintError(t('Druckverwaltung nicht erreichbar. Bitte das aktualisierte Setup installieren.'), True) from exc
    if not result.get('ok'):
        raise PrintError(result.get('error') or t('Druckvorgang fehlgeschlagen.'))
    if not isinstance(result.get('state'), dict):
        raise PrintError(t('Ungültige Antwort der Druckverwaltung.'), True)
    return result['state']


def print_document(job):
    """Dokument eines Auftrags: (Bytes, MIME-Typ, Dateiname, im Browser anzeigen)."""
    state = print_request('document', job=job)
    try:
        data = base64.b64decode(state['data'], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise PrintError(t('Ungültige Antwort der Druckverwaltung.'), True) from exc
    mime = state.get('format') if state.get('format') in INLINE_FORMATS or state.get('format') in DOWNLOAD_FORMATS else 'application/octet-stream'
    extension = INLINE_FORMATS.get(mime) or DOWNLOAD_FORMATS.get(mime) or '.bin'
    name = re.sub(r'[^A-Za-z0-9._-]+', '-', unicodedata.normalize('NFKD', str(state.get('title', ''))).encode('ascii', 'ignore').decode()).strip('-.')[:60]
    return data, mime, (name or 'Druckauftrag-' + str(job)) + extension, mime in INLINE_FORMATS


def queue_name(title, existing=()):
    """CUPS-Warteschlangenname aus dem angezeigten Namen: nur ASCII-Buchstaben, Ziffern, - und _."""
    ascii_title = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode()
    base = re.sub(r'[^A-Za-z0-9_-]+', '-', ascii_title).strip('-_')[:48] or 'Drucker'
    taken = {name.casefold() for name in existing}
    name, number = base, 2
    while name.casefold() in taken:
        name, number = base + '-' + str(number), number + 1
    return name


def validate_title(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80 or re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError(t('Bitte einen Druckernamen mit höchstens 80 Zeichen eingeben.'))
    return value.strip()


def validate_uri(value):
    """Nur Druckeradressen, nie Dateien oder Programme (file:, cups-pdf:, Pipes)."""
    if not isinstance(value, str):
        raise ValueError(t('Ungültige Druckeradresse.'))
    value = value.strip()
    if '://' not in value and re.fullmatch(r'[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\]', value):
        # Nur Hostname oder IP-Adresse eingegeben: IPP Everywhere (driverless) ist heute Standard.
        value = 'ipp://' + value + '/ipp/print'
    parsed = urllib.parse.urlsplit(value)
    if (len(value) > 1024 or parsed.scheme not in URI_SCHEMES or re.search(r'[\s\x00-\x1f\x7f\'"\\`]', value)
            or (parsed.scheme not in ('usb', 'dnssd') and not parsed.hostname)):
        raise ValueError(t('Ungültige Druckeradresse. Beispiel: ipp://192.168.1.20/ipp/print'))
    return value


def job_id(value):
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int or not 0 < value < 2 ** 31:
        raise ValueError(t('Ungültiger Druckauftrag.'))
    return value


class PrintController:
    """Hält die CUPS-Warteschlangen im gewählten Modus und verwaltet Aufträge.

    Jede Warteschlange bekommt im Freigabe-Modus `job-hold-until-default=indefinite`: CUPS hält neue Aufträge
    selbst an, bevor sie gedruckt werden. `enforce()` läuft zusätzlich alle zwei Sekunden und erfasst auch
    Warteschlangen, die CUPS selbst angelegt hat (automatisch gefundene Netzwerkdrucker).
    """
    def __init__(self, driver, settings_path=SETTINGS_PATH):
        self.driver, self.settings_path = driver, settings_path
        self.lock = threading.RLock()
        self.config = read_config(settings_path)
        self.discovery = dict(running=False, devices=[], error='')
        self.message, self.error, self.adding = '', '', False

    def save(self):
        write_config(self.config, self.settings_path)

    def enforce(self):
        with self.lock:
            mode = self.config['mode']
            for name, printer in self.driver.printers().items():
                if printer['accepting'] != (mode != 'off'):
                    self.driver.accept(name, mode != 'off')
                hold = 'indefinite' if mode == 'approval' else 'no-hold'
                if printer['hold_default'] != hold:
                    self.driver.set_hold_default(name, hold)
                if printer['shared']:
                    self.driver.unshare(name)
            jobs = self.driver.jobs()
            released = [job for job in self.config['released'] if job in jobs]
            if mode == 'approval':
                for number, job in jobs.items():
                    # Auftrag des Kindes, der an der Vorgabe vorbei sofort drucken würde.
                    if job['state'] == 3 and job['user'] == KIDS_USER and number not in released:
                        self.driver.hold(number)
            if released != self.config['released']:
                self.config['released'] = released
                self.save()

    def snapshot(self):
        with self.lock:
            try:
                printers = self.driver.printers()
                default = self.driver.default()
                jobs = self.driver.jobs()
                history = self.driver.jobs(completed=True)
                available = True
            except Exception as exc:
                printers, default, jobs, history, available = {}, '', {}, {}, False
                self.error = friendly_error(exc)
            listed = [dict(name=name, title=p['info'] or name, model=p['model'], uri=display_uri(p['uri']),
                           state=p['state'], status=p['message'], default=name == default,
                           accepting=p['accepting']) for name, p in printers.items()]
            listed.sort(key=lambda p: (not p['default'], p['title'].casefold()))
            def entry(number, job):
                return dict(id=number, title=job['title'], printer=job['printer'],
                            printer_title=printers.get(job['printer'], {}).get('info') or job['printer'],
                            user=job['user'], state=JOB_STATES.get(job['state'], 'pending'), created=job['created'],
                            completed=job['completed'], size_kb=job['size_kb'], pages=job['pages'], reason=job['reason'],
                            document=self.driver.has_document(number))
            active = sorted((entry(n, j) for n, j in jobs.items()), key=lambda j: -j['id'])
            done = sorted((entry(n, j) for n, j in history.items()), key=lambda j: -j['id'])[:HISTORY]
            state = dict(available=available, mode=self.config['mode'], printers=listed, jobs=active, history=done,
                         discovery=dict(self.discovery), adding=self.adding, message=self.message, error=self.error)
            self.message, self.error = '', ''
            return state

    def handle(self, data):
        if not isinstance(data, dict) or not isinstance(data.get('action'), str):
            raise ValueError(t('Ungültige Druckanfrage.'))
        action = data['action']
        if action == 'status':
            return self.snapshot()
        if action == 'document':
            number = job_id(data.get('job'))
            raw, mime = self.driver.document(number)
            job = self.driver.jobs().get(number) or self.driver.jobs(completed=True).get(number) or {}
            return dict(data=base64.b64encode(raw).decode(), format=mime, title=job.get('title', ''))
        with self.lock:
            if action == 'mode':
                if data.get('mode') not in MODES:
                    raise ValueError(t('Ungültige Druckeinstellung.'))
                self.config['mode'] = data['mode']
                self.save()
                self.enforce()
                self.message = {'off': t('Drucken für das Kind ist ausgeschaltet.'),
                                'approval': t('Druckaufträge des Kindes warten jetzt auf Ihre Freigabe.'),
                                'direct': t('Das Kind kann jetzt ohne Freigabe drucken.')}[data['mode']]
            elif action in ('release', 'cancel'):
                number = job_id(data.get('job'))
                if number not in self.driver.jobs():
                    raise ValueError(t('Der Druckauftrag ist nicht mehr vorhanden.'))
                if action == 'release':
                    self.config['released'] = sorted(set(self.config['released']) | {number})
                    self.save()
                    self.driver.release(number)
                    self.message = t('Druckauftrag freigegeben.')
                else:
                    self.driver.cancel(number)
                    self.message = t('Druckauftrag verworfen.')
            elif action == 'add':
                return self.add(data)
            elif action in ('remove', 'default', 'test'):
                name = data.get('printer')
                if not isinstance(name, str) or name not in self.driver.printers():
                    raise ValueError(t('Der Drucker ist nicht mehr vorhanden.'))
                if action == 'remove':
                    self.driver.remove(name)
                    self.message = t('Drucker entfernt.')
                elif action == 'default':
                    self.driver.set_default(name)
                    self.message = t('Standarddrucker gespeichert.')
                else:
                    if self.config['mode'] == 'off':
                        raise ValueError(t('Für eine Testseite bitte zuerst Drucken erlauben.'))
                    self.driver.test_page(name)
                    self.message = t('Testseite wird gedruckt.')
            elif action == 'discover':
                if not self.discovery['running']:
                    self.discovery = dict(running=True, devices=self.discovery['devices'], error='')
                    threading.Thread(target=self.discover, daemon=True).start()
            else:
                raise ValueError(t('Unbekannte Druckaktion.'))
        return self.snapshot()

    def add(self, data):
        title, uri = validate_title(data.get('title')), validate_uri(data.get('uri'))
        with self.lock:
            if self.adding:
                raise ValueError(t('Ein Drucker wird bereits eingerichtet.'))
            name = queue_name(title, self.driver.printers())
            device = next((d for d in self.discovery['devices'] if d['uri'] == uri), None)
            self.adding = True
        try:
            # lpadmin fragt den Drucker selbst ab und kann dauern: Status und Freigaben laufen weiter.
            self.driver.add(name, title, uri, device)
        finally:
            with self.lock:
                self.adding = False
        with self.lock:
            if not self.driver.default():
                self.driver.set_default(name)
            self.enforce()
            for item in self.discovery['devices']:
                item['added'] = item['added'] or item['uri'] == uri
            self.message = t('Drucker „{value0}“ hinzugefügt.', value0=title)
        return self.snapshot()

    def discover(self):
        try:
            devices, error = self.driver.discover(), ''
        except Exception as exc:
            devices, error = [], friendly_error(exc)
        with self.lock:
            known = {p['uri'] for p in self.driver.printers().values()} if not error else set()
            for device in devices:
                device['added'] = device['uri'] in known
            self.discovery = dict(running=False, devices=devices, error=error)
            if not error and not devices:
                self.discovery['error'] = t('Kein Drucker gefunden. Drucker einschalten, mit demselben WLAN bzw. per USB verbinden und erneut suchen.')


def display_uri(uri):
    # Zugangsdaten in Adressen (ipp://user:pass@host) nie anzeigen.
    parsed = urllib.parse.urlsplit(uri)
    if parsed.password or parsed.username:
        return parsed._replace(netloc=parsed.netloc.rsplit('@', 1)[-1]).geturl()
    return uri


def friendly_error(error):
    if isinstance(error, (PrintError, ValueError)):
        return str(error)
    if isinstance(error, subprocess.TimeoutExpired):
        return t('Der Drucker antwortet nicht. Drucker einschalten und Netzwerk prüfen.')
    if isinstance(error, FileNotFoundError):
        return t('Das Druckdokument ist nicht mehr gespeichert.')
    text = str(error)
    if 'not accepting' in text.lower():
        return t('Der Drucker nimmt keine Aufträge an.')
    if 'connection' in text.lower() or 'connect' in text.lower():
        return t('Der Druckdienst (CUPS) ist nicht erreichbar.')
    return t('Druckvorgang fehlgeschlagen: ') + text[:200]


class CupsDriver:
    """pycups für Abfragen, lpadmin zum Einrichten (IPP Everywhere erzeugt den Treiber selbst)."""
    def __init__(self):
        import cups
        self.cups = cups

    def connection(self):
        # pycups-Verbindungen sind nicht threadsicher: jede Anfrage bekommt eine eigene.
        return self.cups.Connection()

    def printers(self):
        connection = self.connection()
        result = {}
        for name, attributes in connection.getPrinters().items():
            extra = connection.getPrinterAttributes(name, requested_attributes=['job-hold-until-default', 'printer-is-accepting-jobs'])
            result[name] = dict(info=str(attributes.get('printer-info', '')), model=str(attributes.get('printer-make-and-model', '')),
                                uri=str(attributes.get('device-uri', '')), state=int(attributes.get('printer-state', 3)),
                                message=str(attributes.get('printer-state-message', ''))[:200],
                                accepting=bool(extra.get('printer-is-accepting-jobs', True)),
                                shared=bool(attributes.get('printer-is-shared', False)),
                                hold_default=str(extra.get('job-hold-until-default', 'no-hold')))
        return result

    def default(self):
        return self.connection().getDefault() or ''

    def jobs(self, completed=False):
        connection = self.connection()
        # Titel und Benutzer eines Auftrags (JobPrivateValues) liefert CUPS nur angemeldeten Verbindungen.
        # Eine Geräteabfrage mit unbekanntem Schema löst die lokale root-Anmeldung aus und kostet kaum Zeit.
        connection.getDevices(include_schemes=['paimenos-auth'], timeout=1)
        raw = connection.getJobs(which_jobs='completed' if completed else 'not-completed', my_jobs=False,
                                 limit=HISTORY if completed else -1, requested_attributes=JOB_ATTRIBUTES)
        result = {}
        for number, attributes in raw.items():
            reasons = attributes.get('job-state-reasons', [])
            reasons = [reasons] if isinstance(reasons, str) else list(reasons)
            result[int(number)] = dict(
                title=str(attributes.get('job-name', ''))[:200], printer=str(attributes.get('job-printer-uri', '')).rsplit('/', 1)[-1],
                user=str(attributes.get('job-originating-user-name', '')), state=int(attributes.get('job-state', 3)),
                created=int(attributes.get('time-at-creation', 0) or 0), completed=int(attributes.get('time-at-completed', 0) or 0),
                size_kb=int(attributes.get('job-k-octets', 0) or 0),
                pages=int(attributes.get('job-impressions-completed', 0) or attributes.get('job-impressions', 0) or 0),
                reason=str(attributes.get('job-printer-state-message', ''))[:200] if 'job-printing' not in reasons else '')
        return result

    def accept(self, name, enabled):
        connection = self.connection()
        if enabled:
            connection.acceptJobs(name)
            connection.enablePrinter(name)
        else:
            connection.rejectJobs(name, reason='PaimenOS: Drucken im Elternbereich ausgeschaltet')

    def set_hold_default(self, name, value):
        self.connection().addPrinterOptionDefault(name, 'job-hold-until', value)

    def unshare(self, name):
        self.connection().setPrinterShared(name, False)

    def hold(self, number):
        self.connection().setJobHoldUntil(number, 'indefinite')

    def release(self, number):
        self.connection().setJobHoldUntil(number, 'no-hold')

    def cancel(self, number):
        self.connection().cancelJob(number)

    def files(self, number):
        return sorted(SPOOL_DIR.glob('d%05d-[0-9][0-9][0-9]' % number))

    def has_document(self, number):
        return bool(self.files(number))

    def document(self, number):
        files = self.files(number)
        if not files:
            raise FileNotFoundError(number)
        with open(files[0], 'rb') as handle:
            raw = handle.read(MAX_DOCUMENT + 1)
        if raw[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=__import__('io').BytesIO(raw)) as stream:
                raw = stream.read(MAX_DOCUMENT + 1)
        if len(raw) > MAX_DOCUMENT:
            raise PrintError(t('Das Dokument ist zu groß für die Vorschau.'))
        return raw, sniff_format(raw)

    def add(self, name, title, uri, device=None):
        model = 'everywhere' if urllib.parse.urlsplit(uri).scheme in ('ipp', 'ipps', 'dnssd', 'http', 'https') else self.ppd_for(device)
        result = subprocess.run(['/usr/sbin/lpadmin', '-p', name, '-D', title, '-v', uri, '-m', model, '-E',
                                 '-o', 'printer-is-shared=false', '-o', 'printer-error-policy=retry-job'],
                                capture_output=True, text=True, timeout=90)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ['']
            raise PrintError(t('Drucker konnte nicht eingerichtet werden. Ist er eingeschaltet und erreichbar? ') + detail[0][:200])

    def ppd_for(self, device):
        ppds = {}
        if device and (device['device_id'] or device['model']):
            values = {'ppd_device_id': device['device_id']} if device['device_id'] else {'ppd_make_and_model': device['model']}
            ppds = self.connection().getPPDs(limit=1, **values)
        if not ppds:
            raise PrintError(t('Für diesen Drucker wurde kein Treiber gefunden. Viele USB-Drucker funktionieren mit dem Paket ipp-usb oder einem Treiberpaket des Herstellers.'))
        return next(iter(ppds))

    def remove(self, name):
        self.connection().deletePrinter(name)

    def set_default(self, name):
        self.connection().setDefault(name)

    def test_page(self, name):
        self.connection().printTestPage(name)

    def discover(self):
        found = self.connection().getDevices(timeout=12, exclude_schemes=['file', 'cups-pdf', 'serial', 'parallel', 'beh', 'hp', 'hpfax', 'snmp'])
        devices = []
        for uri, attributes in found.items():
            if '://' not in uri or urllib.parse.urlsplit(uri).scheme not in URI_SCHEMES:
                continue
            devices.append(dict(uri=uri, display=display_uri(uri), info=str(attributes.get('device-info', ''))[:120] or uri,
                                model=str(attributes.get('device-make-and-model', ''))[:120],
                                device_id=str(attributes.get('device-id', ''))[:512],
                                connection='usb' if uri.startswith('usb:') else 'network'))
        # dnssd-Einträge zuerst: Sie bleiben auch bei wechselnder IP-Adresse gültig.
        devices.sort(key=lambda d: (not d['uri'].startswith('dnssd:'), d['info'].casefold()))
        unique, seen = [], set()
        for device in devices:
            if device['info'] not in seen:
                unique.append(device)
                seen.add(device['info'])
        return unique[:40]


def sniff_format(raw):
    if raw.startswith(b'%PDF'):
        return 'application/pdf'
    if raw.startswith(b'%!'):
        return 'application/postscript'
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if raw.startswith(b'RaS2'):
        return 'image/pwg-raster'
    if raw.startswith(b'UNIRAST'):
        return 'image/urf'
    return 'application/octet-stream'


def disable_cups_web_interface():
    """Die CUPS-Weboberfläche (localhost:631) wäre im Kinder-Browser erreichbar; Verwaltung läuft über PaimenOS."""
    try:
        current = subprocess.run(['/usr/sbin/cupsctl'], capture_output=True, text=True, timeout=20).stdout
        if re.search(r'^WebInterface=yes$', current, re.M | re.I):
            subprocess.run(['/usr/sbin/cupsctl', 'WebInterface=no'], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def run_daemon():
    disable_cups_web_interface()
    controller = PrintController(CupsDriver())

    def enforce_loop():
        while True:
            try:
                controller.enforce()
            except Exception as exc:
                # CUPS startet evtl. gerade neu; nächster Durchlauf versucht es erneut.
                print(t('Druckverwaltung: ') + friendly_error(exc), flush=True)
                time.sleep(8)
            time.sleep(2)
    threading.Thread(target=enforce_loop, daemon=True).start()

    kids_uid = __import__('pwd').getpwnam(KIDS_USER).pw_uid
    slots = threading.BoundedSemaphore(8)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                _, uid, _ = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid not in (0, kids_uid):
                    return
                self.request.settimeout(100)
                raw = self.rfile.readline(MAX_REQUEST + 1)
                if not raw.endswith(b'\n') or len(raw) > MAX_REQUEST:
                    return
                try:
                    result = dict(ok=True, state=controller.handle(json.loads(raw)))
                except Exception as exc:
                    result = dict(ok=False, error=friendly_error(exc))
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b'\n')
            except (OSError, ValueError):
                return

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
        request_queue_size = 8
        def process_request(self, request, address):
            if not slots.acquire(blocking=False):
                self.shutdown_request(request); return
            try:
                super().process_request(request, address)
            except Exception:
                slots.release(); raise
        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                slots.release()

    os.umask(0o077)
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    server = Server(SOCKET_PATH, Handler)
    os.chown(SOCKET_PATH, 0, grp.getgrnam(KIDS_USER).gr_gid)
    os.chmod(SOCKET_PATH, 0o660)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    run_daemon()
