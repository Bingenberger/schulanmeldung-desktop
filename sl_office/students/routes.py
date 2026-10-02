"""CRUD routes for verified internal student master data."""

import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import login_required

from forms import SchuelerForm, SchuelerImportForm
from models import AOSF, Diagnostik, Rueckstellung, SchulaerztlicheUntersuchung, Schueler, db
from sl_office.authorization import role_required
from sl_office.services.student_classification import recalculate_kann_kind
from sl_office.services.student_deletion import delete_student
from sl_office.students.listing import get_filtered_students
from sl_office.students.excel import InvalidWorkbook, export_students, import_students
from sl_office.students import city_import
from sl_office.criteria import service as criteria
from sl_office.appointments.service import (
    active_booking_for_student, assignable_slots, planning_event, slot_label,
)

students_bp = Blueprint("students", __name__)
WRITE_ROLES = ["Administrator", "Schulleitung", "Sekretariat"]


@students_bp.route("/import", methods=["GET", "POST"])
@role_required(WRITE_ROLES)
def import_excel():
    form = SchuelerImportForm()
    if form.validate_on_submit():
        payload = form.file.data.read(current_app.config["MAX_EXCEL_IMPORT_BYTES"] + 1)
        if len(payload) > current_app.config["MAX_EXCEL_IMPORT_BYTES"]:
            flash("Die Excel-Datei ist zu groß.", "error")
        else:
            try:
                created, skipped, invalid = import_students(payload)
                flash(f"Import abgeschlossen: {created} neu, {skipped} Dubletten, {invalid} ungültige Zeilen.")
                return redirect(url_for("index"))
            except InvalidWorkbook as exc:
                db.session.rollback()
                flash(str(exc), "error")
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Student Excel import failed")
                flash("Die Excel-Datei konnte nicht verarbeitet werden.", "error")
    return render_template("import_schueler.html", form=form)


CITY_IMPORT_SESSION_KEY = "city_import_token"


@students_bp.route("/import/stadt", methods=["GET", "POST"])
@role_required(WRITE_ROLES)
def import_city():
    """Step 1: upload the municipal list and park it for column mapping."""
    form = SchuelerImportForm()
    if form.validate_on_submit():
        payload = form.file.data.read(current_app.config["MAX_EXCEL_IMPORT_BYTES"] + 1)
        if len(payload) > current_app.config["MAX_EXCEL_IMPORT_BYTES"]:
            flash("Die Excel-Datei ist zu groß.", "error")
        else:
            try:
                city_import.purge_stale_staging()
                previous = session.pop(CITY_IMPORT_SESSION_KEY, None)
                if previous:
                    city_import.discard_staged(previous)
                token, headers, preview = city_import.stage_upload(payload)
            except city_import.InvalidWorkbook as exc:
                flash(str(exc), "error")
            except Exception:
                current_app.logger.exception("Municipal list upload failed")
                flash("Die Datei konnte nicht gelesen werden.", "error")
            else:
                session[CITY_IMPORT_SESSION_KEY] = token
                return render_template(
                    "import_stadt.html", form=form, headers=headers, preview=preview,
                    fields=city_import.TARGET_FIELDS,
                    mapping=city_import.suggest_mapping(headers),
                )
    return render_template("import_stadt.html", form=form, headers=None)


