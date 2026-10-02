"""Briefkopf, Satzspiegel und Textfluss der Elternschreiben.

Nachbau der Schulvorlage ``Einladung_Schulanmeldung.odt``: Wortmarke in der
Hausschrift, Motto in Schreibschrift, rechtsbündiger Kontaktblock, blaue
Trennlinie, grüne Titelleiste und der blaue Fußsteg mit der Web-Adresse.
Kopf und Fuß erscheinen -- wie in der Vorlage -- nur auf der ersten Seite.

ReportLab kennt keinen Textfluss. ``Flow`` bringt darum das Nötige mit:
Zeilenumbruch im Blocksatz mit ``**fett**`` ausgezeichneten Stellen,
Aufzählungen, Überschriften mit blauer Grundlinie und Seitenwechsel, sobald
der Satzspiegel voll ist.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger(__name__)

#: Logo, Unterschrift und Hausschriften liegen austauschbar neben dem Code.
ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "briefkopf"

# --- Farben der Vorlage ----------------------------------------------------

RULE = HexColor("#84acce")      # Trennlinien im Kopf, über dem Fußsteg
HEADING_RULE = HexColor("#4472c4")  # Grundlinie der Zwischenüberschriften
TITLE_BG = HexColor("#c7e1ba")  # grüne Titelleiste
ACCENT = HexColor("#729fcf")    # Fußsteg und Rahmen der Zugangskästen
INK = HexColor("#1a1a1a")
MUTED = HexColor("#5a5a5a")

# --- Satzspiegel (Maße von oben, wie in der Vorlage) -----------------------

PAGE_W, PAGE_H = A4
MARGIN_X = 2.0 * cm
TEXT_W = PAGE_W - 2 * MARGIN_X

LOGO_TOP, LOGO_W, LOGO_H = 0.70 * cm, 3.50 * cm, 2.45 * cm
NAME_BASE = 2.30 * cm
MOTTO_BASE = 3.30 * cm
#: Der Kontaktblock rückt unter das Logo, sein rechter Rand bleibt links davon.
CONTACT_RIGHT_INSET = 1.6 * cm
CONTACT_BASE, CONTACT_STEP = 2.75 * cm, 0.40 * cm
HEAD_RULE = 4.25 * cm
#: DIN-5008-Anschriftfeld: sichtbar im Fenster der Kuverts der Schule.
ADDRESS_BASE, ADDRESS_STEP = 5.30 * cm, 0.45 * cm
BODY_TOP_NEXT = 2.60 * cm
#: Untere Satzkante -- auf Seite 1 hält der Fußsteg mehr Platz frei.
BODY_BOTTOM_FIRST, BODY_BOTTOM_NEXT = 2.60 * cm, 2.00 * cm

BODY_SIZE = 11.0
BODY_LEADING = 13.6
PARAGRAPH_GAP = 0.25 * cm

# --- Schriften -------------------------------------------------------------


def _font_roots():
    roots = [ASSET_DIR, Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    try:
        roots += [Path.home() / ".fonts", Path.home() / ".local/share/fonts"]
    except RuntimeError:  # kein Home-Verzeichnis, etwa im Dienstkonto
        pass
    return roots


#: Rolle -> Dateikandidaten, beste zuerst. Calibri Light ist die Hausschrift
#: der Vorlage; Carlito ist metrisch kompatibel und frei verfügbar.
_CANDIDATES = {
    "body": ("calibril.ttf", "Carlito-Regular.ttf", "LiberationSans-Regular.ttf"),
    "bold": ("calibrib.ttf", "Carlito-Bold.ttf", "LiberationSans-Bold.ttf"),
    "italic": ("calibrili.ttf", "Carlito-Italic.ttf", "LiberationSans-Italic.ttf"),
    "display": ("FrenteH1-Regular.ttf",),
    "script": ("Calligraffiti.ttf",),
}

_FALLBACK = {
    "body": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique",
    "display": "Helvetica-Bold", "script": "Helvetica-Oblique",
}


@lru_cache(maxsize=1)
def _font_files():
    """Dateiname (klein geschrieben) -> Pfad, einmal je Prozess aufgebaut."""
    index = {}
    for root in _font_roots():
        try:
            if not root.is_dir():
                continue
            for path in root.rglob("*.ttf"):
                index.setdefault(path.name.lower(), path)
        except OSError:
            logger.debug("Schriftverzeichnis nicht lesbar: %s", root)
    return index


@lru_cache(maxsize=1)
def fonts():
    """Registrierte Schriftnamen je Rolle.

    Fehlt eine Datei auf dem Server, tritt eine PDF-Standardschrift an ihre
    Stelle -- der Brief wird dann schlichter, aber er wird erzeugt.
    """
    index = _font_files()
    chosen = dict(_FALLBACK)
    for role, names in _CANDIDATES.items():
        for name in names:
            path = index.get(name.lower())
            if path is None:
                continue
            alias = f"SL-{role}"
            try:
                pdfmetrics.registerFont(TTFont(alias, str(path)))
            except Exception:
                logger.warning("Schriftdatei nicht verwendbar: %s", path)
                continue
            chosen[role] = alias
            break
        else:
            logger.info("Keine Schrift für %s gefunden, nutze %s", role, chosen[role])
    pdfmetrics.registerFontFamily(
        chosen["body"], normal=chosen["body"], bold=chosen["bold"], italic=chosen["italic"],
    )
    return chosen


# --- Textauszeichnung ------------------------------------------------------

_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _tokens(text, regular, bold):
    """``**fett**`` markierte Stellen in (Wort, Schrift)-Paare zerlegen."""
    parts, position = [], 0
    for match in _BOLD.finditer(text):
        if match.start() > position:
            parts.append((text[position:match.start()], regular))
        parts.append((match.group(1), bold))
        position = match.end()
    if position < len(text):
        parts.append((text[position:], regular))
    out = []
    for chunk, font in parts:
        out += [(word, font) for word in chunk.split() if word]
    return out


def _fit(word, font, size, max_width):
    """Ein überlanges Wort (etwa eine Adresse) hart auf Zeilenbreite brechen."""
    pieces = []
    while pdfmetrics.stringWidth(word, font, size) > max_width and len(word) > 1:
        cut = len(word)
        while cut > 1 and pdfmetrics.stringWidth(word[:cut], font, size) > max_width:
            cut -= 1
        pieces.append(word[:cut])
        word = word[cut:]
    pieces.append(word)
    return pieces


def wrap(tokens, size, max_width, space):
    """Zeilen als (Wörter, natürliche Breite) umbrechen."""
    lines, line, width = [], [], 0.0
    for word, font in tokens:
        for piece in _fit(word, font, size, max_width):
            piece_width = pdfmetrics.stringWidth(piece, font, size)
            needed = piece_width if not line else piece_width + space
            if line and width + needed > max_width:
                lines.append((line, width))
                line, width, needed = [], 0.0, piece_width
            line.append((piece, font, piece_width))
            width += needed
    if line:
        lines.append((line, width))
    return lines or [([], 0.0)]


class Flow:
    """Ein Brief: Briefkopf, laufender Satz und automatischer Seitenwechsel."""

    def __init__(self, pdf, school):
        self.pdf = pdf
        self.school = school
        self.font = fonts()
        self.page = 0
        self.y = 0.0
        self._begin_page(first=True)

    # -- Seiten ------------------------------------------------------------

    def _begin_page(self, first):
        self.page += 1
        self.first_page = first
        if first:
            self._letterhead()
            self._footer()
            self.y = PAGE_H - ADDRESS_BASE
        else:
            self._page_number()
            self.y = PAGE_H - BODY_TOP_NEXT

    @property
    def _bottom(self):
        return BODY_BOTTOM_FIRST if self.first_page else BODY_BOTTOM_NEXT

    def _break(self):
        self.pdf.showPage()
        self._begin_page(first=False)

    def need(self, height):
        """Seitenwechsel, wenn ``height`` nicht mehr auf die Seite passt."""
        if self.y - height < self._bottom:
            self._break()
            return True
        return False

    def finish(self):
        self.pdf.showPage()

    # -- Briefkopf ---------------------------------------------------------

    def _image(self, key, x, y, width, height):
        path = self.school.get(key)
        if not path:
            return False
        try:
            self.pdf.drawImage(str(path), x, y, width=width, height=height,
                               mask="auto", preserveAspectRatio=True, anchor="c")
        except Exception:
            logger.warning("Briefkopfbild %s nicht lesbar: %s", key, path)
            return False
        return True

    def _letterhead(self):
        pdf = self.pdf
        self._image("logo", MARGIN_X + TEXT_W - LOGO_W, PAGE_H - LOGO_TOP - LOGO_H,
                    LOGO_W, LOGO_H)

        name = (self.school.get("name") or "").strip()
        if name:
            size = 24
            while size > 12 and pdfmetrics.stringWidth(name, self.font["display"], size) > TEXT_W:
                size -= 0.5
            pdf.setFillColor(INK)
            pdf.setFont(self.font["display"], size)
            pdf.drawCentredString(PAGE_W / 2, PAGE_H - NAME_BASE, name)

        motto = (self.school.get("motto") or "").strip()
        if motto:
            pdf.setFont(self.font["script"], 13)
            pdf.drawCentredString(PAGE_W / 2 - 0.6 * cm, PAGE_H - MOTTO_BASE, motto)

        pdf.setFont(self.font["body"], 9)
        pdf.setFillColor(INK)
        right = MARGIN_X + TEXT_W - CONTACT_RIGHT_INSET
        for index, line in enumerate(self.school.get("contact") or []):
            pdf.drawRightString(right, PAGE_H - CONTACT_BASE - index * CONTACT_STEP, line)

        pdf.setStrokeColor(RULE)
        pdf.setLineWidth(1.56)
        pdf.line(MARGIN_X, PAGE_H - HEAD_RULE, MARGIN_X + TEXT_W, PAGE_H - HEAD_RULE)

    def _footer(self):
        """Blaue Trennlinie und der schräge Fußsteg mit der Web-Adresse."""
        pdf = self.pdf
        pdf.setStrokeColor(RULE)
        pdf.setLineWidth(1.81)
        pdf.line(MARGIN_X, 1.07 * cm, MARGIN_X + TEXT_W, 1.07 * cm)

        path = pdf.beginPath()
        path.moveTo(15.58 * cm, 1.71 * cm)
        path.lineTo(21.20 * cm, 1.61 * cm)
        path.lineTo(21.20 * cm, 0.30 * cm)
        path.lineTo(14.90 * cm, 0.37 * cm)
        path.close()
        pdf.setFillColor(ACCENT)
        pdf.setStrokeColor(ACCENT)
        pdf.setLineWidth(0.1)
        pdf.drawPath(path, stroke=1, fill=1)

        web = (self.school.get("web") or "").strip()
        if web:
            pdf.setFillColor(white)
            pdf.setFont(self.font["body"], 10.5)
            pdf.drawCentredString(18.0 * cm, 0.95 * cm, web)
        pdf.setFillColor(INK)

    def _page_number(self):
        pdf = self.pdf
        pdf.setFont(self.font["body"], 8)
        pdf.setFillColor(MUTED)
        pdf.drawRightString(MARGIN_X + TEXT_W, 1.4 * cm, f"Seite {self.page}")
        pdf.setFillColor(INK)

    # -- Bausteine des Briefkopfs ------------------------------------------

    def address_block(self, lines):
        """Anschrift im Sichtfenster; feste Position nach DIN 5008."""
        self.pdf.setFont(self.font["body"], 10.5)
        self.pdf.setFillColor(INK)
        for index, line in enumerate(lines):
            self.pdf.drawString(MARGIN_X, PAGE_H - ADDRESS_BASE - index * ADDRESS_STEP, line)
        self.y = PAGE_H - ADDRESS_BASE - max(len(lines), 4) * ADDRESS_STEP

    def date_line(self, text):
        self.y -= 1.0 * cm
        self.pdf.setFont(self.font["body"], 10.5)
        self.pdf.setFillColor(INK)
        self.pdf.drawRightString(MARGIN_X + TEXT_W, self.y, text)
        self.y -= 0.5 * cm

    def title_bar(self, text):
        """Grüne Titelleiste in der Hausschrift, wie in der Vorlage."""
        text = text.upper()
        size = 22
        while size > 11 and pdfmetrics.stringWidth(text, self.font["display"], size) > TEXT_W - 1.0 * cm:
            size -= 0.5
        height = size * 1.75
        self.need(height + 0.6 * cm)
        self.y -= height
        self.pdf.setFillColor(TITLE_BG)
        self.pdf.rect(MARGIN_X, self.y, TEXT_W, height, stroke=0, fill=1)
        self.pdf.setFillColor(INK)
        self.pdf.setFont(self.font["display"], size)
        self.pdf.drawCentredString(PAGE_W / 2, self.y + height * 0.34, text)
        self.y -= 0.7 * cm

    # -- Laufender Satz ----------------------------------------------------

    def _draw_line(self, words, x, y, size, space, gap):
        for word, font, width in words:
            self.pdf.setFont(font, size)
            self.pdf.drawString(x, y, word)
            x += width + space + gap

    def paragraph(self, text, size=BODY_SIZE, leading=BODY_LEADING, align="justify",
                  indent=0.0, width=None, gap_after=PARAGRAPH_GAP, color=INK, font=None):
        """Absatz im Blocksatz; ``**fett**`` hebt einzelne Stellen hervor."""
        regular = font or self.font["body"]
        bold = self.font["bold"] if font is None else font
        space = pdfmetrics.stringWidth(" ", regular, size)
        max_width = (width if width is not None else TEXT_W) - indent
        lines = wrap(_tokens(text, regular, bold), size, max_width, space)
        self.pdf.setFillColor(color)
        index = 0
        while index < len(lines):
            room = int((self.y - self._bottom) // leading)
            rest = len(lines) - index
            # Weder eine einzelne Anfangs- noch eine einzelne Restzeile stehen lassen.
            if rest - room == 1:
                room -= 1
            if room < min(2, rest):
                self._break()
                self.pdf.setFillColor(color)
                continue
            for words, natural in lines[index:index + min(room, rest)]:
                self.y -= leading
                index += 1
                gap = 0.0
                if align == "justify" and index < len(lines) and len(words) > 1:
                    gap = (max_width - natural) / (len(words) - 1)
                x = MARGIN_X + indent
                if align == "center":
                    x += (max_width - natural) / 2
                elif align == "right":
                    x += max_width - natural
                self._draw_line(words, x, self.y, size, space, gap)
        self.pdf.setFillColor(INK)
        self.y -= gap_after

    def heading(self, text):
        """Zwischenüberschrift mit blauer Grundlinie (Heading 1 der Vorlage)."""
        size = 15
        self.need(size + 1.4 * cm)  # Überschrift nie allein am Seitenfuß
        self.y -= 0.45 * cm + size
        self.pdf.setFont(self.font["body"], size)
        self.pdf.setFillColor(INK)
        self.pdf.drawString(MARGIN_X, self.y, text)
        self.pdf.setStrokeColor(HEADING_RULE)
        self.pdf.setLineWidth(1.5)
        self.pdf.line(MARGIN_X, self.y - 0.16 * cm, MARGIN_X + TEXT_W, self.y - 0.16 * cm)
        self.y -= 0.35 * cm

    def bullets(self, items, size=BODY_SIZE, leading=BODY_LEADING):
        """Aufzählung mit zwei Ebenen; ``items`` sind (Ebene, Text)-Paare."""
        for level, text in items:
            indent = (0.6 if level == 1 else 1.3) * cm
            marker = "•" if level == 1 else "o"
            self.need(leading)
            self.pdf.setFont(self.font["body"], size)
            self.pdf.setFillColor(INK)
            self.pdf.drawString(MARGIN_X + indent - 0.35 * cm, self.y - leading, marker)
            self.paragraph(text, size=size, leading=leading, align="left",
                           indent=indent, gap_after=0.05 * cm)
        self.y -= PARAGRAPH_GAP

    def space(self, height):
        self.y -= height

    def reserve(self, height):
        """Platz für einen Kasten sichern und dessen untere Kante liefern."""
        self.need(height)
        self.y -= height
        return self.y

    def signature(self, closing, image_key, name):
        block = 2.6 * cm
        self.need(block)
        self.paragraph(closing, gap_after=0.1 * cm)
        drawn = self._image(image_key, MARGIN_X, self.y - 1.55 * cm, 5.12 * cm, 1.60 * cm)
        self.y -= 1.75 * cm if drawn else 0.6 * cm
        self.paragraph(name, gap_after=0.0)


def branding(config):
    """Briefkopfdaten aus der Anwendungskonfiguration.

    Nicht gesetzte Felder fallen weg, statt Platzhalter zu drucken; fehlen
    Straße und Ort, tritt die einzeilige ``SCHOOL_ADDRESS`` an ihre Stelle.
    """
    contact = [config.get(key) for key in
               ("SCHOOL_STREET", "SCHOOL_CITY_LINE", "SCHOOL_PHONE", "SCHOOL_EMAIL")]
    contact = [line.strip() for line in contact if line and line.strip()]
    if not contact and config.get("SCHOOL_ADDRESS"):
        contact = [config["SCHOOL_ADDRESS"].strip()]
    return {
        "name": (config.get("SCHOOL_NAME") or "").strip(),
        "motto": (config.get("SCHOOL_MOTTO") or "").strip(),
        "contact": contact,
        "address": (config.get("SCHOOL_ADDRESS") or "").strip(),
        "web": (config.get("SCHOOL_WEB") or "").strip(),
        "phone": (config.get("SCHOOL_PHONE") or "").strip(),
        "town": (config.get("SCHOOL_TOWN") or "").strip(),
        "district": (config.get("SCHOOL_DISTRICT") or "").strip(),
        "health_office": (config.get("SCHOOL_HEALTH_OFFICE") or "").strip(),
        "contact_mail": (config.get("SCHOOL_CONTACT_MAIL") or "").strip(),
        "head": (config.get("SCHOOL_HEAD") or "").strip(),
        "logo": config.get("SCHOOL_LOGO") or "",
        "signature": config.get("SCHOOL_SIGNATURE") or "",
    }
