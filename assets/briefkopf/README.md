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

Beide Bilder stammen aus der Word-/Writer-Vorlage der Schule.

## Schriften

ReportLab kann nur TrueType-Umrisse einbetten. `FrenteH1-Regular.ttf` ist deshalb
die aus der systemweit installierten `FrenteH1-Regular.otf` erzeugte
TrueType-Fassung derselben Schrift. `Calligraffiti.ttf` ist die Google-Font
(Apache License 2.0).

Die Fließtextschrift (Calibri Light, ersatzweise Carlito) wird nicht mitgeliefert,
sondern in den Schriftverzeichnissen des Systems gesucht. Fehlt eine Datei,
weicht der Brief automatisch auf eine PDF-Standardschrift aus – er wird dann
schlichter, aber er entsteht.
