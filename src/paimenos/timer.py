#!/usr/bin/env python3
import os, sys, time, subprocess, fcntl
from paimenos.state import read_settings, update_settings, remaining_seconds
from paimenos.screen_guard import app_active, protected_view_active

LOCKSCREEN_SCRIPT = "/usr/local/lib/paimenos/current/run.py"

def main():
    # Auch nach wiederholtem Openbox-Autostart nur einmal zählen.
    with open(os.path.expanduser("~/.local/state/paimenos/paimenos-timer.lock"), "a") as instance:
        try:
            fcntl.flock(instance, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        run_timer()


def run_timer():
    lock_process = None
    previous = time.monotonic()
    pending = 0.0
    while True:
        time.sleep(1)
        now = time.monotonic()
        # Suspend zählt nicht: monotonic läuft während suspend nicht weiter.
        # Aktive Bildschirmzeit: nur zählen, solange ein Programm oder eine Webapp
        # läuft; Menü und Eltern-Bereich verbrauchen nichts.
        if app_active() and (lock_process is None or lock_process.poll() is not None):
            pending += max(0, now - previous)
        previous = now
        used = int(pending)
        def tick(data):
            data["today_used_seconds"] += used
        try:
            settings = read_settings()
            left = remaining_seconds(settings)
            if used >= 10 or (used > 0 and left is not None and used >= left):
                settings = update_settings(tick)
                pending -= used
            remaining = remaining_seconds(settings)
        except (OSError, ValueError, KeyError, TypeError):
            # Keep enforcing the lock and retry after a settings repair instead
            # of exhausting systemd's restart limit. Never overwrite bad data.
            remaining = 0
        if remaining == 0:
            if (lock_process is None or lock_process.poll() is not None) and not protected_view_active():
                lock_process = subprocess.Popen([sys.executable, "-I", LOCKSCREEN_SCRIPT, "lockscreen"])
        elif lock_process is not None and lock_process.poll() is None:
            lock_process.terminate()
            lock_process.wait(timeout=5)

if __name__ == "__main__":
    main()

