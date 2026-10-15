# Drucken mit Elternfreigabe

Das Kind druckt wie gewohnt aus Firefox-Webapps (Strg+P bzw. Menü) oder Tux Paint (Druckknopf, über `lpr`).
Die Eltern sehen jeden Auftrag im Eltern-Bereich unter **Drucken**, öffnen das Dokument und geben es frei oder
verwerfen es. Drucker werden auf derselben Seite gesucht, per Adresse hinzugefügt, als Standard gesetzt, mit einer
Testseite geprüft und entfernt.

## Modi

| Modus | Firefox | CUPS-Warteschlangen |
|---|---|---|
| **Aus** (Standard nach dem Update) | Drucken ausgeblendet (`print.enabled` false) | nehmen keine Aufträge an (`cupsreject`) |
| **Mit Freigabe** | Drucken möglich | `job-hold-until-default=indefinite`: CUPS hält jeden neuen Auftrag an |
| **Ohne Freigabe** | Drucken möglich | `job-hold-until-default=no-hold`: Aufträge drucken sofort, bleiben aber sichtbar |

Der Modus liegt root-eigen in `/var/lib/paimenos/printing/settings.json` (lesbar für die Firefox-Profile des
Kindes, schreibbar nur für den Dienst). Firefox übernimmt eine Änderung beim nächsten Start einer Webapp.

## Aufbau

- `paimenos-print.service` (`src/paimenos/printing.py`) läuft als root, spricht über pycups mit CUPS und
  richtet Drucker mit `lpadmin` ein. Der Elternbereich erreicht ihn über `/run/paimenos-print/api.sock`
  (wie WLAN und Bluetooth).
- Alle zwei Sekunden setzt der Dienst den Modus auf **alle** Warteschlangen durch, auch auf solche, die CUPS
  selbst für gefundene Netzwerkdrucker anlegt, gibt Drucker nicht im Netz frei und hält im Freigabe-Modus
  wartende Aufträge des Kindes an, die die Vorgabe umgehen (z. B. `lp -H immediate`). Freigegebene Aufträge
  merkt sich der Dienst, bis CUPS sie abgeschlossen hat.
- Drucker mit IPP-Adresse (`ipp://`, `ipps://`, `dnssd://`) werden ohne Treiber eingerichtet (IPP Everywhere /
  AirPrint). Für USB- und andere Drucker sucht CUPS einen installierten Treiber passend zur Geräte-ID.
- Die Vorschau liest die Spool-Datei des Auftrags (`/var/spool/cups/d…`). PDF und Bilder zeigt der Browser an,
  PostScript (Tux Paint) wird heruntergeladen. CUPS behält Dokumente standardmäßig einen Tag (`PreserveJobFiles`).
- Die CUPS-Weboberfläche (`localhost:631`) schaltet der Dienst ab, damit sie im Kinder-Browser nicht erreichbar ist.

Pakete: `cups`, `cups-bsd` (`lpr` für Tux Paint), `cups-filters`, `python3-cups`, `avahi-daemon` (Suche im Netz).
Bestehende Geräte bekommen sie mit dem Update der gemeinsamen Komponente; die frühere Firefox-Richtlinie
`PrintingEnabled: false` entfernt das Update aus `/etc/firefox/policies/policies.json`.

## Grenzen

- Wie bei WLAN und Bluetooth kann jeder Prozess des Benutzers `kids` den Dienst-Socket erreichen. In der
  Kiosk-Sitzung hat das Kind keine Shell; die Eltern-PIN schützt die Weboberfläche.
- Für eine Warteschlange, die CUPS gerade erst selbst angelegt hat, greift die Vorgabe spätestens nach zwei
  Sekunden. Drucker, die im Eltern-Bereich eingerichtet sind, halten Aufträge von Anfang an an.
- Das Kind erhält keinen Hinweis, dass ein Auftrag auf Freigabe wartet.
