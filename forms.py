from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, DateField, SelectField, TextAreaField, SubmitField, PasswordField, RadioField, BooleanField, SelectMultipleField, widgets
from wtforms.validators import DataRequired, InputRequired, Length, Optional

class LoginForm(FlaskForm):
    username = StringField('Benutzername', validators=[DataRequired()])
    password = PasswordField('Passwort', validators=[DataRequired()])
    submit = SubmitField('Einloggen')

class TwoFactorForm(FlaskForm):
    code = StringField('Code', validators=[DataRequired()])
    submit = SubmitField('Bestätigen')


class TwoFactorSetupForm(FlaskForm):
    code = StringField('Code aus der App', validators=[DataRequired()])
    submit = SubmitField('Einrichtung abschließen')


class UserAddForm(FlaskForm):
    username = StringField('Benutzername', validators=[DataRequired()])
    password = PasswordField('Passwort', validators=[DataRequired()])
    role = SelectField('Rolle', choices=[
        ('Administrator', 'Administrator'),
        ('Schulleitung', 'Schulleitung'),
        ('Foerderlehrkraft', 'Förderlehrkraft'),
        ('Sekretariat', 'Sekretariat')
    ], validators=[DataRequired()])
    submit = SubmitField('Benutzer anlegen')

class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('Aktuelles Passwort', validators=[DataRequired()])
    new_password = PasswordField('Neues Passwort', validators=[DataRequired()])
    confirm_password = PasswordField('Passwort bestätigen', validators=[DataRequired()])
    submit = SubmitField('Passwort ändern')

    submit = SubmitField('Passwort ändern')

