#!/bin/bash
# Run exclusively in the disposable Debian VM created by wifi-vm.sh.
set -euo pipefail
[[ -f /etc/paimenos-integration-vm ]] || { echo 'Disposable test VM required.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
phase=$1
dns_diagnostics() {
    echo '=== DNS failure diagnostics ==='
    ls -l /etc/resolv.conf
    cat /etc/resolv.conf
    cat /etc/nsswitch.conf
    resolvectl status || true
    cat /etc/systemd/resolved.conf.d/paimenos-install-dns.conf 2>/dev/null || true
    cat /var/lib/paimenos/install-dns.json 2>/dev/null || true
    echo '=== DNS state changes during installation ==='
    cat /root/dns-watch.log 2>/dev/null || true
    journalctl -b --no-pager -u systemd-resolved -u systemd-networkd -n 100 || true
    echo '=== Fresh lookup ==='
    getent ahostsv4 deb.debian.org || true
    resolvectl query -4 deb.debian.org || true
}
trap 'code=$?; echo "Guest test failed ($phase), line $LINENO" >&2; dns_diagnostics; ip -br address; ip route; journalctl -b --no-pager -n 80 -u paimenos-test-ap -u paimenos-wifi-migration -u NetworkManager -u paimenos-packages -u paimenos-parent-web; exit "$code"' ERR
wifi_only() {
    ip route del default dev ens3 || true
    ip -6 route del default dev ens3 || true
    # Retain SSH/DHCP management only. DNS and internet must use Wi-Fi.
    iptables -A OUTPUT -o ens3 -p tcp --sport 22 -j ACCEPT
    iptables -A OUTPUT -o ens3 -p udp --sport 68 --dport 67 -j ACCEPT
    iptables -A OUTPUT -o ens3 -j REJECT
    ip6tables -A OUTPUT -o ens3 -j REJECT
    if command -v resolvectl >/dev/null; then resolvectl flush-caches || true; fi
}
if [[ "$phase" == install ]]; then
    apt-get update
    apt-get install -y wpasupplicant ifupdown hostapd dnsmasq-base iw iptables isc-dhcp-client rfkill
    # Cloud images name these QEMU interfaces consistently after a reboot.
    [[ -d /sys/class/net/ens4 ]] || { ip -br link; exit 1; }
    install -m 0755 /source/tests/integration/wifi-ap.sh /usr/local/sbin/paimenos-test-ap
    cat > /etc/systemd/system/paimenos-test-ap.service <<'UNIT'
[Unit]
DefaultDependencies=no
After=local-fs.target sys-subsystem-net-devices-ens4.device
Requires=sys-subsystem-net-devices-ens4.device
Before=networking.service NetworkManager.service paimenos-wifi-migration.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/paimenos-test-ap
RemainAfterExit=yes
[Install]
WantedBy=networking.service NetworkManager.service
UNIT
    systemctl daemon-reload
    systemctl enable --now paimenos-test-ap.service
    cat > /etc/network/interfaces <<'CONF'
auto lo
iface lo inet loopback
auto wlan0
iface wlan0 inet dhcp
    wpa-ssid PaimenOS-Test
    wpa-psk test-password-only
CONF
    # The cloud management NIC must never become a second internet path.
    mkdir -p /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/99-paimenos-test-management.conf <<'CONF'
[keyfile]
unmanaged-devices=interface-name:ens3;interface-name:ens4
CONF
    ifup wlan0
    # Keep the SSH management link, but force internet traffic through Wi-Fi.
    wifi_only
    printf 'nameserver 192.168.42.1\n' > /etc/resolv.conf
    ip route get 1.1.1.1 | grep -q 'dev wlan0'
    getent ahostsv4 deb.debian.org
    (
        previous=''
        while true; do
            current=$(ls -l /etc/resolv.conf; cat /etc/resolv.conf; resolvectl dns 2>/dev/null || true)
            if [[ "$current" != "$previous" ]]; then
                date -Ins
                printf '%s\n' "$current"
                previous=$current
            fi
            sleep 2
        done
    ) >/root/dns-watch.log 2>&1 &
    cp /etc/network/interfaces /root/interfaces.before
    # Interrupted installation, then the same standard --resume path.
    cp -a /source /root/PaimenOS
    sed -i '1a exit 77' /root/PaimenOS/installer/99-finish.sh
    cd /root/PaimenOS
    if PAIMENOS_PARENT_PIN=4815 PAIMENOS_HARDWARE_PROFILE=low PAIMENOS_APPS=13 PAIMENOS_APP_SOURCE=debian PAIMENOS_EMULATORS=none PAIMENOS_ENABLE_TLP=0 PAIMENOS_ENABLE_ZRAM=0 PAIMENOS_ENABLE_AUTO_UPDATES=0 PAIMENOS_ENABLE_APP_UPDATES=0 bash install.sh; then
        code=0
    else
        code=$?
    fi
    [[ "$code" == 77 ]]
    [[ ! -f /etc/paimenos-laptop.installed ]]
    cmp /root/interfaces.before /etc/network/interfaces
    ip route get 1.1.1.1 | grep -q 'dev wlan0'
    cp /home/kids/.config/paimenos/settings.json /root/settings.before
    cp /source/installer/99-finish.sh installer/99-finish.sh
    PAIMENOS_HARDWARE_PROFILE=low PAIMENOS_APPS=13 PAIMENOS_APP_SOURCE=debian PAIMENOS_EMULATORS=none PAIMENOS_ENABLE_TLP=0 PAIMENOS_ENABLE_ZRAM=0 PAIMENOS_ENABLE_AUTO_UPDATES=0 PAIMENOS_ENABLE_APP_UPDATES=0 bash install.sh --resume
    [[ -f /etc/paimenos-laptop.installed ]]
    cmp /root/settings.before /home/kids/.config/paimenos/settings.json
    cmp /root/interfaces.before /etc/network/interfaces
    [[ -f /var/lib/paimenos/wifi-migration/pending.json ]]
    ip route get 1.1.1.1 | grep -q 'dev wlan0'
    getent ahostsv4 deb.debian.org
    apt-get update
    curl --fail --retry 60 --retry-delay 2 --retry-connrefused --max-time 10 http://127.0.0.1/login >/dev/null
elif [[ "$phase" == reboot ]]; then
    # Test the actual boot ordering, not a manual invoke of the migration.
    [[ -f /var/lib/paimenos/wifi-migration/completed.json ]]
    [[ ! -f /var/lib/paimenos/wifi-migration/pending.json ]]
    if [[ "${SECOND_BOOT:-0}" != 1 ]]; then systemctl is-active --quiet paimenos-wifi-migration.service; fi
    systemctl is-active --quiet NetworkManager.service
    for attempt in {1..60}; do
        state=$(LC_ALL=C nmcli -g GENERAL.STATE device show wlan0)
        [[ "${state%% *}" == 100 ]] && break
        sleep 1
    done
    [[ "${state%% *}" == 100 ]]
    ! ifquery wlan0 >/dev/null 2>&1
    nmcli -g GENERAL.CONNECTION device show wlan0 | grep -q 'PaimenOS Debian WLAN'
    wifi_only
    nmcli -g IP4.DNS device show wlan0 | grep -q '192.168.42.1'
    ip route get 1.1.1.1 | grep -q 'dev wlan0'
    getent ahostsv4 deb.debian.org
    apt-get update
    curl --fail --retry 60 --retry-delay 2 --retry-connrefused --max-time 10 http://127.0.0.1/login >/dev/null
    systemctl is-active --quiet paimenos-wifi.service
    echo 'PASS: fresh installation, interrupted/resumed setup and reboot Wi-Fi autoconnect'
else exit 2; fi
