#!/bin/bash
# Textmenü des PaimenOS-Live-Systems auf tty1: Installieren, WLAN, Konsole, Neustart.
set -uo pipefail
[[ $EUID == 0 ]] || exit 1
REPO_DIR=/opt/paimenos-source
source "$REPO_DIR/installer/language.sh"
MEDIUM=${PAIMENOS_LIVE_MEDIUM:-/run/live/medium}
TITLE="$(paimenos_text 'PaimenOS Live-System')"

# Debian-Installer vom Boot-Medium laden (grafisch, sonst Text) und per kexec starten.
start_installer() {
    local dir kernel initrd
    for dir in "$MEDIUM/install/gtk" "$MEDIUM/install"; do
        [[ -f $dir/vmlinuz && -f $dir/initrd.gz ]] && { kernel=$dir/vmlinuz; initrd=$dir/initrd.gz; break; }
    done
    [[ -n ${kernel:-} ]] || return 1
    command -v kexec >/dev/null || return 1
    local append='preseed/file=/cdrom/preseed.cfg --- quiet'
    # Erst signierte Datei-Variante (Secure Boot), dann die klassische.
    kexec -s -l "$kernel" --initrd="$initrd" --append="$append" 2>/dev/null ||
        kexec -l "$kernel" --initrd="$initrd" --append="$append" 2>/dev/null || return 1
    # Direkt springen: Ein sauberer Shutdown (systemctl kexec) fragt live-boot nach dem Entfernen des Mediums,
    # das der Installer gleich selbst braucht. Das Live-System liegt im RAM, ein sync genügt.
    sync
    kexec -e
}

install_entry() {
    whiptail --title "$TITLE" --yesno "$(paimenos_text 'Der Installer richtet PaimenOS auf diesem Gerät ein. Die Zielplatte wählst du im nächsten Schritt; ihr Inhalt wird dabei gelöscht.\n\nJetzt installieren?')" 14 64 || return 0
    clear
    printf '%s\n' "$(paimenos_text 'Installer wird gestartet ...')"
    start_installer && return 0
    if whiptail --title "$TITLE" --yesno "$(paimenos_text 'Der Installer konnte nicht direkt gestartet werden.\n\nBitte jetzt neu starten und im Bootmenü „Graphical Install“ oder „Install“ wählen. Jetzt neu starten?')" 14 64; then
        systemctl reboot
    fi
}

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
