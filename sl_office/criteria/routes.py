"""Kriterienkatalog bearbeiten: „Verwaltung → Kriterien“."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from models import db
from sl_office.audit import record
from sl_office.authorization import role_required
from sl_office.criteria import service
from sl_office.criteria.catalog import BOEGEN, TYPEN
from sl_office.criteria.models import Kriterium

criteria_bp = Blueprint("criteria", __name__, url_prefix="/admin/kriterien")
ROLLEN = ["Administrator", "Schulleitung"]
#: Feldtypen, bei denen Optionen gebraucht werden.
MIT_OPTIONEN = {"auswahl", "mehrfach"}


def _bogen_oder_404(bogen):
    if bogen not in BOEGEN:
        abort(404)
    return bogen


def _kriterium_oder_404(kriterium_id):
    return db.get_or_404(Kriterium, kriterium_id)


def _protokoll(aktion, kriterium_id=None):
    record("criteria_" + aktion, "kriterium", kriterium_id,
           actor_type="staff", actor_id=current_user.id)


@criteria_bp.get("/")
@role_required(ROLLEN)
def index():
    return redirect(url_for("criteria.catalog", bogen=next(iter(BOEGEN))))


@criteria_bp.get("/<bogen>")
@role_required(ROLLEN)
def catalog(bogen):
    _bogen_oder_404(bogen)
    liste = service.kriterien(bogen, nur_aktive=False)
    db.session.commit()  # ein eben angelegter Standardkatalog bleibt stehen
    benutzt = {k.id for k in liste if service.hat_werte(k.id)}
    return render_template("admin_criteria.html", boegen=BOEGEN, bogen=bogen,
                           gruppen=service.gruppiert(liste), benutzt=benutzt, typen=TYPEN)


def _aus_formular(kriterium, gesperrter_typ=None):
    """Formularwerte prüfen und übernehmen; liefert eine Liste von Beanstandungen."""
    form = request.form
    probleme = []
    bezeichnung = form.get("bezeichnung", "").strip()
    typ = form.get("typ", "skala")
    optionen = "\n".join(zeile.strip() for zeile in form.get("optionen", "").splitlines()
                         if zeile.strip())
    if not bezeichnung:
        probleme.append("Bitte eine Bezeichnung angeben.")
    if typ not in TYPEN:
        probleme.append("Unbekannter Feldtyp.")
    if gesperrter_typ and typ != gesperrter_typ:
        probleme.append("Der Feldtyp lässt sich nicht mehr ändern, weil schon Werte erfasst sind. "
                        "Legen Sie stattdessen ein neues Kriterium an und schalten Sie dieses ab.")
    if typ in MIT_OPTIONEN and not optionen:
        probleme.append("Für eine Auswahl braucht es mindestens eine Option (eine je Zeile).")
    if probleme:
        return probleme
    kriterium.gruppe = form.get("gruppe", "").strip()[:120]
    kriterium.bezeichnung = bezeichnung[:200]
    kriterium.kurz = form.get("kurz", "").strip()[:60]
    kriterium.typ = typ
    kriterium.optionen = optionen if typ in MIT_OPTIONEN else ""
    kriterium.pflicht = bool(form.get("pflicht")) and typ != "janein"
    kriterium.in_wertung = bool(form.get("in_wertung")) and typ == "skala"
    kriterium.foerderhinweis = bool(form.get("foerderhinweis")) and typ in {"janein", "mehrfach", "auswahl"}
    return []


def _gruppen(bogen):
    return sorted({k.gruppe for k in service.kriterien(bogen, nur_aktive=False) if k.gruppe})


@criteria_bp.route("/<bogen>/neu", methods=["GET", "POST"])
@role_required(ROLLEN)
def create(bogen):
    _bogen_oder_404(bogen)
    kriterium = Kriterium(bogen=bogen, typ="skala", in_wertung=True, aktiv=True,
                          gruppe=request.args.get("gruppe", ""))
    probleme = []
    if request.method == "POST":
        probleme = _aus_formular(kriterium)
        if not probleme:
            letzte = db.session.scalar(select(db.func.max(Kriterium.reihenfolge))
                                       .where(Kriterium.bogen == bogen)) or 0
            kriterium.reihenfolge = letzte + 10
            db.session.add(kriterium)
            db.session.flush()
            _protokoll("created", kriterium.id)
            db.session.commit()
            flash(f"„{kriterium.bezeichnung}“ wurde angelegt.")
            return redirect(url_for("criteria.catalog", bogen=bogen))
    return render_template("admin_criterion_form.html", boegen=BOEGEN, bogen=bogen,
                           kriterium=kriterium, typen=TYPEN, probleme=probleme,
                           gruppen=_gruppen(bogen), typ_gesperrt=False)


@criteria_bp.route("/eintrag/<int:kriterium_id>", methods=["GET", "POST"])
@role_required(ROLLEN)
def edit(kriterium_id):
    kriterium = _kriterium_oder_404(kriterium_id)
    gesperrt = service.hat_werte(kriterium.id)
    probleme = []
    if request.method == "POST":
        probleme = _aus_formular(kriterium, gesperrter_typ=kriterium.typ if gesperrt else None)
        if not probleme:
            _protokoll("changed", kriterium.id)
            db.session.commit()
            flash(f"„{kriterium.bezeichnung}“ wurde gespeichert.")
            return redirect(url_for("criteria.catalog", bogen=kriterium.bogen))
        db.session.rollback()
    return render_template("admin_criterion_form.html", boegen=BOEGEN, bogen=kriterium.bogen,
                           kriterium=kriterium, typen=TYPEN, probleme=probleme,
                           gruppen=_gruppen(kriterium.bogen), typ_gesperrt=gesperrt)


@criteria_bp.post("/eintrag/<int:kriterium_id>/verschieben")
@role_required(ROLLEN)
def move(kriterium_id):
    kriterium = _kriterium_oder_404(kriterium_id)
    richtung = request.form.get("richtung")
    if richtung not in {"hoch", "runter"}:
        abort(400)
    liste = service.kriterien(kriterium.bogen, nur_aktive=False)
    stelle = liste.index(kriterium)
    ziel = stelle - 1 if richtung == "hoch" else stelle + 1
    if 0 <= ziel < len(liste):
        liste[stelle], liste[ziel] = liste[ziel], liste[stelle]
        # Ein verschobenes Kriterium übernimmt die Gruppe seines neuen Nachbarn
        # nicht -- die Gruppe ändert man im Bearbeiten-Dialog.
        for index, eintrag in enumerate(liste):
            eintrag.reihenfolge = (index + 1) * 10
        _protokoll("moved", kriterium.id)
        db.session.commit()
    return redirect(url_for("criteria.catalog", bogen=kriterium.bogen) + f"#k{kriterium.id}")


@criteria_bp.post("/eintrag/<int:kriterium_id>/aktiv")
@role_required(ROLLEN)
def toggle(kriterium_id):
    kriterium = _kriterium_oder_404(kriterium_id)
    kriterium.aktiv = not kriterium.aktiv
    _protokoll("enabled" if kriterium.aktiv else "disabled", kriterium.id)
    db.session.commit()
    flash(f"„{kriterium.bezeichnung}“ ist jetzt {'eingeschaltet' if kriterium.aktiv else 'abgeschaltet'}.")
    return redirect(url_for("criteria.catalog", bogen=kriterium.bogen) + f"#k{kriterium.id}")


@criteria_bp.post("/eintrag/<int:kriterium_id>/loeschen")
@role_required(ROLLEN)
def delete(kriterium_id):
    kriterium = _kriterium_oder_404(kriterium_id)
    bogen = kriterium.bogen
    if service.hat_werte(kriterium.id):
        flash("Für dieses Kriterium sind schon Werte erfasst. Es lässt sich nur abschalten, "
              "damit die Werte erhalten bleiben.", "error")
        return redirect(url_for("criteria.catalog", bogen=bogen))
    name = kriterium.bezeichnung
    _protokoll("deleted", kriterium.id)
    db.session.delete(kriterium)
    db.session.commit()
    flash(f"„{name}“ wurde gelöscht.")
    return redirect(url_for("criteria.catalog", bogen=bogen))

