"""Löschen eines Schülerdatensatzes über die Weboberfläche.

Der Weg nach dem Löschen ist eigens geprüft: die Detailseite des Kindes gibt es
dann nicht mehr, ein Rücksprung dorthin endet in einer 404.
"""

import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Schueler, User, db  # noqa: E402
from sl_office import maintenance  # noqa: E402
from sl_office.parent_portal.access_service import (  # noqa: E402
    create_activation_grant, create_login_token,
)
from sl_office.parent_portal.models import (  # noqa: E402
    ActivationGrant, AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentAccess,
    ParentLoginToken, ParentRegistration,
)
from sl_office.services.student_deletion import delete_student  # noqa: E402


class StudentDeletionTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            student = Schueler(vorname="Weg", nachname="Damit")
            user = User(username="leitung", role="Schulleitung",
                        password_hash=generate_password_hash("x"))
            db.session.add_all([student, user])
            db.session.commit()
            self.student_id, user_id = student.id, user.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_deleting_leads_to_the_student_list(self):
        response = self.client.post(f"/schueler/{self.student_id}/delete")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/liste")
        with self.app.app_context():
            self.assertIsNone(db.session.get(Schueler, self.student_id))

    def test_the_page_after_deleting_actually_exists(self):
        # Zuvor wurde auf die Detailseite des gelöschten Kindes umgeleitet: 404.
        response = self.client.post(f"/schueler/{self.student_id}/delete",
                                    follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("wurden gelöscht", response.get_data(as_text=True))

    def test_a_failed_deletion_returns_to_the_still_existing_student(self):
        from sl_office.students import routes

        def explode(student, upload_folder):
            raise OSError("Datenbank nicht erreichbar")

        original = routes.delete_student
        routes.delete_student = explode
        try:
            response = self.client.post(f"/schueler/{self.student_id}/delete")
        finally:
            routes.delete_student = original
        self.assertEqual(response.headers["Location"], f"/schueler/{self.student_id}")
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Schueler, self.student_id))
        self.assertEqual(self.client.get(f"/schueler/{self.student_id}").status_code, 200)


class PortalDataDeletionTests(unittest.TestCase):
    """Beim Löschen eines Kindes müssen auch seine Elternportal-Daten verschwinden.

    Sonst bleiben sie als Waisen liegen, und weil SQLite die freigewordene
    Zeilennummer neu vergibt, erbt sie das nächste angelegte Kind.
    """

    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            student = Schueler(vorname="Test", nachname="Mustermann")
            db.session.add(student)
            db.session.flush()
            _, self.token = create_activation_grant(student.id, "first_access")
            event = AppointmentEvent(title="Anmeldung", school_year=2027, status="published")
            db.session.add(event)
            db.session.flush()
            start = datetime.datetime.now(datetime.UTC).replace(tzinfo=None, minute=0, second=0,
                                                                microsecond=0) + datetime.timedelta(days=7)
            slot = AppointmentSlot(event_id=event.id, starts_at=start,
                                   ends_at=start + datetime.timedelta(minutes=40))
            db.session.add(slot)
            db.session.commit()
            self.student_id, self.slot_id = student.id, slot.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _set_up_parent(self):
        """Zugang aktivieren, Formular anlegen, Termin buchen, Anmeldelink erzeugen."""
        client = self.app.test_client()
        client.post(f"/eltern/aktivieren/{self.token}",
                    data={"email": "eltern@example.de", "display_name": "Familie Mustermann"},
                    follow_redirects=True)
        client.get("/eltern/formular")
        client.post("/eltern/formular/kind",
                    data={"kind_vorname": "Test", "kind_nachname": "Mustermann", "action": "save"},
                    follow_redirects=True)
        client.post(f"/eltern/termine/{self.slot_id}/buchen", follow_redirects=True)
        with self.app.app_context():
            access = db.session.scalar(db.select(ParentAccess))
            create_login_token(access.id)
            db.session.commit()

    def _counts(self):
        with self.app.app_context():
            return {model.__tablename__: db.session.query(model).count()
                    for model in (ParentAccess, ActivationGrant, ParentRegistration,
                                  AppointmentBooking, ParentLoginToken)}

    def _delete(self):
        with self.app.app_context():
            student = db.session.get(Schueler, self.student_id)
            delete_student(student, self.app.config["UPLOAD_FOLDER"])

    def test_the_setup_really_creates_portal_data(self):
        self._set_up_parent()
        self.assertEqual(self._counts(), {"parent_access": 1, "activation_grant": 1,
                                          "parent_registration": 1, "appointment_booking": 1,
                                          "parent_login_token": 1})

    def test_deleting_the_student_takes_the_portal_data_with_it(self):
        self._set_up_parent()
        self._delete()
        self.assertEqual(set(self._counts().values()), {0})

    def test_a_new_student_inherits_nothing_from_the_reused_id(self):
        self._set_up_parent()
        self._delete()
        with self.app.app_context():
            successor = Schueler(vorname="Anderes", nachname="Mustermann")
            db.session.add(successor)
            db.session.commit()
            # SQLite vergibt die freigewordene Zeilennummer erneut.
            self.assertEqual(successor.id, self.student_id)
            self.assertIsNone(db.session.scalar(db.select(ParentAccess).where(
                ParentAccess.schueler_id == successor.id)))
            self.assertIsNone(db.session.scalar(db.select(ParentRegistration).where(
                ParentRegistration.schueler_id == successor.id)))

    def test_sqlite_actually_enforces_foreign_keys(self):
        # Zweite Verteidigungslinie: ohne diese Einstellung bleibt jedes
        # ON DELETE CASCADE in der Datenbank wirkungslos.
        with self.app.app_context():
            self.assertEqual(
                db.session.execute(db.text("PRAGMA foreign_keys")).scalar(), 1)

    def test_the_cleanup_command_finds_and_removes_leftovers(self):
        self._set_up_parent()
        with self.app.app_context():
            # Ein Kind so löschen, wie es die alte Fassung tat: ohne die Portaldaten.
            db.session.execute(db.text("PRAGMA foreign_keys=OFF"))
            db.session.delete(db.session.get(Schueler, self.student_id))
            db.session.commit()
            self.assertEqual(set(maintenance.orphaned_portal_records()),
                             {"parent_access", "activation_grant", "parent_registration",
                              "appointment_booking"})
            maintenance.delete_orphaned_portal_records()
            self.assertEqual(maintenance.orphaned_portal_records(), {})


if __name__ == "__main__":
    unittest.main()
