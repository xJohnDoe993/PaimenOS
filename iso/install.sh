#!/bin/bash
# PaimenOS-Installer im Live-System (ersetzt den Debian-Installer).
#
# Ablauf: Sprache, Tastatur, Zeitzone, WLAN, Zielplatte, Administrator abfragen, die ganze Platte
# einrichten, das Live-Abbild (filesystem.squashfs) darauf kopieren, Bootloader installieren und
# ohne Firmware-Neustart per kexec in das installierte System springen. Dort startet auf tty1 das
# PaimenOS-Setup (paimenos-setup.service, iso/setup-after-install.sh) wie bisher.
# Kann kexec nicht starten, genügt ein normaler Neustart: Das Setup startet dann beim Hochfahren.
set -uo pipefail

REPO_DIR=${REPO_DIR:-/opt/paimenos-source}
MEDIUM=${PAIMENOS_LIVE_MEDIUM:-/run/live/medium}
TARGET=${PAIMENOS_TARGET:-/target}
SQUASH_MOUNT=/run/paimenos-squashfs
LOG=${PAIMENOS_INSTALL_LOG:-/var/log/paimenos-install.log}
MIN_DISK_BYTES=$((16 * 1024 * 1024 * 1024))
HOSTNAME_NEW=paimenos
LIVE_PACKAGES=(live-boot live-boot-initramfs-tools live-config live-config-systemd live-tools)

# ---------- reine Hilfsfunktionen (in tests/test_iso_install.py geprüft) ----------

locale_for() {
    case "$1" in
        en) printf 'en_US.UTF-8' ;;
        *) printf 'de_DE.UTF-8' ;;
    esac
}

# Partitionsname: NVMe/MMC/Loop brauchen ein "p" vor der Nummer.
partition_name() {
    local disk=$1 number=$2
    if [[ $disk =~ [0-9]$ ]]; then printf '%sp%s' "$disk" "$number"; else printf '%s%s' "$disk" "$number"; fi
}

valid_username() {
    [[ $1 =~ ^[a-z][a-z0-9_-]{0,30}$ ]] && [[ $1 != kids && $1 != root && $1 != live ]]
}

# Zeilen von "lsblk -dnbP -o NAME,SIZE,TYPE,MODEL" lesen und nur geeignete Zielplatten ausgeben:
# "NAME|GRÖSSE|MODELL". Ausgeschlossen: das Installationsmedium ($1), Loop-/RAM-/zram-/optische
# Geräte und alles unter 16 GB.
filter_disks() {
    local medium_disk=$1 line NAME='' SIZE='' TYPE='' MODEL=''
    while IFS= read -r line; do
        NAME=''; SIZE=0; TYPE=''; MODEL=''
        eval "$line" 2>/dev/null || continue
        [[ $TYPE == disk ]] || continue
        case "$NAME" in /dev/loop*|/dev/ram*|/dev/zram*|/dev/sr*|/dev/fd*|/dev/nbd*) continue ;; esac
        [[ $NAME == "$medium_disk" ]] && continue
        (( SIZE >= MIN_DISK_BYTES )) || continue
        printf '%s|%s|%s\n' "$NAME" "$SIZE" "${MODEL:-?}"
    done
}

human_size() {
    awk -v b="$1" 'BEGIN { printf "%.0f GB", b / 1000000000 }'
}

# Der Datenträger (ganze Platte), auf dem das Installationsmedium liegt.
medium_disk() {
    local source parent
    source=$(findmnt -no SOURCE "$MEDIUM" 2>/dev/null | head -n 1) || return 0
    [[ -n $source ]] || return 0
    parent=$(lsblk -no PKNAME "$source" 2>/dev/null | head -n 1)
    if [[ -n $parent ]]; then printf '/dev/%s' "$parent"; else printf '%s' "$source"; fi
}

fstab_text() {
    local root_uuid=$1 efi_uuid=${2:-}
    printf '# /etc/fstab: von PaimenOS beim Installieren erzeugt\n'
    printf 'UUID=%s / ext4 errors=remount-ro 0 1\n' "$root_uuid"
    [[ -z $efi_uuid ]] || printf 'UUID=%s /boot/efi vfat umask=0077 0 2\n' "$efi_uuid"
}

