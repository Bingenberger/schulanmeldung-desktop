"""Abschaltbare Teile der Anwendung (Module)."""

from flask import abort


def install_template_context(app):
    @app.context_processor
    def _features():
        return {"two_factor_required": bool(app.config.get("TWO_FACTOR_REQUIRED", True)),
                "modules": _LazyModules(app)}


class _LazyModules:
    """``modules.schulspiel`` im Template; gefragt wird erst beim Zugriff.

    So kostet eine Seite ohne Modulbezug keine Abfrage, und eine Seite mit
    vielen Abfragen nur eine.
    """

    def __init__(self, app):
        self._app, self._states = app, None

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        if self._states is None:
            self._states = module_states(self._app)
        if key not in self._states:
            raise AttributeError(key)
        return self._states[key]


# --- Module -------------------------------------------------------------------
#
# Nicht jede Schule braucht jeden Teil: die eine macht kein Schulspiel, die
# andere bildet ihre Klassen anders. Jedes Modul lässt sich unter
# „Verwaltung → Module“ abschalten. Abgeschaltet verschwindet es aus Menü,
# Startseite und Schülerakte, und seine Seiten antworten mit 404. Die Daten
# bleiben stehen; eingeschaltet ist alles wieder da.

class Module:
    def __init__(self, key, label, description, endpoints=(), prefixes=(), targets=()):
        self.key, self.label, self.description = key, label, description
        self.endpoints, self.prefixes, self.targets = set(endpoints), tuple(prefixes), set(targets)

    def covers(self, endpoint, view_args):
        if not endpoint:
            return False
        if endpoint in self.endpoints or endpoint.startswith(self.prefixes):
            return True
        return endpoint == "students.select" and (view_args or {}).get("target") in self.targets


MODULES = (
    Module("termine", "Terminplanung",
           "Gesprächstermine planen, Kindern zuweisen und Laufzettel drucken.",
           prefixes=("appointments.",)),
    Module("diagnostik", "Pädagogische Diagnostik",
           "Beobachtungen beim Anmeldegespräch erfassen.",
           endpoints=("diagnostik",), targets=("diagnostik",)),
    Module("schulspiel", "Schulspiel", "Diagnostikbogen zum Schulspiel erfassen.",
           endpoints=("schulspiel",), targets=("schulspiel",)),
    Module("schularzt", "Schulärztliche Untersuchung",
           "Ergebnisse der Untersuchung und das Gutachten erfassen.",
           endpoints=("schularzt",), targets=("schularzt",)),
    Module("kita_bericht", "Bericht der Kita", "Einschätzung der Kita erfassen.",
           endpoints=("kita_bericht",), targets=("kita_bericht",)),
    Module("aosf", "AO-SF-Verfahren", "Verdacht und Verfahren auf sonderpädagogische Förderung.",
           endpoints=("aosf_prozess",)),
    Module("rueckstellung", "Rückstellung", "Empfehlung und Verfahren zur Zurückstellung.",
           endpoints=("rueckstellung_prozess",)),
    Module("klassenbildung", "Klassenbildung",
           "Freundeswünsche erfassen, Klassen zusammenstellen, Klassenmappe und -listen.",
           endpoints=("klassenzusammensetzung", "klassen_assign", "export_klassenmappe",
                      "export_klassenlisten", "freunde_bearbeiten"),
           targets=("freunde",)),
    Module("foerderkurse", "Förderkurse", "Kinder Förderkursen zuordnen und Listen drucken.",
           endpoints=("foerderkurse_einzel", "foerderkurse_bulk", "export_foerderkurse")),
    Module("betreuung", "Betreuung", "Betreuungsform (OGS, Übermittag, Abholung) festlegen.",
           endpoints=("betreuung_bulk",)),
)
MODULE_KEYS = tuple(module.key for module in MODULES)


def module_states(app=None):
    """{Schlüssel: an?} für alle Module; gespeicherte Schalter gehen vor."""
    from sl_office.school_profile import Schulprofil
    from models import db

    states = {module.key: True for module in MODULES}
    rows = db.session.scalars(db.select(Schulprofil).where(
        Schulprofil.key.in_([_storage_key(key) for key in MODULE_KEYS])))
    for row in rows:
        states[row.key.split(":", 1)[1]] = row.wert == "1"
    return states


def module_enabled(key):
    if key not in MODULE_KEYS:
        raise KeyError(key)
    return module_states()[key]


def save_module_states(enabled_keys):
    """Schalter speichern; alles, was nicht in ``enabled_keys`` steht, ist aus."""
    from sl_office.school_profile import Schulprofil
    from models import db

    for key in MODULE_KEYS:
        row = db.session.get(Schulprofil, _storage_key(key))
        if row is None:
            row = Schulprofil(key=_storage_key(key))
            db.session.add(row)
        row.wert = "1" if key in enabled_keys else "0"


def _storage_key(key):
    # Die Schalter liegen in der Tabelle des Schulprofils, eine Zeile je Modul.
    return f"modul:{key}"


def install_module_guard(app):
    @app.before_request
    def _block_disabled_modules():
        from flask import request
        module = next((m for m in MODULES if m.covers(request.endpoint, request.view_args)), None)
        if module is not None and not module_states(app)[module.key]:
            abort(404)
