#!/usr/bin/env python3
"""Synthesise missing title recordings with Piper. Run with the Piper virtualenv's Python.

    PIPER_PYTHON tools/speech-generate.py VOICE.onnx OUT_DIR < titles.json

stdin: JSON object {file_name: spoken_text}. Existing files are skipped, each WAV is
written under a temporary name and renamed, and a lock lets only one generator work at a time
(a second one waits, then only creates what is still missing).
Standard library plus piper only, so it works without the PaimenOS package.
"""
import fcntl
import json
import os
import sys
import wave


def main(voice_path, out_dir):
    jobs = json.load(sys.stdin)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, '.lock'), 'a') as lock:
        # Läuft schon ein Generator, warten statt aufgeben: danach zählen nur noch fehlende Dateien,
        # so gehen Titel nicht verloren, die das Menü bereits als angefordert führt.
        fcntl.flock(lock, fcntl.LOCK_EX)
        pending = {name: text for name, text in jobs.items()
                   if not os.path.exists(os.path.join(out_dir, name))}
        if not pending:
            return 0
        from piper import PiperVoice
        voice = PiperVoice.load(voice_path)
        for name, text in pending.items():
            target = os.path.join(out_dir, name)
            temp = target + '.part'
            try:
                with wave.open(temp, 'wb') as handle:
                    voice.synthesize_wav(text, handle)
                os.replace(temp, target)
            except Exception as exc:  # one broken title must not stop the others
                print(f'{name}: {exc}', file=sys.stderr)
                if os.path.exists(temp):
                    os.unlink(temp)
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
