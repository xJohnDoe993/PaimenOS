# Eltern-Bereich am Gerät

Es gibt **einen** Eltern-Bereich: den Webbereich (`src/paimenos/parent_web.py`, Port 80). Am Laptop öffnet ihn der
Button **Eltern-Bereich** im Kopf des Kinder-Menüs in einem eigenen Browserfenster (Zurück/Weiter und „Zum Menü“
wie bei Webapps); von anderen Geräten im Netz erreicht man ihn unter `http://<IP-des-Geräts>/`. Der frühere
Qt-Dialog mit eigenen Seiten für Apps, Zeit, WLAN, Bluetooth, Backups und Updates ist entfernt, es gibt keine
zweite Oberfläche mehr zu pflegen.

## Ablauf am Gerät

1. Der Button prüft, ob der Webdienst auf Port 80 antwortet. Sonst erscheint eine Meldung (Dienst
   `paimenos-parent-web.service`), kein leeres Browserfenster.
2. Das Menü startet `close_overlay --parent-browser` mit dem Profil `~/.mozilla/paimenos-webapps/parent-area` und
   der Adresse `http://localhost/` (es geht kein Datenverkehr ins Netz).
3. Die Eltern geben die PIN auf der Anmeldeseite des Webbereichs ein (5 Versuche pro Minute, danach Pause).
4. **Zum Menü** beendet das Fenster; das Menü lädt Änderungen an Apps und Einstellungen selbst nach.

## Sicherheit: Profil ohne Gedächtnis

Die Anmeldung des Webbereichs ist ein Cookie, das 30 Minuten gilt. Damit ein Kind nach dem Schließen des Fensters
nicht ohne PIN wieder hineinkommt, läuft der Eltern-Bereich in einem **flüchtigen Profil**
(`run_browser(..., ephemeral=True)`): Der Browser-Starter leert das Profil vor dem Start und nach dem Schließen
vollständig (außer den Sperrdateien), und Firefox speichert weder Passwörter noch Formulardaten
(`PARENT_PREFERENCES` in `src/paimenos/webapp.py`). Tests: `tests/test_browser.py`.

## Drucken

Die Seite **Drucken** zeigt Druckaufträge des Kindes zur Freigabe und verwaltet die Drucker; Details in
[printing.md](printing.md).

## Netzwerkeditor

Schul- und Firmennetze sowie VPN richtet der Netzwerkeditor (`nm-connection-editor`) ein. Die WLAN-Seite hat dafür
den Button **Netzwerkeditor am Laptop öffnen**: Die Seite legt die Anfragedatei `paimenos-menu.network-editor` im
Statusordner an, das Kinder-Menü (das den Bildschirm kennt) öffnet den Editor als eigenes Fenster über dem Browser. Beim Verlassen des
Eltern-Bereichs beendet das Menü den Editor wieder (erst SIGTERM, nach zwei Sekunden SIGKILL), er bleibt nie über dem
Kinder-Menü stehen.

## Grenzen

- Fällt der Webdienst aus, gibt es am Gerät keinen Eltern-Bereich, bis er wieder läuft (systemd startet ihn neu).
- Das Fenster braucht einen Browserstart (auf alten Laptops einige Sekunden) statt eines sofort offenen Dialogs.
- Der Sperrbildschirm (Bildschirmzeit abgelaufen) hat weiter seinen eigenen kleinen PIN-Dialog für „Zeit hinzufügen“.
- Ein optionaler Nutzungszeitraum (Bildschirmzeit → Nutzungszeitraum) sperrt das Gerät außerhalb der Uhrzeiten; der Sperrbildschirm bietet dann „Mit Eltern-PIN freischalten“ für den Rest des Tages.
