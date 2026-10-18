"""Serialize Webapp launches and let Firefox flush its persistent profile on exit."""
from paimenos.i18n import t
import errno
import fcntl
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from paimenos.paths import RELEASE_DIR
from paimenos.webapp import PARENT_PREFERENCES, browser_command, prepare_profile

PROFILE_BUSY = 75
CLOSE_TIMEOUT = 8


def profile_available(profile):
    """Probe Firefox's Linux POSIX lock; never unlink a live lock inode."""
    with open(Path(profile) / '.parentlock', 'a') as handle:
        try:
            fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB, 0, 0)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise
        fcntl.lockf(handle, fcntl.LOCK_UN, 0, 0)
    return True


def wait_for_profile(profile, timeout=15, cancelled=lambda: False):
    deadline = time.monotonic() + timeout
    while not cancelled():
        if profile_available(profile):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.1)
    return False


def owns_profile(pid, profile, proc_root=Path('/proc')):
    """Only identify this user's actual Firefox with an explicit matching profile."""
    try:
        entry = proc_root / str(pid)
        if entry.stat().st_uid != os.getuid():
            return False
        executable = os.path.basename(os.readlink(entry / 'exe'))
        if executable not in ('firefox', 'firefox-esr', 'firefox-bin'):
            return False
        args = (entry / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
        for index, arg in enumerate(args[:-1]):
            if arg in ('--profile', '-profile'):
                return os.path.realpath(args[index + 1]) == os.path.realpath(profile)
        return False
    except (OSError, UnicodeError):
        return False


def recover_profile(profile, cancelled=lambda: False):
    """Recover an orphan from an older menu; never guess from a stale lock PID."""
    handles = []
    try:
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit() or not owns_profile(int(entry.name), profile):
                continue
            pid = int(entry.name)
            try:
                fd = os.pidfd_open(pid)
            except (OSError, AttributeError):
                continue
            # Verify identity after opening the stable process handle.
            if not owns_profile(pid, profile):
                os.close(fd)
                continue
            handles.append(fd)
            close_windows(pid)
        if not handles:
            return False
        if wait_for_profile(profile, timeout=CLOSE_TIMEOUT, cancelled=cancelled):
            return True
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if cancelled():
                return False
            for fd in handles:
                try:
                    signal.pidfd_send_signal(fd, sig)
                except ProcessLookupError:
                    pass
            if wait_for_profile(profile, timeout=2, cancelled=cancelled):
                return True
        return False
    finally:
        for fd in handles:
            os.close(fd)


def close_windows(pid, display_factory=None):
    """Send a normal WM_DELETE_WINDOW only to windows owned by this Firefox."""
    from Xlib import X, Xatom, display, error, protocol
    connection = None
    try:
        connection = (display_factory or display.Display)()
        root = connection.screen().root
        atom = connection.intern_atom
        windows = root.get_full_property(atom('_NET_CLIENT_LIST'), Xatom.WINDOW)
        if windows is None:
            return False
        sent = False
        for window_id in windows.value:
            try:
                window = connection.create_resource_object('window', int(window_id))
                owner = window.get_full_property(atom('_NET_WM_PID'), Xatom.CARDINAL)
                if owner is None or not len(owner.value) or int(owner.value[0]) != pid:
                    continue
                supported = window.get_full_property(atom('WM_PROTOCOLS'), Xatom.ATOM)
                delete = atom('WM_DELETE_WINDOW')
                if supported is None or delete not in supported.value:
                    continue
                window.send_event(protocol.event.ClientMessage(
                    window=window, client_type=atom('WM_PROTOCOLS'),
                    data=(32, [delete, X.CurrentTime, 0, 0, 0])), event_mask=0)
                sent = True
            except error.BadWindow:
                continue
        connection.sync()
        return sent
    except (error.DisplayError, error.ConnectionClosedError, error.XError, OSError):
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except (error.DisplayError, error.ConnectionClosedError, OSError):
                pass


def stop_browser(process, timeout=CLOSE_TIMEOUT):
    """Normal quit first; bounded recovery for a browser that has stopped responding."""
    if process.poll() is not None:
        return
    close_windows(process.pid)
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        print(t('Webapp reagiert nicht auf Schließen; beende die eigene Browsergruppe.'), flush=True)
    # Firefox was started in a separate session. Other apps/profiles are untouched.
    for sig, delay in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=delay)
            break
        except subprocess.TimeoutExpired:
            continue


def wipe_profile(profile):
    """Alles außer den eigenen Sperrdateien löschen (nur bei laufendem Zugriff durch den Aufrufer, nie bei offenem Firefox)."""
    keep = {'.paimenos-launch.lock', '.parentlock'}
    for entry in Path(profile).iterdir():
        if entry.name in keep:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            pass


def run_browser(profile, template, url, command=None, with_overlay=True, ephemeral=False):
    """ephemeral: Profil vor dem Start leeren und nach dem Schließen wieder löschen (Eltern-Bereich)."""
    profile = os.path.realpath(profile)
    os.makedirs(profile, mode=0o700, exist_ok=True)
    stopping = False
    overlay_failed = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    process = overlay = None
    try:
        # Held until Firefox exits and releases its own lock, including overlay cleanup.
        with open(Path(profile) / '.paimenos-launch.lock', 'a') as launch_lock:
            try:
                fcntl.flock(launch_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(t('Diese Webapp wird bereits gestartet oder geschlossen.'), flush=True)
                return PROFILE_BUSY
            available = wait_for_profile(profile, timeout=2, cancelled=lambda: stopping)
            if not available and not stopping:
                available = recover_profile(profile, cancelled=lambda: stopping)
            if not available:
                if stopping:
                    return 0
                print(t('Firefox-Profil ist noch durch einen anderen Prozess belegt.'), flush=True)
                return PROFILE_BUSY
            # Never modify a running profile, or delete its cache/cookies/lock files.
            if ephemeral:
                wipe_profile(profile)
                prepare_profile(profile, template, PARENT_PREFERENCES, context_menu=True)
            else:
                prepare_profile(profile, template)
            if stopping:
                return 0
            process = subprocess.Popen(command or browser_command(profile, url), start_new_session=True)
            if with_overlay:
                try:
                    overlay = subprocess.Popen([sys.executable, '-I', str(RELEASE_DIR / 'run.py'),
                        'close_overlay', str(process.pid), str(os.getpid())])
                except OSError as exc:
                    print(t('Schließen-Overlay: ') + str(exc), flush=True)
                    stopping = True
                    overlay_failed = True
            while process.poll() is None and not stopping:
                if overlay is not None and overlay.poll() is not None:
                    print(t('Der Webapp-Menüknopf wurde unerwartet beendet.'), flush=True)
                    overlay_failed = True
                    stopping = True
                    break
                time.sleep(.1)
            if stopping:
                stop_browser(process)
            code = process.wait()
            if overlay is not None and overlay.poll() is None:
                overlay.terminate()
                try:
                    overlay.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    overlay.kill()
                    overlay.wait()
            if not wait_for_profile(profile, timeout=5):
                print(t('Firefox hat das Profil noch nicht freigegeben.'), flush=True)
                return PROFILE_BUSY
            if ephemeral:
                wipe_profile(profile)
            if overlay_failed:
                return 1
            return 0 if stopping else (code if code >= 0 else 1)
    finally:
        if process is not None and process.poll() is None:
            stop_browser(process)
        if overlay is not None and overlay.poll() is None:
            overlay.terminate()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


