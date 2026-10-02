"""Tabellen der frei anlegbaren Kriterien."""

from models import db


class Kriterium(db.Model):
    """Ein Beobachtungspunkt eines Bogens, z. B. „Wortschatz“ in der Diagnostik.

    ``altfeld`` nennt die Spalte, in der die Serverfassung den Wert früher fest
    gespeichert hat; nur der vorbelegte Katalog hat sie, damit vorhandene
    Werte einmalig übernommen werden können.
    """
    __tablename__ = "kriterium"
    id = db.Column(db.Integer, primary_key=True)
    bogen = db.Column(db.String(20), nullable=False, index=True)
    gruppe = db.Column(db.String(120), nullable=False, default="")
    bezeichnung = db.Column(db.String(200), nullable=False)
    #: Kurzform für Abzeichen und Druckstücke; leer heißt: Bezeichnung.
    kurz = db.Column(db.String(60), nullable=False, default="")
    typ = db.Column(db.String(20), nullable=False, default="skala")
    #: Auswahlmöglichkeiten, eine je Zeile (nur Auswahl und Mehrfachauswahl).
    optionen = db.Column(db.Text, nullable=False, default="")
    pflicht = db.Column(db.Boolean, nullable=False, default=False)
    #: Skalenwert geht in Summe und Durchschnitt des Bogens ein.
    in_wertung = db.Column(db.Boolean, nullable=False, default=True)
    #: Angekreuzt (Ja/Nein) oder gewählt (Mehrfachauswahl) ist es ein Förderhinweis.
    foerderhinweis = db.Column(db.Boolean, nullable=False, default=False)
    reihenfolge = db.Column(db.Integer, nullable=False, default=0)
    aktiv = db.Column(db.Boolean, nullable=False, default=True)
    altfeld = db.Column(db.String(60), nullable=True)

    @property
    def kurzname(self):
        return self.kurz or self.bezeichnung

    @property
    def optionsliste(self):
        return [line.strip() for line in (self.optionen or "").splitlines() if line.strip()]


class KriteriumWert(db.Model):
    """Der erfasste Wert eines Kriteriums für ein Kind, immer als Text."""
    __tablename__ = "kriterium_wert"
    __table_args__ = (db.UniqueConstraint("kriterium_id", "schueler_id",
                                          name="uq_kriterium_wert_kind"),)
    id = db.Column(db.Integer, primary_key=True)
    kriterium_id = db.Column(db.Integer, db.ForeignKey("kriterium.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey("schueler.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    wert = db.Column(db.Text, nullable=False, default="")
