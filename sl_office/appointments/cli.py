"""CLI-Einstieg für den Erinnerungsdienst.

Der Webdienst läuft mit mehreren gunicorn-Arbeitsprozessen; ein Scheduler im
Prozess würde jede Erinnerung darum mehrfach verschicken. Der Versand hängt
stattdessen an einem eigenen Aufruf, den ein systemd-Timer stündlich startet.
"""

import click
from flask.cli import with_appcontext

from sl_office.appointments import notifications


def _run(app):
    """Erinnerungen verschicken, wenn nötig mit gestelltem Anfragekontext.

    Ohne Anfrage lässt sich kein absoluter Link in den Elternbereich bauen;
    ``PUBLIC_BASE_URL`` liefert die Adresse dafür nach.
    """
    base_url = app.config.get("PUBLIC_BASE_URL")
    if base_url:
        with app.test_request_context(base_url=base_url):
            return notifications.send_due_reminders(app)
    return notifications.send_due_reminders(app)


def register_cli(app):
    @app.cli.command("appointment-reminders")
    @with_appcontext
    def appointment_reminders():
        """Fällige Terminerinnerungen an die Eltern verschicken."""
        sent, skipped = _run(app)
        click.echo(f"Erinnerungen verschickt: {sent}, ohne Elternzugang übersprungen: {skipped}")
