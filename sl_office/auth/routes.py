"""Routes for internal staff authentication with a mandatory second factor.

Password and second factor are two separate steps: a correct password only
parks the user id in the session; ``login_user`` runs after the code check, so
a stolen password alone never yields a usable session.
"""

import ipaddress
from functools import wraps

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from forms import ChangePasswordForm, FirstRunForm, LoginForm, TwoFactorForm, TwoFactorSetupForm
from models import User, db
from sl_office.audit import record
from sl_office.auth import two_factor
from sl_office import school_profile

auth_bp = Blueprint("auth", __name__)

#: User who passed the password step but still owes a second factor.
PENDING_USER_KEY = "pending_2fa_user"
#: Secret offered during enrolment, still unconfirmed.
PENDING_SECRET_KEY = "pending_2fa_secret"
#: Recovery codes shown exactly once, right after enrolment.
FRESH_CODES_KEY = "fresh_recovery_codes"


def _needs_setup():
    """Eine frische Installation hat noch kein Konto."""
    return (current_app.config.get("FIRST_RUN_SETUP")
            and db.session.scalar(db.select(User.id).limit(1)) is None)


def _from_this_computer():
    """Im lokalen Netz bereitgestellt, darf nur der Rechner selbst das erste Konto anlegen."""
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def install_first_run_redirect(app):
    """Solange es kein Konto gibt, führt jede Seite zur Einrichtung."""
    @app.before_request
    def _first_run():
        if request.endpoint in {"static", "lebenszeichen"}:
            return None
        if _needs_setup():
            if not _from_this_computer():
                return ("SL-Office ist noch nicht eingerichtet. Das erste Konto wird am Rechner "
                        "angelegt, auf dem SL-Office läuft.", 403,
                        {"Content-Type": "text/plain; charset=utf-8"})
            if request.endpoint != "auth.first_run":
                return redirect(url_for("auth.first_run"))
        return None


@auth_bp.route("/einrichtung", methods=["GET", "POST"])
def first_run():
    """Das erste Administrationskonto anlegen -- nur, solange es keines gibt."""
    if not _needs_setup():
        return redirect(url_for("auth.login"))
    form = FirstRunForm()
    if form.validate_on_submit():
        if form.new_password.data != form.confirm_password.data:
            flash("Die beiden Passwörter stimmen nicht überein.", "error")
        else:
            user = User(username=form.username.data.strip(), role="Administrator",
                        password_hash=generate_password_hash(form.new_password.data))
            db.session.add(user)
            db.session.flush()
            record("first_admin_created", "user", user.id, actor_type="system")
            db.session.commit()
            if current_app.config.get("TWO_FACTOR_REQUIRED", True):
                flash("Das Konto ist angelegt. Bitte melden Sie sich jetzt an; danach richten Sie "
                      "die Bestätigung per Authenticator-App ein.")
            else:
                flash("Das Konto ist angelegt. Bitte melden Sie sich jetzt an.")
            return redirect(url_for("auth.login"))
    return render_template("first_run.html", form=form)


def _issuer():
    return school_profile.get("SCHOOL_NAME") or "SL-Office"


def _lock_message(seconds):
    minutes = max(1, round(seconds / 60))
    return f"Zu viele Fehlversuche. Bitte in {minutes} Minute(n) erneut versuchen."


def _pending_user():
    user_id = session.get(PENDING_USER_KEY)
    return db.session.get(User, user_id) if user_id else None


