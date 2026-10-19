# USB-Backups im Elternbackend

1. USB-Medium anschließen und im Kindermenü einbinden lassen. Es wird nicht formatiert.
2. Alle Emulatoren und Apps beenden. Im Elternbackend **USB-Backups** öffnen und das Medium auswählen.
3. ROMs mit Menüeinträgen, Spielstände/Savestates, BIOS-Dateien, Spielstände und
   Einstellungen einzelner Apps, die Eltern-Einstellungen und/oder eigene Dateien auswählen.
4. **Auf USB sichern** starten und den Abschluss abwarten. Danach im Kindermenü sicher auswerfen.

Erkannte vollständige Backups werden nach Auswahl des Mediums auf derselben Seite
angeboten. Für die Wiederherstellung Inhalte auswählen und das Ersetzen bestehender
Dateien bestätigen. Zusätzliche lokale Dateien bleiben erhalten. ROM-Menüeinträge
werden hinzugefügt bzw. anhand ihrer ID aktualisiert; Startbefehle werden lokal neu
aufgebaut. Emulatoren/Core-Systemdateien und Apps sind auf dem Zielgerät separat zu
installieren. Backups sind unverschlüsselt.

## Eltern-Einstellungen und Webapps

Die Auswahl **Eltern-Einstellungen und Webapps (ohne PIN)** sichert Tageslimit, Nutzungszeitraum, Hintergrundfarbe,
Kategorie-Reiter, Vorlesen, gesperrte Apps und die selbst angelegten oder geänderten Webapps (Name, Adresse, Kategorie,
Aussprache). **Nicht** enthalten sind die Eltern-PIN, Bonuszeit, verbrauchte Zeit und die Tagesfreigabe: Die PIN bleibt
auf dem Zielgerät, wie sie ist. Auch Webapp-Symbole, Browserprofile (Anmeldungen) und Apps mit Startbefehl werden nicht
gesichert, weil deren Startbefehl als Kind läuft.

Mit dem [Guard](guard.md) setzt die Wiederherstellung die Einstellungen über einen eigenen, auf „Einstellungen setzen“ beschränkten Token (sechs Stunden gültig), den die Weboberfläche beim Start erzeugt. Beim Wiederherstellen wird der gesamte Inhalt zuerst geprüft (Prüfsumme, Format, nur `http`/`https`-Adressen, gültige
Werte). Schlägt etwas fehl, bleibt das Gerät unverändert. Webapps mit bekannter ID werden aktualisiert, neue werden
angelegt; trägt eine ID ein anderes Programm, wird sie übersprungen. Gesperrte Apps werden nur übernommen, wenn es sie auf
dem Gerät gibt.

## Format und Verhalten

- Verzeichnis `PaimenOS-Backups/backup-<Zeit>-<ID>` auf dem gewählten USB-Medium.
- JSON-Manifest mit Kategorien, relativen Dateipfaden, Größen und SHA-256-Prüfsummen.
- 64-MiB-Teilstücke statt einer großen Archivdatei: damit funktionieren auch große
  Wii-ROMs auf FAT32, sofern genügend freier Platz vorhanden ist.
- ROMs: `~/.local/share/paimenos/emulators/roms`, plus begrenzte Spiele-Metadaten.
- Spielstände: `saves` und `states` im selben Emulator-Verzeichnis, einschließlich
  Wiederaufnahme-Kennzeichnung; zusätzlich `~/.config/retroarch/states` für ältere Zustände.
- BIOS: `~/.local/share/paimenos/emulators/bios`.
- App-Daten (je App eine Auswahl, nur angeboten, wenn lokal Daten existieren): die
  Ordner aus `data/app-data-catalog.json`, z. B. Luanti-Welten, Tux-Paint-Bilder,
  SuperTuxKart-/GCompris-Fortschritt und Einstellungen von LibreOffice, GIMP, Geany
  und VLC. Flatpak-Apps werden über `~/.var/app/<App-ID>` ohne `cache` gesichert,
  Debian-Apps über ihre üblichen Ordner. Beide Varianten werden an ihren ursprünglichen
  Ort zurückgeschrieben; ein Wechsel zwischen Flatpak und Debian-Paket migriert nichts.
  Orte ohne gesicherte Dateien werden beim Wiederherstellen nicht angelegt.
