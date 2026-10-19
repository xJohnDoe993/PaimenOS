#!/bin/bash
# Wird von install.sh in einer gemeinsamen Shell geladen.
# 7. Webapps Definition & Standard-Einstellungen
# ---------------------------------------------------------------------------
echo "$(paimenos_text 'Kinder-Webapps & Einstellungen konfigurieren ...')"

WEBAPP_ENTRIES=$( /usr/bin/python3 "${REPO_DIR}/tools/default-webapps.py" )

# PIN und Einschränkungen liegen root-eigen im Guard-Speicher, nicht mehr in einer Datei von kids.
if [[ ! -e /var/lib/paimenos/guard.enabled && ! -f "${SETTINGS_JSON}" ]]; then
    /usr/bin/python3 "${REPO_DIR}/tools/initialize-settings.py" "$PAIMENOS_PARENT_PIN"
fi

# App-Liste für das Dashboard erstellen.

if [[ -f "${APPS_JSON}" ]]; then
    cp -a "${APPS_JSON}" "${APPS_JSON}.before-update"
fi
render_repo_file data/apps.json.in "${APPS_JSON}.new"
/usr/bin/python3 "${REPO_DIR}/tools/merge-apps.py" "${APPS_JSON}"

install_repo_file assets/icons/category-all.svg "${ICONS_DIR}/category-all.svg"
install_repo_file assets/icons/category-webapps.svg "${ICONS_DIR}/category-webapps.svg"
install_repo_file assets/icons/category-games.svg "${ICONS_DIR}/category-games.svg"
install_repo_file assets/icons/category-productive.svg "${ICONS_DIR}/category-productive.svg"
chmod 0644 "${APPS_JSON}"

