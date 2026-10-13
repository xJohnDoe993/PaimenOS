# Architektur

## Programmcode und Daten

Seit 0.64.0 heißt das Produkt PaimenOS (Paimen: Finnisch für Schäfer). Das Python-Paket heißt `paimenos`, die Paket-API ist `2`. Die neue Installation verwendet eigene Pfade und Dienste; LaurinOS-Paket-API `1` und dessen Daten werden nicht automatisch migriert.

| Zielpfad | Zweck / Eigentümer |
|---|---|
| `/usr/local/lib/paimenos/releases/<Version>-<Hash>/` | Vollständiger Code-Stand, root-eigen |
| `/usr/local/lib/paimenos/current` | Atomar umgeschalteter Link auf den aktiven Stand |
| `/usr/local/lib/paimenos/previous` | Vorheriger Code-Stand für Rollback |
| `<Release>/app/paimenos/` | Installiertes Python-Paket aus `src/paimenos/` |
| `<Release>/assets/`, `<Release>/data/`, `<Release>/tools/` | Ressourcen und Programmhelfer |
| `/usr/local/bin/paimenos-*`, `/usr/local/sbin/paimenos-wlan-handoff` | Links auf Startprogramme des aktiven Releases; Flatpak-App-Starter werden vom Setup erzeugt |
| `/home/kids/.config/paimenos/` | `settings.json`, `apps.json`, Web-Sitzungsschlüssel; Eigentümer `kids` |
| `/home/kids/.local/state/paimenos/` | Protokolle, Sperren, Menüanforderungen und Medienstatus |
| `/home/kids/.local/share/paimenos/` | Icons, Controller-Profile, Emulator-Spiele und Spielstände |
| `/home/kids/.config/openbox/` | Nur Openbox-Konfiguration und Autostart |
| `/home/kids/.mozilla/paimenos-webapps/` | Browserprofile der Webapps |
| `/var/lib/paimenos/` | Systemzustand der Emulator-Installation und Liste verwalteter Flatpak-Apps |
| `/var/lib/paimenos/updates/` | root-eigener Versionscache und dauerhafter Update-Auftrag |
| `/var/cache/paimenos-updates/` | Temporäre Release-Downloads; nach Abschluss entfernt |
| `/usr/local/share/paimenos/` | Heruntergeladene RetroArch-Profile und PSP-Zusatzdateien; kein Python-Programmcode |
| `/run/paimenos-*` | Dienst-Sockets und Wartungssperren |

Root-Dienste importieren keinen Programmcode aus dem Kinderverzeichnis. `run.py` startet das Paket aus seinem eigenen, aufgelösten Release-Verzeichnis. Der Aufruf mit `python3 -I` verhindert das Übernehmen eines fremden `PYTHONPATH` oder benutzereigener Python-Pakete. Laufende Prozesse behalten ihren geladenen Release; nach einem Update werden die Dienste neu gestartet.

## Installationsmodule

`install.sh` prüft das Zielsystem, fragt die PIN ab, sperrt parallele Wartung und lädt die Module in einer gemeinsamen Shell. Dadurch bleiben Paketlisten, App-Auswahl und Pfade zwischen den Schritten verfügbar.

| Modul | Aufgabe |
|---|---|
| `10-system.sh` | Python-Basis, erstes Code-Deployment, Emulator-Auswahl, Debian-Quellen und Basispakete |
| `20-hardware.sh` | Firmware, TLP, ZRAM, Sicherheitsupdates, Touchpad und Journald |
| `30-plymouth.sh` | Boot-Theme und Initramfs |
| `40-user.sh` | Benutzer, Gruppen, Verzeichnisse und Cursor |
| `50-browser.sh` | Polkit, DNS, Firefox-Vorgaben und Webapp-Icons |
| `60-apps.sh` | App-Auswahl, Debian/Flatpak, Starter und App-Update-Timer |
| `70-defaults.sh` | Erste Einstellungen und App-Liste |
| `80-services.sh` | Emulatoren installieren, Systemdienste und deren Startprüfung |
| `network.sh` | WLAN an NetworkManager übergeben; vom Dienstmodul geladen |
| `90-desktop.sh` | LightDM, Openbox und systemd-Benutzerdienste |
| `99-finish.sh` | Datenrechte, Startziel und Abschlussmarkierung |

