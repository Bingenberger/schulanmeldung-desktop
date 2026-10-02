"""Gesetzte Vorlagen mit Werten füllen -- einzeln oder als Stapel.

Das Anmeldeformular der Schule (``Schulanmeldung.pdf``) ist ein fertig
gesetztes Dokument ohne Formularfelder. Gefüllt wird es, indem die Werte auf leere Seiten gleicher
Größe gezeichnet und darübergelegt werden; herausgegeben wird also stets die
Vorlage, nur eben beschriftet.
"""

from io import BytesIO

from pypdf import PdfReader, PdfWriter


def page_size(reader):
    """Breite und Höhe der ersten Seite, als Maß für die Werteebene."""
    erste = reader.pages[0]
    return float(erste.mediabox.width), float(erste.mediabox.height)


def stack(template, overlays, title="", doppelseitig=False):
    """Je Werteebene eine Ausfertigung der Vorlage; liefert ein PDF als Bytes.

    ``overlays`` ist eine Folge von PDF-Bytes mit je so vielen Seiten wie die
    Vorlage. Eine leere Folge ergibt ein leeres Dokument -- der Aufrufer
    entscheidet, ob das ein Fehler ist.

    Die Vorlage wird je Ausfertigung erneut angehängt. Das ist Absicht: Würden
    sich die Seiten eine Quelle teilen, schriebe jede Beschriftung in denselben
    Inhaltsstrom, und am Ende stünden alle Kinder übereinander auf einer Seite.

    ``doppelseitig`` rundet jede Ausfertigung mit einer leeren Seite auf eine
    gerade Seitenzahl auf. Nur dann beginnt beim beidseitigen Druck jedes Kind
    auf einem frischen Blatt, statt auf der Rückseite des vorigen zu landen.
    Bei gerader Seitenzahl ändert sich nichts.
    """
    vorlage = PdfReader(str(template))
    je_satz = len(vorlage.pages)
    leerseite = doppelseitig and je_satz % 2 == 1
    breite, hoehe = page_size(vorlage)
    writer = PdfWriter()
    versatz = 0
    for ebene in overlays:
        writer.append(str(template))
        seiten = PdfReader(BytesIO(ebene)).pages
        for nummer in range(min(je_satz, len(seiten))):
            writer.pages[versatz + nummer].merge_page(seiten[nummer])
        versatz += je_satz
        if leerseite:
            writer.add_blank_page(breite, hoehe)
            versatz += 1
    writer.add_metadata({"/Title": title, "/Producer": "SL-Office"})
    # Briefkopf, Logo und Schriften stehen sonst je Kind erneut im Dokument --
    # aus einem Jahrgang würde so schnell eine Datei von vielen Megabyte.
    try:
        writer.compress_identical_objects()
    except AttributeError:      # ältere pypdf-Fassung: dann eben größer
        pass
    ergebnis = BytesIO()
    writer.write(ergebnis)
    return ergebnis.getvalue()
