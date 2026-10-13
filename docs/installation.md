# Installation

## Zielsystem

Ein frisches Debian 12 (bookworm) oder Debian 13 (trixie) mit Internetzugang, administrativem Benutzer und funktionierendem APT. Der Installer ist für einen dedizierten Kinder-Laptop vorgesehen: Er richtet LightDM-Autologin für `kids`, Openbox, Grafik, Audio, Familien-DNS, Proxy, Paketquellen der eigenen Debian-Version, Energiesparen und Gerätedienste ein.

Das frühere monolithische Setup wird nicht mehr benötigt. `install.sh` lädt die Module aus `installer/`; Python-Code und Konfigurationsdateien stehen als eigene Dateien im Projekt. Das gesamte Projekt muss deshalb lokal verfügbar sein. Ein einzelnes aus einer Pipe gestartetes Setup-Skript genügt nicht mehr.

## Ablauf

1. Projekt entpacken und in den Ordner `PaimenOS` wechseln.
2. `sudo bash install.sh` ausführen.
3. Neue Eltern-PIN mit 4 bis 12 Ziffern festlegen und wiederholen.
4. Emulatoren wählen. `empfohlen` entspricht der Auswahl aus v59; N64 und PSP bleiben optional.
5. Native Apps und Flathub oder Debian als Quelle wählen.
6. Abschlussmeldung und gegebenenfalls offene WLAN-Diagnose beachten.
7. `sudo reboot` ausführen.

Das Setup startet das Gerät standardmäßig nicht automatisch neu. Die bestehende Netzwerkverbindung bleibt während der Installation aktiv. Ein Debian-ifupdown-WLAN wird offline für NetworkManager vorbereitet und beim regulären Neustart vor dem Start der Netzwerkdienste übernommen.

Eine unvollständige modulare Installation lässt sich mit `sudo bash install.sh --resume` fortsetzen. Bereits vorhandene Eltern-Einstellungen werden dabei erhalten. Für abgeschlossene modulare Installationen ist `update.sh` zuständig. Bestehende LaurinOS-Installationen einschließlich 0.63.x werden weder beim Setup noch beim Update übernommen. PaimenOS 0.64.0 benötigt frisches Debian; eigene Daten vorher sichern. Der Installer bricht beim Erkennen der alten Installation vor Systemänderungen ab.

