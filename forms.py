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

#: Mindestlänge beim Zurücksetzen durch die Administration. Bewusst nur hier
#: verlangt: die bestehenden Passwörter der Kolleginnen und Kollegen sollen
#: nicht bei der nächsten Anmeldung plötzlich abgelehnt werden.
MIN_PASSWORD_LENGTH = 10


class PasswordResetForm(FlaskForm):
    """Neues Passwort für eine andere Person, vergeben von der Administration."""

    new_password = PasswordField(
        "Neues Passwort",
        validators=[DataRequired(),
                    Length(min=MIN_PASSWORD_LENGTH,
                           message=f"Mindestens {MIN_PASSWORD_LENGTH} Zeichen.")])
    confirm_password = PasswordField("Neues Passwort bestätigen", validators=[DataRequired()])
    submit = SubmitField("Passwort setzen")


class FirstRunForm(FlaskForm):
    """Das erste Administrationskonto einer neuen Installation."""

    username = StringField("Benutzername", validators=[DataRequired(), Length(max=80)])
    new_password = PasswordField(
        "Passwort",
        validators=[DataRequired(),
                    Length(min=MIN_PASSWORD_LENGTH,
                           message=f"Mindestens {MIN_PASSWORD_LENGTH} Zeichen.")])
    confirm_password = PasswordField("Passwort bestätigen", validators=[DataRequired()])
    submit = SubmitField("Konto anlegen")


class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('Aktuelles Passwort', validators=[DataRequired()])
    new_password = PasswordField('Neues Passwort', validators=[DataRequired()])
    confirm_password = PasswordField('Passwort bestätigen', validators=[DataRequired()])
    submit = SubmitField('Passwort ändern')

    submit = SubmitField('Passwort ändern')

class DiagnostikForm(FlaskForm):
    # Skala: 3=++, 2=+, 1=o, 0=-
    CHOICES = [(3, '++'), (2, '+'), (1, 'o'), (0, '-')]

    # Die einzelnen Beobachtungspunkte sind frei anlegbare Kriterien
    # (sl_office.criteria); hier stehen nur die festen Angaben.
    gesamteindruck_kognitiv = RadioField('Gesamteindruck Kognitiv (Vorschlag)', choices=CHOICES, coerce=int, validators=[InputRequired()])
    gesamteindruck_verhalten = RadioField('Gesamteindruck Verhalten', choices=CHOICES, coerce=int, validators=[InputRequired()])
    
    aosf_verdacht = BooleanField('Verdacht auf AO-SF')
    rueckstellung_empfohlen = BooleanField('Rückstellung vom Schulbesuch empfehlen')
    
    pdf_datei = FileField('Diagnostik Scan (PDF)', validators=[FileAllowed(['pdf'], 'Nur PDF-Dateien!')])
    
    bemerkung = TextAreaField('Bemerkungen', validators=[Optional()])
    schulspiel = RadioField('Einladung zum Schulspiel', choices=[(0, 'Nein'), (1, 'Ja')], coerce=int, default=0) # RadioField to allow iteration in template
    submit = SubmitField('Speichern')

class SchulspielForm(FlaskForm):
    # Die Beobachtungspunkte sind frei anlegbare Kriterien (sl_office.criteria).
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

class StadtImportForm(FlaskForm):
    file = FileField('Liste der Stadt (.xlsx oder .csv)', validators=[
        InputRequired(),
        FileAllowed(['xlsx', 'csv'], 'Nur XLSX- oder CSV-Dateien erlaubt!')
    ])
    submit = SubmitField('Hochladen')

class SchuelerImportForm(FlaskForm):
    file = FileField('Excel-Datei (.xlsx)', validators=[
        InputRequired(),
        FileAllowed(['xlsx'], 'Nur XLSX-Dateien erlaubt!')
    ])
    submit = SubmitField('Importieren')

class SchularztForm(FlaskForm):
    # Befund und Förderempfehlungen sind frei anlegbare Kriterien
    # (sl_office.criteria); hier stehen nur die festen Angaben.
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
