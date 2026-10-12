# PaimenOS-ISO: Debian-Basis und interaktives Setup

Unter **Actions → Build PaimenOS ISO → Run workflow** den gewünschten
Branch auswählen. Das Artefakt **PaimenOS-ISO-amd64** enthält ISO und SHA-256.
Buildprotokolle stehen auch bei Fehlern bereit. Downloads bleiben sieben Tage
verfügbar; der Workflow veröffentlicht keinen Release.

## Installation

1. ISO auf einen USB-Stick schreiben und davon booten.
2. Im Bootmenü den Debian-Installer starten. Systemsprache, Zielplatte und Administrator-Zugang
   selbst wählen; als Administrator einen anderen Namen als `kids` verwenden.
3. Installation abschließen, USB-Stick entfernen und neu starten.
4. Auf der ersten Textkonsole erscheint die PaimenOS-Einrichtung automatisch.
   Bei Bedarf mit **1** über `nmtui` WLAN einrichten; mit **2** das Setup starten.
   **4** zeigt die Netzwerkdiagnose und prüft den Paketabruf ausschließlich per IPv4.
5. Eltern-PIN, Emulatoren, Apps und Debian/Flathub-Paketquelle im normalen
   Setup selbst wählen. Nach Abschluss neu starten: Die Kinderoberfläche erscheint.

Die PaimenOS-Einrichtung übernimmt die im Debian-Installer gewählte Systemsprache.
Englisch führt zu englischen Setup-Dialogen und englischsprachigen Standard-Webapps.
Unter Debian 13 gilt `/etc/locale.conf` vor einem älteren Eintrag in
`/etc/default/locale`; unter Debian 12 wird weiterhin dessen Locale-Datei verwendet.

Das ISO enthält das Debian-Grundsystem, NetworkManager, WLAN-Firmware und
root-eigenen PaimenOS-Quellcode. Es installiert beim ISO-Bau keine Kinderprogramme,
Emulatoren, Eltern-Dienste oder voreingestellte PIN. Das vollständige Setup
benötigt auf dem Gerät eine Internetverbindung und verwendet unverändert
`/opt/paimenos-source/install.sh`, den aktuellen modularen Setup-Prozess des Repos.
Das Plymouth-Theme wird ebenfalls erst durch dieses Setup aktiviert.

Der Live-Modus startet auf tty1 ein Textmenü (`iso/live-menu.sh`) mit
**PaimenOS installieren**, **WLAN/Netzwerk einrichten** (`nmtui`), **Konsole
öffnen** (Benutzer `live`, mit `exit` zurück), **Neu starten** und
**Ausschalten**. „Installieren“ lädt den Debian-Installer vom Boot-Medium per
`kexec` (grafisch, sonst Text) und startet ihn mit derselben `preseed.cfg` wie
der Bootmenü-Eintrag. Geht das nicht, etwa bei Secure Boot, erklärt das Menü
den Neustart und bietet ihn an; im Bootmenü dann „Graphical Install“ oder
„Install“ wählen. Das Menü läuft nur im Live-System (`/run/live/medium`), nie
auf dem installierten Gerät. Es gibt keine fertige Kinderoberfläche im
Live-System. Die automatische Einrichtung wird nur auf dem installierten
System durch den Debian-Installer aktiviert. Nach erfolgreichem Setup wird sie
deaktiviert und erscheint bei späteren Neustarts nicht erneut.

Bei einem Fehler kann das Setup im selben Menü erneut gestartet werden. Eine
bereits angelegte modulare Installation wird mit `--resume` fortgesetzt. Wird
das Gerät vorher ausgeschaltet, startet die Einrichtung beim nächsten Boot wieder.
Manuell ist das Fortsetzen auch möglich:

```bash
sudo bash /opt/paimenos-source/install.sh --resume
```