- Eigene Dateien (standardmäßig nicht angehakt): XDG-Ordner Dokumente, Bilder, Musik
  und Videos laut `~/.config/user-dirs.dirs`, sonst `Dokumente`/`Documents` usw.
- In App- und eigenen Ordnern werden Verknüpfungen und Sonderdateien übersprungen
  (die Anzahl wird angezeigt) statt das ganze Backup abzubrechen. Lokale Verknüpfungen
  bleiben bei einer Wiederherstellung als Verknüpfung erhalten und werden nie verfolgt.
- Läuft eine ausgewählte App noch (`pgrep`), werden Backup und Wiederherstellung
  abgewiesen. Überlappende Ordner (z. B. Dokumente in einem App-Ordner) werden abgelehnt.
- Backups mit App-Daten oder eigenen Dateien werden von älteren PaimenOS-Versionen
  nicht angezeigt.
- Auflistung benötigt nur das begrenzte Manifest; Inhaltsprüfsummen werden bei der
  Wiederherstellung geprüft. Ein sichtbares Backup ist daher noch kein bestandener
  vollständiger Integritätstest.
- Unvollständige `.partial-*`-Verzeichnisse werden nicht zur Wiederherstellung angeboten.
  Nach einem abgebrochenen Backup dürfen diese auf dem USB-Medium manuell gelöscht werden.

Nur durch `lsblk` als USB erkannte, bereits eingehängte Dateisysteme werden angeboten.
Zugriffe bleiben an offenen Verzeichnisdeskriptoren des ausgewählten Mediums verankert.
Absolute Pfade, Traversal und symbolische Links aus Backups werden nicht übernommen.
Die Prüfsummen erkennen beschädigte Daten, sind jedoch keine digitale Signatur.

Während einer Operation schützt eine Prozesssperre die Emulator-Daten; Emulatorstarts
und Uploads werden abgewiesen. Die App-Verwaltung ist währenddessen gesperrt. Backups
sollten nicht gleichzeitig mit manuellen Dateiveränderungen außerhalb von PaimenOS
oder einem Software-Update ausgeführt werden.

## Wiederherstellung und Fehler

Ausgewählte Daten werden zuerst neben dem jeweiligen Ziel in einem Arbeitsverzeichnis
vorbereitet: vorhandene Dateien kopieren, Backup-Inhalte darüberlegen und prüfen.
Dadurch wird zusätzlicher lokaler Speicher für die ausgewählten Daten benötigt.
Erst danach werden die vorbereiteten Verzeichnisse umgeschaltet. Ein Journal hält
fest, ob alle Umschaltungen abgeschlossen sind. Fehler rollen unvollständige
Umschaltungen zurück; alte Daten werden erst nach erfolgreichem Abschluss entfernt.

Nach einem Prozessabbruch/Neustart mit offenem Journal blockieren Emulatorstarts.
Im Backup-Bereich **Unterbrochene Wiederherstellung reparieren** wählen: eine noch
nicht abgeschlossene Übernahme wird zurückgesetzt, eine abgeschlossene wird aufgeräumt.
Auch vor einem neuen Backup-Vorgang erfolgt diese Reparatur. Das USB-Medium wird für
sie nicht benötigt. Andere App-/Dateiveränderungen bis dahin vermeiden.

Ein Fehler (USB entfernt, schreibgeschützt, voller Datenträger, Prüfsumme falsch)
wird im Backend angezeigt. Nach Neustart des Webdienstes ist der bisherige
Fortschrittsstatus nicht mehr verfügbar; fertige USB-Backups und ein offenes
Wiederherstellungsjournal bleiben erkennbar.

## Geräteprüfung

Auf einem USB-Stick alle Kategorien sichern, einzelne lokale Test-Spielstände
ändern und nur Spielstände zurückholen. Danach ROMs inklusive Menüeinträgen auf einer
zweiten Installation prüfen. Für App-Daten eine Luanti-Welt (Flatpak und Debian)
und ein Tux-Paint-Bild sichern, ändern und zurückholen; mit geöffneter App muss der
Vorgang abgewiesen werden. Zusätzlich FAT32 mit einer ROM >4 GiB, zu wenig freien
Platz, Abziehen während des Schreibens und Reparatur nach Dienst-Neustart testen.
Automatische Tests verwenden temporäre Dateisysteme und simulierte USB-Erkennung;
ein physischer USB-/Stromausfalltest ist damit nicht ersetzt.
