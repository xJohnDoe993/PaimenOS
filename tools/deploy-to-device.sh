#!/usr/bin/env bash
# Copy the current working tree (tracked and new files, uncommitted changes included) to a
# PaimenOS test device and activate it with tools/deploy.py. Development helper, not shipped.
#
#   tools/deploy-to-device.sh [HOST] [deploy.py arguments, e.g. --component desktop]
#
# HOST defaults to $PAIMENOS_DEVICE or the ssh alias "paimenos-t450". sudo asks for the
# device password in the terminal; for unattended runs export DEPLOY_SUDO_PASSWORD
# (it is only piped to sudo and never written to disk).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
host="${1:-${PAIMENOS_DEVICE:-paimenos-t450}}"
[ "$#" -gt 0 ] && shift
remote_dir='paimenos-deploy'

echo "==> Prüfe Quellen"
python3 -B tools/deploy.py --check >/dev/null

echo "==> Kopiere Arbeitsverzeichnis nach ${host}:~/${remote_dir}"
git ls-files -z --cached --others --exclude-standard \
    | tar --null --files-from=- -czf - \
    | ssh "$host" "rm -rf ~/${remote_dir} && mkdir ~/${remote_dir} && tar xzf - -C ~/${remote_dir}"

echo "==> Aktiviere Code auf dem Gerät"
quoted=''
[ "$#" -gt 0 ] && quoted=$(printf ' %q' "$@")
if [ -n "${DEPLOY_SUDO_PASSWORD:-}" ]; then
    printf '%s\n' "$DEPLOY_SUDO_PASSWORD" \
        | ssh "$host" "cd ~/${remote_dir} && sudo -S -p '' python3 -B tools/deploy.py${quoted}"
else
    ssh -t "$host" "cd ~/${remote_dir} && sudo python3 -B tools/deploy.py${quoted}"
fi
