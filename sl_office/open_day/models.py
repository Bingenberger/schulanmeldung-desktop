"""Datenmodell für den Tag der offenen Tür.

Der Tag hat mit der Schulanmeldung fachlich wenig zu tun, für die Eltern aber
sehr wohl: Die Einladung liegt dem Anmeldebrief bei, und angemeldet wird sich
über denselben Elternzugang. Deshalb hängt eine Anmeldung am Kind, nicht an
einer frei eingetippten Adresse -- Name und E-Mail sind aus dem Zugang bereits
bekannt und werden nur noch bestätigt.

Der Ablauf besteht aus drei Stationen, die zwei Gruppen in unterschiedlicher
Reihenfolge durchlaufen. Welche Station wann und für welche Gruppe stattfindet,
steht als Zeile in :class:`OpenDayStation` -- so lassen sich die Zeiten pflegen,
ohne die Reihenfolge im Code zu verdrahten.

Eine Station kann sich in mehrere :class:`OpenDayPlatz` aufteilen: In die
Unterrichtshospitation geht niemand "in den Unterricht", sondern in die 2b bei
Frau Meier in Raum 12; die OGS-Hospitation verteilt sich ebenso auf ihre
Gruppen. Die Eltern wählen davon nichts aus -- sie kreuzen nur die Station an,
die Verteilung auf die Plätze ist Sache der Schule. Stationen ohne Plätze
bleiben, wie sie sind; die Schulführung braucht keine.
"""

import datetime

from models import db
from sl_office.parent_portal.models import utcnow

#: Die drei Stationen in ihrer natürlichen Reihenfolge, mit Bezeichnung für
#: Formular, Auswertung und Rückmeldemail.
STATIONEN = (
    ("fuehrung", "Schulführung"),
    ("unterricht", "Unterrichtsstunde"),
    ("ogs", "Hospitation in einer OGS-Gruppe"),
)
STATION_ARTEN = tuple(art for art, _ in STATIONEN)
STATION_LABELS = dict(STATIONEN)
GRUPPEN = (1, 2)

#: Reihenfolge je Gruppe. Gruppe 1 beginnt mit der Führung, Gruppe 2 mit dem
#: Unterricht; die OGS-Hospitation schließt beide Wege ab.
GRUPPEN_ABLAUF = {
    1: ("fuehrung", "unterricht", "ogs"),
    2: ("unterricht", "fuehrung", "ogs"),
}

#: Vorbelegung beim Anlegen einer Veranstaltung -- (Beginn, Ende) je Station.
#: Die beiden Gruppen tauschen die ersten beiden Blöcke und treffen sich bei
#: der OGS wieder.
STANDARDZEITEN = {
    (1, "fuehrung"): (datetime.time(10, 0), datetime.time(10, 40)),
    (1, "unterricht"): (datetime.time(10, 50), datetime.time(11, 35)),
    (2, "unterricht"): (datetime.time(10, 0), datetime.time(10, 45)),
    (2, "fuehrung"): (datetime.time(10, 55), datetime.time(11, 35)),
    (1, "ogs"): (datetime.time(11, 45), datetime.time(12, 15)),
    (2, "ogs"): (datetime.time(11, 45), datetime.time(12, 15)),
}


class OpenDayEvent(db.Model):
    """Ein Tag der offenen Tür eines Einschulungsjahrgangs."""

    __tablename__ = "open_day_event"
    __table_args__ = (
        db.CheckConstraint("status IN ('draft','published','closed')",
                           name="ck_open_day_event_status"),
        # Je Jahrgang darf nur eine Veranstaltung offen stehen: das Elternportal
        # zeigt genau eine an, und eine zweite wäre nicht zu unterscheiden.
        db.Index("uq_open_day_event_published_year", "school_year", unique=True,
                 sqlite_where=db.text("status = 'published'"),
                 postgresql_where=db.text("status = 'published'")),
    )
    id = db.Column(db.Integer, primary_key=True)
    school_year = db.Column(db.Integer, nullable=False, index=True)
    titel = db.Column(db.String(200), nullable=False, default="Tag der offenen Tür")
    datum = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="draft")
    #: Bis wann Eltern sich anmelden oder ihre Angaben ändern dürfen.
    anmeldeschluss = db.Column(db.Date)
    ort = db.Column(db.String(200))
    #: Freier Text, der in der Rückmeldemail unter dem Ablaufplan steht.
    hinweise = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow,
                           onupdate=utcnow)

    stationen = db.relationship("OpenDayStation", backref="event", cascade="all, delete-orphan",
                                order_by="OpenDayStation.beginn")
    anmeldungen = db.relationship("OpenDayRegistration", backref="event",
                                  cascade="all, delete-orphan")

    @property
    def anmeldung_offen(self):
        if self.status != "published":
            return False
        return self.anmeldeschluss is None or datetime.date.today() <= self.anmeldeschluss


