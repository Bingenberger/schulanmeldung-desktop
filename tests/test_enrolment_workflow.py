"""Municipal list import and the parent invitation letters."""

import datetime
import os
import re
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from io import BytesIO  # noqa: E402

import pandas as pd  # noqa: E402
from reportlab.graphics.barcode import qr  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402

from app import create_app  # noqa: E402
from models import Schueler, db  # noqa: E402
from sl_office.parent_portal import letterhead, letters  # noqa: E402
from sl_office.parent_portal.access_service import (  # noqa: E402
    InvalidAccessToken, consume_activation_grant,
)
from sl_office.parent_portal.models import ActivationGrant  # noqa: E402
from sl_office.students import city_import  # noqa: E402

CITY_COLUMNS = {
    "Familienname": "Yilmaz", "Rufname": "Elif", "Geb.-Datum": "14.05.2021",
    "Geschlecht": "weiblich", "Straße / Nr.": "Rheinstraße 12", "PLZ": "53859",
    "Wohnort": "Niederkassel", "Erziehungsberechtigte 1": "Ayşe Yilmaz",
    "Erziehungsberechtigte 2": "Mehmet Yilmaz",
}


def _workbook(rows):
    buffer = BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False)
    return buffer.getvalue()


class CityImportTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _stage_and_import(self, rows, override=None):
        payload = _workbook(rows)
        token, headers, _ = city_import.stage_upload(payload)
        self.addCleanup(city_import.discard_staged, token)
        mapping = city_import.suggest_mapping(headers)
        mapping.update(override or {})
        return city_import.import_rows(city_import.load_staged(token), mapping)

    def test_suggestion_matches_awkward_city_headers(self):
        mapping = city_import.suggest_mapping(list(CITY_COLUMNS))
        self.assertEqual(mapping["nachname"], "Familienname")
        self.assertEqual(mapping["vorname"], "Rufname")
        self.assertEqual(mapping["geburtsdatum"], "Geb.-Datum")
        self.assertEqual(mapping["strasse"], "Straße / Nr.")
        self.assertEqual(mapping["erzb_2_name"], "Erziehungsberechtigte 2")

    def test_import_stores_address_and_guardians(self):
        with self.app.app_context():
            created, updated, invalid = self._stage_and_import([CITY_COLUMNS])
            self.assertEqual((created, updated, invalid), (1, 0, 0))
            child = Schueler.query.one()
            self.assertEqual(child.strasse, "Rheinstraße 12")
            self.assertEqual(child.plz, "53859")
            self.assertEqual(child.erzb_1_name, "Ayşe Yilmaz")
            self.assertEqual(child.erzb_2_name, "Mehmet Yilmaz")
            self.assertEqual(child.geburtsdatum, datetime.date(2021, 5, 14))
            self.assertEqual(child.geschlecht, "w")

    def test_rows_without_a_usable_birthdate_are_skipped(self):
        # pandas turns an empty date into NaT rather than raising.
        broken = dict(CITY_COLUMNS, Familienname="Ohne", Rufname="Datum")
        broken["Geb.-Datum"] = ""
        with self.app.app_context():
            created, _, invalid = self._stage_and_import([CITY_COLUMNS, broken])
            self.assertEqual((created, invalid), (1, 1))
            self.assertEqual(Schueler.query.count(), 1)

    def test_reimport_updates_instead_of_duplicating(self):
        with self.app.app_context():
            self._stage_and_import([CITY_COLUMNS])
            moved = dict(CITY_COLUMNS, **{"Straße / Nr.": "Neuer Weg 3"})
            created, updated, _ = self._stage_and_import([moved])
            self.assertEqual((created, updated), (0, 1))
            self.assertEqual(Schueler.query.count(), 1)
            self.assertEqual(Schueler.query.one().strasse, "Neuer Weg 3")

    def test_missing_required_mapping_is_rejected(self):
        with self.app.app_context():
            payload = _workbook([CITY_COLUMNS])
            token, headers, _ = city_import.stage_upload(payload)
            self.addCleanup(city_import.discard_staged, token)
            staged = city_import.load_staged(token)
            with self.assertRaises(city_import.InvalidWorkbook):
                city_import.import_rows(staged, {"vorname": "Rufname"})

    def test_mapping_must_reference_columns_of_the_upload(self):
        with self.app.app_context():
            payload = _workbook([CITY_COLUMNS])
            token, headers, _ = city_import.stage_upload(payload)
            self.addCleanup(city_import.discard_staged, token)
            staged = city_import.load_staged(token)
            bogus = dict(city_import.suggest_mapping(headers), nachname="Gibt es nicht")
            with self.assertRaises(city_import.InvalidWorkbook):
                city_import.import_rows(staged, bogus)

    def test_expired_staging_is_reported(self):
        with self.assertRaises(city_import.StagingExpired):
            city_import.load_staged("nicht-vorhanden")


class ParentLetterTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            child = Schueler(
                vorname="Elif", nachname="Yilmaz", geburtsdatum=datetime.date(2021, 5, 14),
                strasse="Rheinstraße 12", plz="53859", ort="Niederkassel",
                erzb_1_name="Ayşe Yilmaz", erzb_2_name="Mehmet Yilmaz",
            )
            db.session.add(child)
            db.session.commit()
            self.child_id = child.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _build(self, **options):
        child = db.session.get(Schueler, self.child_id)
        return letters.build_letters(
            [child], {"name": "GGS", "address": "Schulstraße 1"},
            lambda token: f"https://schule.example/eltern/aktivieren/{token}",
            **options,
        )

    def test_letter_issues_one_grant_per_guardian(self):
        with self.app.app_context():
            buffer, issued = self._build()
            db.session.commit()
            self.assertEqual(issued, 2)
            self.assertTrue(buffer.getvalue().startswith(b"%PDF"))
            purposes = {grant.purpose for grant in ActivationGrant.query.all()}
            self.assertEqual(purposes, {"first_access", "second_access"})

    def test_reprint_reuses_the_existing_grants(self):
        with self.app.app_context():
            self._build()
            db.session.commit()
            _, issued = self._build()
            db.session.commit()
            self.assertEqual(issued, 0)
            self.assertEqual(ActivationGrant.query.count(), 2)

    def test_redeemed_access_is_not_reissued(self):
        with self.app.app_context():
            tokens, _ = letters.issue_letter_tokens(self.child_id)
            db.session.commit()
            consume_activation_grant(tokens["first_access"], "mum@example.de", "Ayşe Yilmaz")
            db.session.commit()
            self.assertEqual(letters.redeemed_purposes(self.child_id), {"first_access"})
            fresh, covered = letters.issue_letter_tokens(self.child_id)
            db.session.commit()
            # The unused second grant is reused; the redeemed first one is not reissued.
            self.assertNotIn("first_access", fresh)
            self.assertEqual(covered["first_access"], "redeemed")
            self.assertEqual(covered["second_access"], "issued")

    def test_children_with_an_access_drop_out_of_the_pending_list(self):
        with self.app.app_context():
            child = db.session.get(Schueler, self.child_id)
            self.assertEqual(letters.students_without_access([child]), [child])
            tokens, _ = letters.issue_letter_tokens(self.child_id)
            db.session.commit()
            consume_activation_grant(tokens["first_access"], "mum@example.de", "Ayşe Yilmaz")
            db.session.commit()
            self.assertEqual(letters.students_without_access([child]), [])

    def test_qr_encodes_exactly_the_activation_url(self):
        # No QR decoder is available here, so verify the payload handed to the
        # widget and that different links really produce different geometry.
        first = "https://schule.example/eltern/aktivieren/AAA"
        second = "https://schule.example/eltern/aktivieren/BBB"
        def matrix(url):
            widget = qr.QrCodeWidget(url)
            widget.qr.make()  # modules stay None until the code is generated
            return widget.qr.modules

        self.assertEqual(qr.QrCodeWidget(first).value, first)
        self.assertEqual(matrix(first), matrix(first))
        self.assertNotEqual(matrix(first), matrix(second))


    def test_reissue_replaces_an_open_grant(self):
        # Wurde der erste Brief nie verschickt, muss ein neuer wieder Links tragen.
        with self.app.app_context():
            first, _ = letters.issue_letter_tokens(self.child_id)
            db.session.commit()
            fresh, covered = letters.issue_letter_tokens(self.child_id, reissue=True)
            db.session.commit()
            self.assertEqual(set(fresh), set(letters.PURPOSES))
            self.assertEqual(covered, {})
            self.assertNotEqual(fresh["first_access"], first["first_access"])
            with self.assertRaises(InvalidAccessToken):
                consume_activation_grant(first["first_access"], "mum@example.de", "Ayşe Yilmaz")

    def test_reissue_leaves_a_redeemed_grant_alone(self):
        with self.app.app_context():
            tokens, _ = letters.issue_letter_tokens(self.child_id)
            db.session.commit()
            consume_activation_grant(tokens["first_access"], "mum@example.de", "Ayşe Yilmaz")
            db.session.commit()
            fresh, covered = letters.issue_letter_tokens(self.child_id, reissue=True)
            db.session.commit()
            self.assertNotIn("first_access", fresh)
            self.assertEqual(covered["first_access"], "redeemed")
            self.assertIn("second_access", fresh)

    def test_letter_runs_over_several_pages(self):
        # Der Brief trägt den vollständigen Einladungstext der Schulvorlage.
        with self.app.app_context():
            buffer, _ = self._build()
            db.session.commit()
            pages = len(re.findall(rb"/Type /Page[^s]", buffer.getvalue()))
            self.assertGreater(pages, 1)

    def test_missing_letterhead_fields_do_not_break_the_letter(self):
        # Ohne Logo, Motto und Termine muss der Brief trotzdem entstehen.
        with self.app.app_context():
            buffer, issued = self._build(period=None, deadline=None, school_year=None)
            db.session.commit()
            self.assertEqual(issued, 2)
            self.assertTrue(buffer.getvalue().startswith(b"%PDF"))


