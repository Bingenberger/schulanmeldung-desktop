"""Termine als iCalendar-Datei (RFC 5545).

Zeiten liegen in der Datenbank als naive UTC-Werte. Der Export schreibt sie
darum unverändert als UTC-Zeitstempel (``…Z``) -- damit braucht die Datei keine
VTIMEZONE-Definition, und jeder Kalender rechnet selbst in die Ortszeit um.
"""

import datetime

_CRLF = "\r\n"
#: Weiche Zeilengrenze nach RFC 5545, gemessen in Oktetten.
_LINE_OCTETS = 75


def _escape(text):
    """Sonderzeichen eines Textwerts maskieren."""
    return (str(text or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n"))


def _fold(line):
    """Lange Zeilen umbrechen, ohne ein Mehrbyte-Zeichen zu zerschneiden."""
    raw = line.encode("utf-8")
    if len(raw) <= _LINE_OCTETS:
        return line
    pieces, rest, limit = [], raw, _LINE_OCTETS
    while len(rest) > limit:
        cut = limit
        # Nie mitten in einer UTF-8-Sequenz trennen.
        while cut > 1 and (rest[cut] & 0xC0) == 0x80:
            cut -= 1
        pieces.append(rest[:cut].decode("utf-8"))
        rest = rest[cut:]
        limit = _LINE_OCTETS - 1  # Folgezeilen beginnen mit einem Leerzeichen
    pieces.append(rest.decode("utf-8"))
    return (_CRLF + " ").join(pieces)


def _stamp(value):
    """Naive oder aware Zeit als UTC-Zeitstempel."""
    aware = value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value
    return aware.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")


def _event_lines(entry, now):
    """Ein VEVENT. ``entry`` beschreibt einen gebuchten Termin."""
    lines = [
        "BEGIN:VEVENT",
        f"UID:{entry['uid']}",
        f"DTSTAMP:{now}",
        f"DTSTART:{_stamp(entry['starts_at'])}",
        f"DTEND:{_stamp(entry['ends_at'])}",
        f"SUMMARY:{_escape(entry['summary'])}",
    ]
    if entry.get("location"):
        lines.append(f"LOCATION:{_escape(entry['location'])}")
    if entry.get("description"):
        lines.append(f"DESCRIPTION:{_escape(entry['description'])}")
    lines += ["STATUS:CONFIRMED", "TRANSP:OPAQUE", "END:VEVENT"]
    return lines


def build_calendar(entries, name=""):
    """Eine ICS-Datei aus mehreren Terminen bauen; liefert ``bytes``."""
    now = _stamp(datetime.datetime.now(datetime.UTC))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SL-Office//Schulanmeldung//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    if name:
        # Nicht standardisiert, aber von gängigen Kalendern als Name gelesen.
        lines += [f"X-WR-CALNAME:{_escape(name)}"]
    for entry in entries:
        lines += _event_lines(entry, now)
    lines.append("END:VCALENDAR")
    return (_CRLF.join(_fold(line) for line in lines) + _CRLF).encode("utf-8")


def _uid(booking_id):
    """Stabil je Buchung, damit ein erneuter Import den Termin ersetzt."""
    return f"anmeldetermin-{booking_id}@sl-office"


def staff_entry(booking, slot, event, student, school_name=""):
    """Derselbe Termin aus Sicht der Schule: Nachname zuerst, mit Herkunft."""
    source = "von den Eltern gebucht" if booking.source == "parent" else "von der Schule vergeben"
    return {
        "uid": _uid(booking.id),
        "starts_at": slot.starts_at,
        "ends_at": slot.ends_at,
        "summary": f"Anmeldung: {student.nachname}, {student.vorname}",
        "location": slot.location or school_name,
        "description": f"{event.title} – {source}."
                       + (f" Hinweis: {booking.internal_note}" if booking.internal_note else ""),
    }
