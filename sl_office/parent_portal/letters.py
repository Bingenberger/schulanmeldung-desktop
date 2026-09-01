"""Elternanschreiben zur Schulanmeldung.

Inhalt und Gestaltung folgen der Schulvorlage ``Einladung_Schulanmeldung.odt``.
Neu ist der Abschnitt mit den persönlichen Zugängen: statt einer öffentlichen
Buchungsseite bekommt jede erziehungsberechtigte Person einen eigenen
Einmal-Link -- als Adresse und als QR-Code. Wird ein Brief erneut gedruckt,
bleibt ein noch gültiger Zugang bestehen, damit Familien mit dem alten
Schreiben weiterarbeiten können.

Der Brieftext ist redaktionell änderbar (``/admin/elternbrief-text``) und liegt
dann in der Tabelle ``elternbrief``; ohne gespeicherte Fassung gilt
:data:`DEFAULT_TEXT`, der Wortlaut der Schulvorlage. Das Textformat ist bewusst
schlicht -- siehe :func:`render_body`. Der Briefkopf und der Satz stecken in
:mod:`sl_office.parent_portal.letterhead`.
"""

import datetime
import re
from io import BytesIO
from types import SimpleNamespace

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from sqlalchemy import select

from models import db
from sl_office.parent_portal import letterhead
from sl_office.parent_portal.access_service import create_activation_grant
from sl_office.parent_portal.letterhead import Flow
from sl_office.parent_portal.models import ActivationGrant, Elternbrief, ParentAccess, utcnow

PURPOSES = ("first_access", "second_access")
#: Letters are printed well before the registration period, so give them room.
GRANT_LIFETIME_DAYS = 120
#: Schlüssel der Brieftexte in der Tabelle ``elternbrief``.
#: ``einladung``        -- die Eltern wählen ihren Termin selbst,
#: ``einladung_termin`` -- die Schule hat den Termin bereits vergeben.
TEXT_KEY = "einladung"
ASSIGNED_TEXT_KEY = "einladung_termin"

MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember")


def _is_usable(grant, now):
    if grant.used_at or grant.revoked_at:
        return False
    expires = grant.expires_at
    comparable = now.replace(tzinfo=None) if expires.tzinfo is None else now
    return expires > comparable


def issue_letter_tokens(student_id, created_by_user_id=None, reissue=False):
    """Return (fresh tokens, already-covered purposes) for one child.

    A token's plaintext exists only at creation time -- the database keeps just
    its digest -- so a purpose is normally minted only when nothing covers it
    yet. Two cases are deliberately not reissued:

    * a grant that is still unused and unexpired (the earlier letter is valid),
    * a grant that was already redeemed (that guardian has an account; a new
      link would consume the child's second access slot).

    ``reissue`` covers the first case: the still-open grants are revoked and
    replaced, so a fresh letter carries working links again. Every link from an
    earlier letter stops working at that moment. Redeemed grants stay untouched
    -- that access exists and is reached by e-mail, not by a new letter.
    """
    now = utcnow()
    existing = db.session.scalars(select(ActivationGrant).where(
        ActivationGrant.schueler_id == student_id
    )).all()
    covered = {}
    for grant in existing:
        if grant.used_at:
            covered[grant.purpose] = "redeemed"
        elif _is_usable(grant, now):
            if reissue:
                grant.revoked_at = now
                continue
            covered.setdefault(grant.purpose, "issued")
    tokens = {}
    for purpose in PURPOSES:
        if purpose in covered:
            continue
        _, token = create_activation_grant(
            student_id, purpose, created_by_user_id=created_by_user_id,
            lifetime_days=GRANT_LIFETIME_DAYS,
        )
        tokens[purpose] = token
    return tokens, covered


def redeemed_purposes(student_id):
    """Purposes whose access already exists, so the letter section is done."""
    used = db.session.scalars(select(ActivationGrant.purpose).where(
        ActivationGrant.schueler_id == student_id,
        ActivationGrant.used_at.isnot(None),
    )).all()
    return set(used)


def _guardian_names(student):
    first = (student.erzb_1_name or "").strip() or "Erziehungsberechtigte Person 1"
    second = (student.erzb_2_name or "").strip() or "Erziehungsberechtigte Person 2"
    return first, second


