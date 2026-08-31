"""School-year based student classification rules."""

import datetime

from models import Diagnostik, GlobalSettings, Schueler, db


def recalculate_kann_kind(student=None, settings=None):
    """Recalculate optional-enrolment status without committing the transaction."""
    settings = settings or GlobalSettings.query.first()
    if settings is None:
        return 0

    students = [student] if student is not None else Schueler.query.all()
    for current in students:
        # The cut-off follows the child's own enrolment year, so recalculating
        # while an earlier year is open cannot mislabel that year's children.
        year = current.einschulungsjahr or settings.einschulungsjahr
        cutoff = datetime.date(year - 6, 9, 30)
        current.kann_kind = bool(current.geburtsdatum and current.geburtsdatum > cutoff)
        if current.kann_kind:
            if current.diagnostik is None:
                current.diagnostik = Diagnostik(schulspiel=True)
                db.session.add(current.diagnostik)
            else:
                current.diagnostik.schulspiel = True
    return len(students)
