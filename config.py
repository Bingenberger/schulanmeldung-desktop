"""Central configuration for SL-Office."""

from __future__ import annotations

import os
import secrets
import sys
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
    #: Zweiter Faktor (Authenticator-App) bei der Anmeldung. Auf dem Server
    #: Pflicht; die Desktop-Fassung lauscht in der Regel nur auf diesem Rechner
    #: und kommt mit Benutzername und Passwort aus.
    TWO_FACTOR_REQUIRED = _env_bool("SL_OFFICE_TWO_FACTOR", True)

    # Briefkopf der Elternschreiben. Die Schule pflegt die Angaben unter
    # „Verwaltung → Schulprofil“ (siehe sl_office.school_profile); was dort
    # nicht gespeichert ist, kommt aus diesen Umgebungsvariablen. Leere
    # Angaben werden im Brief einfach weggelassen.
    SCHOOL_NAME = os.getenv("SL_OFFICE_SCHOOL_NAME", "")
    SCHOOL_MOTTO = os.getenv("SL_OFFICE_SCHOOL_MOTTO", "")
    SCHOOL_STREET = os.getenv("SL_OFFICE_SCHOOL_STREET", "")
    SCHOOL_CITY_LINE = os.getenv("SL_OFFICE_SCHOOL_CITY_LINE", "")
    SCHOOL_PHONE = os.getenv("SL_OFFICE_SCHOOL_PHONE", "")
    SCHOOL_EMAIL = os.getenv("SL_OFFICE_SCHOOL_EMAIL", "")
    SCHOOL_WEB = os.getenv("SL_OFFICE_SCHOOL_WEB", "")
    #: Ortsangabe der Datumszeile und des Anmeldescheins ("Stadt ...").
    SCHOOL_TOWN = os.getenv("SL_OFFICE_SCHOOL_TOWN", "")
    #: Einzugsbereich, wie er im Brieftext genannt wird.
    SCHOOL_DISTRICT = os.getenv("SL_OFFICE_SCHOOL_DISTRICT", "")
    SCHOOL_HEALTH_OFFICE = os.getenv("SL_OFFICE_SCHOOL_HEALTH_OFFICE", "")
    SCHOOL_CONTACT_MAIL = os.getenv("SL_OFFICE_SCHOOL_CONTACT_MAIL", "")
    SCHOOL_HEAD = os.getenv("SL_OFFICE_SCHOOL_HEAD", "")
    #: Einzeilige Anschrift; wird genutzt, wenn Straße und Ort nicht gesetzt sind.
    SCHOOL_ADDRESS = os.getenv("SL_OFFICE_SCHOOL_ADDRESS", "")
    #: Pfade zu Logo und Unterschrift; im Schulprofil hochgeladene Bilder gehen vor.
    SCHOOL_LOGO = os.getenv("SL_OFFICE_SCHOOL_LOGO", "")
    SCHOOL_SIGNATURE = os.getenv("SL_OFFICE_SCHOOL_SIGNATURE", "")


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


def desktop_data_dir() -> Path:
    """Wo die Desktop-Fassung ihre Daten ablegt.

    Unter Windows ``%APPDATA%\\SL-Office`` -- das gehört der angemeldeten
    Person und wird von der Schul-IT meist mitgesichert. Unter macOS
    ``~/Library/Application Support/SL-Office``. ``SL_OFFICE_DATA_DIR`` legt
    einen anderen Ort fest, etwa ein Netzlaufwerk.
    """
    configured = os.getenv("SL_OFFICE_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt" and os.getenv("APPDATA"):
        return Path(os.environ["APPDATA"]) / "SL-Office"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SL-Office"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "sl-office"


def _persistent_secret(path: Path) -> str:
    """Schlüssel für Sitzungen und CSRF, einmal erzeugt und dann beibehalten."""
    try:
        return path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        secret = secrets.token_hex(32)
        path.write_text(secret, encoding="ascii")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return secret


class DesktopConfig(BaseConfig):
    """Eigenständige Anwendung auf einem Schulrechner (siehe desktop.py).

    Der Server lauscht auf 127.0.0.1, auf Wunsch im lokalen Netz (siehe
    desktop.py); Datenbank, hochgeladene Dateien, Sicherungen und der
    Sitzungsschlüssel liegen im Datenordner.
    """
    ENV_NAME = "desktop"
    AUTO_CREATE_DB = False
    TWO_FACTOR_REQUIRED = _env_bool("SL_OFFICE_TWO_FACTOR", False)
    #: Lokal gibt es keinen Grund für knappe Grenzen: eine Datenbank zum
    #: Zurückspielen darf größer sein.
    MAX_CONTENT_LENGTH = int(os.getenv("SL_OFFICE_MAX_UPLOAD_BYTES", 200 * 1024 * 1024))
    FIRST_RUN_SETUP = True
    SESSION_COOKIE_SECURE = False

    @classmethod
    def values(cls) -> dict:
        data_dir = desktop_data_dir()
        for folder in (data_dir, data_dir / "uploads", data_dir / "Datensicherungen"):
            folder.mkdir(parents=True, exist_ok=True)
        return {
            "DATA_DIR": str(data_dir),
            "SECRET_KEY": os.getenv("SL_OFFICE_SECRET_KEY") or _persistent_secret(data_dir / ".secret-key"),
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(data_dir / "sl-office.db").replace("\\", "/"),
            "UPLOAD_FOLDER": str(data_dir / "uploads"),
            "BACKUP_FOLDER": str(data_dir / "Datensicherungen"),
        }


def load_config(app, environment: str | None = None) -> None:
    environment = (environment or os.getenv("SL_OFFICE_ENV", "development")).lower()
    configurations = {"development": DevelopmentConfig, "testing": TestingConfig,
                      "production": ProductionConfig, "desktop": DesktopConfig}
    try:
        config_class = configurations[environment]
    except KeyError as exc:
        raise RuntimeError(f"Unbekannte SL_OFFICE_ENV: {environment}") from exc
    app.config.from_object(config_class)
    if config_class in (ProductionConfig, DesktopConfig):
        app.config.update(config_class.values())
    elif config_class is DevelopmentConfig:
        app.config["SECRET_KEY"] = _development_secret()
    if environment not in ("production", "desktop"):
        app.config["SESSION_COOKIE_SECURE"] = _env_bool("SL_OFFICE_SECURE_COOKIES", app.config["SESSION_COOKIE_SECURE"])
