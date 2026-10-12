#!/usr/bin/env bash
# Wiederverwendbare Test-VM (Debian 13, QEMU/KVM) für PaimenOS. Entwicklungs-Helfer, nicht Teil des Releases.
#
#   tools/vm.sh create            Image laden, VM-Platte und Cloud-Init anlegen, Snapshot "clean"
#   tools/vm.sh start             VM mit sichtbarem Fenster starten (Hintergrund)
#   tools/vm.sh install           Arbeitsverzeichnis in die VM kopieren und install.sh ausführen
#                                 (PAIMENOS_PARENT_PIN=… PAIMENOS_APPS=… usw. vorher setzen = ohne Rückfragen)
#   tools/vm.sh deploy [ARGS]     Code auf eine bereits installierte VM übertragen (wie make deploy)
#   tools/vm.sh ssh [BEFEHL]      Shell in der VM
#   tools/vm.sh stop              VM sauber herunterfahren
#   tools/vm.sh snapshot NAME     Zustand sichern (VM muss gestoppt sein)
#   tools/vm.sh reset [NAME]      Zurück zu einem Snapshot (Standard: clean)
#   tools/vm.sh status | destroy
#
# Daten liegen in $PAIMENOS_VM_DIR (Standard ~/VMs/paimenos). SSH-Alias: paimenos-vm
# (localhost:2222), Eltern-Web: http://localhost:8080, Konsolen-Login john / paimenos.
# Umgebung: PAIMENOS_VM_RAM (MB, 4096), PAIMENOS_VM_CPUS (4), PAIMENOS_VM_DISK (40G),
# PAIMENOS_VM_DISPLAY (gtk, auch sdl oder none).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
dir="${PAIMENOS_VM_DIR:-$HOME/VMs/paimenos}"
ram="${PAIMENOS_VM_RAM:-4096}"
cpus="${PAIMENOS_VM_CPUS:-4}"
disk_size="${PAIMENOS_VM_DISK:-40G}"
display="${PAIMENOS_VM_DISPLAY:-gtk}"
image_url='https://cloud.debian.org/images/cloud/trixie/latest'
image_name='debian-13-generic-amd64.qcow2'
key="$HOME/.ssh/id_ed25519_paimenos_vm"
ssh_port=2222
web_port=8080
disk="$dir/disk.qcow2"
pidfile="$dir/qemu.pid"
remote_dir='paimenos-deploy'

