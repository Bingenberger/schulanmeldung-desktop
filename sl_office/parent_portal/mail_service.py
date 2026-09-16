"""Small SMTP adapter; token values are only ever placed in the message link.

Die Nachrichten selbst werden anderswo gebaut -- Terminmails etwa in
:mod:`sl_office.appointments.notifications`. Hier steht nur der Versandweg.
"""
import smtplib
from email.message import EmailMessage


def send_message(app, message):
    """Eine fertige Nachricht zustellen.

    Liefert ``True``, wenn sie das SMTP-Gespräch erreicht hat, und ``False``,
    wenn der Versand konfigurationsbedingt unterdrückt ist (Testbetrieb).
    """
    if app.config.get("MAIL_SUPPRESS_SEND", False):
        app.logger.info("Mail suppressed", extra={"recipient": message["To"]})
        return False
    with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=10) as smtp:
        if app.config.get("MAIL_USE_TLS"):
            smtp.starttls()
        if app.config.get("MAIL_USERNAME"):
            smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
        smtp.send_message(message)
    return True


def build_message(app, recipient, subject, body):
    """Grundgerüst einer Textnachricht mit dem konfigurierten Absender."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = app.config["MAIL_FROM"]
    message["To"] = recipient
    message.set_content(body)
    return message


def attach_pdf(message, filename, payload):
    """Ein PDF an eine fertige Nachricht hängen."""
    message.add_attachment(payload, maintype="application", subtype="pdf", filename=filename)
    return message


def staff_recipient(app):
    """Adresse der Schule für Benachrichtigungen; ohne Angabe geht nichts raus."""
    return app.config.get("NOTIFY_MAIL") or app.config.get("SCHOOL_CONTACT_MAIL") or ""


def send_parent_login_link(app, recipient, link, child_name, start_url=""):
    zeilen = [
        "Guten Tag,",
        "",
        f"über diesen Link melden Sie sich im Elternportal für {child_name} an:",
        link,
        "",
        "Der Link ist 15 Minuten gültig und funktioniert nur einmal. Ein Passwort "
        "brauchen Sie nicht.",
    ]
    if start_url:
        zeilen += [
            "",
            "Wenn Sie sich später wieder anmelden möchten, geben Sie einfach auf",
            start_url,
            "Ihre E-Mail-Adresse ein – Sie bekommen dann einen neuen Link.",
        ]
    zeilen += [
        "",
        "Falls Sie keinen Anmeldelink angefordert haben, können Sie diese Nachricht "
        "ignorieren.",
        "",
    ]
    return send_message(app, build_message(
        app, recipient, "Ihr Anmeldelink zum Elternportal", "\n".join(zeilen)))
