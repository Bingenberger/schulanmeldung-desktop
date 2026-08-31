"""Public, passwordless parent portal routes."""

from functools import wraps

from io import BytesIO

from flask import (Blueprint, current_app, flash, redirect, render_template, request,
                   send_file, session, url_for)
from sqlalchemy import func, select

from models import Schueler, db
from sl_office.appointments import calendar
from sl_office.appointments.service import (BookingError, active_booking_for_student,
                                            book_slot, cancel_booking, slot_label)
from sl_office.parent_portal.access_service import InvalidAccessToken, consume_activation_grant, consume_login_token, create_login_token, normalize_email
from sl_office.parent_portal.mail_service import send_parent_login_link
from sl_office.parent_portal.models import AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentAccess, ParentRegistration, utcnow
from sl_office.parent_portal import registration_form
from sl_office.audit import record

parent_portal_bp = Blueprint("parent_portal", __name__, url_prefix="/eltern")
SESSION_KEY = "parent_access_id"
SESSION_VERSION_KEY = "parent_access_version"


def _current_parent_access():
    access_id = session.get(SESSION_KEY)
    access = db.session.get(ParentAccess, access_id) if access_id else None
    if not access or access.status != "active" or access.security_version != session.get(SESSION_VERSION_KEY):
        session.pop(SESSION_KEY, None)
        session.pop(SESSION_VERSION_KEY, None)
        return None
    return access


def parent_access_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        access = _current_parent_access()
        if access is None:
            flash("Bitte verwenden Sie Ihren persönlichen Zugangslink.")
            return redirect(url_for("parent_portal.start"))
        return view(access, *args, **kwargs)
    return wrapped


def _start_session(access):
    session.clear()
    session[SESSION_KEY] = access.id
    session[SESSION_VERSION_KEY] = access.security_version
    session.permanent = True


@parent_portal_bp.get("/")
def start():
    if _current_parent_access():
        return redirect(url_for("parent_portal.dashboard"))
    return render_template("parent_portal/start.html")


@parent_portal_bp.route("/link-anfordern", methods=["GET", "POST"])
def request_login_link():
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        access = db.session.scalar(select(ParentAccess).where(
            ParentAccess.email_normalized == email, ParentAccess.status == "active"
        )) if email else None
        if access:
            _, token = create_login_token(access.id)
            db.session.commit()
            link = url_for("parent_portal.login", token=token, _external=True)
            student = db.session.get(Schueler, access.schueler_id)
            try:
                send_parent_login_link(current_app, email, link, f"{student.vorname} {student.nachname}")
            except Exception:
                current_app.logger.exception("Parent login mail failed")
        else:
            db.session.rollback()
        flash("Wenn ein aktiver Zugang zu dieser Adresse besteht, wurde ein neuer Link versendet.")
        return redirect(url_for("parent_portal.start"))
    return render_template("parent_portal/request_link.html")


@parent_portal_bp.route("/aktivieren/<token>", methods=["GET", "POST"])
def activate(token):
    if request.method == "POST":
        try:
            access = consume_activation_grant(token, request.form.get("email", ""), request.form.get("display_name", ""))
            record("parent_access_activated", "parent_access", access.id, actor_type="parent", actor_id=access.id)
            db.session.commit()
        except (InvalidAccessToken, ValueError) as exc:
            db.session.rollback()
            flash(str(exc))
        else:
            _start_session(access)
            return redirect(url_for("parent_portal.dashboard"))
    return render_template("parent_portal/activate.html")


@parent_portal_bp.get("/anmelden/<token>")
def login(token):
    try:
        access = consume_login_token(token)
        record("parent_login", "parent_access", access.id, actor_type="parent", actor_id=access.id)
        db.session.commit()
    except InvalidAccessToken as exc:
        db.session.rollback()
        flash(str(exc))
        return redirect(url_for("parent_portal.start"))
    _start_session(access)
    return redirect(url_for("parent_portal.dashboard"))


@parent_portal_bp.post("/abmelden")
def logout():
    session.clear()
    return redirect(url_for("parent_portal.start"))


