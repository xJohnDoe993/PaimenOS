#!/bin/bash
# Wird von install.sh in einer gemeinsamen Shell geladen.
# 6.5 Sprachausgabe der Kachel-Titel (Piper, optional)
# ---------------------------------------------------------------------------
if [[ "${PAIMENOS_OFFLINE:-0}" == 1 ]]; then
    echo "$(paimenos_text 'Offline-Modus: Die Sprachausgabe (Piper) wird übersprungen. Nachholen: sudo python3 /usr/local/lib/paimenos/current/tools/install-paimenos-speech.py')"
else
    echo "$(paimenos_text 'Sprachausgabe (Piper) einrichten ...')"
    if ! /usr/bin/python3 /usr/local/lib/paimenos/current/tools/install-paimenos-speech.py; then
        echo "$(paimenos_text 'Hinweis: Die Sprachausgabe konnte nicht eingerichtet werden. Nachholen: sudo python3 /usr/local/lib/paimenos/current/tools/install-paimenos-speech.py')" >&2
    fi
fi
