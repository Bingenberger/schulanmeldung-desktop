import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from app import create_app  # noqa: E402
from models import Schueler, db  # noqa: E402
from sl_office.parent_portal.access_service import create_activation_grant  # noqa: E402
from sl_office.parent_portal import registration_form  # noqa: E402
from sl_office.parent_portal.models import (  # noqa: E402
    AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentLoginToken, ParentRegistration,
)


class ParentPortalTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.client = self.app.test_client()
        with self.app.app_context():
            student = Schueler(vorname="Portal", nachname="Kind")
            other = Schueler(vorname="Fremd", nachname="Kind")
            now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            start = (now + datetime.timedelta(days=7)).replace(minute=0, second=0, microsecond=0)
            event = AppointmentEvent(
                title="Anmeldung", school_year=2027, status="published",
                booking_opens_at=now - datetime.timedelta(days=1),
                booking_closes_at=start + datetime.timedelta(days=1),
            )
            db.session.add_all([student, other, event])
            db.session.flush()
            slot = AppointmentSlot(event_id=event.id, starts_at=start, ends_at=start + datetime.timedelta(minutes=40))
            grant, token = create_activation_grant(student.id, "first_access")
            db.session.add(slot)
            db.session.commit()
            self.student_id, self.other_id, self.slot_id, self.token = student.id, other.id, slot.id, token

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _activate(self):
        return self.client.post(
            f"/eltern/aktivieren/{self.token}",
            data={"email": "parent@example.de", "display_name": "Elternteil"},
            follow_redirects=True,
        )

    def test_parent_portal_requires_personal_access(self):
        response = self.client.get("/eltern/uebersicht")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/eltern/", response.location)

    def test_activation_opens_only_own_student_dashboard(self):
        response = self._activate()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Portal Kind", response.data)
        self.assertNotIn(b"Fremd Kind", response.data)

    def test_authenticated_parent_can_book_available_slot(self):
        self._activate()
        response = self.client.post(f"/eltern/termine/{self.slot_id}/buchen", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            booking = AppointmentBooking.query.one()
            self.assertEqual(booking.schueler_id, self.student_id)

    def test_parent_can_cancel_own_booking(self):
        self._activate()
        self.client.post(f"/eltern/termine/{self.slot_id}/buchen")
        with self.app.app_context():
            booking_id = AppointmentBooking.query.one().id
        response = self.client.post(f"/eltern/termine/{booking_id}/stornieren", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(AppointmentBooking.query.one().status, "cancelled")

    def test_session_cannot_select_another_student(self):
        self._activate()
        response = self.client.post(f"/eltern/termine/{self.slot_id}/buchen?schueler_id={self.other_id}")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(AppointmentBooking.query.one().schueler_id, self.student_id)

    def _fill(self, step, data, action="next"):
        return self.client.post(f"/eltern/formular/{step}", data=dict(data, action=action),
                                follow_redirects=True)

    def _fill_required_steps(self):
        self._fill("kind", {"kind_nachname": "Kind", "kind_vorname": "Portal",
                            "kind_geburtsdatum": "2020-01-02"})
        self._fill("sorge1", {"sorgeberechtigt_1_name": "Elternteil",
                              "sorgeberechtigt_1_email": "parent@example.de"})

    def test_each_step_saves_on_its_own(self):
        # Zwischenspeichern darf nicht an noch fehlenden Pflichtangaben scheitern.
        self._activate()
        response = self._fill("kind", {"kind_vorname": "Portal"}, action="save")
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            registration = ParentRegistration.query.one()
            self.assertEqual(registration.status, "draft")
            self.assertEqual(registration.data["kind_vorname"], "Portal")

    def test_a_later_step_keeps_the_earlier_answers(self):
        self._activate()
        self._fill("kind", {"kind_nachname": "Kind", "kind_vorname": "Portal",
                            "kind_geburtsdatum": "2020-01-02"})
        self._fill("weiteres", {"familiensprache": "Türkisch"})
        with self.app.app_context():
            data = ParentRegistration.query.one().data
            self.assertEqual(data["kind_vorname"], "Portal")
            self.assertEqual(data["familiensprache"], "Türkisch")

    def test_registration_can_be_saved_and_submitted(self):
        self._activate()
        self._fill_required_steps()
        with self.app.app_context():
            self.assertEqual(ParentRegistration.query.one().status, "draft")
        response = self._fill("abschluss", {"ort_datum": "Niederkassel, 1.10.2026",
                                            "datenschutz_bestaetigt": "ja",
                                            "angaben_richtig": "ja"}, action="submit")
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(ParentRegistration.query.one().status, "submitted")

    def test_registration_requires_minimum_fields(self):
        self._activate()
        response = self._fill("abschluss", {"datenschutz_bestaetigt": "ja",
                                            "angaben_richtig": "ja"}, action="submit")
        self.assertEqual(response.status_code, 200)
        self.assertIn("fehlen noch Pflichtangaben", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(ParentRegistration.query.one().status, "draft")

    def test_the_confirmations_are_required_for_submission(self):
        self._activate()
        self._fill_required_steps()
        self._fill("abschluss", {"ort_datum": "Niederkassel"}, action="submit")
        with self.app.app_context():
            self.assertEqual(ParentRegistration.query.one().status, "draft")

    def test_the_form_resumes_at_the_first_open_step(self):
        self._activate()
        first = self.client.get("/eltern/formular")
        self.assertTrue(first.headers["Location"].endswith("/formular/kind"))
        self._fill_required_steps()
        later = self.client.get("/eltern/formular")
        self.assertTrue(later.headers["Location"].endswith("/formular/abschluss"))

    def test_the_second_guardian_step_can_be_skipped(self):
        self._activate()
        response = self.client.post("/eltern/formular/sorge2",
                                    data={"action": "skip", "sorgeberechtigt_2_name": "Ignoriert"})
        self.assertTrue(response.headers["Location"].endswith("/formular/weiteres"))
        with self.app.app_context():
            self.assertFalse((ParentRegistration.query.one().data or {}).get("sorgeberechtigt_2_name"))

    def test_existing_parent_can_request_login_link_without_exposing_account(self):
        self._activate()
        response = self.client.post("/eltern/link-anfordern", data={"email": "parent@example.de"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Link", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(ParentLoginToken.query.count(), 1)
        response = self.client.post("/eltern/link-anfordern", data={"email": "unknown@example.de"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Link", response.get_data(as_text=True))


class RegistrationCatalogueTests(unittest.TestCase):
    """Der Feldkatalog muss das Papierformular Schulanmeldung.pdf abbilden."""

    #: Jede Zeile des Papierformulars, ohne die drei reinen Papierfelder
    #: (zwei Unterschriften und "vorab digital übermittelt").
    PAPER_FIELDS = {
        "kind_nachname", "kind_vorname", "kind_geburtsdatum", "kind_geburtsort",
        "kind_strasse", "kind_plz", "kind_ort", "kind_staatsangehoerigkeit",
        "kind_muttersprache", "kind_geschlecht", "kind_konfession",
        "kind_konfession_andere", "religion_abgemeldet",
        "notfallinformationen", "weitere_notfallnummer", "weitere_sorgeberechtigte",
        "besuchte_kita", "besuchte_kita_andere", "kita_von", "kita_bis", "familiensprache",
        "ort_datum", "datenschutz_bestaetigt", "angaben_richtig",
    } | {
        f"sorgeberechtigt_{number}_{part}"
        for number in (1, 2)
        for part in ("name", "strasse", "plz", "ort", "staatsangehoerigkeit", "muttersprache",
                     "zuzugsjahr", "geburtsland", "sorgeberechtigt", "email", "telefon",
                     "notfalltelefon")
    }

    def test_every_field_of_the_paper_form_is_offered(self):
        self.assertEqual(self.PAPER_FIELDS - set(registration_form.FORM_FIELDS), set())

    def test_no_field_is_offered_twice(self):
        names = registration_form.FORM_FIELDS
        self.assertEqual(len(names), len(set(names)))

    def test_answers_with_fixed_options_are_selections(self):
        # Geschlecht, Konfession und alle Ja/Nein-Fragen dürfen kein Freitext sein.
        for name in ("kind_geschlecht", "kind_konfession", "religion_abgemeldet",
                     "weitere_sorgeberechtigte", "besuchte_kita",
                     "sorgeberechtigt_1_sorgeberechtigt", "sorgeberechtigt_2_sorgeberechtigt"):
            with self.subTest(name=name):
                field = registration_form.FIELDS_BY_NAME[name]
                self.assertEqual(field.kind, "select")
                self.assertTrue(field.options)

    def test_contact_details_use_their_own_input_types(self):
        expected = {
            "kind_geburtsdatum": "date",
            "sorgeberechtigt_1_email": "email",
            "sorgeberechtigt_1_telefon": "tel",
            "sorgeberechtigt_1_notfalltelefon": "tel",
            "weitere_notfallnummer": "tel",
            "sorgeberechtigt_1_zuzugsjahr": "number",
            "familiensprache": "text",
            "kita_von": "text",
        }
        for name, kind in expected.items():
            with self.subTest(name=name):
                self.assertEqual(registration_form.FIELDS_BY_NAME[name].kind, kind)

    def test_the_kita_period_belongs_visibly_to_the_kita(self):
        # "Besuchszeitraum" allein war missverständlich: gemeint ist die Kita.
        for name in ("besuchte_kita", "besuchte_kita_andere", "kita_von", "kita_bis"):
            with self.subTest(name=name):
                self.assertEqual(registration_form.FIELDS_BY_NAME[name].group, "Besuchte Kita")
        for name in ("kita_von", "kita_bis"):
            with self.subTest(name=name):
                field = registration_form.FIELDS_BY_NAME[name]
                self.assertEqual(field.placeholder, "MM/JJJJ")
                self.assertTrue(field.pattern)

    def test_only_the_free_text_note_is_a_textarea(self):
        textareas = [name for name in registration_form.FORM_FIELDS
                     if registration_form.FIELDS_BY_NAME[name].kind == "textarea"]
        self.assertEqual(textareas, ["notfallinformationen"])

    def test_the_steps_stay_small_enough_to_read(self):
        for step in registration_form.STEPS:
            with self.subTest(step=step.key):
                self.assertLessEqual(len(step.fields), 12)


if __name__ == "__main__":
    unittest.main()
