#!/bin/bash
set -Eeuo pipefail
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export REPO_DIR
source "${REPO_DIR}/installer/language.sh"
if [[ "${1:-}" == --help ]]; then
    if [[ "$PAIMENOS_LANGUAGE" == en ]]; then
        cat "${REPO_DIR}/docs/install-help.en.txt"
    else
        cat "${REPO_DIR}/docs/install-help.txt"
    fi
    exit 0
fi
RESUME=0
if [[ "${1:-}" == --resume ]]; then RESUME=1; shift; fi
if [[ $# -ne 0 ]]; then echo "$(paimenos_text 'Unbekannte Argumente. Siehe ./install.sh --help')" >&2; exit 2; fi
if [[ "$EUID" -ne 0 ]]; then echo "$(paimenos_text 'Aufruf: sudo bash install.sh')" >&2; exit 1; fi
. /etc/os-release
if [[ "${ID:-}" != debian || ! "${VERSION_CODENAME:-}" =~ ^(bookworm|trixie)$ ]]; then
    echo "$(paimenos_text 'Debian 12 oder 13 erforderlich.')" >&2; exit 1
fi
if [[ -e /usr/local/lib/laurinos/current || -L /usr/local/lib/laurinos/current ||
      -e /etc/laurinos-laptop.installed || -f /home/kids/.config/openbox/laurinos-settings.json ||
      -d /home/kids/.config/laurinos ]]; then
    echo "$(paimenos_text 'LaurinOS-Installation gefunden. PaimenOS 0.64.0 benötigt eine Neuinstallation auf frischem Debian; keine automatische Migration.')" >&2
    exit 1
fi
if [[ -f /home/kids/.config/openbox/paimenos-settings.json || -e /etc/paimenos-laptop.installed ]]; then
    echo "$(paimenos_text 'Diese Version erwartet eine Neuinstallation. Für modulare Installationen: sudo bash update.sh')" >&2
    exit 1
fi
if [[ -e /usr/local/lib/paimenos/current && "$RESUME" != 1 ]]; then
    echo "$(paimenos_text 'Unvollständige modulare Installation gefunden. Fortsetzen: sudo bash install.sh --resume')" >&2
    exit 1
fi
source "${REPO_DIR}/installer/architecture.sh"
exec 8>/run/paimenos-maintenance.lock
if ! flock -n 8; then echo "$(paimenos_text 'Eine PaimenOS-Wartung läuft bereits.')" >&2; exit 1; fi
installation_failed() {
    echo "$(paimenos_text 'FEHLER: {value0}:{value1}; Installation nicht abgeschlossen.' "$1" "$2")" >&2
}
trap 'installation_failed "${BASH_SOURCE[0]}" "$LINENO"' ERR
# A custom PIN is required before making changes to the system.
if [[ "$RESUME" == 1 && -f /home/kids/.config/paimenos/settings.json ]]; then
    PAIMENOS_PARENT_PIN=""
    echo "$(paimenos_text 'Vorhandene Eltern-Einstellungen und PIN werden beibehalten.')"
else
    PAIMENOS_PARENT_PIN="${PAIMENOS_PARENT_PIN:-}"
    if [[ -z "$PAIMENOS_PARENT_PIN" ]]; then
        read -r -s -p "$(paimenos_text 'Neue Eltern-PIN (4 bis 12 Ziffern): ')" PAIMENOS_PARENT_PIN </dev/tty
        echo
        read -r -s -p "$(paimenos_text 'PIN wiederholen: ')" pin_repeat </dev/tty
        echo
        if [[ "$PAIMENOS_PARENT_PIN" != "$pin_repeat" ]]; then echo "$(paimenos_text 'PINs stimmen nicht überein.')" >&2; exit 1; fi
        unset pin_repeat
    fi
    if [[ ! "$PAIMENOS_PARENT_PIN" =~ ^[0-9]{4,12}$ ]]; then echo "$(paimenos_text 'PIN: 4 bis 12 Ziffern erforderlich.')" >&2; exit 1; fi
fi
unset BASH_ENV
source "${REPO_DIR}/installer/common.sh"
source "${REPO_DIR}/installer/environment.sh"
for module in 10-system 20-hardware 30-plymouth 40-user 50-browser 60-apps 65-speech 70-defaults 80-services 90-desktop 99-finish; do
    source "${REPO_DIR}/installer/${module}.sh"
done
