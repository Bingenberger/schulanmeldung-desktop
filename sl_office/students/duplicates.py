"""Doppelt angelegte Kinder zusammenführen.

Der Import ordnet über Name *und* Geburtsdatum zu. Solange die Tag/Monat-
Vertauschung bestand (siehe :mod:`sl_office.students.dates`), hat ein zweiter
Import derselben Liste die betroffenen Kinder deshalb nicht wiedererkannt und
ein zweites Mal angelegt -- einmal mit richtigem, einmal mit vertauschtem
Geburtsdatum.

Aufgelöst wird das gegen die Originalliste: sie sagt, welches Datum stimmt.
Bestehen bleibt aber nicht zwangsläufig der richtig datierte Datensatz, sondern
der, an dem bereits Arbeit hängt -- Diagnostik, Schulspiel, Elternzugang,
Termine. Sein Geburtsdatum wird berichtigt, der leere Zweitdatensatz entfällt.
Tragen beide Daten, entscheidet niemand automatisch.
"""

from models import Schueler, db
from sl_office.parent_portal.models import (
    ActivationGrant, AppointmentBooking, ParentAccess, ParentRegistration,
)
from sl_office.services.student_classification import recalculate_kann_kind
from sl_office.services.student_deletion import delete_student

#: Felder des Kindes, die der Import selbst füllt -- sie belegen keine Arbeit.
_AUS_DEM_IMPORT = {
    "id", "nachname", "vorname", "geburtsdatum", "geschlecht", "kita",
    "einschulungsjahr", "kann_kind", "strasse", "plz", "ort",
    "erzb_1_name", "erzb_2_name",
}
#: Vorbelegungen, die nur den unbearbeiteten Ausgangszustand beschreiben.
_LEERSTAND = {"diag_status": "Offen", "arzt_status": "Warten", "schulspiel_status": "Offen"}
#: Beim Kann-Kind wird das Schulspiel automatisch gesetzt, das ist keine Eingabe.
_ABGELEITET = {"schulspiel"}


def _gefuellt(wert):
    if wert is None:
        return False
    if isinstance(wert, bool):
        return wert
    if isinstance(wert, str):
        return bool(wert.strip())
    return True   # Zahlen zählen mit: 0 ist auf der Skala eine echte Bewertung.


def _zeile_hat_inhalt(zeile, ignorieren=()):
    auslassen = {"id", "schueler_id"} | set(ignorieren)
    return any(_gefuellt(getattr(zeile, spalte.name))
               for spalte in zeile.__table__.columns if spalte.name not in auslassen)


def datenspuren(kind):
    """Klartext-Liste dessen, was an diesem Datensatz an Arbeit hängt."""
    spuren = []
    for spalte in Schueler.__table__.columns:
        name = spalte.name
        if name in _AUS_DEM_IMPORT:
            continue
        wert = getattr(kind, name)
        if name in _LEERSTAND and wert == _LEERSTAND[name]:
            continue
        if _gefuellt(wert):
            spuren.append(f"{name}={wert}")

    unterlagen = (
        ("Diagnostik", kind.diagnostik, _ABGELEITET),
        ("Schulspiel-Diagnostik", kind.schulspiel_diagnostik, ()),
        ("Schulärztliche Untersuchung", kind.schularzt_untersuchung, ()),
        ("Kita-Bericht", kind.kita_bericht, ()),
        ("AO-SF-Verfahren", kind.aosf_prozess, ()),
        ("Rückstellung", kind.rueckstellung_prozess, ()),
    )
    for bezeichnung, zeile, ignorieren in unterlagen:
        if zeile is not None and _zeile_hat_inhalt(zeile, ignorieren):
            spuren.append(bezeichnung)

    for bezeichnung, modell in (("Elternzugang", ParentAccess),
                                ("Elternanmeldung", ParentRegistration),
                                ("Freischaltung", ActivationGrant),
                                ("Terminbuchung", AppointmentBooking)):
        if modell.query.filter_by(schueler_id=kind.id).first():
            spuren.append(bezeichnung)

    return spuren


def verweise(kind):
    """Wie oft dieses Kind als Freundschaftswunsch genannt ist.

    Das ist keine Bearbeitung des Datensatzes, sondern ein Zeiger darauf --
    :func:`zusammenfuehren` hängt ihn um, statt daran zu scheitern.
    """
    return Schueler.query.filter(
        (Schueler.freund1_id == kind.id) | (Schueler.freund2_id == kind.id)).count()


class Dublette:
    """Ein Namenspaar aus dem Bestand, gegen die Liste der Stadt beurteilt."""

    def __init__(self, name, datum, kinder):
        self.name = name
        self.datum = datum          # Geburtsdatum laut Liste der Stadt
        self.kinder = kinder
        self.spuren = {kind.id: datenspuren(kind) for kind in kinder}
        self.verweise = {kind.id: verweise(kind) for kind in kinder}
        self.behalten = None
        self.verwerfen = None
        self.konflikt = None        # Klartext, wenn nicht automatisch lösbar
        self._beurteilen()

    def _beurteilen(self):
        if len(self.kinder) != 2:
            self.konflikt = f"{len(self.kinder)} Datensätze -- nur Paare sind auflösbar"
            return
        bearbeitet = [kind for kind in self.kinder if self.spuren[kind.id]]
        if len(bearbeitet) == 2:
            self.konflikt = "an beiden Datensätzen hängt Arbeit -- bitte von Hand zusammenführen"
            return
        if bearbeitet:
            self.behalten = bearbeitet[0]
        else:
            # Keiner ist bearbeitet: der ältere bleibt, er ist länger im Umlauf.
            self.behalten = min(self.kinder, key=lambda kind: kind.id)
        self.verwerfen = next(kind for kind in self.kinder if kind is not self.behalten)

    @property
    def loesbar(self):
        return self.konflikt is None

    @property
    def datum_zu_berichtigen(self):
        return self.behalten is not None and self.behalten.geburtsdatum != self.datum


def dubletten(abgleich):
    """Die mehrdeutigen Namen eines :class:`~...reconcile.Abgleich` beurteilen."""
    return [Dublette(f"{vorname} {nachname}", datum, kinder)
            for vorname, nachname, kinder, datum in abgleich.mehrdeutig]


def zusammenfuehren(befunde, upload_folder):
    """Die lösbaren Dubletten auflösen; liefert die Zahl der entfernten Kinder."""
    entfernt = 0
    for befund in befunde:
        if not befund.loesbar:
            continue
        # Freundschaftswünsche auf den bleibenden Datensatz umhängen, sonst
        # zeigen sie nach dem Löschen ins Leere.
        for feld in ("freund1_id", "freund2_id"):
            for anderes in Schueler.query.filter(
                    getattr(Schueler, feld) == befund.verwerfen.id).all():
                setattr(anderes, feld, befund.behalten.id)
        befund.behalten.geburtsdatum = befund.datum
        db.session.flush()
        delete_student(befund.verwerfen, upload_folder)
        entfernt += 1
    if entfernt:
        recalculate_kann_kind()
        db.session.commit()
    return entfernt
