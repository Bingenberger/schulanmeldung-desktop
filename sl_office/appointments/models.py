"""Termine für das Anmeldegespräch: Veranstaltung, Gesprächsfenster, Buchung."""

from models import db, utcnow


class AppointmentEvent(db.Model):
    __tablename__ = "appointment_event"
    __table_args__ = (db.CheckConstraint("status IN ('draft','published','closed','cancelled')", name="ck_appointment_event_status"),)
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    school_year = db.Column(db.Integer, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="draft")
    #: An welchen Tagen Gesprächsfenster angeboten werden. Reine Datumsangaben
    #: in der Zeitzone der Veranstaltung; ohne Angabe gilt keine Einschränkung.
    slot_days_from = db.Column(db.Date)
    slot_days_until = db.Column(db.Date)
    timezone = db.Column(db.String(64), nullable=False, default="Europe/Berlin")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class AppointmentSlot(db.Model):
    """One interview window. Parallel interviews are expressed as capacity,
    not as separate resources. Windows may overlap; only an exact duplicate
    period is refused, since that belongs in the capacity instead."""

    __tablename__ = "appointment_slot"
    __table_args__ = (
        # Nur unter den lebenden Fenstern ist ein Zeitraum einmalig. Ein
        # stillgelegtes darf seine Uhrzeit nicht blockieren -- sonst ließe sich
        # ein versehentlich gelöschtes Fenster nicht wieder neu anlegen.
        db.Index("uq_slot_event_period_active", "event_id", "starts_at", "ends_at",
                 unique=True,
                 sqlite_where=db.text("status != 'cancelled'"),
                 postgresql_where=db.text("status <> 'cancelled'")),
        db.CheckConstraint("capacity > 0", name="ck_appointment_slot_capacity"),
        db.CheckConstraint("ends_at > starts_at", name="ck_appointment_slot_period"),
        db.CheckConstraint("status IN ('available','blocked','cancelled')", name="ck_appointment_slot_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("appointment_event.id", ondelete="CASCADE"), nullable=False, index=True)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    capacity = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="available")
    location = db.Column(db.String(200))


class AppointmentBooking(db.Model):
    __tablename__ = "appointment_booking"
    __table_args__ = (
        db.CheckConstraint("status IN ('confirmed','cancelled','attended','no_show')", name="ck_appointment_booking_status"),
        db.CheckConstraint("source IN ('parent','staff')", name="ck_appointment_booking_source"),
    )
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("appointment_event.id", ondelete="CASCADE"), nullable=False, index=True)
    slot_id = db.Column(db.Integer, db.ForeignKey("appointment_slot.id", ondelete="RESTRICT"), nullable=False, index=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey("schueler.id", ondelete="CASCADE"), nullable=False, index=True)
    #: Herkunft der Buchung. Heute vergibt immer die Schule ("staff");
    #: "parent" stammt aus der Zeit mit Elternportal.
    source = db.Column(db.String(10), nullable=False, default="staff")
    status = db.Column(db.String(20), nullable=False, default="confirmed", index=True)
    booked_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    cancelled_at = db.Column(db.DateTime(timezone=True))
    internal_note = db.Column(db.Text)
