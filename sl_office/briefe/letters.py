"""Elternanschreiben zur Schulanmeldung.

Jedes Kind bekommt einen Brief im Briefkopf der Schule. Hat die Schule den
Termin schon vergeben, nennt der Brief ihn; sonst bittet er, einen Termin zu
vereinbaren. Welche Fassung ein Kind bekommt, entscheidet sich beim Druck.

Der Brieftext ist redaktionell änderbar (``/admin/elternbrief-text``) und liegt
dann in der Tabelle ``elternbrief``; ohne gespeicherte Fassung gilt die
Vorbelegung (:data:`DEFAULT_TEXT`, :data:`ASSIGNED_TEXT`). Das Textformat ist
bewusst schlicht -- siehe :func:`render_body`. Der Briefkopf und der Satz
stecken in :mod:`sl_office.briefe.letterhead`.
"""

import datetime
import re
from io import BytesIO
from types import SimpleNamespace

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from sqlalchemy import select

from models import db, utcnow
from sl_office.briefe.letterhead import Flow
from sl_office.briefe.models import Elternbrief

#: Schlüssel der Brieftexte in der Tabelle ``elternbrief``.
#: ``einladung``        -- die Eltern vereinbaren ihren Termin,
#: ``einladung_termin`` -- die Schule hat den Termin bereits vergeben.
TEXT_KEY = "einladung"
ASSIGNED_TEXT_KEY = "einladung_termin"

MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember")


def _guardian_names(student):
    first = (student.erzb_1_name or "").strip() or "Erziehungsberechtigte Person 1"
    second = (student.erzb_2_name or "").strip() or "Erziehungsberechtigte Person 2"
    return first, second


def german_date(day):
    return f"{day.day}. {MONTHS[day.month - 1]} {day.year}"


# --- Brieftext -------------------------------------------------------------

#: Platzhalter, die im Brieftext eingesetzt werden dürfen.
FIELDS = {
    "kind": "Vorname des Kindes",
    "schuljahr": "Schuljahr der Einschulung, z. B. 2027/2028",
    "schule": "Name der Schule aus dem Briefkopf",
    "bezirk": "Einzugsbereich der Schule",
    "stadt": "Stadt bzw. Gemeinde der Schule",
    "gesundheitsamt": "zuständiges Gesundheitsamt",
    "zeitraum": "Anmeldezeitraum aus dem Druckformular",
    "frist": "Frist für die Terminvereinbarung aus dem Druckformular",
    "kontakt": "E-Mail-Adresse für Rückfragen",
    "telefon": "Telefonnummer der Schule aus dem Briefkopf",
    "schulleitung": "Name der Schulleitung aus dem Briefkopf",
}

DEFAULT_TITLE = "Einladung zur Schulanmeldung für das Schuljahr {schuljahr}"
ASSIGNED_TITLE = DEFAULT_TITLE
DEFAULT_CLOSING = "Mit freundlichen Grüßen"

#: Marken aus der Zeit mit Elternportal; steht eine davon noch in einem
#: gespeicherten Brieftext, wird die Zeile still übergangen.
LEGACY_MARKERS = {"[zugaenge]"}

#: Marke, an der die jeweilige Fassung ihren Terminabsatz einsetzt.
SLOT_MARKER = "[termin-absatz]"

