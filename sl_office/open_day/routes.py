"""Verwaltung des Tags der offenen Tür: Veranstaltung, Einteilung, Rückmeldung."""

import datetime

from io import BytesIO

from flask import (Blueprint, current_app, flash, redirect, render_template, request, send_file,
                   url_for)
from sqlalchemy.exc import IntegrityError

from models import db
from sl_office.audit import record
from sl_office.authorization import role_required
from sl_office.open_day import notifications
from sl_office.open_day import plan_pdf
from sl_office.open_day.models import (
    GRUPPEN, GRUPPEN_ABLAUF, STATIONEN, OpenDayEvent, OpenDayPlatz, OpenDayRegistration,
    OpenDayStation,
)
from sl_office.open_day.service import (
    ablaufplan, anmeldungen, einteilen, events_des_jahrgangs, ohne_platz, plaetze_zuteilen,
    platz_setzen, platzbelegung, standard_stationen, stationsplan, zaehlung, zeitspanne,
    zuteilung_je_station, zuteilungen_pruefen,
)
from sl_office.parent_portal.letterhead import branding
from sl_office.school_year import active_year, is_readonly

open_day_bp = Blueprint("open_day", __name__, url_prefix="/admin/tag-der-offenen-tuer")
MANAGE_ROLES = ["Administrator", "Schulleitung", "Sekretariat"]


def _event_or_404(event_id):
    event = db.session.get(OpenDayEvent, event_id)
    if event is None or event.school_year != active_year():
        return None
    return event


def _datum(wert):
    try:
        return datetime.date.fromisoformat((wert or "").strip())
    except ValueError:
        return None


def _uhrzeit(wert):
    try:
        return datetime.time.fromisoformat((wert or "").strip())
    except ValueError:
        return None


def _kapazitaet(wert):
    """Leer heißt unbegrenzt; Unsinn wird zu unbegrenzt statt zum Fehler."""
    wert = (wert or "").strip()
    if not wert:
        return None
    return int(wert) if wert.isdigit() and int(wert) > 0 else None


@open_day_bp.get("/")
@role_required(MANAGE_ROLES)
def index():
    jahr = active_year()
    events = events_des_jahrgangs(jahr)
    return render_template("open_day/index.html", events=events, jahr=jahr,
                           readonly=is_readonly(), heute=datetime.date.today())


@open_day_bp.post("/neu")
@role_required(MANAGE_ROLES)
def create():
    if is_readonly():
        flash("Der Jahrgang ist abgeschlossen und kann nicht mehr geändert werden.")
        return redirect(url_for("open_day.index"))
    datum = _datum(request.form.get("datum"))
    if datum is None:
        flash("Bitte ein gültiges Datum für den Tag der offenen Tür angeben.")
        return redirect(url_for("open_day.index"))
    event = OpenDayEvent(
        school_year=active_year(), datum=datum,
        titel=(request.form.get("titel") or "").strip() or "Tag der offenen Tür")
    standard_stationen(event)
    db.session.add(event)
    db.session.commit()
    record("open_day_created", "open_day_event", event.id, actor_type="staff")
    db.session.commit()
    flash("Der Tag der offenen Tür ist angelegt. Bitte Zeiten und Orte prüfen.")
    return redirect(url_for("open_day.detail", event_id=event.id))


@open_day_bp.get("/<int:event_id>")
@role_required(MANAGE_ROLES)
def detail(event_id):
    event = _event_or_404(event_id)
    if event is None:
        flash("Diese Veranstaltung gehört nicht zum geöffneten Jahrgang.")
        return redirect(url_for("open_day.index"))
    return render_template("open_day/detail.html", event=event, plan=stationsplan(event),
                           gruppen=GRUPPEN, ablauf=GRUPPEN_ABLAUF, stationen=STATIONEN,
                           readonly=is_readonly())


