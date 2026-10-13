#!/bin/bash
# Runs interactively on tty1, using the same installer as a manual installation.
set -Eeuo pipefail
[[ $EUID == 0 ]] || exit 1
SOURCE=/opt/paimenos-source
PENDING=/var/lib/paimenos/setup-pending
SUCCESS=/etc/paimenos-laptop.installed
[[ -f $PENDING ]] || exit 0
REPO_DIR="$SOURCE"
source "$SOURCE/installer/language.sh"
APT_SESSION_CONFIG=$(mktemp /run/paimenos-apt.XXXXXX)
MEDIUM_MOUNT=/run/paimenos-medium
trap 'rm -f "$APT_SESSION_CONFIG"; umount "$MEDIUM_MOUNT" 2>/dev/null || true' EXIT

# Frische, signierte Paketlisten per Internet (wie bisher).
online_ready() {
    if ! /usr/bin/python3 "$SOURCE/iso/prepare-apt.py" --apt-config "$APT_SESSION_CONFIG"; then
        printf "$(paimenos_text 'Paketquellen konnten nicht vorbereitet werden. Bitte prüfen und erneut versuchen.\n')"
        return 1
    fi
    # An active link does not prove internet access. Require fresh, signed
    # package indexes before asking for the PIN or installing anything.
    if ! APT_CONFIG="$APT_SESSION_CONFIG" apt-get update; then
        printf "$(paimenos_text 'Debian-Paketquellen konnten nicht vollständig geprüft werden. Netzwerk und APT-Fehler oben prüfen; dann erneut versuchen.\n')"
        return 1
    fi
}

# Offline-Paketquelle des Installationsmediums (Volume-Label PAIMENOS) einbinden und prüfen.
offline_ready() {
    local device
    if ! mountpoint -q "$MEDIUM_MOUNT"; then
        device=$(blkid -L PAIMENOS 2>/dev/null | head -n 1) || return 1
        [[ -n "$device" ]] || return 1
        mkdir -p "$MEDIUM_MOUNT"
        mount -o ro "$device" "$MEDIUM_MOUNT" || return 1
    fi
    [[ -f "$MEDIUM_MOUNT/paimenos-repo/InRelease" ]] || return 1
    /usr/bin/python3 "$SOURCE/iso/prepare-apt.py" --offline-repo "$MEDIUM_MOUNT/paimenos-repo" --apt-config "$APT_SESSION_CONFIG" || return 1
    APT_CONFIG="$APT_SESSION_CONFIG" apt-get update || return 1
}
# Never install/update a pre-existing finished system automatically.
if [[ ! -f $SUCCESS ]]; then
    printf "$(paimenos_text '\nWillkommen bei PaimenOS!\nDie Debian-Grundinstallation ist abgeschlossen.\n')"
    printf "$(paimenos_text 'Für Programme und Emulatoren ist jetzt eine Internetverbindung erforderlich.\n')"
    while :; do
        printf "$(paimenos_text '\n1) WLAN/Netzwerk einrichten\n2) PaimenOS-Setup starten\n3) Neu starten\n4) Netzwerkdiagnose / IPv4-Test\n')"
        read -r -p "$(paimenos_text 'Auswahl [2]: ')" choice
        case "$choice" in
            1) nmtui || true; continue ;;
            3) systemctl reboot; exit 0 ;;
            4)
                printf "$(paimenos_text '\nNetzwerkdiagnose und IPv4-APT-Test ...\n')"
                /usr/bin/python3 "$SOURCE/iso/prepare-apt.py" --diagnose || true
                if /usr/bin/python3 "$SOURCE/iso/prepare-apt.py" --apt-config "$APT_SESSION_CONFIG" &&
                    APT_CONFIG="$APT_SESSION_CONFIG" apt-get \
                        -o Acquire::ForceIPv4=true -o Acquire::ForceIPv6=false \
                        -o Acquire::Retries=0 -o Acquire::http::Timeout=10 \
                        -o Acquire::https::Timeout=10 -o APT::Update::Error-Mode=any update; then
                    printf "$(paimenos_text 'IPv4-APT-Test erfolgreich.\n')"
                else
                    printf "$(paimenos_text 'IPv4-APT-Test fehlgeschlagen. Bitte die Fehlermeldungen oben prüfen.\n')"
                fi
                read -r -p "$(paimenos_text 'Mit Enter zurück zum Setup-Menü: ')" diagnostic_answer
                continue
                ;;
            ''|2) ;;
            *) continue ;;
        esac
        # Online bevorzugt; ohne Internet dient das Installationsmedium (Offline-Paketquelle) als Rückfall.
        OFFLINE=0
        if ! online_ready; then
            if ! offline_ready; then
                printf "$(paimenos_text 'Kein Internet und kein Installationsmedium mit Offline-Paketen gefunden.\nBitte Netzwerk einrichten (1) oder den USB-Stick mit PaimenOS einstecken und erneut versuchen.\n')"
                /usr/bin/python3 "$SOURCE/iso/prepare-apt.py" --diagnose || true
                continue
            fi
            OFFLINE=1
            printf "$(paimenos_text '\nKein Internet: Das Setup nutzt die Offline-Pakete vom Installationsmedium.\nDer USB-Stick muss bis zum Ende des Setups stecken bleiben. Emulatoren, Flathub-Apps und Sprachausgabe lassen sich später mit Internet nachinstallieren.\n')"
        fi
        args=()
        if [[ -e /usr/local/lib/paimenos/current ]]; then args+=(--resume); fi
        # App, emulator and PIN prompts are handled by install.sh unchanged.
        offline_env=()
        if (( OFFLINE )); then
            offline_env=(PAIMENOS_OFFLINE=1 PAIMENOS_APP_SOURCE=debian PAIMENOS_EMULATORS=none)
        fi
        if env APT_CONFIG="$APT_SESSION_CONFIG" PAIMENOS_REBOOT=0 "${offline_env[@]}" bash "$SOURCE/install.sh" "${args[@]}"; then
            [[ -f $SUCCESS ]] && break
        fi
        printf "$(paimenos_text '\nSetup noch nicht abgeschlossen. Netzwerk prüfen und erneut starten.\n')"
        printf "$(paimenos_text 'Vorhandene Einstellungen werden beim Fortsetzen beibehalten.\n')"
    done
fi
rm -f "$PENDING"
systemctl disable paimenos-setup.service
printf "$(paimenos_text '\nPaimenOS ist eingerichtet. Der nächste Neustart öffnet die Kinderoberfläche.\n')"
read -r -p "$(paimenos_text 'Jetzt neu starten? [J/n]: ')" answer
case "$answer" in
    n|N) printf "$(paimenos_text 'Neustart später mit sudo reboot.\n')" ;;
    *) systemctl reboot ;;
esac
