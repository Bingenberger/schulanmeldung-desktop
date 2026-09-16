"""Public, passwordless parent portal routes."""

from functools import wraps

from io import BytesIO

from flask import (Blueprint, current_app, flash, redirect, render_template, request,
                   send_file, session, url_for)
from sqlalchemy import func, select

from models import Schueler, db
from sl_office.appointments import calendar
from sl_office.appointments import notifications as appointment_mail
from sl_office.appointments.service import (AssignedByStaff, BookingError,
                                            active_booking_for_student, book_slot, cancel_booking,
                                            moment_label, naive_utc, slot_label)
from sl_office.parent_portal import letterhead, progress
from sl_office.parent_portal.access_service import (
    InvalidAccessToken, consume_activation_grant, consume_login_token, create_login_token,
    letter_link_state, login_link_recently_sent, mask_email, normalize_email,
)
from sl_office.parent_portal.mail_service import send_parent_login_link
from sl_office.parent_portal import notifications as registration_mail
from sl_office.parent_portal.models import AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentAccess, ParentRegistration, utcnow
from sl_office.parent_portal import registration_form
from sl_office.audit import record

parent_portal_bp = Blueprint("parent_portal", __name__, url_prefix="/eltern")
SESSION_KEY = "parent_access_id"
SESSION_VERSION_KEY = "parent_access_version"


def _current_parent_access():
    access_id = session.get(SESSION_KEY)
    access = db.session.get(ParentAccess, access_id) if access_id else None
    if not access or access.status != "active" or access.security_version != session.get(SESSION_VERSION_KEY):
        session.pop(SESSION_KEY, None)
        session.pop(SESSION_VERSION_KEY, None)
        return None
    return access


def parent_access_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        access = _current_parent_access()
        if access is None:
            flash("Bitte verwenden Sie Ihren persönlichen Zugangslink.")
            return redirect(url_for("parent_portal.start"))
        return view(access, *args, **kwargs)
    return wrapped


def _start_session(access):
    session.clear()
    session[SESSION_KEY] = access.id
    session[SESSION_VERSION_KEY] = access.security_version
    session.permanent = True


@parent_portal_bp.context_processor
def _parent_navigation():
    """Die Navigation des Elternbereichs statt der der Schulverwaltung.

    Ohne das führten Schriftzug und "Anmelden" oben auf jeder Elternseite zur
    Anmeldung der Verwaltung -- dort suchen Eltern dann nach einem Passwort,
    das es für sie gar nicht gibt.
    """
    return {"parent_area": True, "parent_access": _current_parent_access()}


@parent_portal_bp.get("/")
def start():
    """Die Anmeldung für Eltern: E-Mail-Adresse eingeben, Link kommt per Mail."""
    if _current_parent_access():
        return redirect(url_for("parent_portal.dashboard"))
    return render_template("parent_portal/start.html")


def _send_login_link(access):
    """Einen Anmeldelink an die hinterlegte Adresse schicken.

    Innerhalb der Sperrfrist geht nichts hinaus; die Eltern sehen trotzdem
    dieselbe Bestätigung -- die erste Mail ist ja unterwegs.
    """
    if login_link_recently_sent(access.id):
        return
    _, token = create_login_token(access.id)
    record("parent_login_link_requested", "parent_access", access.id,
           actor_type="parent", actor_id=access.id)
    db.session.commit()
    link = url_for("parent_portal.login", token=token, _external=True)
    student = db.session.get(Schueler, access.schueler_id)
    try:
        send_parent_login_link(current_app, access.email_normalized, link,
                               f"{student.vorname} {student.nachname}",
                               url_for("parent_portal.start", _external=True))
    except Exception:
        current_app.logger.exception("Parent login mail failed")


