"""Tag der offenen Tür: Rückmeldung der Eltern, Einteilung, Ablaufplan.

Der Kern ist die Einteilung. Sie muss nicht nur die beiden Gruppen gleich groß
machen, sondern jede Station für sich ausgleichen -- Familien wählen ja
unterschiedlich viel aus.
"""

import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Einschulungsjahr, GlobalSettings, Schueler, User, db  # noqa: E402
from sl_office.parent_portal.models import ParentRegistration  # noqa: E402
from sl_office.open_day import notifications  # noqa: E402
from sl_office.open_day.models import (  # noqa: E402
    GRUPPEN, OpenDayEvent, OpenDayPlatz, OpenDayRegistration, OpenDayStation, OpenDayZuteilung,
)
from sl_office.open_day.service import (  # noqa: E402
    ablaufplan, einteilen, ohne_platz, plaetze_zuteilen, standard_stationen, stationsplan,
    veroeffentlichtes_event, zaehlung, zuteilung_je_station,
)
from sl_office.parent_portal.access_service import create_activation_grant  # noqa: E402

JAHR = 2027


class _OpenDayFixture:
    """Ein veröffentlichter Tag der offenen Tür mit den Standardzeiten."""

    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            db.session.add_all([
                GlobalSettings(einschulungsjahr=JAHR),
                Einschulungsjahr(jahr=JAHR, ist_aktuell=True),
            ])
            event = OpenDayEvent(school_year=JAHR, status="published",
                                 datum=datetime.date.today() + datetime.timedelta(days=30),
                                 ort="Haupteingang")
            standard_stationen(event)
            db.session.add(event)
            db.session.commit()
            self.event_id = event.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _anmelden(self, nachname, wuensche, teilnahme=True, gruppe=None):
        kind = Schueler(vorname="Kind", nachname=nachname, einschulungsjahr=JAHR)
        db.session.add(kind)
        db.session.flush()
        eintrag = OpenDayRegistration(
            event_id=self.event_id, schueler_id=kind.id, name=f"Familie {nachname}",
            email=f"{nachname.lower()}@example.de", teilnahme=teilnahme, gruppe=gruppe,
            wunsch_fuehrung="fuehrung" in wuensche,
            wunsch_unterricht="unterricht" in wuensche,
            wunsch_ogs="ogs" in wuensche)
        db.session.add(eintrag)
        db.session.commit()
        return eintrag

    def _login_staff(self):
        with self.app.app_context():
            user = User(username="leitung", password_hash=generate_password_hash("x"),
                        role="Schulleitung")
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        return client


