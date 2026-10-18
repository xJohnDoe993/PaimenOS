#!/bin/bash
# Wird von install.sh in einer gemeinsamen Shell geladen.
# 2a. Laptop-Betrieb, Arbeitsspeicher und Wartung (Debian 12/13)
# Eigene /etc/tlp.conf und andere ZRAM-Verwalter bleiben erhalten.
# ---------------------------------------------------------------------------
echo "$(paimenos_text 'Laptop-Werkzeuge und sichere Energiesparregeln ...')"

# Hardware-Profil: grobe Grafik-/Leistungsstufe für Emulatoren, Luanti, Webapps und Menü.
# Später änderbar mit: sudo paimenos-hardware-profile
HARDWARE_PROFILE_FILE=/etc/paimenos/hardware-profile
HARDWARE_PROFILES=(ultra-low low medium high)
hardware_profile_tool() {
    /usr/bin/python3 -I /usr/local/lib/paimenos/current/run.py hardware_profile "$@"
}
HARDWARE_PROFILE="${PAIMENOS_HARDWARE_PROFILE:-}"
if [[ -z "$HARDWARE_PROFILE" && "$RESUME" == 1 && -f "$HARDWARE_PROFILE_FILE" ]]; then
    HARDWARE_PROFILE=$(hardware_profile_tool --current)
    echo "$(paimenos_text 'Vorhandenes Hardware-Profil wird beibehalten: {value0}' "$HARDWARE_PROFILE")"
fi
if [[ -z "$HARDWARE_PROFILE" ]]; then
    HARDWARE_SUGGESTION=$(hardware_profile_tool --detect || echo low)
    echo
    echo "$(paimenos_text 'Hardware-Profil wählen (steuert Grafik- und Leistungseinstellungen der Apps):')"
    for index in "${!HARDWARE_PROFILES[@]}"; do
        echo "  $((index + 1))) ${HARDWARE_PROFILES[$index]} – $(hardware_profile_tool --describe "${HARDWARE_PROFILES[$index]}")"
    done
    echo "$(paimenos_text 'Vorschlag für dieses Gerät: {value0}' "$HARDWARE_SUGGESTION")"
    read -r -p "$(paimenos_text 'Profil (1-4, Enter = Vorschlag): ')" HARDWARE_CHOICE
    case "$HARDWARE_CHOICE" in
        '') HARDWARE_PROFILE=$HARDWARE_SUGGESTION ;;
        [1-4]) HARDWARE_PROFILE=${HARDWARE_PROFILES[$((HARDWARE_CHOICE - 1))]} ;;
        *) echo "$(paimenos_text 'Ungültige Auswahl.')" >&2; exit 1 ;;
    esac
fi
case "$HARDWARE_PROFILE" in
    ultra-low|low|medium|high) ;;
    *) echo "$(paimenos_text 'PAIMENOS_HARDWARE_PROFILE muss ultra-low, low, medium oder high sein.')" >&2; exit 1 ;;
esac
install -d -m 0755 /etc/paimenos
printf '%s\n' "$HARDWARE_PROFILE" > "${HARDWARE_PROFILE_FILE}.new"
chmod 0644 "${HARDWARE_PROFILE_FILE}.new"
mv -f "${HARDWARE_PROFILE_FILE}.new" "$HARDWARE_PROFILE_FILE"
echo "$(paimenos_text 'Hardware-Profil: {value0}' "$HARDWARE_PROFILE")"

package_installed() {
    [[ "$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null || true)" == "install ok installed" ]]
}

