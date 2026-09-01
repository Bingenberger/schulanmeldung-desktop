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
from sl_office.appointments.service import (  # noqa: E402
    SlotHasBookings, assign_slot, assignable_slots, book_slot, confirmed_booking_count, move_slot,
)
from sl_office.services.student_deletion import (  # noqa: E402
    delete_all_students, delete_student,
)


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


class SlotReleaseTests(unittest.TestCase):
    """Ein gelöschtes Kind gibt sein Zeitfenster wieder frei.

    Die Buchung verschwindet dabei, statt auf "storniert" gesetzt zu werden --
    sie zeigt auf ein Kind, das es nicht mehr gibt, und bliebe sonst als
    Waise liegen. Der Platz wird über bestätigte Buchungen gezählt, das
    Fenster ist damit sofort wieder zu vergeben.
    """

    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            start = (now + datetime.timedelta(days=10)).replace(minute=0, second=0, microsecond=0)
            event = AppointmentEvent(
                title="Anmeldung", school_year=2027, status="published",
                booking_opens_at=now - datetime.timedelta(days=1),
                booking_closes_at=start + datetime.timedelta(days=5),
                slot_days_from=start.date(), slot_days_until=start.date(),
            )
            leaving = Schueler(vorname="Weg", nachname="Damit")
            waiting = Schueler(vorname="Wartet", nachname="Schon")
            db.session.add_all([event, leaving, waiting])
            db.session.flush()
            slot = AppointmentSlot(event_id=event.id, capacity=1, starts_at=start,
                                   ends_at=start + datetime.timedelta(minutes=40))
            access = ParentAccess(schueler_id=waiting.id, status="active",
                                  email_normalized="wartet@example.de", display_name="Wartende")
            db.session.add_all([slot, access])
            db.session.flush()
            assign_slot(slot.id, leaving.id)
            db.session.commit()
            self.event_id, self.slot_id = event.id, slot.id
            self.leaving_id, self.waiting_id = leaving.id, waiting.id
            self.access_id = access.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _delete_leaving(self):
        with self.app.app_context():
            delete_student(db.session.get(Schueler, self.leaving_id),
                           self.app.config["UPLOAD_FOLDER"])

    def test_the_slot_is_taken_before_the_deletion(self):
        with self.app.app_context():
            self.assertEqual(confirmed_booking_count(self.slot_id), 1)
            self.assertEqual(assignable_slots(self.event_id), [])

    def test_deleting_the_child_frees_its_slot(self):
        self._delete_leaving()
        with self.app.app_context():
            self.assertEqual(confirmed_booking_count(self.slot_id), 0)
            self.assertEqual([slot.id for slot, _ in assignable_slots(self.event_id)],
                             [self.slot_id])

    def test_the_slot_itself_survives_the_child(self):
        # Das Fenster gehört dem Plan der Schule, nicht dem Kind.
        self._delete_leaving()
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(AppointmentSlot, self.slot_id))
            self.assertEqual(db.session.query(AppointmentBooking).count(), 0)

    def test_another_family_can_book_the_freed_slot(self):
        self._delete_leaving()
        with self.app.app_context():
            booking = book_slot(self.slot_id, self.waiting_id, self.access_id)
            db.session.commit()
            self.assertEqual(booking.status, "confirmed")

    def test_the_freed_slot_can_be_moved_again(self):
        # Solange es vergeben war, war das Fenster gegen Verschieben gesperrt.
        with self.app.app_context():
            target = db.session.get(AppointmentSlot, self.slot_id).starts_at
            with self.assertRaises(SlotHasBookings):
                move_slot(self.slot_id, target + datetime.timedelta(hours=1),
                          target + datetime.timedelta(hours=1, minutes=40))
        self._delete_leaving()
        with self.app.app_context():
            moved = move_slot(self.slot_id, target + datetime.timedelta(hours=1),
                              target + datetime.timedelta(hours=1, minutes=40))
            db.session.commit()
            self.assertEqual(moved.starts_at, target + datetime.timedelta(hours=1))

    def test_deleting_through_the_web_route_frees_the_slot_too(self):
        with self.app.app_context():
            user = User(username="leitung", role="Schulleitung",
                        password_hash=generate_password_hash("x"))
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        response = client.post(f"/schueler/{self.leaving_id}/delete", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(confirmed_booking_count(self.slot_id), 0)

    def test_deleting_every_child_frees_every_slot(self):
        with self.app.app_context():
            # Zweites Kind auf denselben Platz, damit beide Wege geprüft sind.
            db.session.get(AppointmentSlot, self.slot_id).capacity = 2
            assign_slot(self.slot_id, self.waiting_id)
            db.session.commit()
            self.assertEqual(confirmed_booking_count(self.slot_id), 2)
            delete_all_students(self.app.config["UPLOAD_FOLDER"])
            db.session.commit()
            self.assertEqual(confirmed_booking_count(self.slot_id), 0)
            self.assertIsNotNone(db.session.get(AppointmentSlot, self.slot_id))


if __name__ == "__main__":
    unittest.main()
