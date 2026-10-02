"""Abschaltbare Teile der Anwendung.

Die Desktop-Fassung läuft auf einem Rechner in der Schule und ist aus dem
Internet nicht erreichbar. Das Elternportal ergibt dort keinen Sinn und ist
darum ohne ``SL_OFFICE_PARENT_PORTAL=1`` abgeschaltet: seine Seiten werden
nicht geladen, die Verwaltung blendet die Punkte dazu aus und die Elternbriefe
kommen ohne Zugangslinks aus.
"""

from functools import wraps

from flask import abort, current_app, has_app_context


def parent_portal_enabled(app=None):
    """Ob das Elternportal in dieser Anwendung läuft."""
    if app is None:
        if not has_app_context():
            return False
        app = current_app
    return bool(app.config.get("PARENT_PORTAL_ENABLED"))


def portal_required(view):
    """Verwaltungsseiten, die nur mit Elternportal einen Sinn haben."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not parent_portal_enabled():
            abort(404)
        return view(*args, **kwargs)
    return wrapper


def install_template_context(app):
    @app.context_processor
    def _features():
        return {"parent_portal_enabled": parent_portal_enabled(app)}
