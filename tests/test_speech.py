"""Sprachausgabe der Kachel-Titel: Text, Cache und Hintergrunderzeugung ohne Piper und ohne Ton."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['PAIMENOS_LANGUAGE'] = 'de'

try:
    from PyQt5.QtCore import QEvent, Qt
    from PyQt5.QtGui import QFocusEvent
    from PyQt5.QtWidgets import QApplication
except ImportError:  # PyQt5 ist eine Geräte-Abhängigkeit
    QApplication = None


@unittest.skipIf(QApplication is None, 'PyQt5 not installed')
class SpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_spoken_text_drops_symbols_and_turns_slashes_into_pauses(self):
        from paimenos.speech import spoken_text
        self.assertEqual(spoken_text('📷 Kamera / Bilder / Videos'), 'Kamera, Bilder, Videos')
        self.assertEqual(spoken_text('🍊  Tux & Co'), 'Tux und Co')
        self.assertEqual(spoken_text('🍊'), '')

    def test_cache_name_depends_on_voice_and_text(self):
        from paimenos.speech import cache_name
        self.assertEqual(cache_name('Tux Paint', 'a'), cache_name('Tux Paint', 'a'))
        self.assertNotEqual(cache_name('Tux Paint', 'a'), cache_name('Tux Paint', 'b'))
        self.assertNotEqual(cache_name('Tux Paint', 'a'), cache_name('Tux Math', 'a'))

    def test_each_language_offers_a_female_and_a_male_voice(self):
        from paimenos import speech
        for lang in ('de', 'en'):
            female, male = speech.voice_name('female', lang), speech.voice_name('male', lang)
            self.assertNotEqual(female, male)
            self.assertTrue(female.startswith(lang) and male.startswith(lang))
        self.assertEqual(speech.voice_name('unbekannt', 'de'), speech.voice_name('male', 'de'))
        self.assertEqual(speech.voice_name(None, 'en'), speech.voice_name('male', 'en'))  # Standard: Männerstimme

    def test_changing_the_voice_uses_a_separate_set_of_recordings(self):
        from paimenos import speech
        speaker = speech.TitleSpeaker()
        speaker.set_gender('female')
        female = speaker.voice
        speaker.set_gender('male')
        self.assertNotEqual(speaker.voice, female)
        self.assertNotEqual(speech.cache_name('Geany', female), speech.cache_name('Geany', speaker.voice))

    def test_recording_only_exists_once_generated(self):
        from paimenos import speech
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(speech.recording_path('Tux Paint', 'v', folder))
            (Path(folder) / speech.cache_name('Tux Paint', 'v')).write_bytes(b'RIFF')
            self.assertIsNotNone(speech.recording_path('Tux Paint', 'v', folder))

    def test_generator_starts_once_for_missing_titles_only(self):
        from paimenos import speech
        speaker = speech.TitleSpeaker()
        speaker.set_enabled(True)
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(speech, 'CACHE_DIR', Path(folder)), \
                mock.patch.object(speech, 'voice_available', return_value=True), \
                mock.patch.object(speech.subprocess, 'Popen') as popen:
            done = Path(folder) / speech.cache_name('Tux Paint', speech.voice_name())
            done.write_bytes(b'RIFF')
            popen.return_value.poll.return_value = 0
            speaker.ensure_recordings(['Tux Paint', 'Geany', '🍊'])
            popen.assert_called_once()
            sent = popen.return_value.stdin.write.call_args[0][0].decode()
            self.assertIn('Geany', sent)
            self.assertNotIn('Tux Paint', sent)
            speaker.ensure_recordings(['Tux Paint', 'Geany'])  # bereits angefordert
            popen.assert_called_once()

    def test_nothing_is_generated_when_disabled_or_voice_missing(self):
        from paimenos import speech
        speaker = speech.TitleSpeaker()
        with mock.patch.object(speech, 'voice_available', return_value=True), \
                mock.patch.object(speech.subprocess, 'Popen') as popen:
            speaker.ensure_recordings(['Geany'])
            speaker.set_enabled(True)
        with mock.patch.object(speech, 'voice_available', return_value=False), \
                mock.patch.object(speech.subprocess, 'Popen') as popen:
            speaker.ensure_recordings(['Geany'])
        popen.assert_not_called()

    def test_tile_announces_on_keyboard_focus_but_not_on_window_activation(self):
        from paimenos.menu_tiles import PaimenOSTile
        spoken = []
        tile = PaimenOSTile({'id': 'x', 'title': 'Der Elefant', 'type': 'native', 'command': 'true'})
        tile.window = lambda: type('W', (), {'speak_title': staticmethod(spoken.append)})()
        with mock.patch('paimenos.menu_tiles.child_access_blocked', return_value=False):
            tile.focusInEvent(QFocusEvent(QEvent.FocusIn, Qt.ActiveWindowFocusReason))
            self.assertEqual(spoken, [])
            tile.focusInEvent(QFocusEvent(QEvent.FocusIn, Qt.TabFocusReason))
        self.assertEqual(spoken, ['Der Elefant'])
        tile.close()


class GeneratorTests(unittest.TestCase):
    FAKE_PIPER = (
        "import time, wave\n"
        "class PiperVoice:\n"
        "    @staticmethod\n"
        "    def load(path):\n"
        "        return PiperVoice()\n"
        "    def synthesize_wav(self, text, handle):\n"
        "        time.sleep(0.3)\n"
        "        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)\n"
        "        handle.writeframes(b'\\0\\0' * 160)\n")

    def test_concurrent_generators_lose_no_recordings(self):
        import json
        import subprocess
        script = ROOT / 'tools/speech-generate.py'
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'piper.py').write_text(self.FAKE_PIPER)
            out = Path(folder) / 'out'
            env = dict(os.environ, PYTHONPATH=folder)
            jobs = [{'a.wav': 'A', 'b.wav': 'B'}, {'b.wav': 'B', 'c.wav': 'C'}]
            processes = []
            for job in jobs:
                process = subprocess.Popen([sys.executable, str(script), 'voice.onnx', str(out)], env=env,
                                           stdin=subprocess.PIPE, text=True)
                process.stdin.write(json.dumps(job))
                process.stdin.close()
                processes.append(process)
            self.assertEqual([p.wait(timeout=30) for p in processes], [0, 0])
            self.assertEqual(sorted(p.name for p in out.glob('*.wav')), ['a.wav', 'b.wav', 'c.wav'])
            self.assertEqual(list(out.glob('*.part')), [])


@unittest.skipIf(QApplication is None, 'PyQt5 not installed')
class PronunciationTests(unittest.TestCase):
    def setUp(self):
        from paimenos import parent
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(parent, 'APPS_FILE', str(Path(folder.name) / 'apps.json'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.parent = parent

    def stored(self, app_id):
        return next(a for a in self.parent.read_apps() if a['id'] == app_id)

    def test_pronunciation_is_saved_kept_and_cleared(self):
        app_id = self.parent.save_app('Geany', 'https://example.org', pronunciation='  Dschieni ')
        self.assertEqual(self.stored(app_id)['pronunciation'], 'Dschieni')
        self.parent.save_app('Geany', 'https://example.org', app_id=app_id)  # None: unverändert
        self.assertEqual(self.stored(app_id)['pronunciation'], 'Dschieni')
        self.parent.save_app('Geany', 'https://example.org', app_id=app_id, pronunciation='')
        self.assertNotIn('pronunciation', self.stored(app_id))

    def test_overlong_or_control_character_pronunciation_is_rejected(self):
        for bad in ('x' * 81, 'a\x07b'):
            with self.assertRaises(ValueError):
                self.parent.save_app('Geany', 'https://example.org', pronunciation=bad)

    def test_pronunciation_replaces_the_title_for_speech(self):
        from paimenos.speech import spoken_source
        self.assertEqual(spoken_source({'title': 'Geany', 'pronunciation': 'Dschieni'}), 'Dschieni')
        self.assertEqual(spoken_source({'title': 'Eigene App', 'pronunciation': ''}), 'Eigene App')
        self.assertEqual(spoken_source({}), '')

    def test_bundled_pronunciation_applies_per_language_and_parents_win(self):
        from paimenos.pronunciation import default_pronunciation
        from paimenos.speech import spoken_source
        self.assertEqual(default_pronunciation('Geany', 'de'), 'Dschieni')
        self.assertEqual(default_pronunciation('Geany', 'en'), 'Jeany')
        self.assertEqual(default_pronunciation('Meine Geany-App', 'de'), '')  # umbenannt: kein Standard
        self.assertEqual(spoken_source({'title': 'Geany'}), 'Dschieni')  # Testsprache: de
        self.assertEqual(spoken_source({'title': 'Geany', 'pronunciation': 'Gieni'}), 'Gieni')
        self.assertEqual(spoken_source({'title': 'Die Maus'}), 'Die Maus')

    def test_every_bundled_pronunciation_survives_the_speech_filter(self):
        from paimenos.pronunciation import defaults
        from paimenos.speech import spoken_text
        for lang, table in defaults().items():
            for title, text in table.items():
                self.assertTrue(spoken_text(text), (lang, title))
                self.assertLessEqual(len(text), 80)


if __name__ == '__main__':
    unittest.main()
