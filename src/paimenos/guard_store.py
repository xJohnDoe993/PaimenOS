"""Root-eigener Speicher für Eltern-PIN und Einschränkungen (Tageslimit, Nutzungszeitraum, gesperrte Apps).

Die Datei liegt in /var/lib/paimenos/guard (0700, root). Kinderprozesse erreichen sie nur über den Guard-Dienst
(guard.py) mit benannten, geprüften Operationen. Die PIN wird als scrypt-Hash gespeichert; vor Ratespielen schützt
die Sperrzeit nach Fehlversuchen, die auch einen Neustart des Dienstes übersteht.
"""
from paimenos.i18n import t
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from datetime import date

from paimenos import state

STORE_NAME = 'state.json'
SCHEMA = 1
FREE_ATTEMPTS = 5
MAX_LOCK_SECONDS = 900
SCRYPT = {'n': 2 ** 14, 'r': 8, 'p': 1}
PIN_PATTERN = re.compile(r'[0-9]{4,12}')
COLOR_PATTERN = re.compile(r'#[0-9A-Fa-f]{6}')
CATEGORIES_VOICES = ('female', 'male')
MAX_IDS = 1000
# Schlüssel, die ein Backup setzen darf (nie PIN, Bonuszeit oder Verbrauch).
BACKUP_KEYS = ('bg_color', 'daily_limit_minutes', 'category_tabs', 'speak_titles', 'speak_voice',
               'allowed_from', 'allowed_until', 'disabled_apps')


class GuardError(ValueError):
    """Abgelehnte Operation. code: auth, locked, pin, invalid."""
    def __init__(self, message, code='invalid', wait=0):
        super().__init__(message)
        self.code, self.wait = code, wait


class Denied:
    """Rückgabewert eines Handlers für einen falschen PIN; wird erst nach dem Speichern der Zähler zum Fehler."""
    def __init__(self, error):
        self.error = error


def hash_pin(pin, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **SCRYPT)
    return {'algo': 'scrypt', 'salt': salt.hex(), 'hash': digest.hex(), **SCRYPT}


def pin_matches(record, pin):
    try:
        salt = bytes.fromhex(record['salt'])
        digest = hashlib.scrypt(pin.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024,
                                n=record['n'], r=record['r'], p=record['p'])
        return hmac.compare_digest(digest.hex(), record['hash'])
    except (KeyError, ValueError, TypeError):
        return False


def valid_pin(pin):
    return isinstance(pin, str) and PIN_PATTERN.fullmatch(pin) is not None


def _ids(value):
    if (not isinstance(value, list) or len(value) > MAX_IDS
            or any(not isinstance(i, str) or not 0 < len(i) <= 64 or any(ord(c) < 32 for c in i) for i in value)):
        raise GuardError(t('Ungültige App-Liste.'))
    return value


def _minutes(value, low, high=600):
    if type(value) is not int or not low <= value <= high:
        raise GuardError(t('Bitte ganze Minuten zwischen {value0} und {value1} eingeben.', value0=low, value1=high))
    return value


