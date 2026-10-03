"""Passwort zurücksetzen und Zugänge löschen in der Benutzerverwaltung.

Beides darf nur die Administration, und der eigene Zugang bleibt gesperrt --
sonst schlösse sich jemand selbst aus.
"""

import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import AOSF, RecoveryCode, Schueler, User, db  # noqa: E402
from models import AuditEvent  # noqa: E402

NEW_PASSWORD = "neues-sicheres-passwort"


class _UserFixture:
    """Eine Administration, eine Kollegin mit gesperrtem Zugang, ein AO-SF-Fall."""

    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            admin = User(username="chefin", role="Administrator",
                         password_hash=generate_password_hash("x"))
            colleague = User(
                username="kollegin", role="Sekretariat",
                password_hash=generate_password_hash("altes-passwort"),
                failed_logins=5,
                locked_until=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
            )
            student = Schueler(vorname="Test", nachname="Kind")
            db.session.add_all([admin, colleague, student])
            db.session.flush()
            db.session.add(AOSF(schueler_id=student.id, leitung_user_id=colleague.id))
            db.session.add(RecoveryCode(user_id=colleague.id,
                                        code_hash=generate_password_hash("notfall")))
            db.session.commit()
            self.admin_id, self.colleague_id = admin.id, colleague.id
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

    def _reset(self, password=NEW_PASSWORD, confirm=None):
        return self.client.post(
            f"/admin/users/{self.colleague_id}/passwort",
            data={"new_password": password,
                  "confirm_password": password if confirm is None else confirm},
            follow_redirects=True)


class PasswordResetTests(_UserFixture, unittest.TestCase):

    def test_the_page_names_the_person(self):
        body = self.client.get(f"/admin/users/{self.colleague_id}/passwort").get_data(as_text=True)
        self.assertIn("kollegin", body)

    def test_the_new_password_takes_effect(self):
        self._reset()
        with self.app.app_context():
            user = db.session.get(User, self.colleague_id)
            self.assertTrue(check_password_hash(user.password_hash, NEW_PASSWORD))
            self.assertFalse(check_password_hash(user.password_hash, "altes-passwort"))

    def test_a_lockout_is_lifted_along_with_it(self):
        self._reset()
        with self.app.app_context():
            user = db.session.get(User, self.colleague_id)
            self.assertIsNone(user.locked_until)
            self.assertEqual(user.failed_logins, 0)

    def test_the_second_factor_is_left_alone(self):
        # Ein vergessenes Passwort sagt nichts über das Gerät der Person.
        with self.app.app_context():
            user = db.session.get(User, self.colleague_id)
            user.totp_secret = "GEHEIM"
            user.totp_confirmed_at = datetime.datetime.now(datetime.UTC)
            db.session.commit()
        self._reset()
        with self.app.app_context():
            self.assertEqual(db.session.get(User, self.colleague_id).totp_secret, "GEHEIM")

    def test_a_short_password_is_refused(self):
        response = self._reset(password="kurz")
        self.assertIn("Mindestens 10 Zeichen", response.get_data(as_text=True))
        with self.app.app_context():
            user = db.session.get(User, self.colleague_id)
            self.assertTrue(check_password_hash(user.password_hash, "altes-passwort"))

    def test_a_mistyped_confirmation_is_refused(self):
        response = self._reset(confirm="etwas-anderes-ganz")
        self.assertIn("stimmen nicht überein", response.get_data(as_text=True))
        with self.app.app_context():
            user = db.session.get(User, self.colleague_id)
            self.assertTrue(check_password_hash(user.password_hash, "altes-passwort"))

    def test_the_reset_is_written_to_the_audit_log(self):
        self._reset()
        with self.app.app_context():
            actions = [event.action for event in db.session.scalars(db.select(AuditEvent))]
            self.assertIn("staff_password_reset", actions)

    def test_only_administrators_may_reset(self):
        client = self._login(self.colleague_id)
        response = client.get(f"/admin/users/{self.admin_id}/passwort")
        self.assertNotEqual(response.status_code, 200)


class AccountDeletionTests(_UserFixture, unittest.TestCase):

    def test_an_account_can_be_deleted(self):
        self.client.post(f"/admin/users/{self.colleague_id}/loeschen", follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(db.session.get(User, self.colleague_id))

    def test_the_own_account_is_protected(self):
        response = self.client.post(f"/admin/users/{self.admin_id}/loeschen",
                                    follow_redirects=True)
        self.assertIn("eigenen Zugang", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User, self.admin_id))

    def test_one_administrator_always_remains(self):
        # Weil nur die Administration hierher kommt und sich selbst nicht
        # löschen kann, bleibt stets mindestens ein Zugang übrig.
        with self.app.app_context():
            second = User(username="zweite", role="Administrator",
                          password_hash=generate_password_hash("x"))
            db.session.add(second)
            db.session.commit()
            second_id = second.id
        self.client.post(f"/admin/users/{second_id}/loeschen", follow_redirects=True)
        self.client.post(f"/admin/users/{self.admin_id}/loeschen", follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(
                db.session.query(User).filter_by(role="Administrator").count(), 1)

    def test_led_procedures_survive_without_their_lead(self):
        self.client.post(f"/admin/users/{self.colleague_id}/loeschen", follow_redirects=True)
        with self.app.app_context():
            case = db.session.query(AOSF).one()
            self.assertIsNone(case.leitung_user_id)

    def test_recovery_codes_go_with_the_account(self):
        self.client.post(f"/admin/users/{self.colleague_id}/loeschen", follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(db.session.query(RecoveryCode).count(), 0)

    def test_the_deletion_is_written_to_the_audit_log(self):
        self.client.post(f"/admin/users/{self.colleague_id}/loeschen", follow_redirects=True)
        with self.app.app_context():
            actions = [event.action for event in db.session.scalars(db.select(AuditEvent))]
            self.assertIn("staff_account_deleted", actions)

    def test_only_administrators_may_delete(self):
        client = self._login(self.colleague_id)
        response = client.post(f"/admin/users/{self.admin_id}/loeschen")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User, self.admin_id))

    def test_the_list_offers_both_actions(self):
        body = self.client.get("/admin/users").get_data(as_text=True)
        self.assertIn(f"/admin/users/{self.colleague_id}/passwort", body)
        self.assertIn(f"/admin/users/{self.colleague_id}/loeschen", body)
        # Beim eigenen Zugang fehlt die Löschen-Schaltfläche.
        self.assertNotIn(f"/admin/users/{self.admin_id}/loeschen", body)


if __name__ == "__main__":
    unittest.main()
