#!/bin/bash
# Legt eine signierte Offline-Paketquelle (/paimenos-repo) auf eine fertige PaimenOS-ISO.
#
#   iso/build-offline-repo.sh ISO CHROOT
#
# ISO     fertige Live-ISO (wird ersetzt)
# CHROOT  Wurzel des Live-Systems (live-build: <Build>/chroot); "/" nur für Tests im Container
# Läuft als root in einer Debian-13-Umgebung mit Internet. Es lädt genau die Pakete, die das
# Setup auf dem installierten System noch installieren würde (installer/offline-packages.txt),
# jeweils mit denselben Optionen wie dort. Signaturschlüssel: PAIMENOS_REPO_SIGNING_KEY
# (ASCII-armored, privat) oder ein Einmalschlüssel für diesen Build.
#
# Das Repo ist flach (Packages/Release/InRelease direkt im Verzeichnis) und enthält eine leere
# .aptignr: Der Debian-Installer durchsucht das Medium per apt-cdrom nach Paketindizes und bricht
# "Paketmanager konfigurieren" ab, sobald er ein signiertes Repo findet (im Installer fehlt gpgv).
# .aptignr lässt apt-cdrom dieses Verzeichnis überspringen.
#
# Nur für schnelle Tests: PAIMENOS_REPO_POOL=Ordner mit .deb-Dateien überspringt den Download.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Als root ausführen.' >&2; exit 1; }
ISO=$(realpath "$1")
CHROOT=$2
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
CODENAME=trixie
WORK=$(mktemp -d /var/tmp/paimenos-offline.XXXXXX)
MOUNTS=()
cleanup() {
    local mount
    for mount in "${MOUNTS[@]}"; do umount -l "$mount" 2>/dev/null || true; done
    rm -rf "$WORK"
}
trap cleanup EXIT

inchroot() {
    if [[ $CHROOT == / ]]; then env DEBIAN_FRONTEND=noninteractive "$@"
    else chroot "$CHROOT" env DEBIAN_FRONTEND=noninteractive LC_ALL=C "$@"; fi
}
group() {  # Pakete einer Gruppe aus installer/offline-packages.txt
    awk -v g="$1" '/^\[/ {on = ($0 ~ "^\\[" g "\\]")} on && NF && $0 !~ /^[#\[]/ {print $1}' "$ROOT/installer/offline-packages.txt"
}

REPO=$WORK/paimenos-repo
mkdir -p "$REPO/pool"
if [[ -n ${PAIMENOS_REPO_POOL:-} ]]; then
    cp "$PAIMENOS_REPO_POOL"/*.deb "$REPO/pool/"
    requested=(wiederverwendet)
else
    if [[ $CHROOT != / ]]; then
        mount -t proc proc "$CHROOT/proc"; MOUNTS+=("$CHROOT/proc")
        mount --bind /dev "$CHROOT/dev"; MOUNTS+=("$CHROOT/dev")
        cp /etc/resolv.conf "$CHROOT/etc/resolv.conf"
    fi
    # Dieselben Quellen wie das Setup auf dem Gerät (main, contrib, non-free, non-free-firmware).
    rm -f "$CHROOT"/etc/apt/sources.list.d/*
    cat > "$CHROOT/etc/apt/sources.list" <<SOURCES
deb http://deb.debian.org/debian $CODENAME main contrib non-free non-free-firmware
deb http://deb.debian.org/debian $CODENAME-updates main contrib non-free non-free-firmware
deb http://deb.debian.org/debian-security $CODENAME-security main contrib non-free non-free-firmware
SOURCES
    inchroot apt-get update
    # Wie im Setup: system und apps mit Empfehlungen, hardware ohne.
    mapfile -t system < <(group system); mapfile -t hardware < <(group hardware); mapfile -t apps < <(group apps)
    inchroot apt-get install --download-only -y "${system[@]}"
    inchroot apt-get install --download-only -y --no-install-recommends "${hardware[@]}"
    inchroot apt-get install --download-only -y "${apps[@]}"
    cp "$CHROOT"/var/cache/apt/archives/*.deb "$REPO/pool/"
    requested=("${system[@]}" "${hardware[@]}" "${apps[@]}")
fi
echo "Pakete: $(ls "$REPO/pool" | wc -l), Größe: $(du -sh "$REPO/pool" | cut -f1)"

# Flaches Repo: Packages und Release direkt im Verzeichnis. Release enthält nur die Index-Dateien.
(cd "$REPO" && apt-ftparchive packages pool > Packages)
gzip -9kf "$REPO/Packages"
mkdir "$WORK/index"
cp "$REPO/Packages" "$REPO/Packages.gz" "$WORK/index/"
apt-ftparchive \
    -o "APT::FTPArchive::Release::Origin=PaimenOS" -o "APT::FTPArchive::Release::Label=PaimenOS Offline" \
    -o "APT::FTPArchive::Release::Suite=$CODENAME" -o "APT::FTPArchive::Release::Codename=$CODENAME" \
    -o "APT::FTPArchive::Release::Architectures=amd64" \
    release "$WORK/index" | sed "s#$WORK/index/##" > "$REPO/Release"

export GNUPGHOME=$WORK/gnupg
mkdir -m 0700 "$GNUPGHOME"
if [[ -n ${PAIMENOS_REPO_SIGNING_KEY:-} ]]; then
    gpg --batch --quiet --import <<< "$PAIMENOS_REPO_SIGNING_KEY"
    echo 'Signatur: Projektschlüssel'
else
    gpg --batch --quiet --passphrase '' --quick-generate-key 'PaimenOS Offline-Repo (Build) <offline@paimenos.invalid>' ed25519 sign never
    echo 'Signatur: Einmalschlüssel dieses Builds (PAIMENOS_REPO_SIGNING_KEY nicht gesetzt)'
fi
FPR=$(gpg --batch --list-secret-keys --with-colons | awk -F: '/^fpr:/ {print $10; exit}')
sign=(gpg --batch --yes --pinentry-mode loopback --passphrase '' --local-user "$FPR")
"${sign[@]}" --armor --detach-sign -o "$REPO/Release.gpg" "$REPO/Release"
"${sign[@]}" --clearsign -o "$REPO/InRelease" "$REPO/Release"
gpg --batch --export "$FPR" > "$REPO/paimenos-repo.gpg"
printf '%s\n' "$FPR" > "$REPO/FINGERPRINT"
: > "$REPO/.aptignr"
cp "$ROOT/VERSION" "$REPO/VERSION"
printf '%s\n' "${requested[@]}" > "$REPO/REQUESTED"

# In die ISO einbringen (ein vorhandenes /paimenos-repo wird ersetzt); Boot (BIOS und UEFI) bleibt unverändert.
replace=()
if xorriso -indev "$ISO" -find /paimenos-repo -maxdepth 0 2>/dev/null | grep -q paimenos-repo; then
    replace=(-rm_r /paimenos-repo --)
fi
xorriso -indev "$ISO" -outdev "$WORK/new.iso" -boot_image any replay "${replace[@]}" -map "$REPO" /paimenos-repo -commit
xorriso -indev "$WORK/new.iso" -find /paimenos-repo -maxdepth 1 | head -n 14
mv "$WORK/new.iso" "$ISO"
echo "Offline-Repo auf der ISO: $(du -sh "$ISO" | cut -f1) gesamt"
