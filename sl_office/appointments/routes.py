"""Internal appointment administration: planning board and booking overview."""

import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from flask import (Blueprint, current_app, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from models import db, Schueler
from sl_office.authorization import role_required
from sl_office.appointments import calendar
from sl_office.appointments import admin_protocol_pdf, protocol_pdf
from sl_office.appointments.service import (
    ALLOWED_DURATIONS, GRID_MINUTES, MAX_CAPACITY, OHNE_TERMIN_GRUENDE, BookingError,
    assign_slot, assignable_slots, active_booking_for_student,
    students_without_appointment,
    cancel_booking_as_staff, create_slot, delete_slot, generate_slots, local_date, move_slot,
    set_capacity, slot_label,
)
from sl_office.parent_portal.letterhead import branding
from sl_office.parent_portal.models import (
    AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentAccess,
)

appointments_bp = Blueprint("appointments", __name__, url_prefix="/admin/appointments")
MANAGE_ROLES = ["Administrator", "Schulleitung", "Sekretariat"]

#: Vertical extent of the planning board, widened when slots fall outside.
DEFAULT_DAY_START = datetime.time(7, 0)
DEFAULT_DAY_END = datetime.time(18, 0)


def _to_local(value, tz):
    """Render a stored (tz-naive UTC) timestamp in the event's timezone."""
    if value is None:
        return None
    aware = value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value
    return aware.astimezone(tz)


def _from_local(value, tz):
    """Parse a local ``YYYY-MM-DDTHH:MM`` string into tz-naive UTC."""
    local = datetime.datetime.fromisoformat(value).replace(tzinfo=tz)
    return local.astimezone(datetime.UTC).replace(tzinfo=None)


def _booking_index(event_id):
    """Map slot_id -> list of confirmed bookings with the child's name."""
    rows = db.session.execute(
        select(AppointmentBooking, Schueler)
        .join(Schueler, Schueler.id == AppointmentBooking.schueler_id)
        .where(AppointmentBooking.event_id == event_id, AppointmentBooking.status == "confirmed")
    ).all()
    index = {}
    for booking, student in rows:
        index.setdefault(booking.slot_id, []).append({
            "booking_id": booking.id,
            "name": f"{student.nachname}, {student.vorname}",
            "source": booking.source,
        })
    return index


def _planner_state(event):
    """Everything the planning board needs: the day columns and the slots."""
    tz = ZoneInfo(event.timezone)
    slots = AppointmentSlot.query.filter_by(event_id=event.id).filter(
        AppointmentSlot.status != "cancelled"
    ).order_by(AppointmentSlot.starts_at).all()
    bookings = _booking_index(event.id)

    # Die Spalten des Planers sind die Gesprächstage, nicht der Anmeldezeitraum:
    # gebucht wird Wochen vorher, gesprochen an wenigen Tagen.
    days = []
    first, last = event.slot_days_from, event.slot_days_until
    if first and last:
        day = first
        while day <= last:
            if day.weekday() < 5:
                days.append(day)
            day += datetime.timedelta(days=1)

    payload, day_start, day_end = [], DEFAULT_DAY_START, DEFAULT_DAY_END
    for slot in slots:
        starts, ends = _to_local(slot.starts_at, tz), _to_local(slot.ends_at, tz)
        booked = bookings.get(slot.id, [])
        day_start = min(day_start, starts.time())
        day_end = max(day_end, ends.time())
        # A slot outside the booking window still has to be visible, otherwise
        # it could never be corrected.
        if starts.date() not in days:
            days.append(starts.date())
        payload.append({
            "id": slot.id,
            "day": starts.date().isoformat(),
            "start": starts.strftime("%H:%M"),
            "end": ends.strftime("%H:%M"),
            "local_start": starts.strftime("%Y-%m-%dT%H:%M"),
            "duration": int((ends - starts).total_seconds() // 60),
            "capacity": slot.capacity,
            "booked": len(booked),
            "students": booked,
            "locked": bool(booked),
        })
    days.sort()
    return {
        "days": [{"date": day.isoformat(), "label": day.strftime("%a %d.%m.")} for day in days],
        "slots": payload,
        "day_start": day_start.strftime("%H:%M"),
        "day_end": day_end.strftime("%H:%M"),
        "grid_minutes": GRID_MINUTES,
        "durations": list(ALLOWED_DURATIONS),
        "max_capacity": MAX_CAPACITY,
    }


@appointments_bp.route("/", methods=["GET", "POST"])
@role_required(MANAGE_ROLES)
def index():
    if request.method == "POST":
        try:
            title = request.form["title"].strip()
            school_year = int(request.form["school_year"])
            if not title or not 2020 <= school_year <= 2100:
                raise ValueError
            event = AppointmentEvent(title=title, school_year=school_year, status="draft")
            db.session.add(event)
            db.session.commit()
            flash("Terminveranstaltung wurde angelegt.")
            return redirect(url_for("appointments.detail", event_id=event.id))
        except (KeyError, ValueError):
            db.session.rollback()
            flash("Bitte Titel und gültiges Einschulungsjahr angeben.", "error")
    events = AppointmentEvent.query.order_by(
        AppointmentEvent.school_year.desc(), AppointmentEvent.id.desc()
    ).all()
    return render_template("appointments/index.html", events=events)


@appointments_bp.route("/<int:event_id>", methods=["GET", "POST"])
@role_required(MANAGE_ROLES)
def detail(event_id):
    event = db.get_or_404(AppointmentEvent, event_id)
    if request.method == "POST":
        try:
            _handle_settings_post(event, request.form)
            db.session.commit()
            flash("Die Terminverwaltung wurde aktualisiert.")
            return redirect(url_for("appointments.detail", event_id=event.id))
        except BookingError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except (KeyError, ValueError, IntegrityError):
            db.session.rollback()
            flash("Die Eingabe ist unvollständig oder ungültig.", "error")
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Appointment administration failed", extra={"event_id": event.id})
            flash("Die Änderung konnte nicht gespeichert werden.", "error")
    # Das Eingabefeld ist ein <input type="datetime-local">, erwartet also
    # Ortszeit. Gespeichert wird UTC -- ohne diese Umrechnung zeigte die Maske
    # den Zeitraum um den Zeitzonenversatz verschoben an.
    tz = ZoneInfo(event.timezone)
    window = {
        name: (_to_local(getattr(event, name), tz).strftime("%Y-%m-%dT%H:%M")
               if getattr(event, name) else "")
        for name in ("booking_opens_at", "booking_closes_at")
    }
    window.update({
        name: (getattr(event, name).isoformat() if getattr(event, name) else "")
        for name in ("slot_days_from", "slot_days_until")
    })
    return render_template("appointments/detail.html", event=event, window=window,
                           planner=_planner_state(event))


def _assert_slots_still_fit(event, first, last):
    """Gesprächstage nicht so beschneiden, dass angelegte Fenster herausfallen.

    Sonst stünden Fenster außerhalb des eigenen Zeitraums -- gebuchte noch dazu,
    die sich nicht mehr verschieben lassen.
    """
    slots = AppointmentSlot.query.filter_by(event_id=event.id).filter(
        AppointmentSlot.status != "cancelled").all()
    stranded = sorted({local_date(slot.starts_at, event) for slot in slots
                       if not first <= local_date(slot.starts_at, event) <= last})
    if stranded:
        gelistet = ", ".join(day.strftime("%d.%m.%Y") for day in stranded[:5])
        raise BookingError(
            "Außerhalb des gewählten Zeitraums liegen bereits Gesprächsfenster "
            f"({gelistet}). Verschiebe oder lösche sie zuerst.")


def _handle_settings_post(event, form):
    """Apply one of the settings forms on the planner page."""
    action = form.get("action")
    tz = ZoneInfo(event.timezone)
    if action == "booking_window":
        opens = _from_local(form["booking_opens_at"], tz)
        closes = _from_local(form["booking_closes_at"], tz)
        if closes <= opens:
            raise BookingError("Das Ende des Anmeldezeitraums muss nach dem Beginn liegen.")
        event.booking_opens_at, event.booking_closes_at = opens, closes
    elif action == "slot_days":
        first = datetime.date.fromisoformat(form["slot_days_from"])
        last = datetime.date.fromisoformat(form["slot_days_until"])
        if last < first:
            raise BookingError("Der letzte Gesprächstag darf nicht vor dem ersten liegen.")
        _assert_slots_still_fit(event, first, last)
        event.slot_days_from, event.slot_days_until = first, last
    elif action == "status":
        status = form["status"]
        if status not in {"draft", "published", "closed", "cancelled"}:
            raise ValueError
        event.status = status
    elif action == "bulk_slots":
        generate_slots(
            event.id,
            _from_local(form["first_start"], tz),
            int(form["count"]),
            int(form["duration_minutes"]),
            int(form.get("gap_minutes", 0)),
            int(form.get("capacity", 1)),
        )
    else:
        raise ValueError


# --- Planner API -----------------------------------------------------------
# The board talks JSON so dragging never reloads the page.

def _json_action(event_id, handler):
    """Run a planner mutation and return the refreshed board state."""
    event = db.get_or_404(AppointmentEvent, event_id)
    try:
        handler(event, request.get_json(force=True) or {})
        db.session.commit()
        return jsonify(ok=True, planner=_planner_state(event))
    except BookingError as exc:
        db.session.rollback()
        return jsonify(ok=False, error=str(exc)), 400
    except (KeyError, TypeError, ValueError):
        db.session.rollback()
        return jsonify(ok=False, error="Ungültige Anfrage."), 400
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Planner action failed", extra={"event_id": event_id})
        return jsonify(ok=False, error="Die Änderung konnte nicht gespeichert werden."), 500


@appointments_bp.get("/<int:event_id>/planner")
@role_required(MANAGE_ROLES)
def planner_state(event_id):
    event = db.get_or_404(AppointmentEvent, event_id)
    return jsonify(ok=True, planner=_planner_state(event))


@appointments_bp.post("/<int:event_id>/slots")
@role_required(MANAGE_ROLES)
def create(event_id):
    def handler(event, payload):
        tz = ZoneInfo(event.timezone)
        starts_at = _from_local(payload["local_start"], tz)
        duration = int(payload.get("duration", ALLOWED_DURATIONS[0]))
        create_slot(
            event.id, starts_at, starts_at + datetime.timedelta(minutes=duration),
            int(payload.get("capacity", 1)),
        )
    return _json_action(event_id, handler)


@appointments_bp.post("/<int:event_id>/slots/<int:slot_id>/move")
@role_required(MANAGE_ROLES)
def move(event_id, slot_id):
    def handler(event, payload):
        _owned_slot(event, slot_id)
        tz = ZoneInfo(event.timezone)
        starts_at = _from_local(payload["local_start"], tz)
        duration = int(payload.get("duration") or 0)
        if not duration:
            slot = db.session.get(AppointmentSlot, slot_id)
            duration = int((slot.ends_at - slot.starts_at).total_seconds() // 60)
        move_slot(slot_id, starts_at, starts_at + datetime.timedelta(minutes=duration))
    return _json_action(event_id, handler)


@appointments_bp.post("/<int:event_id>/slots/<int:slot_id>/capacity")
@role_required(MANAGE_ROLES)
def capacity(event_id, slot_id):
    def handler(event, payload):
        _owned_slot(event, slot_id)
        set_capacity(slot_id, int(payload["capacity"]))
    return _json_action(event_id, handler)


@appointments_bp.post("/<int:event_id>/slots/<int:slot_id>/delete")
@role_required(MANAGE_ROLES)
def remove(event_id, slot_id):
    def handler(event, payload):
        _owned_slot(event, slot_id)
        delete_slot(slot_id)
    return _json_action(event_id, handler)


def _safe_next(candidate, fallback):
    """Only follow same-site relative targets, never an attacker's absolute URL."""
    if candidate and candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return fallback


def _owned_slot(event, slot_id):
    slot = db.session.get(AppointmentSlot, slot_id)
    if slot is None or slot.event_id != event.id:
        raise BookingError("Zeitfenster gehört nicht zu dieser Veranstaltung.")
    return slot


@appointments_bp.get("/<int:event_id>/buchungen")
@role_required(MANAGE_ROLES)
def bookings(event_id):
    event = db.get_or_404(AppointmentEvent, event_id)
    rows = db.session.execute(
        select(AppointmentBooking, AppointmentSlot, Schueler, ParentAccess)
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .join(Schueler, Schueler.id == AppointmentBooking.schueler_id)
        .outerjoin(ParentAccess, ParentAccess.id == AppointmentBooking.parent_access_id)
        .where(AppointmentBooking.event_id == event.id)
        .order_by(AppointmentSlot.starts_at)
    ).all()
    return render_template("appointments/bookings.html", event=event, rows=rows,
                           slot_label=slot_label)


@appointments_bp.get("/<int:event_id>/ohne-termin")
@role_required(MANAGE_ROLES)
def without_appointment(event_id):
    """Kinder, für die weder selbst gebucht noch ein Termin vergeben wurde.

    Nach dem Grund gefiltert, weil jeder Grund etwas anderes verlangt: an eine
    Familie mit Zugang geht eine Erinnerung, bei einer ohne Zugang hilft nur
    ein Anruf oder ein vergebener Termin.
    """
    event = db.get_or_404(AppointmentEvent, event_id)
    alle = students_without_appointment()
    filter_ = request.args.get("filter")
    zeilen = [zeile for zeile in alle if zeile["grund"] == filter_] \
        if filter_ in OHNE_TERMIN_GRUENDE else alle
    anzahl = {grund: sum(1 for zeile in alle if zeile["grund"] == grund)
              for grund in OHNE_TERMIN_GRUENDE}
    freie = [{"id": slot.id, "label": slot_label(slot, event), "frei": slot.capacity - belegt}
             for slot, belegt in assignable_slots(event.id)]
    adressen = sorted({access.email_normalized for zeile in zeilen for access in zeile["eltern"]})
    return render_template(
        "appointments/without_appointment.html", event=event, zeilen=zeilen, gesamt=len(alle),
        anzahl=anzahl, gruende=OHNE_TERMIN_GRUENDE, filter_=filter_, freie=freie,
        adressen=adressen, hier=request.full_path.rstrip("?"))


@appointments_bp.get("/<int:event_id>/protokolle.pdf")
@role_required(MANAGE_ROLES)
def protocols(event_id):
    """Die Protokolle des Anmeldespiels für alle Kinder, nach Termin sortiert.

    Kinder ohne Termin stehen am Ende: Auch sie brauchen einen Bogen, wenn sie
    kurzfristig erscheinen, einsortieren lassen sie sich aber nicht.
    """
    event = db.get_or_404(AppointmentEvent, event_id)
    eintraege = protocol_pdf.fuer_veranstaltung(event)
    if not eintraege:
        flash("Für diesen Jahrgang sind noch keine Kinder erfasst.")
        return redirect(url_for("appointments.detail", event_id=event.id))
    payload = protocol_pdf.build_many(
        eintraege, title=f"Protokolle Anmeldespiel ({len(eintraege)})")
    return send_file(BytesIO(payload), mimetype="application/pdf", as_attachment=True,
                     download_name=f"Protokolle_Anmeldespiel_{len(eintraege)}.pdf")


@appointments_bp.get("/<int:event_id>/verwaltungsprotokolle.pdf")
@role_required(MANAGE_ROLES)
def admin_protocols(event_id):
    """Die Laufzettel der Verwaltungsanmeldung, in derselben Reihenfolge."""
    event = db.get_or_404(AppointmentEvent, event_id)
    eintraege = protocol_pdf.fuer_veranstaltung(event)
    if not eintraege:
        flash("Für diesen Jahrgang sind noch keine Kinder erfasst.")
        return redirect(url_for("appointments.detail", event_id=event.id))
    payload = admin_protocol_pdf.build_many(
        eintraege, branding(current_app.config),
        title=f"Verwaltungsanmeldung ({len(eintraege)})")
    return send_file(BytesIO(payload), mimetype="application/pdf", as_attachment=True,
                     download_name=f"Verwaltungsanmeldung_{len(eintraege)}.pdf")


@appointments_bp.get("/schueler/<int:student_id>/verwaltungsprotokoll.pdf")
@role_required(MANAGE_ROLES)
def admin_protocol(student_id):
    """Der Laufzettel eines einzelnen Kindes, zur Ansicht im Browser."""
    student = db.get_or_404(Schueler, student_id)
    payload = admin_protocol_pdf.build(student, branding(current_app.config),
                                       active_booking_for_student(student.id))
    name = f"{student.nachname}_{student.vorname}".replace(" ", "-")
    return send_file(BytesIO(payload), mimetype="application/pdf", as_attachment=False,
                     download_name=f"Verwaltungsanmeldung_{name}.pdf")


@appointments_bp.get("/schueler/<int:student_id>/protokoll.pdf")
@role_required(MANAGE_ROLES)
def protocol(student_id):
    """Der Protokollbogen eines einzelnen Kindes, zur Ansicht im Browser."""
    student = db.get_or_404(Schueler, student_id)
    payload = protocol_pdf.build(student, active_booking_for_student(student.id))
    name = f"{student.nachname}_{student.vorname}".replace(" ", "-")
    return send_file(BytesIO(payload), mimetype="application/pdf", as_attachment=False,
                     download_name=f"Protokoll_{name}.pdf")


@appointments_bp.get("/<int:event_id>/buchungen.ics")
@role_required(MANAGE_ROLES)
def bookings_calendar(event_id):
    """Alle bestätigten Termine einer Veranstaltung als Kalenderdatei."""
    event = db.get_or_404(AppointmentEvent, event_id)
    rows = db.session.execute(
        select(AppointmentBooking, AppointmentSlot, Schueler)
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .join(Schueler, Schueler.id == AppointmentBooking.schueler_id)
        .where(AppointmentBooking.event_id == event.id,
               AppointmentBooking.status == "confirmed")
        .order_by(AppointmentSlot.starts_at)
    ).all()
    school = current_app.config.get("SCHOOL_NAME", "")
    payload = calendar.build_calendar(
        [calendar.staff_entry(booking, slot, event, student, school_name=school)
         for booking, slot, student in rows],
        name=event.title,
    )
    return send_file(BytesIO(payload), mimetype="text/calendar", as_attachment=True,
                     download_name=f"Anmeldetermine_{event.school_year}.ics")


@appointments_bp.post("/buchung/<int:booking_id>/stornieren")
@role_required(MANAGE_ROLES)
def cancel(booking_id):
    booking = db.get_or_404(AppointmentBooking, booking_id)
    target = _safe_next(
        request.form.get("next"), url_for("appointments.detail", event_id=booking.event_id)
    )
    try:
        cancel_booking_as_staff(booking_id)
        db.session.commit()
        flash("Der Termin wurde storniert.")
    except BookingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(target)


@appointments_bp.post("/schueler/<int:student_id>/zuweisen")
@role_required(MANAGE_ROLES)
def assign(student_id):
    student = db.get_or_404(Schueler, student_id)
    target = _safe_next(
        request.form.get("next"), url_for("students.detail", student_id=student.id)
    )
    try:
        assign_slot(int(request.form["slot_id"]), student.id)
        db.session.commit()
        flash(f"Termin für {student.vorname} {student.nachname} wurde vergeben.")
    except BookingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except (KeyError, ValueError):
        db.session.rollback()
        flash("Bitte ein Zeitfenster auswählen.", "error")
    return redirect(target)
