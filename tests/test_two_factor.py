"""Second factor and login lockout for staff accounts."""

import datetime
import os
import time
import unittest
from unittest import mock

os.environ["SL_OFFICE_ENV"] = "testing"

import pyotp  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import RecoveryCode, User, db  # noqa: E402
from sl_office.auth import two_factor  # noqa: E402


class TwoFactorServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            user = User(username="chef", password_hash=generate_password_hash("pw"), role="Administrator")
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _enrolled(self):
        user = db.session.get(User, self.user_id)
        user.totp_secret = two_factor.generate_secret()
        user.totp_confirmed_at = two_factor.utcnow()
        db.session.flush()
        return user

    def test_valid_code_is_accepted(self):
        with self.app.app_context():
            user = self._enrolled()
            code = pyotp.TOTP(user.totp_secret, interval=30).now()
            self.assertTrue(two_factor.verify_totp(user, code))

    def test_code_cannot_be_replayed(self):
        with self.app.app_context():
            user = self._enrolled()
            code = pyotp.TOTP(user.totp_secret, interval=30).now()
            self.assertTrue(two_factor.verify_totp(user, code))
            # Same code, same 30 s window: a captured code must not work twice.
            self.assertFalse(two_factor.verify_totp(user, code))

    def test_previous_window_is_accepted_once_for_clock_drift(self):
        with self.app.app_context():
            user = self._enrolled()
            now = time.time()
            previous = pyotp.TOTP(user.totp_secret, interval=30).at(now - 30)
            self.assertTrue(two_factor.verify_totp(user, previous, at=now))

    def test_old_code_is_rejected_after_a_newer_one_was_used(self):
        with self.app.app_context():
            user = self._enrolled()
            now = time.time()
            totp = pyotp.TOTP(user.totp_secret, interval=30)
            self.assertTrue(two_factor.verify_totp(user, totp.at(now), at=now))
            self.assertFalse(two_factor.verify_totp(user, totp.at(now - 30), at=now))

    def test_wrong_and_malformed_codes_are_rejected(self):
        with self.app.app_context():
            user = self._enrolled()
            for candidate in ("", "abc", "12345", "1234567", "000000 "):
                self.assertFalse(two_factor.verify_totp(user, candidate), candidate)

    def test_code_is_rejected_without_a_secret(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            self.assertFalse(two_factor.verify_totp(user, "123456"))

    def test_recovery_code_works_once(self):
        with self.app.app_context():
            user = self._enrolled()
            codes = two_factor.generate_recovery_codes(user)
            self.assertEqual(len(codes), two_factor.RECOVERY_CODE_COUNT)
            self.assertTrue(two_factor.consume_recovery_code(user, codes[0]))
            self.assertFalse(two_factor.consume_recovery_code(user, codes[0]))
            self.assertEqual(two_factor.unused_recovery_code_count(user), len(codes) - 1)

    def test_recovery_codes_are_only_stored_as_digests(self):
        with self.app.app_context():
            user = self._enrolled()
            codes = two_factor.generate_recovery_codes(user)
            db.session.commit()
            stored = [row.code_hash for row in RecoveryCode.query.all()]
            for raw in codes:
                self.assertNotIn(raw, stored)

    def test_regenerating_invalidates_the_old_codes(self):
        with self.app.app_context():
            user = self._enrolled()
            old = two_factor.generate_recovery_codes(user)
            two_factor.generate_recovery_codes(user)
            self.assertFalse(two_factor.consume_recovery_code(user, old[0]))

    def test_lockout_starts_only_after_the_free_attempts(self):
        with self.app.app_context():
            user = self._enrolled()
            for _ in range(two_factor.MAX_FREE_ATTEMPTS):
                two_factor.register_failed_attempt(user)
            self.assertEqual(two_factor.lock_remaining_seconds(user), 0)
            two_factor.register_failed_attempt(user)
            self.assertGreater(two_factor.lock_remaining_seconds(user), 0)

    def test_lockout_grows_with_further_attempts(self):
        with self.app.app_context():
            user = self._enrolled()
            waits = []
            for _ in range(two_factor.MAX_FREE_ATTEMPTS + 3):
                two_factor.register_failed_attempt(user)
                waits.append(two_factor.lock_remaining_seconds(user))
            self.assertLess(waits[-3], waits[-1])

    def test_successful_login_clears_the_counter(self):
        with self.app.app_context():
            user = self._enrolled()
            for _ in range(6):
                two_factor.register_failed_attempt(user)
            two_factor.clear_failed_attempts(user)
            self.assertEqual(two_factor.lock_remaining_seconds(user), 0)
            self.assertEqual(user.failed_logins, 0)
            self.assertIsNotNone(user.last_login_at)

    def test_reset_removes_secret_and_codes(self):
        with self.app.app_context():
            user = self._enrolled()
            two_factor.generate_recovery_codes(user)
            db.session.commit()
            two_factor.reset_two_factor(user)
            db.session.commit()
            self.assertFalse(user.two_factor_active)
            self.assertIsNone(user.totp_secret)
            self.assertEqual(RecoveryCode.query.count(), 0)

    def test_qr_svg_encodes_the_provisioning_uri(self):
        with self.app.app_context():
            secret = two_factor.generate_secret()
            uri = two_factor.provisioning_uri("chef", secret, "GGS")
            self.assertIn("otpauth://totp/", uri)
            self.assertIn(secret, uri)
            markup = two_factor.qr_svg(uri)
            self.assertTrue(markup.startswith("<svg"))
            self.assertIn("<rect", markup)
            self.assertNotEqual(markup, two_factor.qr_svg(uri + "x"))


class LoginFlowTests(unittest.TestCase):
    """The routes, from the outside: a password alone must not be enough."""

    def setUp(self):
        self.app = create_app("testing")
        self.client = self.app.test_client()
        with self.app.app_context():
            db.session.add(User(username="chef", password_hash=generate_password_hash("pw"),
                                role="Administrator"))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _password_step(self, password="pw"):
        return self.client.post("/login", data={"username": "chef", "password": password},
                                follow_redirects=True)

    def _enrol(self):
        """Complete first-login enrolment and return the shared secret."""
        self._password_step()
        page = self.client.get("/login/einrichten").get_data(as_text=True)
        with self.app.app_context():
            pass
        with self.client.session_transaction() as flask_session:
            secret = flask_session["pending_2fa_secret"]
        self.client.post("/login/einrichten",
                         data={"code": pyotp.TOTP(secret, interval=30).now()},
                         follow_redirects=True)
        return secret

    def test_password_alone_does_not_open_the_application(self):
        response = self._password_step()
        self.assertIn("Zwei-Faktor", response.get_data(as_text=True))
        # Not logged in: a protected page still bounces.
        self.assertEqual(self.client.get("/liste").status_code, 302)

    def test_second_step_is_unreachable_without_the_password_step(self):
        response = self.client.get("/login/bestaetigen", follow_redirects=True)
        self.assertIn("erneut an", response.get_data(as_text=True))

    def test_enrolment_then_login_with_a_code(self):
        secret = self._enrol()
        with self.app.app_context():
            self.assertTrue(User.query.filter_by(username="chef").one().two_factor_active)
        self.assertEqual(self.client.get("/liste").status_code, 200)

        self.client.get("/logout")
        self._password_step()
        self.assertEqual(self.client.get("/liste").status_code, 302)  # still pending
        # Enrolment already burned the current time step, so a real second
        # login necessarily happens in a later window. Only the step function
        # is moved: patching time.time itself would also date the session
        # cookie into the future and invalidate it.
        later = time.time() + 60
        with mock.patch("sl_office.auth.two_factor._current_step",
                        return_value=int(later // two_factor.TOTP_INTERVAL)):
            self.client.post("/login/bestaetigen",
                             data={"code": pyotp.TOTP(secret, interval=30).at(later)},
                             follow_redirects=True)
        self.assertEqual(self.client.get("/liste").status_code, 200)

    def test_wrong_code_keeps_the_session_closed(self):
        self._enrol()
        self.client.get("/logout")
        self._password_step()
        self.client.post("/login/bestaetigen", data={"code": "000000"}, follow_redirects=True)
        self.assertEqual(self.client.get("/liste").status_code, 302)

    def test_repeated_wrong_passwords_lock_the_account(self):
        for _ in range(two_factor.MAX_FREE_ATTEMPTS + 1):
            self._password_step(password="falsch")
        response = self._password_step()  # correct password, but locked now
        self.assertIn("Fehlversuche", response.get_data(as_text=True))

    def test_the_same_code_cannot_be_used_for_a_second_login(self):
        secret = self._enrol()
        self.client.get("/logout")
        self._password_step()
        # Deliberately the code from the enrolment window.
        self.client.post("/login/bestaetigen",
                         data={"code": pyotp.TOTP(secret, interval=30).now()},
                         follow_redirects=True)
        self.assertEqual(self.client.get("/liste").status_code, 302)

    def test_recovery_code_lets_a_locked_out_colleague_in(self):
        self._enrol()
        with self.app.app_context():
            user = User.query.one()
            codes = two_factor.generate_recovery_codes(user)
            db.session.commit()
        self.client.get("/logout")
        self._password_step()
        self.client.post("/login/bestaetigen", data={"code": codes[0]}, follow_redirects=True)
        self.assertEqual(self.client.get("/liste").status_code, 200)

    def test_wrong_password_does_not_reveal_unknown_users(self):
        known = self._password_step(password="falsch").get_data(as_text=True)
        unknown = self.client.post("/login", data={"username": "gibtsnicht", "password": "x"},
                                   follow_redirects=True).get_data(as_text=True)
        self.assertIn("Ungültiger Benutzername oder Passwort", known)
        self.assertIn("Ungültiger Benutzername oder Passwort", unknown)


if __name__ == "__main__":
    unittest.main()
