"""Scoping of all student data to one school year.

Every enrolment year is worked on separately. Rather than adding a filter to
each of the ~100 queries in this application -- where one forgotten filter
would silently mix years -- the scope is applied once, centrally, through a
SQLAlchemy ``do_orm_execute`` hook. It covers plain selects, joins, aggregates,
``get()`` by primary key and lazily loaded relationships alike.

The chosen year lives in the user's session, so one person can look at an
earlier year while colleagues keep working in the current one.
"""

from contextlib import contextmanager

from flask import g, has_request_context, session
import datetime

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, with_loader_criteria

from models import (
    AOSF, Diagnostik, Einschulungsjahr, Rueckstellung, SchulaerztlicheUntersuchung,
    SchulspielDiagnostik, Schueler, db,
)

SESSION_KEY = "active_school_year"
#: Flask-Login stores the signed-in staff user under this session key.
STAFF_SESSION_KEY = "_user_id"
#: Execution option that deliberately lifts the scope (backups, year admin).
UNSCOPED = "sl_office_all_years"

#: Records that belong to a child and therefore inherit its year. Each entry is
#: the model plus the column pointing at ``schueler.id``.
DEPENDENT_MODELS = (
    Diagnostik, SchulspielDiagnostik, SchulaerztlicheUntersuchung, AOSF, Rueckstellung,
)


def current_year_row():
    """The year the school is officially working on."""
    return db.session.scalar(
        select(Einschulungsjahr).where(Einschulungsjahr.ist_aktuell.is_(True))
        .execution_options(**{UNSCOPED: True})
    )


def all_years():
    return db.session.scalars(
        select(Einschulungsjahr).order_by(Einschulungsjahr.jahr.desc())
        .execution_options(**{UNSCOPED: True})
    ).all()


_UNSET = object()


def active_year():
    """Year selected for this session, falling back to the current one.

    Only staff sessions are scoped. The parent portal keeps its own session and
    reaches exactly one child through an access token, so scoping it would hide
    that child as soon as the school moves on to the next year.
    """
    if not has_request_context():
        return None
    # Read the session key Flask-Login writes, never ``current_user``: touching
    # the proxy would load the user through a query, which re-enters this hook.
    if not session.get(STAFF_SESSION_KEY):
        return None
    cached = getattr(g, "sl_office_active_year", _UNSET)
    if cached is not _UNSET:
        return cached
    year = session.get(SESSION_KEY)
    if year is None:
        row = current_year_row()
        year = row.jahr if row else None
    g.sl_office_active_year = year
    return year


def set_active_year(jahr):
    session[SESSION_KEY] = jahr
    g.sl_office_active_year = jahr


def clear_active_year():
    """Return to the current year for this session."""
    session.pop(SESSION_KEY, None)
    if has_request_context():
        g.pop("sl_office_active_year", None)


def active_year_row():
    year = active_year()
    if year is None:
        return None
    return db.session.scalar(
        select(Einschulungsjahr).where(Einschulungsjahr.jahr == year)
        .execution_options(**{UNSCOPED: True})
    )


def is_readonly():
    """True while the session looks at a locked (closed) year."""
    row = active_year_row()
    return bool(row and row.gesperrt)


def is_current_year():
    row = current_year_row()
    return row is not None and row.jahr == active_year()


@contextmanager
def all_years_scope():
    """Temporarily see every year -- for backups and year administration."""
    token = getattr(g, "sl_office_scope_off", False) if has_request_context() else False
    if has_request_context():
        g.sl_office_scope_off = True
    try:
        yield
    finally:
        if has_request_context():
            g.sl_office_scope_off = token


def _scope_disabled():
    return has_request_context() and getattr(g, "sl_office_scope_off", False)


def install(app):
    """Register the query hook. Safe to call once per application."""

    @event.listens_for(Session, "do_orm_execute")
    def _apply_year_scope(state):
        if not state.is_select or state.execution_options.get(UNSCOPED):
            return
        if state.execution_options.get("is_column_load") or state.is_relationship_load:
            # Refreshing an already loaded row must not be filtered away.
            return
        if _scope_disabled():
            return
        year = active_year()
        if year is None:
            return
        options = [with_loader_criteria(
            Schueler, lambda cls: cls.einschulungsjahr == year, include_aliases=True
        )]
        for model in DEPENDENT_MODELS:
            options.append(with_loader_criteria(
                model,
                lambda cls: cls.schueler_id.in_(
                    select(Schueler.id).where(Schueler.einschulungsjahr == year)
                ),
                include_aliases=True,
            ))
        state.statement = state.statement.options(*options)

    @event.listens_for(Schueler, "before_insert")
    def _stamp_year(mapper, connection, target):
        """New children belong to the year currently being worked on.

        Done centrally so no import or form has to remember it. The lookups go
        through the raw connection: this runs inside a flush, where issuing
        further ORM queries would be re-entrant.
        """
        if target.einschulungsjahr is not None:
            return
        year = None
        if has_request_context() and session.get(STAFF_SESSION_KEY):
            year = session.get(SESSION_KEY)
        if year is None:
            year = connection.scalar(
                text("SELECT jahr FROM einschulungsjahr WHERE ist_aktuell = 1 LIMIT 1"))
        if year is None:
            year = connection.scalar(text("SELECT einschulungsjahr FROM global_settings LIMIT 1"))
        # Enrolment is always for an upcoming year; used only when nothing is
        # configured at all, which the migration prevents in practice.
        target.einschulungsjahr = year or (datetime.date.today().year + 1)

    app.extensions["sl_office_year_scope"] = _apply_year_scope
    return _apply_year_scope


#: Endpoints that must keep working even in a locked year: leaving the year,
#: logging out, and the year administration itself.
READONLY_EXEMPT_ENDPOINTS = frozenset({
    "admin.school_years", "admin.switch_year", "admin.unlock_year",
    "auth.logout", "auth.login", "auth.login_two_factor", "auth.setup_two_factor",
    "auth.security", "auth.change_password",
    # Das Schulprofil gilt für alle Jahrgänge, nicht für den geöffneten.
    "admin.school_profile", "admin.school_profile_image", "admin.modules",
})


def install_readonly_guard(app):
    """Reject writes while a closed year is selected.

    A banner alone would not be enough -- this blocks the request itself, so no
    route needs to remember the rule.
    """
    from flask import flash, redirect, request, url_for

    @app.before_request
    def _block_writes_in_closed_years():
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        if request.blueprint == "parent_portal":
            return None
        if request.endpoint in READONLY_EXEMPT_ENDPOINTS:
            return None
        if not is_readonly():
            return None
        flash("Dieser Jahrgang ist abgeschlossen und schreibgeschützt. "
              "Zum Ändern zuerst im Bereich „Einschulungsjahre“ entsperren.", "error")
        return redirect(request.referrer or url_for("index"))


def install_template_context(app):
    """Expose the active year to every template, for the banner and badge."""

    @app.context_processor
    def _year_context():
        current = current_year_row()
        return {
            "active_school_year": active_year(),
            "current_school_year": current.jahr if current else None,
            "on_current_school_year": is_current_year(),
            "school_year_readonly": is_readonly(),
        }
