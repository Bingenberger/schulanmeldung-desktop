"""Vorlagen der Schule und der verbesserte Import der Liste der Stadt."""

import os
import unittest
from io import BytesIO

os.environ["SL_OFFICE_ENV"] = "testing"

from pypdf import PdfReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Schueler, User, db  # noqa: E402
from sl_office import vorlagen  # noqa: E402
from sl_office.students import city_import  # noqa: E402


def _pdf(seiten=2, text="Aufgabe"):
    puffer = BytesIO()
    blatt = canvas.Canvas(puffer)
    for nummer in range(1, seiten + 1):
        blatt.drawString(100, 700, f"{text} {nummer}")
        blatt.showPage()
    blatt.save()
    return puffer.getvalue()


def _text(payload):
    return " ".join(seite.extract_text() or "" for seite in PdfReader(BytesIO(payload)).pages)


class _Fixture:
    def setUp(self):
        self.app = create_app("testing", {"SCHOOL_TOWN": "Musterstadt",
                                          "SCHOOL_NAME": "Grundschule am Park"})
        with self.app.app_context():
            admin = User(username="chefin", role="Administrator",
                         password_hash=generate_password_hash("x"))
            db.session.add(admin)
            db.session.commit()
            self.admin_id = admin.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_id)
            sess["_fresh"] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()


class LaufzettelTests(_Fixture, unittest.TestCase):

    def test_the_markup_becomes_sections_and_items(self):
        abschnitte = vorlagen.laufzettel_abschnitte(
            "# Unterlagen\n- Anmeldeschein der Stadt {stadt}\n~ oder\n"
            "- OGS: Ja | Nein\nohne Strich\n", schule={"town": "Musterstadt"})
        self.assertEqual(len(abschnitte), 1)
        titel, punkte = abschnitte[0]
        self.assertEqual(titel, "Unterlagen")
        self.assertEqual(punkte[0], vorlagen.Kasten("Anmeldeschein der Stadt Musterstadt"))
        self.assertEqual(punkte[1], vorlagen.Zwischenwort("oder"))
        self.assertEqual(punkte[2], vorlagen.Reihe("OGS", ("Ja", "Nein")))
        self.assertEqual(punkte[3], vorlagen.Kasten("ohne Strich"))

    def test_the_default_names_the_town_from_the_profile(self):
        payload = self.client.get("/admin/vorlagen/vorschau/laufzettel").data
        self.assertIn("Anmeldeschein der Stadt Musterstadt", _text(payload))
        self.assertNotIn("Niederkassel", _text(payload))

    def test_a_saved_checklist_is_printed(self):
        response = self.client.post("/admin/vorlagen", data={
            "action": "laufzettel",
            "laufzettel": "# Mitbringen\n- Impfpass\n- Foto für den Schülerausweis\n"})
        self.assertEqual(response.status_code, 302)
        text = _text(self.client.get("/admin/vorlagen/vorschau/laufzettel").data)
        self.assertIn("Foto für den Schülerausweis", text)
        self.assertNotIn("Masernschutz", text)

        self.client.post("/admin/vorlagen", data={"action": "laufzettel_standard"})
        self.assertIn("Masernschutz", _text(self.client.get("/admin/vorlagen/vorschau/laufzettel").data))

    def test_an_empty_checklist_is_refused(self):
        response = self.client.post("/admin/vorlagen", data={
            "action": "laufzettel", "laufzettel": "# Nur eine Überschrift\n"},
            follow_redirects=True)
        self.assertIn("keinen einzigen Punkt", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertTrue(vorlagen.ist_standard())


class MaterialTests(_Fixture, unittest.TestCase):

    def _hochladen(self, payload, name="Material.pdf"):
        return self.client.post("/admin/vorlagen", data={
            "action": "material", "material": (BytesIO(payload), name)},
            content_type="multipart/form-data", follow_redirects=True)

    def test_material_is_embedded_in_the_protocol(self):
        self._hochladen(_pdf(3, "Gesprächsleitfaden"))
        page = self.client.get("/admin/vorlagen").get_data(as_text=True)
        self.assertIn("3 Seiten", page)
        text = _text(self.client.get("/admin/vorlagen/vorschau/protokoll").data)
        self.assertIn("Gesprächsleitfaden 3", text)
        self.assertIn("Mia", text)

        self.client.post("/admin/vorlagen", data={"action": "material_entfernen"})
        text = _text(self.client.get("/admin/vorlagen/vorschau/protokoll").data)
        self.assertNotIn("Gesprächsleitfaden", text)

    def test_non_pdf_files_are_refused(self):
        response = self._hochladen(b"keine PDF-Datei", "notiz.pdf")
        self.assertIn("PDF-Datei", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(vorlagen.material())

    def test_broken_pdfs_are_refused(self):
        response = self._hochladen(b"%PDF-1.4 kaputt", "kaputt.pdf")
        self.assertIn("beschädigt", response.get_data(as_text=True))


CSV_SEMIKOLON = (
    "Familienname;Rufname;Geburtsdatum;Straße;Hausnr.;PLZ;Wohnort\n"
    "Müller;Jörg;03.02.2021;Hauptstraße;5a;12345;Musterstadt\n"
).encode("cp1252")


class CityImportTests(_Fixture, unittest.TestCase):

    def test_a_windows_csv_with_semicolons_is_read(self):
        token, headers, preview = city_import.stage_upload(CSV_SEMIKOLON, "liste.csv")
        self.addCleanup(city_import.discard_staged, token)
        self.assertIn("Hausnr.", headers)
        self.assertEqual(preview[0]["Rufname"], "Jörg")

    def test_house_numbers_in_their_own_column_are_joined(self):
        token, headers, _ = city_import.stage_upload(CSV_SEMIKOLON, "liste.csv")
        self.addCleanup(city_import.discard_staged, token)
        with self.app.app_context():
            mapping = city_import.suggest_mapping(headers)
            self.assertEqual(mapping["strasse"], "Straße")
            mapping["hausnummer"] = "Hausnr."
            city_import.import_rows(city_import.load_staged(token), mapping)
            self.assertEqual(Schueler.query.one().strasse, "Hauptstraße 5a")

    def test_the_last_mapping_is_remembered(self):
        token, headers, _ = city_import.stage_upload(CSV_SEMIKOLON, "liste.csv")
        self.addCleanup(city_import.discard_staged, token)
        with self.app.app_context():
            mapping = city_import.suggest_mapping(headers)
            # Die Schule ordnet "Wohnort" ausnahmsweise nicht zu.
            mapping.pop("ort", None)
            mapping["hausnummer"] = "Hausnr."
            city_import.import_rows(city_import.load_staged(token), mapping)
            neu = city_import.suggest_mapping(headers)
        self.assertEqual(neu["hausnummer"], "Hausnr.")
        self.assertEqual(neu["nachname"], "Familienname")

    def test_the_upload_form_accepts_csv(self):
        response = self.client.post("/import/stadt", data={
            "file": (BytesIO(CSV_SEMIKOLON), "liste.csv")}, content_type="multipart/form-data")
        page = response.get_data(as_text=True)
        self.assertIn("Spalten zuordnen", page)
        self.assertIn("Hausnr.", page)


if __name__ == "__main__":
    unittest.main()