class LetterheadTests(unittest.TestCase):
    def test_branding_collects_the_contact_block(self):
        school = letterhead.branding({
            "SCHOOL_NAME": "GGS", "SCHOOL_STREET": "Annostraße 3",
            "SCHOOL_CITY_LINE": "53859 Niederkassel", "SCHOOL_PHONE": "",
            "SCHOOL_EMAIL": "info@example.de",
        })
        self.assertEqual(school["contact"],
                         ["Annostraße 3", "53859 Niederkassel", "info@example.de"])

    def test_branding_falls_back_to_the_single_line_address(self):
        school = letterhead.branding({"SCHOOL_NAME": "GGS", "SCHOOL_ADDRESS": "Schulstraße 1"})
        self.assertEqual(school["contact"], ["Schulstraße 1"])

    def test_bold_markers_pick_the_bold_font(self):
        tokens = letterhead._tokens("ganz **wichtig hier** sonst", "Regular", "Bold")
        self.assertEqual(tokens, [("ganz", "Regular"), ("wichtig", "Bold"),
                                  ("hier", "Bold"), ("sonst", "Regular")])

    def test_wrapping_keeps_every_line_within_the_measure(self):
        words = "Erziehungsberechtigte melden ihr Kind an der Grundschule an".split()
        space = pdfmetrics.stringWidth(" ", "Helvetica", 11)
        lines = letterhead.wrap([(word, "Helvetica") for word in words], 11, 120, space)
        self.assertGreater(len(lines), 1)
        for _, width in lines:
            self.assertLessEqual(width, 120)

    def test_an_overlong_word_is_broken_instead_of_overflowing(self):
        url = "https://schule.example/eltern/aktivieren/" + "A" * 60
        space = pdfmetrics.stringWidth(" ", "Courier", 8)
        lines = letterhead.wrap([(url, "Courier")], 8, 100, space)
        self.assertGreater(len(lines), 1)
        for _, width in lines:
            self.assertLessEqual(width, 100)