@open_day_bp.post("/<int:event_id>")
@role_required(MANAGE_ROLES)
def update(event_id):
    event = _event_or_404(event_id)
    if event is None or is_readonly():
        flash("Die Veranstaltung kann nicht geändert werden.")
        return redirect(url_for("open_day.index"))
    datum = _datum(request.form.get("datum"))
    if datum is None:
        flash("Bitte ein gültiges Datum angeben.")
        return redirect(url_for("open_day.detail", event_id=event.id))
    event.titel = (request.form.get("titel") or "").strip() or "Tag der offenen Tür"
    event.datum = datum
    event.ort = (request.form.get("ort") or "").strip() or None
    event.anmeldeschluss = _datum(request.form.get("anmeldeschluss"))
    event.hinweise = (request.form.get("hinweise") or "").strip() or None

    fehler = []
    for station in event.stationen:
        praefix = f"station-{station.id}"
        beginn = _uhrzeit(request.form.get(f"{praefix}-beginn"))
        ende = _uhrzeit(request.form.get(f"{praefix}-ende"))
        if beginn is None or ende is None:
            fehler.append(f"{station.label} (Gruppe {station.gruppe}): Uhrzeit unvollständig.")
            continue
        if ende <= beginn:
            fehler.append(f"{station.label} (Gruppe {station.gruppe}): Ende liegt vor Beginn.")
            continue
        station.beginn, station.ende = beginn, ende
        station.ort = (request.form.get(f"{praefix}-ort") or "").strip() or None
        for platz in station.plaetze:
            bezeichnung = (request.form.get(f"platz-{platz.id}-bezeichnung") or "").strip()
            if not bezeichnung:
                fehler.append(f"{station.label} (Gruppe {station.gruppe}): "
                              "Ein Platz braucht eine Bezeichnung.")
                continue
            platz.bezeichnung = bezeichnung
            platz.ort = (request.form.get(f"platz-{platz.id}-ort") or "").strip() or None
            platz.kapazitaet = _kapazitaet(request.form.get(f"platz-{platz.id}-kapazitaet"))
    if fehler:
        db.session.rollback()
        for meldung in fehler:
            flash(meldung)
        return redirect(url_for("open_day.detail", event_id=event.id))

    status = request.form.get("status")
    if status in ("draft", "published", "closed"):
        event.status = status
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Für diesen Jahrgang ist bereits ein Tag der offenen Tür veröffentlicht. "
              "Bitte zuerst den anderen schließen.")
        return redirect(url_for("open_day.detail", event_id=event.id))
    flash("Die Veranstaltung ist gespeichert.")
    return redirect(url_for("open_day.detail", event_id=event.id))


@open_day_bp.get("/<int:event_id>/auswertung")
@role_required(MANAGE_ROLES)
def evaluation(event_id):
    event = _event_or_404(event_id)
    if event is None:
        flash("Diese Veranstaltung gehört nicht zum geöffneten Jahrgang.")
        return redirect(url_for("open_day.index"))
    plan = stationsplan(event)
    paare = anmeldungen(event)
    zeilen = [{
        "eintrag": eintrag,
        "kind": student,
        "plan": ablaufplan(eintrag, plan),
    } for eintrag, student in paare]
    return render_template(
        "open_day/evaluation.html", event=event, zeilen=zeilen, werte=zaehlung(event),
        gruppen=GRUPPEN, stationen=STATIONEN, plan=plan, zeitspanne=zeitspanne,
        readonly=is_readonly())


@open_day_bp.post("/<int:event_id>/einteilen")
@role_required(MANAGE_ROLES)
def assign(event_id):
    event = _event_or_404(event_id)
    if event is None or is_readonly():
        flash("Die Einteilung kann nicht geändert werden.")
        return redirect(url_for("open_day.index"))
    neu = request.form.get("modus") == "neu"
    verteilt = einteilen(event, neu_verteilen=neu)
    record("open_day_grouped", "open_day_event", event.id, actor_type="staff")
    db.session.commit()
    if neu:
        flash(f"Alle Zusagen wurden neu verteilt: {verteilt} Familien eingeteilt. "
              "Bereits verschickte Ablaufpläne sind dadurch möglicherweise überholt.")
    else:
        flash(f"{verteilt} noch nicht eingeteilte Familien wurden ergänzt."
              if verteilt else "Es gab keine offenen Einteilungen.")
    return redirect(url_for("open_day.evaluation", event_id=event.id))


@open_day_bp.post("/<int:event_id>/gruppe/<int:registration_id>")
@role_required(MANAGE_ROLES)
def set_group(event_id, registration_id):
    event = _event_or_404(event_id)
    eintrag = db.session.get(OpenDayRegistration, registration_id)
    if event is None or eintrag is None or eintrag.event_id != event.id or is_readonly():
        flash("Die Einteilung kann nicht geändert werden.")
        return redirect(url_for("open_day.index"))
    wert = (request.form.get("gruppe") or "").strip()
    eintrag.gruppe = int(wert) if wert in ("1", "2") else None
    db.session.flush()
    zuteilungen_pruefen(event)
    db.session.commit()
    flash(f"{eintrag.name}: Gruppe geändert.")
    return redirect(url_for("open_day.evaluation", event_id=event.id))


@open_day_bp.post("/<int:event_id>/versenden")
@role_required(MANAGE_ROLES)
def send(event_id):
    event = _event_or_404(event_id)
    if event is None:
        flash("Diese Veranstaltung gehört nicht zum geöffneten Jahrgang.")
        return redirect(url_for("open_day.index"))
    eintraege = [eintrag for eintrag, _ in anmeldungen(event)]
    alle = request.form.get("umfang") == "alle"
    zugestellt, uebersprungen = notifications.send_plans(
        current_app, eintraege, event, nur_offene=not alle)
    record("open_day_plans_sent", "open_day_event", event.id, actor_type="staff")
    db.session.commit()
    flash(f"{zugestellt} Rückmeldungen versendet, {uebersprungen} übersprungen.")
    return redirect(url_for("open_day.evaluation", event_id=event.id))


