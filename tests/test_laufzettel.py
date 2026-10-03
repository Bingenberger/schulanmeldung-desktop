"""Der Laufzettel der Anmeldung: Reihenfolge, Inhalt, Sammel- und Einzeldruck.

Der heikle Punkt beim Sammeldruck ist, dass sich die Bögen nicht in die Quere
kommen. Die Tests lesen deshalb den Text je Seite zurück.
"""

import datetime
import os
import unittest
from io import BytesIO

os.environ["SL_OFFICE_ENV"] = "testing"

from pypdf import PdfReader  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Einschulungsjahr, GlobalSettings, Schueler, User, db  # noqa: E402
from sl_office.appointments import laufzettel_pdf  # noqa: E402
from sl_office.appointments.models import (  # noqa: E402
    AppointmentBooking, AppointmentEvent, AppointmentSlot,
)

JAHR = 2027


def _seitentexte(payload):
    return [seite.extract_text() or "" for seite in PdfReader(BytesIO(payload)).pages]


class _Fixture:
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            db.session.add_all([GlobalSettings(einschulungsjahr=JAHR),
                                Einschulungsjahr(jahr=JAHR, ist_aktuell=True)])
            event = AppointmentEvent(title="Schulanmeldung", school_year=JAHR,
                                     status="published")
            db.session.add(event)
            db.session.flush()
            self.event_id = event.id
            user = User(username="sekretariat", password_hash=generate_password_hash("x"),
                        role="Sekretariat")
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _kind(self, nachname, vorname="Kind", **felder):
        student = Schueler(vorname=vorname, nachname=nachname, einschulungsjahr=JAHR, **felder)
        db.session.add(student)
        db.session.flush()
        return student

    def _termin(self, student, stunde):
        beginn = datetime.datetime(2026, 10, 14, stunde, 0)
        slot = AppointmentSlot(event_id=self.event_id, starts_at=beginn,
                               ends_at=beginn + datetime.timedelta(minutes=40))
        db.session.add(slot)
        db.session.flush()
        db.session.add(AppointmentBooking(event_id=self.event_id, slot_id=slot.id,
                                          schueler_id=student.id))
        return slot

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True
        return client


class OrderTests(_Fixture, unittest.TestCase):
    def test_sortiert_nach_anmeldetermin(self):
        with self.app.app_context():
            spaet = self._kind("Ammer", "Spaet")       # alphabetisch zuerst
            frueh = self._kind("Zander", "Frueh")
            self._termin(spaet, 11)
            self._termin(frueh, 8)
            db.session.commit()
            event = db.session.get(AppointmentEvent, self.event_id)
            reihenfolge = [student.vorname
                           for student, _ in laufzettel_pdf.fuer_veranstaltung(event)]
        self.assertEqual(reihenfolge, ["Frueh", "Spaet"])

    def test_kinder_ohne_termin_stehen_am_ende(self):
        with self.app.app_context():
            mit = self._kind("Ammer", "MitTermin")
            self._kind("Berg", "OhneTermin")
            self._termin(mit, 9)
            db.session.commit()
            event = db.session.get(AppointmentEvent, self.event_id)
            eintraege = laufzettel_pdf.fuer_veranstaltung(event)
        self.assertEqual([student.vorname for student, _ in eintraege],
                         ["MitTermin", "OhneTermin"])
        self.assertIsNone(eintraege[1][1])


class LaufzettelTests(_Fixture, unittest.TestCase):
    """Der Laufzettel wird selbst gesetzt, im Briefkopf der Schule."""

    def _schule(self):
        from sl_office.briefe.letterhead import branding
        return branding(self.app.config)

    def test_ein_bogen_passt_auf_eine_seite(self):
        """Mit Unterschriftszeile -- die rutschte beim ersten Entwurf auf Seite 2."""
        with self.app.app_context():
            student = self._kind("Asanović-Baumgartner", "Leonardo Maximilian")
            self._termin(student, 9)
            db.session.commit()
            seiten = _seitentexte(laufzettel_pdf.build(
                student, self._schule(), (None, AppointmentSlot.query.one(),
                                          db.session.get(AppointmentEvent, self.event_id))))
        self.assertEqual(len(seiten), 1)
        self.assertIn("Unterschrift Mitarbeiter:in Schule", seiten[0])

    def test_name_und_termin_stehen_darauf(self):
        from sl_office.appointments.service import slot_label
        with self.app.app_context():
            student = self._kind("Yilmaz", "Ela")
            slot = self._termin(student, 9)
            db.session.commit()
            event = db.session.get(AppointmentEvent, self.event_id)
            erwartet = slot_label(slot, event)
            seite = _seitentexte(laufzettel_pdf.build(
                student, self._schule(), (None, slot, event)))[0]
        self.assertIn("Ela Yilmaz", seite)
        self.assertIn(erwartet, seite)

    def test_der_wortlaut_der_vorlage_steht_vollstaendig_darauf(self):
        with self.app.app_context():
            student = self._kind("Ammer", "Lina")
            db.session.commit()
            seite = _seitentexte(laufzettel_pdf.build(student, self._schule()))[0]
        from sl_office.vorlagen import laufzettel_abschnitte
        with self.app.app_context():
            abschnitte = laufzettel_abschnitte(schule=self._schule())
        self.assertTrue(abschnitte)
        for abschnitt, punkte in abschnitte:
            self.assertIn(abschnitt, seite)
            for punkt in punkte:
                self.assertIn(punkt.text, seite, punkt.text)
                for option in getattr(punkt, "optionen", ()):
                    self.assertIn(option, seite, option)

    def test_der_stapel_laesst_jedem_kind_ein_eigenes_blatt(self):
        with self.app.app_context():
            erste = self._kind("Ammer", "Lina")
            zweite = self._kind("Berg", "Tom")
            db.session.commit()
            seiten = _seitentexte(laufzettel_pdf.build_many(
                [(erste, None), (zweite, None)], self._schule()))
        self.assertEqual(len(seiten), 4, "je Kind ein Bogen und eine Leerseite")
        self.assertIn("Lina", seiten[0])
        self.assertEqual(seiten[1].strip(), "")
        self.assertIn("Tom", seiten[2])

    def test_ueber_die_oberflaeche(self):
        with self.app.app_context():
            student = self._kind("Ammer", "Lina")
            self._termin(student, 9)
            db.session.commit()
            student_id = student.id
        client = self._client()
        stapel = client.get(f"/admin/appointments/{self.event_id}/laufzettel.pdf")
        self.assertEqual(stapel.mimetype, "application/pdf")
        self.assertIn("Laufzettel_Anmeldung", stapel.headers["Content-Disposition"])
        einzeln = client.get(f"/admin/appointments/schueler/{student_id}/laufzettel.pdf")
        seite = _seitentexte(einzeln.data)[0]
        self.assertIn("Lina", seite)
        self.assertIn("LAUFZETTEL ANMELDUNG", seite.upper())

    def test_der_protokolldruck_ist_ausgebaut(self):
        with self.app.app_context():
            student = self._kind("Ammer", "Lina")
            db.session.commit()
            student_id = student.id
        client = self._client()
        self.assertEqual(client.get(
            f"/admin/appointments/{self.event_id}/protokolle.pdf").status_code, 404)
        self.assertEqual(client.get(
            f"/admin/appointments/schueler/{student_id}/protokoll.pdf").status_code, 404)


if __name__ == "__main__":
    unittest.main()
