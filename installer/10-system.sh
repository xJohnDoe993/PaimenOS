#!/bin/bash
# Wird von install.sh in einer gemeinsamen Shell geladen.
# 1. Paketquellen & Basis-System (Alle Abhängigkeiten)
# ---------------------------------------------------------------------------
echo "$(paimenos_text 'Paketlisten aktualisieren ...')"
apt-get update

# Quellen nur für die installierte Debian-Version ergänzen.
# Ubuntu-PPAs und fremde Debian-Releases werden nicht hinzugefügt.
. /etc/os-release
if [[ "${ID:-}" != debian || ! "${VERSION_CODENAME:-}" =~ ^(bookworm|trixie)$ ]]; then
    echo "$(paimenos_text 'FEHLER: Dieses Setup erwartet Debian 12 (bookworm) oder 13 (trixie).')" >&2
    exit 1
fi
apt-get install -y --no-install-recommends python3 debian-archive-keyring ca-certificates
install_runtime
mkdir -p /usr/local/share/paimenos /var/lib/paimenos
# Auswahl über /dev/tty funktioniert auch beim Start über eine Pipe.
EMULATOR_SELECTION=$(/usr/bin/python3 -I /usr/local/lib/paimenos/current/run.py emulator_service --select)
echo "$(paimenos_text 'Emulator-Auswahl: {value0} (vorhandene Emulatoren bleiben erhalten).' "$EMULATOR_SELECTION")"

/usr/bin/python3 /usr/local/lib/paimenos/current/tools/configure-debian-components.py "$VERSION_CODENAME"
apt-get update

echo "$(paimenos_text 'System, Plymouth, X11, Python & Tools installieren ...')"
/usr/bin/python3 "${REPO_DIR}/tools/configure-dns.py" prepare
if apt-get install -y \
    pipewire \
    pipewire-audio \
    pipewire-alsa \
    pipewire-pulse \
    wireplumber \
    alsa-utils python3-venv \
    libspeechd2 speech-dispatcher speech-dispatcher-espeak-ng \
    firmware-linux-free bluez libspa-0.2-bluetooth \
    xserver-xorg \
    x11-xserver-utils unclutter-xfixes \
    openbox \
    lightdm \
    python3 \
    python3-pyqt5 python3-xlib python3-evdev \
    python3-pyqt5.qtsvg qttranslations5-l10n \
    python3-pyqt5.qtmultimedia libqt5multimedia5-plugins \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav \
    qt5-image-formats-plugins \
    python3-flask python3-waitress python3-dbus python3-gi \
    feh \
    dbus-x11 \
    dbus-user-session \
    libpam-systemd \
    polkitd \
    network-manager wpasupplicant \
    network-manager-gnome \
    cups cups-bsd python3-cups \
    xterm \
    pciutils \
    usbutils \
    brightnessctl \
    fonts-dejavu \
    firefox-esr \
    systemd-resolved \
    udisks2 \
    exfatprogs \
    ntfs-3g \
    plymouth \
    plymouth-themes \
    tar \
    xz-utils \
    wget \
    curl \
    iproute2 \
    fonts-noto-color-emoji \
    fonts-font-awesome \
    imagemagick \
    librsvg2-bin \
    whiptail \
    procps \
    util-linux; then
    /usr/bin/python3 "${REPO_DIR}/tools/configure-dns.py" recover
    /usr/bin/python3 /usr/local/lib/paimenos/current/tools/install-update-packages.py
else
    /usr/bin/python3 "${REPO_DIR}/tools/configure-dns.py" recover || true
    echo "$(paimenos_text 'FEHLER: Basispakete konnten nicht vollständig installiert werden.')" >&2
    exit 1
fi