Das ISO-Setup prüft vor der Eltern-PIN und vor der Paketinstallation, ob APT
aktuelle, signierte Paketlisten abrufen kann. Eine aktive Ethernet- oder
WLAN-Verbindung allein bestätigt keinen Internetzugang. Scheitert der Abruf,
erscheinen die APT-Fehler sowie IPv4-/IPv6-Routen, die IPv4-Namensauflösung und
getrennte TCP-Verbindungstests zu `deb.debian.org` auf Port 80 und 443.
Die Diagnose lässt sich ohne Änderungen am System wiederholen:

```bash
python3 /opt/paimenos-source/iso/prepare-apt.py --diagnose
```

Ohne weitere Konsole im Setup **4) Netzwerkdiagnose / IPv4-Test** wählen.
Der Test zeigt die Netzwerkdaten und führt einen vollständigen APT-Abruf der
Paketlisten ausschließlich über IPv4 aus. Dabei werden die Debian-Onlinequellen
vorbereitet und Paketlisten aktualisiert; Programme werden nicht installiert.
Nach dem Ergebnis bleibt die Ausgabe bis zum Drücken von Enter stehen und das
Setup-Menü erscheint erneut. Auch ein fehlgeschlagener Test beendet das Setup nicht.

In GNOME Boxes werden manche Tastenkombinationen vom Host abgefangen. Über den
Tastaturknopf in der Kopfleiste **Strg+Alt+F2** oder **Strg+Alt+F3** an die VM
senden, um eine weitere Konsole zu öffnen; **Strg+Alt+F1** führt zum Setup zurück.
Siehe die [GNOME-Anleitung](https://help.gnome.org/gnome-boxes/keystrokes.html.de).

Ist der Debian-Server per IPv4 erreichbar, per IPv6 jedoch nicht, verwendet APT
während der Einrichtung IPv4. Die temporäre APT-Konfiguration wird bei jedem
Setup-Versuch neu geprüft und beim Beenden entfernt. IPv6 bleibt im System
aktiv; bestehende Netzwerkprofile, DNS- und Proxy-Einstellungen werden beibehalten.
Scheitert auch IPv4, hilft diese Ausweichlösung nicht: In einer VM dann Adresse,
Standardroute und die Internetweiterleitung des Hosts prüfen. Ein erfolgreicher
TCP-Test bestätigt noch keinen erfolgreichen HTTPS- oder APT-Abruf; deshalb
bleibt die Prüfung durch APT erforderlich.

## Build und Prüfung

Der Build läuft in einem privilegierten Debian-13-Container auf einem
GitHub-Runner, ohne zusätzliche Repository-Secrets. BIOS- und UEFI-Bootloader
sind enthalten; Secure Boot ist noch nicht verifiziert. Nur vertrauenswürdige
Branches bauen. Das ISO enthält den eingecheckten Stand, keine Git-Zugangsdaten.
Die Installer-Vorkonfiguration liegt unter `/cdrom/preseed.cfg`; der Build
extrahiert sie aus dem fertigen ISO und prüft den Inhalt gegen die Quelle.

Für einen lokalen Build in Debian 13 mit root-Rechten:

```bash
apt-get update
apt-get install -y live-build debootstrap git ca-certificates xorriso squashfs-tools grub-pc-bin grub-efi-amd64-bin mtools dosfstools
bash iso/build.sh
```

Änderungen vorher committen: Der Build verwendet `git archive HEAD`.
`/var/tmp/paimenos-live` darf noch nicht existieren; für weitere Builds ein neues
Verzeichnis über `PAIMENOS_BUILD_DIR` wählen. Ergebnis: `dist/iso/`.

Vor Freigabe in einer VM BIOS/UEFI-Boot und Debian-Installation testen. Nach
Entfernen des ISO prüfen: Setup startet, Netzwerk lässt sich einrichten,
App-/Emulator-Auswahl funktioniert, Fehler lassen sich fortsetzen, danach
startet die Kinderoberfläche und die Einrichtung bleibt deaktiviert. Anschließend
T450-WLAN, Bluetooth, Ton, Suspend und Controller prüfen. Ein vollständiger
ISO-/Installations- und Hardwaretest wurde für diese Umstellung noch nicht ausgeführt.
