"""Persistence models for passwordless parent access and appointments."""

import datetime
from sqlalchemy.types import JSON

from models import db


def utcnow():
    return datetime.datetime.now(datetime.UTC)


class ParentAccess(db.Model):
    __tablename__ = "parent_access"
    __table_args__ = (
        db.UniqueConstraint("schueler_id", "email_normalized", name="uq_parent_access_student_email"),
        db.CheckConstraint("status IN ('pending','active','locked','revoked')", name="ck_parent_access_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey("schueler.id", ondelete="CASCADE"), nullable=False, index=True)
    email_normalized = db.Column(db.String(320), nullable=False)
    display_name = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    email_confirmed_at = db.Column(db.DateTime(timezone=True))
    last_used_at = db.Column(db.DateTime(timezone=True))
    security_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class ActivationGrant(db.Model):
    __tablename__ = "activation_grant"
    __table_args__ = (
        db.CheckConstraint("purpose IN ('first_access','second_access')", name="ck_activation_grant_purpose"),
    )
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey("schueler.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_access_id = db.Column(db.Integer, db.ForeignKey("parent_access.id", ondelete="SET NULL"))
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    purpose = db.Column(db.String(20), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True))
    revoked_at = db.Column(db.DateTime(timezone=True))
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class Elternbrief(db.Model):
    """Redaktionell gepflegter Text des Elternanschreibens.

    Ohne Zeile gilt der Wortlaut der Schulvorlage aus
    :data:`sl_office.parent_portal.letters.DEFAULT_TEXT`; "Zurücksetzen"
    loescht die Zeile wieder. ``key`` laesst Platz fuer weitere Schreiben.
    """
    __tablename__ = "elternbrief"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), nullable=False, unique=True)
    titel = db.Column(db.Text, nullable=False)
    text = db.Column(db.Text, nullable=False)
    gruss = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow,
                           onupdate=utcnow)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))


class ParentLoginToken(db.Model):
    __tablename__ = "parent_login_token"
    __table_args__ = (db.CheckConstraint("purpose IN ('login','confirm_email')", name="ck_parent_login_token_purpose"),)
    id = db.Column(db.Integer, primary_key=True)
    parent_access_id = db.Column(db.Integer, db.ForeignKey("parent_access.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    purpose = db.Column(db.String(20), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class ParentRegistration(db.Model):
    """Versioned payload of the digital school registration form."""
    __tablename__ = "parent_registration"
    __table_args__ = (db.CheckConstraint("status IN ('draft','submitted','in_review','completed')", name="ck_parent_registration_status"),)
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey("schueler.id", ondelete="CASCADE"), nullable=False, unique=True)
    created_by_access_id = db.Column(db.Integer, db.ForeignKey("parent_access.id", ondelete="SET NULL"))
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    data = db.Column(JSON, nullable=False, default=dict)
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Erste Abgabe -- bleibt stehen, auch wenn danach noch geändert wird. Für
    #: die Anmeldefrist zählt dieser Zeitpunkt, nicht die letzte Bearbeitung.
    submitted_at = db.Column(db.DateTime(timezone=True))
    submitted_by_access_id = db.Column(db.Integer, db.ForeignKey("parent_access.id", ondelete="SET NULL"))
    #: Gesetzt, sobald die Schule über eine Änderung nach der Abgabe informiert
    #: wurde. Greift die Schule den Vorgang wieder auf, wird der Wert geleert
    #: und die nächste Änderung meldet sich erneut -- sonst käme bei jedem
    #: gespeicherten Schritt eine Mail.
    change_notified_at = db.Column(db.DateTime(timezone=True))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AuditEvent(db.Model):
    """Minimal immutable audit record; never store tokens or form payloads here."""
    __tablename__ = "audit_event"
    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    actor_type = db.Column(db.String(20), nullable=False)
    actor_id = db.Column(db.Integer)
    action = db.Column(db.String(80), nullable=False, index=True)
    object_type = db.Column(db.String(80), nullable=False)
    object_id = db.Column(db.Integer)
    outcome = db.Column(db.String(20), nullable=False, default="success")
    request_id = db.Column(db.String(64))


class AppointmentEvent(db.Model):
    __tablename__ = "appointment_event"
    __table_args__ = (db.CheckConstraint("status IN ('draft','published','closed','cancelled')", name="ck_appointment_event_status"),)
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    school_year = db.Column(db.Integer, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="draft")
    #: Wann Eltern buchen dürfen. Liegt in aller Regel *vor* den Gesprächstagen.
    booking_opens_at = db.Column(db.DateTime(timezone=True))
    booking_closes_at = db.Column(db.DateTime(timezone=True))
    #: An welchen Tagen Gesprächsfenster angeboten werden. Reine Datumsangaben
    #: in der Zeitzone der Veranstaltung; ohne Angabe gilt keine Einschränkung.
    slot_days_from = db.Column(db.Date)
    slot_days_until = db.Column(db.Date)
    cancellation_deadline_hours = db.Column(db.Integer, nullable=False, default=24)
    timezone = db.Column(db.String(64), nullable=False, default="Europe/Berlin")
    parent_instructions = db.Column(db.Text)
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
    parent_access_id = db.Column(db.Integer, db.ForeignKey("parent_access.id", ondelete="SET NULL"))
    # "staff" marks an appointment the school assigned; parent_access_id alone
    # cannot express that, because its FK nulls out when an access is deleted.
    source = db.Column(db.String(10), nullable=False, default="parent")
    status = db.Column(db.String(20), nullable=False, default="confirmed", index=True)
    booked_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    cancelled_at = db.Column(db.DateTime(timezone=True))
    internal_note = db.Column(db.Text)
    #: Zeitpunkt der verschickten Erinnerung. Gesetzt heißt "erledigt", damit
    #: ein zweiter Lauf des Erinnerungsdienstes nicht erneut zustellt.
    reminder_sent_at = db.Column(db.DateTime(timezone=True))