def german_date(day):
    return f"{day.day}. {MONTHS[day.month - 1]} {day.year}"


# --- Brieftext -------------------------------------------------------------

#: Marke für den Block mit den beiden Zugangskästen.
ACCESS_MARKER = "[zugaenge]"

#: Platzhalter, die im Brieftext eingesetzt werden dürfen.
FIELDS = {
    "kind": "Vorname des Kindes",
    "schuljahr": "Schuljahr der Einschulung, z. B. 2027/2028",
    "schule": "Name der Schule aus dem Briefkopf",
    "bezirk": "Einzugsbereich der Schule",
    "stadt": "Stadt bzw. Gemeinde der Schule",
    "gesundheitsamt": "zuständiges Gesundheitsamt",
    "zeitraum": "Anmeldezeitraum aus dem Druckformular",
    "frist": "Frist für die Terminbuchung aus dem Druckformular",
    "kontakt": "E-Mail-Adresse für Rückfragen",
    "schulleitung": "Name der Schulleitung aus dem Briefkopf",
}

DEFAULT_TITLE = "Einladung zur Schulanmeldung für das Schuljahr {schuljahr}"
DEFAULT_CLOSING = "Mit freundlichen Grüßen"

#: Wortlaut der Schulvorlage. Eine Zeile ist ein Absatz; siehe render_body.
DEFAULT_BODY = """Sehr geehrte Erziehungsberechtigte,

Ihr Kind {kind} wird zum Schuljahr {schuljahr} schulpflichtig. Als nächstgelegene Schule dürfen wir Sie daher heute zur Anmeldung Ihres Kindes an unserer Schule ganz herzlich einladen.

Als Eltern können Sie Ihr Kind an der Grundschule Ihrer Wahl anmelden. Jedes Kind hat jedoch einen rechtlichen Anspruch auf den Besuch der nächstgelegenen Grundschule. Daher laden wir als Grundschule in {bezirk} zunächst alle hier wohnenden Kinder und ihre Eltern zur Schulanmeldung ein und freuen uns selbstverständlich sehr darüber, wenn Sie als Eltern sich entscheiden, Ihr Kind zum Besuch unserer Schule anzumelden.

Melden Eltern ihr Kind an einer anderen als der nächstgelegenen Schule an, so kann ihr Kind dort nur im Rahmen vorhandener freier Kapazitäten aufgenommen werden. Zu berücksichtigen ist hierbei, dass die Aufnahmeentscheidung erst nach Abschluss aller Überprüfungsverfahren erfolgen kann. Entstehende Fahrtkosten für den Besuch einer anderen als der nächstgelegenen Grundschule werden nicht vom Schulträger übernommen.

# Anmeldung an der nächstgelegenen Schule

Bei der {schule} handelt es sich für Ihr Kind um die nächstgelegene Schule, auf deren Besuch ein Rechtsanspruch besteht. Daher laden wir Sie und Ihr Kind mit diesem Schreiben zur Anmeldung an unserer Schule ein.

In diesem Jahr findet die Schulanmeldung vom {zeitraum} statt.

[termin-absatz]

# Ihre persönlichen Zugänge zum Elternportal

[zugaenge]

Sollten Sie Fragen zur Terminbuchung oder zu Ihrem Zugang haben, können Sie sich gerne per Mail an {kontakt} wenden.

**Bitte bringen Sie folgende Unterlagen mit zum Anmeldegespräch:**

- den Anmeldeschein der Stadt {stadt}
- das ausgefüllte Anmeldeformular unserer Schule, unterschrieben von beiden Erziehungsberechtigten
-- bitte nutzen Sie möglichst die von uns angebotene Option, die Anmeldedaten über das Elternportal auch elektronisch zu übermitteln
- den unterschriebenen Erziehungsvertrag (siehe unten)
- bei alleinigem Sorgerecht eine entsprechende Sorgerechtsbescheinigung
- das Familienstammbuch
- Personalausweis/Pass bei nicht deutscher Staatsangehörigkeit
- den Impfpass zum Nachweis eines ausreichenden Masernschutzes

Sofern Ihnen ärztliche Diagnosen und/oder Therapieberichte vorliegen, die für die schulische Förderung Ihres Kindes relevant sein könnten, dürfen Sie diese gerne mit zum Einschulungsgespräch bringen.

An Ihrem Anmeldetermin wird eine Lehrkraft mit Ihrem Kind ein ca. halbstündiges Anmeldespiel durchführen, während Sie im Sekretariat den formalen Anteil der Anmeldung erledigen. Über das Ergebnis des Anmeldespiels werden Sie sofort im Anschluss in einem Gespräch durch die Lehrkraft informiert.

**Es ist daher wichtig, dass Sie mit Ihrem Kind zum Anmeldegespräch kommen!**

Einen Termin zur schulärztlichen Untersuchung Ihres Kindes erhalten Sie durch ein gesondertes Schreiben des {gesundheitsamt}.

# Anmeldung an einer anderen als der nächstgelegenen Schule

Falls Sie Ihr Kind nicht an unserer Schule, sondern an einer anderen als der nächstgelegenen Schule anmelden wollen, so bitten wir Sie, dies baldmöglichst zu tun und den beigefügten Anmeldeschein dort abzugeben. Nur so können wir die vom Gesetzgeber auferlegte Überwachung der Schulpflicht sicherstellen.

Die gewünschte Schule wird sodann diesen Anmeldeschein mit der Bestätigung der Anmeldung Ihres Kindes an uns zurücksenden und Sie und uns zu einem späteren Zeitpunkt über die erfolgte Aufnahmeentscheidung unterrichten.

# Erziehungsvertrag

Erstmalig erhalten Sie mit den Unterlagen auch unseren Erziehungsvertrag. Er beschreibt die Werte und Schwerpunkte, die uns in der Erziehungsarbeit wichtig sind, und soll deutlich machen, wie wir als Schule gemeinsam mit Ihnen als Eltern zum Wohl Ihres Kindes handeln möchten. Bitte nehmen Sie sich Zeit, den Vertrag in Ruhe zu Hause zu lesen. Wenn Sie ihn bereits unterschrieben zur Anmeldung mitbringen, können wir von Anfang an auf einer gemeinsamen Grundlage starten – damit Ihr Kind sich bei uns gut aufgehoben fühlt und die bestmögliche Unterstützung erhält.

# Tag der offenen Tür

Alle Informationen bzgl. des Tages der offenen Tür erhalten Sie über den diesem Schreiben beiliegenden Informationsflyer.

Wir freuen uns auf das Anmeldegespräch mit Ihnen und Ihrem Kind."""

