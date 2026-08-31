from flask_sqlalchemy import SQLAlchemy
import datetime

from flask_login import UserMixin, LoginManager

db = SQLAlchemy()
login_manager = LoginManager()

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'Administrator', 'Schulleitung', 'Foerderlehrkraft', 'Sekretariat'

    # Zweiter Faktor (TOTP). Ohne bestaetigten Zeitpunkt gilt 2FA als nicht eingerichtet.
    totp_secret = db.Column(db.String(64))
    totp_confirmed_at = db.Column(db.DateTime(timezone=True))
    # Hoechster bereits verbrauchter Zeitschritt: verhindert, dass ein
    # abgefangener Code innerhalb seines Fensters erneut verwendet wird.
    totp_last_counter = db.Column(db.BigInteger)

    # Schutz gegen Durchprobieren von Passwoertern und Codes.
    failed_logins = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime(timezone=True))
    last_login_at = db.Column(db.DateTime(timezone=True))

    recovery_codes = db.relationship(
        "RecoveryCode", backref="user", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def two_factor_active(self):
        return self.totp_confirmed_at is not None


class RecoveryCode(db.Model):
    """One-time fallback code, stored only as a digest."""
    __tablename__ = "recovery_code"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash = db.Column(db.String(128), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.UTC))