@open_day_bp.post("/<int:event_id>/versenden/<int:registration_id>")
@role_required(MANAGE_ROLES)
def send_single(event_id, registration_id):
    event = _event_or_404(event_id)
    eintrag = db.session.get(OpenDayRegistration, registration_id)
    if event is None or eintrag is None or eintrag.event_id != event.id:
        flash("Diese Anmeldung gehört nicht zu dieser Veranstaltung.")
        return redirect(url_for("open_day.index"))
    zugestellt, _ = notifications.send_plans(current_app, [eintrag], event, nur_offene=False)
    flash(f"Ablaufplan an {eintrag.name} versendet." if zugestellt
          else f"Der Ablaufplan an {eintrag.name} konnte nicht versendet werden.")
    return redirect(url_for("open_day.evaluation", event_id=event.id))


# --- Plätze innerhalb einer Station ----------------------------------------

def _station_der_veranstaltung(event, station_id):
    station = db.session.get(OpenDayStation, station_id)
    return station if station is not None and station.event_id == event.id else None


@open_day_bp.post("/<int:event_id>/station/<int:station_id>/platz")
@role_required(MANAGE_ROLES)
def add_platz(event_id, station_id):
    """Eine Klasse bzw. OGS-Gruppe zu einer Station hinzufügen."""
    event = _event_or_404(event_id)
    station = _station_der_veranstaltung(event, station_id) if event else None
    if station is None or is_readonly():
        flash("Der Platz konnte nicht angelegt werden.")
        return redirect(url_for("open_day.index"))
    # Die Felder liegen im großen Formular der Seite und tragen deshalb die
    # Stationsnummer im Namen -- verschachtelte Formulare gibt es in HTML nicht.
    praefix = f"neuer-platz-{station.id}"
    bezeichnung = (request.form.get(f"{praefix}-bezeichnung") or "").strip()
    if not bezeichnung:
        flash("Bitte eine Bezeichnung angeben, etwa „Klasse 2b“.")
        return redirect(url_for("open_day.detail", event_id=event.id))
    station.plaetze.append(OpenDayPlatz(
        bezeichnung=bezeichnung,
        ort=(request.form.get(f"{praefix}-ort") or "").strip() or None,
        kapazitaet=_kapazitaet(request.form.get(f"{praefix}-kapazitaet"))))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(f"„{bezeichnung}“ gibt es an dieser Station bereits.")
    return redirect(url_for("open_day.detail", event_id=event.id))


@open_day_bp.post("/<int:event_id>/platz/<int:platz_id>/loeschen")
@role_required(MANAGE_ROLES)
def delete_platz(event_id, platz_id):
    event = _event_or_404(event_id)
    platz = db.session.get(OpenDayPlatz, platz_id)
    if event is None or platz is None or platz.station.event_id != event.id or is_readonly():
        flash("Der Platz konnte nicht entfernt werden.")
        return redirect(url_for("open_day.index"))
    # Die Zuteilungen hängen am Platz und fallen mit ihm; die betroffenen
    # Familien tauchen danach wieder unter "ohne Platz" auf.
    bezeichnung = platz.bezeichnung
    db.session.delete(platz)
    db.session.commit()
    flash(f"„{bezeichnung}“ wurde entfernt. Betroffene Familien sind wieder ohne Platz.")
    return redirect(url_for("open_day.detail", event_id=event.id))


@open_day_bp.post("/<int:event_id>/plaetze-zuteilen")
@role_required(MANAGE_ROLES)
def assign_plaetze(event_id):
    event = _event_or_404(event_id)
    if event is None or is_readonly():
        flash("Die Zuteilung kann nicht geändert werden.")
        return redirect(url_for("open_day.index"))
    neu = request.form.get("modus") == "neu"
    zugeteilt, offen = plaetze_zuteilen(event, neu_verteilen=neu)
    record("open_day_places_assigned", "open_day_event", event.id, actor_type="staff")
    db.session.commit()
    flash(f"{zugeteilt} Zuteilungen vorgenommen."
          if zugeteilt else "Es gab nichts zuzuteilen.")
    if offen:
        flash(f"{offen} Familien blieben ohne Platz – die Kapazitäten reichen nicht aus.")
    return redirect(url_for("open_day.fine_assignment", event_id=event.id))


