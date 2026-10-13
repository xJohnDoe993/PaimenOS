# PaimenOS-ISO: Debian-Basis und interaktives Setup

Unter **Actions → Build PaimenOS ISO → Run workflow** den gewünschten
Branch auswählen. Das Artefakt **PaimenOS-ISO-amd64** enthält ISO und SHA-256.
Buildprotokolle stehen auch bei Fehlern bereit. Downloads bleiben sieben Tage
verfügbar; der Workflow veröffentlicht keinen Release.

## Installation

1. ISO auf einen USB-Stick schreiben und davon booten.
2. Im Bootmenü **PaimenOS installieren** wählen . Der PaimenOS-Installer (`iso/install.sh`) fragt der Reihe nach:
   Sprache, Tastatur-Layout, Zeitzone, WLAN (ja/nein), Zielplatte und ein
   Administrator-Konto (Name und Passwort, nicht `kids`). Die **ganze Platte** wird benutzt
   und gelöscht; eine eigene Partitionierung gibt es nicht.
3. Der Installer teilt die Platte ein (UEFI: 512 MB EFI-Partition + ext4, BIOS: GPT mit
   BIOS-Boot-Partition + ext4), kopiert das Live-System darauf (`filesystem.squashfs`),
   richtet Sprache, Tastatur, Zeitzone, Administrator und Bootloader ein und startet das
   installierte System per `kexec` **ohne Firmware-Neustart**.
4. Dort erscheint auf der ersten Textkonsole sofort die PaimenOS-Einrichtung.
   Bei Bedarf mit **1** über `nmtui` WLAN einrichten; mit **2** das Setup starten.
   **4** zeigt die Netzwerkdiagnose und prüft den Paketabruf ausschließlich per IPv4.
5. Eltern-PIN, Emulatoren, Apps und Debian/Flathub-Paketquelle im normalen
   Setup selbst wählen. Nach Abschluss neu starten: Die Kinderoberfläche erscheint.

