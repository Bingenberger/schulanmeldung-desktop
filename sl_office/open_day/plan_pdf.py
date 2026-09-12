"""Der persönliche Ablaufplan als PDF -- zum Mitnehmen an den Tag selbst.

Gesetzt wird er mit demselben Briefkopf wie die Elternschreiben, damit die
Familien ein Papier in der Hand haben, das erkennbar von der Schule stammt.
Der Zeitplan steht als Tabelle, denn hier zählt die Uhrzeit neben dem Ort --
im laufenden Satz ginge das unter.
"""

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

from models import Schueler, db
from sl_office.open_day.service import ablaufplan, stationsplan, zeitspanne
from sl_office.parent_portal.letterhead import INK, MARGIN_X, MUTED, RULE, TEXT_W, Flow
from sl_office.parent_portal.letters import german_date

#: Spaltenbreite der Uhrzeit; der Rest gehört der Station samt Platz.
ZEIT_SPALTE = 3.6 * cm
ZEILEN_HOEHE = 0.95 * cm


def _tabellenzeile(flow, zeit, titel, ort):
    """Eine Zeile des Zeitplans: Uhrzeit links, Station und Ort rechts."""
    hoehe = ZEILEN_HOEHE if not ort else ZEILEN_HOEHE + 0.42 * cm
    flow.need(hoehe)
    flow.y -= hoehe
    schrift = flow.font
    flow.pdf.setFillColor(INK)
    flow.pdf.setFont(schrift["bold"], 11)
    flow.pdf.drawString(MARGIN_X, flow.y + hoehe - 0.62 * cm, zeit)
    flow.pdf.setFont(schrift["body"], 11)
    flow.pdf.drawString(MARGIN_X + ZEIT_SPALTE, flow.y + hoehe - 0.62 * cm, titel)
    if ort:
        flow.pdf.setFillColor(MUTED)
        flow.pdf.setFont(schrift["body"], 9.5)
        flow.pdf.drawString(MARGIN_X + ZEIT_SPALTE, flow.y + hoehe - 1.04 * cm, ort)
        flow.pdf.setFillColor(INK)
    flow.pdf.setStrokeColor(RULE)
    flow.pdf.setLineWidth(0.5)
    flow.pdf.line(MARGIN_X, flow.y, MARGIN_X + TEXT_W, flow.y)


def build_plan(event, eintrag, school, plan=None):
    """Den Ablaufplan einer Familie setzen; liefert die PDF-Bytes."""
    plan = plan if plan is not None else stationsplan(event)
    student = db.session.get(Schueler, eintrag.schueler_id)
    puffer = BytesIO()
    pdf = canvas.Canvas(puffer, pagesize=A4)
    pdf.setTitle(f"{event.titel} – Ablaufplan")
    pdf.setAuthor(school.get("name", ""))

    flow = Flow(pdf, school)
    flow.address_block([line for line in (
        eintrag.name,
        student.strasse if student else "",
        f"{student.plz or ''} {student.ort or ''}".strip() if student else "",
    ) if line])
    town = school.get("town")
    datum = german_date(event.datum)
    flow.date_line(f"{town}, {datum}" if town else datum)
    flow.title_bar(event.titel)

    kind = student.vorname if student else "Ihr Kind"
    flow.paragraph(f"Guten Tag {eintrag.name},", gap_after=0.25 * cm)
    # Satzzeichen gehören mit in die Fettauszeichnung: der Textfluss trennt
    # Wörter grundsätzlich durch ein Leerzeichen, ein nachgestellter Punkt
    # stünde sonst abgerückt.
    begruessung = f"schön, dass Sie mit {kind} am **{datum}** zu uns kommen."
    if event.ort:
        begruessung += f" Treffpunkt ist **{event.ort}.**"
    flow.paragraph(begruessung)

    geplant = ablaufplan(eintrag, plan)
    if geplant:
        flow.heading(f"Ihr Ablauf – Gruppe {eintrag.gruppe}")
        flow.space(0.2 * cm)
        for station, label, platz in geplant:
            _tabellenzeile(flow, zeitspanne(station), label,
                           platz.beschriftung if platz else (station.ort or ""))
        flow.space(0.5 * cm)
        flow.paragraph("Bitte finden Sie sich einige Minuten vor dem ersten Punkt ein. "
                       "Wenn Sie sich verlaufen, fragen Sie gerne jemanden vom Team – "
                       "wir bringen Sie hin.")
    else:
        flow.paragraph("Sie haben keinen der angebotenen Programmpunkte ausgewählt. "
                       "Schauen Sie sich gerne in Ruhe bei uns um – wir freuen uns "
                       "auf Ihren Besuch.")

    if event.hinweise:
        flow.heading("Hinweise")
        for zeile in event.hinweise.splitlines():
            if zeile.strip():
                flow.paragraph(zeile.strip())

    flow.space(0.3 * cm)
    flow.signature("Wir freuen uns auf Sie!", "signature",
                   school.get("head") or "Schulleitung")
    flow.finish()
    pdf.save()
    return puffer.getvalue()


def dateiname(event):
    return f"Ablaufplan {event.titel}.pdf".replace("/", "-")
