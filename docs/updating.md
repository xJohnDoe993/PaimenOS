# PaimenOS: Updates und Releases

## Umbenennung und Neuinstallation

Version 0.64.0 ist das erste PaimenOS-Release. Paimen ist Finnisch für Schäfer. Die Umbenennung umfasst Oberfläche, Python-Paket (`paimenos`), Befehle, Systemd-Dienste, Verzeichnisse, Installationsvariablen (`PAIMENOS_*`) und Update-Archive. Die Umbenennung führte Paket-API `2` ein.

Eine vorhandene LaurinOS-Installation einschließlich 0.63.6 kann dieses Release nicht als Backend-Update installieren. Das alte Updateformat und die alten Datenverzeichnisse werden nicht migriert. Bilder, ROMs und Spielstände vorher sichern, frisches Debian installieren und anschließend PaimenOS mit `sudo bash install.sh` einrichten. Der Installer verweigert die parallele Einrichtung über eine erkannte LaurinOS-Installation. Ab dieser PaimenOS-Neuinstallation sind die folgenden Backend-Updates wieder möglich.

## Englisch-Unterstützung und Paket-API 3

Die gemeinsame Übersetzungsbibliothek erfordert Paket-API `3`. Eine vorhandene PaimenOS-Installation mit API `2` erhält diese Änderung einmalig durch ein **manuelles vollständiges Update aus dem neuen Projektordner**: `bash update.sh --check`, dann `sudo bash update.sh`. Der bisher installierte Backend-Updater kann API `3` nicht einspielen; Teilupdates mit `--component` sind für diesen Übergang ebenfalls gesperrt. Eine Debian-Neuinstallation ist dafür nicht nötig. Eltern-Einstellungen, Apps und gespeicherte Daten bleiben erhalten. Sprachwahl und englische Standard-Webapps sind unter [Lokalisierung](localization.md) beschrieben.

## PaimenOS-Boot-Theme

