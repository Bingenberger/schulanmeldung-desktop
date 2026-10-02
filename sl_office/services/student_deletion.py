"""Transactional deletion support for student records and legacy files."""

from pathlib import Path

from sqlalchemy import delete, select

from models import Schueler, db
from sl_office.criteria.models import KriteriumWert
from sl_office.parent_portal.models import (
    ActivationGrant, AppointmentBooking, ParentAccess, ParentLoginToken, ParentRegistration,
)


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


def _delete_portal_records(student_id):
    """Elternportal-Daten und erfasste Kriterien des Kindes entfernen.

    Diese Tabellen hängen nicht als ORM-Beziehung am Schüler, sondern nur über
    ``ON DELETE CASCADE`` in der Datenbank. Darauf allein ist kein Verlass:
    SQLite prüft Fremdschlüssel nur bei eingeschaltetem ``PRAGMA foreign_keys``,
    und die Zeilen blieben sonst als Waisen zurück. Da SQLite die freigewordene
    Zeilennummer weitervergibt, würde das nächste angelegte Kind sie erben --
    mitsamt fremdem Elternzugang und fremden Formulardaten.
    """
    # Zugänge zuerst einsammeln: an ihnen hängen die Anmeldelinks.
    access_ids = list(db.session.scalars(
        select(ParentAccess.id).where(ParentAccess.schueler_id == student_id)))
    if access_ids:
        db.session.execute(delete(ParentLoginToken).where(
            ParentLoginToken.parent_access_id.in_(access_ids)))
    for model in (ActivationGrant, ParentRegistration, AppointmentBooking, ParentAccess,
                  KriteriumWert):
        db.session.execute(delete(model).where(model.schueler_id == student_id))


def delete_student(student, upload_folder):
    """Delete one student transactionally, then clean up referenced files."""
    filenames = {name for name in _document_names(student) if name}
    _delete_portal_records(student.id)
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
        _delete_portal_records(student.id)
        db.session.delete(student)
    db.session.commit()

    _delete_files(filenames, upload_folder)
    return len(students)
