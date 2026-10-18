export DEBIAN_FRONTEND=noninteractive
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games"


KIDS_USER="kids"
KIDS_HOME="/home/${KIDS_USER}"
PAIMENOS_DIR="/usr/local/lib/paimenos/current/app/paimenos"
OPENBOX_DIR="${KIDS_HOME}/.config/openbox"
CONFIG_DIR="${KIDS_HOME}/.config/paimenos"
STATE_DIR="${KIDS_HOME}/.local/state/paimenos"
ICONS_DIR="${KIDS_HOME}/.local/share/paimenos/icons"
MENU="${PAIMENOS_DIR}/menu.py"
APPS_JSON="${CONFIG_DIR}/apps.json"
SETTINGS_JSON="${CONFIG_DIR}/settings.json"
TIMER_SCRIPT="${PAIMENOS_DIR}/timer.py"
LOCKSCREEN_SCRIPT="${PAIMENOS_DIR}/lockscreen.py"
OVERLAY_SCRIPT="${PAIMENOS_DIR}/close_overlay.py"
MEDIA_DAEMON="${PAIMENOS_DIR}/media.py"
PARENT_WEB_SCRIPT="${PAIMENOS_DIR}/parent_web.py"
STATE_SCRIPT="${PAIMENOS_DIR}/state.py"
MARKER_FILE="/etc/paimenos-laptop.installed"

# Optionale Laptop-Funktionen: 0 überspringt die Einrichtung, 1 aktiviert sie.
# TLP ist für Laptops gedacht: auf ARM-Boards ohne Akku (z. B. Raspberry Pi) standardmäßig aus.
if [[ -z "${PAIMENOS_ENABLE_TLP:-}" && "$PAIMENOS_IS_ARM" == 1 ]] && ! compgen -G '/sys/class/power_supply/BAT*' >/dev/null; then
    PAIMENOS_ENABLE_TLP=0
fi
PAIMENOS_ENABLE_TLP="${PAIMENOS_ENABLE_TLP:-1}"
PAIMENOS_ENABLE_ZRAM="${PAIMENOS_ENABLE_ZRAM:-1}"
PAIMENOS_ENABLE_AUTO_UPDATES="${PAIMENOS_ENABLE_AUTO_UPDATES:-1}"
PAIMENOS_ENABLE_APP_UPDATES="${PAIMENOS_ENABLE_APP_UPDATES:-$PAIMENOS_ENABLE_AUTO_UPDATES}"
for install_option in "$PAIMENOS_ENABLE_TLP" "$PAIMENOS_ENABLE_ZRAM" "$PAIMENOS_ENABLE_AUTO_UPDATES" "$PAIMENOS_ENABLE_APP_UPDATES"; do
    if [[ "$install_option" != 0 && "$install_option" != 1 ]]; then
        echo "$(paimenos_text 'FEHLER: Laptop-Installationsschalter müssen 0 oder 1 sein.')" >&2
        exit 1
    fi
done

echo "$(paimenos_text 'PaimenOS {value0} – Neuinstallation' "$(cat "${REPO_DIR}/VERSION")")"

# Einen laufenden Backend-Auftrag nicht mitten in apt/dpkg abbrechen.
exec 9>/run/paimenos-emulator-install.lock
chmod 0600 /run/paimenos-emulator-install.lock
if ! flock -n 9; then
    echo "$(paimenos_text 'FEHLER: Im Backend läuft eine Emulator-Installation. Bitte deren Abschluss abwarten und das Setup erneut starten.')" >&2
    exit 1
fi
systemctl stop paimenos-emulators.service >/dev/null 2>&1 || true
flock -u 9
exec 9>&-
