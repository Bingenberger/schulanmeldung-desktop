"""Wer hat noch keinen Anmeldetermin -- weder selbst gebucht noch vergeben?

Die Liste unterscheidet, warum: Eltern mit Zugang können an die Buchung
erinnert werden, Eltern ohne Zugang nicht -- und ohne Brief wissen sie noch
gar nichts vom Verfahren.
"""

import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Einschulungsjahr, GlobalSettings, Schueler, User, db  # noqa: E402
from sl_office.appointments.service import students_without_appointment  # noqa: E402
from sl_office.parent_portal.access_service import create_activation_grant  # noqa: E402
from sl_office.parent_portal.models import (  # noqa: E402
    AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentAccess, utcnow,
)


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

            def kind(nachname, jahr=2027):
                eintrag = Schueler(vorname="Kind", nachname=nachname, einschulungsjahr=jahr)
                db.session.add(eintrag)
                db.session.flush()
                return eintrag

            selbst = kind("Selbstgebucht")
            vergeben = kind("Vergeben")
            erinnern = kind("Erinnern")
            ohne_zugang = kind("OhneZugang")
            ohne_brief = kind("OhneBrief")
            storniert = kind("Storniert")
            kind("AndererJahrgang", jahr=2026)
            db.session.flush()

            for eintrag in (selbst, erinnern, storniert):
                db.session.add(ParentAccess(schueler_id=eintrag.id, status="active",
                                            display_name=f"Eltern {eintrag.nachname}",
                                            email_normalized=f"{eintrag.nachname.lower()}@example.de"))
            create_activation_grant(ohne_zugang.id, "first_access")
            db.session.add_all([
                AppointmentBooking(event_id=event.id, slot_id=slot.id, schueler_id=selbst.id),
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
            self.erinnern_id = erinnern.id

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

    def _liste(self, filter_=None):
        pfad = f"/admin/appointments/{self.event_id}/ohne-termin"
        return self._client().get(pfad + (f"?filter={filter_}" if filter_ else "")).get_data(
            as_text=True)

    def test_gebuchte_und_vergebene_termine_fehlen_in_der_liste(self):
        html = self._liste()
        for name in ("Erinnern", "OhneZugang", "OhneBrief", "Storniert"):
            self.assertIn(name, html)
        self.assertNotIn("Selbstgebucht", html)
        self.assertNotIn("Vergeben,", html)

    def test_nur_der_geoeffnete_jahrgang_zaehlt(self):
        self.assertNotIn("AndererJahrgang", self._liste())

    def test_der_grund_wird_richtig_bestimmt(self):
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(User, self.user_id))
            gruende = {zeile["kind"].nachname: zeile["grund"]
                       for zeile in students_without_appointment()}
        self.assertEqual(gruende["Erinnern"], "zugang")
        self.assertEqual(gruende["Storniert"], "zugang")
        self.assertEqual(gruende["OhneZugang"], "brief")
        self.assertEqual(gruende["OhneBrief"], "kein_brief")

    def test_eine_stornierung_wird_vermerkt(self):
        self.assertIn("storniert am", self._liste("zugang"))

    def test_der_filter_zeigt_nur_die_gewaehlte_gruppe(self):
        html = self._liste("brief")
        self.assertIn("OhneZugang", html)
        self.assertNotIn("Erinnern", html)

    def test_die_adressen_fuer_eine_erinnerung_stehen_bereit(self):
        html = self._liste("zugang")
        self.assertIn("erinnern@example.de; storniert@example.de", html)

    def test_aus_der_liste_heraus_laesst_sich_ein_termin_vergeben(self):
        client = self._client()
        pfad = f"/admin/appointments/{self.event_id}/ohne-termin?filter=zugang"
        antwort = client.post(f"/admin/appointments/schueler/{self.erinnern_id}/zuweisen",
                              data={"slot_id": self.slot_id, "next": pfad})
        self.assertEqual(antwort.location, pfad)
        with self.app.app_context():
            buchung = AppointmentBooking.query.filter_by(
                schueler_id=self.erinnern_id, status="confirmed").one()
            self.assertEqual(buchung.source, "staff")
        self.assertNotIn("Erinnern, Kind", client.get(pfad).get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