class DiagnostikForm(FlaskForm):
    # Skala: 3=++, 2=+, 1=o, 0=-
    CHOICES = [(3, '++'), (2, '+'), (1, 'o'), (0, '-')]
    
    wortschatz = RadioField('Wortschatz', choices=CHOICES, coerce=int, validators=[InputRequired()])
    grammatik = RadioField('Grammatik', choices=CHOICES, coerce=int, validators=[InputRequired()])
    aussprache = RadioField('Aussprache', choices=CHOICES, coerce=int, validators=[InputRequired()])
    gespraechsverhalten = RadioField('Gesprächsverhalten', choices=CHOICES, coerce=int, validators=[InputRequired()])
    saetze_nachsprechen = RadioField('Sätze nachsprechen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    reimen = RadioField('Reimen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    pluralbildung = RadioField('Pluralbildung', choices=CHOICES, coerce=int, validators=[InputRequired()])
    woerter_segmentieren = RadioField('Wörter segmentieren', choices=CHOICES, coerce=int, validators=[InputRequired()])
    mengenerfassung = RadioField('Mengenerfassung', choices=CHOICES, coerce=int, validators=[InputRequired()])
    menge_herstellen = RadioField('Menge herstellen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    zahlen_erkennen = RadioField('Zahlen erkennen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    rueckwaerts_zaehlen = RadioField('Rückwärts zählen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    zahlreihe_erzeugen = RadioField('Zahlreihe erzeugen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    logische_reihe = RadioField('Logische Reihe', choices=CHOICES, coerce=int, validators=[InputRequired()])
    bild_malen = RadioField('Bild malen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    komplexe_figur = RadioField('Komplexe Figur', choices=CHOICES, coerce=int, validators=[InputRequired()])
    

    gesamteindruck_kognitiv = RadioField('Gesamteindruck Kognitiv (Vorschlag)', choices=CHOICES, coerce=int, validators=[InputRequired()])
    gesamteindruck_verhalten = RadioField('Gesamteindruck Verhalten', choices=CHOICES, coerce=int, validators=[InputRequired()])
    
    aosf_verdacht = BooleanField('Verdacht auf AO-SF')
    rueckstellung_empfohlen = BooleanField('Rückstellung vom Schulbesuch empfehlen')
    
    pdf_datei = FileField('Diagnostik Scan (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF-Dateien!')])
    
    bemerkung = TextAreaField('Bemerkungen', validators=[Optional()])
    schulspiel = RadioField('Einladung zum Schulspiel', choices=[(0, 'Nein'), (1, 'Ja')], coerce=int, default=0) # RadioField to allow iteration in template
    submit = SubmitField('Speichern')

class SchulspielForm(FlaskForm):
    CHOICES = [(3, '++'), (2, '+'), (1, 'o'), (0, '-')]
    
    aufgabenverstaendnis = RadioField('Aufgabenverständnis', choices=CHOICES, coerce=int, validators=[InputRequired()])
    konzentration = RadioField('Konzentration', choices=CHOICES, coerce=int, validators=[InputRequired()])
    anstrengungsbereitschaft = RadioField('Anstrengungsbereitschaft', choices=CHOICES, coerce=int, validators=[InputRequired()])
    merkfaehigkeit = RadioField('Merkfähigkeit', choices=CHOICES, coerce=int, validators=[InputRequired()])
    ausdauer = RadioField('Ausdauer', choices=CHOICES, coerce=int, validators=[InputRequired()])
    selbstbewusstsein = RadioField('Selbstbewusstsein', choices=CHOICES, coerce=int, validators=[InputRequired()])
    kontaktfaehigkeit = RadioField('Kontaktfähigkeit', choices=CHOICES, coerce=int, validators=[InputRequired()])
    regelverhalten = RadioField('Regelverhalten', choices=CHOICES, coerce=int, validators=[InputRequired()])
    versteht_anweisungen = RadioField('Versteht Anweisungen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    ausdruck_altersangemessen = RadioField('Ausdruck altersangemessen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    vollstaendige_saetze = RadioField('Vollständige Sätze', choices=CHOICES, coerce=int, validators=[InputRequired()])
    richtige_verbformen = RadioField('Richtige Verbformen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    richtige_artikel = RadioField('Richtige Artikel', choices=CHOICES, coerce=int, validators=[InputRequired()])
    konzept_von_schrift = RadioField('Konzept von Schrift', choices=CHOICES, coerce=int, validators=[InputRequired()])
    schreibt_eigenen_namen = RadioField('Schreibt eigenen Namen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    koerperkoordination = RadioField('Körperkoordination', choices=CHOICES, coerce=int, validators=[InputRequired()])
    fingerkoordination = RadioField('Fingerkoordination', choices=CHOICES, coerce=int, validators=[InputRequired()])
    farben_und_formen = RadioField('Farben und Formen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    figur_grund_wahrnehmung = RadioField('Figur-Grund-Wahrnehmung', choices=CHOICES, coerce=int, validators=[InputRequired()])
    mengeninvarianz = RadioField('Mengeninvarianz', choices=CHOICES, coerce=int, validators=[InputRequired()])
    kognition = RadioField('Kognition', choices=CHOICES, coerce=int, validators=[InputRequired()])
    raum_lage_beziehung = RadioField('Raum-Lage-Beziehung', choices=CHOICES, coerce=int, validators=[InputRequired()])
    auditive_wahrnehmung = RadioField('Auditive Wahrnehmung', choices=CHOICES, coerce=int, validators=[InputRequired()])
    silben_segmentieren = RadioField('Silben segmentieren', choices=CHOICES, coerce=int, validators=[InputRequired()])
    reime_erkennen = RadioField('Reime erkennen', choices=CHOICES, coerce=int, validators=[InputRequired()])
    
    pdf_datei = FileField('Schulspiel Scan (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF-Dateien!')])
    
    bemerkung = TextAreaField('Bemerkungen', validators=[Optional()])
    submit = SubmitField('Speichern')

class SchuelerForm(FlaskForm):
    vorname = StringField('Vorname', validators=[DataRequired()])
    nachname = StringField('Nachname', validators=[DataRequired()])
    geburtsdatum = DateField('Geburtsdatum (JJJJ-MM-TT)', format='%Y-%m-%d')
    geschlecht = SelectField('Geschlecht', choices=[('m', 'Männlich'), ('w', 'Weiblich'), ('d', 'Divers'), ('u', 'Unbekannt')], default='u')
    kita = StringField('Herkunfts-Kita')

    betreuung = SelectField('Betreuung nach Unterricht', choices=[
        ('', '– noch nicht bekannt –'),
        ('OGS', 'OGS (Offene Ganztagsschule)'),
        ('ÜMI', 'ÜMI (Übermittagsbetreuung)'),
        ('Abholung', 'Abholung / Nach Hause'),
    ], default='')

    # Anschrift und Erziehungsberechtigte kommen aus der Schulpflichtigenliste
    # der Stadt und stehen im Elternbrief. Die Laengen entsprechen dem Modell,
    # damit zu lange Eingaben hier auffallen und nicht erst in der Datenbank.
    strasse = StringField('Straße und Hausnummer', validators=[Optional(), Length(max=200)])
    plz = StringField('PLZ', validators=[Optional(), Length(max=10)])
    ort = StringField('Ort', validators=[Optional(), Length(max=120)])
    erzb_1_name = StringField('Erziehungsberechtigte:r 1', validators=[Optional(), Length(max=200)])
    erzb_2_name = StringField('Erziehungsberechtigte:r 2', validators=[Optional(), Length(max=200)])

    bemerkung = TextAreaField('Interne Bemerkungen')
    submit = SubmitField('Datensatz Speichern')

class SchuelerImportForm(FlaskForm):
    file = FileField('Excel-Datei (.xlsx)', validators=[
        InputRequired(),
        FileAllowed(['xlsx'], 'Nur XLSX-Dateien erlaubt!')
    ])
    submit = SubmitField('Importieren')

class SchularztForm(FlaskForm):
    datum = DateField('Untersuchungsdatum', format='%Y-%m-%d', validators=[Optional()])
    
    hoerfaehigkeit = SelectField('Hörfähigkeit/Audiometrie', choices=[
        ('unauffällig', 'Unauffällig'), ('auffällig', 'Auffällig'), ('beobachten', 'Beobachten')
    ], validators=[Optional()])
    
    sehfaehigkeit = SelectField('Sehfähigkeit', choices=[
        ('unauffällig', 'unauffällig'),
        ('auffällig', 'auffällig'),
        ('Kontrolle empfohlen', 'Kontrolle empfohlen')
    ], validators=[InputRequired()])
    
    haendigkeit = SelectField('Händigkeit', choices=[
        ('rechts', 'rechts'),
        ('links', 'links'),
        ('beidhändig', 'beidhändig')
    ], validators=[InputRequired()])
    
    erstsprache = StringField('Erstsprache', validators=[DataRequired()])
    
    ergebnis = SelectField('Ergebnis der Untersuchung', choices=[
        ('keine Bedenken', 'keine Bedenken'),
        ('erhebliche Bedenken', 'erhebliche Bedenken'),
        ('Prüfung Sonderpäd. Förderbedarf', 'Prüfung sonderpädagogischer Förderbedarf empfohlen'),
        ('vorzeitige Aufnahme nicht empfohlen', 'vorzeitige Aufnahme nicht empfohlen')
    ], validators=[InputRequired()])
    
    # Checkboxen für Förderempfehlungen
    
    foerder_grobmotorik = BooleanField('Grobmotorik')
    foerder_fein_visuomotorik = BooleanField('Fein- und Visuomotorik')
    foerder_visuelle_wahrnehmung = BooleanField('Visuelle Wahrnehmung')
    foerder_auditive_wahrnehmung = BooleanField('Auditive Wahrnehmung')
    foerder_deutschkenntnisse = BooleanField('Deutschkenntnisse')
    foerder_zahlen_mengen = BooleanField('Zahlen- und Mengenverständnis')
    foerder_konzentration = BooleanField('Konzentration')
    foerder_psychosozial = BooleanField('Psychosoziale Entwicklung')
    
    # Sprache Multi-Select
    foerder_sprache = SelectMultipleField('Sprache (Mehrfachauswahl)', choices=[
        ('Artikulation', 'Artikulation'),
        ('Grammatik', 'Grammatik'),
        ('Verständnis', 'Verständnis'),
        ('Wortschatz', 'Wortschatz')
    ], option_widget=widgets.CheckboxInput(), widget=widgets.ListWidget(prefix_label=False))
    
    aosf_verdacht = BooleanField('Verdacht auf AO-SF')
    pdf_datei = FileField('Bericht hochladen (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF-Dateien!')])
    
    bemerkung = TextAreaField('Sonstige Bemerkungen', validators=[Optional()])
    
    # Gesamteinschätzung (Radio ähnlich Diagnostik)
    CHOICES = [(3, '++'), (2, '+'), (1, 'o'), (0, '-')]
    gesamteinschaetzung = RadioField('Gesamteinschätzung', choices=CHOICES, coerce=int, validators=[InputRequired()], default=3)
    
    submit = SubmitField('Speichern')


class AOSFProzessForm(FlaskForm):
    leitung_user_id = SelectField('Interne Zuständigkeit', coerce=int, validators=[Optional()])
    
    # 1. Unterlagen
    bericht_medizin = FileField('Ärztlicher Bericht (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF!')])
    bericht_therapie = FileField('Therapeutischer Bericht (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF!')])
    
    # 2. Förderkonferenz
    konferenz_datum = DateField('Datum der Förderkonferenz', format='%Y-%m-%d', validators=[Optional()])
    konferenz_ergebnis = SelectField('Ergebnis der Konferenz', choices=[
        ('Offen', 'Offen'),
        ('Verfahren einleiten', 'Verfahren einleiten (Status wird "Fall")'),
        ('Kein Verfahren', 'Kein Verfahren')
    ], validators=[Optional()])
    
    # 3. Antrag
    antrag_datum = DateField('Datum des Antrags', format='%Y-%m-%d', validators=[Optional()])
    antrag_datei = FileField('Fertigen Antrag hochladen (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF!')])
    
    # 4. Schulamt
    schulamt_datum = DateField('Datum der Entscheidung', format='%Y-%m-%d', validators=[Optional()])
    schulamt_entscheidung = SelectField('Entscheidung Schulamt', choices=[
        ('Offen', 'Offen'),
        ('Stattgegeben', 'Stattgegeben (AO-SF genehmigt)'),
        ('Abgelehnt', 'Abgelehnt')
    ], validators=[Optional()])
    
    bemerkung = TextAreaField('Prozess-Notizen', validators=[Optional()])
    submit = SubmitField('Speichern')

