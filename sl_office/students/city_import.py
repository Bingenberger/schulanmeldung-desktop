"""Two-step import of the municipal enrolment list.

The city delivers a spreadsheet whose column headings change from year to year,
so the file is parsed first and the operator maps the columns afterwards. The
parsed rows are parked in a server-side staging file; only its id travels in
the session.
"""

import json
import os
import re
import secrets
import tempfile
from io import BytesIO, StringIO
from zipfile import BadZipFile, ZipFile

import pandas as pd

from models import Schueler, db
from sl_office.services.student_classification import recalculate_kann_kind
from sl_office.students.dates import parse_birthdate

MAX_ROWS = 2000
MAX_COLUMNS = 60
MAX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
STAGING_PREFIX = "sl_office_city_import_"
#: How long a parked upload may wait for its mapping.
STAGING_MAX_AGE_SECONDS = 60 * 60


class InvalidWorkbook(ValueError):
    pass


class StagingExpired(ValueError):
    pass


#: Target field -> (label, required, header aliases used to pre-select a column)
TARGET_FIELDS = (
    ("nachname", "Nachname", True, ("nachname", "name", "familienname", "zuname")),
    ("vorname", "Vorname", True, ("vorname", "rufname", "vornamen")),
    ("geburtsdatum", "Geburtsdatum", True, ("geburtsdatum", "geburtstag", "geboren", "geb", "geb.datum")),
    ("geschlecht", "Geschlecht", False, ("geschlecht", "sex", "gender")),
    ("strasse", "Straße (ggf. mit Hausnummer)", False, ("strasse", "straße", "strasse hausnummer", "anschrift", "adresse", "str")),
    ("hausnummer", "Hausnummer (falls eigene Spalte)", False, ("hausnummer", "hausnr", "hnr", "nr")),
    ("plz", "PLZ", False, ("plz", "postleitzahl")),
    ("ort", "Ort", False, ("ort", "wohnort", "stadt", "gemeinde")),
    ("erzb_1_name", "Erziehungsberechtigte:r 1", False,
     ("erziehungsberechtigter 1", "erziehungsberechtigte 1", "erziehungsberechtigter", "mutter", "elternteil 1", "sorgeberechtigt 1")),
    ("erzb_2_name", "Erziehungsberechtigte:r 2", False,
     ("erziehungsberechtigter 2", "erziehungsberechtigte 2", "vater", "elternteil 2", "sorgeberechtigt 2")),
    ("kita", "Kita", False, ("kita", "abgebende kita", "kindergarten", "tageseinrichtung")),
)
REQUIRED_FIELDS = tuple(name for name, _, required, _ in TARGET_FIELDS if required)


def _validate_xlsx(payload):
    if not payload.startswith(b"PK"):
        raise InvalidWorkbook("Die Datei ist keine gültige XLSX-Arbeitsmappe.")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > 500 or sum(item.file_size for item in entries) > MAX_UNCOMPRESSED_BYTES:
                raise InvalidWorkbook("Die Arbeitsmappe ist zu groß oder zu komplex.")
    except BadZipFile as exc:
        raise InvalidWorkbook("Die XLSX-Arbeitsmappe ist beschädigt.") from exc


def _normalize_header(value):
    return re.sub(r"[\s_.:-]+", " ", str(value)).strip().casefold()


REMEMBERED_KEY = "import:stadt"
#: Kurze Aliasse wie "nr" stecken in vielen Spaltenköpfen ("Straße / Nr.");
#: diese Felder werden nur bei genau passender Überschrift vorgeschlagen.
EXACT_ONLY = {"hausnummer"}


def remembered_mapping():
    """Die Zuordnung des letzten erfolgreichen Imports, ``{Feld: Spaltenkopf}``."""
    from flask import has_app_context
    from sl_office.school_profile import Schulprofil

    if not has_app_context():
        return {}
    row = db.session.get(Schulprofil, REMEMBERED_KEY)
    try:
        stored = json.loads(row.wert) if row is not None else {}
    except ValueError:
        return {}
    return {name: column for name, column in stored.items()
            if isinstance(name, str) and isinstance(column, str)}


