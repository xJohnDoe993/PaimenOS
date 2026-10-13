"""Zusätzliche Flatpak-Apps aus Flathub installieren und entfernen (Root-Dienst).

Wird vom Steuer-Socket des Emulator-Dienstes aufgerufen (nur root und kids). Der Client schickt nur eine
geprüfte App-ID; Befehle und Pfade bestimmt dieser Dienst. Ein Auftrag läuft nach dem anderen im Hintergrund.
"""
from paimenos.i18n import t
from paimenos.flatpak_store import MANAGED_FILE, managed_ids, tile_id, valid_app_id
from contextlib import nullcontext
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading

REMOTE = 'paimenos-flathub'
REMOTE_URL = 'https://dl.flathub.org/repo/flathub.flatpakrepo'
RELEASE = Path('/usr/local/lib/paimenos/current')
KIDS_HOME = Path('/home/kids')
BIN_DIR = Path('/usr/local/bin')
STATE_PATH = Path('/var/lib/paimenos/flatpak-job.json')
FLATPAK = '/usr/bin/flatpak'
# Das Setup legt diese Datei nur an, wenn automatische Aktualisierungen gewünscht sind.
UPDATES_MARKER = Path('/etc/apt/apt.conf.d/90-paimenos-updates')
IDLE = {'state': 'idle', 'kind': '', 'app_id': '', 'percent': 0, 'message': '', 'log': []}


class FlatpakError(ValueError):
    pass