@parent_portal_bp.get("/uebersicht")
@parent_access_required
def dashboard(access):
    student = db.session.get(Schueler, access.schueler_id)
    booking = db.session.scalar(select(AppointmentBooking).where(
        AppointmentBooking.schueler_id == access.schueler_id,
        AppointmentBooking.status == "confirmed",
    ).order_by(AppointmentBooking.booked_at.desc()))
    registration = db.session.scalar(select(ParentRegistration).where(ParentRegistration.schueler_id == access.schueler_id))
    public_status = {"draft": "Formular in Bearbeitung", "submitted": "Formular eingereicht", "in_review": "Unterlagen werden geprüft", "completed": "Verfahren abgeschlossen"}
    appointment = active_booking_for_student(access.schueler_id)
    return render_template("parent_portal/dashboard.html", student=student, booking=booking,
                           appointment=appointment, slot_label=slot_label,
                           registration=registration, public_status=public_status)


@parent_portal_bp.get("/termin.ics")
@parent_access_required
def appointment_calendar(access):
    """Den eigenen Termin als Kalenderdatei.

    Der Termin wird über die Sitzung ermittelt, nicht über eine ID aus der
    Adresse -- so kann niemand die Buchung einer anderen Familie abrufen.
    """
    appointment = active_booking_for_student(access.schueler_id)
    if appointment is None:
        flash("Es ist noch kein Termin gebucht.")
        return redirect(url_for("parent_portal.dashboard"))
    booking, slot, event = appointment
    student = db.session.get(Schueler, access.schueler_id)
    payload = calendar.build_calendar([calendar.parent_entry(
        booking, slot, event, student,
        school_name=current_app.config.get("SCHOOL_NAME", ""),
        instructions=event.parent_instructions or "",
    )], name="Schulanmeldung")
    return send_file(BytesIO(payload), mimetype="text/calendar",
                     as_attachment=True, download_name="Anmeldetermin.ics")


@parent_portal_bp.get("/formular")
@parent_access_required
def registration(access):
    """Zum offenen Schritt springen; ein leeres Formular beginnt vorne."""
    form = _registration_draft(access)
    db.session.commit()
    return redirect(url_for("parent_portal.registration_step",
                            step=registration_form.resume_step(form.data or {})))


@parent_portal_bp.route("/formular/<step>", methods=["GET", "POST"])
@parent_access_required
def registration_step(access, step):
    """Ein Abschnitt des Anmeldeformulars.

    Jeder Schritt speichert für sich, damit Eltern jederzeit aufhören und
    später weitermachen können. Pflichtangaben werden erst beim Absenden
    verlangt -- vorher würde die Prüfung das Zwischenspeichern verhindern.
    """
    current = registration_form.step_by_key(step)
    if current is None:
        return redirect(url_for("parent_portal.registration"))
    student = db.session.get(Schueler, access.schueler_id)
    form = _registration_draft(access)
    data = dict(form.data or {})
    gaps = []

    if request.method == "POST":
        action = request.form.get("action", "next")
        if action != "skip":
            for field in current.fields:
                data[field.name] = request.form.get(field.name, "").strip()
        form.data = data
        form.version = (form.version or 0) + 1
        submitted = False
        if action == "submit":
            gaps = registration_form.missing(data)
            if not gaps:
                form.status = "submitted"
                form.submitted_at = utcnow()
                submitted = True
        record("registration_submitted" if submitted else "registration_saved",
               "parent_registration", form.id, actor_type="parent", actor_id=access.id)
        db.session.commit()
        if action == "save":
            flash("Ihre Angaben sind gespeichert. Sie können jederzeit über Ihren "
                  "Zugangslink weitermachen.")
            return redirect(url_for("parent_portal.dashboard"))
        if action in ("next", "skip"):
            following = registration_form.neighbours(step)[1]
            return redirect(url_for("parent_portal.registration_step", step=following.key)
                            if following else url_for("parent_portal.dashboard"))
        if submitted:
            flash("Das Anmeldeformular wurde übermittelt.")
            return redirect(url_for("parent_portal.dashboard"))
        flash("Bitte ergänzen Sie noch die fehlenden Pflichtangaben.")

    previous, following = registration_form.neighbours(step)
    return render_template(
        "parent_portal/registration.html", student=student, form=form, step=current,
        steps=registration_form.STEPS, number=registration_form.step_number(step),
        previous=previous, following=following, data=data, gaps=gaps,
        summary=registration_form.summary(data) if current is registration_form.SUMMARY_STEP else None,
        prefill=_registration_prefill(access, student),
    )