def _pending_required(view):
    """Guard the second step: only reachable right after a correct password."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _pending_user()
        if user is None:
            flash("Bitte melden Sie sich erneut an.")
            return redirect(url_for("auth.login"))
        return view(user, *args, **kwargs)
    return wrapped


def _begin_pending(user):
    session.clear()
    session[PENDING_USER_KEY] = user.id


def _complete_login(user):
    secret_codes = session.pop(FRESH_CODES_KEY, None)
    session.pop(PENDING_USER_KEY, None)
    session.pop(PENDING_SECRET_KEY, None)
    two_factor.clear_failed_attempts(user)
    record("staff_login", "user", user.id, actor_type="staff", actor_id=user.id)
    db.session.commit()
    login_user(user)
    if secret_codes:
        session[FRESH_CODES_KEY] = secret_codes
        return redirect(url_for("auth.recovery_codes"))
    return redirect(url_for("index"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is not None:
            remaining = two_factor.lock_remaining_seconds(user)
            if remaining:
                flash(_lock_message(remaining))
                return render_template("login.html", form=form)
        if user and check_password_hash(user.password_hash, form.password.data):
            _begin_pending(user)
            if not current_app.config.get("TWO_FACTOR_REQUIRED", True):
                # Desktop-Fassung: nur dieser Rechner, kein zweiter Faktor.
                return _complete_login(user)
            if user.two_factor_active:
                return redirect(url_for("auth.login_two_factor"))
            flash("Bitte richten Sie zuerst die Zwei-Faktor-Anmeldung ein.")
            return redirect(url_for("auth.setup_two_factor"))
        if user is not None:
            two_factor.register_failed_attempt(user)
            record("staff_login_failed", "user", user.id, actor_type="staff",
                   actor_id=user.id, outcome="failure")
            db.session.commit()
        # Same wording either way, so the form never reveals which names exist.
        flash("Ungültiger Benutzername oder Passwort")
    return render_template("login.html", form=form)


@auth_bp.route("/login/bestaetigen", methods=["GET", "POST"])
@_pending_required
def login_two_factor(user):
    if not user.two_factor_active:
        return redirect(url_for("auth.setup_two_factor"))
    form = TwoFactorForm()
    if form.validate_on_submit():
        remaining = two_factor.lock_remaining_seconds(user)
        if remaining:
            flash(_lock_message(remaining))
            return render_template("login_two_factor.html", form=form, user=user)
        entry = form.code.data or ""
        if two_factor.verify_totp(user, entry):
            return _complete_login(user)
        if two_factor.consume_recovery_code(user, entry):
            flash("Notfallcode verwendet. Bitte richten Sie bei Gelegenheit neue Codes ein.")
            record("staff_recovery_code_used", "user", user.id, actor_type="staff", actor_id=user.id)
            return _complete_login(user)
        two_factor.register_failed_attempt(user)
        record("staff_2fa_failed", "user", user.id, actor_type="staff", actor_id=user.id, outcome="failure")
        db.session.commit()
        flash("Der Code ist nicht gültig.")
    return render_template("login_two_factor.html", form=form, user=user)


@auth_bp.route("/login/einrichten", methods=["GET", "POST"])
@_pending_required
def setup_two_factor(user):
    """First-login enrolment; also used to re-enrol after an admin reset."""
    if user.two_factor_active:
        return redirect(url_for("auth.login_two_factor"))
    secret = session.get(PENDING_SECRET_KEY)
    if not secret:
        secret = two_factor.generate_secret()
        session[PENDING_SECRET_KEY] = secret
    form = TwoFactorSetupForm()
    if form.validate_on_submit():
        user.totp_secret = secret
        if two_factor.verify_totp(user, form.code.data):
            user.totp_confirmed_at = two_factor.utcnow()
            codes = two_factor.generate_recovery_codes(user)
            record("staff_2fa_enrolled", "user", user.id, actor_type="staff", actor_id=user.id)
            db.session.commit()
            session[FRESH_CODES_KEY] = codes
            return _complete_login(user)
        # The secret was only assigned so the check could run; roll it back so
        # nothing unconfirmed reaches the database.
        db.session.rollback()
        flash("Der Code stimmt nicht. Bitte prüfen Sie die Uhrzeit des Geräts und versuchen Sie es erneut.")
    uri = two_factor.provisioning_uri(user.username, secret, _issuer())
    return render_template(
        "login_two_factor_setup.html", form=form, user=user, secret=secret,
        qr_svg=two_factor.qr_svg(uri),
    )


@auth_bp.get("/notfallcodes")
def recovery_codes():
    """Show freshly minted codes exactly once, then drop them from the session."""
    codes = session.pop(FRESH_CODES_KEY, None)
    if not codes:
        flash("Notfallcodes können nur direkt nach dem Erzeugen angezeigt werden.")
        return redirect(url_for("auth.security") if current_user.is_authenticated else url_for("auth.login"))
    return render_template("recovery_codes.html", codes=codes)


@auth_bp.route("/sicherheit", methods=["GET", "POST"])
@login_required
def security():
    """Self-service page: status of the second factor and new recovery codes."""
    if request.method == "POST" and request.form.get("action") == "new_codes":
        if not current_user.two_factor_active:
            flash("Die Zwei-Faktor-Anmeldung ist nicht eingerichtet.")
        else:
            codes = two_factor.generate_recovery_codes(current_user)
            record("staff_recovery_codes_regenerated", "user", current_user.id,
                   actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            session[FRESH_CODES_KEY] = codes
            return redirect(url_for("auth.recovery_codes"))
    return render_template(
        "security.html",
        remaining_codes=two_factor.unused_recovery_code_count(current_user),
    )


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not check_password_hash(current_user.password_hash, form.old_password.data):
            flash("Das aktuelle Passwort ist falsch.")
        elif form.new_password.data != form.confirm_password.data:
            flash("Die neuen Passwörter stimmen nicht überein.")
        else:
            current_user.password_hash = generate_password_hash(form.new_password.data)
            record("staff_password_changed", "user", current_user.id,
                   actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash("Ihr Passwort wurde erfolgreich geändert!")
            return redirect(url_for("index"))
    return render_template("change_password.html", form=form)
