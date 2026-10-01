# Briefkopf der Elternschreiben

Diese Dateien bilden den Briefkopf aus `Einladung_Schulanmeldung.odt` nach. Sie
werden von `sl_office/parent_portal/letterhead.py` gelesen; die Pfade lassen sich
über `SL_OFFICE_SCHOOL_LOGO` bzw. `SL_OFFICE_SCHOOL_SIGNATURE` umstellen.

| Datei | Verwendung |
| --- | --- |
| `logo.png` | Schullogo oben rechts (400 × 280 px, transparent) |
| `unterschrift.png` | Unterschrift über der Namenszeile (404 × 132 px, transparent) |
| `FrenteH1-Regular.ttf` | Hausschrift für Wortmarke und Titelleiste |
| `Calligraffiti.ttf` | Schreibschrift des Mottos |
| `Carlito-Regular/Bold/Italic.ttf` | Fließtext aller Schreiben und Formulare |

Beide Bilder stammen aus der Word-/Writer-Vorlage der Schule.

## Schriften

ReportLab kann nur TrueType-Umrisse einbetten. `FrenteH1-Regular.ttf` ist deshalb
die aus der systemweit installierten `FrenteH1-Regular.otf` erzeugte
TrueType-Fassung derselben Schrift. `Calligraffiti.ttf` ist die Google-Font
(Apache License 2.0).

Die Fließtextschrift ist **Carlito** und liegt hier im Projekt. Sie ist metrisch
mit Calibri deckungsgleich – Zeilen brechen also genau wie in der Writer-Vorlage –
und steht unter der SIL Open Font License 1.1 (`Carlito-OFL.txt`), darf also
mitgeliefert werden. Calibri selbst darf das nicht und wird deshalb nur benutzt,
wenn es auf dem Rechner ohnehin installiert ist.

Dadurch sehen die erzeugten PDFs auf dem Server genauso aus wie auf dem
Arbeitsplatz. Früher wurde die Fließtextschrift nur im System gesucht; fehlte
sie dort, wichen die Schreiben still auf Helvetica aus. Der Notnagel besteht
weiterhin, greift aber nur noch, wenn auch diese Dateien fehlen.

Was tatsächlich benutzt wird, zeigt:

```bash
venv/bin/flask --app app check-schriften
```
