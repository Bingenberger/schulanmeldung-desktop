"""Filtering queries for student lists and exports."""

from models import Schueler


def get_filtered_students(args):
    def active(name):
        return str(args.get(name)).lower() in {"on", "true", "1"}

    filters = {
        "kita": args.get("kita"),
        "kann_kind": active("kann_kind"),
        "aosf": active("aosf"),
        "rueckstellung": active("rueckstellung"),
        "missing_arzt": active("missing_arzt"),
        "missing_diag": active("missing_diag"),
        "schulspiel": active("schulspiel"),
    }
    query = Schueler.query
    if filters["kita"]:
        query = query.filter(Schueler.kita == filters["kita"])
    if filters["kann_kind"]:
        query = query.filter(Schueler.kann_kind.is_(True))
    if filters["missing_arzt"]:
        query = query.filter(Schueler.arzt_status != "Abgeschlossen")
    if filters["missing_diag"]:
        query = query.filter(Schueler.diag_status != "Abgeschlossen")

    students = []
    for student in query.order_by(Schueler.nachname, Schueler.vorname).all():
        if filters["aosf"] and not student.has_aosf_verdacht:
            continue
        if filters["rueckstellung"] and not student.has_rueckstellung_empfohlen:
            continue
        if filters["schulspiel"] and not (student.diagnostik and student.diagnostik.schulspiel):
            continue
        students.append(student)
    return students, filters
