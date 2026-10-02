"""Frei anlegbare Kriterien für Diagnostik, Schulspiel und Schularzt."""

import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import (  # noqa: E402
    Diagnostik, SchulaerztlicheUntersuchung, Schueler, SchulspielDiagnostik, User, db,
)
from sl_office.criteria import service  # noqa: E402
from sl_office.criteria.catalog import STANDARD  # noqa: E402
from sl_office.criteria.models import Kriterium, KriteriumWert  # noqa: E402


class _Fixture:
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            admin = User(username="chefin", role="Administrator",
                         password_hash=generate_password_hash("x"))
            kind = Schueler(vorname="Mia", nachname="Muster", geburtsdatum=datetime.date(2020, 3, 1))
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

    def _kriterien(self, bogen):
        with self.app.app_context():
            liste = service.kriterien(bogen)
            db.session.commit()
            return [(k.id, k.altfeld, k.typ) for k in liste]

    def _diagnostik_formular(self, wert="2", **extra):
        daten = {f"k{kid}": wert for kid, _, typ in self._kriterien("diagnostik") if typ == "skala"}
        daten.update(gesamteindruck_kognitiv="2", gesamteindruck_verhalten="3", schulspiel="0")
        daten.update(extra)
        return daten


class CatalogTests(_Fixture, unittest.TestCase):

    def test_the_standard_catalog_is_created_once(self):
        with self.app.app_context():
            self.assertTrue(service.ensure_catalog("diagnostik"))
            self.assertFalse(service.ensure_catalog("diagnostik"))
            db.session.commit()
            for bogen, eintraege in STANDARD.items():
                self.assertEqual(len(service.kriterien(bogen)), len(eintraege), bogen)

    def test_values_from_the_old_fixed_columns_are_taken_over(self):
        with self.app.app_context():
            db.session.add(Diagnostik(schueler_id=self.kind_id, wortschatz=3, reimen=0))
            db.session.add(SchulspielDiagnostik(schueler_id=self.kind_id, konzentration=1))
            db.session.add(SchulaerztlicheUntersuchung(
                schueler_id=self.kind_id, sehfaehigkeit="auffällig", foerder_grobmotorik=True,
                foerder_sprache="Artikulation,Wortschatz", datum=datetime.date(2026, 5, 4)))
            db.session.commit()

            diag = {k.altfeld: wert for k, wert in service.eintraege(self.kind_id, "diagnostik")}
            self.assertEqual(diag["wortschatz"], 3)
            self.assertEqual(diag["reimen"], 0)
            self.assertIsNone(diag["grammatik"])
            spiel = {k.altfeld: wert for k, wert in service.eintraege(self.kind_id, "schulspiel")}
            self.assertEqual(spiel["konzentration"], 1)
            arzt = {k.altfeld: wert for k, wert in service.eintraege(self.kind_id, "schularzt")}
            self.assertEqual(arzt["sehfaehigkeit"], "auffällig")
            self.assertEqual(arzt["datum"], datetime.date(2026, 5, 4))
            self.assertEqual(arzt["foerder_sprache"], ["Artikulation", "Wortschatz"])
            self.assertEqual(service.foerderhinweise(self.kind_id),
                             ["Grobmotorik", "Artikulation", "Wortschatz"])


