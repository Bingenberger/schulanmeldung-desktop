"""Bögen, Feldtypen und der vorbelegte Kriterienkatalog.

Der Katalog entspricht den Bögen der Serverfassung. Jede Schule übernimmt ihn
beim ersten Öffnen und passt ihn unter „Verwaltung → Kriterien“ an.
"""

#: Bogen -> (Bezeichnung, Modul aus :mod:`sl_office.features`).
BOEGEN = {
    "diagnostik": ("Pädagogische Diagnostik", "diagnostik"),
    "schulspiel": ("Schulspiel", "schulspiel"),
    "schularzt": ("Schulärztliche Untersuchung", "schularzt"),
}

TYPEN = {
    "skala": "Skala ++ / + / o / –",
    "janein": "Ankreuzfeld (ja/nein)",
    "auswahl": "Auswahl aus einer Liste",
    "mehrfach": "Mehrfachauswahl",
    "text": "Freitext",
    "datum": "Datum",
}

#: Skalenwerte wie in der Serverfassung: 3 = ++, 2 = +, 1 = o, 0 = –.
SKALA = ((3, "++"), (2, "+"), (1, "o"), (0, "-"))
SKALA_TEXT = dict(SKALA)


def _skala(gruppe, eintraege, pflicht=True):
    return [dict(gruppe=gruppe, bezeichnung=bezeichnung, altfeld=altfeld, typ="skala",
                 pflicht=pflicht, in_wertung=True, kurz=kurz)
            for altfeld, bezeichnung, kurz in eintraege]


DIAGNOSTIK = (
    _skala("Sprache", [
        ("wortschatz", "Wortschatz", ""),
        ("grammatik", "Grammatik", ""),
        ("aussprache", "Aussprache", ""),
        ("gespraechsverhalten", "Gesprächsverhalten", ""),
        ("saetze_nachsprechen", "Sätze nachsprechen", ""),
        ("reimen", "Reimen", ""),
        ("pluralbildung", "Pluralbildung", ""),
        ("woerter_segmentieren", "Wörter segmentieren", ""),
    ])
    + _skala("Mathematik", [
        ("mengenerfassung", "Mengenerfassung", ""),
        ("menge_herstellen", "Menge herstellen", ""),
        ("zahlen_erkennen", "Zahlen erkennen", ""),
        ("rueckwaerts_zaehlen", "Rückwärts zählen", ""),
        ("zahlreihe_erzeugen", "Zahlreihe erzeugen", ""),
        ("logische_reihe", "Logische Reihe", ""),
    ])
    + _skala("Zeichnen", [
        ("bild_malen", "Bild malen", ""),
        ("komplexe_figur", "Komplexe Figur", ""),
    ])
)

SCHULSPIEL = (
    _skala("Allgemeines / Verhalten", [
        ("aufgabenverstaendnis", "Aufgabenverständnis", ""),
        ("konzentration", "Konzentration", ""),
        ("anstrengungsbereitschaft", "Anstrengungsbereitschaft", ""),
        ("merkfaehigkeit", "Merkfähigkeit", ""),
        ("ausdauer", "Ausdauer", ""),
        ("selbstbewusstsein", "Selbstbewusstsein", ""),
        ("kontaktfaehigkeit", "Kontaktfähigkeit", ""),
        ("regelverhalten", "Regelverhalten", ""),
    ])
    + _skala("Sprache & Anweisungen", [
        ("versteht_anweisungen", "Versteht Anweisungen", ""),
        ("ausdruck_altersangemessen", "Ausdruck altersangemessen", "Ausdruck altersangem."),
        ("vollstaendige_saetze", "Vollständige Sätze", ""),
        ("richtige_verbformen", "Richtige Verbformen", ""),
        ("richtige_artikel", "Richtige Artikel", ""),
    ])
    + _skala("Schrift & Motorik", [
        ("konzept_von_schrift", "Konzept von Schrift", ""),
        ("schreibt_eigenen_namen", "Schreibt eigenen Namen", ""),
        ("koerperkoordination", "Körperkoordination", ""),
        ("fingerkoordination", "Fingerkoordination", ""),
    ])
    + _skala("Kognition & Wahrnehmung", [
        ("farben_und_formen", "Farben und Formen", ""),
        ("figur_grund_wahrnehmung", "Figur-Grund-Wahrnehmung", "Figur-Grund-Wahrn."),
        ("mengeninvarianz", "Mengeninvarianz", ""),
        ("kognition", "Kognition", ""),
        ("raum_lage_beziehung", "Raum-Lage-Beziehung", ""),
        ("auditive_wahrnehmung", "Auditive Wahrnehmung", ""),
        ("silben_segmentieren", "Silben segmentieren", ""),
        ("reime_erkennen", "Reime erkennen", ""),
    ])
)


