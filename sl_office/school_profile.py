"""Schulprofil: Angaben zur Schule, in der Anwendung gepflegt.

Name, Anschrift, Schulleitung, Logo und Unterschrift stehen im Briefkopf und
in den Schreiben. Die Serverfassung las sie aus Umgebungsvariablen; die
Desktop-Fassung soll jede Schule selbst einrichten können. Darum liegen sie
in der Tabelle ``schulprofil``: eine Zeile je Angabe, Bilder als Bytes in
derselben Zeile -- so stecken sie mit in jeder Datensicherung.

Was in der Tabelle fehlt, kommt weiter aus der Konfiguration. Eine bestehende
Installation mit ``SL_OFFICE_SCHOOL_*`` läuft also unverändert weiter, bis
jemand das Profil speichert.
"""

from io import BytesIO

from flask import current_app
from PIL import Image, UnidentifiedImageError

from models import db

#: Textangaben in der Reihenfolge des Formulars: (Schlüssel, Bezeichnung, Hinweis).
TEXT_FIELDS = (
    ("SCHOOL_NAME", "Name der Schule", "steht groß im Briefkopf und in der Anmeldemaske"),
    ("SCHOOL_MOTTO", "Leitspruch", "kleine Zeile unter dem Namen, optional"),
    ("SCHOOL_STREET", "Straße und Hausnummer", ""),
    ("SCHOOL_CITY_LINE", "PLZ und Ort", "z. B. 12345 Musterstadt"),
    ("SCHOOL_PHONE", "Telefon", "erscheint im Briefkopf und als {telefon} im Elternbrief"),
    ("SCHOOL_EMAIL", "E-Mail der Schule", "Briefkopf"),
    ("SCHOOL_WEB", "Internetseite", "Briefkopf, optional"),
    ("SCHOOL_TOWN", "Stadt oder Gemeinde",
     "Datumszeile der Briefe und „Anmeldeschein der Stadt …“"),
    ("SCHOOL_DISTRICT", "Einzugsbereich", "wie er im Elternbrief genannt wird, z. B. Musterstadt-Nord"),
    ("SCHOOL_HEALTH_OFFICE", "Gesundheitsamt",
     "im Genitiv, wie es im Satz steht: „Schreiben des Gesundheitsamtes Musterkreis“"),
    ("SCHOOL_CONTACT_MAIL", "E-Mail für Rückfragen der Eltern", "{kontakt} im Elternbrief"),
    ("SCHOOL_HEAD", "Schulleitung", "Zeile unter der Unterschrift, z. B. „A. Muster, Schulleiterin“"),
)
TEXT_KEYS = tuple(key for key, _, _ in TEXT_FIELDS)

#: Bilder des Briefkopfs: (Schlüssel, Bezeichnung, Hinweis).
IMAGE_FIELDS = (
    ("SCHOOL_LOGO", "Logo", "oben rechts im Briefkopf; PNG mit transparentem Hintergrund eignet sich am besten"),
    ("SCHOOL_SIGNATURE", "Unterschrift", "eingescannte Unterschrift der Schulleitung unter den Briefen"),
)
IMAGE_KEYS = tuple(key for key, _, _ in IMAGE_FIELDS)

MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg"}


class Schulprofil(db.Model):
    """Eine Angabe des Schulprofils; Bilder stehen in ``daten``."""
    __tablename__ = "schulprofil"
    key = db.Column(db.String(40), primary_key=True)
    wert = db.Column(db.Text, nullable=False, default="")
    daten = db.Column(db.LargeBinary, nullable=True)


class ImageError(ValueError):
    """Hochgeladene Datei ist kein brauchbares Bild."""


def _rows():
    return {row.key: row for row in db.session.scalars(db.select(Schulprofil))}


def settings(config=None):
    """Alle Angaben zur Schule, wie sie :func:`letterhead.branding` erwartet.

    Gespeicherte Werte gehen vor; was fehlt, kommt aus der Konfiguration.
    Bilder sind entweder Bytes aus der Datenbank oder ein Dateipfad.
    """
    config = current_app.config if config is None else config
    merged = {key: config.get(key) or "" for key in TEXT_KEYS + IMAGE_KEYS + ("SCHOOL_ADDRESS",)}
    for key, row in _rows().items():
        if key in TEXT_KEYS:
            merged[key] = row.wert
        elif key in IMAGE_KEYS:
            merged[key] = row.daten or ""
    return merged


def get(key, default=""):
    return settings().get(key) or default


def is_configured():
    """Ob die Schule ihr Profil schon einmal gespeichert hat."""
    return db.session.get(Schulprofil, "SCHOOL_NAME") is not None


def save_texts(values):
    """Textangaben übernehmen; unbekannte Schlüssel werden ignoriert."""
    rows = _rows()
    for key in TEXT_KEYS:
        value = (values.get(key) or "").strip()
        row = rows.get(key)
        if row is None:
            db.session.add(Schulprofil(key=key, wert=value))
        else:
            row.wert = value


def check_image(payload):
    """Prüft ein hochgeladenes Bild und gibt den Inhaltstyp zurück."""
    if not payload:
        raise ImageError("Die Datei ist leer.")
    if len(payload) > MAX_IMAGE_BYTES:
        raise ImageError("Das Bild ist größer als 2 MB.")
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
            kind = image.format
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ImageError("Die Datei ist kein lesbares Bild.") from exc
    if kind not in IMAGE_FORMATS:
        raise ImageError("Bitte ein Bild als PNG oder JPEG hochladen.")
    return IMAGE_FORMATS[kind]


def save_image(key, payload):
    if key not in IMAGE_KEYS:
        raise KeyError(key)
    mimetype = check_image(payload)
    row = db.session.get(Schulprofil, key)
    if row is None:
        row = Schulprofil(key=key)
        db.session.add(row)
    row.wert, row.daten = mimetype, payload


def remove_image(key):
    """Bild entfernen; der Briefkopf kommt dann ohne aus.

    Die Zeile bleibt mit leeren Daten stehen, damit nicht wieder das Bild aus
    der Konfiguration erscheint.
    """
    if key not in IMAGE_KEYS:
        raise KeyError(key)
    row = db.session.get(Schulprofil, key)
    if row is None:
        row = Schulprofil(key=key)
        db.session.add(row)
    row.wert, row.daten = "", None


def image(key):
    """(Bytes, Inhaltstyp) eines gespeicherten Bildes, sonst ``None``."""
    row = db.session.get(Schulprofil, key)
    if row is None or not row.daten:
        return None
    return row.daten, row.wert or "application/octet-stream"
