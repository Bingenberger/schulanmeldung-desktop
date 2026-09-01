"""Transactional appointment rules for the school registration interview.

There is exactly one kind of appointment, so slots carry no resource: parallel
interviews are expressed through ``capacity``. Windows may overlap freely --
the school runs staggered interviews of different lengths -- so the only
period rule left is that an exactly identical window is refused.
"""

import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from models import db
from sl_office.parent_portal.models import (
    AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentAccess, utcnow,
)

#: Slots start on a ten minute grid; the planner snaps to the same raster.
GRID_MINUTES = 10
#: The two interview lengths the school actually uses.
ALLOWED_DURATIONS = (40, 50)
MAX_CAPACITY = 8


class BookingError(ValueError):
    pass


class SlotUnavailable(BookingError):
    pass


class StudentAlreadyBooked(BookingError):
    pass


class SlotHasBookings(BookingError):
    """Raised when a slot may not change because appointments hang on it."""


def naive_utc(value):
    """Return ``value`` as a tz-naive UTC datetime.

    Slots are stored tz-naive in UTC (SQLite drops tzinfo), so every comparison
    has to happen in one representation. Normalising on the way in keeps the
    tz-juggling out of the rules below.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


def _validate_period(starts_at, ends_at):
    if ends_at <= starts_at:
        raise BookingError("Das Ende muss nach dem Beginn liegen.")
    if starts_at.minute % GRID_MINUTES or starts_at.second or starts_at.microsecond:
        raise BookingError(f"Der Beginn muss auf einem {GRID_MINUTES}-Minuten-Raster liegen.")
    duration = int((ends_at - starts_at).total_seconds() // 60)
    if duration not in ALLOWED_DURATIONS:
        allowed = " oder ".join(str(value) for value in ALLOWED_DURATIONS)
        raise BookingError(f"Ein Gesprächsfenster dauert {allowed} Minuten.")


def local_date(value, event):
    """Der Kalendertag eines gespeicherten Zeitpunkts in der Zeitzone der Veranstaltung.

    Ein Fenster um 23:30 UTC gehört in Ortszeit schon zum Folgetag; ohne die
    Umrechnung fiele es beim Vergleich mit den Gesprächstagen auf den falschen.
    """
    aware = value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value
    return aware.astimezone(ZoneInfo(event.timezone)).date()


def _validate_within_event(event, starts_at, ends_at):
    """Ein Gesprächsfenster muss auf einen der Gesprächstage fallen.

    Der Anmeldezeitraum spielt hier bewusst keine Rolle: gebucht wird lange
    vor den Gesprächen, die Fenster liegen also regelmäßig außerhalb.
    """
    first, last = event.slot_days_from, event.slot_days_until
    if not first and not last:
        return
    for moment in (starts_at, ends_at):
        day = local_date(moment, event)
        if (first and day < first) or (last and day > last):
            raise BookingError(
                "Das Zeitfenster muss auf einen der Gesprächstage fallen "
                f"({_date_range_label(first, last)}).")


def _date_range_label(first, last):
    if first and last:
        return f"{first.strftime('%d.%m.%Y')} bis {last.strftime('%d.%m.%Y')}"
    if first:
        return f"ab {first.strftime('%d.%m.%Y')}"
    return f"bis {last.strftime('%d.%m.%Y')}"


def _assert_not_duplicate(event_id, starts_at, ends_at, exclude_slot_id=None):
    """Windows may overlap freely, but an identical one is a mis-click.

    Two interviews at the same time are expressed through capacity, so a second
    window with exactly the same period carries no information.
    """
    conditions = [
        AppointmentSlot.event_id == event_id,
        AppointmentSlot.status != "cancelled",
        AppointmentSlot.starts_at == starts_at,
        AppointmentSlot.ends_at == ends_at,
    ]
    if exclude_slot_id is not None:
        conditions.append(AppointmentSlot.id != exclude_slot_id)
    if db.session.scalar(select(AppointmentSlot.id).where(*conditions).limit(1)) is not None:
        raise BookingError(
            "Ein Fenster mit genau diesem Zeitraum gibt es schon. "
            "Erhöhe dort die Platzzahl, statt es zu verdoppeln."
        )


def confirmed_booking_count(slot_id):
    return db.session.scalar(select(func.count(AppointmentBooking.id)).where(
        AppointmentBooking.slot_id == slot_id,
        AppointmentBooking.status == "confirmed",
    )) or 0


def create_slot(event_id, starts_at, ends_at, capacity=1, location=None):
    starts_at, ends_at = naive_utc(starts_at), naive_utc(ends_at)
    _validate_period(starts_at, ends_at)
    if not 1 <= capacity <= MAX_CAPACITY:
        raise BookingError(f"Die Platzzahl muss zwischen 1 und {MAX_CAPACITY} liegen.")
    event = db.session.get(AppointmentEvent, event_id)
    if event is None or event.status == "cancelled":
        raise BookingError("Terminveranstaltung nicht verfügbar.")
    _validate_within_event(event, starts_at, ends_at)
    _assert_not_duplicate(event_id, starts_at, ends_at)
    slot = AppointmentSlot(
        event_id=event_id, starts_at=starts_at, ends_at=ends_at,
        capacity=capacity, location=location,
    )
    db.session.add(slot)
    db.session.flush()
    return slot


def generate_slots(event_id, first_start, count, duration_minutes, gap_minutes=0, capacity=1):
    """Generate a run of consecutive slots; each one passes the normal rules."""
    if not 1 <= count <= 500:
        raise BookingError("Die Anzahl muss zwischen 1 und 500 liegen.")
    if duration_minutes not in ALLOWED_DURATIONS:
        allowed = " oder ".join(str(value) for value in ALLOWED_DURATIONS)
        raise BookingError(f"Ein Gesprächsfenster dauert {allowed} Minuten.")
    if gap_minutes < 0 or gap_minutes % GRID_MINUTES:
        raise BookingError(f"Die Pause muss ein Vielfaches von {GRID_MINUTES} Minuten sein.")
    created = []
    cursor = naive_utc(first_start)
    for _ in range(count):
        end = cursor + datetime.timedelta(minutes=duration_minutes)
        created.append(create_slot(event_id, cursor, end, capacity))
        cursor = end + datetime.timedelta(minutes=gap_minutes)
    return created


def move_slot(slot_id, starts_at, ends_at):
    """Move a slot in time. Slots with appointments on them are locked."""
    slot = db.session.scalar(select(AppointmentSlot).where(AppointmentSlot.id == slot_id).with_for_update())
    if slot is None or slot.status == "cancelled":
        raise BookingError("Zeitfenster nicht gefunden oder bereits gelöscht.")
    if confirmed_booking_count(slot.id):
        raise SlotHasBookings(
            "Dieses Fenster ist bereits vergeben und kann nicht verschoben werden. "
            "Storniere den Termin zuerst."
        )
    starts_at, ends_at = naive_utc(starts_at), naive_utc(ends_at)
    _validate_period(starts_at, ends_at)
    event = db.session.get(AppointmentEvent, slot.event_id)
    _validate_within_event(event, starts_at, ends_at)
    _assert_not_duplicate(slot.event_id, starts_at, ends_at, exclude_slot_id=slot.id)
    slot.starts_at, slot.ends_at = starts_at, ends_at
    db.session.flush()
    return slot


def set_capacity(slot_id, capacity):
    slot = db.session.scalar(select(AppointmentSlot).where(AppointmentSlot.id == slot_id).with_for_update())
    if slot is None or slot.status == "cancelled":
        raise BookingError("Zeitfenster nicht gefunden oder bereits gelöscht.")
    if not 1 <= capacity <= MAX_CAPACITY:
        raise BookingError(f"Die Platzzahl muss zwischen 1 und {MAX_CAPACITY} liegen.")
    booked = confirmed_booking_count(slot.id)
    if capacity < booked:
        raise SlotHasBookings(f"Es sind bereits {booked} Termine vergeben.")
    slot.capacity = capacity
    db.session.flush()
    return slot


def delete_slot(slot_id):
    """Remove a slot; one that carries history is retired instead of dropped.

    An einem Fenster können stornierte Buchungen hängen -- aus einem früheren
    Versuch oder von einem inzwischen gelöschten Kind. Die zeigen weiter auf das
    Fenster, dessen Fremdschlüssel mit ``ON DELETE RESTRICT`` genau das schützt:
    ein echtes Löschen scheiterte an der Datenbank. Solche Fenster werden darum
    auf ``cancelled`` gesetzt. Für den Planer und die Eltern sind sie damit weg
    -- beide zeigen nur nicht stornierte Fenster --, die alten Buchungen
    behalten aber ihren Bezug.
    """
    slot = db.session.scalar(select(AppointmentSlot).where(AppointmentSlot.id == slot_id).with_for_update())
    if slot is None:
        raise BookingError("Zeitfenster nicht gefunden.")
    if confirmed_booking_count(slot_id):
        raise SlotHasBookings(
            "Dieses Fenster ist bereits vergeben. Storniere den Termin zuerst."
        )
    if db.session.scalar(select(AppointmentBooking.id).where(
            AppointmentBooking.slot_id == slot_id).limit(1)) is not None:
        slot.status = "cancelled"
    else:
        db.session.delete(slot)
    db.session.flush()


def _load_bookable_slot(slot_id, require_published):
    slot = db.session.scalar(select(AppointmentSlot).where(AppointmentSlot.id == slot_id).with_for_update())
    if slot is None or slot.status != "available":
        raise SlotUnavailable("Dieses Zeitfenster ist nicht verfügbar.")
    event = db.session.get(AppointmentEvent, slot.event_id)
    if event is None or event.status == "cancelled":
        raise SlotUnavailable("Die Terminvergabe ist nicht möglich.")
    if require_published and event.status != "published":
        raise SlotUnavailable("Die Terminbuchung ist nicht geöffnet.")
    return slot, event


def _assert_capacity_left(slot):
    if confirmed_booking_count(slot.id) >= slot.capacity:
        raise SlotUnavailable("Dieses Zeitfenster ist bereits ausgebucht.")


def _existing_booking(event_id, student_id):
    return db.session.scalar(select(AppointmentBooking).where(
        AppointmentBooking.event_id == event_id,
        AppointmentBooking.schueler_id == student_id,
        AppointmentBooking.status == "confirmed",
    ))


def book_slot(slot_id, student_id, parent_access_id):
    """Book a slot from the parent portal while holding its row lock."""
    slot, event = _load_bookable_slot(slot_id, require_published=True)
    now = naive_utc(utcnow())
    if event.booking_opens_at and now < naive_utc(event.booking_opens_at):
        raise SlotUnavailable("Die Terminbuchung ist noch nicht geöffnet.")
    if event.booking_closes_at and now > naive_utc(event.booking_closes_at):
        raise SlotUnavailable("Die Terminbuchung ist bereits geschlossen.")

    access = db.session.get(ParentAccess, parent_access_id)
    if access is None or access.schueler_id != student_id or access.status != "active":
        raise BookingError("Der Elternzugang ist für dieses Kind nicht berechtigt.")

    existing = _existing_booking(event.id, student_id)
    if existing:
        if existing.slot_id == slot.id:
            return existing
        raise StudentAlreadyBooked("Für dieses Kind besteht bereits ein Termin.")
    _assert_capacity_left(slot)

    booking = AppointmentBooking(
        event_id=event.id, slot_id=slot.id, schueler_id=student_id,
        parent_access_id=parent_access_id, source="parent", status="confirmed",
    )
    db.session.add(booking)
    db.session.flush()
    return booking


def assign_slot(slot_id, student_id):
    """Assign a slot to a child from the staff side, without a parent access.

    Used for families who do not book themselves. Unlike parent booking this
    works while the event is still a draft, since planning happens before the
    booking period opens.
    """
    slot, event = _load_bookable_slot(slot_id, require_published=False)
    existing = _existing_booking(event.id, student_id)
    if existing:
        if existing.slot_id == slot.id:
            return existing
        raise StudentAlreadyBooked("Für dieses Kind besteht bereits ein Termin.")
    _assert_capacity_left(slot)
    booking = AppointmentBooking(
        event_id=event.id, slot_id=slot.id, schueler_id=student_id,
        parent_access_id=None, source="staff", status="confirmed",
    )
    db.session.add(booking)
    db.session.flush()
    return booking


class AssignedByStaff(BookingError):
    """Ein von der Schule vergebener Termin; die Eltern lösen ihn nicht auf."""


#: Was die Eltern zu hören bekommen, wenn sie einen vorgegebenen Termin
#: stornieren wollen. Auch die Oberfläche zeigt diesen Satz, statt den
#: Knopf überhaupt anzubieten.
ASSIGNED_NOTICE = ("Diesen Termin hat die Schule für Sie vorgesehen. Wenn er Ihnen nicht "
                   "möglich ist, wenden Sie sich bitte an die Schule.")


def cancel_booking(booking_id, parent_access_id):
    """Cancel from the parent portal, honouring the cancellation deadline.

    Berechtigt ist, wer einen aktiven Zugang zu diesem Kind hat -- nicht nur,
    wer die Buchung selbst angelegt hat. Sonst könnte die zweite
    sorgeberechtigte Person den gemeinsamen Termin nicht auflösen.
    """
    booking = db.session.scalar(
        select(AppointmentBooking).where(AppointmentBooking.id == booking_id).with_for_update()
    )
    access = db.session.get(ParentAccess, parent_access_id)
    if booking is None or access is None or booking.schueler_id != access.schueler_id:
        raise BookingError("Terminbuchung nicht gefunden.")
    if booking.status != "confirmed":
        return booking
    if booking.source == "staff":
        # Die Schule hat den Termin geplant; ein stiller Rückzug daraus würde
        # ihr eine Lücke hinterlassen, von der sie nichts erfährt.
        raise AssignedByStaff(ASSIGNED_NOTICE)
    slot = db.session.get(AppointmentSlot, booking.slot_id)
    event = db.session.get(AppointmentEvent, booking.event_id)
    deadline = naive_utc(slot.starts_at) - datetime.timedelta(hours=event.cancellation_deadline_hours)
    if naive_utc(utcnow()) > deadline:
        raise BookingError("Die Stornierungsfrist ist abgelaufen.")
    booking.status = "cancelled"
    booking.cancelled_at = utcnow()
    db.session.flush()
    return booking


#: strftime("%a") is locale dependent, so weekday names are spelled out here.
_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def slot_label(slot, event):
    """Human readable slot description in the event's timezone."""
    tz = ZoneInfo(event.timezone)
    starts = slot.starts_at.replace(tzinfo=datetime.UTC) if slot.starts_at.tzinfo is None else slot.starts_at
    ends = slot.ends_at.replace(tzinfo=datetime.UTC) if slot.ends_at.tzinfo is None else slot.ends_at
    starts, ends = starts.astimezone(tz), ends.astimezone(tz)
    return (
        f"{_WEEKDAYS[starts.weekday()]} {starts.strftime('%d.%m.%Y')}, "
        f"{starts.strftime('%H:%M')}–{ends.strftime('%H:%M')} Uhr"
    )


