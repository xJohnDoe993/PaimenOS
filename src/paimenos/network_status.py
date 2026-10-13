"""Erreichbarkeit prüfen, ohne den Qt-Menüthread auf einen Socket warten zu lassen."""
import socket
import threading
import time


class NetworkStatus:
    def __init__(self, check=None, clock=time.monotonic):
        self.clock, self.check = clock, check or self.probe
        self.lock = threading.Lock()
        self.online, self.pending, self.last_started = False, False, -100

    # Mehrere Ziele: Manche Netze (VM-NAT, Filter) sperren einzelne Adressen oder Ports.
    TARGETS = (('1.1.1.1', 53), ('9.9.9.9', 53), ('deb.debian.org', 443))

    @classmethod
    def probe(cls):
        for target in cls.TARGETS:
            try:
                with socket.create_connection(target, timeout=1.5):
                    return True
            except OSError:
                continue
        return False

    def value(self):
        with self.lock:
            if not self.pending and self.clock() - self.last_started >= 3:
                self.pending = True
                self.last_started = self.clock()
                threading.Thread(target=self.refresh, daemon=True).start()
            return self.online

    def refresh(self):
        try:
            online = bool(self.check())
        except Exception:
            online = False
        with self.lock:
            self.online, self.pending = online, False