Der USB-Stick muss **stecken bleiben**, wenn das Gerät beim Setup kein Internet hat
(siehe [Offline-Installation](#offline-installation)). Startet `kexec` nicht (zum Beispiel
wegen Secure Boot), startet das Gerät normal neu und setzt die Einrichtung beim Hochfahren
fort; dann nicht vom Stick booten. Das Protokoll der Installation steht in
`/var/log/paimenos-install.log` (im Live-System).

Gespeicherte WLAN-Zugänge aus dem Live-System werden in das installierte System übernommen.
Der Bootloader besteht aus den Paketen `grub-pc-bin`, `grub-efi-amd64-bin`,
`grub-efi-amd64-signed` und `shim-signed`; nach größeren GRUB-Updates kann
`sudo grub-install` nötig sein.

Die PaimenOS-Einrichtung übernimmt die im Installer gewählte Systemsprache.
Englisch führt zu englischen Setup-Dialogen und englischsprachigen Standard-Webapps.
Unter Debian 13 gilt `/etc/locale.conf` vor einem älteren Eintrag in
`/etc/default/locale`; unter Debian 12 wird weiterhin dessen Locale-Datei verwendet.

## Bootmenü

BIOS und UEFI nutzen dasselbe GRUB-Menü (`iso/grub/`, von `iso/build.sh` über
`config/bootloaders/grub-pc` in live-build eingespielt, statt der Vorlage von
Debian). Es enthält nur **PaimenOS installieren** und bei UEFI die
Firmware-Einstellungen. Der Eintrag startet das Live-System mit `paimenos.install`, worauf das
Live-Menü den Installer direkt öffnet; bricht man ihn ab, bleibt das Live-Menü (WLAN, Konsole,
Neustart, Ausschalten). Einen reinen Live-Test gibt es nicht, da das Live-System keine
Kinderoberfläche enthält. Der Debian-Installer ist nicht mehr auf der ISO. Failsafe, Memtest
und Prüfsummen entfallen. Das Logo steht in
`iso/grub/splash.png` (erzeugt mit `tools/make-grub-splash.py`), die Farben in
`iso/grub/live-theme/theme.txt`. Syslinux wird nicht mehr verwendet.

## Offline-Installation

Die ISO enthält neben dem Debian-Grundsystem eine signierte Offline-Paketquelle
(`/paimenos-repo`, rund 0,9 GB; berechnet relativ zu einem minimalen Debian-Grundsystem, damit nichts fehlt, was der Installer aus dem Live-System entfernt): alle Debian-Pakete des PaimenOS-Kerns
(Kinderoberfläche, Eltern-Webbereich, Firefox, Controller, WLAN, Energiesparen,
Firmware) plus eine kleine App-Auswahl (Tux Paint, Geany, Tux Math, SuperTux).
Die Liste steht in `installer/offline-packages.txt`; ein Test hält sie mit
`installer/10-system.sh` und `installer/20-hardware.sh` synchron.

- **Online bevorzugt:** Das Setup wartet kurz auf das Netzwerk (`nm-online`) und prüft dann
  das Internet. Funktioniert es, läuft alles mit aktuellen Paketen und voller App-Auswahl.
  Schlägt die Prüfung fehl, wechselt das Setup nicht mehr still auf Offline: Es zeigt die
  Fehler und fragt, ob es erneut versuchen oder mit den Offline-Paketen weitermachen soll.
- **Ohne Internet** bindet das Setup das Installationsmedium (Volume-Label
  `PAIMENOS`) ein und installiert daraus (`PAIMENOS_OFFLINE=1`). **Der USB-Stick
  muss bis zum Ende des Setups stecken bleiben.** Beim Neustart nach der
  Debian-Installation also nicht entfernen.
- **Webapps** (KiKA, Die Maus usw.) sind nach dem Setup vorhanden, das Kinder-Menü blendet
  sie aber ohne Internet aus und zeigt sie, sobald eine Verbindung besteht.
- **Prüfungen ohne Netz:** DNS-, WLAN- und APT-Prüfungen des Setups, die ein funktionierendes
  Internet voraussetzen, entfallen mit `PAIMENOS_OFFLINE=1`. Die Familien-DNS-Konfiguration
  wird trotzdem geschrieben und aktiviert.
- **Im Offline-Modus entfallen:** Emulatoren, Flathub-Apps, die übrigen Apps,
  Sprachausgabe (Piper) und die heruntergeladenen Webseiten-Logos. Alles lässt sich
  später mit Internet nachinstallieren (Eltern-Bereich bzw.
  `install-paimenos-speech.py`). Das fertige System hat danach normale Debian-
  Online-Quellen für Updates.
- **Signatur:** Der Build signiert das Repo. Mit dem GitHub-Secret
  `PAIMENOS_REPO_SIGNING_KEY` (privater Schlüssel, ASCII-armored) signiert er mit
  dem Projektschlüssel, ohne Secret mit einem Einmalschlüssel dieses Builds. Der
  öffentliche Schlüssel liegt als `paimenos-repo.gpg` im Repo, der Fingerabdruck in
  `FINGERPRINT`. Schlüssel erzeugen:
  `gpg --quick-generate-key 'PaimenOS Offline-Repo <offline@paimenos.invalid>' ed25519 sign never`,
  dann `gpg --armor --export-secret-keys <ID>` als Secret hinterlegen.
- **Bau:** `iso/build-offline-repo.sh` läuft am Ende von `iso/build.sh`
  (`PAIMENOS_OFFLINE_REPO=0` überspringt es für schnelle Testbauten). Es lädt die
  Pakete im fertigen Live-System per `apt-get install --download-only` mit denselben
  Optionen wie das Setup und fügt das Repo mit `xorriso` ein, ohne die Bootkonfiguration
  zu ändern.

Das ISO enthält das Debian-Grundsystem, NetworkManager, WLAN-Firmware und
root-eigenen PaimenOS-Quellcode. Es installiert beim ISO-Bau keine Kinderprogramme,
Emulatoren, Eltern-Dienste oder voreingestellte PIN. Das vollständige Setup
benötigt auf dem Gerät eine Internetverbindung und verwendet unverändert
`/opt/paimenos-source/install.sh`, den aktuellen modularen Setup-Prozess des Repos.
Das Plymouth-Theme wird ebenfalls erst durch dieses Setup aktiviert.

Der Live-Modus startet auf tty1 ein Textmenü (`iso/live-menu.sh`) mit
**PaimenOS installieren**, **WLAN/Netzwerk einrichten** (`nmtui`), **Konsole
öffnen** (Benutzer `live`, mit `exit` zurück), **Neu starten** und
**Ausschalten**. „Installieren“ startet den PaimenOS-Installer (siehe oben). Das Menü läuft nur im Live-System (`/run/live/medium`), nie
auf dem installierten Gerät. Es gibt keine fertige Kinderoberfläche im
Live-System. Die automatische Einrichtung wird nur auf dem installierten
System durch den PaimenOS-Installer aktiviert. Nach erfolgreichem Setup wird sie
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
Der Build prüft, dass `/live/filesystem.squashfs` auf der fertigen ISO liegt;
der Installer kopiert genau dieses Abbild.

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
