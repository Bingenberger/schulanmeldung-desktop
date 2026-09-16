"""Token generation and access invariants for the parent portal."""

import datetime
import hashlib
import secrets

from sqlalchemy import select

from models import Schueler, db
from sl_office.parent_portal.models import ActivationGrant, ParentAccess, ParentLoginToken, utcnow

MAX_PARENT_ACCESSES_PER_STUDENT = 2


class ParentAccessLimitReached(ValueError):
    pass


class InvalidAccessToken(ValueError):
    pass


def normalize_email(email):
    return email.strip().casefold()


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_expired(expires_at, now):
    """SQLite may return timezone-aware columns as naive datetimes."""
    if expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return expires_at <= now


def create_activation_grant(student_id, purpose, created_by_user_id=None, lifetime_days=30):
    token = secrets.token_urlsafe(32)
    grant = ActivationGrant(
        schueler_id=student_id, token_hash=hash_token(token), purpose=purpose,
        created_by_user_id=created_by_user_id,
        expires_at=utcnow() + datetime.timedelta(days=lifetime_days),
    )
    db.session.add(grant)
    return grant, token


def activate_parent_access(student_id, email, display_name):
    normalized = normalize_email(email)
    display_name = display_name.strip()
    if not normalized or "@" not in normalized or len(normalized) > 320:
        raise ValueError("Bitte geben Sie eine gültige E-Mail-Adresse ein.")
    if not display_name:
        raise ValueError("Bitte geben Sie Ihren Namen ein.")
    # Serializes access creation per student on databases supporting row locks.
    if db.session.get(Schueler, student_id, with_for_update=True) is None:
        raise ValueError("Unbekannter Schülerdatensatz.")
    existing = db.session.scalar(select(ParentAccess).where(
        ParentAccess.schueler_id == student_id,
        ParentAccess.email_normalized == normalized,
    ))
    if existing:
        if existing.status == "revoked":
            existing.status = "pending"
            existing.display_name = display_name
            existing.security_version += 1
        return existing
    active_count = db.session.query(ParentAccess).filter(
        ParentAccess.schueler_id == student_id,
        ParentAccess.status.in_(("pending", "active", "locked")),
    ).count()
    if active_count >= MAX_PARENT_ACCESSES_PER_STUDENT:
        raise ParentAccessLimitReached("Für dieses Kind bestehen bereits zwei Elternzugänge.")
    access = ParentAccess(
        schueler_id=student_id, email_normalized=normalized,
        display_name=display_name, status="pending",
    )
    db.session.add(access)
    db.session.flush()
    return access


def consume_activation_grant(token, email, display_name):
    """Redeem a one-time letter token and create one of at most two accesses."""
    now = utcnow()
    grant = db.session.scalar(select(ActivationGrant).where(
        ActivationGrant.token_hash == hash_token(token),
    ).with_for_update())
    if not grant or grant.used_at or grant.revoked_at or _is_expired(grant.expires_at, now):
        raise InvalidAccessToken("Dieser Zugangslink ist ungültig oder nicht mehr gültig.")
    access = activate_parent_access(grant.schueler_id, email, display_name)
    access.status = "active"
    access.email_confirmed_at = now
    access.last_used_at = now
    grant.parent_access_id = access.id
    grant.used_at = now
    db.session.flush()
    return access


def create_login_token(parent_access_id, lifetime_minutes=15):
    """Create a short-lived magic login token; only its digest is persisted."""
    access = db.session.get(ParentAccess, parent_access_id)
    if not access or access.status != "active":
        raise ValueError("Der Elternzugang ist nicht aktiv.")
    token = secrets.token_urlsafe(32)
    login_token = ParentLoginToken(
        parent_access_id=parent_access_id, token_hash=hash_token(token), purpose="login",
        expires_at=utcnow() + datetime.timedelta(minutes=lifetime_minutes),
    )
    db.session.add(login_token)
    return login_token, token


def consume_login_token(token):
    now = utcnow()
    login_token = db.session.scalar(select(ParentLoginToken).where(
        ParentLoginToken.token_hash == hash_token(token),
        ParentLoginToken.purpose == "login",
    ).with_for_update())
    if not login_token or login_token.used_at or _is_expired(login_token.expires_at, now):
        raise InvalidAccessToken("Dieser Anmeldelink ist ungültig oder nicht mehr gültig.")
    access = db.session.get(ParentAccess, login_token.parent_access_id)
    if not access or access.status != "active":
        raise InvalidAccessToken("Dieser Elternzugang ist nicht aktiv.")
    login_token.used_at = now
    access.last_used_at = now
    return access


#: Wie lange nach einem verschickten Anmeldelink kein weiterer hinausgeht.
#: Eltern drücken den Knopf gern zweimal, wenn die Mail auf sich warten lässt --
#: das soll ihr Postfach nicht füllen und niemand soll es gezielt fluten können.
LOGIN_LINK_COOLDOWN_SECONDS = 120


def mask_email(email):
    """``anna.beispiel@example.de`` -> ``a***@example.de``.

    Genug, damit Eltern wissen, in welches Postfach sie schauen sollen; zu wenig,
    um die Adresse jemandem zu verraten, der nur den Brief in der Hand hält.
    """
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def login_link_recently_sent(parent_access_id, now=None):
    """True, wenn für diesen Zugang gerade erst ein Anmeldelink erzeugt wurde."""
    now = now or utcnow()
    latest = db.session.scalar(
        select(ParentLoginToken.created_at)
        .where(ParentLoginToken.parent_access_id == parent_access_id,
               ParentLoginToken.purpose == "login")
        .order_by(ParentLoginToken.created_at.desc())
        .limit(1))
    if latest is None:
        return False
    if latest.tzinfo is None:
        now = now.replace(tzinfo=None)
    return (now - latest).total_seconds() < LOGIN_LINK_COOLDOWN_SECONDS


def letter_link_state(token):
    """Was ein erneut geöffneter Brieflink bedeutet.

    Liefert ``(zustand, zugang)``:

    ``"offen"``          noch nicht eingelöst -- das Aktivierungsformular
    ``"eingerichtet"``   bereits eingelöst, der Zugang ist aktiv
    ``"ungueltig"``      unbekannt, abgelaufen, widerrufen oder Zugang gesperrt

    Der Brieflink bleibt dabei, was er ist: ein Einmalschlüssel. Nach der
    Einlösung öffnet er nichts mehr selbst, er führt nur noch zum Anmeldelink an
    die hinterlegte Adresse -- wer bloß den Brief findet, kommt so nicht hinein.
    """
    grant = db.session.scalar(select(ActivationGrant).where(
        ActivationGrant.token_hash == hash_token(token)))
    if grant is None or grant.revoked_at:
        return "ungueltig", None
    if grant.used_at is None:
        if _is_expired(grant.expires_at, utcnow()):
            return "ungueltig", None
        return "offen", None
    access = db.session.get(ParentAccess, grant.parent_access_id) if grant.parent_access_id else None
    if access is None or access.status != "active":
        return "ungueltig", None
    return "eingerichtet", access
