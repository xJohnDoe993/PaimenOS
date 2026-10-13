#!/bin/bash
# Textmenü des PaimenOS-Live-Systems auf tty1: Installieren, WLAN, Konsole, Neustart.
set -uo pipefail
[[ $EUID == 0 ]] || exit 1
REPO_DIR=/opt/paimenos-source
source "$REPO_DIR/installer/language.sh"
MEDIUM=${PAIMENOS_LIVE_MEDIUM:-/run/live/medium}
TITLE="$(paimenos_text 'PaimenOS Live-System')"

# Der PaimenOS-Installer (iso/install.sh) ersetzt den Debian-Installer.
install_entry() {
    /usr/local/sbin/paimenos-install || true
}

# Der Bootmenü-Eintrag "PaimenOS installieren" startet den Installer direkt.
if grep -qw paimenos.install /proc/cmdline && [[ ! -e /run/paimenos-install-offered ]]; then
    touch /run/paimenos-install-offered
    install_entry
fi

while :; do
    choice=$(whiptail --title "$TITLE" --notags --default-item install \
        --menu "$(paimenos_text 'Willkommen bei PaimenOS. Was möchtest du tun?')" 17 64 5 \
        install "$(paimenos_text 'PaimenOS installieren')" \
        network "$(paimenos_text 'WLAN/Netzwerk einrichten')" \
        shell "$(paimenos_text 'Konsole öffnen')" \
        reboot "$(paimenos_text 'Neu starten')" \
        poweroff "$(paimenos_text 'Ausschalten')" 3>&1 1>&2 2>&3) || continue
    case "$choice" in
        install) install_entry ;;
        network) nmtui || true ;;
        shell)
            clear
            printf '%s\n' "$(paimenos_text 'Mit exit zurück zum Menü.')"
            runuser -u live -- bash -l || true ;;
        reboot) systemctl reboot ;;
        poweroff) systemctl poweroff ;;
    esac
done