#: Vorbelegter Wortlaut; jede Schule passt ihn im Editor an. Eine Zeile ist
#: ein Absatz, siehe :func:`render_body`.
DEFAULT_BODY = """Sehr geehrte Erziehungsberechtigte,

Ihr Kind {kind} wird zum Schuljahr {schuljahr} schulpflichtig. Als nächstgelegene Schule laden wir Sie und Ihr Kind daher heute herzlich zur Anmeldung an unserer Schule ein.

Als Eltern können Sie Ihr Kind an der Grundschule Ihrer Wahl anmelden. Jedes Kind hat jedoch einen rechtlichen Anspruch auf den Besuch der nächstgelegenen Grundschule. Melden Eltern ihr Kind an einer anderen als der nächstgelegenen Schule an, so kann es dort nur im Rahmen freier Kapazitäten aufgenommen werden.

# Anmeldung an der nächstgelegenen Schule

Bei der {schule} handelt es sich für Ihr Kind um die nächstgelegene Schule, auf deren Besuch ein Rechtsanspruch besteht.

In diesem Jahr findet die Schulanmeldung vom {zeitraum} statt.

[termin-absatz]

**Bitte bringen Sie folgende Unterlagen mit zum Anmeldegespräch:**

- den Anmeldeschein der Stadt {stadt}
- das ausgefüllte Anmeldeformular unserer Schule, unterschrieben von beiden Erziehungsberechtigten
- bei alleinigem Sorgerecht eine entsprechende Sorgerechtsbescheinigung
- die Geburtsurkunde oder das Familienstammbuch
- den Impfpass zum Nachweis eines ausreichenden Masernschutzes

**Bitte kommen Sie mit Ihrem Kind zum Anmeldegespräch.**

Einen Termin zur schulärztlichen Untersuchung Ihres Kindes erhalten Sie durch ein gesondertes Schreiben des {gesundheitsamt}.

# Anmeldung an einer anderen Schule

Falls Sie Ihr Kind an einer anderen als der nächstgelegenen Schule anmelden wollen, bitten wir Sie, dies baldmöglichst zu tun und den beigefügten Anmeldeschein dort abzugeben. Nur so können wir die Überwachung der Schulpflicht sicherstellen."""

SELF_PARAGRAPH = """Bitte vereinbaren Sie für das Anmeldegespräch einen Termin mit unserem Sekretariat.

Sie erreichen uns telefonisch unter {telefon}.

Per E-Mail erreichen Sie uns unter {kontakt}.

**Bitte melden Sie sich bis spätestens zum {frist}. Andernfalls werden wir Ihnen einen Termin zuweisen.**"""

ASSIGNED_PARAGRAPH = """Für Sie und Ihr Kind haben wir bereits einen Termin für das Anmeldegespräch vorgesehen:

**{termin}**

Sollte Ihnen dieser Termin nicht möglich sein, melden Sie sich bitte bei uns, damit wir gemeinsam eine andere Zeit finden.

Sie erreichen uns telefonisch unter {telefon}.

Per E-Mail erreichen Sie uns unter {kontakt}."""

DEFAULT_TEXT = {"titel": DEFAULT_TITLE,
                "text": DEFAULT_BODY.replace(SLOT_MARKER, SELF_PARAGRAPH),
                "gruss": DEFAULT_CLOSING}
ASSIGNED_TEXT = {"titel": ASSIGNED_TITLE,
                 "text": DEFAULT_BODY.replace(SLOT_MARKER, ASSIGNED_PARAGRAPH),
                 "gruss": DEFAULT_CLOSING}

#: ``termin`` gibt es nur in der zugewiesenen Fassung, ``frist`` nur in der
#: anderen -- eine Frist zur Terminvereinbarung hat sonst keinen Sinn.
ASSIGNED_FIELDS = {name: label for name, label in FIELDS.items() if name != "frist"}
ASSIGNED_FIELDS["termin"] = "zugewiesener Termin des Kindes, aus der Terminverwaltung"

VARIANTS = {
    TEXT_KEY: {
        "label": "Die Eltern vereinbaren einen Termin",
        "default": DEFAULT_TEXT,
        "fields": FIELDS,
    },
    ASSIGNED_TEXT_KEY: {
        "label": "Die Schule gibt den Termin vor",
        "default": ASSIGNED_TEXT,
        "fields": ASSIGNED_FIELDS,
    },
}


def variant(key):
    """Beschreibung einer Fassung; unbekannte Schlüssel fallen auf die erste zurück."""
    return VARIANTS.get(key) or VARIANTS[TEXT_KEY]


def stored_text(key=TEXT_KEY):
    """Gespeicherte Fassung des Brieftextes, sonst die Vorbelegung."""
    row = db.session.scalar(select(Elternbrief).where(Elternbrief.key == key))
    if row is None:
        return dict(variant(key)["default"])
    return {"titel": row.titel, "text": row.text, "gruss": row.gruss}


def save_text(titel, text, gruss, user_id=None, key=TEXT_KEY):
    """Brieftext ablegen; geprüft wird davor mit :func:`check_text`."""
    row = db.session.scalar(select(Elternbrief).where(Elternbrief.key == key))
    if row is None:
        row = Elternbrief(key=key)
        db.session.add(row)
    row.titel, row.text, row.gruss = titel.strip(), text.strip(), gruss.strip()
    row.updated_by_user_id = user_id
    row.updated_at = utcnow()
    return row


