"""Validated Excel import and formula-safe export helpers."""

from io import BytesIO
from zipfile import BadZipFile, ZipFile

import pandas as pd

from models import Schueler, db
from sl_office.services.student_classification import recalculate_kann_kind
from sl_office.students.dates import parse_birthdate

MAX_ROWS = 2000
MAX_COLUMNS = 50
MAX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024


class InvalidWorkbook(ValueError):
    pass


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


def import_students(payload):
    _validate_xlsx(payload)
    frame = pd.read_excel(BytesIO(payload), engine="openpyxl")
    if len(frame.index) > MAX_ROWS or len(frame.columns) > MAX_COLUMNS:
        raise InvalidWorkbook(f"Maximal {MAX_ROWS} Zeilen und {MAX_COLUMNS} Spalten sind erlaubt.")
    frame.columns = frame.columns.astype(str).str.lower().str.strip()

    def column(*names):
        return next((name for name in names if name in frame.columns), None)

    last_name, first_name = column("name", "nachname"), column("vorname")
    birth, daycare, gender = column("geburtsdatum", "geburtstag", "geburt"), column("kita", "abgebende kita", "kindergarten"), column("geschlecht", "sex", "gender")
    if not all((last_name, first_name, birth)):
        raise InvalidWorkbook('Erforderlich sind „Name/Nachname“, „Vorname“ und „Geburtsdatum“.')

    created = skipped = invalid = 0
    for _, row in frame.iterrows():
        try:
            nachname, vorname = str(row[last_name]).strip(), str(row[first_name]).strip()
            geburtsdatum = parse_birthdate(row[birth])
            if not nachname or not vorname or nachname.lower() == "nan" or vorname.lower() == "nan":
                raise ValueError
        except (TypeError, ValueError):
            invalid += 1
            continue
        if Schueler.query.filter_by(vorname=vorname, nachname=nachname, geburtsdatum=geburtsdatum).first():
            skipped += 1
            continue
        gender_value = str(row[gender]).strip().lower() if gender and pd.notna(row[gender]) else ""
        geschlecht = "m" if gender_value.startswith("m") or gender_value == "junge" else "w" if gender_value.startswith(("w", "f")) else "d" if gender_value.startswith("d") else "u"
        kita = str(row[daycare]).strip() if daycare and pd.notna(row[daycare]) else ""
        db.session.add(Schueler(vorname=vorname, nachname=nachname, geburtsdatum=geburtsdatum, kita=kita, geschlecht=geschlecht))
        created += 1
    recalculate_kann_kind()
    db.session.commit()
    return created, skipped, invalid


def safe_excel_value(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_students(students):
    rows = []
    for student in students:
        rows.append({
            "Name": safe_excel_value(student.nachname), "Vorname": safe_excel_value(student.vorname),
            "Geburtsdatum": student.geburtsdatum.strftime("%d.%m.%Y") if student.geburtsdatum else "",
            "Kita": safe_excel_value(student.kita or ""), "Kann-Kind": "Ja" if student.kann_kind else "Nein",
            "AO-SF Status": student.aosf_prozess.status if student.aosf_prozess else ("Verdacht" if student.has_aosf_verdacht else "Nein"),
            "Gesamteindruck Kognitiv": student.diagnostik.gesamteindruck_kognitiv if student.diagnostik else "",
            "Gesamteindruck Verhalten": student.diagnostik.gesamteindruck_verhalten if student.diagnostik else "",
            "Gesamteinschätzung Schularzt": student.schularzt_untersuchung.gesamteinschaetzung if student.schularzt_untersuchung else "",
            "Teilnahme Schulspiel": "Ja" if student.diagnostik and student.diagnostik.schulspiel else "Nein",
        })
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Schülerliste")
        worksheet = writer.sheets["Schülerliste"]
        for cells in worksheet.columns:
            worksheet.column_dimensions[cells[0].column_letter].width = min(60, max(len(str(cell.value or "")) for cell in cells) + 2)
    output.seek(0)
    return output
