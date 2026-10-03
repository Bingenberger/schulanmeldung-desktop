"""User administration routes."""

import datetime
from io import BytesIO

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, logout_user
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import generate_password_hash

from forms import MIN_PASSWORD_LENGTH, PasswordResetForm, SettingsForm, UserAddForm
from models import Einschulungsjahr, GlobalSettings, User, db
from sl_office.authorization import role_required
from sl_office import features
from sl_office.features import parent_portal_enabled, portal_required
from sl_office import school_profile, vorlagen
from sl_office.services.student_classification import recalculate_kann_kind
from sl_office.services.student_deletion import delete_all_students
from sl_office.parent_portal.models import ParentAccess, ParentRegistration
from sl_office.parent_portal.access_service import create_activation_grant
from sl_office.parent_portal import letterhead, letters, registration_form, registration_pdf
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
    if current_app.config.get("TWO_FACTOR_REQUIRED", True):
        flash(f"Zwei-Faktor-Anmeldung für {user.username} zurückgesetzt. "
              "Beim nächsten Login wird sie neu eingerichtet.")
    else:
        flash(f"Die Sperre für {user.username} ist aufgehoben.")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/passwort", methods=["GET", "POST"])
@role_required(["Administrator"])
def reset_user_password(user_id):
    """Ein neues Passwort für eine Kollegin oder einen Kollegen setzen.

    Das alte Passwort wird nicht abgefragt -- wer es noch wüsste, bräuchte
    diese Seite nicht. Der zweite Faktor bleibt unangetastet: er ist ein
    eigener Nachweis und hat mit einem vergessenen Passwort nichts zu tun.
    """
    user = db.get_or_404(User, user_id)
    form = PasswordResetForm()
    if form.validate_on_submit():
        if form.new_password.data != form.confirm_password.data:
            flash("Die beiden Passwörter stimmen nicht überein.", "error")
        else:
            user.password_hash = generate_password_hash(form.new_password.data)
            # Wer ausgesperrt war, soll sich mit dem neuen Passwort sofort
            # anmelden können und nicht erst die Sperrfrist absitzen.
            two_factor.clear_failed_attempts(user)
            record("staff_password_reset", "user", user.id,
                   actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash(f"Das Passwort für {user.username} wurde neu gesetzt. Bitte geben Sie es "
                  "der Person persönlich weiter.")
            return redirect(url_for("admin.users"))
    return render_template("admin_user_password.html", form=form, benutzer=user,
                           min_length=MIN_PASSWORD_LENGTH)


@admin_bp.post("/users/<int:user_id>/loeschen")
@role_required(["Administrator"])
def delete_user(user_id):
    """Einen Zugang entfernen.

    Der eigene Zugang bleibt versperrt -- und damit auch der letzte
    Administratorzugang: hierher kommt nur die Administration, und wer hier
    steht, ist selbst Administrator. Es bleibt also immer mindestens dieser
    eine übrig, ohne dass es dafür eine eigene Prüfung bräuchte.

    Geführte AO-SF- und Rückstellungsverfahren bleiben erhalten und verlieren
    nur ihre Zuordnung; die Übersicht nennt ihre Zahl in der Rückfrage.
    """
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("Den eigenen Zugang können Sie nicht löschen.", "error")
        return redirect(url_for("admin.users"))
    name = user.username
    try:
        db.session.delete(user)
        record("staff_account_deleted", "user", user_id,
               actor_type="staff", actor_id=current_user.id)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("User deletion failed", extra={"user_id": user_id})
        flash("Der Zugang konnte nicht gelöscht werden.", "error")
        return redirect(url_for("admin.users"))
    flash(f"Der Zugang {name} wurde gelöscht.")
    return redirect(url_for("admin.users"))


@admin_bp.get("/users")
@role_required(["Administrator"])
def users():
    people = User.query.order_by(User.username).all()
    # Verfahren, die an einer Person hängen: beim Löschen verlieren sie ihre
    # Zuordnung, bleiben aber bestehen. Das gehört in die Rückfrage.
    led = {
        person.id: len(person.aosf_faelle) + len(person.rueckstellung_faelle)
        for person in people
    }
    return render_template("admin_users.html", users=people, led=led)


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
        # Der Stichtag haengt am Einschulungsjahr des Kindes, nicht an dieser
        # Einstellung -- der Lauf raeumt also nur Abweichungen im gerade
        # geoeffneten Jahrgang auf, statt ihn umzuetikettieren.
        geprueft = recalculate_kann_kind(settings=settings_record)
        db.session.commit()
        flash(f"Einstellungen gespeichert. {geprueft} Kinder des geöffneten "
              "Jahrgangs wurden auf ihren Kann-Kind-Status geprüft.")
        return redirect(url_for("admin.settings"))
    return render_template("admin_settings.html", form=form)


# --- Schulprofil --------------------------------------------------------------

@admin_bp.route("/schulprofil", methods=["GET", "POST"], endpoint="school_profile")
@role_required(["Administrator", "Schulleitung"])
def school_profile_page():
    """Name, Anschrift, Logo und Unterschrift der Schule für Briefkopf und Schreiben."""
    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            if action == "save":
                school_profile.save_texts(request.form)
                for key in school_profile.IMAGE_KEYS:
                    upload = request.files.get(key)
                    if upload and upload.filename:
                        school_profile.save_image(key, upload.read())
            elif action.startswith("remove:"):
                school_profile.remove_image(action.split(":", 1)[1])
            else:
                abort(400)
        except school_profile.ImageError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin.school_profile"))
        except KeyError:
            db.session.rollback()
            abort(400)
        record("school_profile_saved", "schulprofil", None,
               actor_type="staff", actor_id=current_user.id)
        db.session.commit()
        flash("Das Schulprofil wurde gespeichert.")
        return redirect(url_for("admin.school_profile"))

    values = school_profile.settings()
    images = {key: bool(values.get(key)) for key in school_profile.IMAGE_KEYS}
    stored = {key: school_profile.image(key) is not None for key in school_profile.IMAGE_KEYS}
    return render_template(
        "admin_school_profile.html", values=values, text_fields=school_profile.TEXT_FIELDS,
        image_fields=school_profile.IMAGE_FIELDS, images=images, stored=stored,
    )


@admin_bp.get("/schulprofil/bild/<key>", endpoint="school_profile_image")
@role_required(["Administrator", "Schulleitung"])
def school_profile_image(key):
    if key not in school_profile.IMAGE_KEYS:
        abort(404)
    found = school_profile.image(key)
    if found is None:
        abort(404)
    payload, mimetype = found
    return send_file(BytesIO(payload), mimetype=mimetype)


@admin_bp.get("/schulprofil/vorschau", endpoint="school_profile_preview")
@role_required(["Administrator", "Schulleitung"])
def school_profile_preview():
    """Der Elternbrief mit Beispielkind -- zeigt Briefkopf und Unterschrift."""
    year = school_year.active_year()
    return send_file(
        letters.build_preview(letterhead.branding(), letters.stored_text(),
                              school_year=f"{year}/{year + 1}" if year else None),
        mimetype="application/pdf", download_name="Vorschau_Briefkopf.pdf",
    )


@admin_bp.route("/module", methods=["GET", "POST"], endpoint="modules")
@role_required(["Administrator", "Schulleitung"])
def modules_page():
    """Teile der Anwendung, die diese Schule nutzt, ein- und ausschalten."""
    if request.method == "POST":
        enabled = set(request.form.getlist("modules")) & set(features.MODULE_KEYS)
        features.save_module_states(enabled)
        record("modules_saved", "schulprofil", None, actor_type="staff", actor_id=current_user.id)
        db.session.commit()
        flash("Die Auswahl der Module wurde gespeichert.")
        return redirect(url_for("admin.modules"))
    return render_template("admin_modules.html", module_list=features.MODULES,
                           states=features.module_states())


# --- Vorlagen -------------------------------------------------------------------

@admin_bp.route("/vorlagen", methods=["GET", "POST"], endpoint="templates")
@role_required(["Administrator", "Schulleitung"])
def templates_page():
    """Laufzettel der Verwaltungsanmeldung und Material zum Anmeldespiel."""
    text = None
    if request.method == "POST":
        aktion = request.form.get("action", "")
        try:
            if aktion == "laufzettel":
                text = request.form.get("laufzettel", "")
                vorlagen.laufzettel_speichern(text)
                meldung = "Der Laufzettel wurde gespeichert."
            elif aktion == "laufzettel_standard":
                vorlagen.laufzettel_zuruecksetzen()
                meldung = "Der Laufzettel entspricht wieder der Vorbelegung."
            elif aktion == "material":
                upload = request.files.get("material")
                if not upload or not upload.filename:
                    raise vorlagen.VorlagenFehler("Bitte eine PDF-Datei auswählen.")
                seiten = vorlagen.material_speichern(upload.read(), upload.filename)
                meldung = f"Das Material ({seiten} Seiten) wird jetzt in jedes Protokoll eingebunden."
            elif aktion == "material_entfernen":
                vorlagen.material_entfernen()
                meldung = "Das Material wurde entfernt."
            else:
                abort(400)
        except vorlagen.VorlagenFehler as exc:
            db.session.rollback()
            flash(str(exc), "error")
        else:
            record("templates_saved", "vorlage", None, actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash(meldung)
            return redirect(url_for("admin.templates"))
    return render_template(
        "admin_templates.html",
        laufzettel=text if text is not None else vorlagen.laufzettel_text(),
        ist_standard=vorlagen.ist_standard(), material=vorlagen.material_info())


@admin_bp.get("/vorlagen/vorschau/<art>", endpoint="template_preview")
@role_required(["Administrator", "Schulleitung"])
def template_preview(art):
    """Laufzettel oder Protokoll mit einem Beispielkind, ohne etwas zu speichern."""
    from types import SimpleNamespace
    from sl_office.appointments import admin_protocol_pdf, protocol_pdf

    beispiel = SimpleNamespace(
        id=0, vorname="Mia", nachname="Musterkind", strasse="Musterweg 7", plz="12345",
        ort="Musterstadt", geburtsdatum=datetime.date(datetime.date.today().year - 6, 3, 14),
        kita="Kita Sonnenschein", kann_kind=False)
    if art == "laufzettel":
        payload = admin_protocol_pdf.build(beispiel, letterhead.branding())
    elif art == "protokoll":
        payload = protocol_pdf.build(beispiel)
    else:
        abort(404)
    return send_file(BytesIO(payload), mimetype="application/pdf",
                     download_name=f"Vorschau_{art}.pdf")


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


@admin_bp.post("/datensicherung/wiederherstellen", endpoint="restore_backup")
@role_required(["Administrator"])
def restore_backup():
    """Eine Sicherung zurückspielen -- aus der Liste oder als hochgeladene Datenbank.

    Vorher wird der aktuelle Stand gesichert. Danach meldet sich jede Person neu
    an: die zurückgespielten Benutzerkonten können andere sein.
    """
    try:
        upload = request.files.get("datenbank")
        if upload and upload.filename:
            payload = upload.read(current_app.config["MAX_CONTENT_LENGTH"] + 1)
            safety = backup_service.restore_upload(current_app, payload)
            quelle = upload.filename
        else:
            name = request.form.get("name", "")
            safety = backup_service.restore_backup(current_app, name)
            quelle = name
    except backup_service.BackupError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.backups"))
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Restore failed")
        flash("Die Sicherung konnte nicht zurückgespielt werden. Der vorherige Stand liegt "
              "unter den Sicherungen mit dem Namen „vor-wiederherstellung-…“.", "error")
        return redirect(url_for("admin.backups"))

    actor = current_user.id
    record("backup_restored", "backup", None, actor_type="staff", actor_id=actor)
    db.session.commit()
    current_app.logger.info("Backup restored", extra={"source": quelle, "safety": safety.name})
    logout_user()
    session.clear()
    flash(f"Die Sicherung „{quelle}“ wurde zurückgespielt. Der vorherige Stand ist als "
          f"„{safety.name}“ gesichert. Bitte melden Sie sich neu an.")
    return redirect(url_for("auth.login"))


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
@portal_required
def registrations():
    status = request.args.get("status")
    query = db.session.query(ParentRegistration, Schueler).join(Schueler, Schueler.id == ParentRegistration.schueler_id)
    if status in {"draft", "submitted", "in_review", "completed"}:
        query = query.filter(ParentRegistration.status == status)
    entries = query.order_by(ParentRegistration.updated_at.desc()).all()
    return render_template("admin_registrations.html", entries=entries, selected_status=status)


@admin_bp.route("/anmeldungen/<int:registration_id>", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
@portal_required
def registration_detail(registration_id):
    registration = db.get_or_404(ParentRegistration, registration_id)
    student = db.get_or_404(Schueler, registration.schueler_id)
    if request.method == "POST":
        status = request.form.get("status")
        if status not in {"draft", "submitted", "in_review", "completed"}:
            flash("Ungültiger Bearbeitungsstatus.", "error")
        else:
            registration.status = status
            # Der Vorgang ist wieder in der Hand der Schule: ein erneuter
            # Hinweis auf eine Änderung der Eltern ist damit wieder fällig.
            registration.change_notified_at = None
            record("registration_status_changed", "parent_registration", registration.id, actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash("Bearbeitungsstatus gespeichert.")
            return redirect(url_for("admin.registration_detail", registration_id=registration.id))
    submitted_by = (db.session.get(ParentAccess, registration.submitted_by_access_id)
                    if registration.submitted_by_access_id else None)
    return render_template("admin_registration_detail.html", registration=registration,
                           student=student, submitted_by=submitted_by,
                           summary=registration_form.summary(registration.data or {}))


@admin_bp.get("/anmeldungen/<int:registration_id>/formular.pdf")
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
@portal_required
def registration_printout(registration_id):
    """Die Angaben der Eltern auf der amtlichen Vorlage, zum Ausdrucken.

    Wird zur Ansicht ausgeliefert statt als Download: aus dem PDF-Betrachter
    des Browsers geht der Ausdruck direkt, ohne Umweg über die Ablage.
    """
    registration = db.get_or_404(ParentRegistration, registration_id)
    student = db.get_or_404(Schueler, registration.schueler_id)
    try:
        payload = registration_pdf.build(registration.data or {}, student)
    except Exception:
        current_app.logger.exception("Registration printout failed",
                                     extra={"registration_id": registration.id})
        flash("Das Formular konnte nicht erzeugt werden.", "error")
        return redirect(url_for("admin.registration_detail", registration_id=registration.id))
    record("registration_printed", "parent_registration", registration.id,
           actor_type="staff", actor_id=current_user.id)
    db.session.commit()
    name = f"{student.nachname}_{student.vorname}".replace(" ", "-")
    return send_file(
        BytesIO(payload), mimetype="application/pdf", as_attachment=False,
        download_name=f"Schulanmeldung_{name}.pdf",
    )


@admin_bp.get("/anmeldungen/formulare.pdf")
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
@portal_required
def registration_printouts():
    """Alle übermittelten Anmeldungen in einem PDF, nach Namen sortiert.

    Entwürfe bleiben außen vor -- sie sind noch in Arbeit und taugen nicht zum
    Abheften. Mit ``?status=`` lässt sich genau das herunterladen, was die
    Übersicht gerade zeigt.
    """
    status = request.args.get("status")
    query = (db.session.query(ParentRegistration, Schueler)
             .join(Schueler, Schueler.id == ParentRegistration.schueler_id))
    if status in {"draft", "submitted", "in_review", "completed"}:
        query = query.filter(ParentRegistration.status == status)
    else:
        query = query.filter(ParentRegistration.status != "draft")
    eintraege = query.order_by(Schueler.nachname, Schueler.vorname).all()
    if not eintraege:
        flash("Es liegen keine übermittelten Anmeldungen zum Drucken vor.")
        return redirect(url_for("admin.registrations", status=status))
    try:
        payload = registration_pdf.build_many(
            [(registration.data or {}, student) for registration, student in eintraege],
            title=f"Anmeldeformulare ({len(eintraege)})")
    except Exception:
        current_app.logger.exception("Bulk registration printout failed")
        flash("Die Formulare konnten nicht erzeugt werden.", "error")
        return redirect(url_for("admin.registrations", status=status))
    for registration, _student in eintraege:
        record("registration_printed", "parent_registration", registration.id,
               actor_type="staff", actor_id=current_user.id)
    db.session.commit()
    return send_file(BytesIO(payload), mimetype="application/pdf", as_attachment=True,
                     download_name=f"Anmeldeformulare_{len(eintraege)}.pdf")


@admin_bp.route("/elternzugänge", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
@portal_required
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
                letterhead.branding(),
                (lambda token: url_for("parent_portal.activate", token=token, _external=True))
                if parent_portal_enabled() else None,
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
    # Kinder mit vergebenem Termin bekommen die Fassung, die ihn nennt.
    appointments = {student.id: letters.appointment_label(student.id) for student in students}
    return render_template(
        "admin_parent_letters.html", students=students, pending=pending, redeemed=redeemed,
        appointments={key: value for key, value in appointments.items() if value},
    )


@admin_bp.route("/elternbrief-text", methods=["GET", "POST"])
@role_required(["Administrator", "Schulleitung"])
def parent_letter_text():
    """Wortlaut des Elternanschreibens bearbeiten, prüfen und ansehen.

    Es gibt zwei Fassungen -- mit und ohne bereits vergebenen Termin. Welche
    bearbeitet wird, steht in ``variante``; beide werden getrennt gespeichert.
    """
    key = request.values.get("variante", letters.TEXT_KEY)
    if key not in letters.variants():
        key = letters.TEXT_KEY
    text = letters.stored_text(key)
    problems = []
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "reset":
            text = letters.reset_text(key)
            record("parent_letter_text_reset", "elternbrief", None,
                   actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash("Der Brieftext entspricht wieder der Schulvorlage.")
            return redirect(url_for("admin.parent_letter_text", variante=key))

        text = {field: request.form.get(field, "") for field in ("titel", "text", "gruss")}
        problems = letters.check_text(text["titel"], text["text"], text["gruss"], key=key)
        if not problems and action == "preview":
            year = school_year.active_year()
            return send_file(
                letters.build_preview(
                    letterhead.branding(), text,
                    deadline=request.form.get("deadline", "").strip() or None,
                    period=request.form.get("period", "").strip() or None,
                    school_year=f"{year}/{year + 1}" if year else None,
                ),
                mimetype="application/pdf", download_name="Vorschau_Elternbrief.pdf",
            )
        if not problems:
            letters.save_text(text["titel"], text["text"], text["gruss"], current_user.id, key=key)
            record("parent_letter_text_saved", "elternbrief", None,
                   actor_type="staff", actor_id=current_user.id)
            db.session.commit()
            flash("Der Brieftext wurde gespeichert.")
            return redirect(url_for("admin.parent_letter_text", variante=key))

    return render_template(
        "admin_parent_letter_text.html", text=text, problems=problems,
        fields=letters.variant(key)["fields"], marker=letters.ACCESS_MARKER,
        is_default=text == letters.variant(key)["default"],
        variants=letters.variants(), current_key=key,
    )


@admin_bp.post("/elternzugänge/<int:access_id>/<action>")
@role_required(["Administrator", "Schulleitung", "Sekretariat"])
@portal_required
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
