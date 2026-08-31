import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash

from app import create_app
from models import GlobalSettings, Schueler, User, db
from sl_office.parent_portal.models import ActivationGrant, ParentRegistration
from sl_office.services.student_classification import recalculate_kann_kind


class AdminServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login_as(self, role):
        with self.app.app_context():
            user = User(
                username=f"role-{role}",
                password_hash=generate_password_hash("unused-password"),
                role=role,
            )
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def test_kann_kind_cutoff_and_schulspiel_are_calculated(self):
        with self.app.app_context():
            settings = GlobalSettings(einschulungsjahr=2026)
            must_child = Schueler(
                vorname="Muss", nachname="Kind", geburtsdatum=datetime.date(2020, 9, 30)
            )
            optional_child = Schueler(
                vorname="Kann", nachname="Kind", geburtsdatum=datetime.date(2020, 10, 1)
            )
            db.session.add_all([settings, must_child, optional_child])
            db.session.flush()

            count = recalculate_kann_kind(settings=settings)
            db.session.commit()

            self.assertEqual(count, 2)
            self.assertFalse(must_child.kann_kind)
            self.assertTrue(optional_child.kann_kind)
            self.assertIsNotNone(optional_child.diagnostik)
            self.assertTrue(optional_child.diagnostik.schulspiel)

    def test_secretariat_cannot_open_settings(self):
        self.login_as("Sekretariat")
        response = self.client.get("/admin/settings", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_administrator_can_open_settings(self):
        self.login_as("Administrator")
        response = self.client.get("/admin/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Einstellungen", response.data)

    def test_support_teacher_cannot_create_student(self):
        self.login_as("Foerderlehrkraft")
        response = self.client.get("/add", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_support_teacher_cannot_export_student_data(self):
        self.login_as("Foerderlehrkraft")
        response = self.client.get("/export/excel", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_support_teacher_cannot_manage_appointments(self):
        self.login_as("Foerderlehrkraft")
        response = self.client.get("/admin/appointments/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_secretariat_can_create_appointment_event(self):
        self.login_as("Sekretariat")
        response = self.client.post(
            "/admin/appointments/",
            data={"title": "Schulanmeldung 2027", "school_year": "2027"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/appointments/", response.headers["Location"])

    def test_secretariat_can_open_student_creation(self):
        self.login_as("Sekretariat")
        response = self.client.get("/add")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sch\xc3\xbcler anlegen", response.data)

    def test_secretariat_can_create_and_open_student(self):
        self.login_as("Sekretariat")
        response = self.client.post(
            "/add",
            data={
                "vorname": "Blueprint",
                "nachname": "Testkind",
                "geburtsdatum": "2020-10-01",
                "geschlecht": "d",
                "kita": "Test-Kita",
                "betreuung": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            student = Schueler.query.filter_by(vorname="Blueprint", nachname="Testkind").one()
            student_id = student.id
        detail = self.client.get(f"/schueler/{student_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Blueprint", detail.data)

    def test_the_address_is_saved_when_a_student_is_created(self):
        # Ohne Anschrift lässt sich kein Elternbrief zustellen.
        self.login_as("Sekretariat")
        response = self.client.post(
            "/add",
            data={
                "vorname": "Anschrift", "nachname": "Testkind", "geburtsdatum": "2020-10-01",
                "geschlecht": "w", "kita": "", "betreuung": "",
                "strasse": "Rheinstraße 12", "plz": "53859", "ort": "Niederkassel",
                "erzb_1_name": "Ayşe Yilmaz", "erzb_2_name": "Mehmet Yilmaz",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            student = Schueler.query.filter_by(nachname="Testkind").one()
            self.assertEqual(
                (student.strasse, student.plz, student.ort, student.erzb_1_name),
                ("Rheinstraße 12", "53859", "Niederkassel", "Ayşe Yilmaz"))

    def test_the_address_can_be_corrected_afterwards(self):
        self.login_as("Sekretariat")
        with self.app.app_context():
            student = Schueler(vorname="Umzug", nachname="Kind",
                               geburtsdatum=datetime.date(2020, 10, 1), strasse="Alte Gasse 1",
                               plz="53859", ort="Niederkassel")
            db.session.add(student)
            db.session.commit()
            student_id = student.id
        form = self.client.get(f"/schueler/{student_id}/edit")
        self.assertIn("Alte Gasse 1".encode(), form.data)
        self.client.post(
            f"/schueler/{student_id}/edit",
            data={
                "vorname": "Umzug", "nachname": "Kind", "geburtsdatum": "2020-10-01",
                "geschlecht": "u",
                "kita": "", "betreuung": "", "strasse": "Neue Straße 9", "plz": "53859",
                "ort": "Niederkassel", "erzb_1_name": "Mutter Kind", "erzb_2_name": "",
            },
            follow_redirects=False,
        )
        with self.app.app_context():
            student = db.session.get(Schueler, student_id)
            self.assertEqual(student.strasse, "Neue Straße 9")
            self.assertEqual(student.erzb_1_name, "Mutter Kind")

    def test_an_overlong_postcode_is_rejected_before_the_database(self):
        self.login_as("Sekretariat")
        response = self.client.post(
            "/add",
            data={
                "vorname": "Zu", "nachname": "Lang", "geburtsdatum": "2020-10-01",
                "geschlecht": "u", "kita": "", "betreuung": "", "plz": "1" * 20,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)  # Formular wird erneut gezeigt
        with self.app.app_context():
            self.assertIsNone(Schueler.query.filter_by(nachname="Lang").first())

    def test_secretariat_can_review_parent_registration(self):
        self.login_as("Sekretariat")
        with self.app.app_context():
            student = Schueler(vorname="Digital", nachname="Kind")
            db.session.add(student)
            db.session.flush()
            registration = ParentRegistration(schueler_id=student.id, data={"kind_vorname": "Digital"}, status="submitted", version=1)
            db.session.add(registration)
            db.session.commit()
            registration_id = registration.id
        response = self.client.get("/admin/anmeldungen")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Digital", response.data)
        response = self.client.post(f"/admin/anmeldungen/{registration_id}", data={"status": "in_review"}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(ParentRegistration, registration_id).status, "in_review")

    def test_support_teacher_cannot_review_parent_registration(self):
        self.login_as("Foerderlehrkraft")
        response = self.client.get("/admin/anmeldungen", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_secretariat_can_generate_parent_letter_link(self):
        self.login_as("Sekretariat")
        with self.app.app_context():
            student = Schueler(vorname="Brief", nachname="Kind")
            db.session.add(student)
            db.session.commit()
            student_id = student.id
        response = self.client.post("/admin/elternzugänge", data={"student_id": student_id, "purpose": "first_access"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Brief-Link", response.data)
        with self.app.app_context():
            self.assertEqual(ActivationGrant.query.count(), 1)

    def test_support_teacher_cannot_generate_parent_letter_link(self):
        self.login_as("Foerderlehrkraft")
        response = self.client.get("/admin/elternzug\u00e4nge", follow_redirects=False)
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