def remember_mapping(mapping):
    """Die Zuordnung merken: die Stadt liefert meist Jahr für Jahr dieselben Spalten."""
    from sl_office.school_profile import Schulprofil

    row = db.session.get(Schulprofil, REMEMBERED_KEY)
    if row is None:
        row = Schulprofil(key=REMEMBERED_KEY)
        db.session.add(row)
    row.wert = json.dumps(mapping, ensure_ascii=False)


def suggest_mapping(headers, remembered=None):
    """Pre-select a column per target field.

    A column the school chose for this field last time wins; otherwise known
    header aliases decide.
    """
    remembered = remembered_mapping() if remembered is None else remembered
    normalized = {_normalize_header(header): header for header in headers}
    mapping = {name: column for name, column in remembered.items() if column in headers}
    for name, _, _, aliases in TARGET_FIELDS:
        if name in mapping:
            continue
        match = next((normalized[alias] for alias in aliases if alias in normalized), None)
        if match is None and name not in EXACT_ONLY:
            # Fall back to a header that merely contains an alias, e.g. "Straße/Nr.".
            match = next(
                (original for key, original in normalized.items()
                 if any(alias in key for alias in aliases)),
                None,
            )
        if match is not None and match not in mapping.values():
            mapping[name] = match
    return mapping


def _read_csv(payload):
    """CSV-Export einer Stadtverwaltung: Trennzeichen und Zeichensatz erraten.

    Behördensoftware schreibt oft Windows-1252 mit Semikolon, andere UTF-8
    mit Komma; beides kommt vor.
    """
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise InvalidWorkbook("Die CSV-Datei hat einen unbekannten Zeichensatz.")
    first_line = text.splitlines()[0] if text.strip() else ""
    separator = max((";", ",", "\t"), key=first_line.count)
    try:
        return pd.read_csv(StringIO(text), sep=separator, dtype=str, nrows=MAX_ROWS + 1)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise InvalidWorkbook("Die CSV-Datei ist nicht lesbar.") from exc


def stage_upload(payload, filename=""):
    """Parse the workbook and park its rows; returns (token, headers, preview)."""
    if filename.lower().endswith(".csv") or not payload.startswith(b"PK"):
        if b"\x00" in payload[:4096]:
            raise InvalidWorkbook("Die Datei ist weder eine XLSX-Arbeitsmappe noch eine CSV-Datei.")
        frame = _read_csv(payload)
    else:
        _validate_xlsx(payload)
        frame = pd.read_excel(BytesIO(payload), engine="openpyxl", dtype=str)
    if frame.empty:
        raise InvalidWorkbook("Die Arbeitsmappe enthält keine Datenzeilen.")
    if len(frame.index) > MAX_ROWS or len(frame.columns) > MAX_COLUMNS:
        raise InvalidWorkbook(f"Maximal {MAX_ROWS} Zeilen und {MAX_COLUMNS} Spalten sind erlaubt.")
    frame.columns = [str(column).strip() for column in frame.columns]
    frame = frame.where(pd.notna(frame), None)
    rows = frame.to_dict(orient="records")
    headers = list(frame.columns)

    token = secrets.token_urlsafe(16)
    with open(_staging_path(token), "w", encoding="utf-8") as handle:
        json.dump({"headers": headers, "rows": rows}, handle)
    return token, headers, rows[:5]


