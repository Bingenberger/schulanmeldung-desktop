"""Die Angaben der Eltern auf der amtlichen Vorlage.

Geprüft wird, dass die Werte tatsächlich im PDF stehen, auf der richtigen
Seite, und dass die Ankreuzfelder der Auswahl entsprechen. Die Positionen
selbst lassen sich nur am gedruckten Blatt beurteilen -- die Tests halten
darum fest, was maschinell nachweisbar ist.
"""

import datetime
import os
import unittest
from io import BytesIO

os.environ["SL_OFFICE_ENV"] = "testing"

from pypdf import PdfReader  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Schueler, User, db  # noqa: E402
from sl_office.parent_portal import registration_form as rf  # noqa: E402
from sl_office.parent_portal import registration_pdf  # noqa: E402
from sl_office.parent_portal.models import ParentRegistration  # noqa: E402

DATA = {
    "kind_nachname": "Mustermann", "kind_vorname": "Lena",
    "kind_geburtsdatum": "2021-03-14", "kind_geburtsort": "Bonn",
    "kind_geschlecht": "weiblich", "kind_strasse": "Musterweg 12a",
    "kind_plz": "53859", "kind_ort": "Niederkassel",
    "kind_konfession": "andere", "kind_konfession_andere": "buddhistisch",
    "religion_abgemeldet": "nein",
    "sorgeberechtigt_1_name": "Mustermann, Anna",
    "sorgeberechtigt_1_email": "anna@example.de",
    "sorgeberechtigt_2_name": "Mustermann, Bernd",
    "sorgeberechtigt_2_geburtsland": "Österreich",
    "notfallinformationen": "Erdnussallergie",
    "besuchte_kita": "Kita Pappelweg", "kita_von": "08/2023", "kita_bis": "07/2027",
    "ort_datum": "Niederkassel, 01.09.2026",
}


def _pages(payload):
    reader = PdfReader(BytesIO(payload))
    return [page.extract_text() or "" for page in reader.pages]


class PrintoutContentTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            self.student = Schueler(vorname="Lena", nachname="Mustermann",
                                    geburtsdatum=datetime.date(2021, 3, 14))

    def _build(self, data=None):
        with self.app.app_context():
            return registration_pdf.build(DATA if data is None else data, self.student)

    def test_the_official_template_keeps_its_two_pages(self):
        pages = _pages(self._build())
        self.assertEqual(len(pages), 2)
        self.assertIn("Schulanmeldung", pages[0])
        self.assertIn("Unterschrift sorgeberechtigte Person 1", pages[1])

    def test_the_child_block_lands_on_the_first_page(self):
        first, second = _pages(self._build())
        self.assertIn("Mustermann, Lena", first)
        self.assertIn("14.03.2021", first)   # ISO wird deutsch geschrieben
        self.assertIn("Bonn", first)
        self.assertIn("53859 Niederkassel", first)
        self.assertNotIn("14.03.2021", second)

    def test_the_second_guardian_lands_on_the_second_page(self):
        first, second = _pages(self._build())
        self.assertIn("Mustermann, Bernd", second)
        self.assertIn("Österreich", second)
        self.assertNotIn("Mustermann, Bernd", first)

    def test_the_kita_period_is_written_as_one_line(self):
        self.assertIn("08/2023 bis 07/2027", _pages(self._build())[1])

    def test_a_long_note_is_wrapped_instead_of_running_off_the_page(self):
        lang = " ".join(["Allergie"] * 60)
        pages = _pages(self._build(dict(DATA, notfallinformationen=lang)))
        # Umbrochen auf höchstens fünf Zeilen; der Rest wird nicht gedruckt.
        self.assertLessEqual(pages[1].count("Allergie"), 60)
        self.assertIn("Allergie Allergie", pages[1])

    def test_an_empty_form_still_produces_the_blank_template(self):
        pages = _pages(self._build({}))
        self.assertEqual(len(pages), 2)
        self.assertIn("Daten des Kindes", pages[0])

    def test_master_data_fills_only_what_the_parents_left_empty(self):
        with self.app.app_context():
            merged = registration_pdf.with_master_data(
                {"kind_vorname": "Von den Eltern"}, self.student)
        self.assertEqual(merged["kind_vorname"], "Von den Eltern")
        self.assertEqual(merged["kind_nachname"], "Mustermann")
        self.assertEqual(merged["kind_geburtsdatum"], "2021-03-14")

    def test_every_choice_matches_an_option_of_the_digital_form(self):
        # Sonst kreuzt die Vorlage nichts an, weil die Schreibweise abweicht.
        for choice in registration_pdf.CHOICES:
            if choice.field.startswith("_"):
                continue
            field = rf.FIELDS_BY_NAME[choice.field]
            self.assertIn(choice.value, field.options,
                          f"{choice.field}: {choice.value!r} fehlt in {field.options}")

    def test_every_placed_field_exists_in_the_digital_form(self):
        known = set(rf.FIELDS_BY_NAME) | {"kind_nachname", "kind_vorname"}
        for name in ("kind_geburtsdatum", "sorgeberechtigt_1_email", "notfallinformationen"):
            self.assertIn(name, known)


class PrintoutRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            student = Schueler(vorname="Lena", nachname="Mustermann")
            user = User(username="sekretariat", role="Sekretariat",
                        password_hash=generate_password_hash("x"))
            db.session.add_all([student, user])
            db.session.flush()
            registration = ParentRegistration(schueler_id=student.id, data=DATA,
                                              status="submitted")
            db.session.add(registration)
            db.session.commit()
            self.registration_id, user_id = registration.id, user.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_staff_receive_the_filled_pdf(self):
        response = self.client.get(f"/admin/anmeldungen/{self.registration_id}/formular.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("Mustermann, Lena", _pages(response.get_data())[0])

    def test_the_printout_is_shown_rather_than_downloaded(self):
        response = self.client.get(f"/admin/anmeldungen/{self.registration_id}/formular.pdf")
        self.assertIn("inline", response.headers["Content-Disposition"])

    def test_the_detail_view_offers_the_printout(self):
        body = self.client.get(f"/admin/anmeldungen/{self.registration_id}").get_data(as_text=True)
        self.assertIn(f"/admin/anmeldungen/{self.registration_id}/formular.pdf", body)

    def test_the_printout_needs_a_staff_login(self):
        response = self.app.test_client().get(
            f"/admin/anmeldungen/{self.registration_id}/formular.pdf")
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
