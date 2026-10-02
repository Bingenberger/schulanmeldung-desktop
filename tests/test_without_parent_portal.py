"""Die Desktop-Fassung ohne Elternportal.

Ohne ``PARENT_PORTAL_ENABLED`` gibt es keine Elternseiten, die Verwaltung
blendet Zugänge und eingegangene Anmeldungen aus, und die Elternbriefe laden
ein, ohne Zugangslinks auszustellen.
"""

import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from pypdf import PdfReader  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Schueler, User, db  # noqa: E402
from sl_office.appointments.service import assign_slot  # noqa: E402
from sl_office.parent_portal import letters  # noqa: E402
from sl_office.parent_portal.models import (  # noqa: E402
    ActivationGrant, AppointmentEvent, AppointmentSlot,
)

SCHOOL = {"name": "Grundschule Beispiel", "contact_mail": "buero@example.de",
          "phone": "01234 5678", "town": "Musterstadt"}


class _DesktopFixture:
    def setUp(self):
        self.app = create_app("testing", {"PARENT_PORTAL_ENABLED": False})
        with self.app.app_context():
            admin = User(username="chefin", role="Administrator",
                         password_hash=generate_password_hash("x"))
            ohne = Schueler(vorname="Tom", nachname="Ohnetermin", strasse="Weg 9",
                            plz="12345", ort="Musterstadt", erzb_1_name="Cem O.")
            mit = Schueler(vorname="Mia", nachname="Mittermin", strasse="Weg 7",
                           plz="12345", ort="Musterstadt", erzb_1_name="Anna M.")
            event = AppointmentEvent(
                title="Anmeldung", school_year=2027, status="published",
                slot_days_from=datetime.date(2026, 10, 12),
                slot_days_until=datetime.date(2026, 10, 16),
            )
            db.session.add_all([admin, ohne, mit, event])
            db.session.flush()
            slot = AppointmentSlot(
                event_id=event.id, capacity=1, location="Raum 1",
                starts_at=datetime.datetime(2026, 10, 14, 7, 20),
                ends_at=datetime.datetime(2026, 10, 14, 8, 0),
            )
            db.session.add(slot)
            db.session.flush()
            assign_slot(slot.id, mit.id)
            db.session.commit()
            self.admin_id, self.ohne_id, self.mit_id = admin.id, ohne.id, mit.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_id)
            sess["_fresh"] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()


class PortalSwitchTests(_DesktopFixture, unittest.TestCase):

    def test_the_parent_pages_do_not_exist(self):
        self.assertNotIn("parent_portal", self.app.blueprints)
        self.assertEqual(self.app.test_client().get("/eltern/").status_code, 404)

    def test_portal_administration_is_gone(self):
        for path in ("/admin/elternzugänge", "/admin/anmeldungen"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_menu_and_login_do_not_mention_the_portal(self):
        start = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("Elternzugänge", start)
        self.assertNotIn("Eingegangene Anmeldungen", start)
        login = self.app.test_client().get("/login").get_data(as_text=True)
        self.assertNotIn("Elternportal", login)

    def test_the_letter_pages_still_work(self):
        self.assertEqual(self.client.get("/admin/elternbriefe").status_code, 200)
        page = self.client.get("/admin/elternbrief-text").get_data(as_text=True)
        self.assertIn("Die Eltern vereinbaren einen Termin", page)
        self.assertNotIn(letters.ACCESS_MARKER, page)

    def test_the_default_switch_is_off(self):
        # Ohne ausdrückliche Einstellung läuft die Anwendung ohne Portal.
        os.environ.pop("SL_OFFICE_PARENT_PORTAL", None)
        app = create_app("development", {"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        self.assertFalse(app.config["PARENT_PORTAL_ENABLED"])


class OfflineLetterTests(_DesktopFixture, unittest.TestCase):

    def _text(self, buffer):
        raw = " ".join(page.extract_text() or "" for page in PdfReader(buffer).pages)
        return " ".join(raw.split())

    def test_letters_carry_no_access_and_issue_no_grants(self):
        response = self.client.post("/admin/elternbriefe", data={
            "student_ids": [self.ohne_id, self.mit_id], "deadline": "30. September 2026"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        with self.app.app_context():
            self.assertEqual(ActivationGrant.query.count(), 0)

    def test_each_child_gets_the_matching_wording(self):
        with self.app.app_context():
            children = [db.session.get(Schueler, sid) for sid in (self.ohne_id, self.mit_id)]
            buffer, issued = letters.build_letters(
                children, SCHOOL, None, letter_date=datetime.date(2026, 9, 1),
                school_year="2027/2028", deadline="30. September 2026")
            text = self._text(buffer)
        self.assertEqual(issued, 0)
        self.assertIn("vereinbaren Sie für das Anmeldegespräch einen Termin", text)
        self.assertIn("01234 5678", text)
        self.assertIn("haben wir bereits einen Termin", text)
        self.assertNotIn("Elternportal", text)
        self.assertNotIn("Zugang", text)

    def test_the_default_wording_passes_the_check_without_marker(self):
        with self.app.app_context():
            for key in letters.variants():
                default = letters.variant(key)["default"]
                self.assertNotIn(letters.ACCESS_MARKER, default["text"])
                self.assertEqual(letters.check_text(key=key, **default), [])

    def test_the_preview_has_no_access_boxes(self):
        with self.app.app_context():
            text = self._text(letters.build_preview(SCHOOL, letters.OFFLINE_TEXT))
        self.assertNotIn("NUR-ZUR-ANSICHT", text)


if __name__ == "__main__":
    unittest.main()
