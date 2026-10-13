#!/bin/bash
# Wird von install.sh in einer gemeinsamen Shell geladen.
# 6. Native Apps & Spiele Auswahl
# ---------------------------------------------------------------------------
echo
echo "============================================================"
echo "$(paimenos_text ' NATIVE APP- & SPIELE-AUSWAHL')"
echo "============================================================"
echo
echo "$(paimenos_text 'Wähle die Programme, die installiert/aktualisiert werden sollen:')"
echo "$(paimenos_text 'Eingabe-Beispiel: 1 2 6 7 (oder '"'"'13'"'"' für keine weiteren)')"
echo
echo "  1) Scratch          2) Tux Paint        3) LibreOffice"
echo "  4) GIMP             5) Geany            6) Luanti / Minetest"
echo "  7) SuperTuxKart     8) SuperTux         9) GCompris"
echo " 10) Tux Math        11) VLC             12) Terminal"
echo "$(paimenos_text ' 13) Überspringen / Keine')"
echo

OFFLINE_AVAILABLE=""
if [[ "${PAIMENOS_OFFLINE:-0}" == 1 ]]; then
    declare -A OFFLINE_APP_PACKAGES=([1]=scratch [2]=tuxpaint [3]=libreoffice [4]=gimp [5]=geany [6]=minetest
        [7]=supertuxkart [8]=supertux [9]=gcompris-qt [10]=tuxmath [11]=vlc [12]=xterm)
    for number in $(printf '%s\n' "${!OFFLINE_APP_PACKAGES[@]}" | sort -n); do
        candidate=$(LC_ALL=C apt-cache policy "${OFFLINE_APP_PACKAGES[$number]}" 2>/dev/null | awk '/Candidate:/ {print $2; exit}')
        if [[ -n "$candidate" && "$candidate" != "(none)" ]]; then OFFLINE_AVAILABLE+="$number "; fi
    done
    echo "$(paimenos_text 'Offline-Modus: Auf dem Installationsmedium gibt es nur diese Apps: {value0}' "${OFFLINE_AVAILABLE:-–}")"
    echo "$(paimenos_text 'Alle anderen Apps lassen sich später mit Internet nachinstallieren.')"
    echo
fi

APP_SELECTION="${PAIMENOS_APPS:-}"
if [[ -z "$APP_SELECTION" ]]; then read -r -p "$(paimenos_text 'Auswahl: ')" APP_SELECTION; fi

APP_SOURCE="${PAIMENOS_APP_SOURCE:-}"
if [[ -z "$APP_SOURCE" ]]; then
    echo "$(paimenos_text 'Paketquelle: 1 = stabile Flathub-Apps (empfohlen), 2 = nur Debian-Pakete')"
    echo "$(paimenos_text 'Flathub: Luanti, GCompris, SuperTuxKart, GIMP und LibreOffice.')"
    echo "$(paimenos_text 'Zusätzliche Laufzeitpakete benötigen beim ersten Mal mehr Download und Speicher.')"
    read -r -p "$(paimenos_text 'Paketquelle [1]: ')" SOURCE_SELECTION
    case "$SOURCE_SELECTION" in
        ''|1) APP_SOURCE=flathub ;;
        2) APP_SOURCE=debian ;;
        *) echo "$(paimenos_text 'Ungültige Paketquelle.')" >&2; exit 1 ;;
    esac
fi
case "$APP_SOURCE" in
    flathub|debian) ;;
    *) echo "$(paimenos_text 'PAIMENOS_APP_SOURCE muss flathub oder debian sein.')" >&2; exit 1 ;;
esac

# Offline nur Apps, die die Offline-Paketquelle des Installationsmediums enthält.
if [[ "${PAIMENOS_OFFLINE:-0}" == 1 ]]; then
    APP_SOURCE=debian
    FILTERED_SELECTION=""
    for choice in ${APP_SELECTION}; do
        if [[ " $OFFLINE_AVAILABLE" == *" $choice "* ]]; then FILTERED_SELECTION+="$choice "; fi
    done
    APP_SELECTION="$FILTERED_SELECTION"
fi

INSTALL_PKGS=()
APP_ENTRIES=""
SELECTED_NATIVE_APPS=""
FLATPAK_READY=unknown
FLATPAK_REMOTE=paimenos-flathub
MANAGED_FLATPAKS=/var/lib/paimenos/flatpak-apps.list
declare -A SELECTED_APP_IDS=()
declare -A FLATPAK_IDS=(
    [minetest]=org.luanti.luanti
    [gcompris]=org.kde.gcompris
    [supertuxkart]=net.supertuxkart.SuperTuxKart
    [gimp]=org.gimp.GIMP
    [libreoffice]=org.libreoffice.LibreOffice
)