install_available_firmware() {
    local package candidate
    local -a available=()
    for package in "$@"; do
        candidate=$(LC_ALL=C apt-cache policy "$package" 2>/dev/null | awk '/Candidate:/ {print $2; exit}')
        if [[ -n "$candidate" && "$candidate" != "(none)" ]]; then
            available+=("$package")
        else
            echo "$(paimenos_text 'Hinweis: {value0} nicht verfügbar; ggf. Debian non-free-firmware aktivieren.' "${package}")" >&2
        fi
    done
    if (( ${#available[@]} )); then
        apt-get install -y --no-install-recommends "${available[@]}" || \
            echo "$(paimenos_text 'Hinweis: optionale Firmware konnte nicht vollständig installiert werden.')" >&2
    fi
}

apt-get install -y --no-install-recommends \
    upower acpi rfkill ethtool v4l-utils lm-sensors smartmontools fwupd \
    unattended-upgrades ca-certificates xserver-xorg-input-libinput

# Firmware nur aus bereits eingerichteten Debian-Paketquellen beziehen.
if [[ "$PAIMENOS_IS_ARM" == 1 ]]; then
    # Kein x86-Microcode und keine Intel-WLAN-Firmware; Raspberry Pi braucht Broadcom-WLAN und Boot-Firmware.
    install_available_firmware firmware-misc-nonfree firmware-realtek firmware-atheros
    if [[ "$PAIMENOS_IS_RPI" == 1 ]]; then
        install_available_firmware firmware-brcm80211 raspi-firmware
    fi
else
    install_available_firmware firmware-misc-nonfree firmware-iwlwifi firmware-realtek firmware-atheros
    case "$(awk '/vendor_id/ {print $3; exit}' /proc/cpuinfo)" in
        GenuineIntel) install_available_firmware intel-microcode ;;
        AuthenticAMD) install_available_firmware amd64-microcode ;;
    esac
fi

if [[ "$PAIMENOS_ENABLE_TLP" == 1 ]]; then
    mkdir -p /etc/tlp.d
    install_repo_file config/tlp/01-paimenos.conf /etc/tlp.d/01-paimenos.conf
    # Regeln vor Installation schreiben: auch ein Start durch das Paket nutzt sie.
    # Debian löst den Paketkonflikt mit power-profiles-daemon selbst auf.
    apt-get install -y --no-install-recommends tlp tlp-rdw
    systemctl enable tlp.service
    # Bereits aktive fremde Optimierer dürfen die TLP-Regeln nicht überschreiben.
    for power_service in auto-cpufreq.service tuned.service; do
        if systemctl is-active --quiet "$power_service"; then
            echo "$(paimenos_text 'Hinweis: {value0} wird zugunsten von TLP deaktiviert.' "${power_service}")"
            systemctl disable --now "$power_service"
        fi
    done
    # Einstellungen werden beim folgenden Neustart angewendet. Kein tlp start
    # während Kamera, Medien oder eine bestehende Kindersitzung aktiv sein könnten.
fi

if [[ "$PAIMENOS_ENABLE_ZRAM" == 1 ]]; then
    ZRAM_OTHER_MANAGER=false
    if package_installed systemd-zram-generator || package_installed zram-config || \
        [[ -f /etc/systemd/zram-generator.conf || -d /etc/systemd/zram-generator.conf.d ]] || \
        [[ -f /usr/lib/systemd/zram-generator.conf || -d /usr/lib/systemd/zram-generator.conf.d ]]; then
        ZRAM_OTHER_MANAGER=true
    elif compgen -G '/sys/block/zram*' >/dev/null && ! package_installed zram-tools; then
        ZRAM_OTHER_MANAGER=true
    fi
    if [[ "$ZRAM_OTHER_MANAGER" == true ]]; then
        echo "$(paimenos_text 'Hinweis: vorhandene ZRAM-Verwaltung wird beibehalten.')"
    else
        apt-get install -y --no-install-recommends zram-tools
        # Paketinstallation kann den Dienst bereits starten. Deshalb keinen
        # laufenden Swap abschalten/neustarten; neue Werte gelten ab Neustart.
        if [[ ! -f /etc/default/zramswap ]] || \
            grep -q '^# PaimenOS managed ZRAM$' /etc/default/zramswap || \
            ! grep -Eq '^[[:space:]]*[^#[:space:]]' /etc/default/zramswap; then
            mkdir -p /etc/default
            install_repo_file config/zram/zramswap /etc/default/zramswap
        else
            echo "$(paimenos_text 'Hinweis: eigene ZRAM-Konfiguration wird beibehalten.')"
        fi
        systemctl enable zramswap.service
    fi
fi

echo "$(paimenos_text 'Sicherheitsupdates, SSD-Wartung und Eingabegeräte ...')"
if [[ "$PAIMENOS_ENABLE_AUTO_UPDATES" == 1 ]]; then
    mkdir -p /etc/apt/apt.conf.d
    install_repo_file config/apt/90-paimenos-updates /etc/apt/apt.conf.d/90-paimenos-updates
    systemctl enable apt-daily.timer apt-daily-upgrade.timer
fi

# Wöchentliches TRIM über die Debian-Unit, ohne permanente discard-Mountoption.
systemctl enable fstrim.timer
mkdir -p /etc/systemd/journald.conf.d /etc/X11/xorg.conf.d /usr/local/share/paimenos
install_repo_file config/journald/10-paimenos.conf /etc/systemd/journald.conf.d/10-paimenos.conf
install_repo_file config/xorg/40-paimenos-touchpad.conf /etc/X11/xorg.conf.d/40-paimenos-touchpad.conf
install_repo_file docs/laptop-setup.txt /usr/local/share/paimenos/laptop-setup.txt

# Verifizierung kritischer Python-Pakete
/usr/bin/python3 -c "import flask, dbus, evdev; from gi.repository import GLib; from PyQt5.QtWidgets import QApplication" >/dev/null 2>&1 || {
    echo "$(paimenos_text 'FEHLER: Flask, PyQt5 oder die Bluetooth-/Controller-Python-Pakete fehlen.')" >&2
    exit 1
}

