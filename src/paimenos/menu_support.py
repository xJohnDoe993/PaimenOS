"""Kleine Hilfsfunktionen des Kinder-Menüs ohne Qt-Zustand."""
import os
import shutil
import socket

def find_program(name):
    for candidate in ([name, "luanti"] if name == "minetest" else [name]):
        found = shutil.which(candidate)
        if found:
            return found
        for directory in ("/usr/games", "/usr/local/games", "/usr/bin"):
            path = os.path.join(directory, candidate)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return None

def is_online():
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=0.5):
            return True
    except OSError:
        return False

def get_battery_status():
    try:
        power_supply = "/sys/class/power_supply"
        if not os.path.isdir(power_supply):
            return None

        batteries = []
        for name in sorted(os.listdir(power_supply)):
            path = os.path.join(power_supply, name)
            if not os.path.isdir(path):
                continue
            try:
                with open(os.path.join(path, "type"), "r", encoding="utf-8") as f:
                    supply_type = f.read().strip().lower()
            except OSError:
                continue
            if supply_type != "battery":
                continue

            capacity = None
            try:
                with open(os.path.join(path, "capacity"), "r", encoding="utf-8") as f:
                    value = int(f.read().strip())
                if 0 <= value <= 100:
                    capacity = value
            except (OSError, ValueError):
                pass

            try:
                with open(os.path.join(path, "status"), "r", encoding="utf-8") as f:
                    status = f.read().strip().lower()
            except OSError:
                status = ""

            if capacity is not None:
                batteries.append((capacity, status))

        if not batteries:
            return None

        capacity = round(sum(item[0] for item in batteries) / len(batteries))
        statuses = {item[1] for item in batteries}
        charging = bool(statuses & {"charging", "full"})
        return capacity, charging
    except Exception:
        return None