class GroupingTests(_OpenDayFixture, unittest.TestCase):
    def test_stationen_werden_einzeln_ausgeglichen(self):
        """Zwanzig gleiche Wünsche müssen sich zehn zu zehn teilen."""
        with self.app.app_context():
            for nummer in range(20):
                self._anmelden(f"Alle{nummer:02d}", ("fuehrung", "unterricht", "ogs"))
            event = db.session.get(OpenDayEvent, self.event_id)
            einteilen(event)
            db.session.commit()

            werte = zaehlung(event)
            for art in ("fuehrung", "unterricht", "ogs"):
                self.assertEqual(werte[art][1], 10, art)
                self.assertEqual(werte[art][2], 10, art)

    def test_ungleiche_wuensche_gleichen_sich_je_station_aus(self):
        """Der eigentliche Anspruch: nicht die Gruppen, die Stationen zählen."""
        with self.app.app_context():
            for nummer in range(9):
                self._anmelden(f"NurFuehrung{nummer}", ("fuehrung",))
            for nummer in range(7):
                self._anmelden(f"NurUnterricht{nummer}", ("unterricht",))
            for nummer in range(4):
                self._anmelden(f"Alles{nummer}", ("fuehrung", "unterricht", "ogs"))
            event = db.session.get(OpenDayEvent, self.event_id)
            einteilen(event)
            db.session.commit()

            werte = zaehlung(event)
            for art in ("fuehrung", "unterricht", "ogs"):
                self.assertLessEqual(abs(werte[art][1] - werte[art][2]), 1,
                                     f"{art} ist unausgewogen: {werte[art]}")

    def test_bereits_eingeteilte_familien_bleiben_stehen(self):
        """Ein verschickter Ablaufplan soll nicht ohne Not hinfällig werden."""
        with self.app.app_context():
            fest = self._anmelden("Fest", ("fuehrung",), gruppe=2)
            self._anmelden("Neu", ("fuehrung",))
            event = db.session.get(OpenDayEvent, self.event_id)
            verteilt = einteilen(event)
            db.session.commit()

            self.assertEqual(verteilt, 1)
            self.assertEqual(db.session.get(OpenDayRegistration, fest.id).gruppe, 2)

    def test_neu_verteilen_hebt_die_bisherige_einteilung_auf(self):
        with self.app.app_context():
            for nummer in range(4):
                self._anmelden(f"Kind{nummer}", ("fuehrung",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            einteilen(event, neu_verteilen=True)
            db.session.commit()

            werte = zaehlung(event)
            self.assertEqual(werte["fuehrung"][1], 2)
            self.assertEqual(werte["fuehrung"][2], 2)

    def test_absagen_und_programmlose_zusagen_bekommen_keine_gruppe(self):
        with self.app.app_context():
            absage = self._anmelden("Absage", ("fuehrung",), teilnahme=False)
            ohne = self._anmelden("Ohne", ())
            event = db.session.get(OpenDayEvent, self.event_id)
            einteilen(event)
            db.session.commit()

            self.assertIsNone(db.session.get(OpenDayRegistration, absage.id).gruppe)
            self.assertIsNone(db.session.get(OpenDayRegistration, ohne.id).gruppe)
            self.assertEqual(zaehlung(event)["absagen"], 1)

    def test_die_gruppen_durchlaufen_die_stationen_verschieden(self):
        with self.app.app_context():
            erste = self._anmelden("Eins", ("fuehrung", "unterricht", "ogs"), gruppe=1)
            zweite = self._anmelden("Zwei", ("fuehrung", "unterricht", "ogs"), gruppe=2)
            plan = stationsplan(db.session.get(OpenDayEvent, self.event_id))

            self.assertEqual([station.art for station, _, _ in ablaufplan(erste, plan)],
                             ["fuehrung", "unterricht", "ogs"])
            self.assertEqual([station.art for station, _, _ in ablaufplan(zweite, plan)],
                             ["unterricht", "fuehrung", "ogs"])

    def test_der_ablauf_enthaelt_nur_die_gewaehlten_stationen(self):
        with self.app.app_context():
            eintrag = self._anmelden("Wenig", ("ogs",), gruppe=1)
            plan = ablaufplan(eintrag, stationsplan(db.session.get(OpenDayEvent, self.event_id)))
            self.assertEqual([station.art for station, _, _ in plan], ["ogs"])


class ParentFormTests(_OpenDayFixture, unittest.TestCase):
    """Die Rückmeldung läuft über den vorhandenen Elternzugang."""

    def setUp(self):
        super().setUp()
        with self.app.app_context():
            kind = Schueler(vorname="Portal", nachname="Kind", einschulungsjahr=JAHR)
            db.session.add(kind)
            db.session.flush()
            _, token = create_activation_grant(kind.id, "first_access")
            _, zweiter = create_activation_grant(kind.id, "second_access")
            db.session.commit()
            self.student_id, self.token, self.token_zwei = kind.id, token, zweiter
        self.client = self.app.test_client()

    def _anmelden_als_eltern(self, token=None, client=None, email="mutter@example.de"):
        client = client or self.client
        client.post(f"/eltern/aktivieren/{token or self.token}",
                    data={"email": email, "display_name": "Anna Beispiel"},
                    follow_redirects=True)
        return client

    def test_name_und_mail_sind_aus_dem_zugang_vorbelegt(self):
        self._anmelden_als_eltern()
        seite = self.client.get("/eltern/tag-der-offenen-tuer").get_data(as_text=True)
        self.assertIn("Anna Beispiel", seite)
        self.assertIn("mutter@example.de", seite)

    def test_rueckmeldung_wird_gespeichert(self):
        self._anmelden_als_eltern()
        self.client.post("/eltern/tag-der-offenen-tuer", data={
            "name": "Anna Beispiel", "email": "mutter@example.de", "teilnahme": "ja",
            "fuehrung": "ja", "ogs": "ja", "bemerkung": "Wir kommen zu zweit.",
        }, follow_redirects=True)
        with self.app.app_context():
            eintrag = OpenDayRegistration.query.one()
            self.assertEqual(eintrag.schueler_id, self.student_id)
            self.assertTrue(eintrag.teilnahme)
            self.assertEqual(eintrag.wuensche, ("fuehrung", "ogs"))
            self.assertEqual(eintrag.bemerkung, "Wir kommen zu zweit.")

    def test_absage_verwirft_die_programmwuensche(self):
        """Sonst zählte eine Familie überall mit, die gar nicht kommt."""
        self._anmelden_als_eltern()
        self.client.post("/eltern/tag-der-offenen-tuer", data={
            "name": "Anna Beispiel", "email": "mutter@example.de", "teilnahme": "ja",
            "fuehrung": "ja"}, follow_redirects=True)
        self.client.post("/eltern/tag-der-offenen-tuer", data={
            "name": "Anna Beispiel", "email": "mutter@example.de", "teilnahme": "nein",
            "fuehrung": "ja"}, follow_redirects=True)
        with self.app.app_context():
            eintrag = OpenDayRegistration.query.one()
            self.assertFalse(eintrag.teilnahme)
            self.assertEqual(eintrag.wuensche, ())

    def test_beide_sorgeberechtigten_bearbeiten_dieselbe_rueckmeldung(self):
        self._anmelden_als_eltern()
        self.client.post("/eltern/tag-der-offenen-tuer", data={
            "name": "Anna Beispiel", "email": "mutter@example.de", "teilnahme": "ja",
            "fuehrung": "ja"}, follow_redirects=True)
        zweiter = self._anmelden_als_eltern(self.token_zwei, self.app.test_client(),
                                            "vater@example.de")
        zweiter.post("/eltern/tag-der-offenen-tuer", data={
            "name": "Ben Beispiel", "email": "vater@example.de", "teilnahme": "ja",
            "unterricht": "ja"}, follow_redirects=True)
        with self.app.app_context():
            eintrag = OpenDayRegistration.query.one()
            self.assertEqual(eintrag.name, "Ben Beispiel")
            self.assertEqual(eintrag.wuensche, ("unterricht",))

    def test_nach_anmeldeschluss_wird_nichts_mehr_angenommen(self):
        with self.app.app_context():
            event = db.session.get(OpenDayEvent, self.event_id)
            event.anmeldeschluss = datetime.date.today() - datetime.timedelta(days=1)
            db.session.commit()
        self._anmelden_als_eltern()
        antwort = self.client.post("/eltern/tag-der-offenen-tuer", data={
            "name": "Anna Beispiel", "email": "mutter@example.de", "teilnahme": "ja",
            "fuehrung": "ja"}, follow_redirects=True)
        self.assertIn("Anmeldefrist ist abgelaufen", antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(OpenDayRegistration.query.count(), 0)

    def test_ein_entwurf_ist_fuer_eltern_unsichtbar(self):
        with self.app.app_context():
            db.session.get(OpenDayEvent, self.event_id).status = "draft"
            db.session.commit()
        self._anmelden_als_eltern()
        antwort = self.client.get("/eltern/tag-der-offenen-tuer", follow_redirects=True)
        self.assertIn("kein Tag der offenen Tür ausgeschrieben", antwort.get_data(as_text=True))

    def test_der_zugewiesene_ablauf_erscheint_im_portal(self):
        self._anmelden_als_eltern()
        self.client.post("/eltern/tag-der-offenen-tuer", data={
            "name": "Anna Beispiel", "email": "mutter@example.de", "teilnahme": "ja",
            "fuehrung": "ja"}, follow_redirects=True)
        with self.app.app_context():
            event = db.session.get(OpenDayEvent, self.event_id)
            einteilen(event)
            db.session.commit()
        seite = self.client.get("/eltern/tag-der-offenen-tuer").get_data(as_text=True)
        self.assertIn("Ihr Ablauf", seite)
        self.assertIn("Schulführung", seite)


class MailTests(_OpenDayFixture, unittest.TestCase):
    def test_der_plan_nennt_nur_die_gewaehlten_stationen(self):
        with self.app.app_context():
            eintrag = self._anmelden("Text", ("fuehrung", "ogs"), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            text = notifications.plan_text(eintrag, event, schule="Musterschule")

            self.assertIn("Gruppe 1", text)
            self.assertIn("Schulführung", text)
            self.assertIn("Hospitation in einer OGS-Gruppe", text)
            self.assertNotIn("Unterrichtsstunde", text)
            self.assertIn("Haupteingang", text)

    def test_eine_absage_bekommt_eine_bestaetigung_ohne_ablauf(self):
        with self.app.app_context():
            eintrag = self._anmelden("Absage", (), teilnahme=False)
            event = db.session.get(OpenDayEvent, self.event_id)
            text = notifications.plan_text(eintrag, event)
            self.assertIn("nicht teilnehmen", text)
            self.assertNotIn("Gruppe", text)

    def test_versand_vermerkt_den_stand_und_wiederholt_sich_nicht(self):
        with self.app.app_context():
            eintrag = self._anmelden("Einmal", ("fuehrung",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            zugestellt, _ = notifications.send_plans(self.app, [eintrag], event)
            self.assertEqual(zugestellt, 1)
            self.assertIsNotNone(eintrag.plan_gesendet_am)

            wieder, uebersprungen = notifications.send_plans(self.app, [eintrag], event)
            self.assertEqual((wieder, uebersprungen), (0, 1))

    def test_eine_geaenderte_einteilung_macht_den_plan_ueberholt(self):
        with self.app.app_context():
            eintrag = self._anmelden("Wechsel", ("fuehrung",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            notifications.send_plans(self.app, [eintrag], event)
            self.assertFalse(eintrag.plan_veraltet)

            eintrag.gruppe = 2
            self.assertTrue(eintrag.plan_veraltet)
            zugestellt, _ = notifications.send_plans(self.app, [eintrag], event)
            self.assertEqual(zugestellt, 1)

    def test_ohne_gruppe_geht_kein_ablaufplan_hinaus(self):
        with self.app.app_context():
            eintrag = self._anmelden("Offen", ("fuehrung",))
            event = db.session.get(OpenDayEvent, self.event_id)
            zugestellt, uebersprungen = notifications.send_plans(self.app, [eintrag], event)
            self.assertEqual((zugestellt, uebersprungen), (0, 1))


class StaffRouteTests(_OpenDayFixture, unittest.TestCase):
    def test_anlegen_erzeugt_die_sechs_stationen(self):
        client = self._login_staff()
        client.post("/admin/tag-der-offenen-tuer/neu",
                    data={"titel": "Zweiter Termin", "datum": "2027-03-05"},
                    follow_redirects=True)
        with self.app.app_context():
            event = OpenDayEvent.query.filter_by(titel="Zweiter Termin").one()
            self.assertEqual(len(event.stationen), 6)
            self.assertEqual({station.gruppe for station in event.stationen}, set(GRUPPEN))

    def _stationsfelder(self, event_id):
        """Die Zeitfelder, die das Formular stets vollständig mitschickt."""
        felder = {}
        for station in OpenDayStation.query.filter_by(event_id=event_id).all():
            felder[f"station-{station.id}-beginn"] = station.beginn.strftime("%H:%M")
            felder[f"station-{station.id}-ende"] = station.ende.strftime("%H:%M")
        return felder

    def test_nur_ein_veroeffentlichter_tag_je_jahrgang(self):
        client = self._login_staff()
        client.post("/admin/tag-der-offenen-tuer/neu",
                    data={"titel": "Zweiter", "datum": "2027-03-05"}, follow_redirects=True)
        with self.app.app_context():
            zweiter = OpenDayEvent.query.filter_by(titel="Zweiter").one().id
            felder = self._stationsfelder(zweiter)
        antwort = client.post(f"/admin/tag-der-offenen-tuer/{zweiter}", data=dict(
            felder, titel="Zweiter", datum="2027-03-05", status="published"),
            follow_redirects=True)
        self.assertIn("bereits ein Tag der offenen Tür veröffentlicht",
                      antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(db.session.get(OpenDayEvent, zweiter).status, "draft")

    def test_stationszeiten_muessen_zusammenpassen(self):
        client = self._login_staff()
        with self.app.app_context():
            station = OpenDayStation.query.filter_by(event_id=self.event_id, gruppe=1,
                                                     art="fuehrung").one()
            station_id, vorher = station.id, station.beginn
        antwort = client.post(f"/admin/tag-der-offenen-tuer/{self.event_id}", data={
            "titel": "Tag der offenen Tür", "datum": "2027-03-05",
            f"station-{station_id}-beginn": "11:00", f"station-{station_id}-ende": "10:00",
        }, follow_redirects=True)
        self.assertIn("Ende liegt vor Beginn", antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(db.session.get(OpenDayStation, station_id).beginn, vorher)

    def test_auswertung_zeigt_die_auslastung_je_station(self):
        with self.app.app_context():
            self._anmelden("Ammer", ("fuehrung", "ogs"), gruppe=1)
            self._anmelden("Berg", ("fuehrung",), gruppe=2)
        seite = self._login_staff().get(
            f"/admin/tag-der-offenen-tuer/{self.event_id}/auswertung").get_data(as_text=True)
        self.assertIn("Familie Ammer", seite)
        self.assertIn("Auslastung der Stationen", seite)

    def test_gruppe_laesst_sich_von_hand_umstellen(self):
        with self.app.app_context():
            eintrag = self._anmelden("Hand", ("fuehrung",), gruppe=1)
            eintrag_id = eintrag.id
        self._login_staff().post(
            f"/admin/tag-der-offenen-tuer/{self.event_id}/gruppe/{eintrag_id}",
            data={"gruppe": "2"}, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(db.session.get(OpenDayRegistration, eintrag_id).gruppe, 2)

    def test_teilnehmerlisten_stehen_je_station_bereit(self):
        with self.app.app_context():
            self._anmelden("Liste", ("unterricht",), gruppe=2)
        seite = self._login_staff().get(
            f"/admin/tag-der-offenen-tuer/{self.event_id}/listen").get_data(as_text=True)
        self.assertIn("Familie Liste", seite)
        self.assertIn("Unterrichtsstunde", seite)

    def test_fremde_jahrgaenge_sind_nicht_erreichbar(self):
        with self.app.app_context():
            fremd = OpenDayEvent(school_year=JAHR + 5, datum=datetime.date(2032, 5, 1))
            db.session.add(fremd)
            db.session.commit()
            fremd_id = fremd.id
        antwort = self._login_staff().get(f"/admin/tag-der-offenen-tuer/{fremd_id}",
                                          follow_redirects=True)
        self.assertIn("gehört nicht zum geöffneten Jahrgang", antwort.get_data(as_text=True))


class LetterTests(_OpenDayFixture, unittest.TestCase):
    """Die Einladung steht als Abschnitt im Anmeldebrief."""

    def test_der_abschnitt_nennt_das_datum_der_veranstaltung(self):
        from sl_office.parent_portal.letters import german_date, open_day_label
        with self.app.app_context():
            event = db.session.get(OpenDayEvent, self.event_id)
            self.assertEqual(open_day_label(JAHR), german_date(event.datum))

    def test_ohne_veroeffentlichte_veranstaltung_bleibt_der_platzhalter_leer(self):
        """Dann entfallen Überschrift und Absatz von selbst -- wie beim Zeitraum."""
        from sl_office.parent_portal.letters import DEFAULT_BODY, fill, open_day_label
        with self.app.app_context():
            db.session.get(OpenDayEvent, self.event_id).status = "draft"
            db.session.commit()
            self.assertEqual(open_day_label(JAHR), "")
            self.assertIsNone(veroeffentlichtes_event(JAHR))

        ueberschrift = next(zeile for zeile in DEFAULT_BODY.splitlines()
                            if zeile.startswith("# Tag der offenen Tür"))
        self.assertIsNone(fill(ueberschrift, {"tdot": ""}))


class PlatzTests(_OpenDayFixture, unittest.TestCase):
    """Die Feineinteilung: Eltern wählen die Station, die Schule die Klasse."""

    def _plaetze(self, art, namen, kapazitaet=None):
        """Je Gruppe dieselben Plätze anlegen; liefert {gruppe: [platz, ...]}."""
        plan = stationsplan(db.session.get(OpenDayEvent, self.event_id))
        angelegt = {}
        for gruppe in GRUPPEN:
            station = plan[(gruppe, art)]
            for name in namen:
                station.plaetze.append(OpenDayPlatz(bezeichnung=name, ort=f"Raum {name[-2:]}",
                                                    kapazitaet=kapazitaet))
            angelegt[gruppe] = station.plaetze
        db.session.commit()
        return angelegt

    def test_familien_verteilen_sich_gleichmaessig_auf_die_klassen(self):
        with self.app.app_context():
            self._plaetze("unterricht", ["Klasse 1a", "Klasse 2b", "Klasse 3c"])
            for nummer in range(12):
                self._anmelden(f"Kind{nummer:02d}", ("unterricht",),
                               gruppe=1 if nummer % 2 == 0 else 2)
            event = db.session.get(OpenDayEvent, self.event_id)
            zugeteilt, offen = plaetze_zuteilen(event)
            db.session.commit()

            self.assertEqual((zugeteilt, offen), (12, 0))
            belegung = {}
            for zeile in OpenDayZuteilung.query.all():
                belegung[zeile.platz_id] = belegung.get(zeile.platz_id, 0) + 1
            self.assertEqual(sorted(belegung.values()), [2] * 6)

    def test_die_kapazitaet_wird_eingehalten_und_engpaesse_gemeldet(self):
        with self.app.app_context():
            self._plaetze("unterricht", ["Klasse 1a"], kapazitaet=2)
            for nummer in range(4):
                self._anmelden(f"Voll{nummer}", ("unterricht",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            zugeteilt, offen = plaetze_zuteilen(event)
            db.session.commit()

            self.assertEqual((zugeteilt, offen), (2, 2))
            self.assertEqual(len(ohne_platz(event)), 2)

    def test_stationen_ohne_plaetze_bleiben_unberuehrt(self):
        """Die Schulführung teilt sich nicht auf -- sie braucht keine Zuteilung."""
        with self.app.app_context():
            self._plaetze("unterricht", ["Klasse 1a"])
            eintrag = self._anmelden("Beides", ("fuehrung", "unterricht"), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            plaetze_zuteilen(event)
            db.session.commit()

            self.assertEqual(len(eintrag.zuteilungen), 1)
            plan = ablaufplan(eintrag, stationsplan(event))
            self.assertIsNone(plan[0][2], "Führung ohne Platz")
            self.assertIsNotNone(plan[1][2], "Unterricht mit Klasse")

    def test_gruppenwechsel_raeumt_den_alten_platz_weg(self):
        """Sonst stünde die Familie in einer Klasse, die ihre Gruppe nicht besucht."""
        with self.app.app_context():
            self._plaetze("unterricht", ["Klasse 1a"])
            eintrag = self._anmelden("Wechsel", ("unterricht",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            plaetze_zuteilen(event)
            db.session.commit()
            alte_station = next(iter(zuteilung_je_station(eintrag))) 

            eintrag.gruppe = 2
            db.session.flush()
            plaetze_zuteilen(event)
            db.session.commit()

            neue = zuteilung_je_station(eintrag)
            self.assertEqual(len(neue), 1)
            self.assertNotIn(alte_station, neue)
            self.assertEqual(neue[next(iter(neue))].platz.station.gruppe, 2)

    def test_abgewaehlter_programmpunkt_hebt_die_zuteilung_auf(self):
        with self.app.app_context():
            self._plaetze("unterricht", ["Klasse 1a"])
            eintrag = self._anmelden("Abwahl", ("unterricht",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            plaetze_zuteilen(event)
            db.session.commit()
            self.assertEqual(len(eintrag.zuteilungen), 1)

            eintrag.wunsch_unterricht = False
            db.session.flush()
            plaetze_zuteilen(event)
            db.session.commit()
            self.assertEqual(len(eintrag.zuteilungen), 0)

    def test_ein_geaenderter_platz_macht_den_verschickten_plan_ueberholt(self):
        with self.app.app_context():
            plaetze = self._plaetze("unterricht", ["Klasse 1a", "Klasse 2b"])
            eintrag = self._anmelden("Umzug", ("unterricht",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            plaetze_zuteilen(event)
            db.session.commit()
            notifications.send_plans(self.app, [eintrag], event)
            self.assertFalse(eintrag.plan_veraltet)

            zeile = eintrag.zuteilungen[0]
            anderer = next(platz for platz in plaetze[1] if platz.id != zeile.platz_id)
            zeile.platz_id = anderer.id
            db.session.commit()
            self.assertTrue(eintrag.plan_veraltet)

    def test_der_platz_steht_in_mail_und_pdf(self):
        from sl_office.open_day import plan_pdf
        from sl_office.parent_portal.letterhead import branding
        with self.app.app_context():
            self._plaetze("unterricht", ["Klasse 2b"])
            eintrag = self._anmelden("Papier", ("unterricht",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            plaetze_zuteilen(event)
            db.session.commit()

            self.assertIn("Klasse 2b", notifications.plan_text(eintrag, event))
            daten = plan_pdf.build_plan(event, eintrag, branding(self.app.config))
            self.assertTrue(daten.startswith(b"%PDF-"))

    def test_die_mail_haengt_den_ablaufplan_als_pdf_an(self):
        with self.app.app_context():
            versendet = []
            eintrag = self._anmelden("Anhang", ("unterricht",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            from sl_office.open_day import notifications as modul
            original = modul.send_message
            modul.send_message = lambda app, message: versendet.append(message) or True
            try:
                modul.send_plan(self.app, eintrag, event)
            finally:
                modul.send_message = original

            anhaenge = [teil.get_filename() for teil in versendet[0].iter_attachments()]
            self.assertEqual(anhaenge, ["Ablaufplan Tag der offenen Tür.pdf"])

    def test_handzuteilung_setzt_und_hebt_auf(self):
        client = self._login_staff()
        with self.app.app_context():
            plaetze = self._plaetze("unterricht", ["Klasse 1a", "Klasse 2b"])
            eintrag = self._anmelden("Hand", ("unterricht",), gruppe=1)
            eintrag_id, station_id = eintrag.id, plaetze[1][0].station_id
            ziel_id = plaetze[1][1].id
        pfad = (f"/admin/tag-der-offenen-tuer/{self.event_id}/feineinteilung/"
                f"{eintrag_id}/{station_id}")
        client.post(pfad, data={"platz": str(ziel_id)}, follow_redirects=True)
        with self.app.app_context():
            eintrag = db.session.get(OpenDayRegistration, eintrag_id)
            self.assertEqual(eintrag.zuteilungen[0].platz_id, ziel_id)
        client.post(pfad, data={"platz": ""}, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(db.session.get(OpenDayRegistration, eintrag_id).zuteilungen, [])

    def test_ein_platz_aus_einer_fremden_station_wird_abgewiesen(self):
        client = self._login_staff()
        with self.app.app_context():
            plaetze = self._plaetze("unterricht", ["Klasse 1a"])
            eintrag = self._anmelden("Fremd", ("unterricht",), gruppe=1)
            eintrag_id = eintrag.id
            station_id = plaetze[1][0].station_id      # Gruppe 1
            fremder_platz = plaetze[2][0].id           # Gruppe 2
        antwort = client.post(
            f"/admin/tag-der-offenen-tuer/{self.event_id}/feineinteilung/"
            f"{eintrag_id}/{station_id}",
            data={"platz": str(fremder_platz)}, follow_redirects=True)
        self.assertIn("gehört zu einer anderen Station", antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(db.session.get(OpenDayRegistration, eintrag_id).zuteilungen, [])

    def test_klassen_lassen_sich_ueber_das_formular_pflegen(self):
        """Die Felder liegen im großen Formular und tragen die Stationsnummer."""
        client = self._login_staff()
        with self.app.app_context():
            station = OpenDayStation.query.filter_by(
                event_id=self.event_id, gruppe=1, art="unterricht").one()
            station_id = station.id
            felder = {f"station-{s.id}-beginn": s.beginn.strftime("%H:%M")
                      for s in OpenDayStation.query.filter_by(event_id=self.event_id)}
            felder.update({f"station-{s.id}-ende": s.ende.strftime("%H:%M")
                           for s in OpenDayStation.query.filter_by(event_id=self.event_id)})

        client.post(f"/admin/tag-der-offenen-tuer/{self.event_id}/station/{station_id}/platz",
                    data=dict(felder, **{f"neuer-platz-{station_id}-bezeichnung": "Klasse 2b",
                                         f"neuer-platz-{station_id}-ort": "Raum 12",
                                         f"neuer-platz-{station_id}-kapazitaet": "8"}),
                    follow_redirects=True)
        with self.app.app_context():
            platz = OpenDayPlatz.query.one()
            self.assertEqual((platz.bezeichnung, platz.ort, platz.kapazitaet),
                             ("Klasse 2b", "Raum 12", 8))
            platz_id = platz.id

        # Umbenennen über das Hauptformular
        with self.app.app_context():
            felder = {f"station-{s.id}-beginn": s.beginn.strftime("%H:%M")
                      for s in OpenDayStation.query.filter_by(event_id=self.event_id)}
            felder.update({f"station-{s.id}-ende": s.ende.strftime("%H:%M")
                           for s in OpenDayStation.query.filter_by(event_id=self.event_id)})
        client.post(f"/admin/tag-der-offenen-tuer/{self.event_id}",
                    data=dict(felder, titel="Tag der offenen Tür", datum="2027-03-05",
                              **{f"platz-{platz_id}-bezeichnung": "Klasse 3c",
                                 f"platz-{platz_id}-ort": "Raum 21",
                                 f"platz-{platz_id}-kapazitaet": ""}),
                    follow_redirects=True)
        with self.app.app_context():
            platz = db.session.get(OpenDayPlatz, platz_id)
            self.assertEqual((platz.bezeichnung, platz.ort, platz.kapazitaet),
                             ("Klasse 3c", "Raum 21", None))

        client.post(f"/admin/tag-der-offenen-tuer/{self.event_id}/platz/{platz_id}/loeschen",
                    follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(OpenDayPlatz.query.count(), 0)

    def test_teilnehmerlisten_werden_je_klasse_gefuehrt(self):
        with self.app.app_context():
            self._plaetze("unterricht", ["Klasse 1a", "Klasse 2b"])
            self._anmelden("Listig", ("unterricht",), gruppe=1)
            event = db.session.get(OpenDayEvent, self.event_id)
            plaetze_zuteilen(event)
            db.session.commit()
        seite = self._login_staff().get(
            f"/admin/tag-der-offenen-tuer/{self.event_id}/listen").get_data(as_text=True)
        self.assertIn("Klasse 1a", seite)
        self.assertIn("Klasse 2b", seite)


class ProgressTests(unittest.TestCase):
    """Der Stand des Anmeldeverfahrens, wie Eltern ihn im Portal sehen."""

    def setUp(self):
        self.app = create_app("testing")
        self.client = self.app.test_client()
        with self.app.app_context():
            db.session.add_all([GlobalSettings(einschulungsjahr=JAHR),
                                Einschulungsjahr(jahr=JAHR, ist_aktuell=True)])
            kind = Schueler(vorname="Stand", nachname="Kind", einschulungsjahr=JAHR)
            db.session.add(kind)
            db.session.flush()
            _, token = create_activation_grant(kind.id, "first_access")
            db.session.commit()
            self.student_id, self.token = kind.id, token

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _anmelden(self):
        self.client.post(f"/eltern/aktivieren/{self.token}",
                         data={"email": "mutter@example.de", "display_name": "Anna Beispiel"},
                         follow_redirects=True)

    def test_das_dashboard_zeigt_die_schritte_des_verfahrens(self):
        self._anmelden()
        seite = self.client.get("/eltern/uebersicht").get_data(as_text=True)
        self.assertIn("Ihr aktueller Stand", seite)
        for titel in ("Zugang eingerichtet", "Anmeldetermin wählen",
                      "Anmeldeformular ausfüllen", "Anmeldegespräch"):
            self.assertIn(titel, seite)

    def test_der_zweite_zugang_wird_als_offen_vermerkt(self):
        self._anmelden()
        seite = self.client.get("/eltern/uebersicht").get_data(as_text=True)
        self.assertIn("zweite Zugang aus dem Anmeldeschreiben", seite)

    def test_ein_begonnenes_formular_gilt_als_in_bearbeitung(self):
        from sl_office.parent_portal.progress import prozessschritte
        with self.app.app_context():
            kind = db.session.get(Schueler, self.student_id)
            entwurf = ParentRegistration(schueler_id=kind.id, status="draft",
                                         data={"kind_vorname": "Stand"})
            db.session.add(entwurf)
            db.session.commit()
            schritte = prozessschritte(kind, None, entwurf, lambda slot, event: "")
            formular = next(s for s in schritte if s["titel"].startswith("Anmeldeformular"))
            self.assertEqual(formular["zustand"], "laeuft")

    def test_ein_uebermitteltes_formular_gilt_als_erledigt(self):
        from sl_office.parent_portal.progress import fortschritt, prozessschritte
        with self.app.app_context():
            kind = db.session.get(Schueler, self.student_id)
            abgegeben = ParentRegistration(
                schueler_id=kind.id, status="submitted", data={"kind_vorname": "Stand"},
                submitted_at=datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC))
            db.session.add(abgegeben)
            db.session.commit()
            schritte = prozessschritte(kind, None, abgegeben, lambda slot, event: "")
            formular = next(s for s in schritte if s["titel"].startswith("Anmeldeformular"))
            self.assertEqual(formular["zustand"], "erledigt")
            self.assertIn("01.10.2026", formular["text"])
            self.assertGreater(fortschritt(schritte), 0)

    def test_der_tag_der_offenen_tuer_erscheint_nur_wenn_ausgeschrieben(self):
        from sl_office.parent_portal.progress import prozessschritte
        with self.app.app_context():
            kind = db.session.get(Schueler, self.student_id)
            ohne = prozessschritte(kind, None, None, lambda slot, event: "")
            self.assertFalse(any("offenen Tür" in s["titel"] for s in ohne))

            event = OpenDayEvent(school_year=JAHR, status="published",
                                 datum=datetime.date(2026, 11, 14))
            standard_stationen(event)
            db.session.add(event)
            db.session.commit()
            mit = prozessschritte(kind, None, None, lambda slot, event: "",
                                  open_day_event=event)
            tag = next(s for s in mit if "offenen Tür" in s["titel"])
            self.assertEqual(tag["zustand"], "offen")

    def test_ein_vergangenes_gespraech_gilt_als_gefuehrt(self):
        from sl_office.parent_portal.models import AppointmentEvent, AppointmentSlot
        from sl_office.parent_portal.progress import prozessschritte
        with self.app.app_context():
            kind = db.session.get(Schueler, self.student_id)
            frueher = datetime.datetime(2026, 1, 5, 9, 0)
            event = AppointmentEvent(title="Anmeldung", school_year=JAHR, status="published")
            db.session.add(event)
            db.session.flush()
            slot = AppointmentSlot(event_id=event.id, starts_at=frueher,
                                   ends_at=frueher + datetime.timedelta(minutes=40))
            db.session.add(slot)
            db.session.commit()
            schritte = prozessschritte(kind, (None, slot, event), None,
                                       lambda slot, event: "Mo 05.01.2026, 09:00 Uhr",
                                       jetzt=datetime.datetime(2026, 3, 1))
            gespraech = next(s for s in schritte if s["titel"] == "Anmeldegespräch")
            self.assertEqual(gespraech["zustand"], "erledigt")
            self.assertIn("stattgefunden", gespraech["text"])


if __name__ == "__main__":
    unittest.main()