running() { [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; }
need_stopped() { ! running || { echo "Die VM läuft. Zuerst: tools/vm.sh stop" >&2; exit 1; }; }
vm_ssh() { ssh -F /dev/null -i "$key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -p "$ssh_port" john@localhost "$@"; }

wait_for_ssh() {
    echo "==> Warte auf SSH"
    for _ in $(seq 1 120); do
        vm_ssh true 2>/dev/null && return 0
        sleep 2
    done
    echo "SSH nicht erreichbar. Fenster der VM prüfen." >&2
    exit 1
}

copy_tree() {
    python3 -B tools/deploy.py --check >/dev/null
    git ls-files -z --cached --others --exclude-standard \
        | tar --null --files-from=- -czf - \
        | vm_ssh "sudo rm -rf ~/${remote_dir} && mkdir ~/${remote_dir} && tar xzf - -C ~/${remote_dir}"
}

cmd_create() {
    [ ! -e "$disk" ] || { echo "VM existiert bereits ($dir). Zum Neuanlegen: tools/vm.sh destroy" >&2; exit 1; }
    mkdir -p "$dir/base"
    if [ ! -s "$dir/base/$image_name" ]; then
        echo "==> Lade $image_name"
        curl -fL --progress-bar -o "$dir/base/$image_name.part" "$image_url/$image_name"
        curl -fsS -o "$dir/base/SHA512SUMS" "$image_url/SHA512SUMS"
        expected=$(awk -v n="$image_name" '$2 == n {print $1}' "$dir/base/SHA512SUMS")
        actual=$(sha512sum "$dir/base/$image_name.part" | cut -d' ' -f1)
        [ -n "$expected" ] && [ "$expected" = "$actual" ] || { echo "Prüfsumme des Images stimmt nicht." >&2; exit 1; }
        mv "$dir/base/$image_name.part" "$dir/base/$image_name"
    fi
    if [ ! -f "$key" ]; then
        ssh-keygen -q -t ed25519 -N '' -C paimenos-vm -f "$key"
    fi
    echo "==> Lege Platte an ($disk_size)"
    qemu-img convert -O qcow2 "$dir/base/$image_name" "$disk"
    qemu-img resize -q "$disk" "$disk_size"

    seed="$dir/seed"
    mkdir -p "$seed"
    cat > "$seed/user-data" <<EOF
#cloud-config
hostname: paimenos-vm
users:
  - name: john
    groups: [sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: false
    plain_text_passwd: paimenos
    ssh_authorized_keys:
      - $(cat "$key.pub")
ssh_pwauth: false
package_update: true
packages: [git, rsync]
EOF
    printf 'instance-id: paimenos-vm\nlocal-hostname: paimenos-vm\n' > "$seed/meta-data"
    genisoimage -quiet -output "$dir/seed.iso" -volid cidata -joliet -rock "$seed/user-data" "$seed/meta-data"

    if ! grep -q '^Host paimenos-vm$' "$HOME/.ssh/config" 2>/dev/null; then
        printf '\nHost paimenos-vm\n    HostName localhost\n    Port %s\n    User john\n    IdentityFile %s\n    IdentitiesOnly yes\n    StrictHostKeyChecking no\n    UserKnownHostsFile /dev/null\n    LogLevel ERROR\n' \
            "$ssh_port" "$key" >> "$HOME/.ssh/config"
    fi

    cmd_start
    wait_for_ssh
    echo "==> Warte auf Cloud-Init"
    vm_ssh 'sudo cloud-init status --wait' >/dev/null || true
    cmd_stop
    qemu-img snapshot -c clean "$disk"
    echo "==> Fertig. Snapshot \"clean\" gesichert. Weiter mit: tools/vm.sh start && tools/vm.sh install"
}

cmd_start() {
    running && { echo "VM läuft bereits."; return; }
    [ -e "$disk" ] || { echo "Keine VM. Zuerst: tools/vm.sh create" >&2; exit 1; }
    local graphics=(-display "$display" -device virtio-vga)
    [ "$display" = none ] && graphics=(-display none -vga none)
    # Audio über PipeWire, damit sich die Sprachausgabe des Menüs anhören lässt.
    qemu-system-x86_64 -name paimenos-vm -machine q35,accel=kvm -cpu host -smp "$cpus" -m "$ram" \
        -drive file="$disk",if=virtio,format=qcow2 \
        -drive file="$dir/seed.iso",if=virtio,format=raw,readonly=on \
        -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${ssh_port}-:22,hostfwd=tcp:127.0.0.1:${web_port}-:80 \
        -device virtio-net-pci,netdev=n0 \
        -audiodev pipewire,id=snd0 -device intel-hda -device hda-duplex,audiodev=snd0 \
        -device qemu-xhci -device usb-tablet -device usb-kbd \
        "${graphics[@]}" \
        -pidfile "$pidfile" -daemonize
    echo "==> VM gestartet (SSH: localhost:$ssh_port, Web: http://localhost:$web_port)"
}

cmd_stop() {
    running || { echo "VM läuft nicht."; return; }
    vm_ssh 'sudo systemctl poweroff' 2>/dev/null || true
    for _ in $(seq 1 60); do running || { echo "==> VM gestoppt"; return; }; sleep 1; done
    echo "Herunterfahren dauert zu lange, beende QEMU." >&2
    kill "$(cat "$pidfile")"
}

case "${1:-}" in
    create) cmd_create ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    install)
        running || { echo "VM läuft nicht. Zuerst: tools/vm.sh start" >&2; exit 1; }
        wait_for_ssh; copy_tree
        # PAIMENOS_PARENT_PIN, _APPS, _APP_SOURCE, _HARDWARE_PROFILE, _EMULATORS usw. werden für
        # ein Setup ohne Rückfragen weitergereicht (PAIMENOS_VM_* bleibt lokal).
        vars=$(env | grep -E '^PAIMENOS_[A-Z_]+=' | grep -v '^PAIMENOS_VM_' | while IFS= read -r line; do printf ' %q' "$line"; done || true)
        tty_flag=(); [ -t 0 ] && tty_flag=(-t)
        vm_ssh "${tty_flag[@]}" "cd ~/${remote_dir} && sudo env${vars} bash install.sh" ;;
    deploy)
        shift
        running || { echo "VM läuft nicht. Zuerst: tools/vm.sh start" >&2; exit 1; }
        wait_for_ssh; copy_tree
        vm_ssh -t "cd ~/${remote_dir} && sudo python3 -B tools/deploy.py$( [ "$#" -gt 0 ] && printf ' %q' "$@" )" ;;
    ssh) shift; vm_ssh -t "$@" ;;
    snapshot)
        need_stopped; [ -n "${2:-}" ] || { echo "Name fehlt." >&2; exit 1; }
        qemu-img snapshot -d "$2" "$disk" 2>/dev/null || true
        qemu-img snapshot -c "$2" "$disk"; echo "==> Snapshot $2 gesichert" ;;
    reset)
        need_stopped; qemu-img snapshot -a "${2:-clean}" "$disk"; echo "==> Zurück auf ${2:-clean}" ;;
    status)
        running && echo "läuft (PID $(cat "$pidfile"))" || echo "gestoppt"
        # Snapshots lassen sich nur bei gestoppter VM lesen (Schreibsperre).
        if ! running && [ -e "$disk" ]; then qemu-img snapshot -l "$disk"; fi ;;
    destroy)
        need_stopped; read -r -p "VM-Daten in $dir löschen (Image bleibt)? [j/N] " answer
        [ "$answer" = j ] && rm -rf "$disk" "$dir/seed" "$dir/seed.iso" && echo "==> Gelöscht" ;;
    *) sed -n "2,18p" "$0"; exit 1 ;;
esac