Das Setup installiert auch die in `data/update-packages.json` deklarierten Debian-Pakete. Spätere Releases können dort weitere Abhängigkeiten aufnehmen: Backend-Updates und manuelle Updates installieren fehlende Pakete automatisch. Weitere Einzelheiten stehen unter [Updates](updating.md#debian-pakete-bei-updates).

## PaimenOS-Plymouth-Theme

Das Setup installiert das eigene PaimenOS-Theme anstelle von
Pixels. Logo, native Plymouth-Skriptdatei und 48 kleine Ladekreis-Bilder sind im
Projekt enthalten; ein externer Theme-Download entfällt. Der Hintergrund ist hell,
das Logo blendet sanft ein und pulsiert dezent. Die Wortmarke dreht sich nicht.
Der Ladekreis zeigt Aktivität, keinen künstlichen Prozentwert. Die Animation
verzögert den Systemstart nicht absichtlich und unterstützt Passwort-/Frageabfragen.

Normale Code-Updates transportieren die Theme-Dateien als Teil der Komponente
`tools`, aktivieren sie jedoch nicht automatisch. Auf einem bereits eingerichteten
PaimenOS-Gerät nach einem vollständigen Update einmalig ausführen:

```bash
sudo python3 /usr/local/lib/paimenos/current/tools/install-paimenos-plymouth.py --activate
```

Alternativ nach dem Entpacken dieses Release-Projekts aus dessen Ordner:

```bash
sudo python3 tools/install-paimenos-plymouth.py --activate
```

Der Befehl prüft die Quelldateien, installiert das Theme, wählt `paimenos`, erhält
bestehende GRUB-Kerneloptionen und baut die initramfs für alle vorhandenen Kernel
neu. Laufende PaimenOS-Wartung oder Paketinstallation verhindert einen parallelen
Aufruf. Anschließend selbst neu starten; der Helfer startet nicht automatisch neu.
Fehler werden gemeldet; bei fehlgeschlagener Aktivierung wird versucht, die bisherige
Theme-Auswahl und GRUB-Konfiguration samt Startabbildern wiederherzustellen.

Nur die Dateien prüfen, ohne Systemänderung:

```bash
python3 tools/install-paimenos-plymouth.py --check
```

Das bisher installierte Pixels-Theme wird nicht entfernt. Zur Rückkehr, falls es
auf dem Gerät vorhanden ist:

```bash
sudo plymouth-set-default-theme -R pixels
```

Andere installierte Themes zeigt `plymouth-set-default-theme --list`. Ein normaler
Code-Rollback ändert das separat eingerichtete Plymouth-Theme nicht. Bei Änderungen
an den 52 Theme-Dateien muss auch `assets/plymouth/paimenos/SHA256SUMS` aktualisiert
werden; `tools/check.py` prüft diese Hashes und die PNG-Struktur.

## WLAN während des Setups

Während der Installation werden WLAN-Adapter nicht getrennt, nicht per `managed no/yes` umgeschaltet und NetworkManager sowie WPA-Dienste nicht neu gestartet. Bereits über NetworkManager eingerichtete Verbindungen bleiben unverändert.

Für WLAN aus Debian-ifupdown bereitet `tools/stage-wifi.py` die Profile offline vor. Der normale NetworkManager-Parser prüft das Format ohne laufenden Netzwerkzugriff. Die bestehende Verbindung trägt weiter alle APT-/App-/Emulator-Downloads. Ein erneuter Aufruf mit `--resume` ersetzt den vorbereiteten Plan, ohne Profile zu duplizieren oder die Verbindung zu verändern.

Beim regulären Neustart läuft `paimenos-wifi-migration.service` vor `networking.service`, NetworkManager und dem globalen WPA-Dienst. Erst dann werden die ifupdown-Einträge gezielt entfernt, die root-eigenen Profile mit Modus `0600` installiert und konkurrierende adapterbezogene Autostarts deaktiviert. NetworkManager startet anschließend mit dem gespeicherten Profil und Autoconnect. Die Kinderoberfläche startet ebenfalls nach diesem Neustart.

Ein privates Transaktionsjournal ermöglicht die Wiederherstellung nach Fehlern und einem unterbrochenen Prozess. Veränderungen an den ursprünglichen Konfigurationsdateien zwischen Vorbereitung und Neustart werden nicht überschrieben. Sicherungen und Statusdateien liegen unter `/var/lib/paimenos/wifi-migration/`, nur für root zugänglich. Erfolgreiche Übernahme: `completed.json`; zurückgesetzte fehlgeschlagene Übernahme: `failed.json`. Diagnose: `sudo journalctl -b -u paimenos-wifi-migration.service`.

Die automatische Vorbereitung unterstützt IPv4-DHCP mit WPA-PSK oder explizit offenem WLAN, optionale versteckte SSID/DNS-Angaben und einen einzelnen passenden `wpa-conf`-Netzwerkblock. Erweiterte statische/Enterprise-/Hook-Konfigurationen werden vor einer Änderung abgewiesen. Sie benötigen eine eigene NetworkManager-Konfiguration.

Der frühere Umgehungsschalter `PAIMENOS_KEEP_NETWORK` wird nicht benötigt. Das Standard-Setup erhält die laufende Verbindung selbst.

## DNS während des Setups

Vor der Basis-Paketinstallation prüft das Setup die Auflösung von `deb.debian.org`, sichert `resolv.conf` und übernimmt bekannte Upstream-DNS-Server als Bootstrap-Konfiguration. Das ist nötig, weil die Installation von `systemd-resolved` selbst `resolv.conf` ersetzen kann. Nach der Paketinstallation wird DNS erneut geprüft; bei einem Fehler wird der vorherige Resolver-Zustand wiederhergestellt und geprüft.

Auch die spätere Familien-DNS-Umstellung wird geprüft. Schlägt der Dienststart oder die Namensauflösung fehl, werden die bisherigen DNS-Dateien wiederhergestellt. Funktioniert DNS danach, läuft das Setup mit einem deutlichen Hinweis weiter: Der neue Familien-DNS-Filter ist dann nicht aktiviert. Bleibt DNS defekt, hält das Setup an. Ein vorher bereits unterbrochener Internetzugang wird durch diese Absicherung nicht automatisch repariert.

Die globale Familien-DNS-Konfiguration setzt `Domains=~.`: Normale Internetabfragen gehen damit an die Familien-DNS-Server, statt parallel an per DHCP gelieferte Standard-DNS-Server. Spezifischere lokale Domains wie `lan` bleiben über den DNS-Server des jeweiligen Netzes auflösbar. In `resolvectl status` muss unter `Global` neben den Familien-DNS-Adressen auch `DNS Domain: ~.` erscheinen. Die WLAN-DNS-Server können weiterhin angezeigt werden; ihre bloße Anzeige bedeutet keine Verwendung für normale Internetabfragen. Zusätzliche VPN-Routing-Domains oder Programme mit eigenen DNS-Abfragen sind gesondert zu prüfen.

`update.sh` und das Backend-Update ersetzen keine Systemkonfiguration. Auf bereits installierten Geräten muss diese DNS-Änderung daher separat angewendet werden.

Als Familien-DNS dienen Cloudflare Family (`1.1.1.3`, `1.0.0.3`) und AdGuard DNS Family (`94.140.14.15`, `94.140.15.16`); beide blocken Erwachseneninhalte und Schadseiten, AdGuard erzwingt zusätzlich SafeSearch. Am 13. Oktober 2026 wurden alle vier mit 20 Abfragen je Server geprüft: 100 % erreichbar, im Mittel 23 bis 40 ms, `pornhub.com` und `xvideos.com` werden geblockt. Der zuvor genutzte zweite CleanBrowsing-Server (`185.228.169.168`) beantwortete im selben Test nur 16 von 20 Abfragen. Firefox nutzt zusätzlich per Richtlinie DNS über HTTPS mit `https://family.adguard-dns.com/dns-query` (gesperrt, mit Rückfall auf das System-DNS). Firefox löst Namen selbst per DoH auf; alle anderen Programme nutzen das System-DNS. Auf bereits eingerichteten Geräten wirkt die Änderung nach `sudo python3 tools/configure-dns.py family config/resolved/paimenos-family-dns.conf` und einem Firefox-Neustart (Richtlinien in `/etc/firefox/policies/`).

Die DNS-Sicherung liegt root-eigen unter `/var/lib/paimenos/install-dns.json`. Die Bootstrap-Konfiguration bleibt bis zur erfolgreichen Familien-DNS-Übernahme erhalten; sie kann auch eine bewusst gesetzte temporäre DNS-Reparatur übernehmen. Die Prüfungen bestätigen Namensauflösung, nicht die Filterwirkung auf jeder Netzwerkverbindung.

## Schalter

Umgebungsvariablen mit `sudo env` übergeben, wenn `sudo` die Umgebung bereinigt:

```bash
sudo env PAIMENOS_APPS="2 6 9 11" \
  PAIMENOS_APP_SOURCE=debian \
  PAIMENOS_EMULATORS=empfohlen \
  bash install.sh
```

| Variable | Werte / Standard |
|---|---|
| `PAIMENOS_APPS` | Leer: interaktive Auswahl. Nummern aus dem Setup; `13`: keine nativen Apps |
| `PAIMENOS_APP_SOURCE` | Leer: interaktiv; `flathub` oder `debian` |
| `PAIMENOS_EMULATORS` | Leer: interaktiv; `empfohlen`, `all`, `none` oder IDs mit Kommas |
| `PAIMENOS_HARDWARE_PROFILE` | Leer: interaktiv mit Vorschlag; `ultra-low`, `low`, `medium` oder `high` (siehe [Hardware-Profile](hardware-profiles.md)) |
| `PAIMENOS_ENABLE_TLP` | `1` / `0`, Standard `1` |
| `PAIMENOS_ENABLE_ZRAM` | `1` / `0`, Standard `1` |
| `PAIMENOS_ENABLE_AUTO_UPDATES` | Debian-Sicherheitsupdates, Standard `1` |
| `PAIMENOS_ENABLE_APP_UPDATES` | Verwaltete Flatpak-Apps, Standard wie Auto-Updates |
| `PAIMENOS_REBOOT` | `1`: am Ende automatisch neu starten; Standard `0` |
| `PAIMENOS_PARENT_PIN` | Für automatisierte Installationen; sonst verdeckte Eingabe. Keine feste Standard-PIN |

Die Emulator-IDs stehen in `data/emulator-catalog.json`. Das Setup bzw. Backend installiert Emulator-Software; ROMs und erforderliche BIOS-Dateien bringt man selbst mit.

## Nach dem Neustart

Der Elternbereich ist lokal über das Menü sowie im Netz über `http://<Geräte-IP>/` erreichbar. Erst den Gerätecheck in `docs/device-validation.md` durchgehen. Falls das Setup abgebrochen wurde, Dienste nicht als erfolgreich eingerichtet betrachten: Die Abschlussmarkierung wird erst ganz am Ende geschrieben.

## Videos von USB und SD

Der Eintrag heißt im Kinder-Menü und Elternbereich `Kamera / Bilder / Videos`. Nach einem Update wird der bisherige Standardname beim Laden der App-Liste automatisch angepasst; eigene Namen, Icons und Freigaben bleiben erhalten.

Der Kamera-Medienbrowser zeigt Bilder und Videoclips gemeinsam und kennzeichnet Videos mit `▶ Video`. Ein Klick öffnet den eingebauten Player mit Wiedergabe/Pause, Zeitleiste, Lautstärke und vorherigem/nächstem Clip. Doppelklick auf das Video, F11 oder `Vollbild` blendet die Bedienung aus und füllt den Bildschirm mit dem Video. Ein weiterer Doppelklick, F11 oder Esc stellt die Bedienung wieder her, während der Clip weiterläuft. Leertaste pausiert oder startet auch im Vollbild; Esc in der normalen Playeransicht führt zurück zur Übersicht. Beim Zurückgehen, Schließen oder Entfernen des Mediums wird das Video gestoppt und die Datei freigegeben. Bildnavigation und Tux Paint bleiben auf Bilder beschränkt.

Erkannt werden MP4, M4V, MOV, AVI, MKV, WebM, MPEG/MPG, 3GP, MTS/M2TS/TS und OGV. Der Codec innerhalb der Datei entscheidet über die tatsächliche Abspielbarkeit. Defekte, entfernte oder nicht unterstützte Dateien zeigen einen Hinweis im Player; weitere Dateien bleiben auswählbar.

Die aus Version `0.63.5` übernommene Videoausgabe zeichnet über eine Qt-Grafikfläche. Die bisherige native GStreamer-Ausgabe kann mit `glimagesink` Eingaben außerhalb der Qt-Ereignisbehandlung konsumieren; der Filter aus `0.63.4` reicht dafür nicht aus. Das neue Release benötigt keine manuelle Einrichtung nach einem vollständigen Backend-Update.

Neuinstallationen und vollständige Backend-Updates installieren fehlende Qt-Multimedia- und GStreamer-Pakete automatisch. Für ein manuelles Code-Teilupdate können die Abhängigkeiten separat installiert werden:

```bash
sudo apt-get update
sudo apt-get install -y python3-pyqt5.qtmultimedia libqt5multimedia5-plugins \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav
sudo reboot
```

Ohne diese Pakete funktionieren Menü und Bildanzeige weiter; der Videoplayer weist auf die fehlende Einrichtung hin.

Für ein manuelles Teilupdate dieses Features sowohl `shared` als auch `desktop` wählen (`sudo bash update.sh --component shared --component desktop`). Das vollständige Backend-Update übernimmt beide Bereiche ohnehin.

## Sprachausgabe im Kinder-Menü (Piper)

Das Setup richtet Piper (`piper-tts` 1.8.0 in einem eigenen virtualenv) und zwei Stimmen für die Systemsprache unter `/usr/local/lib/paimenos-speech` ein (rund 320 MB; je Sprache eine Männer- und eine Frauenstimme: Deutsch `de_DE-thorsten-medium` und `de_DE-ramona-low`, Englisch `en_US-ryan-medium` und `en_US-amy-medium`). Die Stimme stammt von einem festen Hugging-Face-Commit und wird per SHA-256 geprüft. Schlägt das fehl, läuft das Setup weiter, die Sprachausgabe bleibt dann aus.

Auf einem bereits eingerichteten Gerät einmalig nachholen:

```bash
sudo python3 /usr/local/lib/paimenos/current/tools/install-paimenos-speech.py
```

Aktivieren und die Stimme (Frau oder Mann) wählen lässt sich im Eltern-Bereich unter **Einstellungen** (Standard: aus, Männerstimme). Piper läuft nicht dauerhaft: Das Menü erzeugt für jeden App-Namen einmalig eine WAV-Datei in `~/.local/share/paimenos/speech` (Dateiname aus Stimme und Text; ein Hintergrundprozess mit niedrigster Priorität, nur für fehlende Namen) und spielt beim Anwählen nur diese Datei ab. Neue oder umbenannte Apps bekommen ihre Aufnahme beim nächsten Neuladen der App-Liste, in der Regel innerhalb weniger Sekunden. `--check` zeigt, ob Piper und Stimme vorhanden sind.
