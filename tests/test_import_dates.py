"""Geburtsdaten aus Tabellenzellen -- besonders die Tag/Monat-Vertauschung.

Der Fehler, gegen den diese Tests stehen: ``pd.to_datetime("2021-03-12",
dayfirst=True)`` liefert den 3. Dezember. Aus der Schulpflichtigenliste, die mit
``dtype=str`` gelesen wird, kam jede echte Datumszelle als ISO-Zeichenkette --
und damit war jeder Geburtstag bis zum 12. eines Monats vertauscht.
"""

import datetime
import io
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

import pandas as pd  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Einschulungsjahr, GlobalSettings, Schueler, User, db  # noqa: E402
from sl_office.students.dates import parse_birthdate  # noqa: E402

ZWOELFTER_MAERZ = datetime.date(2021, 3, 12)


class ParseBirthdateTests(unittest.TestCase):
    def test_iso_wird_nicht_vertauscht(self):
        """Der eigentliche Fehler: Jahr-Monat-Tag ist niemals tagzuerst."""
        for text in ("2021-03-12", "2021-03-12 00:00:00", "2021-03-12T00:00:00"):
            with self.subTest(text=text):
                self.assertEqual(parse_birthdate(text), ZWOELFTER_MAERZ)

    def test_deutsche_schreibweise_ist_tagzuerst(self):
        for text in ("12.03.2021", "12.3.2021", "12/03/2021", "12-03-2021", "12.03.21"):
            with self.subTest(text=text):
                self.assertEqual(parse_birthdate(text), ZWOELFTER_MAERZ)

    def test_echte_datumswerte_werden_uebernommen(self):
        for wert in (datetime.date(2021, 3, 12), datetime.datetime(2021, 3, 12, 9, 30),
                     pd.Timestamp("2021-03-12")):
            with self.subTest(wert=wert):
                self.assertEqual(parse_birthdate(wert), ZWOELFTER_MAERZ)

    def test_tage_ab_dem_13_bleiben_eindeutig(self):
        self.assertEqual(parse_birthdate("2021-03-25"), datetime.date(2021, 3, 25))
        self.assertEqual(parse_birthdate("25.03.2021"), datetime.date(2021, 3, 25))

    def test_leere_und_unlesbare_zellen_werden_abgewiesen(self):
        for wert in (None, "", "   ", "nan", "Unbekannt", "31.02.2021", "2021-13-05",
                     float("nan"), pd.NaT):
            with self.subTest(wert=wert):
                with self.assertRaises((ValueError, TypeError)):
                    parse_birthdate(wert)


class ImportDateTests(unittest.TestCase):
    """Beide Importwege gegen eine Arbeitsmappe mit echten Datumszellen."""

    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            db.session.add_all([
                GlobalSettings(einschulungsjahr=2027),
                Einschulungsjahr(jahr=2027, ist_aktuell=True),
                User(username="chef", password_hash=generate_password_hash("pw"),
                     role="Administrator"),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _mappe(self, zeilen):
        """Arbeitsmappe mit echten Datumszellen, wie die Stadt sie liefert."""
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Nachname", "Vorname", "Geburtsdatum"])
        for nachname, geburt in zeilen:
            sheet.append([nachname, "Test", geburt])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    #: Ein Geburtstag am 12.03. (vertauschbar) und einer am 25.03. (eindeutig).
    ZEILEN = [("Vertauschbar", datetime.datetime(2021, 3, 12)),
              ("Eindeutig", datetime.datetime(2021, 3, 25))]

    def test_excel_import_vertauscht_tag_und_monat_nicht(self):
        from sl_office.students.excel import import_students
        with self.app.app_context():
            self.assertEqual(import_students(self._mappe(self.ZEILEN)), (2, 0, 0))
            self.assertEqual(Schueler.query.filter_by(nachname="Vertauschbar").one().geburtsdatum,
                             ZWOELFTER_MAERZ)
            self.assertEqual(Schueler.query.filter_by(nachname="Eindeutig").one().geburtsdatum,
                             datetime.date(2021, 3, 25))

    def test_staedtische_liste_vertauscht_tag_und_monat_nicht(self):
        from sl_office.students.city_import import import_rows, load_staged, stage_upload
        token, _headers, _preview = stage_upload(self._mappe(self.ZEILEN))
        staged = load_staged(token)
        mapping = {"nachname": "Nachname", "vorname": "Vorname",
                   "geburtsdatum": "Geburtsdatum"}
        with self.app.app_context():
            created, updated, invalid = import_rows(staged, mapping)
            self.assertEqual((created, updated, invalid), (2, 0, 0))
            self.assertEqual(Schueler.query.filter_by(nachname="Vertauschbar").one().geburtsdatum,
                             ZWOELFTER_MAERZ)
            self.assertEqual(Schueler.query.filter_by(nachname="Eindeutig").one().geburtsdatum,
                             datetime.date(2021, 3, 25))

    def test_vertauschter_geburtstag_kippt_den_kann_kind_status_nicht_mehr(self):
        """Aus 12.03.2021 (Muss-Kind) wurde der 03.12.2021 -- ein Kann-Kind."""
        from sl_office.students.excel import import_students
        with self.app.app_context():
            import_students(self._mappe([("Grenzfall", datetime.datetime(2021, 3, 12))]))
            kind = Schueler.query.filter_by(nachname="Grenzfall").one()
            self.assertEqual(kind.geburtsdatum, ZWOELFTER_MAERZ)
            self.assertFalse(kind.kann_kind)


if __name__ == "__main__":
    unittest.main()
