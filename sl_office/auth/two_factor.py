"""Second factor (TOTP) and brute-force protection for staff logins.

Codes are verified against the standard 30-second TOTP window with one step of
leeway for clock drift. Every accepted step is recorded so a code that was
observed in transit cannot be replayed while it is still nominally valid.
"""

import datetime
import secrets
import time

import pyotp
from reportlab.graphics.barcode import qr
from werkzeug.security import check_password_hash, generate_password_hash

from models import RecoveryCode, db

TOTP_INTERVAL = 30
#: One step before and after, i.e. up to ~30 s of clock drift either way.
TOTP_LEEWAY_STEPS = 1
RECOVERY_CODE_COUNT = 8
#: Free attempts before the account starts locking.
MAX_FREE_ATTEMPTS = 4
#: Lockout grows 1, 2, 4, 8 ... minutes and stops here.
MAX_LOCK_MINUTES = 60


def utcnow():
    return datetime.datetime.now(datetime.UTC)


def _naive(value):
    """Compare consistently: SQLite hands back tz-naive datetimes."""
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(datetime.UTC).replace(tzinfo=None)


# --- enrolment -------------------------------------------------------------

def generate_secret():
    return pyotp.random_base32()


def provisioning_uri(username, secret, issuer):
    """otpauth:// URI for the authenticator app."""
    return pyotp.TOTP(secret, interval=TOTP_INTERVAL).provisioning_uri(name=username, issuer_name=issuer)


def qr_svg(payload, module_px=4, quiet_zone=2):
    """Inline SVG for a QR code, built from reportlab's encoder.

    Returned as markup rather than an image file so no extra route, static
    asset or Content-Security-Policy exception is needed.
    """
    widget = qr.QrCodeWidget(payload)
    widget.qr.make()
    matrix = widget.qr.modules
    count = len(matrix)
    size = (count + 2 * quiet_zone) * module_px
    rects = []
    for row, line in enumerate(matrix):
        for column, filled in enumerate(line):
            if filled:
                x = (column + quiet_zone) * module_px
                y = (row + quiet_zone) * module_px
                rects.append(f'<rect x="{x}" y="{y}" width="{module_px}" height="{module_px}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" role="img" aria-label="QR-Code zur Einrichtung">'
        f'<rect width="{size}" height="{size}" fill="#fff"/>'
        f'<g fill="#000">{"".join(rects)}</g></svg>'
    )


# --- verification ----------------------------------------------------------

def _current_step(at=None):
    return int((at if at is not None else time.time()) // TOTP_INTERVAL)


def verify_totp(user, code, at=None):
    """Check a TOTP code and burn its time step so it cannot be reused."""
    code = (code or "").strip().replace(" ", "")
    if not user.totp_secret or not code.isdigit() or len(code) != 6:
        return False
    totp = pyotp.TOTP(user.totp_secret, interval=TOTP_INTERVAL)
    now_step = _current_step(at)
    for offset in range(-TOTP_LEEWAY_STEPS, TOTP_LEEWAY_STEPS + 1):
        step = now_step + offset
        if not secrets.compare_digest(totp.at(step * TOTP_INTERVAL), code):
            continue
        if user.totp_last_counter is not None and step <= user.totp_last_counter:
            return False  # already used: replay
        user.totp_last_counter = step
        return True
    return False


def generate_recovery_codes(user, count=RECOVERY_CODE_COUNT):
    """Replace the user's recovery codes; the plaintext is returned only here."""
    # Go through the relationship rather than the foreign key, so the loaded
    # collection matches the database within this request too.
    user.recovery_codes.clear()
    codes = []
    for _ in range(count):
        raw = f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}"
        codes.append(raw)
        user.recovery_codes.append(RecoveryCode(code_hash=generate_password_hash(raw)))
    db.session.flush()
    return codes


def consume_recovery_code(user, code):
    candidate = (code or "").strip().lower()
    if not candidate:
        return False
    for entry in user.recovery_codes:
        if entry.used_at is None and check_password_hash(entry.code_hash, candidate):
            entry.used_at = utcnow()
            db.session.flush()
            return True
    return False


def unused_recovery_code_count(user):
    return sum(1 for entry in user.recovery_codes if entry.used_at is None)


# --- brute-force protection ------------------------------------------------

def lock_remaining_seconds(user, at=None):
    """Seconds the account stays locked, or 0 when it is usable."""
    if user.locked_until is None:
        return 0
    now = _naive(at or utcnow())
    remaining = (_naive(user.locked_until) - now).total_seconds()
    return int(remaining) if remaining > 0 else 0


def register_failed_attempt(user):
    """Count a wrong password or code and lock with growing back-off."""
    user.failed_logins = (user.failed_logins or 0) + 1
    over = user.failed_logins - MAX_FREE_ATTEMPTS
    if over > 0:
        minutes = min(2 ** (over - 1), MAX_LOCK_MINUTES)
        user.locked_until = utcnow() + datetime.timedelta(minutes=minutes)
    db.session.flush()
    return lock_remaining_seconds(user)


def clear_failed_attempts(user):
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    db.session.flush()


def reset_two_factor(user):
    """Drop the second factor so the user enrols again on the next login."""
    user.totp_secret = None
    user.totp_confirmed_at = None
    user.totp_last_counter = None
    for entry in list(user.recovery_codes):
        db.session.delete(entry)
    db.session.flush()
