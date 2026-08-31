import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import app, create_app  # noqa: E402
from models import User, db  # noqa: E402


class ApplicationAccessTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_login_page_is_available_with_csrf_metadata(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="csrf-token"', response.data)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def _complete_two_factor(self):
        """Walk through first-login enrolment and return the shared secret."""
        import pyotp

        self.client.get("/login/einrichten")
        with self.client.session_transaction() as flask_session:
            secret = flask_session["pending_2fa_secret"]
        self.client.post(
            "/login/einrichten",
            data={"code": pyotp.TOTP(secret, interval=30).now()},
            follow_redirects=True,
        )
        return secret

    def test_staff_can_login_and_logout_through_auth_blueprint(self):
        with app.app_context():
            user = User(
                username="auth-blueprint-test",
                password_hash=generate_password_hash("correct-test-password"),
                role="Administrator",
            )
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        # Registered before the assertions so a failing test cannot leave an
        # administrator account with a repository-known password behind.
        self.addCleanup(self._remove_user, user_id)

        response = self.client.post(
            "/login",
            data={"username": "auth-blueprint-test", "password": "correct-test-password"},
            follow_redirects=False,
        )
        # The password is only the first of two steps: it leads to the second
        # factor, never straight into the application.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login/einrichten")
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 302)

        secret = self._complete_two_factor()
        self.assertEqual(self.client.get("/").status_code, 200)

        response = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 302)

    @staticmethod
    def _remove_user(user_id):
        with app.app_context():
            user = db.session.get(User, user_id)
            if user is not None:
                db.session.delete(user)
                db.session.commit()

    def test_internal_start_page_requires_login(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login?next=%2F"))

    def test_factory_creates_an_independent_app_with_existing_routes(self):
        second_app = create_app("testing")
        self.assertIsNot(second_app, app)
        response = second_app.test_client().get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("auth.login", second_app.view_functions)
        self.assertIn("admin.users", second_app.view_functions)
        self.assertIn("students.add", second_app.view_functions)
        self.assertIn("students.detail", second_app.view_functions)
        self.assertIn("students.list_students", second_app.view_functions)
        self.assertIn("appointments.index", second_app.view_functions)
        self.assertNotIn("add_schueler", second_app.view_functions)

    def test_json_write_without_csrf_token_is_rejected(self):
        previous = app.config["WTF_CSRF_ENABLED"]
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            response = self.client.post(
                "/api/klassen/assign",
                json={"schueler_id": 1, "klasse": "1a"},
            )
        finally:
            app.config["WTF_CSRF_ENABLED"] = previous
        self.assertEqual(response.status_code, 400)

    def test_unmanaged_upload_is_not_exposed_to_authenticated_user(self):
        with app.app_context():
            user = User(username="document-test", password_hash="unused", role="Administrator")
            db.session.add(user)
            db.session.commit()
            user_id = user.id

        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

        response = self.client.get("/uploads/not-referenced.pdf")
        self.assertEqual(response.status_code, 404)

        with app.app_context():
            db.session.delete(db.session.get(User, user_id))
            db.session.commit()


if __name__ == "__main__":
    unittest.main()