@parent_portal_bp.route("/link-anfordern", methods=["GET", "POST"])
def request_login_link():
    if request.method == "GET":
        # Die Adresse stand früher in Mails und Aushängen; sie führt jetzt auf
        # die Anmeldeseite, auf der das Formular selbst steht.
        return redirect(url_for("parent_portal.start"))
    email = normalize_email(request.form.get("email", ""))
    # Ein Elternteil kann mehrere Kinder angemeldet haben -- dann hat jedes
    # Kind seinen eigenen Zugang, und jeder bekommt seinen Link.
    accesses = list(db.session.scalars(select(ParentAccess).where(
        ParentAccess.email_normalized == email, ParentAccess.status == "active"
    ))) if email else []
    for access in accesses:
        _send_login_link(access)
    if not accesses:
        db.session.rollback()
    # Immer dieselbe Antwort: sonst ließe sich ausprobieren, welche Adressen
    # bei der Schule hinterlegt sind.
    return render_template("parent_portal/link_sent.html", email=email)


@parent_portal_bp.route("/aktivieren/<token>", methods=["GET", "POST"])
def activate(token):
    if request.method == "POST":
        try:
            access = consume_activation_grant(token, request.form.get("email", ""), request.form.get("display_name", ""))
            record("parent_access_activated", "parent_access", access.id, actor_type="parent", actor_id=access.id)
            db.session.commit()
        except (InvalidAccessToken, ValueError) as exc:
            db.session.rollback()
            flash(str(exc))
        else:
            _start_session(access)
            return redirect(url_for("parent_portal.dashboard"))

    zustand, access = letter_link_state(token)
    if zustand == "eingerichtet":
        # Wer schon angemeldet ist und nur den QR-Code erneut scannt, will
        # einfach hinein.
        current = _current_parent_access()
        if current is not None and current.id == access.id:
            return redirect(url_for("parent_portal.dashboard"))
        return render_template("parent_portal/already_active.html", token=token,
                               masked_email=mask_email(access.email_normalized))
    if zustand == "ungueltig":
        return render_template("parent_portal/link_invalid.html"), 410
    return render_template("parent_portal/activate.html")


@parent_portal_bp.post("/aktivieren/<token>/anmeldelink")
def resend_from_letter(token):
    """Aus dem Brief heraus einen Anmeldelink an die hinterlegte Adresse senden.

    Die Eltern müssen dafür nichts eintippen. Der Link geht ausschließlich an
    die Adresse, die beim ersten Öffnen hinterlegt wurde -- wer nur den Brief
    hat, bekommt ihn also nicht.
    """
    zustand, access = letter_link_state(token)
    if zustand != "eingerichtet":
        return render_template("parent_portal/link_invalid.html"), 410
    _send_login_link(access)
    return render_template("parent_portal/link_sent.html",
                           email=mask_email(access.email_normalized), masked=True)


@parent_portal_bp.get("/anmelden/<token>")
def login(token):
    try:
        access = consume_login_token(token)
        record("parent_login", "parent_access", access.id, actor_type="parent", actor_id=access.id)
        db.session.commit()
    except InvalidAccessToken:
        db.session.rollback()
        flash("Dieser Anmeldelink ist abgelaufen oder wurde schon benutzt. Geben Sie "
              "unten einfach Ihre E-Mail-Adresse ein – Sie bekommen sofort einen neuen.")
        return redirect(url_for("parent_portal.start"))
    _start_session(access)
    return redirect(url_for("parent_portal.dashboard"))


@parent_portal_bp.post("/abmelden")
def logout():
    session.clear()
    return redirect(url_for("parent_portal.start"))


@parent_portal_bp.get("/uebersicht")
@parent_access_required
def dashboard(access):
    student = db.session.get(Schueler, access.schueler_id)
    booking = db.session.scalar(select(AppointmentBooking).where(
        AppointmentBooking.schueler_id == access.schueler_id,
        AppointmentBooking.status == "confirmed",
    ).order_by(AppointmentBooking.booked_at.desc()))
    registration = db.session.scalar(select(ParentRegistration).where(ParentRegistration.schueler_id == access.schueler_id))
    public_status = {"draft": "Formular in Bearbeitung", "submitted": "Formular eingereicht", "in_review": "Unterlagen werden geprüft", "completed": "Verfahren abgeschlossen"}
    appointment = active_booking_for_student(access.schueler_id)
    open_day_event, open_day_entry, _ = _open_day_context(access)
    schritte = progress.prozessschritte(student, appointment, registration, slot_label,
                                        open_day_event, open_day_entry)
    return render_template("parent_portal/dashboard.html", student=student, booking=booking,
                           appointment=appointment, slot_label=slot_label,
                           registration=registration, public_status=public_status,
                           submission_note=_submission_note(registration),
                           open_day_event=open_day_event, open_day_entry=open_day_entry,
                           schritte=schritte, zustaende=progress.ZUSTAENDE,
                           fortschritt=progress.fortschritt(schritte),
                           contact=current_app.config.get("SCHOOL_CONTACT_MAIL", ""))