def _registration_draft(access):
    form = db.session.scalar(select(ParentRegistration).where(
        ParentRegistration.schueler_id == access.schueler_id))
    if not form:
        form = ParentRegistration(schueler_id=access.schueler_id,
                                  created_by_access_id=access.id, data={})
        db.session.add(form)
        db.session.flush()
    return form


def _registration_prefill(access, student):
    """Seed an empty form from the login and the municipal master data.

    Only used where the form has no value yet, so a parent's own entry is never
    overwritten -- both guardians edit the same form for a child.
    """
    return {key: value for key, value in {
        "kind_nachname": student.nachname,
        "kind_vorname": student.vorname,
        # <input type="date"> erwartet ISO, sonst bleibt das Feld leer.
        "kind_geburtsdatum": student.geburtsdatum.isoformat() if student.geburtsdatum else "",
        "kind_strasse": student.strasse or "",
        "kind_plz": student.plz or "",
        "kind_ort": student.ort or "",
        "sorgeberechtigt_1_name": access.display_name or "",
        "sorgeberechtigt_1_email": access.email_normalized or "",
        "sorgeberechtigt_1_strasse": student.strasse or "",
        "sorgeberechtigt_1_plz": student.plz or "",
        "sorgeberechtigt_1_ort": student.ort or "",
    }.items() if value}


@parent_portal_bp.get("/termine")
@parent_access_required
def appointments(access):
    now = utcnow().replace(tzinfo=None)
    # Exactly one event is open for booking at a time; picking the newest
    # keeps slots of an older school year out of the parent's list.
    event = db.session.scalar(
        select(AppointmentEvent)
        .where(AppointmentEvent.status == "published")
        .order_by(AppointmentEvent.school_year.desc(), AppointmentEvent.id.desc())
        .limit(1)
    )
    if event is None:
        return render_template("parent_portal/appointments.html", slots=[], event=None,
                               slot_label=slot_label)
    booked_count = func.count(AppointmentBooking.id).filter(AppointmentBooking.status == "confirmed")
    rows = db.session.execute(
        select(AppointmentSlot, booked_count.label("booked_count"))
        .outerjoin(AppointmentBooking, AppointmentBooking.slot_id == AppointmentSlot.id)
        .where(
            AppointmentSlot.event_id == event.id,
            AppointmentSlot.status == "available",
            AppointmentSlot.starts_at > now,
        )
        .group_by(AppointmentSlot.id).order_by(AppointmentSlot.starts_at)
    ).all()
    slots = [(slot, count) for slot, count in rows if count < slot.capacity]
    return render_template("parent_portal/appointments.html", slots=slots, event=event,
                           slot_label=slot_label)


@parent_portal_bp.post("/termine/<int:slot_id>/buchen")
@parent_access_required
def book(access, slot_id):
    try:
        book_slot(slot_id, access.schueler_id, access.id)
        record("appointment_booked", "appointment_slot", slot_id, actor_type="parent", actor_id=access.id)
        db.session.commit()
        flash("Der Termin wurde verbindlich gebucht.")
    except BookingError as exc:
        db.session.rollback()
        flash(str(exc))
    return redirect(url_for("parent_portal.dashboard"))


@parent_portal_bp.post("/termine/<int:booking_id>/stornieren")
@parent_access_required
def cancel(access, booking_id):
    try:
        cancel_booking(booking_id, access.id)
        record("appointment_cancelled", "appointment_booking", booking_id, actor_type="parent", actor_id=access.id)
        db.session.commit()
        flash("Der Termin wurde storniert.")
    except BookingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("parent_portal.dashboard"))