#: Der Absatz zur Terminfindung -- das Einzige, worin sich die beiden
#: Fassungen unterscheiden. Alles andere steht nur einmal da und bleibt
#: dadurch von selbst gleich.
SELF_BOOKING_PARAGRAPH = """Den genauen Termin des Einschulungsgesprächs können Sie selbst wählen. \
Nutzen Sie dazu bitte Ihren persönlichen Zugang zum Elternportal unserer Schule. Dort buchen Sie \
Ihren Termin, füllen das Anmeldeformular elektronisch aus und übermitteln uns die Daten Ihres \
Kindes vorab – das verkürzt den formalen Teil des Anmeldegesprächs spürbar.

**Bitte wählen Sie Ihren Termin bis spätestens zum {frist} aus. Andernfalls werden wir Ihnen \
einen Termin zuweisen müssen.**"""

ASSIGNED_PARAGRAPH = """Damit Sie nicht selbst suchen müssen, haben wir für Sie und Ihr Kind \
bereits einen Termin für das Einschulungsgespräch vorgesehen:

**{termin}**

Nutzen Sie bitte Ihren persönlichen Zugang zum Elternportal unserer Schule. Dort füllen Sie das \
Anmeldeformular elektronisch aus und übermitteln uns die Daten Ihres Kindes vorab – das verkürzt \
den formalen Teil des Anmeldegesprächs spürbar. Ihren Termin finden Sie dort ebenfalls noch einmal.

Sollte Ihnen dieser Termin nicht möglich sein, melden Sie sich bitte unter {kontakt}, damit wir \
gemeinsam eine andere Zeit finden."""

