# Flatpak-Apps im Eltern-Webbereich

Unter **Flatpak-Apps** (Menü im Webbereich, `/flatpaks`) lassen sich zusätzliche Apps aus
[Flathub](https://flathub.org) suchen, installieren und wieder entfernen. Voraussetzung ist eine
Internetverbindung; Flathub enthält auch Apps, die nicht für Kinder gedacht sind, deshalb entscheiden
die Eltern bei jeder App.

## Ablauf

- **Suche:** Der Webbereich fragt die Flathub-Suche (`flathub.org/api/v2/search`) und zeigt bis zu 24
  Desktop-Anwendungen mit Name, Beschreibung und Symbol. Der Suchbegriff ist das Einzige, was gesendet wird.
- **Installieren:** Der Webbereich schickt nur die geprüfte App-ID (`[A-Za-z0-9_]+(.[A-Za-z0-9_-]+){2,}`) an
  den Root-Dienst. Dieser installiert die stabile Version systemweit aus der Quelle `paimenos-flathub`,
  legt das Startskript `/usr/local/bin/paimenos-app-fp-…` an (die App läuft mit denselben Einschränkungen wie
  die Apps aus dem Setup: kein Zugriff auf Home und PaimenOS-Daten), trägt die App in
  `/var/lib/paimenos/flatpak-apps.list` ein und legt eine Kachel im Kinder-Menü an.
- **Flatpak fehlt?** Dann richtet der Dienst es beim ersten Mal ein (`apt-get install flatpak …`, Internet
  nötig) und fügt Flathub als Quelle hinzu.
- **Entfernen:** Erst verschwindet die Kachel, dann wird die App deinstalliert und werden nicht mehr benötigte
  Laufzeiten entfernt. Auf Wunsch werden auch die gespeicherten App-Daten (`~/.var/app/<ID>`) gelöscht,
  sonst bleiben sie erhalten. Nur Apps aus `flatpak-apps.list` lassen sich entfernen, also solche, die
  PaimenOS selbst installiert hat (auch die aus dem Setup).
- **Aktualisierungen:** Alle Apps aus `flatpak-apps.list` aktualisiert der Timer `paimenos-app-update.timer`.
  Wurden automatische Aktualisierungen im Setup nicht abgewählt, aktiviert die erste Installation über die Seite
  den Timer.

## Technik

| Teil | Datei |
|---|---|
| Suche, ID-Prüfung, Liste (Webbereich, als `kids`) | `src/paimenos/flatpak_store.py` |
| Aufträge als Root (über den Socket des Emulator-Dienstes) | `src/paimenos/flatpak_service.py` |
| Kachel anlegen/entfernen (als `kids`) | `src/paimenos/cli_flatpak_menu.py` |
| Seite und Routen | `assets/parent-web/flatpaks.html`, `src/paimenos/parent_web.py` |

Der Root-Dienst startet die Menü-Änderung über `systemd-run --uid=kids`, weil seine eigene Unit mit
`ProtectHome=read-only` läuft. Es läuft immer nur ein Auftrag; der Fortschritt steht in
`/var/lib/paimenos/flatpak-job.json` und wird auf der Seite angezeigt. Ein Neustart während eines Auftrags
wird als unterbrochen gemeldet. Tests: `tests/test_flatpak_store.py`.

## Grenzen

- Der Webbereich läuft wie das Kinder-Menü als Benutzer `kids`; die PIN schützt die Oberfläche, ist aber keine
  Sicherheitsgrenze gegen Kinder mit Zugang zu einem Terminal. Das gilt genauso für die Emulator-Installation.
- Ohne Internet sind Suche und Installation nicht möglich; Entfernen funktioniert auch offline.