@parent_portal_bp.get("/termin.ics")
@parent_access_required
def appointment_calendar(access):
    """Den eigenen Termin als Kalenderdatei.

    Der Termin wird über die Sitzung ermittelt, nicht über eine ID aus der
    Adresse -- so kann niemand die Buchung einer anderen Familie abrufen.
    """
    appointment = active_booking_for_student(access.schueler_id)
    if appointment is None:
        flash("Es ist noch kein Termin gebucht.")
        return redirect(url_for("parent_portal.dashboard"))
    booking, slot, event = appointment
    student = db.session.get(Schueler, access.schueler_id)
    payload = calendar.build_calendar([calendar.parent_entry(
        booking, slot, event, student,
        school_name=current_app.config.get("SCHOOL_NAME", ""),
        instructions=event.parent_instructions or "",
    )], name="Schulanmeldung")
    return send_file(BytesIO(payload), mimetype="text/calendar",
                     as_attachment=True, download_name="Anmeldetermin.ics")


@parent_portal_bp.get("/formular")
@parent_access_required
def registration(access):
    """Zum offenen Schritt springen; ein leeres Formular beginnt vorne."""
    form = _registration_draft(access)
    db.session.commit()
    return redirect(url_for("parent_portal.registration_step",
                            step=registration_form.resume_step(form.data or {})))


@parent_portal_bp.route("/formular/<step>", methods=["GET", "POST"])
@parent_access_required
def registration_step(access, step):
    """Ein Abschnitt des Anmeldeformulars.

    Jeder Schritt speichert für sich, damit Eltern jederzeit aufhören und
    später weitermachen können. Pflichtangaben werden erst beim Absenden
    verlangt -- vorher würde die Prüfung das Zwischenspeichern verhindern.
    """
    current = registration_form.step_by_key(step)
    if current is None:
        return redirect(url_for("parent_portal.registration"))
    student = db.session.get(Schueler, access.schueler_id)
    form = _registration_draft(access)
    data = dict(form.data or {})
    before = dict(data)
    gaps = []

    if request.method == "POST":
        action = request.form.get("action", "next")
        if action != "skip":
            for field in current.fields:
                # normalise() vereinheitlicht, was in mehreren Schreibweisen
                # ankommen darf -- etwa "082023" statt "08/2023" vom
                # Ziffernfeld eines Mobiltelefons.
                data[field.name] = registration_form.normalise(
                    field.name, request.form.get(field.name, ""))
        # Beide Sorgeberechtigten füllen dasselbe Formular. War es schon
        # abgesendet, ist jede inhaltliche Änderung für die Schule relevant --
        # reines Durchblättern ohne Änderung dagegen nicht.
        changed_after_submission = form.status != "draft" and data != before
        form.data = data
        form.version = (form.version or 0) + 1
        submitted = False
        if action == "submit":
            gaps = registration_form.missing(data)
            if not gaps:
                form.status = "submitted"
                # Nur die erste Abgabe zählt: für die Anmeldefrist ist der
                # ursprüngliche Zeitpunkt maßgeblich, nicht die letzte Korrektur.
                form.submitted_at = form.submitted_at or utcnow()
                form.submitted_by_access_id = form.submitted_by_access_id or access.id
                submitted = True
        if changed_after_submission:
            # Aus "In Prüfung" oder "Abgeschlossen" zurück auf "Übermittelt":
            # die Schule hat einen Stand gesehen, der so nicht mehr gilt.
            form.status = "submitted"
            record("registration_changed_after_submission", "parent_registration", form.id,
                   actor_type="parent", actor_id=access.id)
        record("registration_submitted" if submitted else "registration_saved",
               "parent_registration", form.id, actor_type="parent", actor_id=access.id)
        db.session.commit()
        if changed_after_submission:
            flash("Das Formular war bereits abgesendet. Ihre Änderung ist gespeichert, "
                  "die Schule sieht sich die Anmeldung noch einmal an.")
            # Erst nach dem Commit: eine klemmende Mail darf die Eingabe nicht verwerfen.
            try:
                registration_mail.notify_registration_change(
                    current_app, form.id, changed_by=access.display_name)
            except Exception:
                current_app.logger.exception("Hinweis auf geänderte Anmeldung nicht versendet")
        if action == "save":
            flash("Ihre Angaben sind gespeichert. Sie können jederzeit über Ihren "
                  "Zugangslink weitermachen.")
            return redirect(url_for("parent_portal.dashboard"))
        if action in ("next", "skip"):
            following = registration_form.neighbours(step)[1]
            return redirect(url_for("parent_portal.registration_step", step=following.key)
                            if following else url_for("parent_portal.dashboard"))
        if submitted:
            flash("Das Anmeldeformular wurde übermittelt.")
            return redirect(url_for("parent_portal.dashboard"))
        flash("Bitte ergänzen Sie noch die fehlenden Pflichtangaben.")

    previous, following = registration_form.neighbours(step)
    return render_template(
        "parent_portal/registration.html", student=student, form=form, step=current,
        steps=registration_form.STEPS, number=registration_form.step_number(step),
        previous=previous, following=following, data=data, gaps=gaps,
        summary=registration_form.summary(data) if current is registration_form.SUMMARY_STEP else None,
        prefill=_registration_prefill(access, student),
        submission_note=_submission_note(form),
    )


