import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from app import create_app  # noqa: E402
from models import Schueler, db  # noqa: E402
from sl_office.parent_portal.access_service import (  # noqa: E402
    InvalidAccessToken, ParentAccessLimitReached, activate_parent_access,
    consume_activation_grant, consume_login_token, create_activation_grant,
    create_login_token, hash_token,
)
from sl_office.parent_portal.models import ActivationGrant, ParentAccess, ParentLoginToken  # noqa: E402


class ParentAccessTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            student = Schueler(vorname="Portal", nachname="Kind")
            db.session.add(student)
            db.session.commit()
            self.student_id = student.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_only_token_hash_is_persisted(self):
        with self.app.app_context():
            grant, token = create_activation_grant(self.student_id, "first_access")
            db.session.commit()
            self.assertNotEqual(grant.token_hash, token)
            self.assertEqual(grant.token_hash, hash_token(token))
            self.assertGreaterEqual(len(token), 40)

    def test_email_is_normalized_and_access_is_idempotent(self):
        with self.app.app_context():
            first = activate_parent_access(self.student_id, " Parent@Example.DE ", "Elternteil")
            db.session.commit()
            second = activate_parent_access(self.student_id, "parent@example.de", "Elternteil")
            self.assertEqual(first.id, second.id)
            self.assertEqual(first.email_normalized, "parent@example.de")

    def test_third_parent_access_is_rejected(self):
        with self.app.app_context():
            activate_parent_access(self.student_id, "one@example.de", "Eins")
            activate_parent_access(self.student_id, "two@example.de", "Zwei")
            db.session.flush()
            with self.assertRaises(ParentAccessLimitReached):
                activate_parent_access(self.student_id, "three@example.de", "Drei")

    def test_activation_token_is_one_time_and_only_hash_is_stored(self):
        with self.app.app_context():
            grant, token = create_activation_grant(self.student_id, "first_access")
            db.session.commit()
            access = consume_activation_grant(token, "parent@example.de", "Elternteil")
            db.session.commit()
            self.assertEqual(access.status, "active")
            self.assertNotEqual(grant.token_hash, token)
            with self.assertRaises(InvalidAccessToken):
                consume_activation_grant(token, "parent@example.de", "Elternteil")

    def test_expired_activation_token_is_rejected(self):
        with self.app.app_context():
            grant, token = create_activation_grant(self.student_id, "first_access")
            grant.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
            db.session.commit()
            with self.assertRaises(InvalidAccessToken):
                consume_activation_grant(token, "parent@example.de", "Elternteil")

    def test_login_token_is_short_lived_and_one_time(self):
        with self.app.app_context():
            access = activate_parent_access(self.student_id, "parent@example.de", "Elternteil")
            access.status = "active"
            db.session.commit()
            login_token, token = create_login_token(access.id)
            db.session.commit()
            self.assertNotEqual(login_token.token_hash, token)
            self.assertEqual(consume_login_token(token).id, access.id)
            db.session.commit()
            with self.assertRaises(InvalidAccessToken):
                consume_login_token(token)

    def test_invalid_email_is_rejected(self):
        with self.app.app_context():
            with self.assertRaises(ValueError):
                activate_parent_access(self.student_id, "keine-mailadresse", "Elternteil")


if __name__ == "__main__":
    unittest.main()