Das Setup verwendet das mitgelieferte PaimenOS-Theme. Ab einer PaimenOS-
Installation übernimmt ein vollständiges Code-Update bzw. ein Teilupdate von
`tools` den Helfer und alle Theme-Ressourcen. Ein Code-Update aktiviert die
Boot-Systemkonfiguration nicht selbst. Die einmalige Aktivierung und die
Rückkehr zu einem bisherigen Theme stehen unter
[Plymouth](installation.md#paimenos-plymouth-theme).

## GitHub als Update-Quelle

Die Quelle ist das [PaimenOS-Repo](https://github.com/xJohnDoe993/PaimenOS/releases). Der frühere Name `xJohnDoe993/LaurinOS` wird bei Release-Dateien als Alias akzeptiert. `paimenos-updates.service` prüft beim Start und alle sechs Stunden das neueste veröffentlichte stabile Release. Entwürfe und Pre-Releases werden nicht angeboten. Tags müssen `vMAJOR.MINOR.PATCH` heißen, zum Beispiel `v0.65.0`; `VERSION` und Manifest enthalten `0.65.0`. Die Prüfung vergleicht Versionsnummern numerisch und installiert nichts automatisch.

Angemeldete Eltern sehen einen Hinweis auf allen Backend-Seiten und können unter **Updates** manuell prüfen, Release-Notizen lesen und ein Update starten. Offline bleiben die letzte Prüfung und der Fehler sichtbar. Download und Aktivierung laufen als eigener Dienst `paimenos-update-job.service`. Der Auftrag überlebt einen Neustart des Webbackends; nach Stromausfall wird er als unterbrochen gemeldet und nicht automatisch fortgesetzt.

Ein Release benötigt diese beiden Assets:

| Asset für Version 0.65.0 | Inhalt |
|---|---|
| `PaimenOS-0.65.0.zip` | Mit `tools/build-release.py` gebautes Projektarchiv, Stammordner `PaimenOS/` |
| `PaimenOS-0.65.0.zip.sha256` | SHA-256 und Dateiname des Archivs |

GitHubs automatisch erzeugte „Source code“-Archive ersetzen diese Assets nicht. Archive im alten LaurinOS-Format werden nicht akzeptiert. ZIP-Download höchstens 50 MiB; entpackt höchstens 100 MiB und 5.000 Einträge.

## Automatischer Release-Build auf GitHub

Der Workflow `.github/workflows/release.yml` heißt **PaimenOS Release**. Er prüft Quellstand, Tests und echte Videowiedergabe unter X11, baut ZIP/SHA-256 und bestätigt beide Uploads. Tags und manuelle Läufe erstellen weiterhin einen Entwurf. Ein Push auf einen Branch `release/vMAJOR.MINOR.PATCH` veröffentlicht nach erfolgreicher Prüfung ein neues stabiles Release mit den passenden Notizen aus `docs/releases/MAJOR.MINOR.PATCH.md`.

1. Änderungen in `main` zusammenführen. `VERSION`, `src/paimenos/__init__.py` und `pyproject.toml` müssen dieselbe Version enthalten. Manifest mit `python3 tools/build-manifest.py` erzeugen und committen.
2. [Actions](https://github.com/xJohnDoe993/PaimenOS/actions) öffnen: **PaimenOS Release → Run workflow**, Branch `main`, passenden neuen Tag eingeben.
3. Erfolgreichen Lauf abwarten. Der Workflow installiert Flask, Qt Multimedia sowie X11-/Video-Testabhängigkeiten und erstellt den Release-Entwurf.
4. Unter [Releases](https://github.com/xJohnDoe993/PaimenOS/releases) prüfen, dass ZIP und SHA-256 für die gewählte Version vollständig hochgeladen wurden. In den Release-Notizen nötige API-Übergänge und die Neuinstallation für LaurinOS-Geräte nennen.
5. Die Dateien auf einem Testgerät mit frischem Debian installieren und die [Geräteprüfung](device-validation.md) durchführen. Danach **Publish release**, ohne **Pre-release**, als neuestes stabiles Release veröffentlichen.
6. Spätere PaimenOS-Releases mit höherer Version werden im Elternbackend unter **Updates** angeboten.

Alternativ startet ein neuer Tag den Workflow automatisch:

```bash
git tag v0.65.0
git push origin v0.65.0
```

Ein vorhandener Tag wird auch bei Auswahl von `main` aus dessen ursprünglichem Commit gebaut. Die Eingabe eines Tags erhöht die Projektversion nicht. Veröffentlichte Tags/Assets nicht ersetzen; Korrekturen erhalten eine neue Versionsnummer. Bei einem unvollständigen Upload den Entwurf unveröffentlicht lassen, nur seine ZIP-/SHA-Dateien entfernen und neu bauen. Ein bereits veröffentlichter Release wird vom Helfer nicht verändert.

Für die direkte Veröffentlichung zuerst Änderungen, Versionsdateien und `docs/releases/0.65.0.md` prüfen und in `main` übernehmen. Anschließend den geprüften Stand auf `release/v0.65.0` pushen. Dieser Branch ist ein ausdrücklicher Veröffentlichungsauftrag: Nur bei bestandenen Tests, übereinstimmenden Versionsdateien, korrekter ZIP-Prüfsumme und vollständig bestätigten Uploads wird das Release als neuestes stabiles Release veröffentlicht. Der Tag wird am gebauten Commit erstellt und danach nochmals geprüft. Ein bereits vorhandener Tag wird auf diesem Weg abgelehnt. Kein zusätzlicher Workflow-Token ist nötig.

Es ist kein zusätzlicher Token nötig: Der Workflow verwendet `GITHUB_TOKEN` mit `contents: write`. **Run workflow** erscheint, nachdem der Workflow in `main` übernommen wurde. Für einen lokalen Build `python3 tools/build-release.py` ausführen und beide Dateien aus `dist/` an einen passenden Release-Entwurf anhängen.

## Repository auf GitHub umbenennen

Das bestehende Repository heißt jetzt `xJohnDoe993/PaimenOS`. Der Updater verwendet diese Adresse und akzeptiert zusätzlich Release-Dateien unter dem früheren Namen `xJohnDoe993/LaurinOS`. Damit führt die Umbenennung nicht mehr zur Meldung „Release-Datei stammt nicht aus dem festgelegten PaimenOS-Repo“.

Bei einem eigenen Git-Checkout das Remote aktualisieren:

```bash
git remote set-url origin https://github.com/xJohnDoe993/PaimenOS.git
```

### Abweichende Release-Quelle ausdrücklich zulassen

Unter **Updates** vor **Nach Updates suchen** die Option **Abweichende Release-Quelle zulassen** auswählen. Sie erlaubt eine abweichende GitHub-Repository-Adresse für die ZIP-/SHA-Dateien, die die festgelegte Release-API anbietet. Die tatsächliche Quelle wird angezeigt und vor der Installation nochmals bestätigt. Es gibt keine freie URL-Eingabe und keine dauerhafte Abschaltung der Quellenprüfung; automatische Prüfungen verwenden den Standard. Die Zustimmung wird nur im jeweiligen Installationsauftrag gespeichert, damit auch dessen separater Worker die Quelle erneut prüfen kann.

ZIP und Prüfsumme müssen aus demselben GitHub-Repository und zum selben Tag gehören. HTTPS, erlaubte Download-Hosts, Dateigrößen, SHA-256, Archivstruktur, Versionsprüfung und die Sperre gegen ältere Versionen bleiben aktiv. Die Option erzwingt keine Installation beschädigter Archive und kein Downgrade.

Auf Geräten mit dem bisherigen Updater muss diese Codeänderung zuerst über das vorhandene lokale Deployment eingespielt werden; eine neue Option kann nicht rückwirkend im alten Backend erscheinen. Dafür ist keine Neuinstallation nötig: siehe **Auf dem Gerät aktualisieren** unten.

## Prüfung und Vertrauensmodell

Der Dienst lädt nur über HTTPS von GitHub und den erlaubten GitHub-Asset-Hosts. Archivgröße, SHA-256, ein gegebenenfalls von GitHub gelieferter Digest, sichere ZIP-Pfade, Tag/Archiv-Version sowie Manifest, Paket-API und Python-Syntax werden vor der Aktivierung geprüft. Der aktuell installierte Deployment-Code führt die Installation aus; heruntergeladene Deployment-Skripte werden nicht vor der Aktivierung ausgeführt.

Die SHA-Datei sichert die Integrität; sie ist keine unabhängige Signatur. Schreibberechtigte dieses Repos sind damit auch Herausgeber von root-ausgeführten Updates. Der lokale Kontroll-Socket akzeptiert ausschließlich root und den fest vorgesehenen `kids`-Benutzer; die Webaktionen benötigen zusätzlich Eltern-Anmeldung und CSRF-Token. Quelle, Download-URLs und Systembefehle sind nicht frei über den Socket wählbar. Private Repos mit Token werden derzeit nicht unterstützt.

Code, Systemd-Units und deklarierte fehlende Debian-Abhängigkeiten werden aktualisiert. Flatpak-Einrichtung, übrige Systemkonfiguration und Datenmigrationen benötigen eigene Einrichtungsschritte. Bei einer vom installierten Deployment-Code nicht unterstützten Paket-API ist weiterhin eine manuelle Aktualisierung oder Neuinstallation nötig; dies muss in den Release-Notizen stehen.

Status und Fehlersuche auf dem Gerät:

```bash
sudo journalctl -u paimenos-updates.service -u paimenos-update-job.service -u paimenos-packages.service -n 100 --no-pager
sudo cat /var/lib/paimenos/updates/state.json
cat /usr/local/lib/paimenos/current/installed.json
```

## Arbeiten im Repo

Quellcode direkt in `src/paimenos/` bearbeiten. Vor einer Freigabe:

```bash
python3 tools/build-manifest.py
python3 -B tools/check.py
python3 tools/build-release.py
```

`VERSION` ist die Release-Version. `manifest.json` muss denselben Wert enthalten. `build-manifest.py` aktualisiert das Inventar, wenn Dateien oder Komponenten hinzugefügt oder entfernt werden. Beim Ändern einer bestehenden Datei muss keine Prüfsumme von Hand gepflegt werden: Das Deployment berechnet sie für die Versionsaufzeichnung.

Das Release-Werkzeug erzeugt ein Archiv und eine SHA-256-Datei in `dist/`. Beide Dateien als GitHub-Release-Assets veröffentlichen, wie oben beschrieben.

### Direkt auf ein Testgerät übertragen

```bash
make deploy                                   # Ziel: SSH-Alias paimenos-t450
make deploy DEVICE=john@192.168.10.196 ARGS="--component desktop"
```

`tools/deploy-to-device.sh` prüft die Quellen, kopiert das Arbeitsverzeichnis (auch uncommittete und neue Dateien) nach `~/paimenos-deploy` auf dem Gerät und startet dort `tools/deploy.py`. Das Passwort für `sudo` wird im Terminal abgefragt; für unbeaufsichtigte Läufe kann `DEPLOY_SUDO_PASSWORD` gesetzt werden. Das Tastatur- und Menü-Verhalten lässt sich ohne Gerät mit `tests/test_menu_navigation.py` prüfen (benötigt PyQt5, läuft mit `QT_QPA_PLATFORM=offscreen`).

### Test-VM (QEMU/KVM)

`tools/vm.sh` legt eine wiederverwendbare Debian-13-VM an (`create`), startet sie mit sichtbarem Fenster (`start`), richtet PaimenOS darin ein (`install`, mit `PAIMENOS_PARENT_PIN`, `PAIMENOS_APPS` usw. ohne Rückfragen) und überträgt Änderungen (`deploy`). `snapshot NAME` und `reset NAME` sichern und stellen Zustände wieder her; `vm.sh` ohne Argument zeigt alle Befehle. Daten liegen in `~/VMs/paimenos`, SSH-Alias `paimenos-vm`, Eltern-Web auf `http://localhost:8080`.

## Auf dem Gerät aktualisieren

Archiv aus einer vertrauenswürdigen Quelle kopieren und entpacken. Alle folgenden Befehle aus dem neuen Projektordner ausführen:

```bash
bash update.sh --check
sudo bash update.sh
```

Apps und Spiele vor dem Update schließen. Ein aktives Kinder-Menü und die Gerätedienste werden zur Übernahme des Codes neu gestartet. Ohne laufende Benutzersitzung startet der neue Sitzungscode bei der nächsten Anmeldung.

`update.sh` installiert deklarierte fehlende Debian-Pakete. Es überschreibt keine Eltern-Daten und verändert keine WLAN-Profile, Firefox-Profile, TLP-Regeln oder andere PaimenOS-Systemkonfigurationen. Flatpak-Einrichtung und weitere Systemänderungen müssen als gesonderte Einrichtungsschritte dokumentiert werden. Die Komponente `services` installiert die im Release enthaltenen PaimenOS-Systemd-Units neu.

## Debian-Pakete bei Updates

Releases deklarieren benötigte Pakete in `data/update-packages.json`, getrennt nach Manifest-Komponenten. Beispiel:

```json
{
  "schema": 1,
  "components": {
    "desktop": ["python3-pyqt5.qtmultimedia", "gstreamer1.0-libav"]
  }
}
```

Die aktuelle Datei enthält die fünf Qt-Multimedia-/GStreamer-Pakete für die Videowiedergabe. Für eine neue Funktion deren benötigte Debian-Paketnamen ergänzen und ein neues Release bauen. Erlaubt sind reine Paketnamen aus den bereits eingerichteten APT-Quellen; Shell-Befehle, neue Quellen, URLs und Paketversionen gehören nicht in diese Datei. Die Zuordnung wird beim Staging in `installed.json` gespeichert. Teilupdates ersetzen nur Anforderungen der gewählten Komponenten und behalten die übrigen Anforderungen bei. PaimenOS 0.64.0 enthält diesen Mechanismus bereits in der Neuinstallation.

Bei späteren Updates prüft der bereits installierte Pakethelfer vor dem Codewechsel die Anforderungen des vorbereiteten Releases. Wenn alle Pakete installiert sind, werden keine Paketlisten geladen und kein APT-Installationslauf gestartet. Sonst aktualisiert er die Paketlisten und installiert fehlende Pakete ohne Rückfragen. Er verwendet die exakten Kandidatenversionen aus den bestehenden Quellen, übernimmt bestehende Konfigurationsdateien und verweigert Paketentfernungen. APT kann für die neuen Pakete weitere Abhängigkeiten installieren oder vorhandene Abhängigkeiten aktualisieren; ein allgemeines Systemupgrade oder automatisches Entfernen alter Pakete erfolgt nicht. Ein eventuell installiertes `needrestart` wird für diesen Aufruf auf reine Anzeige gestellt; PaimenOS startet seine Dienste selbst bei der Aktivierung neu.

Der Backend-Fortschritt zeigt Paketprüfung und Nachinstallation. APT-/DNS-Fehler brechen das Update vor dem Codewechsel ab; der bisherige Code bleibt aktiv. Nach Behebung des Fehlers das Update erneut anbieten lassen und starten. Bereits installierte Pakete bleiben erhalten und werden beim nächsten Versuch übersprungen. Ein Code-Rollback entfernt oder degradiert keine Debian-Pakete.

`paimenos-packages.service` prüft zusätzlich beim Start die benötigten Pakete, bevor Elternbackend und Update-Prüfdienst starten dürfen. Der Dienst lädt nichts herunter, solange keine Pakete fehlen.

Paketinstallation und andere PaimenOS-Wartung werden durch Sperren serialisiert. APT wartet bis zu zwei Minuten auf seine Paketsperre und verwendet begrenzte Verbindungswartezeiten sowie Wiederholungen. Wird ein Installationslauf durch Ausschalten unterbrochen und meldet APT anschließend eine beschädigte Paketverwaltung, muss diese auf dem Gerät repariert werden; der Updater führt keine pauschalen Reparaturbefehle aus.

## Teilupdates

```bash
bash update.sh --list
sudo bash update.sh --component controller
sudo bash update.sh --component backend --component network
```

| Komponente | Inhalt |
|---|---|
| `shared` | Gemeinsame Pfade, Datenzugriff, Bilder, Diagnose, Webapp-CSS, OSD-Zustand und Launcher |
| `desktop` | Menü, Timer, Medien und Overlays |
| `controller` | Gerätefilter, Controller-Reader und Belegungsprofile |
| `network` | WLAN, Bluetooth, lokale Dialoge und Netzwerkstatus |
| `emulators` | Katalog, Spieleverwaltung, Installationsdienst und Client |
| `backend` | Gemeinsame Elternverwaltung, Webserver und HTML-Ansichten |
| `updates` | Lokaler Update-Client, GitHub-Download und Prüfdienst/Installationsworker |
| `cli` | Starter, gemeinsame Flatpak-Startlogik und Hardware-Befehle |
| `tools` | Installations-/Wartungshelfer als Release-Dateien |
| `services` | System- und Benutzerdienste |

Ein Teilupdate kopiert nur gewählte Bereiche in eine Kopie des aktiven Releases. Die restlichen Dateien werden aus dem installierten Stand übernommen. Es gibt keine automatische Abhängigkeitsauflösung: Bei einer Änderung einer Schnittstelle zwischen Komponenten alle betroffenen Bereiche gemeinsam aktualisieren. Bei inkompatiblen Paketänderungen `runtime_api` im Manifest und `API` in `tools/deploy.py` erhöhen; dann ist ein vollständiges Update erforderlich.

## Wechsel und Rollback

1. Manifest, Pfade und Python-Syntax prüfen.
2. Laufende Emulator-Paketinstallation bzw. parallele Wartung ausschließen.
3. Neuen Stand in einem Staging-Verzeichnis aufbauen; vorherige Dateien ausgewählter Komponenten entfernen.
4. Ressourcen und gemeinsame Python-Imports prüfen.
5. Fehlende deklarierte Debian-Pakete installieren; bei Fehler den Codewechsel abbrechen.
6. Den aktiven Link atomar auf den fertigen Code-Stand umschalten.
7. Gegebenenfalls Systemd-Units kopieren, Dienste neu starten und deren aktiven Zustand prüfen.
8. Bei einem erkannten Startfehler den bisherigen Code und gegebenenfalls die bisherigen Units wieder aktivieren und erneut starten.

Die Aktivierung der Code-Version ist atomar. Dienstneustarts und das Kopieren von Systemd-Units sind keine atomare Gesamttransaktion. Ein Stromausfall während dieser Schritte kann manuelle Wiederherstellung erfordern. Ein aktiver Dienst bedeutet außerdem nicht, dass jede Hardwarefunktion erfolgreich getestet wurde.

Manuell zurückkehren:

```bash
sudo bash update.sh --rollback
```

Das betrifft Code und PaimenOS-Units. Spielstände, Eltern-Einstellungen, heruntergeladene Cores, BIOS-Dateien, Debian-/Flatpak-Pakete und sonstige Daten werden nicht zurückgesetzt. Ein Code-Rollback ist keine Datensicherung.

Versionen anzeigen:

```bash
cat /usr/local/lib/paimenos/current/installed.json
```

Frühere Releases bleiben für Fehlersuche erhalten. Eine automatische Löschstrategie ist noch nicht enthalten.

## Squid-Proxy entfernt

Der frühere lokale Squid-Cache-Proxy (`127.0.0.1:3128`) wird nicht mehr installiert: Er cachte bei HTTPS
praktisch nichts, verdoppelte den Firefox-Cache und verhinderte, dass Firefox seine DNS-over-HTTPS-Einstellung
nutzt. Bestehende Geräte werden beim Backend-Update bereinigt: `tools/deploy.py` stoppt und deaktiviert
`squid.service` (das Paket bleibt installiert; `sudo apt purge squid` entfernt es), und beim nächsten Start
einer Webapp oder des Eltern-Bereichs entfernt `prepare_profile` die alten Proxy-Einstellungen aus der
Firefox-Vorlage und setzt `network.proxy.type` auf 0.