# --- Tag der offenen Tür ---------------------------------------------------

def _open_day_context(access):
    """Veranstaltung und bisherige Rückmeldung dieser Familie.

    Der Import steht in der Funktion: das Modul zum Tag der offenen Tür greift
    seinerseits auf das Elternportal zu, ein Import oben liefe im Kreis.
    """
    from sl_office.open_day.models import OpenDayRegistration
    from sl_office.open_day.service import veroeffentlichtes_event

    student = db.session.get(Schueler, access.schueler_id)
    event = veroeffentlichtes_event(student.einschulungsjahr) if student else None
    if event is None:
        return None, None, student
    eintrag = db.session.scalar(select(OpenDayRegistration).where(
        OpenDayRegistration.event_id == event.id,
        OpenDayRegistration.schueler_id == access.schueler_id))
    return event, eintrag, student


@parent_portal_bp.route("/tag-der-offenen-tuer", methods=["GET", "POST"])
@parent_access_required
def open_day(access):
    """Rückmeldung zum Tag der offenen Tür.

    Eine Rückmeldung je Kind: Beide Sorgeberechtigten bearbeiten dieselbe
    Zeile, wie beim Anmeldeformular auch. Eine Absage ist eine vollwertige
    Antwort -- die Schule plant damit.
    """
    from sl_office.open_day.models import OpenDayRegistration
    from sl_office.open_day.service import ablaufplan, stationsplan, zuteilungen_pruefen

    event, eintrag, student = _open_day_context(access)
    if event is None:
        flash("Zurzeit ist kein Tag der offenen Tür ausgeschrieben.")
        return redirect(url_for("parent_portal.dashboard"))

    if request.method == "POST":
        if not event.anmeldung_offen:
            flash("Die Anmeldefrist ist abgelaufen. Bitte wenden Sie sich an das Sekretariat.")
            return redirect(url_for("parent_portal.open_day"))
        name = (request.form.get("name") or "").strip()
        email = normalize_email(request.form.get("email", ""))
        if not name or not email:
            flash("Bitte geben Sie Ihren Namen und Ihre E-Mail-Adresse an.")
            return redirect(url_for("parent_portal.open_day"))
        if eintrag is None:
            eintrag = OpenDayRegistration(event_id=event.id, schueler_id=access.schueler_id,
                                          name=name, email=email)
            db.session.add(eintrag)
        teilnahme = request.form.get("teilnahme") == "ja"
        eintrag.name, eintrag.email = name, email
        eintrag.parent_access_id = access.id
        eintrag.teilnahme = teilnahme
        # Bei einer Absage sind die Programmwünsche gegenstandslos; sie stehen
        # zu lassen ergäbe eine Familie, die zu nichts kommt, aber überall zählt.
        eintrag.wunsch_fuehrung = teilnahme and request.form.get("fuehrung") == "ja"
        eintrag.wunsch_unterricht = teilnahme and request.form.get("unterricht") == "ja"
        eintrag.wunsch_ogs = teilnahme and request.form.get("ogs") == "ja"
        if not teilnahme:
            eintrag.gruppe = None
        eintrag.bemerkung = (request.form.get("bemerkung") or "").strip() or None
        db.session.flush()
        # Wer einen Programmpunkt abwählt, darf nicht mit einer Einladung in
        # eine Klasse dastehen, die er gar nicht mehr besucht.
        zuteilungen_pruefen(event)
        record("open_day_registered", "open_day_registration", eintrag.id,
               actor_type="parent", actor_id=access.id)
        db.session.commit()
        flash("Vielen Dank, Ihre Rückmeldung ist gespeichert." if teilnahme
              else "Vielen Dank, Ihre Absage ist vermerkt.")
        return redirect(url_for("parent_portal.dashboard"))

    return render_template(
        "parent_portal/open_day.html", event=event, eintrag=eintrag, student=student,
        vorschlag_name=(eintrag.name if eintrag else access.display_name) or "",
        vorschlag_email=(eintrag.email if eintrag else access.email_normalized) or "",
        plan=ablaufplan(eintrag, stationsplan(event)) if eintrag else [],
        contact=current_app.config.get("SCHOOL_CONTACT_MAIL", ""))


