"""USB backups: bounded chunks, verified restores and recoverable directory swaps."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import threading

from paimenos.i18n import t
from paimenos.paths import DATA_DIR, USER_DATA_DIR, STATE_DIR
from paimenos import media, parent, settings_backup
from paimenos.emulator_catalog import CATALOG
from paimenos.state import atomic_json

ROOT = USER_DATA_DIR / 'emulators'
CHUNK = 64 * 1024 * 1024  # FAT32-compatible, even for large disc images.
MANIFEST_LIMIT = 16 * 1024 * 1024
APP_CATALOG = json.loads((DATA_DIR / 'app-data-catalog.json').read_text(encoding='utf-8'))
# XDG name plus German/English defaults when user-dirs.dirs is missing.
USER_FOLDERS = {'documents': ('DOCUMENTS', 'Dokumente', 'Documents'), 'pictures': ('PICTURES', 'Bilder', 'Pictures'),
                'music': ('MUSIC', 'Musik', 'Music'), 'videos': ('VIDEOS', 'Videos', 'Videos')}
GROUPS = {'roms': ('roms',), 'saves': ('saves', 'states', 'legacy_states'), 'bios': ('bios',),
          **{'app-' + app: tuple(f'app.{app}.{loc}' for loc in info['locations'])
             for app, info in APP_CATALOG.items()},
          'files': tuple('files.' + kind for kind in USER_FOLDERS),
          'settings': ('settings',)}
# Kein Ordner auf dem Gerät: wird als Momentaufnahme gesichert und nach Prüfung übernommen (settings_backup).
SETTINGS_KEY = 'settings'
# Top-level folders that are neither backed up nor kept while staging (Flatpak caches).
EXCLUDES = {f'app.{app}.{loc}': frozenset(spec.get('exclude', ()))
            for app, info in APP_CATALOG.items() for loc, spec in info['locations'].items()}
JOURNAL = STATE_DIR / 'backup-restore.json'
JOB = {'status': 'idle', 'message': '', 'done': 0, 'total': 0}
JOB_LOCK = threading.Lock()


def lenient(key):
    """App and personal folders skip links/special files instead of refusing the whole backup."""
    return key.startswith(('app.', 'files.'))


def user_folder(kind):
    home = Path.home()
    name, *defaults = USER_FOLDERS[kind]
    try:
        for line in (home / '.config/user-dirs.dirs').read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(rf'\s*XDG_{name}_DIR="\$HOME/([^"$\\]+)"\s*', line)
            if match:
                path = PurePosixPath(match.group(1).rstrip('/'))
                if path.parts and not path.is_absolute() and '..' not in path.parts:
                    return home / path
    except (OSError, UnicodeDecodeError):
        pass
    return next((home / d for d in defaults if (home / d).is_dir()), home / defaults[-1])


def targets():
    home = Path.home()
    return {**{key: ROOT / key for key in ('roms', 'saves', 'states', 'bios')},
            'legacy_states': home / '.config/retroarch/states',
            'apps': Path(parent.APPS_FILE),
            **{f'app.{app}.{loc}': home / spec['path']
               for app, info in APP_CATALOG.items() for loc, spec in info['locations'].items()},
            **{'files.' + kind: user_folder(kind) for kind in USER_FOLDERS}}


def labels():
    return {'roms': t('ROMs und Menüeinträge'), 'saves': t('Spielstände und Savestates'), 'bios': t('BIOS-Dateien'),
            **{'app-' + app: info['title'] for app, info in APP_CATALOG.items()},
            'files': t('Eigene Dateien (Dokumente, Bilder, Musik, Videos)'),
            'settings': t('Eltern-Einstellungen und Webapps (ohne PIN)')}


def offered():
    """Emulator groups always; app and personal groups only when local data exists."""
    dests = targets()
    return [g for g in GROUPS if g in ('roms', 'saves', 'bios', 'settings') or any(dests[k].is_dir() for k in GROUPS[g])]


def check_running(groups):
    titles = []
    for group in groups:
        if group.startswith('app-'):
            info = APP_CATALOG[group[4:]]
            found = subprocess.run(['pgrep', '-u', str(os.getuid()), '-x', info['process']],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if found.returncode == 0:
                titles.append(info['title'])
    if titles:
        raise ValueError(t('Bitte zuerst diese Apps beenden: {value0}', value0=', '.join(titles)))


def check_separate(keys):
    # Directory swaps must never contain one another, e.g. XDG folders pointing into app data.
    dests = targets()
    if any(lenient(key) and Path.home() not in dests[key].parents for key in keys):
        raise ValueError(t('Ein Sicherungsordner liegt außerhalb des Benutzerordners.'))
    paths = [dests[key] for key in keys]
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            if a == b or a in b.parents or b in a.parents:
                raise ValueError(t('Sicherungsordner überschneiden sich. Bitte Ordnereinstellungen prüfen.'))


@contextmanager
def data_lock(exclusive=False):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'emulator-data.lock').open('a') as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(t('Emulator oder Backup läuft. Bitte zuerst beenden.')) from None
        if not exclusive and JOURNAL.exists():
            raise ValueError(t('Wiederherstellung unterbrochen. Bitte im Backup-Bereich reparieren.'))
        yield


def protect_data(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with data_lock():
            return fn(*args, **kwargs)
    return wrapped


def drives():
    result = []
    def visit(items, usb=False):
        for dev in items:
            is_usb = usb or dev.get('tran') == 'usb'
            if is_usb:
                for mount in media.mountpoints(dev):
                    if mount == '/' or not os.path.ismount(mount) or Path(mount).is_symlink():
                        continue
                    key = hashlib.sha256((dev.get('name', '') + '\0' + mount).encode()).hexdigest()[:24]
                    result.append({'id': key, 'path': mount, 'label': str(dev.get('label') or dev.get('name')),
                                   'free': shutil.disk_usage(mount).free})
            visit(dev.get('children') or [], is_usb)
    visit(media.scan_devices() or [])
    return result


@contextmanager
def usb_root(device):
    selected = next((d for d in drives() if d['id'] == device), None)
    if selected is None:
        raise ValueError(t('USB-Medium nicht mehr verfügbar.'))
    fd = os.open(selected['path'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Keep all USB access relative to this open mount, even if unplugged/unmounted.
        if (not os.path.ismount(selected['path']) or
                os.fstat(fd).st_dev != os.stat(selected['path']).st_dev or
                not any(d['id'] == device for d in drives())):
            raise ValueError(t('USB-Medium nicht mehr verfügbar.'))
        yield fd
    finally:
        os.close(fd)


@contextmanager
def directory(fd, name, create=False):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
        except FileExistsError:
            pass
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        yield child
    finally:
        os.close(child)


def read_file(fd, name, limit):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError(t('Ungültiges Backup.'))
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError(t('Ungültiges Backup.'))
    return data


def write_file(fd, name, data):
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(handle, 'wb') as dest:
        dest.write(data)
        dest.flush()
        os.fsync(dest.fileno())


def checked_manifest(fd):
    data = json.loads(read_file(fd, 'manifest.json', MANIFEST_LIMIT))
    if not isinstance(data, dict) or data.get('format') != 'PaimenOS-backup-1':
        raise ValueError(t('Ungültiges Backup.'))
    groups = data.get('groups')
    if not isinstance(groups, list) or not groups or any(g not in GROUPS for g in groups):
        raise ValueError(t('Ungültiges Backup.'))
    files = data.get('files')
    if not isinstance(files, list) or len(files) > 100000:
        raise ValueError(t('Ungültiges Backup.'))
    allowed = {key for g in groups for key in GROUPS[g]}
    seen = set()
    for entry in files:
        key, name = entry['root'], entry['path']
        path = PurePosixPath(name)
        if (key not in allowed or not name or path.is_absolute() or '..' in path.parts
                or '\\' in name or str(path) != name or (key, name) in seen):
            raise ValueError(t('Ungültiges Backup.'))
        seen.add((key, name))
        if type(entry['size']) is not int or entry['size'] < 0 or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise ValueError(t('Ungültiges Backup.'))
        if not isinstance(entry['chunks'], list) or len(entry['chunks']) != (entry['size'] + CHUNK - 1) // CHUNK:
            raise ValueError(t('Ungültiges Backup.'))
        if any(not isinstance(c, str) or not re.fullmatch(r'[0-9]{8}\.part', c) for c in entry['chunks']):
            raise ValueError(t('Ungültiges Backup.'))
    return data


def available(device):
    result = []
    with usb_root(device) as root:
        try:
            with directory(root, 'PaimenOS-Backups') as folder:
                for name in sorted(os.listdir(folder), reverse=True):
                    if not re.fullmatch(r'backup-[0-9T-]+-[0-9a-f]{12}', name):
                        continue
                    try:
                        with directory(folder, name) as backup:
                            info = checked_manifest(backup)
                        result.append({'id': name, 'date': info.get('date', name), 'groups': info['groups'],
                                       'size': sum(f['size'] for f in info['files'])})
                    except (OSError, ValueError, KeyError, TypeError):
                        continue
        except FileNotFoundError:
            pass
    return result


def regular_files(root, key='', skipped=None):
    if root.is_symlink():
        raise ValueError(t('Verknüpfungen können nicht gesichert oder wiederhergestellt werden.'))
    if not root.exists():
        return
    for base, dirs, files in os.walk(root):
        if Path(base) == root:
            dirs[:] = [d for d in dirs if d not in EXCLUDES.get(key, ())]
        regular = []
        for name in dirs + files:
            path = Path(base) / name
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
                if name in files:
                    regular.append(name)
                continue
            if not lenient(key):
                raise ValueError(t('Verknüpfungen können nicht gesichert oder wiederhergestellt werden.'))
            if skipped is not None:
                skipped.append(path)
            if name in dirs:
                dirs.remove(name)
        for name in sorted(regular):
            yield Path(base) / name


def stage_ignore(key):
    """copytree filter: keep local links as links, drop special files and excluded caches."""
    def ignore(directory, names):
        top = Path(directory) == targets()[key]
        return [n for n in names if (top and n in EXCLUDES.get(key, ())) or not
                ((Path(directory) / n).is_symlink() or stat.S_ISDIR((Path(directory) / n).lstat().st_mode)
                 or stat.S_ISREG((Path(directory) / n).lstat().st_mode))]
    return ignore


def game_entries(items):
    result = []
    for item in items:
        if item.get('emulator') not in CATALOG:
            continue
        app_id, entry = item.get('id', ''), item.get('rom_entry', '')
        if (not re.fullmatch('rom-[0-9a-f]{24}', app_id) or not isinstance(entry, str)
                or not entry or Path(entry).name != entry or '\\' in entry or entry.startswith('.')):
            raise ValueError(t('Ungültige Spieleinträge im Backup.'))
        title = item.get('title', '')
        if not isinstance(title, str) or not title or len(title) > 80 or any(ord(c) < 32 for c in title):
            raise ValueError(t('Ungültige Spieleinträge im Backup.'))
        result.append({'id': app_id, 'title': title, 'emulator': item['emulator'], 'rom_entry': entry})
    if len({item['id'] for item in result}) != len(result):
        raise ValueError(t('Ungültige Spieleinträge im Backup.'))
    return result


def create(device, groups, report, skipped=None):
    keys = [key for group in groups for key in GROUPS[group] if key != SETTINGS_KEY]
    check_separate(keys)
    check_running(groups)
    for key in keys:
        safe_target(targets()[key])
    files = [(key, root, file) for key in keys
             for root in [targets()[key]] for file in regular_files(root, key, skipped)]
    blob = settings_backup.snapshot() if SETTINGS_KEY in groups else b''
    total = sum(file.stat().st_size for _, _, file in files) + len(blob)
    report(0, total)
    info = {'format': 'PaimenOS-backup-1', 'date': datetime.now(timezone.utc).isoformat(),
            'groups': groups, 'files': [], 'games': game_entries(parent.read_apps()) if 'roms' in groups else []}
    name = 'backup-' + datetime.now().strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(6)
    with usb_root(device) as root, directory(root, 'PaimenOS-Backups', True) as folder:
        if os.fstatvfs(folder).f_bavail * os.fstatvfs(folder).f_frsize < total + MANIFEST_LIMIT:
            raise ValueError(t('Nicht genügend Speicherplatz auf dem USB-Medium.'))
        partial = '.partial-' + name
        done = count = 0
        with directory(folder, partial, True) as out:
            for key, base, file in files:
                before = file.stat()
                digest = hashlib.sha256()
                entry = {'root': key, 'path': file.relative_to(base).as_posix(), 'size': 0, 'chunks': []}
                with file.open('rb') as source:
                    while block := source.read(CHUNK):
                        chunk = f'{count:08d}.part'
                        write_file(out, chunk, block)
                        digest.update(block)
                        entry['chunks'].append(chunk)
                        entry['size'] += len(block)
                        count += 1
                        done += len(block)
                        report(done, total)
                after = file.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError(t('Dateien wurden während des Backups verändert.'))
                entry['sha256'] = digest.hexdigest()
                info['files'].append(entry)
            if blob:
                chunks = []
                for start in range(0, len(blob), CHUNK):
                    chunks.append(f'{count:08d}.part')
                    write_file(out, chunks[-1], blob[start:start + CHUNK])
                    count += 1
                info['files'].append({'root': SETTINGS_KEY, 'path': 'settings.json', 'size': len(blob),
                                      'chunks': chunks, 'sha256': hashlib.sha256(blob).hexdigest()})
            encoded = json.dumps(info, ensure_ascii=False).encode()
            if len(encoded) > MANIFEST_LIMIT:
                raise ValueError(t('Zu viele Dateien für ein Backup.'))
            write_file(out, 'manifest.json', encoded)
            checked_manifest(out)
            os.fsync(out)
        os.rename(partial, name, src_dir_fd=folder, dst_dir_fd=folder)
        os.fsync(folder)
    return name


def safe_target(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(t('Verknüpfungen können nicht gesichert oder wiederhergestellt werden.'))


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove(path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def recover():
    if not JOURNAL.exists():
        return
    journal = json.loads(JOURNAL.read_text())
    token = journal['token']
    if not re.fullmatch('[0-9a-f]{16}', token):
        raise ValueError(t('Ungültiges Wiederherstellungsprotokoll.'))
    for key in reversed(journal['keys']):
        dest = targets()[key]
        new, old = dest.with_name('.restore-' + token + '-' + key), dest.with_name('.previous-' + token + '-' + key)
        for path in (dest, new, old):
            safe_target(path)
        if not journal.get('committed'):
            if old.exists():
                remove(dest)
                os.replace(old, dest)
            elif not journal['existed'][key] and not new.exists():
                remove(dest)
        remove(new)
        remove(old)
        sync_directory(dest.parent)
    JOURNAL.unlink()
    sync_directory(JOURNAL.parent)


def restore(device, backup_id, groups, report):
    if not re.fullmatch(r'backup-[0-9T-]+-[0-9a-f]{12}', backup_id):
        raise ValueError(t('Ungültiges Backup.'))
    with usb_root(device) as root, directory(root, 'PaimenOS-Backups') as folder, directory(folder, backup_id) as backup:
        info = checked_manifest(backup)
        if not set(groups) <= set(info['groups']):
            raise ValueError(t('Auswahl ist in diesem Backup nicht enthalten.'))
        # App/personal locations only when the backup has files there: an empty ~/.luanti
        # next to ~/.minetest would otherwise change where Luanti looks for worlds.
        present = {f['root'] for f in info['files']}
        keys = [key for group in groups for key in GROUPS[group]
                if key != SETTINGS_KEY and (not lenient(key) or key in present)]
        # Einstellungen zuerst vollständig prüfen: bei einem Fehler bleibt das Gerät unverändert.
        plan = None
        if SETTINGS_KEY in groups:
            entry = next((f for f in info['files'] if f['root'] == SETTINGS_KEY), None)
            if entry is None or entry['size'] > settings_backup.LIMIT:
                raise ValueError(t('Ungültige Einstellungen im Backup.'))
            raw = b''.join(read_file(backup, chunk, CHUNK) for chunk in entry['chunks'])
            if len(raw) != entry['size'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError(t('Prüfsumme oder Größe des Backups stimmt nicht.'))
            plan = settings_backup.parse(raw)
        if 'roms' in groups:
            keys.append('apps')
        if not keys:
            settings_backup.apply(plan)
            return
        check_separate(keys)
        check_running(groups)
        token = secrets.token_hex(8)
        dests = targets()
        stages = {key: dests[key].with_name('.restore-' + token + '-' + key) for key in keys}
        entries = [f for f in info['files'] if f['root'] in keys]
        report(0, sum(f['size'] for f in entries))
        # Journal preparation too, so interrupted staging is recoverable.
        journal = {'token': token, 'keys': keys, 'existed': {k: dests[k].exists() for k in keys}, 'committed': False}
        atomic_json(JOURNAL, journal)
        try:
            for key in keys:
                dest, stage = dests[key], stages[key]
                safe_target(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if key == 'apps':
                    continue
                list(regular_files(dest, key))  # Refuse links before copying existing data.
                if dest.exists():
                    # Local links are copied as links, never followed; backup files are not written through them.
                    shutil.copytree(dest, stage, symlinks=True, ignore=stage_ignore(key))
                else:
                    stage.mkdir()
            done = 0
            total = sum(f['size'] for f in entries)
            for entry in entries:
                dest = stages[entry['root']] / entry['path']
                safe_target(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with dest.open('wb') as target:
                    for chunk in entry['chunks']:
                        block = read_file(backup, chunk, CHUNK)
                        size += len(block)
                        if size > entry['size']:
                            raise ValueError(t('Prüfsumme oder Größe des Backups stimmt nicht.'))
                        target.write(block)
                        digest.update(block)
                        done += len(block)
                        report(done, total)
                    target.flush()
                    os.fsync(target.fileno())
                if size != entry['size'] or digest.hexdigest() != entry['sha256']:
                    raise ValueError(t('Prüfsumme oder Größe des Backups stimmt nicht.'))
            if 'roms' in groups:
                games = game_entries(info.get('games', []))
                ids = {g['id'] for g in games}
                current = parent.read_apps()
                if any(a.get('id') in ids and not a.get('emulator') for a in current):
                    raise ValueError(t('Spieleinträge kollidieren mit vorhandenen Anwendungen.'))
                for game in games:
                    rom = stages['roms'] / game['id'] / game['rom_entry']
                    if not rom.is_file():
                        raise ValueError(t('ROM für einen Menüeintrag fehlt im Backup.'))
                    game.update(type='native', custom=True, enabled=True,
                                command=shlex.join(['/usr/local/bin/paimenos-emulator', game['emulator'],
                                                    str(dests['roms'] / game['id'] / game['rom_entry'])]))
                atomic_json(stages['apps'], [a for a in current if a.get('id') not in ids] + games)
            for key in keys:
                stage = stages[key]
                files = [stage] if key == 'apps' else list(regular_files(stage, key))
                for file in files:
                    with file.open('rb') as handle:
                        os.fsync(handle.fileno())
                if stage.is_dir():
                    for base, _, _ in os.walk(stage, topdown=False):
                        sync_directory(base)
                sync_directory(stage.parent)
            sync_directory(JOURNAL.parent)
            try:
                for key in keys:
                    dest = dests[key]
                    if dest.exists():
                        os.replace(dest, dest.with_name('.previous-' + token + '-' + key))
                    os.replace(stages[key], dest)
                    sync_directory(dest.parent)
                journal['committed'] = True
                atomic_json(JOURNAL, journal)
                sync_directory(JOURNAL.parent)
            except Exception:
                recover()
                raise
            recover()
        finally:
            if JOURNAL.exists():
                recover()
            else:
                for stage in stages.values():
                    remove(stage)
        if plan:
            settings_backup.apply(plan)


def job_status():
    with JOB_LOCK:
        return dict(JOB, recovery_needed=JOURNAL.exists())


def start(action, device, groups, backup_id=''):
    if action not in ('create', 'restore', 'recover') or (action != 'recover' and
            (not groups or len(groups) != len(set(groups)) or any(g not in GROUPS for g in groups))):
        raise ValueError(t('Bitte Backup-Inhalte auswählen.'))
    with JOB_LOCK:
        if JOB['status'] == 'running':
            raise ValueError(t('Ein Backup-Vorgang läuft bereits.'))
        JOB.update(status='running', message=t('Vorgang läuft …'), done=0, total=0)
    def progress(done, total):
        with JOB_LOCK:
            JOB.update(done=done, total=total)
    def worker():
        try:
            with data_lock(True), parent.app_transaction():
                recover()
                skipped = []
                if action == 'create':
                    create(device, groups, progress, skipped)
                elif action == 'restore':
                    restore(device, backup_id, groups, progress)
            message = t('Vorgang erfolgreich abgeschlossen.')
            if skipped:
                message += ' ' + t('{value0} Verknüpfungen oder Sonderdateien wurden übersprungen.', value0=len(skipped))
            with JOB_LOCK:
                JOB.update(status='complete', message=message)
        except Exception as exc:
            with JOB_LOCK:
                JOB.update(status='error', message=str(exc))
    threading.Thread(target=worker, daemon=True).start()