def _foerder(altfeld, bezeichnung, kurz):
    return dict(gruppe="Förderempfehlungen", bezeichnung=bezeichnung, kurz=kurz, altfeld=altfeld,
                typ="janein", in_wertung=False, foerderhinweis=True)


SCHULARZT = [
    dict(gruppe="Befund", bezeichnung="Untersuchungsdatum", altfeld="datum", typ="datum",
         kurz="Datum", in_wertung=False),
    dict(gruppe="Befund", bezeichnung="Hörfähigkeit/Audiometrie", kurz="Hörfähigkeit",
         altfeld="hoerfaehigkeit", typ="auswahl", optionen="unauffällig\nauffällig\nbeobachten",
         in_wertung=False),
    dict(gruppe="Befund", bezeichnung="Sehfähigkeit", altfeld="sehfaehigkeit", typ="auswahl",
         optionen="unauffällig\nauffällig\nKontrolle empfohlen", pflicht=True, in_wertung=False),
    dict(gruppe="Befund", bezeichnung="Händigkeit", altfeld="haendigkeit", typ="auswahl",
         optionen="rechts\nlinks\nbeidhändig", pflicht=True, in_wertung=False),
    dict(gruppe="Befund", bezeichnung="Erstsprache", altfeld="erstsprache", typ="text",
         pflicht=True, in_wertung=False),
    dict(gruppe="Befund", bezeichnung="Ergebnis der Untersuchung", kurz="Ergebnis",
         altfeld="ergebnis", typ="auswahl", pflicht=True, in_wertung=False,
         optionen="keine Bedenken\nerhebliche Bedenken\nPrüfung Sonderpäd. Förderbedarf\n"
                  "vorzeitige Aufnahme nicht empfohlen"),
    _foerder("foerder_grobmotorik", "Grobmotorik", ""),
    _foerder("foerder_fein_visuomotorik", "Fein- und Visuomotorik", "Feinmotorik"),
    _foerder("foerder_visuelle_wahrnehmung", "Visuelle Wahrnehmung", "Vis. Wahrn."),
    _foerder("foerder_auditive_wahrnehmung", "Auditive Wahrnehmung", "Audit. Wahrn."),
    _foerder("foerder_deutschkenntnisse", "Deutschkenntnisse", "Deutsch"),
    _foerder("foerder_zahlen_mengen", "Zahlen- und Mengenverständnis", "Zahlen/Mengen"),
    _foerder("foerder_konzentration", "Konzentration", ""),
    _foerder("foerder_psychosozial", "Psychosoziale Entwicklung", "Psychosozial"),
    dict(gruppe="Förderempfehlungen", bezeichnung="Sprache", altfeld="foerder_sprache",
         typ="mehrfach", optionen="Artikulation\nGrammatik\nVerständnis\nWortschatz",
         in_wertung=False, foerderhinweis=True),
]

STANDARD = {"diagnostik": DIAGNOSTIK, "schulspiel": SCHULSPIEL, "schularzt": SCHULARZT}