def reset_text(key=TEXT_KEY):
    """Zurück auf die Vorbelegung: die gespeicherte Fassung entfällt."""
    row = db.session.scalar(select(Elternbrief).where(Elternbrief.key == key))
    if row is not None:
        db.session.delete(row)
    return dict(variant(key)["default"])


_PLACEHOLDER = re.compile(r"\{([A-Za-zÄÖÜäöüß_]+)\}")


def check_text(titel, text, gruss, key=TEXT_KEY):
    """Beanstandungen für den Editor; leere Liste heißt: kann gespeichert werden."""
    problems = []
    allowed = variant(key)["fields"]
    unknown = sorted({name for part in (titel, text, gruss)
                      for name in _PLACEHOLDER.findall(part or "") if name not in allowed})
    if unknown:
        problems.append("Unbekannte Platzhalter: " + ", ".join("{%s}" % name for name in unknown))
    if not (text or "").strip():
        problems.append("Der Brieftext ist leer.")
    if not (titel or "").strip():
        problems.append("Die Titelzeile ist leer.")
    if key == ASSIGNED_TEXT_KEY and "{termin}" not in (text or ""):
        problems.append("Der Platzhalter {termin} fehlt – ohne ihn steht der zugewiesene "
                        "Termin nirgends im Brief.")
    return problems


def fill(text, fields):
    """Platzhalter einsetzen; ``None``, wenn ein benutzter Platzhalter leer ist.

    So verschwindet etwa der Satz zur Frist von selbst, wenn beim Druck keine
    Frist angegeben wurde. Unbekannte Platzhalter bleiben stehen -- gemeldet
    werden sie beim Speichern durch :func:`check_text`.
    """
    for name in _PLACEHOLDER.findall(text):
        if name in fields and not (fields[name] or "").strip():
            return None
    return _PLACEHOLDER.sub(lambda match: fields.get(match.group(1), match.group(0)), text)


# --- Zeichnen --------------------------------------------------------------

def render_body(flow, markup, fields):
    """Den Brieftext setzen.

    Das Format ist absichtlich klein gehalten, damit es sich in drei Zeilen
    erklären lässt:

    * jede Zeile ist ein Absatz, Leerzeilen dienen nur der Übersicht,
    * ``# Text`` ist eine Zwischenüberschrift, ``- Text`` ein Aufzählungspunkt
      und ``-- Text`` ein Unterpunkt,
    * ``**Text**`` setzt fett, ``{platzhalter}`` fügt Daten ein.

    Eine Zeile, deren Platzhalter leer bleibt, entfällt vollständig -- so
    verschwindet der Satz zum Anmeldezeitraum, wenn keiner angegeben wurde.
    """
    lines = markup.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        if line in LEGACY_MARKERS:
            continue
        if line.startswith("#"):
            heading = fill(line.lstrip("#").strip(), fields)
            if not heading:
                continue
            flow.heading(heading)
        elif line.startswith("-"):
            items = []
            index -= 1
            while index < len(lines) and lines[index].strip().startswith("-"):
                item = lines[index].strip()
                text = fill(item.lstrip("-").strip(), fields)
                if text:
                    items.append((2 if item.startswith("--") else 1, text))
                index += 1
            if items:
                flow.bullets(items)
        else:
            text = fill(line, fields)
            if text:
                flow.paragraph(text)


def _address_lines(student):
    lines = [name for name in _guardian_names(student)
             if not name.startswith("Erziehungsberechtigte Person")]
    if not lines:
        lines = ["Erziehungsberechtigte"]
    lines.append(f"für {student.vorname} {student.nachname}")
    if student.strasse:
        lines.append(student.strasse)
    if student.plz or student.ort:
        lines.append(f"{student.plz or ''} {student.ort or ''}".strip())
    return lines


