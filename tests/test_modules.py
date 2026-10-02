"""Module ein- und ausschalten.

Ein abgeschaltetes Modul verschwindet aus Menü, Startseite und Schülerakte,
seine Seiten antworten mit 404. Die Daten bleiben dabei unberührt.
"""

import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Schueler, User, db  # noqa: E402
from sl_office import features  # noqa: E402


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            admin = User(username="chefin", role="Administrator",
                         password_hash=generate_password_hash("x"))
            kind = Schueler(vorname="Mia", nachname="Muster")
            db.session.add_all([admin, kind])
            db.session.commit()
            self.admin_id, self.kind_id = admin.id, kind.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_id)
            sess["_fresh"] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _switch(self, *enabled):
        return self.client.post("/admin/module", data={"modules": list(enabled)})

    def test_everything_is_on_by_default(self):
        with self.app.app_context():
            self.assertTrue(all(features.module_states().values()))
        page = self.client.get("/admin/module").get_data(as_text=True)
        for module in features.MODULES:
            self.assertIn(module.label, page)

    def test_open_day_starts_switched_off_without_portal(self):
        app = create_app("testing", {"PARENT_PORTAL_ENABLED": False})
        with app.app_context():
            states = features.module_states()
            self.assertFalse(states["tag_der_offenen_tuer"])
            self.assertTrue(states["schulspiel"])
            db.drop_all()

    def test_switched_off_modules_disappear(self):
        everything_but = [key for key in features.MODULE_KEYS
                          if key not in {"schulspiel", "aosf", "klassenbildung"}]
        self.assertEqual(self._switch(*everything_but).status_code, 302)

        hub = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("target=schulspiel", hub.replace("/select/", "target="))
        self.assertNotIn("AO-SF", hub)
        self.assertNotIn("Klassen zusammenstellen", hub)
        self.assertIn("Pädagogische Diagnostik", hub)

        detail = self.client.get(f"/schueler/{self.kind_id}").get_data(as_text=True)
        self.assertNotIn(f"/schueler/{self.kind_id}/schulspiel", detail)
        self.assertNotIn(f"/schueler/{self.kind_id}/aosf", detail)
        self.assertIn(f"/schueler/{self.kind_id}/diagnostik", detail)

    def test_pages_of_switched_off_modules_answer_404(self):
        self._switch(*[key for key in features.MODULE_KEYS if key not in {"schulspiel", "klassenbildung"}])
        self.assertEqual(self.client.get(f"/schueler/{self.kind_id}/schulspiel").status_code, 404)
        self.assertEqual(self.client.get("/select/schulspiel").status_code, 404)
        self.assertEqual(self.client.get("/klassen").status_code, 404)
        self.assertEqual(self.client.get("/select/freunde").status_code, 404)
        self.assertEqual(self.client.get(f"/schueler/{self.kind_id}/diagnostik").status_code, 200)

    def test_switching_back_on_restores_the_module(self):
        self._switch()
        self.assertEqual(self.client.get("/klassen").status_code, 404)
        self._switch(*features.MODULE_KEYS)
        self.assertEqual(self.client.get("/klassen").status_code, 200)

    def test_unknown_module_names_are_ignored(self):
        self._switch("schulspiel", "gibt_es_nicht")
        with self.app.app_context():
            states = features.module_states()
        self.assertTrue(states["schulspiel"])
        self.assertNotIn("gibt_es_nicht", states)
        self.assertFalse(states["aosf"])


if __name__ == "__main__":
    unittest.main()
