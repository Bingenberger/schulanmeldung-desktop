"""User administration routes."""

import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from forms import SettingsForm, UserAddForm
from models import Einschulungsjahr, GlobalSettings, User, db
from sl_office.authorization import role_required
from sl_office.services.student_classification import recalculate_kann_kind
from sl_office.services.student_deletion import delete_all_students
from sl_office.parent_portal.models import ParentAccess, ParentRegistration
from sl_office.parent_portal.access_service import create_activation_grant
from sl_office.parent_portal import letterhead, letters, registration_form
from sl_office.auth import two_factor
from sl_office import school_year
from sl_office.admin import backup_service
from sl_office.audit import record
from models import Schueler

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.post("/users/<int:user_id>/2fa-zuruecksetzen")
@role_required(["Administrator"])
def reset_user_two_factor(user_id):
    """Clear a colleague's second factor after a lost or replaced device."""
    user = db.get_or_404(User, user_id)
    two_factor.reset_two_factor(user)
    two_factor.clear_failed_attempts(user)
    record("staff_2fa_reset", "user", user.id, actor_type="staff", actor_id=current_user.id)
    db.session.commit()
    flash(f"Zwei-Faktor-Anmeldung für {user.username} zurückgesetzt. "
          "Beim nächsten Login wird sie neu eingerichtet.")
    return redirect(url_for("admin.users"))


@admin_bp.get("/users")
@role_required(["Administrator"])
def users():
    return render_template("admin_users.html", users=User.query.all())


@admin_bp.route("/users/add", methods=["GET", "POST"])
@role_required(["Administrator"])
def add_user():
    form = UserAddForm()
    if form.validate_on_submit():
        new_user = User(
            username=form.username.data,
            password_hash=generate_password_hash(form.password.data),
            role=form.role.data,
        )
        try:
            db.session.add(new_user)
            db.session.commit()
            flash(f"Benutzer {new_user.username} wurde angelegt!")
            return redirect(url_for("admin.users"))
        except Exception:
            db.session.rollback()
            flash("Fehler beim Anlegen: Benutzername existiert evtl. schon.")
    return render_template("user_neu.html", form=form)