class RecordingTests(_Fixture, unittest.TestCase):

    def test_diagnostik_values_are_saved_as_criteria(self):
        response = self.client.post(f"/schueler/{self.kind_id}/diagnostik",
                                    data=self._diagnostik_formular("3"))
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            werte = service.werte(self.kind_id, "diagnostik")
            self.assertEqual(set(werte.values()), {3})
            self.assertEqual(db.session.get(Schueler, self.kind_id).diag_status, "Abgeschlossen")
            self.assertEqual(Diagnostik.query.one().gesamteindruck_kognitiv, 2)

    def test_missing_required_values_are_reported_and_nothing_is_saved(self):
        daten = self._diagnostik_formular("1")
        erstes = next(key for key in daten if key.startswith("k"))
        del daten[erstes]
        response = self.client.post(f"/schueler/{self.kind_id}/diagnostik", data=daten)
        self.assertEqual(response.status_code, 200)
        self.assertIn("bitte angeben", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(KriteriumWert.query.count(), 0)
            self.assertIsNone(Diagnostik.query.first())

    def test_a_new_criterion_appears_in_the_form_and_is_saved(self):
        response = self.client.post("/admin/kriterien/diagnostik/neu", data={
            "bezeichnung": "Stifthaltung", "gruppe": "Zeichnen", "typ": "auswahl",
            "optionen": "Faustgriff\nDreipunktgriff\n", "pflicht": "1"})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            neu = Kriterium.query.filter_by(bezeichnung="Stifthaltung").one()
            self.assertEqual(neu.optionsliste, ["Faustgriff", "Dreipunktgriff"])
        formular = self.client.get(f"/schueler/{self.kind_id}/diagnostik").get_data(as_text=True)
        self.assertIn("Stifthaltung", formular)
        self.assertIn("Dreipunktgriff", formular)

        daten = self._diagnostik_formular(**{f"k{neu.id}": "Dreipunktgriff"})
        self.client.post(f"/schueler/{self.kind_id}/diagnostik", data=daten)
        detail = self.client.get(f"/schueler/{self.kind_id}").get_data(as_text=True)
        self.assertIn("Stifthaltung", detail)
        self.assertIn("Dreipunktgriff", detail)

        # Eine Option, die es nicht gibt, wird abgewiesen.
        daten[f"k{neu.id}"] = "Erfunden"
        response = self.client.post(f"/schueler/{self.kind_id}/diagnostik", data=daten)
        self.assertIn("ungültige Auswahl", response.get_data(as_text=True))

    def test_schulspiel_total_follows_the_catalog(self):
        ids = [kid for kid, _, _ in self._kriterien("schulspiel")]
        # Nur noch drei Kriterien in der Wertung: alle anderen abschalten.
        for kid in ids[3:]:
            self.client.post(f"/admin/kriterien/eintrag/{kid}/aktiv")
        daten = {f"k{kid}": "3" for kid in ids[:3]}
        self.client.post(f"/schueler/{self.kind_id}/schulspiel", data=daten)
        with self.app.app_context():
            spiel = SchulspielDiagnostik.query.one()
            self.assertEqual((spiel.gesamtwert, spiel.hoechstwert), (9, 9))
            self.assertEqual(spiel.gesamttendenz, 3)

    def test_old_schulspiel_rows_keep_their_scale(self):
        with self.app.app_context():
            spiel = SchulspielDiagnostik(schueler_id=self.kind_id, gesamtwert=40)
            self.assertEqual(spiel.hoechstwert, 75)
            self.assertEqual(spiel.gesamttendenz, 2)

    def test_schularzt_hints_come_from_the_catalog(self):
        kriterien = {altfeld: kid for kid, altfeld, _ in self._kriterien("schularzt")}
        daten = {f"k{kriterien['sehfaehigkeit']}": "unauffällig",
                 f"k{kriterien['haendigkeit']}": "links",
                 f"k{kriterien['erstsprache']}": "Deutsch",
                 f"k{kriterien['ergebnis']}": "keine Bedenken",
                 f"k{kriterien['foerder_konzentration']}": "1",
                 f"k{kriterien['foerder_sprache']}": ["Grammatik"],
                 "gesamteinschaetzung": "2"}
        response = self.client.post(f"/schueler/{self.kind_id}/schularzt", data=daten)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(service.foerderhinweise(self.kind_id), ["Konzentration", "Grammatik"])
        detail = self.client.get(f"/schueler/{self.kind_id}").get_data(as_text=True)
        self.assertIn("Grammatik", detail)
        self.assertIn("links", detail)

    def test_printouts_and_course_view_render_with_criteria(self):
        self.client.post(f"/schueler/{self.kind_id}/diagnostik", data=self._diagnostik_formular())
        for pfad in ("/export/pdf/klassenmappe", "/export/pdf/cards"):
            response = self.client.get(pfad)
            self.assertEqual(response.status_code, 200, pfad)
            self.assertTrue(response.data.startswith(b"%PDF"), pfad)
        self.assertEqual(self.client.get("/foerderkurse/einzel?idx=0").status_code, 200)

    def test_deleting_a_child_removes_its_values(self):
        self.client.post(f"/schueler/{self.kind_id}/diagnostik", data=self._diagnostik_formular())
        with self.app.app_context():
            from sl_office.services.student_deletion import delete_student
            delete_student(db.session.get(Schueler, self.kind_id), self.app.config["UPLOAD_FOLDER"])
            self.assertEqual(KriteriumWert.query.count(), 0)


class EditorTests(_Fixture, unittest.TestCase):

    def test_the_catalog_page_lists_each_form(self):
        for bogen, titel in (("diagnostik", "Wortschatz"), ("schulspiel", "Ausdauer"),
                             ("schularzt", "Händigkeit")):
            page = self.client.get(f"/admin/kriterien/{bogen}").get_data(as_text=True)
            self.assertIn(titel, page)
        self.assertEqual(self.client.get("/admin/kriterien/gibtsnicht").status_code, 404)

    def test_used_criteria_cannot_be_deleted_or_change_type(self):
        self.client.post(f"/schueler/{self.kind_id}/diagnostik", data=self._diagnostik_formular())
        kid = self._kriterien("diagnostik")[0][0]
        self.client.post(f"/admin/kriterien/eintrag/{kid}/loeschen")
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Kriterium, kid))
        response = self.client.post(f"/admin/kriterien/eintrag/{kid}", data={
            "bezeichnung": "Wortschatz neu", "typ": "text"})
        self.assertIn("nicht mehr ändern", response.get_data(as_text=True))
        self.client.post(f"/admin/kriterien/eintrag/{kid}", data={
            "bezeichnung": "Wortschatz (aktiv)", "typ": "skala", "in_wertung": "1"})
        with self.app.app_context():
            self.assertEqual(db.session.get(Kriterium, kid).bezeichnung, "Wortschatz (aktiv)")

    def test_unused_criteria_can_be_deleted(self):
        kid = self._kriterien("schularzt")[0][0]
        self.client.post(f"/admin/kriterien/eintrag/{kid}/loeschen")
        with self.app.app_context():
            self.assertIsNone(db.session.get(Kriterium, kid))

    def test_switched_off_criteria_leave_the_form_but_keep_their_values(self):
        self.client.post(f"/schueler/{self.kind_id}/diagnostik", data=self._diagnostik_formular())
        kid = self._kriterien("diagnostik")[0][0]
        self.client.post(f"/admin/kriterien/eintrag/{kid}/aktiv")
        formular = self.client.get(f"/schueler/{self.kind_id}/diagnostik").get_data(as_text=True)
        self.assertNotIn(f'name="k{kid}"', formular)
        # Speichern ohne das abgeschaltete Kriterium lässt seinen Wert stehen.
        self.client.post(f"/schueler/{self.kind_id}/diagnostik", data=self._diagnostik_formular("1"))
        with self.app.app_context():
            self.assertEqual(service.werte(self.kind_id, "diagnostik")[kid], 2)
        detail = self.client.get(f"/schueler/{self.kind_id}").get_data(as_text=True)
        self.assertIn("(abgeschaltet)", detail)

    def test_moving_changes_the_order(self):
        erstes, zweites = [kid for kid, _, _ in self._kriterien("diagnostik")[:2]]
        self.client.post(f"/admin/kriterien/eintrag/{zweites}/verschieben", data={"richtung": "hoch"})
        self.assertEqual([kid for kid, _, _ in self._kriterien("diagnostik")[:2]], [zweites, erstes])

    def test_options_are_required_for_a_choice(self):
        response = self.client.post("/admin/kriterien/schularzt/neu", data={
            "bezeichnung": "Brille", "typ": "auswahl", "optionen": ""})
        self.assertIn("mindestens eine Option", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