class FlatpakJobs:
    def __init__(self, runner=subprocess.run, state_path=STATE_PATH, lock=nullcontext, bin_dir=BIN_DIR,
                 managed_file=MANAGED_FILE, kids_home=KIDS_HOME, run_in_thread=True, updates_marker=UPDATES_MARKER):
        self.runner, self.state_path, self.lock_factory = runner, Path(state_path), lock
        self.bin_dir, self.managed_file, self.kids_home = Path(bin_dir), Path(managed_file), Path(kids_home)
        self.run_in_thread, self.updates_marker = run_in_thread, Path(updates_marker)
        self.lock = threading.Lock()
        self.job = copy.deepcopy(IDLE)
        try:
            previous = json.loads(self.state_path.read_text())
            if isinstance(previous, dict) and previous.get('state') in ('done', 'error', 'running'):
                self.job = {**IDLE, **previous}
                if previous['state'] == 'running':
                    self.job.update(state='error', message=t('Der Auftrag wurde durch einen Neustart unterbrochen. Bitte erneut versuchen.'))
        except (OSError, ValueError):
            pass

    # --- Zustand -------------------------------------------------------------------------------------------
    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.job)

    def _save_locked(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=self.state_path.parent, delete=False) as stream:
            json.dump(self.job, stream, ensure_ascii=False)
            path = Path(stream.name)
        path.chmod(0o600)
        os.replace(path, self.state_path)

    def report(self, message, percent=None, **fields):
        with self.lock:
            self.job.update(fields, message=message)
            if percent is not None:
                self.job['percent'] = percent
            self.job['log'] = (self.job['log'] + [message])[-40:]
            self._save_locked()

    # --- Befehle -------------------------------------------------------------------------------------------
    def _run(self, args, timeout=3600):
        """Befehl ausführen; bei Fehler geht nur das Ende der Ausgabe an den Webbereich."""
        env = dict(os.environ, DEBIAN_FRONTEND='noninteractive', LC_ALL='C')
        with tempfile.TemporaryFile() as log:
            result = self.runner(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, env=env)
            log.seek(0, 2)
            log.seek(max(0, log.tell() - 1500))
            tail = log.read().decode('utf-8', errors='replace').strip()
        return result.returncode, tail

    def _capture(self, args, timeout=30):
        result = self.runner(args, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, result.stdout or ''

    def _menu(self, *args):
        # Eigene Unit statt runuser: Dieser Dienst läuft mit ProtectHome=read-only, das ein Kind-Prozess erbt.
        # Die Unit gehört systemd, nicht diesem Dienst, und darf das Menü des Benutzers kids ändern.
        code, tail = self._run(['/usr/bin/systemd-run', '--quiet', '--pipe', '--wait', '--collect', '--uid=kids',
                                '--setenv=HOME=' + str(self.kids_home), '/usr/bin/python3', '-I',
                                str(RELEASE / 'run.py'), 'cli_flatpak_menu', *args], timeout=120)
        if code:
            raise FlatpakError(t('Das Menü konnte nicht angepasst werden: ') + tail[-400:])

    def _ensure_flatpak(self):
        if not Path(FLATPAK).exists():
            self.report(t('Flatpak wird eingerichtet (Internet nötig) …'), 8)
            code, tail = self._run(['/usr/bin/apt-get', '-o', 'DPkg::Lock::Timeout=120', 'install', '-y', '--no-install-recommends',
                                    'flatpak', 'xdg-desktop-portal', 'xdg-desktop-portal-gtk'], timeout=1800)
            if code:
                raise FlatpakError(t('Flatpak konnte nicht installiert werden: ') + tail[-600:])
        code, listing = self._capture([FLATPAK, 'remote-list', '--system', '--columns=name'])
        if REMOTE not in listing.split():
            self.report(t('Flathub wird als Quelle eingerichtet …'), 15)
            code, tail = self._run([FLATPAK, 'remote-add', '--system', '--if-not-exists', REMOTE, REMOTE_URL], timeout=300)
            if code:
                raise FlatpakError(t('Flathub konnte nicht eingerichtet werden. Internetverbindung prüfen. ') + tail[-400:])

    def _display_name(self, app_id):
        code, listing = self._capture([FLATPAK, 'list', '--system', '--app', '--columns=application,name'])
        for line in listing.splitlines():
            parts = line.split('\t')
            if len(parts) >= 2 and parts[0].strip() == app_id and parts[1].strip():
                return parts[1].strip()
        return app_id

    # --- Dateien -------------------------------------------------------------------------------------------
    def _write_launcher(self, app_id):
        path = self.bin_dir / ('paimenos-app-' + tile_id(app_id))
        temporary = path.with_name(path.name + '.new')
        temporary.write_text('#!/bin/sh\n# PaimenOS: stabile Flatpak-App als angemeldeter Benutzer starten.\n'
                             'exec /usr/bin/python3 -I /usr/local/lib/paimenos/current/run.py cli_flatpak '
                             + shlex.quote(app_id) + ' "$@"\n', encoding='utf-8')
        temporary.chmod(0o755)
        os.replace(temporary, path)

    def _remove_launchers(self, app_id):
        for path in self.bin_dir.glob('paimenos-app-*'):
            try:
                if path.is_file() and not path.is_symlink() and shlex.quote(app_id) in path.read_text(encoding='utf-8'):
                    path.unlink()
            except (OSError, UnicodeDecodeError):
                continue

    def _write_managed(self, ids):
        self.managed_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=self.managed_file.parent, delete=False) as stream:
            stream.write(''.join(i + '\n' for i in sorted(set(ids))))
            path = Path(stream.name)
        path.chmod(0o644)
        os.replace(path, self.managed_file)

    # --- Aufträge ------------------------------------------------------------------------------------------
    def start(self, kind, app_id, delete_data=False):
        if kind not in ('install', 'remove') or not valid_app_id(app_id):
            raise ValueError(t('Ungültige Flatpak-App-ID.'))
        if kind == 'remove' and app_id not in managed_ids(self.managed_file):
            raise ValueError(t('Diese App wird nicht von PaimenOS verwaltet.'))
        with self.lock:
            if self.job['state'] == 'running':
                raise ValueError(t('Ein anderer Auftrag läuft bereits.'))
            self.job = {**IDLE, 'state': 'running', 'kind': kind, 'app_id': app_id, 'percent': 2,
                        'message': t('Auftrag wird gestartet …'), 'log': []}
            self._save_locked()
        if self.run_in_thread:
            threading.Thread(target=self.worker, args=(kind, app_id, bool(delete_data)), daemon=True).start()
        else:
            self.worker(kind, app_id, bool(delete_data))
        return self.snapshot()

    def worker(self, kind, app_id, delete_data):
        try:
            with self.lock_factory():
                if kind == 'install':
                    self.install(app_id)
                else:
                    self.remove(app_id, delete_data)
            self.report(self.job['message'], 100, state='done')
        except Exception as exc:
            self.report(str(exc)[:1500] or t('Unbekannter Fehler.'), state='error')

    def install(self, app_id):
        self._ensure_flatpak()
        self.report(t('Die App wird heruntergeladen und installiert. Das kann einige Minuten dauern …'), 30)
        code, tail = self._run([FLATPAK, 'install', '--system', '--noninteractive', '-y', REMOTE, app_id + '//stable'])
        if code:
            raise FlatpakError(t('Installation fehlgeschlagen: ') + tail[-800:])
        self.report(t('Menü-Eintrag wird angelegt …'), 85)
        self._write_launcher(app_id)
        self._write_managed(managed_ids(self.managed_file) + [app_id])
        name = self._display_name(app_id)
        self._menu('add', app_id, name)
        if self.updates_marker.exists():
            self._run(['/usr/bin/systemctl', 'enable', '--now', 'paimenos-app-update.timer'], timeout=60)
        self.job['message'] = t('{value0} ist installiert und im Kinder-Menü.', value0=name)

    def remove(self, app_id, delete_data):
        name = self._display_name(app_id)
        self.report(t('Menü-Eintrag wird entfernt …'), 15)
        self._menu('remove', app_id)
        self.report(t('Die App wird deinstalliert …'), 40)
        code, tail = self._run([FLATPAK, 'uninstall', '--system', '--noninteractive', '-y', app_id + '//stable'])
        if code and self._capture([FLATPAK, 'info', '--system', app_id + '//stable'])[0] == 0:
            raise FlatpakError(t('Deinstallation fehlgeschlagen: ') + tail[-800:])
        self._run([FLATPAK, 'uninstall', '--system', '--unused', '--noninteractive', '-y'])
        self._remove_launchers(app_id)
        self._write_managed([i for i in managed_ids(self.managed_file) if i != app_id])
        if delete_data:
            self.report(t('Die Daten der App werden gelöscht …'), 85)
            base = (self.kids_home / '.var/app').resolve()
            target = base / app_id
            if target.is_dir() and not target.is_symlink() and target.resolve().parent == base:
                shutil.rmtree(target, ignore_errors=True)
        self.job['message'] = t('{value0} wurde entfernt.', value0=name)
