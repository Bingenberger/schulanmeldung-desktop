"""Zwei Fassungen des Anmeldebriefs.

Wählen die Eltern ihren Termin selbst, fordert der Brief sie dazu auf. Hat die
Schule den Termin schon vergeben, nennt er ihn stattdessen. Welche Fassung ein
Kind bekommt, entscheidet sich beim Druck an seinem Terminstand.
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
from sl_office.briefe import letters  # noqa: E402
from sl_office.appointments.models import AppointmentEvent, AppointmentSlot  # noqa: E402
from sl_office.briefe.models import (  # noqa: E402
    Elternbrief,
)

SCHOOL = {"name": "GGS", "address": "Schulstraße 1", "contact_mail": "buero@example.de"}


class _LetterFixture:
    """Zwei Kinder: eines mit vergebenem Termin, eines ohne."""

    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            assigned = Schueler(vorname="Mia", nachname="Mitternin", strasse="Weg 7",
                                plz="53859", ort="Niederkassel", erzb_1_name="Anna M.")
            open_choice = Schueler(vorname="Tom", nachname="Ohnetermin", strasse="Weg 9",
                                   plz="53859", ort="Niederkassel", erzb_1_name="Cem O.")
            event = AppointmentEvent(
                title="Anmeldung", school_year=2027, status="published",
                slot_days_from=datetime.date(2026, 10, 12),
                slot_days_until=datetime.date(2026, 10, 16),
            )
            db.session.add_all([assigned, open_choice, event])
            db.session.flush()
            slot = AppointmentSlot(
                event_id=event.id, capacity=2, location="Raum 1",
                starts_at=datetime.datetime(2026, 10, 14, 7, 20),   # 09:20 Ortszeit
                ends_at=datetime.datetime(2026, 10, 14, 8, 0),
            )
            db.session.add(slot)
            db.session.flush()
            assign_slot(slot.id, assigned.id)
            db.session.commit()
            self.assigned_id, self.open_id = assigned.id, open_choice.id
            self.slot_id, self.event_id = slot.id, event.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _letters(self, student_ids, **options):
        with self.app.app_context():
            children = [db.session.get(Schueler, sid) for sid in student_ids]
            buffer = letters.build_letters(
                children, SCHOOL,
                letter_date=datetime.date(2026, 9, 1), school_year="2027/2028",
                deadline="30. September 2026", period="12. bis 16. Oktober 2026",
                **options)
            db.session.commit()
            # Der Blocksatz legt beim Auslesen jedes Wort in eine eigene Zeile;
            # für den Vergleich mit ganzen Sätzen muss das eingeebnet werden.
            raw = " ".join(page.extract_text() or "" for page in PdfReader(buffer).pages)
            return " ".join(raw.split())


class VariantChoiceTests(_LetterFixture, unittest.TestCase):

    def test_a_child_without_an_appointment_is_asked_to_pick_one(self):
        with self.app.app_context():
            self.assertEqual(letters.letter_variant(self.open_id), letters.TEXT_KEY)
        text = self._letters([self.open_id])
        self.assertIn("vereinbaren Sie für das Anmeldegespräch einen Termin", text)
        self.assertIn("30. September 2026", text)
        self.assertNotIn("bereits einen Termin", text)

    def test_a_child_with_an_assigned_appointment_is_told_when_to_come(self):
        with self.app.app_context():
            self.assertEqual(letters.letter_variant(self.assigned_id),
                             letters.ASSIGNED_TEXT_KEY)
        text = self._letters([self.assigned_id])
        self.assertIn("bereits einen Termin", text)
        self.assertIn("14.10.2026", text)
        self.assertIn("09:20", text)          # Ortszeit, nicht die gespeicherte UTC
        self.assertIn("Raum 1", text)
        self.assertNotIn("vereinbaren Sie für das Anmeldegespräch einen Termin", text)

    def test_one_batch_may_carry_both_versions(self):
        text = self._letters([self.assigned_id, self.open_id])
        self.assertIn("bereits einen Termin", text)
        self.assertIn("vereinbaren Sie für das Anmeldegespräch einen Termin", text)

    def test_a_cancelled_appointment_returns_the_child_to_the_open_version(self):
        from sl_office.appointments.service import cancel_booking_as_staff
        from sl_office.appointments.models import AppointmentBooking
        with self.app.app_context():
            booking = db.session.scalar(db.select(AppointmentBooking))
            cancel_booking_as_staff(booking.id)
            db.session.commit()
            self.assertEqual(letters.letter_variant(self.assigned_id), letters.TEXT_KEY)


class VariantTextTests(unittest.TestCase):

    def setUp(self):
        self.app = create_app("testing")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_both_defaults_pass_their_own_check(self):
        self.assertEqual(letters.check_text(**letters.DEFAULT_TEXT), [])
        self.assertEqual(
            letters.check_text(key=letters.ASSIGNED_TEXT_KEY, **letters.ASSIGNED_TEXT), [])

    def test_the_versions_differ_only_in_the_appointment_paragraph(self):
        # Der gemeinsame Teil steht nur einmal im Quelltext und kann darum
        # nicht auseinanderlaufen.
        without_self = letters.DEFAULT_TEXT["text"].replace(letters.SELF_PARAGRAPH, "")
        without_assigned = letters.ASSIGNED_TEXT["text"].replace(letters.ASSIGNED_PARAGRAPH, "")
        self.assertEqual(without_self, without_assigned)

    def test_the_assigned_version_must_name_the_appointment(self):
        problems = letters.check_text("Titel", "Text ohne alles", "Grüße",
                                      key=letters.ASSIGNED_TEXT_KEY)
        self.assertTrue(any("{termin}" in problem for problem in problems))

    def test_a_deadline_makes_no_sense_in_the_assigned_version(self):
        problems = letters.check_text("Titel", "{frist} {termin} ", "Grüße",
                                      key=letters.ASSIGNED_TEXT_KEY)
        self.assertTrue(any("frist" in problem for problem in problems))

    def test_the_two_versions_are_stored_separately(self):
        with self.app.app_context():
            letters.save_text("Nur Selbstwahl", letters.DEFAULT_TEXT["text"], "Grüße")
            db.session.commit()
            self.assertEqual(letters.stored_text()["titel"], "Nur Selbstwahl")
            # Die andere Fassung bleibt bei der Schulvorlage.
            self.assertEqual(letters.stored_text(letters.ASSIGNED_TEXT_KEY),
                             letters.ASSIGNED_TEXT)
            self.assertEqual(db.session.query(Elternbrief).count(), 1)

    def test_resetting_one_version_leaves_the_other_alone(self):
        with self.app.app_context():
            letters.save_text("A", letters.DEFAULT_TEXT["text"], "G")
            letters.save_text("B", letters.ASSIGNED_TEXT["text"], "G",
                              key=letters.ASSIGNED_TEXT_KEY)
            db.session.commit()
            letters.reset_text(letters.TEXT_KEY)
            db.session.commit()
            self.assertEqual(letters.stored_text(), letters.DEFAULT_TEXT)
            self.assertEqual(letters.stored_text(letters.ASSIGNED_TEXT_KEY)["titel"], "B")


class VariantEditorTests(_LetterFixture, unittest.TestCase):

    def _staff(self):
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
        return client

    def test_the_editor_offers_both_versions(self):
        body = self._staff().get("/admin/elternbrief-text").get_data(as_text=True)
        self.assertIn("Die Eltern vereinbaren einen Termin", body)
        self.assertIn("Die Schule gibt den Termin vor", body)

    def test_the_editor_saves_into_the_chosen_version(self):
        client = self._staff()
        client.post("/admin/elternbrief-text", data={
            "variante": letters.ASSIGNED_TEXT_KEY, "action": "save",
            "titel": "Zugewiesen {schuljahr}", "text": letters.ASSIGNED_TEXT["text"],
            "gruss": "Mit freundlichen Grüßen",
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(letters.stored_text(letters.ASSIGNED_TEXT_KEY)["titel"],
                             "Zugewiesen {schuljahr}")
            self.assertEqual(letters.stored_text()["titel"], letters.DEFAULT_TITLE)

    def test_the_print_list_shows_who_gets_a_fixed_appointment(self):
        body = self._staff().get("/admin/elternbriefe").get_data(as_text=True)
        self.assertIn("Termin vorgegeben", body)
        self.assertIn("14.10.2026", body)
        self.assertIn("ohne Termin", body)


if __name__ == "__main__":
    unittest.main()