Die eigentliche Programmfunktion bleibt in Python-Dateien. Konfigurationen werden mit `install_repo_file` kopiert; zwei dynamische Dateien werden mit einem Renderer erzeugt, der nur benannte Platzhalter ersetzt und keinen Shell-Code auswertet.

## Paketgrenzen

Die vorhandenen v59-Funktionsbereiche wurden zunächst als eigenständige Python-Module übernommen und ihre Imports auf `paimenos.*` umgestellt. Das Menü bleibt ein größeres Modul; eine weitere Aufteilung in einzelne Widgets kann separat erfolgen. WLAN-Client und privilegierter WLAN-Dienst verwenden jetzt dieselbe root-eigene Datei. Der Emulator-Katalog liegt in JSON, die Eltern-Webansichten in HTML-Dateien.

Seit 0.61.0 sprechen die authentifizierten Backend-Routen über den lokalen Update-Socket mit `update_service.py`. Der Prüfdienst holt ausschließlich Metadaten aus dem festgelegten GitHub-Repo. Ein Klick startet einen separaten Systemd-Worker, der den Release-Download mit `release_source.py` prüft und anschließend das vorhandene Deployment unter den gemeinsamen Wartungssperren aufruft. Der Prüfdienst kann beim Aktivieren des neuen Releases neu starten, ohne den Worker zu beenden.

`manifest.json` beschreibt die gemeinsam aktualisierbaren Komponenten. Jede installierte Version enthält `installed.json` mit Herkunftsversion und SHA-256 pro Komponentendatei. Nach einem Teilupdate können Komponenten unterschiedliche Versionen haben. Der Hash im Release-Verzeichnis bezeichnet die Kombination dieser Komponenten.

## Energiesparen

Der Benutzerdienst `paimenos-idle` (`idle.py`) liest Gamepad-Ereignisse nur mit, ohne sie abzufangen. Ein Bluetooth-Controller ohne Tastendruck oder deutliche Stick-/Steuerkreuzbewegung wird nach 5 Minuten über die vorhandene Bluetooth-Verwaltung getrennt. Die Kopplung bleibt erhalten. Die meisten Controller schalten sich danach selbst aus und verbinden sich per Home-/Start-Taste wieder. Der Bluetooth-Adapter selbst bleibt eingeschaltet, sonst könnte sich der Controller nicht mehr selbst verbinden.

Gibt es 30 Minuten lang weder Tastatur-/Maus-/Touchpad-Eingaben (X-Leerlaufzeit) noch Gamepad-Eingaben, Tonwiedergabe oder eine laufende USB-Sicherung, setzt der Dienst den logind-IdleHint der Kindersitzung. Der Root-Dienst `paimenos-power` (`power.py`) versetzt das Gerät dann in Bereitschaft (Suspend-to-RAM). Er verzichtet darauf, wenn Wartungssperren oder Update-/Paket-Units aktiv sind oder ein Programm den Ruhezustand per logind-Inhibitor blockiert. Ruhezustand auf Festplatte (Hibernation) ist nicht vorgesehen, da PaimenOS nur ZRAM-Swap einrichtet. CPU-C-States verwaltet der Kernel ohnehin selbst. Weil die Entscheidung über den IdleHint läuft, ist keine Polkit-Regel nötig; Backend-Updates liefern die Funktion daher vollständig aus.

Bekannte Grenzen: Eine Bedienung des Web-Elternbereichs vom Handy aus zählt nicht als Aktivität. Spiele, die dauerhaft Ton ausgeben, halten das Gerät wach.