class RueckstellungProzessForm(FlaskForm):
    leitung_user_id = SelectField('Interne Zuständigkeit', coerce=int, validators=[Optional()])
    
    # 1. Unterlagen
    bericht_medizin = FileField('Ärztlicher Bericht (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF!')])
    bericht_therapie = FileField('Therapeutischer Bericht (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF!')])
    
    # 2. Förderkonferenz
    konferenz_datum = DateField('Datum der Förderkonferenz', format='%Y-%m-%d', validators=[Optional()])
    konferenz_ergebnis = SelectField('Entscheidung', choices=[
        ('Offen', 'Offen'),
        ('Stattgegeben', 'Stattgegeben (Rückstellung beschlossen)'),
        ('Abgelehnt', 'Abgelehnt (Reguläre Einschulung)')
    ], validators=[Optional()])
    
    # 3. Elternschreiben
    elternschreiben_datum = DateField('Datum des Schreibens', format='%Y-%m-%d', validators=[Optional()])
    elternschreiben_datei = FileField('Elternschreiben (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF!')])
    
    bemerkung = TextAreaField('Prozess-Notizen', validators=[Optional()])
    submit = SubmitField('Speichern')

class FreundeForm(FlaskForm):
    freund1 = SelectField('Wunsch / Freund 1', coerce=int, validators=[Optional()])
    freund1_negativ = BooleanField('Nicht in einer Klasse (Negativwunsch)')
    
    freund2 = SelectField('Wunsch / Freund 2', coerce=int, validators=[Optional()])
    freund2_negativ = BooleanField('Nicht in einer Klasse (Negativwunsch)')
    
    keine_freunde = BooleanField('Keine Freunde benannt, die ich wählen möchte')
    
    bemerkung = TextAreaField('Bemerkung')
    
    submit = SubmitField('Speichern')

class KitaBerichtForm(FlaskForm):
    kognitiv = SelectField('Einschätzung Kognitiv', choices=[(0, '-'), (1, 'o'), (2, '+'), (3, '++')], coerce=int, validators=[Optional()])
    verhalten = SelectField('Einschätzung Verhalten', choices=[(0, '-'), (1, 'o'), (2, '+'), (3, '++')], coerce=int, validators=[Optional()])
    bemerkung = TextAreaField('Bemerkung / Beobachtungen')
    
    submit = SubmitField('Speichern')

class BulkBetreuungForm(FlaskForm):
    submit = SubmitField('Alle speichern')

class BulkFoerderkursForm(FlaskForm):
    submit = SubmitField('Alle speichern')

class SettingsForm(FlaskForm):
    einschulungsjahr = StringField('Einschulungsjahr (z.B. 2026)', validators=[DataRequired()])
    frist_aosf = DateField('Frist AO-SF Antrag', format='%Y-%m-%d', validators=[Optional()])
    anzahl_klassen = SelectField('Anzahl Parallelklassen', choices=[(1,'1'),(2,'2'),(3,'3'),(4,'4'),(5,'5')], coerce=int, default=3)
    submit = SubmitField('Einstellungen speichern')