class OpenDayStation(db.Model):
    """Eine Station für eine der beiden Gruppen, mit ihrer Uhrzeit."""

    __tablename__ = "open_day_station"
    __table_args__ = (
        db.UniqueConstraint("event_id", "gruppe", "art", name="uq_open_day_station"),
        db.CheckConstraint("gruppe IN (1,2)", name="ck_open_day_station_gruppe"),
        db.CheckConstraint("art IN ('fuehrung','unterricht','ogs')",
                           name="ck_open_day_station_art"),
        db.CheckConstraint("ende > beginn", name="ck_open_day_station_period"),
    )
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("open_day_event.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    gruppe = db.Column(db.Integer, nullable=False)
    art = db.Column(db.String(20), nullable=False)
    beginn = db.Column(db.Time, nullable=False)
    ende = db.Column(db.Time, nullable=False)
    ort = db.Column(db.String(200))

    plaetze = db.relationship("OpenDayPlatz", backref="station", cascade="all, delete-orphan",
                              order_by="OpenDayPlatz.bezeichnung")

    @property
    def label(self):
        return STATION_LABELS.get(self.art, self.art)

    @property
    def teilt_sich_auf(self):
        return bool(self.plaetze)


class OpenDayPlatz(db.Model):
    """Ein konkreter Hospitationsplatz innerhalb einer Station.

    Für die Unterrichtshospitation eine Klasse mit Raum, für die OGS eine
    Gruppe. ``kapazitaet`` bleibt leer, wenn die Zahl nicht begrenzt ist.
    """

    __tablename__ = "open_day_platz"
    __table_args__ = (
        db.UniqueConstraint("station_id", "bezeichnung", name="uq_open_day_platz"),
        db.CheckConstraint("kapazitaet IS NULL OR kapazitaet > 0",
                           name="ck_open_day_platz_kapazitaet"),
    )
    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("open_day_station.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    bezeichnung = db.Column(db.String(120), nullable=False)
    ort = db.Column(db.String(200))
    kapazitaet = db.Column(db.Integer)

    @property
    def beschriftung(self):
        """Bezeichnung samt Ort, wie sie Eltern zu lesen bekommen."""
        return f"{self.bezeichnung} ({self.ort})" if self.ort else self.bezeichnung


class OpenDayZuteilung(db.Model):
    """Welche Familie an welcher Station auf welchem Platz hospitiert.

    Je Familie und Station höchstens eine Zeile -- das sichert die Datenbank
    zu, damit niemand versehentlich in zwei Klassen gleichzeitig steht.
    """

    __tablename__ = "open_day_zuteilung"
    __table_args__ = (
        db.UniqueConstraint("registration_id", "station_id", name="uq_open_day_zuteilung"),
    )
    id = db.Column(db.Integer, primary_key=True)
    registration_id = db.Column(db.Integer,
                                db.ForeignKey("open_day_registration.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    station_id = db.Column(db.Integer, db.ForeignKey("open_day_station.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    platz_id = db.Column(db.Integer, db.ForeignKey("open_day_platz.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    platz = db.relationship("OpenDayPlatz")


class OpenDayRegistration(db.Model):
    """Die Rückmeldung einer Familie -- eine je Kind, wie beim Anmeldeformular.

    Beide Sorgeberechtigten bearbeiten dieselbe Zeile. ``teilnahme=False`` ist
    eine gültige Antwort und für die Planung genauso wertvoll wie eine Zusage.
    """

    __tablename__ = "open_day_registration"
    __table_args__ = (
        db.UniqueConstraint("event_id", "schueler_id", name="uq_open_day_registration_child"),
        db.CheckConstraint("gruppe IS NULL OR gruppe IN (1,2)",
                           name="ck_open_day_registration_gruppe"),
    )
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("open_day_event.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey("schueler.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    #: Wer zuletzt gespeichert hat. Nullt sich, wenn der Zugang entfällt --
    #: die Anmeldung selbst bleibt davon unberührt.
    parent_access_id = db.Column(db.Integer, db.ForeignKey("parent_access.id",
                                                           ondelete="SET NULL"))
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(320), nullable=False)
    teilnahme = db.Column(db.Boolean, nullable=False, default=True)
    wunsch_fuehrung = db.Column(db.Boolean, nullable=False, default=False)
    wunsch_unterricht = db.Column(db.Boolean, nullable=False, default=False)
    wunsch_ogs = db.Column(db.Boolean, nullable=False, default=False)
    gruppe = db.Column(db.Integer)
    bemerkung = db.Column(db.Text)
    plan_gesendet_am = db.Column(db.DateTime(timezone=True))
    #: Stand der Angaben zum Zeitpunkt des Versands. Weicht er vom heutigen ab,
    #: ist der verschickte Ablaufplan überholt -- das zeigt die Auswertung an.
    plan_signatur = db.Column(db.String(40))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow,
                           onupdate=utcnow)

    zuteilungen = db.relationship("OpenDayZuteilung", backref="registration",
                                  cascade="all, delete-orphan")

    @property
    def wuensche(self):
        """Die gewünschten Stationen in natürlicher Reihenfolge."""
        gewaehlt = {"fuehrung": self.wunsch_fuehrung, "unterricht": self.wunsch_unterricht,
                    "ogs": self.wunsch_ogs}
        return tuple(art for art in STATION_ARTEN if gewaehlt[art])

    @property
    def signatur(self):
        """Kurzform dessen, was im Ablaufplan steht.

        Die Plätze gehören dazu: Wird eine Familie in eine andere Klasse
        verschoben, ist der verschickte Plan überholt, auch wenn Gruppe und
        Wünsche gleich geblieben sind.
        """
        plaetze = ",".join(str(zeile.platz_id)
                           for zeile in sorted(self.zuteilungen, key=lambda z: z.station_id))
        return f"{self.gruppe or 0}:{','.join(self.wuensche)}:{plaetze}"

    @property
    def plan_veraltet(self):
        return self.plan_gesendet_am is not None and self.plan_signatur != self.signatur