def _draw_letter(pdf, student, school, letter, text):
    flow = Flow(pdf, school)
    fields = {
        "kind": student.vorname,
        "schuljahr": letter["schuljahr"],
        "schule": school.get("name") or "unserer Schule",
        "bezirk": school.get("district") or school.get("town") or "unserem Schulbezirk",
        "stadt": school.get("town") or student.ort or "",
        "gesundheitsamt": school.get("health_office") or "Gesundheitsamtes",
        "zeitraum": letter.get("zeitraum", ""),
        "frist": letter.get("frist", ""),
        "kontakt": school.get("contact_mail", ""),
        "telefon": school.get("phone", ""),
        "schulleitung": school.get("head", ""),
        "termin": letter.get("termin", ""),
        # Früher das Datum des Tags der offenen Tür; leer lässt die Zeile entfallen.
        "tdot": "",
    }

    flow.address_block(_address_lines(student))
    town = school.get("town")
    flow.date_line(f"{town}, {letter['datum']}" if town else letter["datum"])
    flow.title_bar(fill(text["titel"], fields) or "")

    render_body(flow, text["text"], fields)

    flow.space(0.4 * cm)
    flow.signature(fill(text["gruss"], fields) or "", "signature",
                   school.get("head") or "Schulleitung")
    flow.finish()


def _letter_fields(letter_date, school_year, period, deadline):
    today = letter_date or datetime.date.today()
    return {
        "datum": german_date(today),
        "schuljahr": school_year or f"{today.year + 1}/{today.year + 2}",
        "zeitraum": (period or "").strip(),
        "frist": (deadline or "").strip(),
    }


def appointment_label(student_id):
    """Der vergebene Termin des Kindes als Satz, oder "" wenn keiner besteht.

    Der Import steht in der Funktion: die Terminverwaltung nutzt ihrerseits
    den Briefkopf, ein Import auf Modulebene liefe im Kreis.
    """
    from sl_office.appointments.service import active_booking_for_student, slot_label

    appointment = active_booking_for_student(student_id)
    if appointment is None:
        return ""
    _, slot, event = appointment
    label = slot_label(slot, event)
    return f"{label}, {slot.location}" if slot.location else label


def letter_variant(student_id):
    """Welche Fassung dieses Kind bekommt: mit oder ohne festen Termin."""
    return ASSIGNED_TEXT_KEY if appointment_label(student_id) else TEXT_KEY


def build_letters(students, school, deadline=None, period=None, school_year=None,
                  letter_date=None, texts=None):
    """Je Kind ein Brief; liefert das PDF als Puffer.

    ``school_year`` is the school year the children start in ("2026/2027");
    ``period`` the registration week ("27. bis 30. Oktober") and ``deadline``
    the date by which parents should have arranged their appointment. All three
    are optional -- without them the wording simply leaves the dates out.

    Je Kind wird die passende Fassung gesetzt: Kinder mit bereits vergebenem
    Termin bekommen ihn im Brief genannt, alle anderen die Bitte, einen zu
    vereinbaren. Ein Stapeldruck kann darum beides enthalten. ``texts``
    überschreibt den gespeicherten Wortlaut je Fassung (Vorschau des Editors).
    """
    letter = _letter_fields(letter_date, school_year, period, deadline)
    texts = texts or {}
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle("Einladung zur Schulanmeldung")
    pdf.setAuthor(school.get("name", ""))
    for student in students:
        termin = appointment_label(student.id)
        key = ASSIGNED_TEXT_KEY if termin else TEXT_KEY
        text = texts.get(key) or stored_text(key)
        _draw_letter(pdf, student, school, dict(letter, termin=termin), text)
    pdf.save()
    buffer.seek(0)
    return buffer


#: Beispielkind der Vorschau -- frei erfunden, gespeichert wird dabei nichts.
SAMPLE_STUDENT = SimpleNamespace(
    id=0, vorname="Mia", nachname="Musterkind", strasse="Musterweg 7", plz="12345",
    ort="Musterstadt", erzb_1_name="Anna Musterkind", erzb_2_name="Ben Musterkind",
)

#: Termin des Beispielkindes in der Vorschau der zugewiesenen Fassung.
SAMPLE_APPOINTMENT = "Mi 14.10.2026, 09:20–10:00 Uhr, Raum 1"


def build_preview(school, text, deadline=None, period=None, school_year=None, letter_date=None,
                  appointment=SAMPLE_APPOINTMENT):
    """Den Brief mit einem Beispielkind setzen."""
    letter = dict(_letter_fields(letter_date, school_year, period, deadline), termin=appointment)
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle("Vorschau Elternbrief")
    _draw_letter(pdf, SAMPLE_STUDENT, school, letter, text)
    pdf.save()
    buffer.seek(0)
    return buffer
