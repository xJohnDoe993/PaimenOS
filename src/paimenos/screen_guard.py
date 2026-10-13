"""Cross-process priority for screen-time and parent views."""
import fcntl
from paimenos.paths import STATE_DIR
from paimenos.state import read_settings, remaining_seconds


def screen_time_expired():
    try:
        return remaining_seconds(read_settings()) == 0
    except (OSError, ValueError, KeyError, TypeError):
        # Missing/corrupt settings must never grant access to child applications.
        return True


def _open_guard(name='paimenos-protected-view.lock'):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return open(STATE_DIR / name, 'a')


class ViewGuard:
    """A live lease; process exit releases it without leaving a stale flag."""
    lock_name = 'paimenos-protected-view.lock'

    def __init__(self):
        self.file = _open_guard(self.lock_name)
        try:
            fcntl.flock(self.file, fcntl.LOCK_SH)
        except BaseException:
            self.file.close()
            raise

    def close(self):
        self.file.close()


class ActiveAppLease(ViewGuard):
    """Held by the menu while a child application runs; only then screen time counts."""
    lock_name = 'paimenos-active-app.lock'


def _lease_held(name):
    try:
        with _open_guard(name) as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
        return False
    except OSError:
        return True


def protected_view_active():
    return _lease_held(ViewGuard.lock_name)


def app_active():
    """True while a child application runs. Unknown state counts as active."""
    return _lease_held(ActiveAppLease.lock_name)


def child_access_blocked():
    return screen_time_expired() or protected_view_active()
