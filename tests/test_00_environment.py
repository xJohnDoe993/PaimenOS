"""Die Tests laufen immer ohne den Guard des Rechners, auf dem sie ausgeführt werden.

discover lädt dieses Modul zuerst; der Patch gilt für den ganzen Lauf. Die Guard-Tests setzen ihren eigenen Marker.
"""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paimenos import state

state.GUARD_MARKER = os.devnull + '.paimenos-test-ohne-guard'