@open_day_bp.get("/<int:event_id>/feineinteilung")
@role_required(MANAGE_ROLES)
def fine_assignment(event_id):
    """Wer hospitiert in welcher Klasse bzw. OGS-Gruppe."""
    event = _event_or_404(event_id)
    if event is None:
        flash("Diese Veranstaltung gehört nicht zum geöffneten Jahrgang.")
        return redirect(url_for("open_day.index"))
    belegung = platzbelegung(event)
    stationen = [station for station in event.stationen if station.plaetze]
    stationen.sort(key=lambda station: (station.beginn, station.gruppe))
    return render_template("open_day/fine_assignment.html", event=event, stationen=stationen,
                           belegung=belegung, offen=ohne_platz(event), zeitspanne=zeitspanne,
                           readonly=is_readonly())


@open_day_bp.post("/<int:event_id>/feineinteilung/<int:registration_id>/<int:station_id>")
@role_required(MANAGE_ROLES)
def set_platz(event_id, registration_id, station_id):
    event = _event_or_404(event_id)
    eintrag = db.session.get(OpenDayRegistration, registration_id)
    station = _station_der_veranstaltung(event, station_id) if event else None
    if (event is None or eintrag is None or station is None
            or eintrag.event_id != event.id or is_readonly()):
        flash("Die Zuteilung kann nicht geändert werden.")
        return redirect(url_for("open_day.index"))
    wert = (request.form.get("platz") or "").strip()
    platz = db.session.get(OpenDayPlatz, int(wert)) if wert.isdigit() else None
    if platz is not None and platz.station_id != station.id:
        flash("Dieser Platz gehört zu einer anderen Station.")
        return redirect(url_for("open_day.fine_assignment", event_id=event.id))
    platz_setzen(eintrag, station, platz)
    db.session.commit()
    flash(f"{eintrag.name}: {station.label} – "
          + (f"jetzt {platz.beschriftung}." if platz else "Zuteilung aufgehoben."))
    return redirect(url_for("open_day.fine_assignment", event_id=event.id))


@open_day_bp.get("/<int:event_id>/anmeldung/<int:registration_id>/ablaufplan.pdf")
@role_required(MANAGE_ROLES)
def plan_document(event_id, registration_id):
    """Der Ablaufplan einer Familie, wie ihn die Mail mitschickt."""
    event = _event_or_404(event_id)
    eintrag = db.session.get(OpenDayRegistration, registration_id)
    if event is None or eintrag is None or eintrag.event_id != event.id:
        flash("Diese Anmeldung gehört nicht zu dieser Veranstaltung.")
        return redirect(url_for("open_day.index"))
    payload = plan_pdf.build_plan(event, eintrag, branding())
    return send_file(BytesIO(payload), mimetype="application/pdf",
                     download_name=plan_pdf.dateiname(event))


@open_day_bp.get("/<int:event_id>/listen")
@role_required(MANAGE_ROLES)
def rosters(event_id):
    """Teilnehmerlisten je Gruppe und Station -- zum Ausdrucken für den Tag."""
    event = _event_or_404(event_id)
    if event is None:
        flash("Diese Veranstaltung gehört nicht zum geöffneten Jahrgang.")
        return redirect(url_for("open_day.index"))
    plan = stationsplan(event)
    zusagen = anmeldungen(event, nur_teilnehmer=True)
    listen = []
    for gruppe in GRUPPEN:
        for art in GRUPPEN_ABLAUF[gruppe]:
            station = plan.get((gruppe, art))
            if station is None:
                continue
            teilnehmer = [(eintrag, student) for eintrag, student in zusagen
                          if eintrag.gruppe == gruppe and art in eintrag.wuensche]
            if not station.plaetze:
                listen.append({"station": station, "platz": None, "familien": teilnehmer})
                continue
            # Eine Liste je Klasse bzw. OGS-Gruppe -- die Lehrkraft braucht
            # ihre eigene, nicht die der ganzen Schiene.
            zugeteilt = {}
            for eintrag, student in teilnehmer:
                zeile = zuteilung_je_station(eintrag).get(station.id)
                zugeteilt.setdefault(zeile.platz_id if zeile else None, []).append(
                    (eintrag, student))
            for platz in station.plaetze:
                listen.append({"station": station, "platz": platz,
                               "familien": zugeteilt.get(platz.id, [])})
            if zugeteilt.get(None):
                listen.append({"station": station, "platz": None,
                               "familien": zugeteilt[None], "unverteilt": True})
    listen.sort(key=lambda zeile: (zeile["station"].beginn, zeile["station"].gruppe,
                                   zeile["platz"].bezeichnung if zeile["platz"] else ""))
    ohne_programm = [(eintrag, student) for eintrag, student in zusagen
                     if not eintrag.wuensche]
    return render_template("open_day/rosters.html", event=event, listen=listen,
                           ohne_programm=ohne_programm, zeitspanne=zeitspanne)