#: Marke, an der die jeweilige Fassung ihren Terminabsatz einsetzt.
SLOT_MARKER = "[termin-absatz]"

ASSIGNED_TITLE = "Einladung zur Schulanmeldung für das Schuljahr {schuljahr}"
ASSIGNED_BODY = DEFAULT_BODY.replace(SLOT_MARKER, ASSIGNED_PARAGRAPH)
DEFAULT_BODY = DEFAULT_BODY.replace(SLOT_MARKER, SELF_BOOKING_PARAGRAPH)

DEFAULT_TEXT = {"titel": DEFAULT_TITLE, "text": DEFAULT_BODY, "gruss": DEFAULT_CLOSING}
ASSIGNED_TEXT = {"titel": ASSIGNED_TITLE, "text": ASSIGNED_BODY, "gruss": DEFAULT_CLOSING}

#: ``termin`` gibt es nur in der zugewiesenen Fassung, ``frist`` nur in der
#: selbstgewählten -- eine Frist zum Buchen hat sonst keinen Sinn.
ASSIGNED_FIELDS = {name: label for name, label in FIELDS.items() if name != "frist"}
ASSIGNED_FIELDS["termin"] = "zugewiesener Termin des Kindes, aus der Terminverwaltung"

VARIANTS = {
    TEXT_KEY: {
        "label": "Eltern wählen den Termin selbst",
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
    """Gespeicherte Fassung des Brieftextes, sonst die Schulvorlage."""
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
    """Zurück auf die Schulvorlage: die gespeicherte Fassung entfällt."""
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
    elif ACCESS_MARKER not in text:
        problems.append(f"Die Marke {ACCESS_MARKER} fehlt – ohne sie enthält der Brief keine "
                        "Zugangsdaten und die Eltern können keinen Termin buchen.")
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

BOX_HEADINGS = ("Zugang für Erziehungsberechtigte:n 1", "Zugang für Erziehungsberechtigte:n 2")
BOX_HEIGHT = 4.0 * cm
#: Überschrift, beide Kästen und der Hinweis gehören zusammen auf eine Seite.
ACCESS_BLOCK_HEIGHT = 2 * BOX_HEIGHT + 3.0 * cm

ACCESS_NOTE = (
    "Jeder Zugang gilt nur einmal und ist persönlich. Beim ersten Öffnen hinterlegen Sie Ihre "
    "E-Mail-Adresse; danach melden Sie sich jederzeit über diese Adresse an. Getrennt lebende "
    "Erziehungsberechtigte nutzen bitte je einen der beiden Zugänge."
)
REDEEMED_NOTE = (
    "Dieser Zugang ist bereits eingerichtet. Melden Sie sich einfach mit Ihrer hinterlegten "
    "E-Mail-Adresse an — Sie bekommen dann einen Anmeldelink zugeschickt."
)
REISSUED_NOTE = (
    "Für diesen Zugang wurde bereits ein Brief erstellt. Der Link aus dem früheren Schreiben "
    "gilt weiter. Falls er nicht mehr vorliegt, melden Sie sich bitte im Sekretariat — wir "
    "stellen einen neuen aus."
)


def _draw_qr(pdf, url, x, y, size):
    widget = qr.QrCodeWidget(url)
    bounds = widget.getBounds()
    drawing = Drawing(size, size, transform=[
        size / (bounds[2] - bounds[0]), 0, 0, size / (bounds[3] - bounds[1]),
        -bounds[0] * size / (bounds[2] - bounds[0]), -bounds[1] * size / (bounds[3] - bounds[1]),
    ])
    drawing.add(widget)
    drawing.drawOn(pdf, x, y)


def _wrapped(pdf, text, font, size, width):
    """Zeilen für die kleinen Textblöcke in den Zugangskästen."""
    space = pdf.stringWidth(" ", font, size)
    tokens = [(word, font) for word in text.split()] or [("", font)]
    return [" ".join(word for word, _, _ in line)
            for line, _ in letterhead.wrap(tokens, size, width, space)]


def _access_box(flow, heading, name, url, note):
    """Ein Zugangskasten in der Anmutung der Titelleiste: grüner Kopf, blauer Rahmen."""
    pdf, font = flow.pdf, flow.font
    x, width = letterhead.MARGIN_X, letterhead.TEXT_W
    y = flow.reserve(BOX_HEIGHT)
    strip = 0.62 * cm

    pdf.setFillColor(white)
    pdf.setStrokeColor(letterhead.RULE)
    pdf.setLineWidth(0.8)
    pdf.roundRect(x, y, width, BOX_HEIGHT, 5, stroke=1, fill=1)
    pdf.setFillColor(letterhead.TITLE_BG)
    pdf.rect(x + 0.05 * cm, y + BOX_HEIGHT - strip, width - 0.1 * cm, strip - 0.05 * cm,
             stroke=0, fill=1)

    pdf.setFillColor(letterhead.INK)
    pdf.setFont(font["bold"], 9)
    pdf.drawString(x + 0.45 * cm, y + BOX_HEIGHT - strip + 0.18 * cm, heading)
    pdf.setFont(font["bold"], 11.5)
    pdf.drawString(x + 0.45 * cm, y + BOX_HEIGHT - strip - 0.62 * cm, name[:44])

    qr_size = 2.5 * cm
    text_width = width - 1.0 * cm
    if url:
        _draw_qr(pdf, url, x + width - qr_size - 0.4 * cm, y + 0.35 * cm, qr_size)
        text_width = width - qr_size - 1.3 * cm

    line_y = y + BOX_HEIGHT - strip - 1.25 * cm
    if note:
        pdf.setFont(font["italic"], 8.5)
        pdf.setFillColor(letterhead.MUTED)
        for line in _wrapped(pdf, note, font["italic"], 8.5, text_width):
            pdf.drawString(x + 0.45 * cm, line_y, line)
            line_y -= 10.5
        pdf.setFillColor(letterhead.INK)
        return

    pdf.setFont(font["body"], 8.5)
    pdf.setFillColor(letterhead.MUTED)
    pdf.drawString(x + 0.45 * cm, line_y, "Adresse im Browser eingeben oder QR-Code scannen:")
    pdf.setFillColor(letterhead.INK)
    line_y -= 13
    for line in _wrapped(pdf, url, "Courier", 7.5, text_width):
        pdf.setFont("Courier", 7.5)
        pdf.drawString(x + 0.45 * cm, line_y, line)
        line_y -= 9.5


def _draw_access_block(flow, student, tokens, reusable, link_builder):
    for index, purpose in enumerate(PURPOSES):
        name = _guardian_names(student)[index]
        if purpose in tokens:
            _access_box(flow, BOX_HEADINGS[index], name, link_builder(tokens[purpose]), None)
        elif reusable.get(purpose) == "redeemed":
            _access_box(flow, BOX_HEADINGS[index], name, "", REDEEMED_NOTE)
        else:
            _access_box(flow, BOX_HEADINGS[index], name, "", REISSUED_NOTE)
        flow.space(0.3 * cm)
    flow.paragraph(ACCESS_NOTE, size=8.5, leading=11, color=letterhead.MUTED)


def _next_meaningful(lines, start):
    for line in lines[start:]:
        if line.strip():
            return line.strip()
    return ""


def render_body(flow, markup, fields, access_block):
    """Den Brieftext setzen.

    Das Format ist absichtlich klein gehalten, damit es sich in drei Zeilen
    erklären lässt:

    * jede Zeile ist ein Absatz, Leerzeilen dienen nur der Übersicht,
    * ``# Text`` ist eine Zwischenüberschrift, ``- Text`` ein Aufzählungspunkt
      und ``-- Text`` ein Unterpunkt,
    * ``**Text**`` setzt fett, ``{platzhalter}`` fügt Daten ein und
      ``[zugaenge]`` steht für die beiden Zugangskästen.

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
        if line == ACCESS_MARKER:
            access_block()
        elif line.startswith("#"):
            heading = fill(line.lstrip("#").strip(), fields)
            if not heading:
                continue
            # Überschrift und Zugangsblock dürfen nicht getrennt werden.
            if _next_meaningful(lines, index) == ACCESS_MARKER:
                flow.need(ACCESS_BLOCK_HEIGHT)
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


def _draw_letter(pdf, student, school, letter, text, access_block):
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
        "schulleitung": school.get("head", ""),
        "termin": letter.get("termin", ""),
    }

    flow.address_block(_address_lines(student))
    town = school.get("town")
    flow.date_line(f"{town}, {letter['datum']}" if town else letter["datum"])
    flow.title_bar(fill(text["titel"], fields) or "")

    render_body(flow, text["text"], fields, lambda: access_block(flow, student))

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

    Der Import steht hier und nicht oben: die Terminverwaltung greift ihrerseits
    auf das Elternportal zu, ein Import auf Modulebene liefe im Kreis.
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


def build_letters(students, school, link_builder, created_by_user_id=None, deadline=None,
                  period=None, school_year=None, letter_date=None, reissue=False, texts=None):
    """Render one letter per child and return (pdf_bytes, issued_token_count).

    ``school_year`` is the school year the children start in ("2026/2027");
    ``period`` the registration week ("27. bis 30. Oktober") and ``deadline``
    the date by which parents should have booked their slot. All three are
    optional -- without them the wording simply leaves the dates out.

    Je Kind wird die passende Fassung gesetzt: Kinder mit bereits vergebenem
    Termin bekommen ihn im Brief genannt, alle anderen die Aufforderung, selbst
    einen zu wählen. Ein Stapeldruck kann darum beides enthalten.

    ``reissue`` replaces still-open access links instead of referring to the
    earlier letter; see :func:`issue_letter_tokens`. ``texts`` overrides the
    stored wording je Fassung und ist das, was die Vorschau des Editors mitgibt.
    """
    letter = _letter_fields(letter_date, school_year, period, deadline)
    texts = texts or {}
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle("Einladung zur Schulanmeldung")
    pdf.setAuthor(school.get("name", ""))
    issued = 0
    for student in students:
        tokens, reusable = issue_letter_tokens(student.id, created_by_user_id, reissue=reissue)
        issued += len(tokens)
        termin = appointment_label(student.id)
        key = ASSIGNED_TEXT_KEY if termin else TEXT_KEY
        text = texts.get(key) or stored_text(key)
        _draw_letter(pdf, student, school, dict(letter, termin=termin), text,
                     lambda flow, child, granted=tokens, covered=reusable:
                     _draw_access_block(flow, child, granted, covered, link_builder))
    pdf.save()
    buffer.seek(0)
    return buffer, issued


#: Beispielkind der Vorschau -- frei erfunden, gespeichert wird dabei nichts.
SAMPLE_STUDENT = SimpleNamespace(
    id=0, vorname="Mia", nachname="Musterkind", strasse="Musterweg 7", plz="53859",
    ort="Niederkassel", erzb_1_name="Anna Musterkind", erzb_2_name="Ben Musterkind",
)
SAMPLE_URL = "https://beispiel.example/eltern/aktivieren/NUR-ZUR-ANSICHT"


#: Termin des Beispielkindes in der Vorschau der zugewiesenen Fassung.
SAMPLE_APPOINTMENT = "Mi 14.10.2026, 09:20–10:00 Uhr, Raum 1"


def build_preview(school, text, deadline=None, period=None, school_year=None, letter_date=None,
                  appointment=SAMPLE_APPOINTMENT):
    """Den Brief mit einem Beispielkind setzen, ohne Zugänge auszustellen."""
    letter = dict(_letter_fields(letter_date, school_year, period, deadline),
                  termin=appointment)
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle("Vorschau Elternbrief")
    _draw_letter(pdf, SAMPLE_STUDENT, school, letter, text,
                 lambda flow, child: _draw_access_block(
                     flow, child, dict.fromkeys(PURPOSES, "beispiel"), {},
                     lambda token: SAMPLE_URL))
    pdf.save()
    buffer.seek(0)
    return buffer


def students_without_access(students):
    """Children for whom no parent access has been activated yet."""
    taken = set(db.session.scalars(select(ParentAccess.schueler_id).where(
        ParentAccess.status.in_(("pending", "active", "locked"))
    )).all())
    return [student for student in students if student.id not in taken]