@parent_portal_bp.get("/tag-der-offenen-tuer/ablaufplan.pdf")
@parent_access_required
def open_day_plan(access):
    """Der eigene Ablaufplan als PDF -- dieselbe Datei wie im Mailanhang."""
    from sl_office.open_day import plan_pdf
    from sl_office.open_day.service import ablaufplan

    event, eintrag, _ = _open_day_context(access)
    if event is None or eintrag is None or not ablaufplan(eintrag):
        flash("Es liegt noch kein Ablaufplan für Sie vor.")
        return redirect(url_for("parent_portal.dashboard"))
    payload = plan_pdf.build_plan(event, eintrag, letterhead.branding(current_app.config))
    return send_file(BytesIO(payload), mimetype="application/pdf",
                     as_attachment=True, download_name=plan_pdf.dateiname(event))


def _submission_note(form):
    """"Am ... von ... übermittelt" -- oder nichts, solange nichts abgesendet ist.

    Beide Sorgeberechtigten sehen dasselbe Formular; ohne diesen Hinweis kann
    die zweite Person nicht erkennen, dass die erste es schon abgeschickt hat.
    """
    if form is None or form.submitted_at is None:
        return ""
    who = db.session.get(ParentAccess, form.submitted_by_access_id) if form.submitted_by_access_id else None
    stamp = form.submitted_at.strftime("%d.%m.%Y")
    return f"Am {stamp} von {who.display_name} übermittelt." if who else f"Am {stamp} übermittelt."


def _registration_draft(access):
    form = db.session.scalar(select(ParentRegistration).where(
        ParentRegistration.schueler_id == access.schueler_id))
    if not form:
        form = ParentRegistration(schueler_id=access.schueler_id,
                                  created_by_access_id=access.id, data={})
        db.session.add(form)
        db.session.flush()
    return form


def _registration_prefill(access, student):
    """Seed an empty form from the login and the municipal master data.

    Only used where the form has no value yet, so a parent's own entry is never
    overwritten -- both guardians edit the same form for a child.
    """
    return {key: value for key, value in {
        "kind_nachname": student.nachname,
        "kind_vorname": student.vorname,
        # <input type="date"> erwartet ISO, sonst bleibt das Feld leer.
        "kind_geburtsdatum": student.geburtsdatum.isoformat() if student.geburtsdatum else "",
        "kind_strasse": student.strasse or "",
        "kind_plz": student.plz or "",
        "kind_ort": student.ort or "",
        "sorgeberechtigt_1_name": access.display_name or "",
        "sorgeberechtigt_1_email": access.email_normalized or "",
        "sorgeberechtigt_1_strasse": student.strasse or "",
        "sorgeberechtigt_1_plz": student.plz or "",
        "sorgeberechtigt_1_ort": student.ort or "",
    }.items() if value}


