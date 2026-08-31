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
from sl_office.appointments.service import (
    ALLOWED_DURATIONS, GRID_MINUTES, MAX_CAPACITY, BookingError, assign_slot,
    cancel_booking_as_staff, create_slot, delete_slot, generate_slots, move_slot, set_capacity,
    slot_label,
)
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

    opens, closes = _to_local(event.booking_opens_at, tz), _to_local(event.booking_closes_at, tz)
    days = []
    if opens and closes:
        day = opens.date()
        while day <= closes.date():
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
    return render_template("appointments/detail.html", event=event, planner=_planner_state(event))


def _handle_settings_post(event, form):
    """Apply one of the settings forms on the planner page."""
    action = form.get("action")
    tz = ZoneInfo(event.timezone)
    if action == "event_settings":
        opens = _from_local(form["booking_opens_at"], tz)
        closes = _from_local(form["booking_closes_at"], tz)
        if closes <= opens:
            raise BookingError("Das Ende des Anmeldezeitraums muss nach dem Beginn liegen.")
        event.booking_opens_at, event.booking_closes_at = opens, closes
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
