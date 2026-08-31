import os
import unittest
from unittest.mock import patch

from flask import Flask

from config import load_config
from security import init_security


class ConfigurationTests(unittest.TestCase):
    def test_testing_configuration_is_isolated(self):
        app = Flask(__name__)
        load_config(app, "testing")
        self.assertTrue(app.config["TESTING"])
        self.assertEqual(app.config["SQLALCHEMY_DATABASE_URI"], "sqlite:///:memory:")
        self.assertFalse(app.config["WTF_CSRF_ENABLED"])

    def test_production_requires_secret_and_database_url(self):
        app = Flask(__name__)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SL_OFFICE_SECRET_KEY"):
                load_config(app, "production")

    def test_production_rejects_short_secret(self):
        app = Flask(__name__)
        environment = {"SL_OFFICE_SECRET_KEY": "too-short", "SL_OFFICE_DATABASE_URL": "postgresql://db/sl_office"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "mindestens 32"):
                load_config(app, "production")


class SecurityHeaderTests(unittest.TestCase):
    def make_app(self, environment="testing"):
        app = Flask(__name__)
        load_config(app, environment)
        init_security(app)

        @app.get("/")
        def index():
            return "ok"

        return app

    def test_baseline_security_headers_are_present(self):
        response = self.make_app().test_client().get("/")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("Strict-Transport-Security", response.headers)

    def test_hsts_is_enabled_in_production(self):
        environment = {"SL_OFFICE_SECRET_KEY": "x" * 32, "SL_OFFICE_DATABASE_URL": "sqlite:///:memory:"}
        with patch.dict(os.environ, environment, clear=True):
            response = self.make_app("production").test_client().get("/")
        self.assertIn("max-age=31536000", response.headers["Strict-Transport-Security"])


if __name__ == "__main__":
    unittest.main()
