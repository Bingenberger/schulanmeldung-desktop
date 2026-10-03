"""Die Schriften der Schreiben müssen mitgeliefert sein, nicht gefunden werden.

Alle PDFs entstehen auf dem Server. Suchte die Anwendung ihre Fließtextschrift
nur in den Systemverzeichnissen, sähen Briefe und Formulare dort anders aus als
auf dem Arbeitsplatz -- ohne dass es jemandem auffiele, denn ReportLab weicht
stillschweigend auf Helvetica aus.
"""

import os
import unittest
from io import BytesIO
from unittest import mock

os.environ["SL_OFFICE_ENV"] = "testing"

from pypdf import PdfReader  # noqa: E402

from sl_office.briefe import letterhead  # noqa: E402


class MitgelieferteSchriftenTests(unittest.TestCase):
    def setUp(self):
        self._leeren()
        self.addCleanup(self._leeren)

    @staticmethod
    def _leeren():
        letterhead.fonts.cache_clear()
        letterhead._font_files.cache_clear()

    def _nur_projekt(self):
        """Die Schriftsuche auf das Projekt beschränken -- wie ein nackter Server."""
        return mock.patch.object(letterhead, "_font_roots",
                                 return_value=[letterhead.ASSET_DIR])

    def test_ohne_systemschriften_reicht_das_projekt(self):
        with self._nur_projekt():
            self._leeren()
            gewaehlt = letterhead.fonts()
        for rolle, ersatz in letterhead._FALLBACK.items():
            self.assertNotEqual(gewaehlt[rolle], ersatz,
                                f"{rolle} fiele auf {ersatz} zurück")

    def test_die_fliesstextschrift_liegt_im_projekt(self):
        with self._nur_projekt():
            self._leeren()
            dateien = letterhead._font_files()
        for name in ("carlito-regular.ttf", "carlito-bold.ttf", "carlito-italic.ttf"):
            self.assertIn(name, dateien)

    def test_die_lizenz_liegt_bei(self):
        """Carlito steht unter der SIL OFL und darf nur mit ihr weitergegeben werden."""
        lizenz = letterhead.ASSET_DIR / "Carlito-OFL.txt"
        self.assertTrue(lizenz.is_file())
        self.assertIn("SIL OPEN FONT LICENSE", lizenz.read_text(encoding="utf-8").upper())

    def test_ein_brief_entsteht_auch_ohne_systemschriften(self):
        with self._nur_projekt():
            self._leeren()
            schule = {"name": "Musterschule", "town": "Niederkassel", "head": "A. Leitung",
                      "contact_mail": "info@example.de"}
            from sl_office.briefe import letters
            puffer = letters.build_preview(schule, letters.DEFAULT_TEXT,
                                           period="27. bis 30. Oktober")
            daten = puffer.getvalue()
        self.assertTrue(daten.startswith(b"%PDF-"))
        seite = PdfReader(BytesIO(daten)).pages[0].extract_text()
        self.assertIn("Musterschule", seite)


if __name__ == "__main__":
    unittest.main()
