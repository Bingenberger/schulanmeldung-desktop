# Briefkopf der Elternschreiben

Diese Schriften setzen den Briefkopf. Sie werden von
`sl_office/briefe/letterhead.py` gelesen.

Logo und Unterschrift liegen nicht im Projekt: jede Schule lädt ihre eigenen
unter **Verwaltung → Schulprofil** hoch, dort liegen sie in der Datenbank.
Ersatzweise lassen sich Pfade über `SL_OFFICE_SCHOOL_LOGO` bzw.
`SL_OFFICE_SCHOOL_SIGNATURE` angeben.

| Datei | Verwendung |
| --- | --- |
| `FrenteH1-Regular.ttf` | Hausschrift für Wortmarke und Titelleiste |
| `Calligraffiti.ttf` | Schreibschrift des Mottos |
| `Carlito-Regular/Bold/Italic.ttf` | Fließtext aller Schreiben und Formulare |

## Schriften

ReportLab kann nur TrueType-Umrisse einbetten. `FrenteH1-Regular.ttf` ist deshalb
die aus der systemweit installierten `FrenteH1-Regular.otf` erzeugte
TrueType-Fassung derselben Schrift. Frente H1 stammt von Rodrigo Brod (Estudio
Frente) und steht unter Creative Commons BY-SA (`FrenteH1-LIZENZ.txt`), darf
also mit Namensnennung und unter derselben Lizenz mitgeliefert werden.
`Calligraffiti.ttf` ist die Google-Font (Apache License 2.0).

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
