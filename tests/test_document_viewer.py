"""Die Dokumentenansicht der Schülerakte.

Hochgeladene Berichte erscheinen in einem eingebetteten Fenster der
Schülerakte. Das klappt nur, wenn die Antwort sich von SL-Office selbst
einbetten lässt; alle anderen Seiten bleiben gegen Einbettung gesperrt.
"""

import os
import tempfile
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import SchulaerztlicheUntersuchung, Schueler, User, db  # noqa: E402


class DocumentViewerTests(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.app = create_app("testing", {"UPLOAD_FOLDER": self.ordner.name})
        with open(os.path.join(self.ordner.name, "schularzt.pdf"), "wb") as datei:
            datei.write(b"%PDF-1.4\n%%EOF\n")
        with self.app.app_context():
            admin = User(username="chefin", role="Administrator",
                         password_hash=generate_password_hash("x"))
            kind = Schueler(vorname="Mia", nachname="Muster")
            db.session.add_all([admin, kind])
            db.session.flush()
            db.session.add(SchulaerztlicheUntersuchung(schueler_id=kind.id,
                                                       pdf_dateiname="schularzt.pdf"))
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

    def test_ein_hochgeladener_bericht_laesst_sich_in_der_akte_einbetten(self):
        antwort = self.client.get("/uploads/schularzt.pdf")
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.mimetype, "application/pdf")
        self.assertEqual(antwort.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", antwort.headers["Content-Security-Policy"])
        antwort.close()

    def test_andere_seiten_bleiben_gegen_einbettung_gesperrt(self):
        antwort = self.client.get(f"/schueler/{self.kind_id}")
        self.assertEqual(antwort.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", antwort.headers["Content-Security-Policy"])
        self.assertIn('id="pdfFrame"', antwort.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
