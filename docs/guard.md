# Guard: Eltern-PIN und Einschränkungen

Ab 0.76.0 liegen die Eltern-PIN und alle Einschränkungen nicht mehr in einer Datei im Home von `kids`, sondern in einem
root-eigenen Speicher, den nur der Dienst `paimenos-guard.service` verwaltet. Vorher konnte jedes Programm, das ein Kind
starten kann (Terminal, Texteditor, Dateidialog), `~/.config/paimenos/settings.json` lesen und damit die PIN erfahren oder
Tageslimit und Nutzungszeitraum ändern.

## Was der Guard schützt

| | |
|---|---|
| Eltern-PIN | als scrypt-Hash mit Salt, nie im Klartext und nie lesbar für `kids` |
| Tageslimit, Bonuszeit, verbrauchte Zeit | nur über benannte Operationen; das Kind darf nur Zeit melden (`tick`, höchstens 300 s je Meldung) |
| Nutzungszeitraum und Tagesfreigabe | Änderung nur mit Eltern-PIN |
| Gesperrte Apps (`disabled_apps`) | Änderung nur mit Eltern-PIN |
| Farbe im Kinder-Menü | darf das Kind selbst ändern (einziger Schlüssel ohne PIN) |

Speicher: `/var/lib/paimenos/guard/state.json` (0600, root). Socket: `/run/paimenos-guard/guard.sock` (root:kids 0660, nur
uid 0 und `kids`). Ein Marker `/var/lib/paimenos/guard.enabled` (für alle lesbar, nur von root anlegbar) zeigt Kinderprozessen,
dass der Guard zuständig ist. Erst dann wird nie mehr die Datei im Home verwendet, auch nicht als Ausweichlösung: Ist der
Guard nicht erreichbar, melden Sperrbildschirm und Eltern-Login einen Fehler, Timer und Menü versuchen es später wieder
(der Timer arbeitet höchstens zwei Minuten mit dem letzten Stand weiter, danach sperrt er).

## PIN-Prüfung und Sperrzeit

Eltern-Login im Web und PIN-Eingabe am Sperrbildschirm laufen über dieselbe Prüfung. Nach 5 Fehlversuchen insgesamt sperrt der
Guard für 30 Sekunden, jeder weitere Fehlversuch verdoppelt die Zeit bis höchstens 15 Minuten. Während der Sperre wird auch die
richtige PIN abgelehnt. Der Zähler überlebt einen Neustart des Dienstes. Wer die PIN errät, braucht also bei vier Ziffern
Stunden bis Tage; wer sie nur stört, sperrt den Elternzugang höchstens 15 Minuten (ein Kind kann das provozieren).

Nach der Anmeldung im Web hält der Prozess der Eltern-Weboberfläche einen Token im Arbeitsspeicher (30 Minuten Leerlauf); im
Cookie steht nur eine Sitzungs-ID. Der Sitzungsschlüssel der Weboberfläche liegt im Home von `kids`: Ein gefälschtes Cookie
ergibt keinen Token und damit keine Änderungsrechte. Eine PIN-Änderung beendet alle anderen Web-Sitzungen.

## PIN vergessen

Die PIN lässt sich nicht mehr auslesen. Auf der Konsole des Administrators:

```bash
sudo paimenos-reset-pin
```

Das setzt eine neue PIN, hebt eine Sperrzeit auf und beendet alle Web-Sitzungen. `kids` kann das Werkzeug nicht ausführen.

## Update auf 0.76.0 und Zurückrollen

Beim ersten Start übernimmt der Guard PIN und Einstellungen aus der bisherigen Datei. Die Datei bleibt **15 Minuten** bestehen
und wird dann entfernt. In dieser Zeit funktioniert ein automatischer oder manueller Rollback auf die vorherige Version ohne
Zutun (die Einstellungen in der Datei sind dann der Stand vor dem Update).

Danach hat die ältere Version keine Datei mehr. Vor dem Zurückrollen:

```bash
sudo paimenos-reset-pin --export-legacy    # fragt eine neue PIN und schreibt die Datei mit den aktuellen Einstellungen
sudo python3 /usr/local/lib/paimenos/current/tools/deploy.py --rollback
```

Beim nächsten Update auf eine Version mit Guard übernimmt dieser die Datei wieder (mit der damals gesetzten PIN) und entfernt sie
erneut nach 15 Minuten. Einen Rollback ohne diesen Schritt erkennt man daran, dass Elternbereich und Sperrbildschirm
„Eltern-Einstellungen fehlen“ melden.

Der Dienst wird bei Updates über `paimenos-emulators.service` aktiviert (siehe [Updates](updating.md#systemdateien-bei-updates)),
weil der bisher installierte Updater neue Dienste zwar kopiert, aber nicht aktiviert.

## Was weiterhin offen ist

Der Guard schützt die **Einstellungen**. Er verhindert nicht, dass ein Kind mit Shell-Zugriff:

- die Kinder-Dienste anhält (`timer`, `menu`, Sperrbildschirm laufen als Benutzer `kids`) und so die Sperre umgeht,
- Programme außerhalb des Menüs startet (gesperrte Apps sind nur im Menü gesperrt),
- `apps.json` ändert (eigene Kacheln mit beliebigem Startbefehl als `kids`).

Wirksam wären dafür ein Wächter als root, der den Zeitverbrauch selbst zählt und bei fehlendem Timer eingreift, sowie ein
eingeschränkter Start von Programmen. Das ist bewusst nicht Teil dieses Schritts. Bis dahin bleibt der beste Schutz, Kindern
kein Terminal und keinen Texteditor mit Dateidialog zu geben (Apps-Auswahl im Elternbereich).

Außerdem entfernt **Flatpak-Apps entfernen** gesperrte App-IDs nicht mehr aus der Sperrliste (der Aufräumschritt läuft als
`kids` und darf die Einstellungen nicht ändern). Übrig bleibende IDs sind unwirksam; wird dieselbe App später erneut
installiert, ist sie weiterhin gesperrt, bis sie im Elternbereich freigegeben wird.
