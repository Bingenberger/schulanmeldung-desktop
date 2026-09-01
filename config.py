"""Central configuration for SL-Office."""

from __future__ import annotations

import os
import secrets
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _development_secret() -> str:
    configured = os.getenv("SL_OFFICE_SECRET_KEY")
    if configured:
        return configured

    secret_path = BASE_DIR / "instance" / ".development-secret-key"
    secret_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return secret_path.read_text(encoding="ascii").strip()
    secret = secrets.token_hex(32)
    with os.fdopen(descriptor, "w", encoding="ascii") as secret_file:
        secret_file.write(secret)
    return secret


class BaseConfig:
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = int(os.getenv("SL_OFFICE_MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
    MAX_EXCEL_IMPORT_BYTES = int(os.getenv("SL_OFFICE_MAX_EXCEL_BYTES", 5 * 1024 * 1024))
    UPLOAD_FOLDER = os.getenv("SL_OFFICE_UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.getenv("SL_OFFICE_SESSION_MINUTES", "60")))
    # Flask-WTF passes this value to itsdangerous, which expects seconds (int),
    # not a datetime.timedelta.
    WTF_CSRF_TIME_LIMIT = int(os.getenv("SL_OFFICE_CSRF_SECONDS", "7200"))
    AUTO_CREATE_DB = False
    #: Anzahl vorgeschalteter Reverse Proxys. 0 = direkt erreichbar, dann
    #: werden X-Forwarded-*-Kopfzeilen ignoriert. Hinter nginx: 1.
    TRUSTED_PROXIES = int(os.getenv("SL_OFFICE_TRUSTED_PROXIES", "0"))
    MAIL_SERVER = os.getenv("SL_OFFICE_MAIL_SERVER", "localhost")
    MAIL_PORT = int(os.getenv("SL_OFFICE_MAIL_PORT", "25"))
    MAIL_USE_TLS = _env_bool("SL_OFFICE_MAIL_USE_TLS", False)
    MAIL_USERNAME = os.getenv("SL_OFFICE_MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("SL_OFFICE_MAIL_PASSWORD")
    MAIL_FROM = os.getenv("SL_OFFICE_MAIL_FROM", "noreply@example.invalid")
    #: Empfänger der Terminbenachrichtigungen an die Schule. Leer = kein Versand.
    #: Ohne eigene Angabe geht die Nachricht an die Kontaktadresse der Schule.
    NOTIFY_MAIL = os.getenv("SL_OFFICE_NOTIFY_MAIL", "")
    #: Vorlauf der Terminerinnerung an die Eltern, in Stunden.
    APPOINTMENT_REMINDER_HOURS = int(os.getenv("SL_OFFICE_REMINDER_HOURS", "24"))
    #: Öffentliche Adresse der Anwendung, z. B. "https://anmeldung.example.de".
    #: Nur der Erinnerungsdienst braucht sie: er läuft ohne Anfrage und kann
    #: den Link zum Elternbereich sonst nicht bauen.
    PUBLIC_BASE_URL = os.getenv("SL_OFFICE_PUBLIC_BASE_URL", "").rstrip("/")

    # Briefkopf der Elternschreiben. Die Vorgaben entsprechen der Schulvorlage
    # "Einladung_Schulanmeldung.odt"; jede Zeile ist per Umgebungsvariable
    # überschreibbar, leere Angaben werden im Brief einfach weggelassen.
    SCHOOL_NAME = os.getenv("SL_OFFICE_SCHOOL_NAME", "Gemeinschaftsgrundschule Niederkassel")
    SCHOOL_MOTTO = os.getenv("SL_OFFICE_SCHOOL_MOTTO", "zusammen · leben · lernen")
    SCHOOL_STREET = os.getenv("SL_OFFICE_SCHOOL_STREET", "Annostraße 3")
    SCHOOL_CITY_LINE = os.getenv("SL_OFFICE_SCHOOL_CITY_LINE", "53859 Niederkassel")
    SCHOOL_PHONE = os.getenv("SL_OFFICE_SCHOOL_PHONE", "(02208) 3761")
    SCHOOL_EMAIL = os.getenv("SL_OFFICE_SCHOOL_EMAIL", "info@ggs-niederkassel.de")
    SCHOOL_WEB = os.getenv("SL_OFFICE_SCHOOL_WEB", "www.ggs-niederkassel.de")
    #: Ortsangabe der Datumszeile und des Anmeldescheins ("Stadt ...").
    SCHOOL_TOWN = os.getenv("SL_OFFICE_SCHOOL_TOWN", "Niederkassel")
    #: Einzugsbereich, wie er im Brieftext genannt wird.
    SCHOOL_DISTRICT = os.getenv("SL_OFFICE_SCHOOL_DISTRICT", "Niederkassel-Ort")
    SCHOOL_HEALTH_OFFICE = os.getenv("SL_OFFICE_SCHOOL_HEALTH_OFFICE", "Gesundheitsamtes Siegburg")
    SCHOOL_CONTACT_MAIL = os.getenv("SL_OFFICE_SCHOOL_CONTACT_MAIL", "emrich-foerster@ggs-ndk.de")
    SCHOOL_HEAD = os.getenv("SL_OFFICE_SCHOOL_HEAD", "F. Emrich-Förster, Schulleiter")
    #: Einzeilige Anschrift; wird genutzt, wenn Straße und Ort nicht gesetzt sind.
    SCHOOL_ADDRESS = os.getenv("SL_OFFICE_SCHOOL_ADDRESS", "")
    SCHOOL_LOGO = os.getenv("SL_OFFICE_SCHOOL_LOGO", str(BASE_DIR / "assets/briefkopf/logo.png"))
    SCHOOL_SIGNATURE = os.getenv(
        "SL_OFFICE_SCHOOL_SIGNATURE", str(BASE_DIR / "assets/briefkopf/unterschrift.png"))
    MAIL_SUPPRESS_SEND = False


class DevelopmentConfig(BaseConfig):
    ENV_NAME = "development"
    SECRET_KEY = None
    SQLALCHEMY_DATABASE_URI = os.getenv("SL_OFFICE_DATABASE_URL", "sqlite:///database.db")
    AUTO_CREATE_DB = False


class TestingConfig(BaseConfig):
    ENV_NAME = "testing"
    TESTING = True
    SECRET_KEY = "test-only-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    AUTO_CREATE_DB = True
    MAIL_SUPPRESS_SEND = True


class ProductionConfig(BaseConfig):
    ENV_NAME = "production"
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @classmethod
    def values(cls) -> dict:
        secret_key = os.getenv("SL_OFFICE_SECRET_KEY")
        database_url = os.getenv("SL_OFFICE_DATABASE_URL")
        missing = [name for name, value in (("SL_OFFICE_SECRET_KEY", secret_key), ("SL_OFFICE_DATABASE_URL", database_url)) if not value]
        if missing:
            raise RuntimeError("Fehlende Produktionskonfiguration: " + ", ".join(missing))
        if len(secret_key) < 32:
            raise RuntimeError("SL_OFFICE_SECRET_KEY muss mindestens 32 Zeichen lang sein.")
        return {"SECRET_KEY": secret_key, "SQLALCHEMY_DATABASE_URI": database_url}


def load_config(app, environment: str | None = None) -> None:
    environment = (environment or os.getenv("SL_OFFICE_ENV", "development")).lower()
    configurations = {"development": DevelopmentConfig, "testing": TestingConfig, "production": ProductionConfig}
    try:
        config_class = configurations[environment]
    except KeyError as exc:
        raise RuntimeError(f"Unbekannte SL_OFFICE_ENV: {environment}") from exc
    app.config.from_object(config_class)
    if config_class is ProductionConfig:
        app.config.update(config_class.values())
    elif config_class is DevelopmentConfig:
        app.config["SECRET_KEY"] = _development_secret()
    if environment != "production":
        app.config["SESSION_COOKIE_SECURE"] = _env_bool("SL_OFFICE_SECURE_COOKIES", app.config["SESSION_COOKIE_SECURE"])
