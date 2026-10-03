"""Schulprofil: Angaben zur Schule in der Anwendung statt in Umgebungsvariablen."""

import os
import unittest
from io import BytesIO

os.environ["SL_OFFICE_ENV"] = "testing"

from PIL import Image  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import User, db  # noqa: E402
from sl_office import school_profile  # noqa: E402
from sl_office.briefe import letterhead  # noqa: E402


def _png(color=(200, 30, 30)):
    buffer = BytesIO()
    Image.new("RGB", (40, 20), color).save(buffer, format="PNG")
    return buffer.getvalue()


class SchoolProfileTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing", {"SCHOOL_NAME": "Aus der Konfiguration",
                                          "SCHOOL_TOWN": "Konfigstadt"})
        with self.app.app_context():
            admin = User(username="chefin", role="Administrator",
                         password_hash=generate_password_hash("x"))
            sekretariat = User(username="buero", role="Sekretariat",
                               password_hash=generate_password_hash("x"))
            db.session.add_all([admin, sekretariat])
            db.session.commit()
            self.admin_id, self.office_id = admin.id, sekretariat.id
        self.client = self._login(self.admin_id)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        return client

    def _save(self, **values):
        data = {key: "" for key in school_profile.TEXT_KEYS}
        data.update(values)
        data["action"] = "save"
        return self.client.post("/admin/schulprofil", data=data,
                                content_type="multipart/form-data")

    def test_without_a_profile_the_configuration_applies(self):
        with self.app.app_context():
            school = letterhead.branding()
        self.assertEqual(school["name"], "Aus der Konfiguration")
        self.assertEqual(school["town"], "Konfigstadt")

    def test_saved_values_replace_the_configuration(self):
        response = self._save(SCHOOL_NAME="Grundschule am Park", SCHOOL_PHONE="01234 5678",
                              SCHOOL_STREET="Parkweg 1", SCHOOL_CITY_LINE="12345 Musterstadt")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            school = letterhead.branding()
            self.assertTrue(school_profile.is_configured())
        self.assertEqual(school["name"], "Grundschule am Park")
        self.assertEqual(school["phone"], "01234 5678")
        self.assertEqual(school["contact"][:2], ["Parkweg 1", "12345 Musterstadt"])
        # Ein gespeichertes leeres Feld bleibt leer, statt auf die Konfiguration zurückzufallen.
        self.assertEqual(school["town"], "")

    def test_logo_and_signature_are_stored_and_drawn(self):
        logo, signature = _png(), _png((0, 0, 120))
        response = self.client.post("/admin/schulprofil", data={
            "SCHOOL_NAME": "Grundschule am Park", "action": "save",
            "SCHOOL_LOGO": (BytesIO(logo), "logo.png"),
            "SCHOOL_SIGNATURE": (BytesIO(signature), "unterschrift.png"),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        image = self.client.get("/admin/schulprofil/bild/SCHOOL_LOGO")
        self.assertEqual(image.mimetype, "image/png")
        self.assertEqual(image.data, logo)

        preview = self.client.get("/admin/schulprofil/vorschau")
        self.assertEqual(preview.mimetype, "application/pdf")
        reader = PdfReader(BytesIO(preview.data))
        # Das Logo steht im Briefkopf der ersten Seite, die Unterschrift unter dem Brief.
        self.assertTrue(reader.pages[0].images)
        self.assertTrue(reader.pages[-1].images)
        self.assertIn("Grundschule am Park", reader.pages[0].extract_text())

    def test_an_image_can_be_removed(self):
        with self.app.app_context():
            school_profile.save_image("SCHOOL_LOGO", _png())
            db.session.commit()
        self.client.post("/admin/schulprofil", data={"action": "remove:SCHOOL_LOGO"})
        self.assertEqual(self.client.get("/admin/schulprofil/bild/SCHOOL_LOGO").status_code, 404)
        with self.app.app_context():
            self.assertEqual(letterhead.branding()["logo"], "")

    def test_a_non_image_is_rejected(self):
        response = self.client.post("/admin/schulprofil", data={
            "action": "save", "SCHOOL_LOGO": (BytesIO(b"kein Bild"), "logo.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("kein lesbares Bild", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(school_profile.image("SCHOOL_LOGO"))

    def test_unknown_image_keys_are_refused(self):
        self.assertEqual(self.client.get("/admin/schulprofil/bild/SECRET_KEY").status_code, 404)
        response = self.client.post("/admin/schulprofil", data={"action": "remove:SECRET_KEY"})
        self.assertEqual(response.status_code, 400)

    def test_only_administration_and_school_leaders_may_edit(self):
        office = self._login(self.office_id)
        self.assertIn(office.get("/admin/schulprofil").status_code, (302, 403))


if __name__ == "__main__":
    unittest.main()
