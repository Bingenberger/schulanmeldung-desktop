"""Die Desktop-Fassung: Datenordner, Ersteinrichtung, Start und Sicherung."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

import desktop  # noqa: E402
from app import create_app  # noqa: E402
from config import desktop_data_dir  # noqa: E402
from models import User, db  # noqa: E402

os.environ["SL_OFFICE_ENV"] = "testing"     # desktop.py setzt "desktop" nur als Vorgabe


class _DataDir:
    def setUp(self):
        self.data_dir = Path(tempfile.mkdtemp(prefix="sl-office-test-"))
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {"SL_OFFICE_DATA_DIR": str(self.data_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)


class DesktopConfigTests(_DataDir, unittest.TestCase):

    def test_everything_lives_in_the_data_directory(self):
        app = create_app("desktop")
        self.assertEqual(desktop_data_dir(), self.data_dir)
        self.assertEqual(app.config["UPLOAD_FOLDER"], str(self.data_dir / "uploads"))
        self.assertEqual(app.config["BACKUP_FOLDER"], str(self.data_dir / "Datensicherungen"))
        self.assertTrue(app.config["SQLALCHEMY_DATABASE_URI"].endswith("sl-office.db"))
        self.assertFalse(app.config["TWO_FACTOR_REQUIRED"])
        self.assertEqual(app.config["ENV_NAME"], "desktop")

    def test_macos_keeps_its_data_in_application_support(self):
        with mock.patch.dict(os.environ), mock.patch("config.sys.platform", "darwin"), \
                mock.patch("config.os.name", "posix"):
            del os.environ["SL_OFFICE_DATA_DIR"]
            self.assertEqual(desktop_data_dir(),
                             Path.home() / "Library" / "Application Support" / "SL-Office")

    def test_the_secret_key_survives_a_restart(self):
        erster = create_app("desktop").config["SECRET_KEY"]
        zweiter = create_app("desktop").config["SECRET_KEY"]
        self.assertEqual(erster, zweiter)
        self.assertGreaterEqual(len(erster), 32)

    def test_the_schema_comes_from_the_migrations(self):
        app = create_app("desktop")
        desktop.datenbank_aktualisieren(app)
        with app.app_context():
            tabellen = set(db.inspect(db.engine).get_table_names())
        self.assertTrue({"schueler", "kriterium", "kriterium_wert", "schulprofil"} <= tabellen)
        # Ein zweiter Start ändert nichts mehr.
        desktop.datenbank_aktualisieren(app)

    def test_the_daily_backup_is_made_once_and_pruned(self):
        app = create_app("desktop")
        desktop.datenbank_aktualisieren(app)
        self.assertIsNotNone(desktop.taegliche_sicherung(app))
        self.assertIsNone(desktop.taegliche_sicherung(app))
        ordner = self.data_dir / "Datensicherungen"
        # Eine von Hand angelegte Sicherung bleibt beim Ausdünnen stehen.
        (ordner / "2020-01-01_0800").mkdir()
        for tag in range(20):
            (ordner / f"auto-2020-02-{tag + 1:02d}_0800").mkdir()
        for eintrag in list(ordner.iterdir()):
            if eintrag.name.startswith("auto-") and not any(eintrag.iterdir()):
                (eintrag / "database.db").write_bytes(b"")
        with mock.patch.object(desktop.datetime, "date") as datum:
            datum.today.return_value.isoformat.return_value = "2099-01-01"
            desktop.taegliche_sicherung(app)
        namen = {eintrag.name for eintrag in ordner.iterdir()}
        self.assertIn("2020-01-01_0800", namen)
        self.assertEqual(sum(name.startswith("auto-") for name in namen), desktop.SICHERUNGEN_BEHALTEN)

    def test_no_running_instance_is_found_in_an_empty_folder(self):
        self.assertIsNone(desktop.laufende_instanz(self.data_dir))
        (self.data_dir / "laufende-instanz.json").write_text('{"port": 1}', encoding="utf-8")
        self.assertIsNone(desktop.laufende_instanz(self.data_dir))

    def test_a_free_port_is_found(self):
        port = desktop.freier_port()
        self.assertIsInstance(port, int)
        self.assertGreater(port, 0)


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing", {"FIRST_RUN_SETUP": True})
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_every_page_leads_to_the_setup_until_an_account_exists(self):
        self.assertIn("/einrichtung", self.client.get("/").headers["Location"])
        self.assertIn("/einrichtung", self.client.get("/login").headers["Location"])
        self.assertEqual(self.client.get("/lebenszeichen").get_data(as_text=True), "SL-Office")
        self.assertEqual(self.client.get("/static/vendor/bootstrap/LICENSE").status_code, 200)

    def test_the_first_account_is_an_administrator(self):
        response = self.client.post("/einrichtung", data={
            "username": "leitung", "new_password": "lang-genug-123", "confirm_password": "lang-genug-123"})
        self.assertIn("/login", response.headers["Location"])
        with self.app.app_context():
            user = User.query.one()
            self.assertEqual((user.username, user.role), ("leitung", "Administrator"))
        # Danach ist die Einrichtung gesperrt.
        self.assertIn("/login", self.client.get("/einrichtung").headers["Location"])
        self.client.post("/einrichtung", data={
            "username": "zweite", "new_password": "lang-genug-123", "confirm_password": "lang-genug-123"})
        with self.app.app_context():
            self.assertEqual(User.query.count(), 1)

    def test_short_or_mismatched_passwords_are_refused(self):
        self.client.post("/einrichtung", data={
            "username": "leitung", "new_password": "kurz", "confirm_password": "kurz"})
        self.client.post("/einrichtung", data={
            "username": "leitung", "new_password": "lang-genug-123", "confirm_password": "anders-123456"})
        with self.app.app_context():
            self.assertEqual(User.query.count(), 0)

    def test_the_server_version_has_no_setup_page(self):
        app = create_app("testing")
        with app.app_context():
            db.session.add(User(username="x", role="Administrator",
                                password_hash=generate_password_hash("x")))
            db.session.commit()
        self.assertNotIn("/einrichtung", app.test_client().get("/").headers.get("Location", ""))
        with app.app_context():
            db.drop_all()


class AssetTests(unittest.TestCase):

    def test_no_page_loads_anything_from_the_internet(self):
        root = Path(__file__).resolve().parents[1] / "templates"
        for template in root.rglob("*.html"):
            text = template.read_text(encoding="utf-8")
            self.assertNotIn("cdn.", text, template.name)
            self.assertNotIn("<script src=\"http", text, template.name)


if __name__ == "__main__":
    unittest.main()