def moment_label(value, event):
    """Ein einzelner Zeitpunkt in der Zeitzone der Veranstaltung."""
    tz = ZoneInfo(event.timezone)
    aware = value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value
    local = aware.astimezone(tz)
    return (f"{_WEEKDAYS[local.weekday()]} {local.strftime('%d.%m.%Y')}, "
            f"{local.strftime('%H:%M')} Uhr")


def active_booking_for_student(student_id):
    """The child's confirmed appointment with its slot and event, or None."""
    return db.session.execute(
        select(AppointmentBooking, AppointmentSlot, AppointmentEvent)
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .join(AppointmentEvent, AppointmentEvent.id == AppointmentBooking.event_id)
        .where(
            AppointmentBooking.schueler_id == student_id,
            AppointmentBooking.status == "confirmed",
        )
        .order_by(AppointmentSlot.starts_at)
    ).first()


def assignable_slots(event_id):
    """Slots of an event that still have a free place, earliest first."""
    taken = (
        select(AppointmentBooking.slot_id, func.count(AppointmentBooking.id).label("taken"))
        .where(AppointmentBooking.status == "confirmed")
        .group_by(AppointmentBooking.slot_id)
        .subquery()
    )
    rows = db.session.execute(
        select(AppointmentSlot, func.coalesce(taken.c.taken, 0))
        .outerjoin(taken, taken.c.slot_id == AppointmentSlot.id)
        .where(AppointmentSlot.event_id == event_id, AppointmentSlot.status == "available")
        .order_by(AppointmentSlot.starts_at)
    ).all()
    return [(slot, count) for slot, count in rows if count < slot.capacity]


def planning_event():
    """The event staff are currently working on: newest non-cancelled one."""
    return db.session.scalar(
        select(AppointmentEvent)
        .where(AppointmentEvent.status != "cancelled")
        .order_by(AppointmentEvent.school_year.desc(), AppointmentEvent.id.desc())
        .limit(1)
    )


def cancel_booking_as_staff(booking_id):
    """Cancel from the school side; no deadline applies and no access is needed."""
    booking = db.session.scalar(
        select(AppointmentBooking).where(AppointmentBooking.id == booking_id).with_for_update()
    )
    if booking is None:
        raise BookingError("Terminbuchung nicht gefunden.")
    if booking.status != "confirmed":
        return booking
    booking.status = "cancelled"
    booking.cancelled_at = utcnow()
    db.session.flush()
    return booking