class _RecordingFlow:
    """Nimmt statt zu zeichnen auf, was render_body anfordert."""

    def __init__(self):
        self.calls = []

    def heading(self, text):
        self.calls.append(("heading", text))

    def paragraph(self, text, **options):
        self.calls.append(("paragraph", text))

    def bullets(self, items, **options):
        self.calls.append(("bullets", items))

    def need(self, height):
        return False

    def space(self, height):
        pass


class LetterTextTests(unittest.TestCase):
    FIELDS = {"kind": "Mia", "schuljahr": "2027/2028", "frist": "", "stadt": "Niederkassel"}

    def _render(self, markup, fields=None):
        flow = _RecordingFlow()
        letters.render_body(flow, markup, fields or self.FIELDS,
                            lambda: flow.calls.append(("zugaenge", None)))
        return flow.calls

    def test_the_markup_becomes_headings_bullets_and_paragraphs(self):
        calls = self._render(
            "# Unterlagen\n\nBitte mitbringen:\n\n- Impfpass\n-- möglichst aktuell\n")
        self.assertEqual(calls, [
            ("heading", "Unterlagen"),
            ("paragraph", "Bitte mitbringen:"),
            ("bullets", [(1, "Impfpass"), (2, "möglichst aktuell")]),
        ])

    def test_a_line_with_an_empty_placeholder_is_dropped(self):
        # Ohne Frist im Druckformular soll der Satz dazu gar nicht erscheinen.
        calls = self._render("Termin bis {frist}.\nIhr Kind {kind} wird schulpflichtig.")
        self.assertEqual(calls, [("paragraph", "Ihr Kind Mia wird schulpflichtig.")])

    def test_the_access_marker_calls_the_block(self):
        calls = self._render(f"Text davor\n{letters.ACCESS_MARKER}\nText danach")
        self.assertEqual([kind for kind, _ in calls], ["paragraph", "zugaenge", "paragraph"])

    def test_an_unknown_placeholder_survives_rendering(self):
        self.assertEqual(letters.fill("Gruß aus {irgendwo}", self.FIELDS), "Gruß aus {irgendwo}")

    def test_the_check_reports_typos_and_a_missing_marker(self):
        # Die Zugangsmarke ist nur Pflicht, wenn das Elternportal läuft.
        with create_app("testing").app_context():
            problems = letters.check_text("Titel {tippfehler}", "Nur Text", "Grüße")
        self.assertEqual(len(problems), 2)
        self.assertIn("{tippfehler}", problems[0])
        self.assertIn(letters.ACCESS_MARKER, problems[1])

    def test_the_default_wording_passes_the_check(self):
        self.assertEqual(letters.check_text(**letters.DEFAULT_TEXT), [])


class StoredLetterTextTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_without_a_stored_row_the_school_template_applies(self):
        with self.app.app_context():
            self.assertEqual(letters.stored_text(), letters.DEFAULT_TEXT)

    def test_saving_and_resetting_the_wording(self):
        with self.app.app_context():
            letters.save_text("Neuer Titel", letters.DEFAULT_TEXT["text"], "Herzliche Grüße")
            db.session.commit()
            self.assertEqual(letters.stored_text()["titel"], "Neuer Titel")
            letters.reset_text()
            db.session.commit()
            self.assertEqual(letters.stored_text(), letters.DEFAULT_TEXT)

    def test_the_preview_issues_no_access_grants(self):
        with self.app.app_context():
            buffer = letters.build_preview({"name": "GGS"}, letters.DEFAULT_TEXT)
            db.session.commit()
            self.assertTrue(buffer.getvalue().startswith(b"%PDF"))
            self.assertEqual(ActivationGrant.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