class GuardStore:
    def __init__(self, directory=None, clock=time.time):
        self.directory = directory or state.GUARD_DIR
        self.path = os.path.join(self.directory, STORE_NAME)
        self.clock = clock

    # --- Datei ---------------------------------------------------------------------------------------------------
    def exists(self):
        return os.path.isfile(self.path)

    def _write(self, doc):
        fd, tmp = tempfile.mkstemp(prefix='.tmp-', dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(doc, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @contextmanager
    def transaction(self):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        with open(os.path.join(self.directory, STORE_NAME + '.lock'), 'a', encoding='utf-8') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with open(self.path, encoding='utf-8') as handle:
                    doc = json.load(handle)
            except FileNotFoundError as exc:
                raise ValueError(t('Der PaimenOS-Guard ist nicht eingerichtet.')) from exc
            if not isinstance(doc, dict) or doc.get('schema') != SCHEMA or not isinstance(doc.get('settings'), dict):
                raise ValueError(t('Der Guard-Speicher ist beschädigt.'))
            before = json.dumps(doc, sort_keys=True)
            doc['settings'], _ = state.normalize(doc['settings'], with_pin=False)
            yield doc
            if json.dumps(doc, sort_keys=True) != before:
                self._write(doc)

    # --- Einrichtung -----------------------------------------------------------------------------------------------
    def initialize(self, pin, settings=None):
        if not valid_pin(pin):
            raise ValueError(t('Eltern-PIN: 4 bis 12 Ziffern erforderlich.'))
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        if self.exists():
            raise ValueError(t('Der PaimenOS-Guard ist bereits eingerichtet.'))
        values = {key: value for key, value in state.DEFAULTS.items() if key != 'pin'}
        values.update({key: value for key, value in (settings or {}).items() if key != 'pin'})
        data, _ = state.normalize(values, with_pin=False)
        self._write({'schema': SCHEMA, 'pin': hash_pin(pin), 'auth_epoch': 1, 'failures': 0, 'locked_until': 0,
                     'settings': data, 'legacy': {}})

    def reset_pin(self, pin):
        """Für die Administrator-Konsole: neue PIN setzen, Sperre aufheben, alle Web-Sitzungen entwerten."""
        if not valid_pin(pin):
            raise ValueError(t('Eltern-PIN: 4 bis 12 Ziffern erforderlich.'))
        with self.transaction() as doc:
            doc.update(pin=hash_pin(pin), failures=0, locked_until=0, auth_epoch=doc['auth_epoch'] + 1)

    def apply(self, change):
        """Direkter Zugriff für root-Werkzeuge (Installer, Flatpak-Aufräumen); keine PIN nötig."""
        with self.transaction() as doc:
            data = dict(doc['settings'])
            if callable(change):
                change(data)
            else:
                data.update(change)
            doc['settings'], _ = state.normalize(data, with_pin=False)
            return copy.deepcopy(doc['settings'])

    # --- PIN-Prüfung mit Sperrzeit ------------------------------------------------------------------------------------
    def _check_pin(self, doc, pin):
        now = self.clock()
        if doc['locked_until'] > now:
            return Denied(GuardError(t('Zu viele Versuche. Bitte später erneut versuchen.'), 'locked',
                                     int(doc['locked_until'] - now) + 1))
        if isinstance(pin, str) and valid_pin(pin) and pin_matches(doc['pin'], pin):
            doc.update(failures=0, locked_until=0)
            return None
        doc['failures'] += 1
        wait = 0
        if doc['failures'] >= FREE_ATTEMPTS:
            wait = min(MAX_LOCK_SECONDS, 30 * 2 ** (doc['failures'] - FREE_ATTEMPTS))
            doc['locked_until'] = now + wait
        return Denied(GuardError(t('Die Eltern-PIN stimmt nicht.'), 'pin', wait))

    # --- Operationen -------------------------------------------------------------------------------------------------
    def call(self, op, args, authenticated=False):
        entry = OPS.get(op)
        if entry is None:
            raise GuardError(t('Unbekannte Operation.'))
        if entry[0] and not authenticated:
            raise GuardError(t('Anmeldung mit der Eltern-PIN erforderlich.'), 'auth')
        if not isinstance(args, dict):
            raise GuardError(t('Ungültige Anfrage.'))
        with self.transaction() as doc:
            result = entry[1](self, doc, args)
        if isinstance(result, Denied):
            raise result.error
        return result


def _public(self, doc, args):
    return copy.deepcopy(doc['settings'])


def _verify(self, doc, args):
    denied = self._check_pin(doc, args.get('pin'))
    return denied or {'epoch': doc['auth_epoch']}


def _epoch(self, doc, args):
    return {'epoch': doc['auth_epoch']}


def _tick(self, doc, args):
    seconds = args.get('seconds')
    if type(seconds) is not int or not 1 <= seconds <= 300:
        raise GuardError(t('Ungültige Zeitangabe.'))
    doc['settings']['today_used_seconds'] += seconds
    return copy.deepcopy(doc['settings'])


def _set_color(self, doc, args):
    color = args.get('bg_color')
    if not isinstance(color, str) or not COLOR_PATTERN.fullmatch(color):
        raise GuardError(t('Bitte eine gültige Hintergrundfarbe auswählen.'))
    doc['settings']['bg_color'] = color.upper()
    return copy.deepcopy(doc['settings'])


def _set_limit(self, doc, args):
    doc['settings']['daily_limit_minutes'] = _minutes(args.get('minutes'), 0)
    return copy.deepcopy(doc['settings'])


def _set_window(self, doc, args):
    start, end = args.get('start', ''), args.get('end', '')
    if not isinstance(start, str) or not isinstance(end, str):
        raise GuardError(t('Bitte Uhrzeiten im Format HH:MM eingeben.'))
    if start or end:
        if not start or not end:
            raise GuardError(t('Bitte Beginn und Ende des Nutzungszeitraums angeben oder beide leeren.'))
        if start == end:
            raise GuardError(t('Beginn und Ende des Nutzungszeitraums müssen verschieden sein.'))
        try:
            state.clock_minutes(start), state.clock_minutes(end)
        except ValueError:
            raise GuardError(t('Bitte Uhrzeiten im Format HH:MM eingeben.')) from None
    doc['settings'].update(allowed_from=start, allowed_until=end, window_override_date='')
    return copy.deepcopy(doc['settings'])


def _add_bonus(self, doc, args):
    minutes = _minutes(args.get('minutes'), 1)
    before = doc['settings']['bonus_minutes']
    doc['settings']['bonus_minutes'] = min(600, before + minutes)
    return {'added': doc['settings']['bonus_minutes'] - before}


def _clear_bonus(self, doc, args):
    doc['settings']['bonus_minutes'] = 0
    return copy.deepcopy(doc['settings'])


def _reset_today(self, doc, args):
    doc['settings'].update(today_used_seconds=0, bonus_minutes=0)
    return copy.deepcopy(doc['settings'])


def _apps_active(self, doc, args):
    ids, active = _ids(args.get('ids')), args.get('active')
    if type(active) is not bool:
        raise GuardError(t('Ungültige Anfrage.'))
    disabled = set(doc['settings']['disabled_apps'])
    disabled.difference_update(ids) if active else disabled.update(ids)
    doc['settings']['disabled_apps'] = sorted(disabled)
    return copy.deepcopy(doc['settings'])


def _toggle_app(self, doc, args):
    disabled = set(doc['settings']['disabled_apps'])
    disabled.symmetric_difference_update(set(_ids([args.get('id')])))
    doc['settings']['disabled_apps'] = sorted(disabled)
    return copy.deepcopy(doc['settings'])


def _save_preferences(self, doc, args):
    new_pin = args.get('new_pin')
    patch = {}
    color = args.get('bg_color')
    if not isinstance(color, str) or not COLOR_PATTERN.fullmatch(color):
        raise GuardError(t('Bitte eine gültige Hintergrundfarbe auswählen.'))
    patch['bg_color'] = color.upper()
    for key in ('category_tabs', 'speak_titles'):
        if key in args:
            if type(args[key]) is not bool:
                raise GuardError(t('Ungültige Anfrage.'))
            patch[key] = args[key]
    if 'speak_voice' in args:
        if args['speak_voice'] not in CATEGORIES_VOICES:
            raise GuardError(t('Ungültige Stimme.'))
        patch['speak_voice'] = args['speak_voice']
    if new_pin is not None and not valid_pin(new_pin):
        raise GuardError(t('Die PIN muss aus 4 bis 12 Ziffern bestehen.'))
    doc['settings'].update(patch)
    if new_pin is not None:
        doc.update(pin=hash_pin(new_pin), failures=0, locked_until=0, auth_epoch=doc['auth_epoch'] + 1)
    return {'epoch': doc['auth_epoch']}


def _set_many(self, doc, args):
    patch = args.get('patch')
    if not isinstance(patch, dict) or not set(patch) <= set(BACKUP_KEYS):
        raise GuardError(t('Ungültige Einstellungen im Backup.'))
    probe = dict(doc['settings'])
    probe.update(patch)
    if 'bg_color' in patch and (not isinstance(patch['bg_color'], str) or not COLOR_PATTERN.fullmatch(patch['bg_color'])):
        raise GuardError(t('Bitte eine gültige Hintergrundfarbe auswählen.'))
    if 'daily_limit_minutes' in patch:
        _minutes(patch['daily_limit_minutes'], 0, 24 * 60)
    for key in ('category_tabs', 'speak_titles'):
        if key in patch and type(patch[key]) is not bool:
            raise GuardError(t('Ungültige Einstellungen im Backup.'))
    if 'speak_voice' in patch and patch['speak_voice'] not in CATEGORIES_VOICES:
        raise GuardError(t('Ungültige Stimme.'))
    if 'disabled_apps' in patch:
        _ids(patch['disabled_apps'])
    try:
        state.usage_window(probe)
    except ValueError:
        raise GuardError(t('Ungültiger Nutzungszeitraum.')) from None
    doc['settings'].update(patch)
    if 'allowed_from' in patch or 'allowed_until' in patch:
        doc['settings']['window_override_date'] = ''
    return copy.deepcopy(doc['settings'])


def _unlock_today(self, doc, args):
    denied = self._check_pin(doc, args.get('pin'))
    if denied:
        return denied
    doc['settings']['window_override_date'] = str(date.today())
    return copy.deepcopy(doc['settings'])


def _bonus_until(self, doc, args):
    """Sperrbildschirm: so viele Minuten ab jetzt, mit PIN. Entspricht der bisherigen Berechnung."""
    minutes = _minutes(args.get('minutes'), 5, 180)
    denied = self._check_pin(doc, args.get('pin'))
    if denied:
        return denied
    settings = doc['settings']
    required = settings['today_used_seconds'] + minutes * 60
    settings['bonus_minutes'] = max(0, (required + 59) // 60 - settings['daily_limit_minutes'])
    return copy.deepcopy(settings)


# Name -> (braucht Anmeldung, Handler). Ohne Anmeldung: lesen, Zeit melden, Farbe und alles mit PIN in der Anfrage.
OPS = {
    'public': (False, _public), 'tick': (False, _tick), 'set_color': (False, _set_color),
    'verify': (False, _verify), 'unlock_today': (False, _unlock_today), 'bonus_until': (False, _bonus_until),
    'epoch': (False, _epoch),
    'set_limit': (True, _set_limit), 'set_window': (True, _set_window), 'add_bonus': (True, _add_bonus),
    'clear_bonus': (True, _clear_bonus), 'reset_today': (True, _reset_today), 'apps_active': (True, _apps_active),
    'toggle_app': (True, _toggle_app), 'save_preferences': (True, _save_preferences), 'set_many': (True, _set_many),
}
