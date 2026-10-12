#!/bin/bash
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root in a Debian 13 build environment.' >&2; exit 1; }
. /etc/os-release
[[ ${ID:-} == debian && ${VERSION_CODENAME:-} == trixie ]] || exit 1
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
BUILD=${PAIMENOS_BUILD_DIR:-/var/tmp/paimenos-live}
[[ ! -e $BUILD ]] || { echo "Build directory already exists: $BUILD" >&2; exit 1; }
mkdir -p "$BUILD" "$ROOT/dist/iso"
cd "$BUILD"
lb config --mode debian --distribution trixie --architectures amd64 \
    --binary-images iso-hybrid --bootloaders 'syslinux grub-efi' \
    --archive-areas 'main contrib non-free non-free-firmware' \
    --debian-installer live --debian-installer-gui true \
    --debian-installer-preseedfile "" \
    --bootappend-install 'preseed/file=/cdrom/preseed.cfg' \
    --bootappend-live 'boot=live components username=live hostname=paimenos locales=de_DE.UTF-8 keyboard-layouts=de timezone=Europe/Berlin live-config.nocomponents=sudo quiet splash' \
    --iso-application PaimenOS --iso-volume PAIMENOS --iso-publisher PaimenOS
mkdir -p config/includes.chroot/opt/paimenos-source config/hooks/live config/package-lists config/includes.binary
# Installer paths refer to the installation medium, not the build container.
install -m 0644 "$ROOT/iso/preseed.cfg" config/includes.binary/preseed.cfg
# Tracked source only: no credentials, git history or previous build output.
git -C "$ROOT" archive HEAD | tar -x -C config/includes.chroot/opt/paimenos-source
printf '%s\n' 'live-boot live-config live-config-systemd linux-image-amd64 systemd-sysv locales sudo python3 ca-certificates network-manager wpasupplicant firmware-iwlwifi firmware-realtek firmware-atheros firmware-misc-nonfree whiptail kexec-tools' > config/package-lists/base.list.chroot
cat > config/hooks/live/0900-paimenos.hook.chroot <<'HOOK'
#!/bin/bash
set -Eeuo pipefail
mkdir -p /run
touch /run/paimenos-live-build
bash /opt/paimenos-source/iso/configure-image.sh
HOOK
chmod 0755 config/hooks/live/0900-paimenos.hook.chroot
lb build
# Verify the actual ISO includes the file requested by the installer.
xorriso -osirrox on -indev live-image-amd64.hybrid.iso \
    -extract /preseed.cfg "$BUILD/packaged-preseed.cfg"
cmp "$ROOT/iso/preseed.cfg" "$BUILD/packaged-preseed.cfg"
version=$(cat "$ROOT/VERSION")
output="PaimenOS-${version}-debian13-amd64.iso"
install -m 0644 live-image-amd64.hybrid.iso "$ROOT/dist/iso/$output"
cd "$ROOT/dist/iso"
sha256sum "$output" > "$output.sha256"
