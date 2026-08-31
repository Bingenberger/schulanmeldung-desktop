"""Small SMTP adapter; token values are only ever placed in the message link."""
import smtplib
from email.message import EmailMessage


def send_parent_login_link(app, recipient, link, child_name):
    if app.config.get("MAIL_SUPPRESS_SEND", False):
        app.logger.info("Parent login mail suppressed", extra={"recipient": recipient})
        return
    message = EmailMessage()
    message["Subject"] = "Ihr Zugangslink zur Schulanmeldung"
    message["From"] = app.config["MAIL_FROM"]
    message["To"] = recipient
    message.set_content(
        f"Guten Tag,\n\nüber diesen Link erreichen Sie den Elternbereich für {child_name}:\n{link}\n\n"
        "Der Link ist 15 Minuten gültig und kann nur einmal verwendet werden.\n"
    )
    with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=10) as smtp:
        if app.config.get("MAIL_USE_TLS"):
            smtp.starttls()
        if app.config.get("MAIL_USERNAME"):
            smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
        smtp.send_message(message)
