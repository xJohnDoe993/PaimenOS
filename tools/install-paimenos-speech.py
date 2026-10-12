#!/usr/bin/python3
"""Install Piper and a female and a male voice for the kids menu's spoken tile titles (needs internet, run as root).

    sudo python3 tools/install-paimenos-speech.py [--language de|en] [--check]

Piper lives in its own virtualenv; the voice comes from a fixed Hugging Face commit and its
SHA-256 is verified. Titles are synthesised later, once each, by tools/speech-generate.py.
"""
from pathlib import Path as _Path
import sys as _sys
_release = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(_release / ('app' if (_release / 'app').is_dir() else 'src')))
from paimenos.i18n import language, t
from paimenos.speech import SPEECH_DIR, VENV_DIR, VOICE_DIR, VOICES
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

PIPER_VERSION = '1.8.0'
VOICE_COMMIT = 'c10ece1aade47bb51c153c893d14e5bf8e5b7117'
VOICE_URL = 'https://huggingface.co/rhasspy/piper-voices/resolve/' + VOICE_COMMIT + '/{path}.{ext}'
VOICE_FILES = {
    'de_DE-ramona-low': ('de/de_DE/ramona/low/de_DE-ramona-low', {
        'onnx': 'a1068592532aad8c49bf3d1a01b8749c11d7dba726689ee28ad64e7b627ae4c5',
        'onnx.json': '64906ab7a050ac685fa3dcea044baa798f67ed77be164f9c668a69298c6487d2'}),
    'de_DE-thorsten-medium': ('de/de_DE/thorsten/medium/de_DE-thorsten-medium', {
        'onnx': '7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819',
        'onnx.json': '974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85'}),
    'en_US-amy-medium': ('en/en_US/amy/medium/en_US-amy-medium', {
        'onnx': 'b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18',
        'onnx.json': '95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77'}),
    'en_US-ryan-medium': ('en/en_US/ryan/medium/en_US-ryan-medium', {
        'onnx': 'abf4c274862564ed647ba0d2c47f8ee7c9b717d27bdad9219100eb310db4047a',
        'onnx.json': '44034c056cb15681b2ad494307c7f3f2e4499d1253c700c711fa0a4607ffe78d'}),
}


def run(command):
    subprocess.run(command, check=True)


def install_piper():
    python = VENV_DIR / 'bin/python'
    if not python.exists():
        run([sys.executable, '-m', 'venv', str(VENV_DIR)])
    installed = subprocess.run([str(python), '-m', 'pip', 'show', 'piper-tts'], capture_output=True, text=True)
    if f'Version: {PIPER_VERSION}' not in installed.stdout:
        run([str(python), '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
             'piper-tts==' + PIPER_VERSION])


def download_voice(name):
    path, checksums = VOICE_FILES[name]
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    for ext, expected in checksums.items():
        target = VOICE_DIR / f'{name}.{ext}'
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            continue
        fd, temp = tempfile.mkstemp(prefix='.voice-', dir=VOICE_DIR)
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, 'wb') as out, urllib.request.urlopen(
                    VOICE_URL.format(path=path, ext=ext), timeout=60) as response:
                for block in iter(lambda: response.read(1 << 20), b''):
                    digest.update(block)
                    out.write(block)
            if digest.hexdigest() != expected:
                raise ValueError(t('Prüfsumme der Stimme stimmt nicht: ') + target.name)
            os.chmod(temp, 0o644)
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


def check(name):
    return ((VENV_DIR / 'bin/python').exists()
            and all((VOICE_DIR / f'{name}.{ext}').exists() for ext in VOICE_FILES[name][1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--language', choices=sorted(VOICES), default=None)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    names = list(VOICES[args.language or language()].values())  # Männer- und Frauenstimme
    if args.check:
        ready = all(check(name) for name in names)
        print(t('Sprachausgabe bereit: ') + str(ready))
        return 0 if ready else 1
    if os.geteuid() != 0:
        raise SystemExit(t('Dieses Werkzeug muss als root laufen.'))
    SPEECH_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SPEECH_DIR, 0o755)
    install_piper()
    for name in names:
        download_voice(name)
    print(t('Sprachausgabe installiert: ') + ', '.join(names))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