prepare_flatpak() {
    if [[ "$FLATPAK_READY" == yes ]]; then return 0; fi
    if [[ "$FLATPAK_READY" == no ]]; then return 1; fi
    FLATPAK_READY=no
    if ! apt-get install -y --no-install-recommends flatpak xdg-desktop-portal xdg-desktop-portal-gtk; then
        echo "$(paimenos_text '  ! Flatpak konnte nicht eingerichtet werden; verwende Debian-Pakete.')" >&2
        return 1
    fi
    local answer
    until flatpak remote-add --system --if-not-exists "$FLATPAK_REMOTE" https://dl.flathub.org/repo/flathub.flatpakrepo; do
        echo "$(paimenos_text '  ! Flathub ist nicht erreichbar.')" >&2
        answer=2
        if [[ -t 0 ]]; then read -r -p "$(paimenos_text '1) Erneut versuchen  2) Debian-Pakete verwenden [1]: ')" answer; fi
        if [[ "$answer" == 2 ]]; then
            echo "$(paimenos_text '  ! Flathub ist nicht erreichbar; verwende Debian-Pakete.')" >&2
            return 1
        fi
    done
    mkdir -p "${KIDS_HOME}/.config/xdg-desktop-portal"
    install_repo_file config/desktop/portals.conf "${KIDS_HOME}/.config/xdg-desktop-portal/portals.conf"
    chown -R "$KIDS_USER:$KIDS_USER" "${KIDS_HOME}/.config/xdg-desktop-portal" || return 1
    FLATPAK_READY=yes
}

install_flatpak_app() {
    local id="$1" app_id="$2" title="$3"
    if ! prepare_flatpak; then return 1; fi
    echo "$(paimenos_text '  + {value0}: stabile Flathub-Version installieren/aktualisieren ...' "${title}")"
    if ! flatpak install --system --noninteractive -y --or-update "$FLATPAK_REMOTE" "${app_id}//stable"; then
        if ! flatpak info --system "${app_id}//stable" >/dev/null 2>&1; then
            echo "$(paimenos_text '  ! {value0}: Flatpak fehlgeschlagen (z. B. Architektur/Netzwerk); verwende Debian-Paket.' "${title}")" >&2
            return 1
        fi
        echo "$(paimenos_text '  ! {value0}: Update fehlgeschlagen; die bereits installierte Flatpak-Version bleibt nutzbar.' "${title}")" >&2
    fi
    # Kein Shell-Parsing der App-Parameter. exec hält die Prozessüberwachung intakt.
    /usr/bin/python3 "${REPO_DIR}/tools/configure-flatpak-app.py" "$id" "$app_id" || return 1
    if ! runuser -u "$KIDS_USER" -- /usr/bin/python3 /usr/local/lib/paimenos/current/tools/migrate-flatpak-data.py "$KIDS_HOME" "$app_id"; then
        echo "$(paimenos_text '  ! {value0}: Datenkopie fehlgeschlagen. Originaldaten bleiben erhalten; siehe Setup-Ausgabe.' "${title}")" >&2
    fi
    touch "$MANAGED_FLATPAKS"
    if ! grep -Fxq "$app_id" "$MANAGED_FLATPAKS"; then
        printf '%s\n' "$app_id" >> "$MANAGED_FLATPAKS"
    fi
    chmod 0644 "$MANAGED_FLATPAKS"
}

select_native_app() {
    local id="$1" pkg="$2" title="$3" icon_theme="$4" command="$5"
    local app_id="${FLATPAK_IDS[$id]:-}" source=debian icon_file=''
    if [[ -n "${SELECTED_APP_IDS[$id]:-}" ]]; then return; fi
    SELECTED_APP_IDS[$id]=1
    if [[ "$APP_SOURCE" == flathub && -n "$app_id" ]] && install_flatpak_app "$id" "$app_id" "$title"; then
        source=flathub
        command="/usr/local/bin/paimenos-app-${id}"
        icon_theme="$app_id"
        # Ein exportiertes Symbol kopieren: keine neue Anmeldung nötig.
        local location candidate
        location=$(flatpak info --system --show-location "${app_id}//stable" 2>/dev/null) || location=''
        if [[ -n "$location" ]]; then
            for candidate in "$location/files/share/icons/hicolor/256x256/apps/${app_id}.png" \
                             "$location/files/share/icons/hicolor/128x128/apps/${app_id}.png" \
                             "$location/files/share/icons/hicolor/scalable/apps/${app_id}.svg"; do
                if [[ -s "$candidate" ]]; then
                    icon_file="flatpak-${id}.${candidate##*.}"
                    cp "$candidate" "${ICONS_DIR}/${icon_file}"
                    break
                fi
            done
        fi
    else
        app_id=''
        # install aktualisiert auch ein schon vorhandenes Debian-Paket.
        INSTALL_PKGS+=("${pkg}")
        echo "$(paimenos_text '  + {value0}: Debian-Paket installieren/aktualisieren' "${title}")"
    fi
    SELECTED_NATIVE_APPS+="${id}|${pkg}|${title}|${icon_theme}|${command}|${source}|${app_id}|${icon_file}"$'\n'
}

