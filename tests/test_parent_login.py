"""Wie Eltern ins Portal zurückfinden -- ohne je ein Passwort zu suchen.

Anlass: Eltern landeten nach dem Abmelden über "Anmelden" oben rechts auf der
Passwortanmeldung der Schulverwaltung, und der Link aus dem Brief ließ sich
nur ein einziges Mal benutzen. Er bleibt ein Einmalschlüssel; ein zweites Mal
geöffnet führt er aber jetzt zum Anmeldelink an die hinterlegte Adresse, statt
das Einrichtungsformular zu zeigen und dann mit "ungültig" abzubrechen.
"""

import datetime
import os
import re
import unittest
from unittest import mock

os.environ["SL_OFFICE_ENV"] = "testing"

from app import create_app  # noqa: E402
from models import Schueler, db  # noqa: E402
from sl_office.parent_portal.access_service import (  # noqa: E402
    create_activation_grant, mask_email,
)
from sl_office.parent_portal.models import ActivationGrant, ParentLoginToken, utcnow  # noqa: E402

MAIL = "sl_office.parent_portal.routes.send_parent_login_link"


class ParentLoginTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.client = self.app.test_client()
        with self.app.app_context():
            kind = Schueler(vorname="Mia", nachname="Musterkind")
            db.session.add(kind)
            db.session.flush()
            _, self.token = create_activation_grant(kind.id, "first_access")
            _, self.token_zwei = create_activation_grant(kind.id, "second_access")
            db.session.commit()
            self.student_id = kind.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _einrichten(self, client=None, token=None, email="anna.beispiel@example.de"):
        client = client or self.client
        return client.post(f"/eltern/aktivieren/{token or self.token}",
                           data={"email": email, "display_name": "Anna Beispiel"})

    @staticmethod
    def _anmeldelink(aufruf):
        """Den Pfad des Links aus einem abgefangenen Mailversand."""
        return re.search(r"(/eltern/anmelden/[^/\s]+)$", aufruf.args[2]).group(1)

    # --- Navigation -------------------------------------------------------

    def test_im_elternbereich_fuehrt_nichts_zur_verwaltungsanmeldung(self):
        for seite in ("/eltern/", f"/eltern/aktivieren/{self.token}"):
            with self.subTest(seite=seite):
                html = self.client.get(seite).get_data(as_text=True)
                self.assertNotIn('href="/login"', html)
                self.assertIn("Elternportal", html)

    def test_angemeldete_eltern_sehen_uebersicht_und_abmelden(self):
        self._einrichten()
        html = self.client.get("/eltern/uebersicht").get_data(as_text=True)
        self.assertNotIn('href="/login"', html)
        self.assertIn('action="/eltern/abmelden"', html)

    def test_nach_dem_abmelden_steht_die_anmeldung_per_mail_bereit(self):
        self._einrichten()
        html = self.client.post("/eltern/abmelden", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Anmeldelink zusenden", html)
        self.assertIn('type="email"', html)
        self.assertNotIn("Passwort", html.split("Sie brauchen kein Passwort")[0])

    def test_die_verwaltungsanmeldung_verweist_eltern_ins_portal(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertIn('href="/eltern/"', html)
        self.assertIn("nur für die Schulverwaltung", html)

    # --- Brieflink ein zweites Mal ----------------------------------------

    def test_der_brieflink_zeigt_nach_der_einrichtung_kein_formular_mehr(self):
        self._einrichten()
        self.client.post("/eltern/abmelden")
        html = self.client.get(f"/eltern/aktivieren/{self.token}").get_data(as_text=True)
        self.assertIn("schon eingerichtet", html)
        self.assertIn("a***@example.de", html)
        self.assertNotIn('name="display_name"', html)

    def test_der_brieflink_schickt_den_anmeldelink_an_die_hinterlegte_adresse(self):
        self._einrichten()
        self.client.post("/eltern/abmelden")
        with mock.patch(MAIL) as versand:
            html = self.client.post(f"/eltern/aktivieren/{self.token}/anmeldelink").get_data(
                as_text=True)
        self.assertIn("Postfach", html)
        self.assertEqual(versand.call_args.args[1], "anna.beispiel@example.de")

        # ... und der Link aus der Mail öffnet das Portal.
        antwort = self.client.get(self._anmeldelink(versand.call_args))
        self.assertEqual(antwort.location, "/eltern/uebersicht")

    def test_wer_schon_angemeldet_ist_kommt_mit_dem_brieflink_direkt_hinein(self):
        self._einrichten()
        antwort = self.client.get(f"/eltern/aktivieren/{self.token}")
        self.assertEqual(antwort.location, "/eltern/uebersicht")

    def test_der_brieflink_allein_oeffnet_nach_der_einrichtung_nichts(self):
        """Wer nur den Brief findet, darf nicht hinein -- nur ins Postfach der Eltern."""
        self._einrichten()
        fremd = self.app.test_client()
        with mock.patch(MAIL):
            fremd.post(f"/eltern/aktivieren/{self.token}/anmeldelink")
        antwort = fremd.get("/eltern/uebersicht")
        self.assertEqual(antwort.status_code, 302)
        # Auch ein zweiter Einrichtungsversuch legt keinen weiteren Zugang an.
        self._einrichten(client=fremd, email="fremd@example.de")
        self.assertEqual(fremd.get("/eltern/uebersicht").status_code, 302)

    def test_ein_widerrufener_brieflink_erklaert_den_weg_zurueck(self):
        with self.app.app_context():
            grant = ActivationGrant.query.filter_by(purpose="first_access").one()
            grant.revoked_at = utcnow()
            db.session.commit()
        antwort = self.client.get(f"/eltern/aktivieren/{self.token}")
        self.assertEqual(antwort.status_code, 410)
        self.assertIn("Mit E-Mail-Adresse anmelden", antwort.get_data(as_text=True))

    def test_ein_unbekannter_link_verraet_nichts(self):
        antwort = self.client.get("/eltern/aktivieren/gibt-es-nicht")
        self.assertEqual(antwort.status_code, 410)

    # --- Anmeldung per E-Mail ---------------------------------------------

    def test_anmeldung_per_mail_funktioniert(self):
        self._einrichten()
        self.client.post("/eltern/abmelden")
        with mock.patch(MAIL) as versand:
            html = self.client.post("/eltern/link-anfordern",
                                    data={"email": " Anna.Beispiel@Example.de "}).get_data(
                as_text=True)
        self.assertIn("Postfach", html)
        antwort = self.client.get(self._anmeldelink(versand.call_args))
        self.assertEqual(antwort.location, "/eltern/uebersicht")

    def test_unbekannte_adressen_bekommen_dieselbe_antwort(self):
        """Sonst ließe sich durchprobieren, welche Adressen hinterlegt sind."""
        self._einrichten()
        self.client.post("/eltern/abmelden")
        with mock.patch(MAIL) as versand:
            bekannt = self.client.post("/eltern/link-anfordern",
                                       data={"email": "anna.beispiel@example.de"})
            unbekannt = self.client.post("/eltern/link-anfordern",
                                         data={"email": "niemand@example.de"})
        self.assertEqual(versand.call_count, 1)
        bereinigt = lambda antwort, adresse: antwort.get_data(as_text=True).replace(adresse, "")
        self.assertEqual(
            re.sub(r'name="csrf_token" value="[^"]+"', "", bereinigt(bekannt, "anna.beispiel@example.de")),
            re.sub(r'name="csrf_token" value="[^"]+"', "", bereinigt(unbekannt, "niemand@example.de")))

    def test_doppelt_gedrueckt_geht_nur_eine_mail_hinaus(self):
        self._einrichten()
        self.client.post("/eltern/abmelden")
        with mock.patch(MAIL) as versand:
            for _ in range(3):
                self.client.post("/eltern/link-anfordern",
                                 data={"email": "anna.beispiel@example.de"})
                self.client.post(f"/eltern/aktivieren/{self.token}/anmeldelink")
        self.assertEqual(versand.call_count, 1)
        with self.app.app_context():
            self.assertEqual(ParentLoginToken.query.count(), 1)

    def test_nach_der_sperrfrist_geht_wieder_ein_link_hinaus(self):
        self._einrichten()
        with mock.patch(MAIL) as versand:
            self.client.post("/eltern/link-anfordern", data={"email": "anna.beispiel@example.de"})
            with self.app.app_context():
                zeile = ParentLoginToken.query.one()
                zeile.created_at = utcnow() - datetime.timedelta(minutes=5)
                db.session.commit()
            self.client.post("/eltern/link-anfordern", data={"email": "anna.beispiel@example.de"})
        self.assertEqual(versand.call_count, 2)

    def test_gleiche_adresse_fuer_zwei_kinder_bekommt_beide_links(self):
        with self.app.app_context():
            geschwister = Schueler(vorname="Ben", nachname="Musterkind")
            db.session.add(geschwister)
            db.session.flush()
            _, token = create_activation_grant(geschwister.id, "first_access")
            db.session.commit()
        self._einrichten()
        self._einrichten(client=self.app.test_client(), token=token)
        with mock.patch(MAIL) as versand:
            self.client.post("/eltern/link-anfordern", data={"email": "anna.beispiel@example.de"})
        kinder = sorted(aufruf.args[3] for aufruf in versand.call_args_list)
        self.assertEqual(kinder, ["Ben Musterkind", "Mia Musterkind"])

    def test_alte_adresse_zum_link_anfordern_fuehrt_zur_anmeldung(self):
        antwort = self.client.get("/eltern/link-anfordern")
        self.assertEqual(antwort.location, "/eltern/")

    def test_ein_verbrauchter_anmeldelink_sagt_was_zu_tun_ist(self):
        self._einrichten()
        self.client.post("/eltern/abmelden")
        with mock.patch(MAIL) as versand:
            self.client.post("/eltern/link-anfordern", data={"email": "anna.beispiel@example.de"})
        pfad = self._anmeldelink(versand.call_args)
        self.client.get(pfad)
        self.client.post("/eltern/abmelden")
        html = self.client.get(pfad, follow_redirects=True).get_data(as_text=True)
        self.assertIn("einfach Ihre E-Mail-Adresse ein", html)

    def test_die_einrichtung_erklaert_wie_man_spaeter_wiederkommt(self):
        html = self.client.get(f"/eltern/aktivieren/{self.token}").get_data(as_text=True)
        self.assertIn("Diesen Link brauchen Sie nur heute", html)


class MaskEmailTests(unittest.TestCase):
    def test_maskierung(self):
        self.assertEqual(mask_email("anna.beispiel@example.de"), "a***@example.de")
        self.assertEqual(mask_email("x@example.de"), "x***@example.de")
        self.assertEqual(mask_email("kaputt"), "***")


if __name__ == "__main__":
    unittest.main()
