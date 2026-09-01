"""Erkennung der Kann-Kinder auf allen Wegen, über die Kinder in die Datenbank kommen."""

import datetime
import io
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

import pandas as pd  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Diagnostik, Einschulungsjahr, GlobalSettings, Schueler, User, db  # noqa: E402
from sl_office.maintenance import (  # noqa: E402
    kann_kind_befunde, kann_kind_kennzeichen_richtigstellen,
)
from sl_office.services.student_classification import (  # noqa: E402
    ist_kann_kind, recalculate_kann_kind, stichtag,
)

#: Einschulungsjahr 2027 -> Stichtag 30.09.2021.
KANN = datetime.date(2021, 11, 15)
MUSS = datetime.date(2021, 3, 15)


class KannKindTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            db.session.add_all([
                GlobalSettings(einschulungsjahr=2027),
                Einschulungsjahr(jahr=2026, ist_aktuell=False, gesperrt=True),
                Einschulungsjahr(jahr=2027, ist_aktuell=True),
                User(username="chef", password_hash=generate_password_hash("pw"),
                     role="Administrator"),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _staff_client(self):
        client = self.app.test_client()
        with client.session_transaction() as flask_session:
            flask_session["_user_id"] = "1"
            flask_session["_fresh"] = True
        return client

    def _kind(self, nachname):
        return Schueler.query.filter_by(nachname=nachname).one()

    # --- Stichtagsregel selbst ---------------------------------------------

    def test_stichtag_folgt_dem_einschulungsjahr(self):
        self.assertEqual(stichtag(2027), datetime.date(2021, 9, 30))
        self.assertFalse(ist_kann_kind(datetime.date(2021, 9, 30), 2027))
        self.assertTrue(ist_kann_kind(datetime.date(2021, 10, 1), 2027))
        self.assertFalse(ist_kann_kind(None, 2027))

    def test_jeder_jahrgang_hat_seinen_eigenen_stichtag(self):
        # Dasselbe Geburtsdatum: im Jahrgang 2026 ein Kann-Kind, ein Jahr
        # später ein regulär schulpflichtiges Kind.
        geburt = datetime.date(2020, 10, 21)
        self.assertTrue(ist_kann_kind(geburt, 2026))
        self.assertFalse(ist_kann_kind(geburt, 2027))

    # --- Weg 1: manuelles Anlegen ------------------------------------------

    def test_manuell_angelegtes_kind_wird_eingeordnet(self):
        client = self._staff_client()
        for nachname, geburt in (("Kannkind", KANN), ("Musskind", MUSS)):
            client.post("/add", data={
                "vorname": "Test", "nachname": nachname,
                "geburtsdatum": geburt.isoformat(), "geschlecht": "m", "kita": "", "betreuung": "",
            }, follow_redirects=True)
        with self.app.app_context():
            kann, muss = self._kind("Kannkind"), self._kind("Musskind")
            self.assertEqual(kann.einschulungsjahr, 2027)
            self.assertTrue(kann.kann_kind)
            self.assertTrue(kann.diagnostik.schulspiel)
            self.assertFalse(muss.kann_kind)

    def test_manuell_angelegtes_kind_folgt_dem_gewaehlten_jahrgang(self):
        """Wer einen anderen Jahrgang geöffnet hat, legt auch dort an -- und der
        Stichtag dieses Jahrgangs entscheidet."""
        with self.app.app_context():
            db.session.get(Einschulungsjahr, 1).gesperrt = False
            db.session.commit()
        client = self._staff_client()
        with client.session_transaction() as flask_session:
            flask_session["active_school_year"] = 2026
        client.post("/add", data={
            "vorname": "Test", "nachname": "Grenzfall",
            "geburtsdatum": "2020-10-21", "geschlecht": "w", "kita": "", "betreuung": "",
        }, follow_redirects=True)
        with self.app.app_context():
            kind = self._kind("Grenzfall")
            self.assertEqual(kind.einschulungsjahr, 2026)
            self.assertTrue(kind.kann_kind)

    def test_korrigiertes_geburtsdatum_hebt_das_kennzeichen_auf(self):
        client = self._staff_client()
        client.post("/add", data={
            "vorname": "Test", "nachname": "Korrektur",
            "geburtsdatum": KANN.isoformat(), "geschlecht": "m", "kita": "", "betreuung": "",
        }, follow_redirects=True)
        with self.app.app_context():
            kind_id = self._kind("Korrektur").id
            self.assertTrue(self._kind("Korrektur").kann_kind)
        client.post(f"/schueler/{kind_id}/edit", data={
            "vorname": "Test", "nachname": "Korrektur",
            "geburtsdatum": MUSS.isoformat(), "geschlecht": "m", "kita": "", "betreuung": "",
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertFalse(self._kind("Korrektur").kann_kind)

    # --- Weg 2: Massenimport ------------------------------------------------

    def _xlsx(self, zeilen):
        buffer = io.BytesIO()
        pd.DataFrame(zeilen).to_excel(buffer, index=False)
        return buffer.getvalue()

    def test_excel_import_ordnet_ein(self):
        from sl_office.students.excel import import_students
        payload = self._xlsx([
            {"Nachname": "ImportKann", "Vorname": "A", "Geburtsdatum": "15.11.2021",
             "Geschlecht": "w", "Kita": "K"},
            {"Nachname": "ImportMuss", "Vorname": "B", "Geburtsdatum": "15.03.2021",
             "Geschlecht": "m", "Kita": "K"},
        ])
        client = self._staff_client()
        with client:
            client.get("/")  # Jahrgang aus der Sitzung wirksam machen
            with self.app.app_context():
                self.assertEqual(import_students(payload), (2, 0, 0))
                self.assertTrue(self._kind("ImportKann").kann_kind)
                self.assertTrue(self._kind("ImportKann").diagnostik.schulspiel)
                self.assertFalse(self._kind("ImportMuss").kann_kind)
                self.assertEqual(self._kind("ImportKann").einschulungsjahr, 2027)

    def test_staedtische_liste_ordnet_ein(self):
        from sl_office.students.city_import import import_rows
        staged = {
            "headers": ["nachname", "vorname", "geburtsdatum"],
            "rows": [
                {"nachname": "StadtKann", "vorname": "C", "geburtsdatum": "15.11.2021"},
                {"nachname": "StadtMuss", "vorname": "D", "geburtsdatum": "15.03.2021"},
            ],
        }
        mapping = {feld: feld for feld in staged["headers"]}
        with self.app.app_context():
            import_rows(staged, mapping)
            self.assertTrue(self._kind("StadtKann").kann_kind)
            self.assertFalse(self._kind("StadtMuss").kann_kind)

    # --- Schulspiel ---------------------------------------------------------

    def test_abwahl_des_schulspiels_bleibt_bestehen(self):
        """Sonst holte jede Stammdatenänderung die Einladung zurück."""
        with self.app.app_context():
            kind = Schueler(vorname="Test", nachname="Abwahl", geburtsdatum=KANN,
                            einschulungsjahr=2027)
            db.session.add(kind)
            recalculate_kann_kind(kind)
            db.session.commit()
            self.assertTrue(kind.diagnostik.schulspiel)

            kind.diagnostik.schulspiel = False   # bewusste Entscheidung der Schule
            db.session.commit()

            recalculate_kann_kind(kind)
            db.session.commit()
            self.assertTrue(kind.kann_kind)
            self.assertFalse(kind.diagnostik.schulspiel)

    # --- Prüflauf über den Bestand -----------------------------------------

    def test_prueflauf_findet_und_korrigiert_alle_jahrgaenge(self):
        with self.app.app_context():
            falsch_2026 = Schueler(vorname="A", nachname="Alt", einschulungsjahr=2026,
                                   geburtsdatum=datetime.date(2020, 10, 21), kann_kind=False)
            falsch_2027 = Schueler(vorname="B", nachname="Neu", einschulungsjahr=2027,
                                   geburtsdatum=MUSS, kann_kind=True)
            ohne_datum = Schueler(vorname="C", nachname="Datumlos", einschulungsjahr=2027)
            unplausibel = Schueler(vorname="D", nachname="Zahlendreher", einschulungsjahr=2027,
                                   geburtsdatum=datetime.date(1975, 1, 17), kann_kind=False)
            db.session.add_all([falsch_2026, falsch_2027, ohne_datum, unplausibel])
            db.session.commit()

            falsch, fehlend, auffaellig = kann_kind_befunde()
            # Der gesperrte Jahrgang 2026 wird mitgeprüft.
            self.assertEqual({k.nachname for k, _ in falsch}, {"Alt", "Neu"})
            self.assertEqual([k.nachname for k in fehlend], ["Datumlos"])
            self.assertEqual([k.nachname for k in auffaellig], ["Zahlendreher"])

            self.assertEqual(kann_kind_kennzeichen_richtigstellen(falsch), 2)
            self.assertTrue(self._kind("Alt").kann_kind)
            self.assertTrue(self._kind("Alt").diagnostik.schulspiel)
            self.assertFalse(self._kind("Neu").kann_kind)

    def test_prueflauf_meldet_sauberen_bestand(self):
        with self.app.app_context():
            db.session.add(Schueler(vorname="A", nachname="Sauber", einschulungsjahr=2027,
                                    geburtsdatum=KANN, kann_kind=True))
            db.session.commit()
            falsch, fehlend, auffaellig = kann_kind_befunde()
            self.assertEqual((falsch, fehlend, auffaellig), ([], [], []))


if __name__ == "__main__":
    unittest.main()
