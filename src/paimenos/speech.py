"""Vorgelesene Kachel-Titel: Piper erzeugt jede Aufnahme einmal, das Menü spielt nur die WAV-Datei ab."""
import hashlib
import json
import os
import subprocess
import unicodedata
from pathlib import Path
from paimenos.i18n import language
from paimenos.paths import RELEASE_DIR, USER_DATA_DIR
from paimenos.pronunciation import default_pronunciation
from PyQt5.QtCore import QObject, QTimer, QUrl
from PyQt5.QtMultimedia import QSoundEffect

SPEECH_DIR = Path('/usr/local/lib/paimenos-speech')
VENV_DIR = SPEECH_DIR / 'venv'
VOICE_DIR = SPEECH_DIR / 'voices'
CACHE_DIR = USER_DATA_DIR / 'speech'
GENERATOR = RELEASE_DIR / 'tools/speech-generate.py'
GENDERS = ('female', 'male')
VOICES = {
    'de': {'male': 'de_DE-thorsten-medium', 'female': 'de_DE-ramona-low'},
    'en': {'male': 'en_US-ryan-medium', 'female': 'en_US-amy-medium'},
}
DEBOUNCE_MS = 250  # Schnelles Durchblättern soll nicht jede Kachel einzeln vorlesen.


def spoken_text(title):
    """Titel für die Sprachausgabe: Emojis und Symbole entfallen, Schrägstriche werden zur Pause."""
    text = str(title).replace('/', ', ').replace('·', ', ')
    kept = ''.join(c for c in text if unicodedata.category(c)[0] in 'LNZ' or c in ".,!?'-&")
    words = ' '.join(kept.replace('&', ' und ' if language() == 'de' else ' and ').split())
    return words.replace(' ,', ',')


def spoken_source(item):
    """Text für die Sprachausgabe: Aussprache der Eltern, sonst mitgelieferte Aussprache, sonst der Titel."""
    title = str(item.get('title') or '')
    return str(item.get('pronunciation') or default_pronunciation(title) or title)


def voice_name(gender='male', lang=None):
    voices = VOICES.get(lang or language(), VOICES['de'])
    return voices.get(gender, voices['male'])


def cache_name(text, voice):
    return hashlib.sha256(f'{voice}\n{text}'.encode()).hexdigest()[:24] + '.wav'


def voice_available(voice):
    return (VENV_DIR / 'bin/python').exists() and (VOICE_DIR / f'{voice}.onnx').exists()


def recording_path(title, voice=None, cache_dir=None):
    """Pfad der fertigen Aufnahme oder None, solange sie noch nicht erzeugt wurde."""
    text = spoken_text(title)
    if not text:
        return None
    path = Path(cache_dir or CACHE_DIR) / cache_name(text, voice or voice_name())
    return path if path.is_file() else None


class TitleSpeaker(QObject):
    """Spielt nur die zuletzt angewählte Aufnahme; fehlende Aufnahmen werden einmalig im Hintergrund erzeugt."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = False
        self.voice = voice_name()
        self.pending = ''
        self.generator = None
        self.requested = set()  # Fehlgeschlagene Titel nicht bei jedem Neuladen erneut versuchen.
        self.effect = QSoundEffect(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(DEBOUNCE_MS)
        self.timer.timeout.connect(self.speak_now)

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.stop()

    def set_gender(self, gender):
        self.voice = voice_name(gender)

    def say(self, title):
        if self.enabled:
            self.pending = title or ''
            self.timer.start()

    def speak_now(self):
        self.effect.stop()
        path = recording_path(self.pending, self.voice)
        if path is not None:
            self.effect.setSource(QUrl.fromLocalFile(str(path)))
            self.effect.play()

    def ensure_recordings(self, titles):
        """Startet Piper mit niedriger Priorität für alle Titel ohne Aufnahme."""
        voice = self.voice
        if not self.enabled or not voice_available(voice):
            return
        if self.generator is not None and self.generator.poll() is None:
            return
        jobs = {}
        for title in titles:
            text = spoken_text(title)
            name = cache_name(text, voice) if text else ''
            if name and not (CACHE_DIR / name).exists() and (voice, name) not in self.requested:
                jobs[name] = text
        if not jobs:
            return
        self.requested.update((voice, name) for name in jobs)
        try:
            self.generator = subprocess.Popen(
                [str(VENV_DIR / 'bin/python'), '-I', str(GENERATOR), str(VOICE_DIR / f'{voice}.onnx'), str(CACHE_DIR)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, preexec_fn=lambda: os.nice(19))
            self.generator.stdin.write(json.dumps(jobs).encode())
            self.generator.stdin.close()
        except OSError:
            self.generator = None

    def stop(self):
        self.timer.stop()
        self.effect.stop()
