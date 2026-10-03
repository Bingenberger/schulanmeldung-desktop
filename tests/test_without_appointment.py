"""Wer hat noch keinen Anmeldetermin?

Die Liste zeigt die Kinder des geöffneten Jahrgangs ohne bestätigten Termin;
ein stornierter Termin wird vermerkt. Aus der Liste heraus lässt sich direkt
ein Zeitfenster vergeben.
"""

import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Einschulungsjahr, GlobalSettings, Schueler, User, db, utcnow  # noqa: E402
from sl_office.appointments.models import (  # noqa: E402
    AppointmentBooking, AppointmentEvent, AppointmentSlot,
)
from sl_office.appointments.service import students_without_appointment  # noqa: E402


class WithoutAppointmentTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            db.session.add_all([GlobalSettings(einschulungsjahr=2027),
                                Einschulungsjahr(jahr=2027, ist_aktuell=True)])
            start = datetime.datetime(2026, 10, 14, 9, 0)
            event = AppointmentEvent(title="Anmeldung", school_year=2027, status="published")
            db.session.add(event)
            db.session.flush()
            slot = AppointmentSlot(event_id=event.id, starts_at=start, capacity=3,
                                   ends_at=start + datetime.timedelta(minutes=40))
            db.session.add(slot)

            def kind(nachname, jahr=2027, **felder):
                eintrag = Schueler(vorname="Kind", nachname=nachname, einschulungsjahr=jahr,
                                   **felder)
                db.session.add(eintrag)
                db.session.flush()
                return eintrag

            vergeben = kind("Vergeben")
            offen = kind("Offen", erzb_1_name="Anna Offen")
            storniert = kind("Storniert")
            kind("AndererJahrgang", jahr=2026)
            db.session.add_all([
                AppointmentBooking(event_id=event.id, slot_id=slot.id, schueler_id=vergeben.id,
                                   source="staff"),
                AppointmentBooking(event_id=event.id, slot_id=slot.id, schueler_id=storniert.id,
                                   status="cancelled", cancelled_at=utcnow()),
            ])
            user = User(username="sekretariat", password_hash=generate_password_hash("x"),
                        role="Sekretariat")
            db.session.add(user)
            db.session.commit()
            self.event_id, self.slot_id, self.user_id = event.id, slot.id, user.id
            self.offen_id = offen.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True
        return client

    def _pfad(self):
        return f"/admin/appointments/{self.event_id}/ohne-termin"

    def _liste(self):
        return self._client().get(self._pfad()).get_data(as_text=True)

    def test_vergebene_termine_fehlen_in_der_liste(self):
        html = self._liste()
        for name in ("Offen", "Storniert"):
            self.assertIn(name, html)
        self.assertNotIn("Vergeben,", html)
        self.assertIn("Anna Offen", html)

    def test_nur_der_geoeffnete_jahrgang_zaehlt(self):
        self.assertNotIn("AndererJahrgang", self._liste())

    def test_eine_stornierung_wird_vermerkt(self):
        self.assertIn("storniert am", self._liste())
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(User, self.user_id))
            storniert = {zeile["kind"].nachname: zeile["storniert_am"]
                         for zeile in students_without_appointment()}
        self.assertIsNotNone(storniert["Storniert"])
        self.assertIsNone(storniert["Offen"])

    def test_aus_der_liste_heraus_laesst_sich_ein_termin_vergeben(self):
        client = self._client()
        antwort = client.post(f"/admin/appointments/schueler/{self.offen_id}/zuweisen",
                              data={"slot_id": self.slot_id, "next": self._pfad()})
        self.assertEqual(antwort.location, self._pfad())
        with self.app.app_context():
            buchung = AppointmentBooking.query.filter_by(
                schueler_id=self.offen_id, status="confirmed").one()
            self.assertEqual(buchung.source, "staff")
        self.assertNotIn("Offen, Kind", client.get(self._pfad()).get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
