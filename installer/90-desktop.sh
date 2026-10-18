#!/bin/bash
# Wird von install.sh in einer gemeinsamen Shell geladen.
# 12. Openbox Autostart & Keybindings
# ---------------------------------------------------------------------------
echo "$(paimenos_text 'Autostart & Openbox Konfiguration aktualisieren ...')"

install_repo_file config/lightdm/lightdm.conf /etc/lightdm/lightdm.conf

# Raspberry Pi OS bringt LightDM-Drop-ins für Autologin und die Pi-Desktop-Sitzung (labwc/Wayfire) mit.
# Sie würden den PaimenOS-Autologin überstimmen; deshalb zur Seite legen (umkehrbar: .paimenos-disabled entfernen).
if [[ "$PAIMENOS_IS_RPI" == 1 && -d /etc/lightdm/lightdm.conf.d ]]; then
    for dropin in /etc/lightdm/lightdm.conf.d/*.conf; do
        [[ -f "$dropin" ]] || continue
        if grep -Eq '^[[:space:]]*(autologin-user|user-session|autologin-session|greeter-session)[[:space:]]*=' "$dropin"; then
            echo "$(paimenos_text 'Hinweis: Raspberry-Pi-OS-Anmeldekonfiguration {value0} wird deaktiviert.' "$dropin")"
            mv -f "$dropin" "${dropin}.paimenos-disabled"
        fi
    done
fi

if [[ -x /usr/sbin/lightdm ]]; then
    printf '%s\n' '/usr/sbin/lightdm' > /etc/X11/default-display-manager
fi

install_repo_file config/openbox/rc.xml "${OPENBOX_DIR}/rc.xml"

mkdir -p /etc/systemd/user
install_repo_file systemd/user/paimenos-session.target /etc/systemd/user/paimenos-session.target

install_repo_file systemd/user/paimenos-osd.service /etc/systemd/user/paimenos-osd.service

install_repo_file systemd/user/paimenos-menu.service /etc/systemd/user/paimenos-menu.service

install_repo_file systemd/user/paimenos-timer.service /etc/systemd/user/paimenos-timer.service

install_repo_file systemd/user/paimenos-media.service /etc/systemd/user/paimenos-media.service

# Trennt unbenutzte Bluetooth-Controller und meldet Leerlauf für paimenos-power.
install_repo_file systemd/user/paimenos-idle.service /etc/systemd/user/paimenos-idle.service

# Sitzungsweit und unabhängig von Bluetooth/Controller; Mausbewegung zeigt ihn wieder.
install_repo_file systemd/user/paimenos-cursor.service /etc/systemd/user/paimenos-cursor.service

install_repo_file config/openbox/autostart "${OPENBOX_DIR}/autostart"

chmod 0755 "${OPENBOX_DIR}/autostart"
chmod 0644 "${OPENBOX_DIR}/rc.xml"

