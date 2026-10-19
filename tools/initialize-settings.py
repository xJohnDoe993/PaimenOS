"""Erste Eltern-Einstellungen im root-eigenen Guard-Speicher anlegen; nie einen vorhandenen überschreiben."""
from pathlib import Path as _Path
import sys as _sys
_release = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(_release / ('app' if (_release / 'app').is_dir() else 'src')))
from paimenos.i18n import t
import json
import os
from pathlib import Path
import re
import sys

from paimenos import state
from paimenos.guard_store import GuardStore

# Standard: PIN und Einschränkungen liegen root-eigen im Guard-Speicher, nie in einer Datei im Home von kids.
#   initialize-settings.py PIN               Guard-Speicher und Marker anlegen (Installer, als root)
#   initialize-settings.py DATEI PIN         alte Einstellungsdatei (Entwicklung, Tests, Geräte ohne Guard)
pin = sys.argv[-1]
if len(sys.argv) not in (2, 3) or not re.fullmatch(r"[0-9]{4,12}", pin):
    raise SystemExit(t('Eltern-PIN: 4 bis 12 Ziffern erforderlich.'))
if len(sys.argv) == 3:
    path = Path(sys.argv[1])
    settings = json.loads((Path(__file__).resolve().parents[1] / "data/default-settings.json").read_text())
    settings["pin"] = pin
    with path.open("x", encoding="utf-8") as handle:
        json.dump(settings, handle, ensure_ascii=False, indent=2)
    path.chmod(0o600)
else:
    GuardStore().initialize(pin)
    os.makedirs(os.path.dirname(state.GUARD_MARKER), exist_ok=True)
    with open(state.GUARD_MARKER, "w", encoding="utf-8") as handle:
        handle.write("1\n")
    os.chmod(state.GUARD_MARKER, 0o644)