@students_bp.post("/import/stadt/zuordnen")
@role_required(WRITE_ROLES)
def import_city_apply():
    """Step 2: apply the operator's column mapping and write the students."""
    token = session.get(CITY_IMPORT_SESSION_KEY)
    try:
        staged = city_import.load_staged(token)
    except city_import.StagingExpired as exc:
        session.pop(CITY_IMPORT_SESSION_KEY, None)
        flash(str(exc), "error")
        return redirect(url_for("students.import_city"))

    mapping = {
        name: request.form.get(f"map_{name}", "").strip()
        for name, _, _, _ in city_import.TARGET_FIELDS
    }
    mapping = {name: column for name, column in mapping.items() if column}
    try:
        created, updated, invalid = city_import.import_rows(staged, mapping)
    except city_import.InvalidWorkbook as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return render_template(
            "import_stadt.html", form=SchuelerImportForm(), headers=staged["headers"],
            preview=staged["rows"][:5], fields=city_import.TARGET_FIELDS, mapping=mapping,
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Municipal list import failed")
        flash("Der Import konnte nicht abgeschlossen werden.", "error")
        return redirect(url_for("students.import_city"))

    city_import.discard_staged(token)
    session.pop(CITY_IMPORT_SESSION_KEY, None)
    flash(f"Import abgeschlossen: {created} neu angelegt, {updated} aktualisiert, {invalid} ungültige Zeilen.")
    return redirect(url_for("students.list_students"))


@students_bp.get("/export/excel")
@role_required(WRITE_ROLES)
def export_excel():
    students, _ = get_filtered_students(request.args)
    if not students:
        flash("Keine Schüler für den Export gefunden.", "warning")
        return redirect(url_for("students.list_students", **request.args))
    return send_file(
        export_students(students), as_attachment=True,
        download_name=f"Schueler_Export_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@students_bp.get("/liste")
@login_required
def list_students():
    students, filters = get_filtered_students(request.args)
    kitas = db.session.query(Schueler.kita).distinct().order_by(Schueler.kita).all()
    return render_template(
        "schueler_liste.html", schueler=students,
        kita_options=[name for (name,) in kitas if name], current_filters=filters,
    )


@students_bp.route("/select/<target>", methods=["GET", "POST"])
@login_required
def select(target):
    endpoints = {
        "diagnostik": "diagnostik", "schulspiel": "schulspiel",
        "schularzt": "schularzt", "freunde": "freunde_bearbeiten",
        "kita_bericht": "kita_bericht",
    }
    if target not in endpoints:
        flash("Ungültiges Ziel.")
        return redirect(url_for("index"))
    if request.method == "POST":
        student_id = request.form.get("schueler_id", type=int)
        if student_id:
            return redirect(url_for(endpoints[target], id=student_id))
        flash("Bitte wählen Sie einen Schüler aus.")
    students = Schueler.query.order_by(Schueler.nachname, Schueler.vorname).all()
    return render_template("select_schueler.html", schueler_list=students, target=target)


@students_bp.route("/add", methods=["GET", "POST"])
@role_required(WRITE_ROLES)
def add():
    form = SchuelerForm()
    if form.validate_on_submit():
        student = Schueler(
            vorname=form.vorname.data,
            nachname=form.nachname.data,
            geburtsdatum=form.geburtsdatum.data,
            geschlecht=form.geschlecht.data,
            kita=form.kita.data,
            betreuung=form.betreuung.data or None,
            strasse=form.strasse.data or None,
            plz=form.plz.data or None,
            ort=form.ort.data or None,
            erzb_1_name=form.erzb_1_name.data or None,
            erzb_2_name=form.erzb_2_name.data or None,
        )
        try:
            db.session.add(student)
            # Erst speichern, dann einordnen: das Einschulungsjahr wird beim
            # Flush gesetzt und bestimmt den Stichtag fuer das Kann-Kind.
            db.session.flush()
            recalculate_kann_kind(student)
            db.session.commit()
            flash(f"Schüler {student.vorname} {student.nachname} wurde erfolgreich angelegt!")
            return redirect(url_for("index"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Student creation failed")
            flash("Der Schülerdatensatz konnte nicht angelegt werden.")
    return render_template("schueler_neu.html", form=form, back_url=url_for("index"), title="Schüler anlegen")


@students_bp.route("/schueler/<int:student_id>/edit", methods=["GET", "POST"])
@role_required(WRITE_ROLES)
def edit(student_id):
    student = db.get_or_404(Schueler, student_id)
    form = SchuelerForm(obj=student)
    if form.validate_on_submit():
        form.populate_obj(student)
        recalculate_kann_kind(student)
        try:
            db.session.commit()
            flash("Stammdaten erfolgreich aktualisiert.")
            return redirect(url_for("students.detail", student_id=student.id))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Student update failed", extra={"student_id": student_id})
            flash("Die Stammdaten konnten nicht gespeichert werden.")
    return render_template(
        "schueler_neu.html", form=form, title="Stammdaten bearbeiten",
        back_url=url_for("students.detail", student_id=student.id),
    )


@students_bp.post("/schueler/<int:student_id>/delete")
@role_required(WRITE_ROLES)
def delete(student_id):
    student = db.get_or_404(Schueler, student_id)
    student_name = f"{student.vorname} {student.nachname}"
    try:
        delete_student(student, current_app.config["UPLOAD_FOLDER"])
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Student deletion failed", extra={"student_id": student_id})
        flash("Der Schülerdatensatz konnte nicht gelöscht werden.")
        # Der Rollback hat den Datensatz erhalten, die Detailseite gibt es also noch.
        return redirect(url_for("students.detail", student_id=student_id))
    # Bewusst außerhalb des try: ein Fehler beim Bauen dieser Adresse ist ein
    # Programmfehler und darf nicht als "Löschen fehlgeschlagen" erscheinen --
    # das Kind ist zu diesem Zeitpunkt weg, seine Detailseite gibt es nicht mehr.
    flash(f"Schüler {student_name} und alle zugehörigen Dateien wurden gelöscht.")
    return redirect(url_for("students.list_students"))


@students_bp.get("/schueler/<int:student_id>")
@login_required
def detail(student_id):
    student = db.get_or_404(Schueler, student_id)
    diagnostik = Diagnostik.query.filter_by(schueler_id=student_id).first()
    schularzt = SchulaerztlicheUntersuchung.query.filter_by(schueler_id=student_id).first()
    aosf = AOSF.query.filter_by(schueler_id=student_id).first()
    rueckstellung = Rueckstellung.query.filter_by(schueler_id=student_id).first()
    return render_template(
        "schueler_detail.html", schueler=student, diagnostik=diagnostik, schularzt=schularzt,
        aosf=aosf,
        has_aosf_suspicion=bool(aosf or (diagnostik and diagnostik.aosf_verdacht) or (schularzt and schularzt.aosf_verdacht)),
        rueckstellung=rueckstellung,
        has_rueckstellung_empfohlen=bool(rueckstellung or (diagnostik and diagnostik.rueckstellung_empfohlen)),
        kriterien_diagnostik=criteria.eintraege(student_id, "diagnostik"),
        kriterien_schularzt=criteria.eintraege(student_id, "schularzt"),
        foerderhinweise=criteria.foerderhinweise(student_id),
        **_appointment_context(student_id),
    )


def _appointment_context(student_id):
    """Appointment card data: either the child's slot, or the choices to give one."""
    booked = active_booking_for_student(student_id)
    if booked:
        booking, slot, event = booked
        return {
            "appointment": booking,
            "appointment_label": slot_label(slot, event),
            "appointment_event": event,
            "free_slots": [],
        }
    event = planning_event()
    return {
        "appointment": None,
        "appointment_label": None,
        "appointment_event": event,
        "free_slots": [] if event is None else [
            {"id": slot.id, "label": slot_label(slot, event), "free": slot.capacity - taken}
            for slot, taken in assignable_slots(event.id)
        ],
    }
