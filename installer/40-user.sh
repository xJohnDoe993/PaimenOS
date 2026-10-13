#!/bin/bash
# Wird von install.sh in einer gemeinsamen Shell geladen.
# 4. Benutzer, Bibata-Cursor & Hardware-Skripte
# ---------------------------------------------------------------------------
echo "$(paimenos_text 'Benutzer '"'"'{value0}'"'"', Ordnerstruktur & Bibata-Cursor ...' "${KIDS_USER}")"

if ! id "${KIDS_USER}" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "Kids" "${KIDS_USER}"
fi

if ! getent group input >/dev/null 2>&1; then
    groupadd --system input
fi
if ! getent group bluetooth >/dev/null 2>&1; then
    groupadd --system bluetooth
fi
for group in audio video input render netdev bluetooth; do
    if getent group "${group}" >/dev/null 2>&1; then
        usermod -aG "${group}" "${KIDS_USER}" || true
    fi
done

# Controller-Zugriff für kids, auch bei nachträglichem Bluetooth-Verbinden.
mkdir -p /etc/udev/rules.d
install_repo_file config/udev/99-paimenos-controller.rules /etc/udev/rules.d/99-paimenos-controller.rules
udevadm control --reload-rules
udevadm trigger --action=change --subsystem-match=input

mkdir -p "${OPENBOX_DIR}" "${CONFIG_DIR}" "${STATE_DIR}"
chmod 0700 "${CONFIG_DIR}" "${STATE_DIR}"
mkdir -p "${ICONS_DIR}"
mkdir -p "${KIDS_HOME}/.mozilla/paimenos-webapps"
mkdir -p "${KIDS_HOME}/.config/gtk-3.0"

if [[ ! -d "/usr/share/icons/Bibata-Original-Ice" && "${PAIMENOS_OFFLINE:-0}" != 1 ]]; then
    echo "$(paimenos_text 'Lade Bibata-Original-Ice Cursor herunter ...')"
    wget -q https://github.com/ful1e5/Bibata_Cursor/releases/download/v2.0.7/Bibata-Original-Ice.tar.xz -O /tmp/Bibata-Original-Ice.tar.xz  || echo "$(paimenos_text 'Download fehlgeschlagen, fahre fort...')"
    if [[ -f /tmp/Bibata-Original-Ice.tar.xz ]]; then
        tar -xf /tmp/Bibata-Original-Ice.tar.xz -C /usr/share/icons/ || true
        rm -f /tmp/Bibata-Original-Ice.tar.xz
    fi
fi

mkdir -p /usr/share/icons/default
install_repo_file config/cursor/index.theme /usr/share/icons/default/index.theme

install_repo_file config/desktop/Xresources "${KIDS_HOME}/.Xresources"

install_repo_file config/desktop/gtkrc-2.0 "${KIDS_HOME}/.gtkrc-2.0"

install_repo_file config/desktop/gtk3-settings.ini "${KIDS_HOME}/.config/gtk-3.0/settings.ini"

# Sitzungsweites OSD für alle Lautstärke- und Helligkeitstasten.

