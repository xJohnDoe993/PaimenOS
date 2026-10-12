"""Mitgelieferte Aussprachen für Titel, die die Stimme sonst falsch liest (ohne Qt, auch für das Backend)."""
import json
from functools import lru_cache
from paimenos.i18n import language
from paimenos.paths import DATA_DIR


@lru_cache(maxsize=1)
def defaults():
    try:
        data = json.loads((DATA_DIR / 'default-pronunciations.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def default_pronunciation(title, lang=None):
    """Standard-Aussprache für genau diesen Titel in der Systemsprache; umbenannte Apps haben keine."""
    table = defaults().get(lang or language(), {})
    return table.get(str(title).strip(), '') if isinstance(table, dict) else ''
