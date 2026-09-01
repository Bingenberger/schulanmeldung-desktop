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


class ReconcileTests(unittest.TestCase):
    """Abgleich gegen die Originalliste -- der Weg, die Vertauschung zu heilen."""

    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            db.session.add_all([
                GlobalSettings(einschulungsjahr=2027),
                Einschulungsjahr(jahr=2026, ist_aktuell=False),
                Einschulungsjahr(jahr=2027, ist_aktuell=True),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _anlegen(self, vorname, nachname, geburtsdatum, jahr=2027, **felder):
        kind = Schueler(vorname=vorname, nachname=nachname, geburtsdatum=geburtsdatum,
                        einschulungsjahr=jahr, **felder)
        db.session.add(kind)
        db.session.commit()
        return kind

    def _liste(self, zeilen):
        """Die Datei der Stadt: Nachname, Vorname, echte Datumszelle."""
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Nachname", "Vorname", "Geburtsdatum"])
        for nachname, vorname, geburt in zeilen:
            sheet.append([nachname, vorname, geburt])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def test_vertauschung_wird_erkannt_und_korrigiert(self):
        from sl_office.students.reconcile import geburtsdaten_abgleichen
        with self.app.app_context():
            # Ausgangslage: der 03.12. liegt hinter dem Stichtag, das Kind steht
            # deshalb faelschlich als Kann-Kind im Bestand.
            self._anlegen("Salvatore", "Ritrovato", datetime.date(2021, 12, 3),
                          kann_kind=True)
            ergebnis = geburtsdaten_abgleichen(
                self._liste([("Ritrovato", "Salvatore", datetime.datetime(2021, 3, 12))]),
                jahr=2027)
            self.assertEqual(len(ergebnis.vertauscht), 1)
            self.assertEqual(ergebnis.abweichend, [])
            self.assertEqual(ergebnis.anwenden(), 1)

            kind = Schueler.query.filter_by(nachname="Ritrovato").one()
            self.assertEqual(kind.geburtsdatum, ZWOELFTER_MAERZ)
            self.assertFalse(kind.kann_kind, "12.03.2021 liegt vor dem Stichtag")

    def test_uebrige_felder_bleiben_unberuehrt(self):
        from sl_office.students.reconcile import geburtsdaten_abgleichen
        with self.app.app_context():
            self._anlegen("Leano", "Birlo", datetime.date(2021, 12, 6),
                          strasse="Hauptstr. 1", kita="St. Martin")
            ergebnis = geburtsdaten_abgleichen(
                self._liste([("Birlo", "Leano", datetime.datetime(2021, 6, 12))]), jahr=2027)
            ergebnis.anwenden()

            kind = Schueler.query.filter_by(nachname="Birlo").one()
            self.assertEqual(kind.geburtsdatum, datetime.date(2021, 6, 12))
            self.assertEqual(kind.strasse, "Hauptstr. 1")
            self.assertEqual(kind.kita, "St. Martin")

    def test_nur_der_angegebene_jahrgang_wird_angefasst(self):
        """Kinder stehen in beiden Jahrgängen -- der andere darf sich nicht ändern."""
        from sl_office.students.reconcile import geburtsdaten_abgleichen
        with self.app.app_context():
            self._anlegen("Eric", "Tarau", datetime.date(2020, 10, 1), jahr=2026)
            self._anlegen("Eric", "Tarau", datetime.date(2020, 1, 10), jahr=2027)
            ergebnis = geburtsdaten_abgleichen(
                self._liste([("Tarau", "Eric", datetime.datetime(2020, 10, 1))]), jahr=2027)
            self.assertEqual(len(ergebnis.vertauscht), 1)
            ergebnis.anwenden()

            jahrgaenge = {kind.einschulungsjahr: kind.geburtsdatum
                          for kind in Schueler.query.all()}
            self.assertEqual(jahrgaenge[2026], datetime.date(2020, 10, 1))
            self.assertEqual(jahrgaenge[2027], datetime.date(2020, 10, 1))

    def test_richtige_daten_werden_bestaetigt_und_nicht_angefasst(self):
        from sl_office.students.reconcile import geburtsdaten_abgleichen
        with self.app.app_context():
            self._anlegen("Mina", "Wedekind", datetime.date(2020, 10, 19))
            ergebnis = geburtsdaten_abgleichen(
                self._liste([("Wedekind", "Mina", datetime.datetime(2020, 10, 19))]), jahr=2027)
            self.assertEqual(ergebnis.bestaetigt, 1)
            self.assertEqual(ergebnis.korrekturen, [])

    def test_abweichung_ohne_vertauschung_wird_getrennt_gemeldet(self):
        """Ein anderes Datum ist kein Importfehler -- vielleicht ein Namensvetter."""
        from sl_office.students.reconcile import geburtsdaten_abgleichen
        with self.app.app_context():
            self._anlegen("Jonas", "Klein", datetime.date(2020, 10, 19))
            ergebnis = geburtsdaten_abgleichen(
                self._liste([("Klein", "Jonas", datetime.datetime(2020, 4, 7))]), jahr=2027)
            self.assertEqual(ergebnis.vertauscht, [])
            self.assertEqual(len(ergebnis.abweichend), 1)

    def test_fehlende_zuordnungen_werden_beidseitig_gemeldet(self):
        from sl_office.students.reconcile import geburtsdaten_abgleichen
        with self.app.app_context():
            self._anlegen("Selbst", "Angemeldet", datetime.date(2021, 11, 4))
            ergebnis = geburtsdaten_abgleichen(
                self._liste([("Unbekannt", "Kind", datetime.datetime(2020, 10, 19))]), jahr=2027)
            self.assertEqual(len(ergebnis.ohne_treffer), 1)
            self.assertEqual([kind.nachname for kind in ergebnis.nicht_in_datei],
                             ["Angemeldet"])

    def test_doppelter_name_wird_nicht_geraten(self):
        from sl_office.students.reconcile import geburtsdaten_abgleichen
        with self.app.app_context():
            self._anlegen("Max", "Muster", datetime.date(2021, 12, 3))
            self._anlegen("Max", "Muster", datetime.date(2021, 11, 2))
            ergebnis = geburtsdaten_abgleichen(
                self._liste([("Muster", "Max", datetime.datetime(2021, 3, 12))]), jahr=2027)
            self.assertEqual(len(ergebnis.mehrdeutig), 1)
            self.assertEqual(ergebnis.korrekturen, [])

    def test_schreibweise_und_akzente_stoeren_die_zuordnung_nicht(self):
        from sl_office.students.reconcile import geburtsdaten_abgleichen
        with self.app.app_context():
            self._anlegen("Eric", "Tărău", datetime.date(2020, 1, 10))
            ergebnis = geburtsdaten_abgleichen(
                self._liste([("  TARAU ", "eric", datetime.datetime(2020, 10, 1))]), jahr=2027)
            self.assertEqual(len(ergebnis.vertauscht), 1)


if __name__ == "__main__":
    unittest.main()
