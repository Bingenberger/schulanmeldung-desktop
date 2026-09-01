"""Zwei Sorgeberechtigte an einem Formular.

Beide bearbeiten denselben Datensatz. Geprüft wird, was die zweite Person sieht
und was passiert, wenn sie nach der Abgabe noch etwas ändert.
"""

import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from app import create_app  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from models import Schueler, User, db  # noqa: E402
from sl_office.parent_portal import notifications, registration_form as rf  # noqa: E402
from sl_office.parent_portal.access_service import create_activation_grant  # noqa: E402
from sl_office.parent_portal.models import AuditEvent, ParentAccess, ParentRegistration  # noqa: E402


class _TwoGuardiansFixture:
    """Ein Kind, zwei Briefzugänge, ein von Elternteil A abgesendetes Formular."""

    def setUp(self):
        self.app = create_app("testing", {"NOTIFY_MAIL": "sekretariat@example.de"})
        self.outbox = []
        self._real_send = notifications.send_message
        notifications.send_message = lambda app, message: (self.outbox.append(message), True)[1]

        with self.app.app_context():
            student = Schueler(vorname="Probe", nachname="Kind")
            db.session.add(student)
            db.session.flush()
            _, first_token = create_activation_grant(student.id, "first_access")
            _, second_token = create_activation_grant(student.id, "second_access")
            db.session.commit()
            self.student_id = student.id

        self.mother = self.app.test_client()
        self.mother.post(f"/eltern/aktivieren/{first_token}",
                         data={"email": "mutter@example.de", "display_name": "Mutter Kind"})
        self.father = self.app.test_client()
        self.father.post(f"/eltern/aktivieren/{second_token}",
                         data={"email": "vater@example.de", "display_name": "Vater Kind"})
        self.mother.get("/eltern/formular")
        self._fill_and_submit(self.mother, "Probe")

    def tearDown(self):
        notifications.send_message = self._real_send
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _fill_and_submit(self, client, first_name):
        """Alle Schritte ausfüllen und absenden."""
        for step in rf.STEPS:
            payload = {"action": "submit" if step is rf.SUMMARY_STEP else "next"}
            for field in step.fields:
                if field.kind == "checkbox":
                    payload[field.name] = "ja"   # der Wert, den das Formular sendet
                elif field.name == "kind_vorname":
                    payload[field.name] = first_name
                elif field.kind == "select":
                    payload[field.name] = field.options[1] if len(field.options) > 1 else ""
                else:
                    payload[field.name] = f"{first_name}-{field.name}"
            client.post(f"/eltern/formular/{step.key}", data=payload, follow_redirects=True)

    def _change_first_name(self, client, value):
        step = rf.STEPS[0]
        payload = {field.name: (value if field.name == "kind_vorname" else f"{value}-{field.name}")
                   for field in step.fields}
        payload["action"] = "save"
        return client.post(f"/eltern/formular/{step.key}", data=payload, follow_redirects=True)

    def _form(self):
        return db.session.scalar(db.select(ParentRegistration))

    def _set_status(self, status):
        with self.app.app_context():
            self._form().status = status
            db.session.commit()


class SubmissionNoticeTests(_TwoGuardiansFixture, unittest.TestCase):

    def test_the_first_submission_is_recorded_with_its_author(self):
        with self.app.app_context():
            form = self._form()
            self.assertEqual(form.status, "submitted")
            self.assertIsNotNone(form.submitted_at)
            access = db.session.get(ParentAccess, form.submitted_by_access_id)
            self.assertEqual(access.email_normalized, "mutter@example.de")

    def test_the_second_guardian_sees_who_submitted(self):
        body = self.father.get("/eltern/uebersicht").get_data(as_text=True)
        self.assertIn("von Mutter Kind übermittelt", body)

    def test_the_form_itself_says_it_was_already_submitted(self):
        body = self.father.get(f"/eltern/formular/{rf.STEPS[0].key}").get_data(as_text=True)
        self.assertIn("von Mutter Kind übermittelt", body)
        self.assertIn("Die Schule erfährt dann davon", body)

    def test_an_unsubmitted_form_carries_no_note(self):
        self._set_status("draft")
        with self.app.app_context():
            self._form().submitted_at = None
            db.session.commit()
        self.assertNotIn("übermittelt", self.father.get("/eltern/uebersicht").get_data(as_text=True))