@parent_portal_bp.get("/termine")
@parent_access_required
def appointments(access):
    now = utcnow().replace(tzinfo=None)
    # Exactly one event is open for booking at a time; picking the newest
    # keeps slots of an older school year out of the parent's list.
    event = db.session.scalar(
        select(AppointmentEvent)
        .where(AppointmentEvent.status == "published")
        .order_by(AppointmentEvent.school_year.desc(), AppointmentEvent.id.desc())
        .limit(1)
    )
    if event is None:
        return render_template("parent_portal/appointments.html", slots=[], event=None,
                               slot_label=slot_label)
    # Ohne diese Prüfung standen die Zeitfenster mitsamt "Buchen"-Schaltfläche
    # da, obwohl book_slot die Buchung ablehnt -- die Eltern liefen erst beim
    # Klick in die Meldung, dass die Buchung noch nicht geöffnet sei.
    opens, closes = naive_utc(event.booking_opens_at), naive_utc(event.booking_closes_at)
    if opens and now < opens:
        return render_template(
            "parent_portal/appointments.html", slots=[], event=event, slot_label=slot_label,
            window_note=f"Die Terminbuchung öffnet am {moment_label(event.booking_opens_at, event)}. "
                        "Bitte schauen Sie ab dann noch einmal herein.")
    if closes and now > closes:
        return render_template(
            "parent_portal/appointments.html", slots=[], event=event, slot_label=slot_label,
            window_note=f"Die Terminbuchung ist seit {moment_label(event.booking_closes_at, event)} "
                        "abgeschlossen. Bitte wenden Sie sich an die Schule.")
    booked_count = func.count(AppointmentBooking.id).filter(AppointmentBooking.status == "confirmed")
    rows = db.session.execute(
        select(AppointmentSlot, booked_count.label("booked_count"))
        .outerjoin(AppointmentBooking, AppointmentBooking.slot_id == AppointmentSlot.id)
        .where(
            AppointmentSlot.event_id == event.id,
            AppointmentSlot.status == "available",
            AppointmentSlot.starts_at > now,
        )
        .group_by(AppointmentSlot.id).order_by(AppointmentSlot.starts_at)
    ).all()
    slots = [(slot, count) for slot, count in rows if count < slot.capacity]
    return render_template("parent_portal/appointments.html", slots=slots, event=event,
                           slot_label=slot_label)


@parent_portal_bp.post("/termine/<int:slot_id>/buchen")
@parent_access_required
def book(access, slot_id):
    try:
        booking = book_slot(slot_id, access.schueler_id, access.id)
        record("appointment_booked", "appointment_slot", slot_id, actor_type="parent", actor_id=access.id)
        db.session.commit()
        flash("Der Termin wurde verbindlich gebucht.")
    except BookingError as exc:
        db.session.rollback()
        flash(str(exc))
        return redirect(url_for("parent_portal.dashboard"))
    # Erst nach dem Commit: der Termin steht, auch wenn der Mailserver klemmt.
    try:
        appointment_mail.confirm_booking(current_app, booking.id)
    except Exception:
        current_app.logger.exception("Terminbestätigung konnte nicht versendet werden")
        flash("Die Bestätigung per E-Mail konnte nicht zugestellt werden. "
              "Der Termin ist trotzdem gebucht.")
    return redirect(url_for("parent_portal.dashboard"))


@parent_portal_bp.post("/termine/<int:booking_id>/stornieren")
@parent_access_required
def cancel(access, booking_id):
    try:
        cancel_booking(booking_id, access.id)
        record("appointment_cancelled", "appointment_booking", booking_id, actor_type="parent", actor_id=access.id)
        db.session.commit()
        flash("Der Termin wurde storniert.")
    except AssignedByStaff as exc:
        # Kein Fehler der Eltern: die Oberfläche bietet den Knopf gar nicht an,
        # hier landet nur, wer die Adresse direkt aufruft.
        db.session.rollback()
        flash(str(exc))
    except BookingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("parent_portal.dashboard"))
