"""Transactional deletion support for student records and legacy files."""

from pathlib import Path

from models import Schueler, db


def _document_names(student):
    if student.diagnostik:
        yield student.diagnostik.pdf_dateiname
    if student.schulspiel_diagnostik:
        yield student.schulspiel_diagnostik.pdf_dateiname
    if student.schularzt_untersuchung:
        yield student.schularzt_untersuchung.pdf_dateiname
    if student.aosf_prozess:
        yield student.aosf_prozess.bericht_medizin_dateiname
        yield student.aosf_prozess.bericht_therapie_dateiname
        yield student.aosf_prozess.antrag_dateiname
    if student.rueckstellung_prozess:
        yield student.rueckstellung_prozess.bericht_medizin_dateiname
        yield student.rueckstellung_prozess.bericht_therapie_dateiname
        yield student.rueckstellung_prozess.elternschreiben_dateiname


def _delete_files(filenames, upload_folder):
    upload_root = Path(upload_folder).resolve()
    for filename in filenames:
        candidate = (upload_root / filename).resolve()
        try:
            candidate.relative_to(upload_root)
        except ValueError:
            continue
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            continue


def delete_student(student, upload_folder):
    """Delete one student transactionally, then clean up referenced files."""
    filenames = {name for name in _document_names(student) if name}
    db.session.delete(student)
    db.session.commit()
    _delete_files(filenames, upload_folder)


def delete_all_students(upload_folder):
    """Delete database records first, then best-effort legacy files.

    File failures leave harmless orphans rather than rolling back already removed
    files while database deletion fails.
    """
    students = Schueler.query.all()
    filenames = {name for student in students for name in _document_names(student) if name}
    for student in students:
        db.session.delete(student)
    db.session.commit()

    _delete_files(filenames, upload_folder)
    return len(students)