def _staging_path(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", token or ""):
        raise StagingExpired("Der Importvorgang ist nicht mehr verfügbar.")
    return os.path.join(tempfile.gettempdir(), f"{STAGING_PREFIX}{token}.json")


def load_staged(token):
    try:
        with open(_staging_path(token), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise StagingExpired("Der Importvorgang ist abgelaufen. Bitte die Datei erneut hochladen.") from exc


def discard_staged(token):
    try:
        os.unlink(_staging_path(token))
    except (OSError, StagingExpired):
        pass


def purge_stale_staging(max_age_seconds=STAGING_MAX_AGE_SECONDS):
    """Drop staging files left behind by abandoned imports."""
    import time

    directory = tempfile.gettempdir()
    cutoff = time.time() - max_age_seconds
    for entry in os.listdir(directory):
        if not entry.startswith(STAGING_PREFIX):
            continue
        path = os.path.join(directory, entry)
        try:
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
        except OSError:
            continue


def _clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "nat"} else text


def _gender(value):
    text = _clean(value).lower()
    if text.startswith("m") or text in {"junge", "männlich"}:
        return "m"
    if text.startswith(("w", "f")) or text == "mädchen":
        return "w"
    if text.startswith("d"):
        return "d"
    return "u"


def import_rows(staged, mapping):
    """Apply the operator's column mapping and write the students.

    Existing children are updated with the address and guardian names rather
    than duplicated, so the list can be re-imported after the city corrects it.
    """
    missing = [name for name in REQUIRED_FIELDS if not mapping.get(name)]
    if missing:
        labels = ", ".join(label for name, label, _, _ in TARGET_FIELDS if name in missing)
        raise InvalidWorkbook(f"Bitte eine Spalte zuordnen für: {labels}.")
    headers = set(staged["headers"])
    unknown = [column for column in mapping.values() if column and column not in headers]
    if unknown:
        raise InvalidWorkbook("Die Zuordnung passt nicht zur hochgeladenen Datei.")
    # Eine Spalte zweimal zuzuordnen ist immer ein Versehen -- und ein teures:
    # So stand im Jahrgang 2027 der Name des Vaters in der Kita-Spalte, und das
    # fiel erst Monate später auf einem gedruckten Bogen auf.
    belegt = {}
    for feld, spalte in mapping.items():
        if spalte:
            belegt.setdefault(spalte, []).append(feld)
    beschriftung = {name: label for name, label, _, _ in TARGET_FIELDS}
    doppelt = [(spalte, felder) for spalte, felder in belegt.items() if len(felder) > 1]
    if doppelt:
        hinweise = "; ".join(
            f"„{spalte}“ für {' und '.join(beschriftung.get(feld, feld) for feld in felder)}"
            for spalte, felder in doppelt)
        raise InvalidWorkbook(
            f"Dieselbe Spalte ist mehrfach zugeordnet: {hinweise}. Bitte je Feld eine "
            "eigene Spalte wählen oder das Feld leer lassen.")

    def value(row, field):
        column = mapping.get(field)
        return _clean(row.get(column)) if column else ""

    created = updated = invalid = 0
    for row in staged["rows"]:
        nachname, vorname = value(row, "nachname"), value(row, "vorname")
        raw_birth = value(row, "geburtsdatum")
        if not nachname or not vorname or not raw_birth:
            invalid += 1
            continue
        try:
            geburtsdatum = parse_birthdate(raw_birth)
        except (TypeError, ValueError):
            invalid += 1
            continue

        strasse = " ".join(part for part in (value(row, "strasse"), value(row, "hausnummer")) if part)
        fields = {
            "strasse": strasse, "plz": value(row, "plz"), "ort": value(row, "ort"),
            "erzb_1_name": value(row, "erzb_1_name"), "erzb_2_name": value(row, "erzb_2_name"),
        }
        kita = value(row, "kita")
        student = Schueler.query.filter_by(
            vorname=vorname, nachname=nachname, geburtsdatum=geburtsdatum
        ).first()
        if student:
            for key, new_value in fields.items():
                if new_value:
                    setattr(student, key, new_value)
            if kita:
                student.kita = kita
            updated += 1
        else:
            db.session.add(Schueler(
                vorname=vorname, nachname=nachname, geburtsdatum=geburtsdatum,
                geschlecht=_gender(row.get(mapping.get("geschlecht"))) if mapping.get("geschlecht") else "u",
                kita=kita, **fields,
            ))
            created += 1
    recalculate_kann_kind()
    remember_mapping(mapping)
    db.session.commit()
    return created, updated, invalid
