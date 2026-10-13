#!/bin/bash
# Prepare only the Debian foundation and root-owned interactive installer.
set -Eeuo pipefail
[[ $EUID == 0 && -f /run/paimenos-live-build ]] || exit 1
SOURCE=/opt/paimenos-source
install -D -m 0755 "$SOURCE/iso/setup-after-install.sh" /usr/local/sbin/paimenos-setup-after-install
install -D -m 0644 "$SOURCE/iso/paimenos-setup.service" /etc/systemd/system/paimenos-setup.service
mkdir -p /etc/systemd/system/getty@tty1.service.d
# Auf tty1 läuft entweder das Live-Menü (Live-System) oder das Setup nach der Installation, nie die Anmeldung.
printf '[Unit]\nConditionPathExists=!/var/lib/paimenos/setup-pending\nConditionPathExists=!/run/live/medium\n' > /etc/systemd/system/getty@tty1.service.d/paimenos-setup.conf
install -D -m 0755 "$SOURCE/iso/live-menu.sh" /usr/local/sbin/paimenos-live-menu
install -D -m 0644 "$SOURCE/iso/paimenos-live-menu.service" /etc/systemd/system/paimenos-live-menu.service
# Ohne /run/live/medium (installiertes System) startet die Unit nicht.
/usr/bin/systemctl enable paimenos-live-menu.service
# Enabled only by Debian Installer's late_command, never in the live session.
# No kids account, parent PIN, PaimenOS runtime, apps or emulator selection here.
/usr/bin/systemctl enable NetworkManager.service
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/10-paimenos-base.conf <<'NETWORK'
[ifupdown]
managed=true
NETWORK
printf 'auto lo\niface lo inet loopback\n' > /etc/network/interfaces
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id /etc/ssh/ssh_host_*
rm -f /run/paimenos-live-build
