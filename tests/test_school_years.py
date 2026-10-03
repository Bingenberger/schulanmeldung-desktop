"""Isolation between enrolment years, and the backup service."""

import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import AOSF, Diagnostik, Einschulungsjahr, Schueler, User, db  # noqa: E402
from sl_office import school_year  # noqa: E402
from sl_office.admin import backup_service  # noqa: E402


class YearScopeTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            db.session.add_all([
                Einschulungsjahr(jahr=2026, ist_aktuell=False, gesperrt=True),
                Einschulungsjahr(jahr=2027, ist_aktuell=True),
                User(username="chef", password_hash=generate_password_hash("pw"), role="Administrator"),
            ])
            db.session.flush()
            alt = Schueler(vorname="Alt", nachname="Kind", einschulungsjahr=2026,
                           geburtsdatum=datetime.date(2020, 5, 1))
            neu = Schueler(vorname="Neu", nachname="Kind", einschulungsjahr=2027,
                           geburtsdatum=datetime.date(2021, 5, 1))
            db.session.add_all([alt, neu])
            db.session.flush()
            db.session.add_all([
                Diagnostik(schueler_id=alt.id, schulspiel=True),
                Diagnostik(schueler_id=neu.id, schulspiel=True),
                AOSF(schueler_id=alt.id, status="Verdacht"),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _staff_client(self):
        client = self.app.test_client()
        with client.session_transaction() as flask_session:
            flask_session["_user_id"] = "1"
            flask_session["_fresh"] = True
        return client

    def test_only_the_active_year_is_visible(self):
        client = self._staff_client()
        with client:
            client.get("/")  # establish the request context
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(User, 1))
            self.assertEqual(school_year.active_year(), 2027)
            names = [s.vorname for s in Schueler.query.all()]
            self.assertEqual(names, ["Neu"])

    def test_switching_shows_the_other_year(self):
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(User, 1))
            school_year.set_active_year(2026)
            self.assertEqual([s.vorname for s in Schueler.query.all()], ["Alt"])

    def test_dependent_records_follow_the_year(self):
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(User, 1))
            # Statistics on the hub query these tables directly.
            self.assertEqual(Diagnostik.query.filter_by(schulspiel=True).count(), 1)
            self.assertEqual(AOSF.query.count(), 0)
            school_year.set_active_year(2026)
            self.assertEqual(Diagnostik.query.filter_by(schulspiel=True).count(), 1)
            self.assertEqual(AOSF.query.count(), 1)

    def test_a_child_of_another_year_cannot_be_opened_by_id(self):
        """The guarantee that matters: guessing a URL must not cross years."""
        with self.app.app_context():
            with school_year.all_years_scope():
                neu_id = Schueler.query.filter_by(vorname="Neu").one().id
        client = self._staff_client()
        self.assertEqual(client.get(f"/schueler/{neu_id}").status_code, 200)
        with client.session_transaction() as flask_session:
            flask_session[school_year.SESSION_KEY] = 2026
        self.assertEqual(client.get(f"/schueler/{neu_id}").status_code, 404)

    def test_identity_map_does_not_leak_across_a_mid_session_switch(self):
        # Within one loaded session the object stays in the identity map; a new
        # request starts clean, which is what expunge_all simulates here.
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(User, 1))
            neu = Schueler.query.filter_by(vorname="Neu").one()
            school_year.set_active_year(2026)
            db.session.expunge_all()
            self.assertIsNone(db.session.get(Schueler, neu.id))

    def test_new_children_get_the_active_year_automatically(self):
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(User, 1))
            child = Schueler(vorname="Frisch", nachname="Kind")
            db.session.add(child)
            db.session.commit()
            self.assertEqual(child.einschulungsjahr, 2027)

    def test_backups_and_administration_can_see_every_year(self):
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(User, 1))
            self.assertEqual(Schueler.query.count(), 1)
            with school_year.all_years_scope():
                self.assertEqual(Schueler.query.count(), 2)

    def test_requests_without_staff_login_are_not_scoped(self):
        # Ohne angemeldete Person gibt es keinen gewählten Jahrgang.
        with self.app.test_request_context():
            self.assertIsNone(school_year.active_year())
            self.assertEqual(Schueler.query.count(), 2)

    def test_locked_year_is_reported_as_readonly(self):
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(User, 1))
            self.assertFalse(school_year.is_readonly())
            school_year.set_active_year(2026)
            self.assertTrue(school_year.is_readonly())


class ReadOnlyGuardTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            db.session.add_all([
                Einschulungsjahr(jahr=2026, ist_aktuell=False, gesperrt=True),
                Einschulungsjahr(jahr=2027, ist_aktuell=True),
                User(username="chef", password_hash=generate_password_hash("pw"), role="Administrator"),
            ])
            db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as flask_session:
            flask_session["_user_id"] = "1"
            flask_session["_fresh"] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_writes_are_blocked_in_a_locked_year(self):
        with self.client.session_transaction() as flask_session:
            flask_session[school_year.SESSION_KEY] = 2026
        response = self.client.post("/add", data={"vorname": "X", "nachname": "Y"},
                                    follow_redirects=True)
        self.assertIn("schreibgeschützt", response.get_data(as_text=True))
        with self.app.app_context():
            with school_year.all_years_scope():
                self.assertEqual(Schueler.query.count(), 0)

    def test_reading_a_locked_year_still_works(self):
        with self.client.session_transaction() as flask_session:
            flask_session[school_year.SESSION_KEY] = 2026
        self.assertEqual(self.client.get("/liste").status_code, 200)

    def test_switching_out_of_a_locked_year_is_allowed(self):
        with self.client.session_transaction() as flask_session:
            flask_session[school_year.SESSION_KEY] = 2026
        response = self.client.post("/admin/einschulungsjahre/2027/wechseln", follow_redirects=True)
        self.assertNotIn("schreibgeschützt", response.get_data(as_text=True))
        with self.client.session_transaction() as flask_session:
            self.assertEqual(flask_session[school_year.SESSION_KEY], 2027)


class BackupServiceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        database = os.path.join(self.directory.name, "test.db")
        uploads = os.path.join(self.directory.name, "uploads")
        os.makedirs(uploads)
        with open(os.path.join(uploads, "gutachten.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4 test")
        self.app = create_app("testing", {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
            "UPLOAD_FOLDER": uploads,
            "BACKUP_FOLDER": os.path.join(self.directory.name, "backups"),
        })
        with self.app.app_context():
            db.create_all()
            db.session.add(Einschulungsjahr(jahr=2027, ist_aktuell=True))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()

    def test_backup_contains_database_and_uploads(self):
        target = backup_service.create_backup(self.app)
        self.assertTrue((target / "database.db").is_file())
        self.assertTrue((target / "uploads" / "gutachten.pdf").is_file())

    def test_backup_copy_is_a_usable_database(self):
        import sqlite3
        target = backup_service.create_backup(self.app)
        connection = sqlite3.connect(target / "database.db")
        self.addCleanup(connection.close)
        years = connection.execute("SELECT jahr FROM einschulungsjahr").fetchall()
        self.assertEqual(years, [(2027,)])

    def test_listing_reports_size_and_contents(self):
        backup_service.create_backup(self.app)
        entries = backup_service.list_backups(self.app)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["has_database"])
        self.assertTrue(entries[0]["has_uploads"])
        self.assertGreater(entries[0]["size"], 0)

    def test_download_snapshot_is_a_valid_database(self):
        payload = backup_service.database_snapshot(self.app).getvalue()
        self.assertTrue(payload.startswith(b"SQLite format 3"))

    def test_delete_removes_only_inside_the_backup_folder(self):
        target = backup_service.create_backup(self.app)
        backup_service.delete_backup(self.app, target.name)
        self.assertEqual(backup_service.list_backups(self.app), [])
        for evil in ("../uploads", "..", "/etc", "", "a/b"):
            with self.assertRaises(backup_service.BackupError, msg=evil):
                backup_service.delete_backup(self.app, evil)


if __name__ == "__main__":
    unittest.main()