class ChangeAfterSubmissionTests(_TwoGuardiansFixture, unittest.TestCase):

    def test_the_second_guardian_may_still_change_the_form(self):
        self.father.get("/eltern/formular")
        response = self._change_first_name(self.father, "Geändert")
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(self._form().data["kind_vorname"], "Geändert")

    def test_a_change_puts_a_reviewed_case_back_on_submitted(self):
        self._set_status("completed")
        self._change_first_name(self.father, "Geändert")
        with self.app.app_context():
            self.assertEqual(self._form().status, "submitted")

    def test_the_school_is_told_about_the_change(self):
        self._change_first_name(self.father, "Geändert")
        (message,) = self.outbox
        self.assertEqual(message["To"], "sekretariat@example.de")
        self.assertIn("Kind, Probe", message["Subject"])
        body = message.get_body(("plain",)).get_content()
        self.assertIn("Vater Kind", body)

    def test_the_parents_are_told_that_the_school_looks_again(self):
        response = self._change_first_name(self.father, "Geändert")
        self.assertIn("sieht sich die Anmeldung noch einmal an",
                      response.get_data(as_text=True))

    def test_the_original_submission_date_survives_a_change(self):
        with self.app.app_context():
            before = self._form().submitted_at
        self._change_first_name(self.father, "Geändert")
        with self.app.app_context():
            self.assertEqual(self._form().submitted_at, before)

    def test_further_changes_do_not_send_a_second_mail(self):
        self._change_first_name(self.father, "Einmal")
        self._change_first_name(self.father, "Zweimal")
        self._change_first_name(self.mother, "Dreimal")
        self.assertEqual(len(self.outbox), 1)

    def test_the_next_change_reports_again_once_the_school_picked_it_up(self):
        self._change_first_name(self.father, "Einmal")
        with self.app.app_context():
            self._form().change_notified_at = None  # wie beim Statuswechsel im Sekretariat
            db.session.commit()
        self._change_first_name(self.father, "Zweimal")
        self.assertEqual(len(self.outbox), 2)

    def test_paging_through_without_editing_reports_nothing(self):
        # Das zweite Elternteil liest das Formular nur durch und klickt weiter.
        for step in rf.STEPS[:3]:
            payload = {field.name: self._current(field.name) for field in step.fields}
            payload["action"] = "next"
            self.father.post(f"/eltern/formular/{step.key}", data=payload, follow_redirects=True)
        self.assertEqual(self.outbox, [])
        with self.app.app_context():
            self.assertEqual(self._form().status, "submitted")

    def test_sending_the_unchanged_form_again_reports_nothing(self):
        # Die Zusammenfassung zeigt die Häkchen gesetzt; ein erneutes "Absenden"
        # schickt darum dieselben Werte zurück und ist keine Änderung.
        payload = {field.name: self._current(field.name) for field in rf.SUMMARY_STEP.fields}
        payload["action"] = "submit"
        self.father.post(f"/eltern/formular/{rf.SUMMARY_STEP.key}", data=payload,
                         follow_redirects=True)
        self.assertEqual(self.outbox, [])
        with self.app.app_context():
            self.assertEqual(self._form().status, "submitted")

    def test_a_draft_change_reports_nothing(self):
        self._set_status("draft")
        self._change_first_name(self.father, "Geändert")
        self.assertEqual(self.outbox, [])

    def test_the_change_is_written_to_the_audit_log(self):
        self._change_first_name(self.father, "Geändert")
        with self.app.app_context():
            actions = [event.action for event in db.session.scalars(db.select(AuditEvent))]
            self.assertIn("registration_changed_after_submission", actions)

    def _current(self, name):
        with self.app.app_context():
            return self._form().data.get(name, "")


class StaffViewTests(_TwoGuardiansFixture, unittest.TestCase):

    def _staff_client(self):
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

    def test_the_detail_view_names_the_author_and_flags_a_later_change(self):
        self._change_first_name(self.father, "Geändert")
        with self.app.app_context():
            registration_id = self._form().id
        body = self._staff_client().get(f"/admin/anmeldungen/{registration_id}").get_data(as_text=True)
        self.assertIn("von Mutter Kind", body)
        self.assertIn("nach dem Absenden geändert", body)

    def test_picking_the_case_up_clears_the_flag(self):
        self._change_first_name(self.father, "Geändert")
        with self.app.app_context():
            registration_id = self._form().id
        client = self._staff_client()
        client.post(f"/admin/anmeldungen/{registration_id}", data={"status": "in_review"},
                    follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(self._form().change_notified_at)


if __name__ == "__main__":
    unittest.main()