class Schueler(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nachname = db.Column(db.String(100), nullable=False)
    vorname = db.Column(db.String(100), nullable=False)
    geburtsdatum = db.Column(db.Date)
    geschlecht = db.Column(db.String(10)) # 'm', 'w', 'd'
    kita = db.Column(db.String(200))
    kann_kind = db.Column(db.Boolean, default=False)

    # Jahrgang, zu dem dieses Kind gehoert. Jeder Jahrgang wird getrennt
    # bearbeitet; der Filter dafuer sitzt zentral in sl_office/school_year.py.
    einschulungsjahr = db.Column(db.Integer, nullable=False, index=True)

    # Stammdaten aus der Meldung der Stadt (Schulpflichtigenliste)
    strasse = db.Column(db.String(200))
    plz = db.Column(db.String(10))
    ort = db.Column(db.String(120))
    erzb_1_name = db.Column(db.String(200))
    erzb_2_name = db.Column(db.String(200))

    # Status der pädagogischen Diagnostik
    diag_status = db.Column(db.String(20), default='Offen') # Offen, Terminiert, Abgeschlossen
    diag_ergebnis = db.Column(db.Text)

    # Status der schulärztlichen Untersuchung
    arzt_status = db.Column(db.String(20), default='Warten') # Warten, Rückmeldung erhalten
    arzt_bemerkung = db.Column(db.Text)

    # Erweiterbarkeit: Spätere Prozesse
    ogs_anmeldung = db.Column(db.Boolean, default=False)
    
    schulspiel_status = db.Column(db.String(20), default='Offen')
    
    # Beziehung zu Diagnostik (One-to-One)
    diagnostik = db.relationship('Diagnostik', backref='schueler', uselist=False, cascade="all, delete-orphan")

    # Beziehung zu SchulspielDiagnostik (One-to-One)
    schulspiel_diagnostik = db.relationship('SchulspielDiagnostik', backref='schueler', uselist=False, cascade="all, delete-orphan")

    # Beziehung zu Schularzt (One-to-One)
    schularzt_untersuchung = db.relationship('SchulaerztlicheUntersuchung', backref='schueler', uselist=False, cascade="all, delete-orphan")

    # Freunde
    freund1_id = db.Column(db.Integer, db.ForeignKey('schueler.id'))
    freund1_negativ = db.Column(db.Boolean, default=False)
    
    freund2_id = db.Column(db.Integer, db.ForeignKey('schueler.id'))
    freund2_negativ = db.Column(db.Boolean, default=False)
    
    freund1 = db.relationship('Schueler', remote_side=[id], foreign_keys=[freund1_id], backref='gewuenscht_von_1')
    freund2 = db.relationship('Schueler', remote_side=[id], foreign_keys=[freund2_id], backref='gewuenscht_von_2')
    
    freunde_bemerkung = db.Column(db.Text)
    keine_freunde = db.Column(db.Boolean, default=False)

    # Klassenzusammensetzung
    klasse = db.Column(db.String(10), nullable=True)

    # Betreuung nach Unterricht
    betreuung = db.Column(db.String(20), nullable=True)  # 'OGS', 'ÜMI', 'Abholung'

    # Förderkurse
    foerderkurs_lrs     = db.Column(db.Boolean, default=False)
    foerderkurs_mathe   = db.Column(db.Boolean, default=False)
    foerderkurs_sport   = db.Column(db.Boolean, default=False)
    foerderkurs_deutsch = db.Column(db.Boolean, default=False)



    # Beziehung zu KitaBericht (One-to-One)
    kita_bericht = db.relationship('KitaBericht', backref='schueler', uselist=False, cascade="all, delete-orphan")

    @property
    def has_aosf_verdacht(self):
        if self.diagnostik and self.diagnostik.aosf_verdacht:
            return True
        if self.schularzt_untersuchung and self.schularzt_untersuchung.aosf_verdacht:
            return True
        if self.aosf_prozess:
            return True
        return False

    @property
    def has_rueckstellung_empfohlen(self):
        # Einfache Logik: Diagnostik Checkbox oder bereits existierender Prozess
        if self.diagnostik and self.diagnostik.rueckstellung_empfohlen:
            return True
        if self.rueckstellung_prozess:
            return True
        return False

class KitaBericht(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False)
    
    # 0-3 Scale (similar to Diagnostik)
    kognitiv = db.Column(db.Integer) 
    verhalten = db.Column(db.Integer)
    
    bemerkung = db.Column(db.Text)

class Diagnostik(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False)
    
    # Items (3=++, 2=+, 1=o, 0=-)
    wortschatz = db.Column(db.Integer)
    grammatik = db.Column(db.Integer)
    aussprache = db.Column(db.Integer)
    gespraechsverhalten = db.Column(db.Integer)
    saetze_nachsprechen = db.Column(db.Integer)
    reimen = db.Column(db.Integer)
    pluralbildung = db.Column(db.Integer)
    woerter_segmentieren = db.Column(db.Integer)
    mengenerfassung = db.Column(db.Integer)
    menge_herstellen = db.Column(db.Integer)
    zahlen_erkennen = db.Column(db.Integer)
    rueckwaerts_zaehlen = db.Column(db.Integer)
    zahlreihe_erzeugen = db.Column(db.Integer)
    logische_reihe = db.Column(db.Integer)
    bild_malen = db.Column(db.Integer)
    komplexe_figur = db.Column(db.Integer)
    
    # Gesamteindrücke (Auch Skala 0-3)
    gesamteindruck_kognitiv = db.Column(db.Integer) 
    gesamteindruck_verhalten = db.Column(db.Integer)
    
    # AO-SF
    aosf_verdacht = db.Column(db.Boolean, default=False)
    
    # Rückstellung
    rueckstellung_empfohlen = db.Column(db.Boolean, default=False)
    
    # PDF Upload
    pdf_dateiname = db.Column(db.String(255))
    
    bemerkung = db.Column(db.Text)
    schulspiel = db.Column(db.Boolean, default=False)

class SchulspielDiagnostik(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False)
    
    # Items (3=++, 2=+, 1=o, 0=-)
    aufgabenverstaendnis = db.Column(db.Integer)
    konzentration = db.Column(db.Integer)
    anstrengungsbereitschaft = db.Column(db.Integer)
    merkfaehigkeit = db.Column(db.Integer)
    ausdauer = db.Column(db.Integer)
    selbstbewusstsein = db.Column(db.Integer)
    kontaktfaehigkeit = db.Column(db.Integer)
    regelverhalten = db.Column(db.Integer)
    versteht_anweisungen = db.Column(db.Integer)
    ausdruck_altersangemessen = db.Column(db.Integer)
    vollstaendige_saetze = db.Column(db.Integer)
    richtige_verbformen = db.Column(db.Integer)
    richtige_artikel = db.Column(db.Integer)
    konzept_von_schrift = db.Column(db.Integer)
    schreibt_eigenen_namen = db.Column(db.Integer)
    koerperkoordination = db.Column(db.Integer)
    fingerkoordination = db.Column(db.Integer)
    farben_und_formen = db.Column(db.Integer)
    figur_grund_wahrnehmung = db.Column(db.Integer)
    mengeninvarianz = db.Column(db.Integer)
    kognition = db.Column(db.Integer)
    raum_lage_beziehung = db.Column(db.Integer)
    auditive_wahrnehmung = db.Column(db.Integer)
    silben_segmentieren = db.Column(db.Integer)
    reime_erkennen = db.Column(db.Integer)
    
    gesamtwert = db.Column(db.Integer, default=0)
    pdf_dateiname = db.Column(db.String(255))
    bemerkung = db.Column(db.Text)
    
    @property
    def gesamttendenz(self):
        if self.gesamtwert is None:
            return None
        # Max score is 25 * 3 = 75
        avg = self.gesamtwert / 25
        if avg >= 2.5:
            return 3  # ++
        elif avg >= 1.5:
            return 2  # +
        elif avg >= 0.5:
            return 1  # o
        else:
            return 0  # -

class Rueckstellung(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False)
    
    # Status
    status = db.Column(db.String(50), default='Empfohlen') # Empfohlen, Prozess läuft, Beschlossen, Abgelehnt
    
    # Zuständigkeit
    leitung_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # 1. Unterlagen
    bericht_medizin_dateiname = db.Column(db.String(255))
    bericht_therapie_dateiname = db.Column(db.String(255))
    
    # 2. Förderkonferenz / Entscheidung
    konferenz_datum = db.Column(db.Date)
    konferenz_ergebnis = db.Column(db.String(50), default='Offen') # Offen, Stattgegeben, Abgelehnt
    
    # 3. Elternschreiben
    elternschreiben_datum = db.Column(db.Date)
    elternschreiben_dateiname = db.Column(db.String(255))
    
    bemerkung = db.Column(db.Text)
    
    # Beziehungen
    leitung_user = db.relationship('User', backref='rueckstellung_faelle')
    schueler = db.relationship('Schueler', backref=db.backref('rueckstellung_prozess', uselist=False, cascade="all, delete-orphan"))

class SchulaerztlicheUntersuchung(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False)
    
    datum = db.Column(db.Date)
    hoerfaehigkeit = db.Column(db.String(50)) # unauffällig, auffällig, Kontrolle empfohlen
    sehfaehigkeit = db.Column(db.String(50)) # unauffällig, auffällig, Kontrolle empfohlen
    haendigkeit = db.Column(db.String(20)) # links, rechts, beidhändig
    erstsprache = db.Column(db.String(100))
    ergebnis = db.Column(db.String(100)) # keine Bedenken, etc.
    
    # Förderempfehlungen (Booleans)
    foerder_grobmotorik = db.Column(db.Boolean, default=False)
    foerder_fein_visuomotorik = db.Column(db.Boolean, default=False)
    foerder_visuelle_wahrnehmung = db.Column(db.Boolean, default=False)
    foerder_auditive_wahrnehmung = db.Column(db.Boolean, default=False)
    foerder_deutschkenntnisse = db.Column(db.Boolean, default=False)
    foerder_zahlen_mengen = db.Column(db.Boolean, default=False)
    foerder_konzentration = db.Column(db.Boolean, default=False)
    foerder_psychosozial = db.Column(db.Boolean, default=False)
    
    # Sprache (Mehrfachauswahl als String, kommasepariert)
    foerder_sprache = db.Column(db.String(200)) 
    
    # AO-SF
    aosf_verdacht = db.Column(db.Boolean, default=False)
    
    # PDF Upload
    pdf_dateiname = db.Column(db.String(255))
    
    bemerkung = db.Column(db.Text)
    
    # Gesamteinschätzung (3=++, 2=+, 1=o, 0=-)
    gesamteinschaetzung = db.Column(db.Integer)


class AOSF(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False)
    
    # Zuständigkeit
    leitung_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # Status des Verfahrens
    status = db.Column(db.String(50), default='Verdacht') # Verdacht, Fall, Abgeschlossen
    
    # 1. Unterlagen einholen
    bericht_medizin_dateiname = db.Column(db.String(255))
    bericht_therapie_dateiname = db.Column(db.String(255))
    
    # 2. Förderkonferenz
    konferenz_datum = db.Column(db.Date)
    konferenz_ergebnis = db.Column(db.String(50), default='Offen') # Offen, Verfahren einleiten, Kein Verfahren
    
    # 3. Antragstellung
    antrag_datum = db.Column(db.Date)
    antrag_dateiname = db.Column(db.String(255))
    
    # 4. Entscheidung Schulamt
    schulamt_datum = db.Column(db.Date)
    schulamt_entscheidung = db.Column(db.String(50), default='Offen') # Offen, Stattgegeben, Abgelehnt
    
    bemerkung = db.Column(db.Text)
    
    # Beziehungen
    leitung_user = db.relationship('User', backref='aosf_faelle')
    schueler = db.relationship('Schueler', backref=db.backref('aosf_prozess', uselist=False, cascade="all, delete-orphan"))

class Einschulungsjahr(db.Model):
    """Registry of school years; exactly one of them is the current one."""
    __tablename__ = "einschulungsjahr"
    id = db.Column(db.Integer, primary_key=True)
    jahr = db.Column(db.Integer, unique=True, nullable=False, index=True)
    ist_aktuell = db.Column(db.Boolean, nullable=False, default=False)
    # Abgeschlossene Jahrgaenge sind schreibgeschuetzt, bis jemand sie bewusst
    # wieder entsperrt.
    gesperrt = db.Column(db.Boolean, nullable=False, default=False)
    angelegt_am = db.Column(db.DateTime(timezone=True), nullable=False,
                            default=lambda: datetime.datetime.now(datetime.UTC))

    def __repr__(self):
        return f"<Einschulungsjahr {self.jahr}>"


class GlobalSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    einschulungsjahr = db.Column(db.Integer,  default=2026) # Default für Laufzeit
    frist_aosf = db.Column(db.Date, nullable=True) # Optional
    anzahl_klassen = db.Column(db.Integer, default=3)
