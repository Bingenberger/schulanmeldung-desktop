"""Datensicherungen zurückspielen und Anmeldung ohne zweiten Faktor (Desktop)."""

import os
import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

import desktop  # noqa: E402
from app import create_app  # noqa: E402
from models import Schueler, User, db  # noqa: E402
from sl_office.admin import backup_service  # noqa: E402

os.environ["SL_OFFICE_ENV"] = "testing"


class _DesktopApp:
    """Eine Desktop-Anwendung mit echter, migrierter Datenbank in einem Temp-Ordner."""

    def setUp(self):
        self.data_dir = Path(tempfile.mkdtemp(prefix="sl-office-restore-"))
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {"SL_OFFICE_DATA_DIR": str(self.data_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = create_app("desktop", {"WTF_CSRF_ENABLED": False})
        desktop.datenbank_aktualisieren(self.app)
        with self.app.app_context():
            for name, role in (("chefin", "Administrator"), ("leitung", "Schulleitung")):
                db.session.add(User(username=name, role=role,
                                    password_hash=generate_password_hash("geheim-genug-1")))
            db.session.add(Schueler(vorname="Mia", nachname="Vorher"))
            db.session.commit()
        self.addCleanup(self._dispose)

    def _dispose(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def _client(self, username="chefin"):
        client = self.app.test_client()
        response = client.post("/login", data={"username": username, "password": "geheim-genug-1"})
        self.assertIn(response.status_code, (302, 303))
        return client

    def _namen(self):
        with self.app.app_context():
            return sorted(s.vorname for s in Schueler.query.all())


class LoginWithoutSecondFactorTests(_DesktopApp, unittest.TestCase):

    def test_password_alone_logs_in_on_the_desktop(self):
        client = self.app.test_client()
        response = client.post("/login", data={"username": "chefin", "password": "geheim-genug-1"})
        self.assertNotIn("einrichten", response.headers["Location"])
        self.assertNotIn("bestaetigen", response.headers["Location"])
        self.assertEqual(client.get("/").status_code, 200)

    def test_a_wrong_password_still_fails(self):
        client = self.app.test_client()
        response = client.post("/login", data={"username": "chefin", "password": "falsch"},
                               follow_redirects=True)
        self.assertIn("Ungültiger Benutzername oder Passwort", response.get_data(as_text=True))
        self.assertIn("/login", client.get("/").headers["Location"])

    def test_the_menu_does_not_offer_two_factor_settings(self):
        page = self._client().get("/").get_data(as_text=True)
        self.assertNotIn("Anmeldesicherheit", page)

    def test_the_server_still_requires_the_second_factor(self):
        app = create_app("testing")
        with app.app_context():
            db.session.add(User(username="x", role="Administrator",
                                password_hash=generate_password_hash("geheim-genug-1")))
            db.session.commit()
        response = app.test_client().post("/login", data={"username": "x", "password": "geheim-genug-1"})
        self.assertIn("/login/einrichten", response.headers["Location"])
        with app.app_context():
            db.drop_all()


class RestoreTests(_DesktopApp, unittest.TestCase):

    def test_a_listed_backup_is_restored_with_its_documents(self):
        uploads = Path(self.app.config["UPLOAD_FOLDER"])
        (uploads / "alt.pdf").write_bytes(b"%PDF alt")
        with self.app.app_context():
            sicherung = backup_service.create_backup(self.app)
            Schueler.query.filter_by(vorname="Mia").delete()
            db.session.add(Schueler(vorname="Tom", nachname="Nachher"))
            db.session.commit()
        (uploads / "alt.pdf").unlink()
        (uploads / "neu.pdf").write_bytes(b"%PDF neu")

        client = self._client()
        response = client.post("/admin/datensicherung/wiederherstellen", data={"name": sicherung.name})
        self.assertIn("/login", response.headers["Location"])

        self.assertEqual(self._namen(), ["Mia"])
        self.assertTrue((uploads / "alt.pdf").exists())
        self.assertFalse((uploads / "neu.pdf").exists())
        # Der Stand davor liegt als eigene Sicherung bereit.
        with self.app.app_context():
            namen = [e["name"] for e in backup_service.list_backups(self.app)]
        self.assertTrue(any(n.startswith(backup_service.BEFORE_RESTORE_PREFIX) for n in namen))
        # Nach dem Zurückspielen ist die Sitzung beendet.
        self.assertIn("/login", client.get("/").headers["Location"])

    def test_the_safety_backup_undoes_a_restore(self):
        with self.app.app_context():
            frueher = backup_service.create_backup(self.app)
            db.session.add(Schueler(vorname="Tom", nachname="Nachher"))
            db.session.commit()
            sicherheit = backup_service.restore_backup(self.app, frueher.name)
        self.assertEqual(self._namen(), ["Mia"])
        with self.app.app_context():
            backup_service.restore_backup(self.app, sicherheit.name)
        self.assertEqual(self._namen(), ["Mia", "Tom"])

    def test_a_downloaded_database_can_be_uploaded(self):
        with self.app.app_context():
            payload = backup_service.database_snapshot(self.app).getvalue()
            db.session.add(Schueler(vorname="Tom", nachname="Nachher"))
            db.session.commit()
        response = self._client().post("/admin/datensicherung/wiederherstellen", data={
            "datenbank": (BytesIO(payload), "sicherung.db")}, content_type="multipart/form-data")
        self.assertIn("/login", response.headers["Location"])
        self.assertEqual(self._namen(), ["Mia"])

    def test_other_files_are_refused(self):
        client = self._client()
        for payload in (b"kein SQLite", b"SQLite format 3\x00" + b"\x00" * 200):
            response = client.post("/admin/datensicherung/wiederherstellen", data={
                "datenbank": (BytesIO(payload), "x.db")}, content_type="multipart/form-data",
                follow_redirects=True)
            self.assertIn("keine SL-Office-Datenbank", response.get_data(as_text=True))
        self.assertEqual(self._namen(), ["Mia"])

    def test_names_outside_the_backup_folder_are_refused(self):
        response = self._client().post("/admin/datensicherung/wiederherstellen",
                                        data={"name": "../.."}, follow_redirects=True)
        self.assertIn("Ungültiger Name", response.get_data(as_text=True))

    def test_only_the_administration_may_restore(self):
        with self.app.app_context():
            sicherung = backup_service.create_backup(self.app)
        response = self._client("leitung").post("/admin/datensicherung/wiederherstellen",
                                                data={"name": sicherung.name})
        self.assertNotIn("/login", response.headers.get("Location", ""))
        self.assertEqual(self._namen(), ["Mia"])


if __name__ == "__main__":
    unittest.main()