@admin_bp.route("/settings", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung"])
def settings():
    settings_record = GlobalSettings.query.first()
    if settings_record is None:
        settings_record = GlobalSettings(einschulungsjahr=2026)
        db.session.add(settings_record)
        db.session.commit()

    form = SettingsForm(obj=settings_record)
    if form.validate_on_submit():
        form.populate_obj(settings_record)
        settings_record.einschulungsjahr = int(form.einschulungsjahr.data)
        recalculate_kann_kind(settings=settings_record)
        db.session.commit()
        flash("Einstellungen gespeichert und Kann-Kinder neu berechnet.")
        return redirect(url_for("admin.settings"))
    return render_template("admin_settings.html", form=form)


# --- Einschulungsjahre ------------------------------------------------------

@admin_bp.route("/einschulungsjahre", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung"])
def school_years():
    if request.method == "POST":
        try:
            jahr = int(request.form["jahr"])
            if not 2020 <= jahr <= 2100:
                raise ValueError
        except (KeyError, ValueError):
            flash("Bitte ein gültiges Jahr zwischen 2020 und 2100 angeben.", "error")
            return redirect(url_for("admin.school_years"))

        with school_year.all_years_scope():
            if db.session.scalar(select(Einschulungsjahr).where(Einschulungsjahr.jahr == jahr)):
                flash(f"Das Einschulungsjahr {jahr} gibt es bereits.", "error")
                return redirect(url_for("admin.school_years"))
            previous = school_year.current_year_row()
            if previous is not None:
                previous.ist_aktuell = False
                # Closing the old year protects it from accidental edits; it
                # can be unlocked again at any time.
                previous.gesperrt = True
            db.session.add(Einschulungsjahr(jahr=jahr, ist_aktuell=True))
            settings_record = GlobalSettings.query.first()
            if settings_record is not None:
                settings_record.einschulungsjahr = jahr
            record("school_year_created", "einschulungsjahr", jahr,
                   actor_type="staff", actor_id=current_user.id)
            db.session.commit()
        school_year.set_active_year(jahr)
        flash(f"Einschulungsjahr {jahr} angelegt und aktiviert. "
              f"{'Jahrgang ' + str(previous.jahr) + ' ist jetzt schreibgeschützt.' if previous else ''}")
        return redirect(url_for("admin.school_years"))

    with school_year.all_years_scope():
        years = school_year.all_years()
        counts = dict(db.session.execute(
            select(Schueler.einschulungsjahr, func.count(Schueler.id))
            .group_by(Schueler.einschulungsjahr)
            .execution_options(**{school_year.UNSCOPED: True})
        ).all())
    return render_template(
        "admin_school_years.html", years=years, counts=counts,
        active=school_year.active_year(), current=school_year.current_year_row(),
    )


@admin_bp.post("/einschulungsjahre/<int:jahr>/wechseln")
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
def switch_year(jahr):
    with school_year.all_years_scope():
        row = db.session.scalar(select(Einschulungsjahr).where(Einschulungsjahr.jahr == jahr))
    if row is None:
        flash("Dieses Einschulungsjahr gibt es nicht.", "error")
    else:
        school_year.set_active_year(jahr)
        flash(f"Sie arbeiten jetzt im Einschulungsjahr {jahr}."
              + (" Der Jahrgang ist schreibgeschützt." if row.gesperrt else ""))
    return redirect(request.form.get("next") if (request.form.get("next") or "").startswith("/")
                    else url_for("index"))


@admin_bp.post("/einschulungsjahre/<int:jahr>/sperre")
@role_required(["Administrator", "Schulleitung"])
def unlock_year(jahr):
    with school_year.all_years_scope():
        row = db.session.scalar(select(Einschulungsjahr).where(Einschulungsjahr.jahr == jahr))
        if row is None:
            flash("Dieses Einschulungsjahr gibt es nicht.", "error")
        else:
            row.gesperrt = not row.gesperrt
            record("school_year_lock_changed", "einschulungsjahr", jahr,
                   actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash(f"Einschulungsjahr {jahr} ist jetzt "
                  + ("schreibgeschützt." if row.gesperrt else "wieder bearbeitbar."))
    return redirect(url_for("admin.school_years"))


# --- Datensicherung ---------------------------------------------------------

@admin_bp.route("/datensicherung", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung"])
def backups():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create":
                target = backup_service.create_backup(current_app)
                record("backup_created", "backup", None, actor_type="staff", actor_id=current_user.id)
                db.session.commit()
                flash(f"Sicherung „{target.name}“ wurde angelegt.")
            elif action == "delete":
                backup_service.delete_backup(current_app, request.form.get("name", ""))
                record("backup_deleted", "backup", None, actor_type="staff", actor_id=current_user.id)
                db.session.commit()
                flash("Die Sicherung wurde gelöscht.")
            else:
                flash("Unbekannte Aktion.", "error")
        except backup_service.BackupError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except OSError:
            db.session.rollback()
            current_app.logger.exception("Backup failed")
            flash("Die Sicherung konnte nicht angelegt werden — bitte Speicherplatz und Rechte prüfen.", "error")
        return redirect(url_for("admin.backups"))

    try:
        entries = backup_service.list_backups(current_app)
        error = None
    except (backup_service.BackupError, OSError) as exc:
        entries, error = [], str(exc)
    return render_template("admin_backups.html", backups=entries, error=error,
                           human_size=backup_service.human_size)


@admin_bp.get("/datensicherung/datenbank")
@role_required(["Administrator", "Schulleitung"])
def download_database():
    """Small, consistent copy of the database for taking off-site."""
    try:
        payload = backup_service.database_snapshot(current_app)
    except backup_service.BackupError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.backups"))
    record("backup_downloaded", "backup", None, actor_type="staff", actor_id=current_user.id)
    db.session.commit()
    return send_file(
        payload, as_attachment=True, mimetype="application/octet-stream",
        download_name=f"sl-office-datenbank_{datetime.date.today():%Y%m%d}.db",
    )


@admin_bp.post("/delete_all_schueler")
@role_required(["Administrator"])
def delete_all_students_route():
    try:
        count = delete_all_students(current_app.config["UPLOAD_FOLDER"])
        flash(f"Alle Schüler ({count}) und zugehörige Daten wurden erfolgreich gelöscht.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Mass deletion of students failed")
        flash("Die Schülerdaten konnten nicht vollständig gelöscht werden.", "error")
    return redirect(url_for("admin.settings"))


@admin_bp.get("/anmeldungen")
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
def registrations():
    status = request.args.get("status")
    query = db.session.query(ParentRegistration, Schueler).join(Schueler, Schueler.id == ParentRegistration.schueler_id)
    if status in {"draft", "submitted", "in_review", "completed"}:
        query = query.filter(ParentRegistration.status == status)
    entries = query.order_by(ParentRegistration.updated_at.desc()).all()
    return render_template("admin_registrations.html", entries=entries, selected_status=status)


@admin_bp.route("/anmeldungen/<int:registration_id>", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
def registration_detail(registration_id):
    registration = db.get_or_404(ParentRegistration, registration_id)
    student = db.get_or_404(Schueler, registration.schueler_id)
    if request.method == "POST":
        status = request.form.get("status")
        if status not in {"draft", "submitted", "in_review", "completed"}:
            flash("Ungültiger Bearbeitungsstatus.", "error")
        else:
            registration.status = status
            record("registration_status_changed", "parent_registration", registration.id, actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash("Bearbeitungsstatus gespeichert.")
            return redirect(url_for("admin.registration_detail", registration_id=registration.id))
    return render_template("admin_registration_detail.html", registration=registration,
                           student=student,
                           summary=registration_form.summary(registration.data or {}))


@admin_bp.route("/elternzugänge", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
def parent_accesses():
    generated_link = None
    if request.method == "POST":
        try:
            student_id = int(request.form["student_id"])
            purpose = request.form.get("purpose", "first_access")
            if purpose not in {"first_access", "second_access"}:
                raise ValueError
            grant, token = create_activation_grant(student_id, purpose, created_by_user_id=current_user.id)
            db.session.commit()
            record("activation_grant_created", "activation_grant", grant.id, actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            generated_link = url_for("parent_portal.activate", token=token, _external=True)
            flash("Brief-Link erzeugt. Er wird nur jetzt im Klartext angezeigt.")
        except (KeyError, ValueError):
            db.session.rollback()
            flash("Bitte ein gültiges Kind und einen gültigen Zugangstyp auswählen.", "error")
    students = Schueler.query.order_by(Schueler.nachname, Schueler.vorname).all()
    accesses = db.session.query(ParentAccess, Schueler).join(Schueler, Schueler.id == ParentAccess.schueler_id).order_by(ParentAccess.created_at.desc()).all()
    return render_template("admin_parent_accesses.html", students=students, accesses=accesses, generated_link=generated_link)


@admin_bp.route("/elternbriefe", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
def parent_letters():
    """Print invitation letters carrying two personal access links each."""
    students = Schueler.query.order_by(Schueler.nachname, Schueler.vorname).all()
    if request.method == "POST":
        selection = request.form.getlist("student_ids", type=int)
        chosen = [student for student in students if student.id in set(selection)]
        if not chosen:
            flash("Bitte mindestens ein Kind auswählen.", "error")
            return redirect(url_for("admin.parent_letters"))
        year = school_year.active_year()
        reissue = request.form.get("reissue") == "1"
        try:
            buffer, issued = letters.build_letters(
                chosen,
                letterhead.branding(current_app.config),
                lambda token: url_for("parent_portal.activate", token=token, _external=True),
                created_by_user_id=current_user.id,
                deadline=request.form.get("deadline", "").strip() or None,
                period=request.form.get("period", "").strip() or None,
                school_year=f"{year}/{year + 1}" if year else None,
                reissue=reissue,
            )
            record("parent_letters_reissued" if reissue else "parent_letters_created",
                   "schueler", None, actor_type="staff", actor_id=current_user.id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Parent letter generation failed")
            flash("Die Briefe konnten nicht erzeugt werden.", "error")
            return redirect(url_for("admin.parent_letters"))
        current_app.logger.info(
            "Parent letters created",
            extra={"children": len(chosen), "tokens": issued, "reissue": reissue})
        return send_file(
            buffer, as_attachment=True, mimetype="application/pdf",
            download_name=f"Elternschreiben_{datetime.date.today():%Y%m%d}.pdf",
        )

    pending = {student.id for student in letters.students_without_access(students)}
    redeemed = {student.id: letters.redeemed_purposes(student.id) for student in students}
    return render_template(
        "admin_parent_letters.html", students=students, pending=pending, redeemed=redeemed,
    )


@admin_bp.route("/elternbrief-text", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung"])
def parent_letter_text():
    """Wortlaut des Elternanschreibens bearbeiten, prüfen und ansehen."""
    text = letters.stored_text()
    problems = []
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "reset":
            text = letters.reset_text()
            record("parent_letter_text_reset", "elternbrief", None,
                   actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash("Der Brieftext entspricht wieder der Schulvorlage.")
            return redirect(url_for("admin.parent_letter_text"))

        text = {key: request.form.get(key, "") for key in ("titel", "text", "gruss")}
        problems = letters.check_text(text["titel"], text["text"], text["gruss"])
        if not problems and action == "preview":
            year = school_year.active_year()
            return send_file(
                letters.build_preview(
                    letterhead.branding(current_app.config), text,
                    deadline=request.form.get("deadline", "").strip() or None,
                    period=request.form.get("period", "").strip() or None,
                    school_year=f"{year}/{year + 1}" if year else None,
                ),
                mimetype="application/pdf", download_name="Vorschau_Elternbrief.pdf",
            )
        if not problems:
            letters.save_text(text["titel"], text["text"], text["gruss"], current_user.id)
            record("parent_letter_text_saved", "elternbrief", None,
                   actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash("Der Brieftext wurde gespeichert.")
            return redirect(url_for("admin.parent_letter_text"))

    return render_template(
        "admin_parent_letter_text.html", text=text, problems=problems,
        fields=letters.FIELDS, marker=letters.ACCESS_MARKER,
        is_default=text == letters.DEFAULT_TEXT,
    )


@admin_bp.post("/elternzugänge/<int:access_id>/<action>")
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
def parent_access_action(access_id, action):
    access = db.get_or_404(ParentAccess, access_id)
    if action not in {"lock", "revoke", "activate"}:
        flash("Ungültige Aktion.", "error")
    else:
        access.status = {"lock": "locked", "revoke": "revoked", "activate": "active"}[action]
        access.security_version += 1
        record("parent_access_" + action, "parent_access", access.id, actor_type="staff", actor_id=current_user.id)
        db.session.commit()
        flash("Elternzugang aktualisiert.")
    return redirect(url_for("admin.parent_accesses"))
