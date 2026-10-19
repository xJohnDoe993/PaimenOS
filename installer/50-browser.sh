#!/bin/bash
# Wird von install.sh in einer gemeinsamen Shell geladen.
# 5. Polkit Rechte, Proxy, DNS & Webapp-Icon Caching
# ---------------------------------------------------------------------------
echo "$(paimenos_text 'System-Rechte, Family-DNS & Webapp-Logos cachen ...')"

# polkit-Regeln, Firefox-Richtlinien und Autoconfig installiert 20-hardware.sh über system_config.

# Firefox user.js Profil
FIREFOX_DIR="${KIDS_HOME}/.mozilla/firefox"
if [ ! -d "$FIREFOX_DIR" ]; then
    mkdir -p "$FIREFOX_DIR/default.profile"
    render_repo_file config/firefox/profiles.ini.in "$FIREFOX_DIR/profiles.ini"
fi

PROFILE_PATH=$(find "$FIREFOX_DIR" -maxdepth 2 -type d \( -name "*.default*" -o -name "default.profile" \) -print -quit)

if [ -n "$PROFILE_PATH" ]; then
    install_repo_file config/firefox/user.js "$PROFILE_PATH/user.js"
    cp "$PROFILE_PATH/user.js" "$FIREFOX_DIR/paimenos-user.js"
    chown -R "${KIDS_USER}:${KIDS_USER}" "$FIREFOX_DIR"
fi

/usr/bin/python3 "${REPO_DIR}/tools/configure-dns.py" family \
    "${REPO_DIR}/config/resolved/paimenos-family-dns.conf"


webapp_icon_download() {
    local url="$1"
    local name="$2"
    local tmp="/tmp/paimenos-${name}.download"
    local dest="${ICONS_DIR}/${name}.png"

    echo "  -> ${name}.png"
    rm -f "${tmp}" "${dest}.tmp"
    if curl -fLsS --connect-timeout 8 --max-time 30 -A "${UA}" -o "${tmp}" "${url}" 2>/dev/null && [[ -s "${tmp}" ]]; then
        if convert "${tmp}" -background none -auto-orient -thumbnail '256x256>' -gravity center -extent 256x256 "png:${dest}.tmp" 2>/dev/null && [[ -s "${dest}.tmp" ]]; then
            mv -f "${dest}.tmp" "${dest}"
            return 0
        fi
    fi

    rm -f "${dest}.tmp"
    if [[ -s "${dest}" ]]; then
        return 0
    fi
    return 0
}

# Eigenes, theme-unabhängiges Power-Symbol; ohne Download verfügbar.
install_repo_file assets/icons/poweroff.svg "${ICONS_DIR}/poweroff.svg"

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
echo "$(paimenos_text 'Lade gewünschte Original-Logos herunter ...')"
if [[ "$PAIMENOS_LANGUAGE" == en ]]; then
    for icon in pbs-kids learnenglish-kids nasa-space-place blockly-games scratch-web; do
        install_repo_file "assets/icons/${icon}.svg" "${ICONS_DIR}/${icon}.svg"
    done
elif [[ "${PAIMENOS_OFFLINE:-0}" == 1 ]]; then
    echo "$(paimenos_text 'Offline-Modus: Logos der Webseiten werden nicht geladen.')"
else
webapp_icon_download "https://licensing.wdr-mediagroup.com/wp-content/uploads/2021/12/die_maus_logo-1200x414.png" "maus"
webapp_icon_download "https://www.wdrmaus.de/elefantenseite/codebase/eltern2021/images/elefant_header.svg" "elefant"
webapp_icon_download "https://logos-world.net/wp-content/uploads/2022/07/KiKA-Logo-700x394.png" "kika"
webapp_icon_download "https://www.kika.de/adventskalender/adventskalender-138-resimage_v-cropped_w-1024.png?version=58631" "kikaninchen"
webapp_icon_download "https://www.scout-magazin.de/files/aktuelles/news/2021/fragFINN.de_Logo.png" "fragfinn"
webapp_icon_download "https://gs-am-selzbogen.de/wp-content/uploads/2023/05/blindekuh-logo_0-1125x715-2-768x524.png" "blinde-kuh"

fi

if [[ ! -s "${ICONS_DIR}/youtube-kids.png" ]]; then
    convert -size 256x256 xc:'#FF3B30' -gravity center -fill white -pointsize 90 -annotate +0+0 '▶' "${ICONS_DIR}/youtube-kids.png" 2>/dev/null || true
fi

rm -f /tmp/paimenos-*.download /tmp/paimenos-*.tmp
chown -R "${KIDS_USER}:${KIDS_USER}" "${KIDS_HOME}/.local/share/paimenos"
chmod -R 755 "${KIDS_HOME}/.local/share/paimenos"