for choice in ${APP_SELECTION}; do
    case "${choice}" in
        1) select_native_app "scratch" "scratch" "Scratch" "scratch" "scratch" ;;
        2) select_native_app "tuxpaint" "tuxpaint" "Tux Paint" "tuxpaint" "tuxpaint" ;;
        3) select_native_app "libreoffice" "libreoffice" "LibreOffice" "libreoffice-startcenter" "libreoffice" ;;
        4) select_native_app "gimp" "gimp" "GIMP" "gimp" "gimp" ;;
        5) select_native_app "geany" "geany" "Geany" "geany" "geany" ;;
        6) select_native_app "minetest" "minetest" "Luanti (Minetest)" "minetest" "minetest" ;;
        7) select_native_app "supertuxkart" "supertuxkart" "SuperTuxKart" "supertuxkart" "supertuxkart" ;;
        8) select_native_app "supertux" "supertux" "SuperTux" "supertux2" "supertux2" ;;
        9) select_native_app "gcompris" "gcompris-qt" "GCompris" "gcompris-qt" "gcompris-qt" ;;
        10) select_native_app "tuxmath" "tuxmath" "Tux Math" "tuxmath" "tuxmath" ;;
        11) select_native_app "vlc" "vlc" "VLC" "vlc" "vlc" ;;
        12) select_native_app "xterm" "xterm" "Terminal" "utilities-terminal" "xterm" ;;
        *) ;;
    esac
done

if [[ -n "${SELECTED_APP_IDS[tuxpaint]:-}" ]]; then
    INSTALL_PKGS+=(tuxpaint-plugins netpbm)
fi

if ((${#INSTALL_PKGS[@]})); then
    mapfile -t INSTALL_PKGS <<< "$(printf '%s\n' "${INSTALL_PKGS[@]}" | awk 'NF && !seen[$0]++')"
    echo "$(paimenos_text 'Ausgewählte Debian-Apps installieren/aktualisieren ...')"
    apt-get install -y "${INSTALL_PKGS[@]}"
fi

resolve_native_command() {
    local requested="$1" candidate
    local -a candidates=("$requested")
    if [[ "$requested" == minetest ]]; then candidates+=(luanti); fi
    if [[ "$requested" == supertux2 ]]; then candidates+=(supertux); fi
    for candidate in "${candidates[@]}"; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

while IFS='|' read -r id pkg title icon_theme command source app_id icon_file; do
    [[ -n "${id}" ]] || continue
    if resolved_command=$(resolve_native_command "${command}"); then
        APP_ENTRIES+="$(/usr/bin/python3 "${REPO_DIR}/tools/native-app-entry.py" "$id" "$title" "$icon_theme" "$resolved_command" "$source" "$app_id" "$icon_file" "$pkg"
)"
        echo "$(paimenos_text '  ✓ Menü: {value0} ({value1})' "${title}" "${source}")"
    else
        echo "$(paimenos_text '  ! {value0}: Startprogramm '"'"'{value1}'"'"' nicht gefunden – nicht ins Menü aufgenommen.' "${title}" "${command}")" >&2
    fi
done <<< "${SELECTED_NATIVE_APPS}"

# Nur von PaimenOS eingerichtete Flatpak-Apps und deren Laufzeiten aktualisieren.
install_repo_file systemd/system/paimenos-app-update.service /etc/systemd/system/paimenos-app-update.service
install_repo_file systemd/system/paimenos-app-update.timer /etc/systemd/system/paimenos-app-update.timer
systemctl daemon-reload
if [[ "$PAIMENOS_ENABLE_APP_UPDATES" == 1 && -s "$MANAGED_FLATPAKS" ]]; then
    systemctl enable --now paimenos-app-update.timer
else
    systemctl disable --now paimenos-app-update.timer >/dev/null 2>&1 || true
fi

# Tux Paint auch bei einem Start außerhalb des Dashboards im Vollbild öffnen.
if command -v tuxpaint >/dev/null 2>&1; then
    mkdir -p "${KIDS_HOME}/.tuxpaint/saved"
    /usr/bin/python3 "${REPO_DIR}/tools/configure-tuxpaint.py" "${KIDS_HOME}/.tuxpaintrc"
    chown -R "${KIDS_USER}:${KIDS_USER}" "${KIDS_HOME}/.tuxpaint" "${KIDS_HOME}/.tuxpaintrc"
fi

