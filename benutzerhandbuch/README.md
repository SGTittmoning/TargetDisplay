# Benutzerhandbuch – Anforderungen und Pflege

Diese Datei legt fest, wie das deutsche Benutzerhandbuch aufgebaut ist und gepflegt wird. Sie gilt für
jede Änderung am Handbuch, egal von wem.

## Dateien

| Datei | Inhalt |
|---|---|
| `Benutzerhandbuch.md` | Textquelle (Markdown) |
| `Benutzerhandbuch.html` | gestaltete Fassung, Grundlage für das PDF |
| `Benutzerhandbuch.pdf` | Druckfassung (A4), aus der HTML-Datei erzeugt |
| `screenshots/NN_*.png` | Abbildungen, `NN` = Abbildungsnummer |
| `assets/fake_scheibe.png` | künstliches Kamerabild für Screenshots (siehe unten) |

- Markdown und HTML haben immer denselben Inhalt. Jede Textänderung wird in beiden Dateien gemacht.
- Nach jeder Änderung an Text, Bildern oder Gestaltung wird das PDF neu erzeugt und geprüft (siehe
  „PDF erzeugen und prüfen").
- Die Versionsangabe oben im Handbuch (Monat, App-Version) passt zum Stand der App.

## Aufbau

- Kapitel 1 beschreibt die Bedienung für alle Vereinsmitglieder, Kapitel 2 den PIN-geschützten
  Administrativen Bereich für Standaufsichten. Der Begriff ist durchgehend „Standaufsicht".
- Jedes Bedienelement bekommt eine Nummer und eine verständliche Erklärung. Die Nummerierung beginnt in
  jeder Gruppe neu bei 1 (Kopfzeile, Zoom, Blinken, Timer, Fußbereich, jeder Admin-Bildschirm).
  Verweise im Text lauten „(Gruppe, Punkt N)".
- Die Legende erklärt ein Element vollständig. Die Abschnitte „… im Detail" ergänzen nur
  Zusammenhänge und wiederholen die Legende nicht.
- Jeder Screenshot hat eine Bildunterschrift „Abb. N" mit fortlaufender Nummer.
- „Bei Problemen" enthält keinen Text-Platzhalter für den Kontakt, sondern Schreiblinien zum Ausfüllen
  von Hand.
- Am Ende steht der Abschnitt „Notizen", nur aus Schreiblinien.

## Screenshots

- Screenshots zeigen die echte Oberfläche der App (keine Nachbauten), in 1280×800.
- **Keine echten Anlagendaten:**
  - Standname „Stand 1".
  - Als Kamerabild dient die künstliche Scheibe `assets/fake_scheibe.png` (ein Ausschnitt aus dem
    öffentlichen `docs/screenshot.png`), nie echtes Trainingsmaterial.
  - Bei „Video aus" ist das Platzhalter-Logo aus `ressources/logo.png` zu sehen, nicht das
    Vereinslogo.
  - Keine echten Kamera-URLs, Zugangsdaten oder PINs im Bild.
- Erzeugung: Entweder die App unter Xvfb (1280×800) mit einer Ersatzkamera starten, die
  `fake_scheibe.png` liefert, oder am Testgerät mit einem Dummy-Stream dieses Bildes. Am Gerät
  vorgenommene Änderungen (Standname, Kalibrierung, Logo, Stream) werden danach zurückgenommen.
- Bildausschnitt und Zoomstufe passen zur Aussage der Bildunterschrift und sind innerhalb des
  Handbuchs einheitlich.
- Ändert sich die Oberfläche, werden alle betroffenen Screenshots neu erstellt, ebenso
  `docs/screenshot.png` für das README.

## Seitenumbrüche im PDF

Das Druck-CSS steht im `@media print`-Block von `Benutzerhandbuch.html`. Es setzt diese Regeln um:

- Eine Überschrift steht nie allein am Seitenende, sie bleibt mit dem folgenden Inhalt zusammen.
- Überschrift, Einleitungstext und zugehörige Abbildung bleiben zusammen (Wrapper `.grp`).
- Eine Abbildung wird nie geteilt und bleibt bei ihrer Bildunterschrift.
- Eine Legende wird nicht über zwei Seiten getrennt und bleibt bei ihrer Abbildung.
- Ein Hinweis („Wichtig: …") steht auf derselben Seite wie der Inhalt, auf den er sich bezieht.
- Tabellen werden nicht getrennt. Ist eine Tabelle länger als eine Seite, wiederholt sich die
  Kopfzeile, und keine Zeile wird geteilt.
- Absätze werden nur mit mindestens drei Zeilen vor und nach dem Umbruch getrennt.
- Kapitel beginnen auf einer neuen Seite. „Notizen" folgt direkt auf „Bei Problemen".
- Keine Seite enthält nur einen Rest (eine Schlusszeile, einen einzelnen Hinweis oder ein
  Legendenende).

Passt etwas nicht auf eine Seite, werden zuerst die Abbildungen im Druck kleiner (Höhenbegrenzung im
Druck-CSS), statt Zusammengehöriges zu trennen.

## PDF erzeugen und prüfen

```bash
cd benutzerhandbuch
weasyprint Benutzerhandbuch.html Benutzerhandbuch.pdf
# Übersicht aller Seiten zum Durchsehen
pdftoppm -r 40 -png Benutzerhandbuch.pdf /tmp/hb/p
montage /tmp/hb/p-*.png -tile 6x -geometry +6+6 /tmp/hb/uebersicht.png
```

Jede Seite wird gegen die Regeln oben durchgesehen, bevor das PDF eingecheckt wird. Das
Inhaltsverzeichnis im PDF nimmt die Seitenzahlen automatisch aus dem Druck.
