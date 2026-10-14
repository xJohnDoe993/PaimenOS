"""Nach einer Offline-Installation fehlende Teile nachholen (Root-Dienst).

Das Setup legt MARKER an, wenn es ohne Internet lief. Der Elternbereich zeigt dann einen Hinweis; erst der Knopf
startet diesen Auftrag. Er holt Mauszeiger, Sprachausgabe und Webapp-Logos nach. Emulatoren und Flathub-Apps
bleiben eigene Seiten im Elternbereich. Der Merker verschwindet nur, wenn alle Schritte gelungen sind.
"""
from paimenos.i18n import language, t
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

MARKER = Path('/var/lib/paimenos/offline-install-pending')
STATE_PATH = Path('/var/lib/paimenos/offline-complete.json')
RELEASE = Path('/usr/local/lib/paimenos/current')
ICON_THEME_DIR = Path('/usr/share/icons')
CURSOR_THEME = 'Bibata-Original-Ice'
CURSOR_URL = 'https://github.com/ful1e5/Bibata_Cursor/releases/download/v2.0.7/Bibata-Original-Ice.tar.xz'
KIDS_HOME = Path('/home/kids')
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/120.0.0.0 Safari/537.36')
LOGOS = {
    'maus': 'https://licensing.wdr-mediagroup.com/wp-content/uploads/2021/12/die_maus_logo-1200x414.png',
    'elefant': 'https://www.wdrmaus.de/elefantenseite/codebase/eltern2021/images/elefant_header.svg',
    'kika': 'https://logos-world.net/wp-content/uploads/2022/07/KiKA-Logo-700x394.png',
    'kikaninchen': 'https://www.kika.de/adventskalender/adventskalender-138-resimage_v-cropped_w-1024.png?version=58631',
    'fragfinn': 'https://www.scout-magazin.de/files/aktuelles/news/2021/fragFINN.de_Logo.png',
    'blinde-kuh': 'https://gs-am-selzbogen.de/wp-content/uploads/2023/05/blindekuh-logo_0-1125x715-2-768x524.png',
}
IDLE = {'state': 'idle', 'percent': 0, 'message': '', 'steps': {}, 'log': []}


def pending(marker=MARKER):
    return Path(marker).exists()


class CompleteJobs:
    def __init__(self, runner=subprocess.run, state_path=STATE_PATH, marker=MARKER, icon_dir=ICON_THEME_DIR,
                 kids_home=KIDS_HOME, release=RELEASE, run_in_thread=True, lang=language):
        self.runner, self.state_path, self.marker = runner, Path(state_path), Path(marker)
        self.icon_dir, self.kids_home, self.release = Path(icon_dir), Path(kids_home), Path(release)
        self.run_in_thread, self.lang = run_in_thread, lang
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

    def snapshot(self):
        with self.lock:
            return {**copy.deepcopy(self.job), 'pending': pending(self.marker)}

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

    def start(self):
        with self.lock:
            if self.job['state'] == 'running':
                raise ValueError(t('Eine Nachinstallation läuft bereits.'))
            if not pending(self.marker):
                raise ValueError(t('Es ist nichts nachzuholen.'))
            self.job = {**copy.deepcopy(IDLE), 'state': 'running', 'message': t('Nachinstallation wird gestartet …')}
            self._save_locked()
        if self.run_in_thread:
            threading.Thread(target=self.worker, daemon=True).start()
        else:
            self.worker()
        return self.snapshot()

    # --- Schritte ------------------------------------------------------------------------------------------
    def _run(self, args, timeout):
        return self.runner(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout, check=False).returncode == 0

    def step_cursor(self):
        if (self.icon_dir / CURSOR_THEME).is_dir():
            return True
        with tempfile.TemporaryDirectory(prefix='paimenos-cursor-') as directory:
            archive = Path(directory) / 'cursor.tar.xz'
            if not self._run(['wget', '-q', '-T', '30', '-O', str(archive), CURSOR_URL], 300):
                return False
            if not self._run(['tar', '-xf', str(archive), '-C', str(self.icon_dir)], 120):
                return False
        return (self.icon_dir / CURSOR_THEME).is_dir()

    def step_speech(self):
        return self._run(['/usr/bin/python3', str(self.release / 'tools/install-paimenos-speech.py')], 3600)

    def step_logos(self):
        # Englische Installationen liefern ihre Logos mit; es gibt nichts herunterzuladen.
        if self.lang() == 'en':
            return True
        target = self.kids_home / '.local/share/paimenos/icons'
        target.mkdir(parents=True, exist_ok=True)
        ok = True
        for name, url in LOGOS.items():
            destination = target / (name + '.png')
            if destination.is_file() and destination.stat().st_size:
                continue
            with tempfile.TemporaryDirectory(prefix='paimenos-logo-') as directory:
                download, converted = Path(directory) / 'download', Path(directory) / 'logo.png'
                if (self._run(['curl', '-fLsS', '--connect-timeout', '8', '--max-time', '30', '-A', USER_AGENT, '-o', str(download), url], 60)
                        and download.stat().st_size
                        and self._run(['convert', str(download), '-background', 'none', '-auto-orient', '-thumbnail', '256x256>',
                                       '-gravity', 'center', '-extent', '256x256', 'png:' + str(converted)], 60)
                        and converted.stat().st_size):
                    shutil.copyfile(converted, destination)
                    shutil.chown(destination, 'kids', 'kids')
                else:
                    ok = False
        return ok

    def worker(self):
        steps = [('cursor', t('Mauszeiger (Bibata)'), self.step_cursor),
                 ('speech', t('Sprachausgabe'), self.step_speech),
                 ('logos', t('Logos der Webseiten'), self.step_logos)]
        results = {}
        try:
            for index, (key, title, action) in enumerate(steps):
                self.report(t('{title} wird geladen …', title=title), percent=int(100 * index / len(steps)))
                try:
                    results[key] = bool(action())
                except Exception:
                    results[key] = False
                self.report(t('{title}: {result}', title=title, result=t('fertig') if results[key] else t('fehlgeschlagen')), steps=dict(results))
            if all(results.values()):
                self.marker.unlink(missing_ok=True)
                self.report(t('Alles nachgeholt. Der Mauszeiger gilt nach dem nächsten Neustart.'), percent=100, state='done')
            else:
                self.report(t('Nicht alles ließ sich nachholen. Internet prüfen und erneut versuchen.'), percent=100, state='error')
        except Exception as exc:
            self.report(str(exc)[:2000], state='error')