keyboard_text() {
    local layout=${1%%:*} variant=''
    [[ $1 == *:* ]] && variant=${1#*:}
    printf 'XKBMODEL="pc105"\nXKBLAYOUT="%s"\nXKBVARIANT="%s"\nXKBOPTIONS=""\nBACKSPACE="guess"\n' "$layout" "$variant"
}

hosts_text() {
    printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n::1\tlocalhost ip6-localhost ip6-loopback\n' "$1"
}

sfdisk_script() {
    if [[ $1 == efi ]]; then
        printf 'label: gpt\nsize=512MiB, type=U, name="EFI"\ntype=L, name="PaimenOS"\n'
    else
        printf 'label: gpt\nsize=1MiB, type=21686148-6449-6E6F-744E-656564454649, name="BIOS"\ntype=L, name="PaimenOS"\n'
    fi
}

# ---------- Oberfläche ----------

TITLE=''
ask_menu() {  # Titeltext, Standard, dann tag/Beschriftung-Paare
    local text=$1 default=$2
    shift 2
    whiptail --title "$TITLE" --notags --default-item "$default" --menu "$text" 20 70 10 "$@" 3>&1 1>&2 2>&3
}

choose_language() {
    local choice
    choice=$(whiptail --title 'PaimenOS' --notags --default-item "${PAIMENOS_LANGUAGE:-de}" \
        --menu 'Sprache / Language' 12 50 2 de 'Deutsch' en 'English' 3>&1 1>&2 2>&3) || return 1
    printf '%s' "$choice"
}

choose_keyboard() {
    local default=$1
    ask_menu "$(paimenos_text 'Welches Tastatur-Layout hat dein Gerät?')" "$default" \
        de "$(paimenos_text 'Deutsch')" \
        us "$(paimenos_text 'Englisch (US)')" \
        gb "$(paimenos_text 'Englisch (UK)')" \
        fr "$(paimenos_text 'Französisch')" \
        es "$(paimenos_text 'Spanisch')" \
        it "$(paimenos_text 'Italienisch')" \
        'ch:de' "$(paimenos_text 'Schweiz (Deutsch)')" \
        at "$(paimenos_text 'Österreich')" \
        pl "$(paimenos_text 'Polnisch')" \
        tr "$(paimenos_text 'Türkisch')" \
        nl "$(paimenos_text 'Niederländisch')" \
        se "$(paimenos_text 'Schwedisch')"
}

choose_timezone() {
    local default=$1 region zone regions=() zones=() name
    local default_region=${default%%/*}
    while IFS= read -r name; do regions+=("$name" "$name"); done < <(timedatectl list-timezones 2>/dev/null | cut -d/ -f1 | sort -u | grep -vx -e UTC -e Etc)
    ((${#regions[@]})) || { printf '%s' "$default"; return 0; }
    while :; do
        region=$(ask_menu "$(paimenos_text 'In welcher Weltregion liegt dein Gerät?')" "$default_region" "${regions[@]}") || return 1
        zones=()
        while IFS= read -r name; do zones+=("$name" "${name#*/}"); done < <(timedatectl list-timezones 2>/dev/null | grep "^$region/")
        zone=$(ask_menu "$(paimenos_text 'Welche Zeitzone?')" "$default" "${zones[@]}") && { printf '%s' "$zone"; return 0; }
    done
}

choose_disk() {
    local entries=() line name size model disk
    while IFS='|' read -r name size model; do
        entries+=("$name" "$model, $(human_size "$size") ($name)")
    done < <(lsblk -dnbP -o NAME,SIZE,TYPE,MODEL | filter_disks "$(medium_disk)")
    if ((${#entries[@]} == 0)); then
        whiptail --title "$TITLE" --msgbox "$(paimenos_text 'Es wurde keine geeignete Festplatte gefunden (mindestens 16 GB, nicht der USB-Stick).')" 10 64
        return 1
    fi
    disk=$(ask_menu "$(paimenos_text 'Auf welche Festplatte soll PaimenOS?\n\nDie ganze Platte wird benutzt. Eine Aufteilung in Partitionen ist nicht nötig.')" "${entries[0]}" "${entries[@]}") || return 1
    printf '%s' "$disk"
}

describe_disk() {
    lsblk -dnb -o MODEL,SIZE "$1" 2>/dev/null | awk '{ size = $NF; $NF = ""; printf "%s, %.0f GB", $0, size / 1000000000 }'
}

choose_admin() {
    local user pass pass2
    while :; do
        user=$(whiptail --title "$TITLE" --inputbox "$(paimenos_text 'Name des Administrator-Kontos für Wartung und Updates (nicht das Konto der Kinder):')" 11 70 admin 3>&1 1>&2 2>&3) || return 1
        valid_username "$user" && break
        whiptail --title "$TITLE" --msgbox "$(paimenos_text 'Ungültiger Name. Erlaubt sind Kleinbuchstaben, Ziffern, - und _; er muss mit einem Buchstaben beginnen. kids, root und live sind nicht erlaubt.')" 10 70
    done
    while :; do
        pass=$(whiptail --title "$TITLE" --passwordbox "$(paimenos_text 'Passwort für dieses Konto (mindestens 6 Zeichen):')" 10 64 3>&1 1>&2 2>&3) || return 1
        pass2=$(whiptail --title "$TITLE" --passwordbox "$(paimenos_text 'Passwort wiederholen:')" 10 64 3>&1 1>&2 2>&3) || return 1
        if [[ $pass == "$pass2" && ${#pass} -ge 6 ]]; then break; fi
        whiptail --title "$TITLE" --msgbox "$(paimenos_text 'Die Passwörter stimmen nicht überein oder sind zu kurz.')" 8 64
    done
    ADMIN_USER=$user
    ADMIN_PASSWORD=$pass
}

# ---------- Installation ----------

step() { printf '\n==> %s\n' "$*"; }

TARGET_MOUNTS=()
cleanup_mounts() {
    local index mount
    for ((index = ${#TARGET_MOUNTS[@]} - 1; index >= 0; index--)); do
        mount=${TARGET_MOUNTS[index]}
        umount "$mount" 2>/dev/null || umount -l "$mount" 2>/dev/null || true
    done
    TARGET_MOUNTS=()
}

mount_target() {
    mount "$@" || return 1
    TARGET_MOUNTS+=("${@: -1}")
}

FAILED=0
# Merkt sich Fehler; run_install bricht nach jeder Stufe ab, wenn eines fehlgeschlagen ist.
must() { "$@" || { FAILED=1; echo "Fehlgeschlagen: $*" >&2; return 1; }; }

in_target() { chroot "$TARGET" env DEBIAN_FRONTEND=noninteractive LC_ALL=C "$@"; }

partition_disk() {
    local disk=$1 mode=$2
    # Gemountete Teile der Platte freigeben, alte Signaturen löschen.
    while read -r part; do umount "$part" 2>/dev/null || true; done < <(lsblk -lnpo NAME "$disk" | tail -n +2)
    wipefs -af "$disk" >/dev/null
    sfdisk "$disk" < <(sfdisk_script "$mode") >/dev/null
    udevadm settle
    partprobe "$disk" 2>/dev/null || true
    udevadm settle
}

copy_system() {
    local squash=$MEDIUM/live/filesystem.squashfs
    [[ -f $squash ]] || { echo "Systemabbild fehlt: $squash" >&2; return 1; }
    mkdir -p "$SQUASH_MOUNT"
    mount -t squashfs -o ro,loop "$squash" "$SQUASH_MOUNT" || return 1
    TARGET_MOUNTS+=("$SQUASH_MOUNT")
    # Nur das reine Abbild, nicht das laufende Live-System mit seinen Änderungen.
    rsync -aHAXx --info=progress2 --no-inc-recursive "$SQUASH_MOUNT"/ "$TARGET"/
}

configure_target() {
    local lang=$1 keyboard=$2 timezone=$3 root_uuid=$4 efi_uuid=$5 locale
    locale=$(locale_for "$lang")
    must fstab_text "$root_uuid" "$efi_uuid" > "$TARGET/etc/fstab"
    printf '%s\n' "$HOSTNAME_NEW" > "$TARGET/etc/hostname"
    hosts_text "$HOSTNAME_NEW" > "$TARGET/etc/hosts"
    keyboard_text "$keyboard" > "$TARGET/etc/default/keyboard"
    printf '%s\n' "$timezone" > "$TARGET/etc/timezone"
    ln -sf "/usr/share/zoneinfo/$timezone" "$TARGET/etc/localtime"
    # Debian 13 liest /etc/locale.conf, ältere Werkzeuge /etc/default/locale.
    sed -i "s/^# *\(${locale//./\\.} UTF-8\)/\1/" "$TARGET/etc/locale.gen"
    grep -q "^${locale} UTF-8" "$TARGET/etc/locale.gen" || printf '%s UTF-8\n' "$locale" >> "$TARGET/etc/locale.gen"
    must in_target locale-gen >/dev/null
    printf 'LANG=%s\n' "$locale" > "$TARGET/etc/locale.conf"
    printf 'LANG=%s\n' "$locale" > "$TARGET/etc/default/locale"
    # Gespeicherte WLAN-Zugänge aus dem Live-System übernehmen.
    if compgen -G '/etc/NetworkManager/system-connections/*' >/dev/null; then
        mkdir -p "$TARGET/etc/NetworkManager/system-connections"
        cp -a /etc/NetworkManager/system-connections/. "$TARGET/etc/NetworkManager/system-connections/"
        chmod 600 "$TARGET"/etc/NetworkManager/system-connections/* 2>/dev/null || true
    fi
    # Administrator; root bleibt gesperrt.
    must in_target useradd -m -s /bin/bash -G sudo "$ADMIN_USER"
    printf '%s:%s\n' "$ADMIN_USER" "$ADMIN_PASSWORD" | must in_target chpasswd
    must in_target passwd -l root >/dev/null
    # Live-Pakete entfernen (ohne autoremove), sonst bootet das Ziel wieder als Live-System.
    local installed=() package
    for package in "${LIVE_PACKAGES[@]}"; do
        in_target dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' && installed+=("$package")
    done
    ((${#installed[@]} == 0)) || must in_target apt-get purge -y "${installed[@]}"
    # Was früher die preseed-late_command erledigte: Setup auf dem installierten System vormerken.
    mkdir -p "$TARGET/var/lib/paimenos"
    touch "$TARGET/var/lib/paimenos/setup-pending"
    in_target systemctl enable paimenos-setup.service NetworkManager.service >/dev/null 2>&1
    in_target systemctl set-default multi-user.target >/dev/null 2>&1
}

install_bootloader() {
    local disk=$1 mode=$2
    sed -i -e 's/^GRUB_DISTRIBUTOR=.*/GRUB_DISTRIBUTOR="PaimenOS"/' "$TARGET/etc/default/grub"
    grep -q '^GRUB_DISABLE_OS_PROBER' "$TARGET/etc/default/grub" || printf 'GRUB_DISABLE_OS_PROBER=true\n' >> "$TARGET/etc/default/grub"
    must in_target update-initramfs -u -k all
    if [[ $mode == efi ]]; then
        mount -t efivarfs efivarfs "$TARGET/sys/firmware/efi/efivars" 2>/dev/null && TARGET_MOUNTS+=("$TARGET/sys/firmware/efi/efivars")
        # Erst der Standardpfad (funktioniert auch bei unzuverlässigen Firmwares), dann der NVRAM-Eintrag.
        must in_target grub-install --target=x86_64-efi --efi-directory=/boot/efi --removable
        in_target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=PaimenOS || true
    else
        must in_target grub-install --target=i386-pc "$disk"
    fi
    must in_target update-grub
}

run_install() {  # Sprache, Tastatur, Zeitzone, Platte
    local lang=$1 keyboard=$2 timezone=$3 disk=$4 mode=bios root_part efi_part='' root_uuid efi_uuid=''
    [[ -d /sys/firmware/efi ]] && mode=efi
    root_part=$(partition_name "$disk" 2)
    step "$(paimenos_text 'Festplatte wird eingerichtet ...')"
    partition_disk "$disk" "$mode" || return 1
    if [[ $mode == efi ]]; then
        efi_part=$(partition_name "$disk" 1)
        mkfs.vfat -F32 -n EFI "$efi_part" >/dev/null || return 1
    fi
    mkfs.ext4 -F -q -L PaimenOS "$root_part" || return 1
    root_uuid=$(blkid -s UUID -o value "$root_part")
    [[ -z $efi_part ]] || efi_uuid=$(blkid -s UUID -o value "$efi_part")
    mkdir -p "$TARGET"
    mount_target "$root_part" "$TARGET" || return 1
    if [[ $mode == efi ]]; then
        mkdir -p "$TARGET/boot/efi"
        mount_target "$efi_part" "$TARGET/boot/efi" || return 1
    fi
    step "$(paimenos_text 'System wird kopiert (das dauert einige Minuten) ...')"
    copy_system || return 1
    mount_target --bind /dev "$TARGET/dev" || return 1
    mount_target -t devpts devpts "$TARGET/dev/pts" || return 1
    mount_target -t proc proc "$TARGET/proc" || return 1
    mount_target -t sysfs sysfs "$TARGET/sys" || return 1
    mount_target -t tmpfs tmpfs "$TARGET/run" || return 1
    step "$(paimenos_text 'System wird eingerichtet ...')"
    configure_target "$lang" "$keyboard" "$timezone" "$root_uuid" "$efi_uuid"
    ((FAILED == 0)) || return 1
    step "$(paimenos_text 'Startprogramm wird installiert ...')"
    install_bootloader "$disk" "$mode"
    ((FAILED == 0)) || return 1
    INSTALLED_ROOT_UUID=$root_uuid
}

# Ohne Firmware-Neustart in das installierte System springen.
jump_into_target() {
    local kernel initrd
    kernel=$(ls -1 "$TARGET"/boot/vmlinuz-* 2>/dev/null | sort -V | tail -n 1)
    initrd=$(ls -1 "$TARGET"/boot/initrd.img-* 2>/dev/null | sort -V | tail -n 1)
    [[ -f $kernel && -f $initrd ]] || return 1
    command -v kexec >/dev/null || return 1
    cp "$kernel" /run/paimenos-kexec-kernel && cp "$initrd" /run/paimenos-kexec-initrd || return 1
    local append="root=UUID=$INSTALLED_ROOT_UUID ro quiet loglevel=3"
    kexec -s -l /run/paimenos-kexec-kernel --initrd=/run/paimenos-kexec-initrd --append="$append" 2>/dev/null ||
        kexec -l /run/paimenos-kexec-kernel --initrd=/run/paimenos-kexec-initrd --append="$append" 2>/dev/null || return 1
    # Das frisch beschriebene Dateisystem sauber trennen, bevor der neue Kernel übernimmt.
    cleanup_mounts
    sync
    kexec -e
}

main() {
    [[ $EUID == 0 ]] || exit 1
    source "$REPO_DIR/installer/language.sh"
    TITLE=$(paimenos_text 'PaimenOS installieren')
    local lang keyboard timezone disk default_keyboard default_zone status
    lang=$(choose_language) || exit 0
    export PAIMENOS_LANGUAGE=$lang
    source "$REPO_DIR/installer/language.sh"
    TITLE=$(paimenos_text 'PaimenOS installieren')
    export LANG=$(locale_for "$lang")
    default_keyboard=de; default_zone=Europe/Berlin
    [[ $lang == en ]] && { default_keyboard=us; default_zone=UTC; }
    keyboard=$(choose_keyboard "$default_keyboard") || exit 0
    loadkeys "${keyboard%%:*}" >/dev/null 2>&1 || true
    timezone=$(choose_timezone "$default_zone") || exit 0
    if nmcli -t -f TYPE device 2>/dev/null | grep -qx wifi; then
        if whiptail --title "$TITLE" --yesno "$(paimenos_text 'Soll dieses Gerät mit einem WLAN verbunden werden? Das Netzwerk wird später auch für Updates genutzt.')" 10 64; then
            nmtui || true
        fi
    fi
    disk=$(choose_disk) || exit 0
    choose_admin || exit 0
    whiptail --title "$TITLE" --defaultno --yesno "$(paimenos_text 'ACHTUNG: Alles auf dieser Festplatte wird gelöscht:\n\n{value0}\n\nPaimenOS benutzt die ganze Platte. Jetzt installieren?' "$disk  $(describe_disk "$disk")")" 14 70 || exit 0
    clear
    mkdir -p "$(dirname "$LOG")"
    # Kein Pipe-Aufruf: run_install setzt Mount-Liste und Root-UUID in dieser Shell.
    run_install "$lang" "$keyboard" "$timezone" "$disk" > >(tee -a "$LOG") 2>&1
    status=$?
    sleep 1
    if ((status == 0)); then
        printf '\n%s\n' "$(paimenos_text 'Die Grundinstallation ist fertig. Das PaimenOS-Setup startet jetzt ...')"
        jump_into_target
        # kexec nicht möglich: normaler Neustart, das Setup startet beim Hochfahren.
        cleanup_mounts
        whiptail --title "$TITLE" --msgbox "$(paimenos_text 'Die Grundinstallation ist fertig. Das Gerät startet jetzt neu und setzt die Einrichtung fort. Den USB-Stick stecken lassen und beim Start nicht davon booten.')" 11 64
        systemctl reboot
        exit 0
    fi
    cleanup_mounts
    whiptail --title "$TITLE" --msgbox "$(paimenos_text 'Die Installation ist fehlgeschlagen. Das Protokoll steht in {value0}. Du kannst es im Live-Menü erneut versuchen.' "$LOG")" 11 64
    exit 1
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
