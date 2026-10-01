"""Sammeldrucke: alle Anmeldungen und alle Protokollbögen in je einem PDF.

Der heikle Punkt ist, dass sich die Ausfertigungen nicht in die Quere kommen:
Teilen sich die Seiten eine Vorlage, landen alle Kinder übereinander auf
derselben Seite. Die Tests lesen deshalb den Text je Seite zurück.
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
from sl_office.appointments import protocol_pdf  # noqa: E402
from sl_office.parent_portal import registration_pdf  # noqa: E402
from sl_office.parent_portal.models import (  # noqa: E402
    AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentRegistration,
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


class ProtocolTests(_Fixture, unittest.TestCase):
    def test_jedes_kind_bekommt_einen_eigenen_bogen(self):
        """Der Fallstrick: geteilte Vorlagenseiten drucken alle übereinander."""
        with self.app.app_context():
            erste = self._kind("Ammer", "Lina")
            zweite = self._kind("Berg", "Tom")
            self._termin(erste, 8)
            self._termin(zweite, 9)
            db.session.commit()
            event = db.session.get(AppointmentEvent, self.event_id)
            seiten = _seitentexte(protocol_pdf.build_many(protocol_pdf.fuer_veranstaltung(event)))

        vorlage_seiten = len(PdfReader(str(protocol_pdf.TEMPLATE)).pages)
        self.assertEqual(len(seiten), 2 * vorlage_seiten)
        self.assertIn("Lina", seiten[0])
        self.assertNotIn("Tom", seiten[0])
        self.assertIn("Tom", seiten[vorlage_seiten])
        self.assertNotIn("Lina", seiten[vorlage_seiten])

    def test_sortiert_nach_anmeldetermin(self):
        with self.app.app_context():
            spaet = self._kind("Ammer", "Spaet")       # alphabetisch zuerst
            frueh = self._kind("Zander", "Frueh")
            self._termin(spaet, 11)
            self._termin(frueh, 8)
            db.session.commit()
            event = db.session.get(AppointmentEvent, self.event_id)
            reihenfolge = [student.vorname
                           for student, _ in protocol_pdf.fuer_veranstaltung(event)]
        self.assertEqual(reihenfolge, ["Frueh", "Spaet"])

    def test_kinder_ohne_termin_stehen_am_ende(self):
        with self.app.app_context():
            mit = self._kind("Ammer", "MitTermin")
            self._kind("Berg", "OhneTermin")
            self._termin(mit, 9)
            db.session.commit()
            event = db.session.get(AppointmentEvent, self.event_id)
            eintraege = protocol_pdf.fuer_veranstaltung(event)
        self.assertEqual([student.vorname for student, _ in eintraege],
                         ["MitTermin", "OhneTermin"])
        self.assertIsNone(eintraege[1][1])

    def test_die_anmeldedaten_stehen_auf_dem_bogen(self):
        with self.app.app_context():
            student = self._kind("Yilmaz", "Ela", strasse="Kurzweg 2", plz="53859",
                                 ort="Niederkassel", kita="Kita Regenbogen",
                                 geburtsdatum=datetime.date(2021, 11, 4), kann_kind=True)
            slot = self._termin(student, 9)
            db.session.commit()
            from sl_office.appointments.service import slot_label
            event = db.session.get(AppointmentEvent, self.event_id)
            erwarteter_termin = slot_label(slot, event)
            seite = _seitentexte(protocol_pdf.build(student, (None, slot, event)))[0]

        for wert in ("Ela", "Yilmaz", "Kurzweg 2", "53859 Niederkassel",
                     "Kita Regenbogen", "04.11.2021", erwarteter_termin):
            self.assertIn(wert, seite, wert)

    def test_die_kita_kommt_aus_dem_anmeldeformular_der_eltern(self):
        """Das Stammdatenfeld stammt aus dem Import und kann falsch belegt sein."""
        with self.app.app_context():
            student = self._kind("Ammer", "Lina", kita="Max Ammer")   # Importfehler
            db.session.add(ParentRegistration(
                schueler_id=student.id, status="submitted",
                data={"besuchte_kita": "Kita Pappelweg"}))
            db.session.commit()
            seite = _seitentexte(protocol_pdf.build(student))[0]
        self.assertIn("Kita Pappelweg", seite)
        self.assertNotIn("Max Ammer", seite)

    def test_andere_einrichtung_nimmt_das_freitextfeld(self):
        with self.app.app_context():
            student = self._kind("Berg", "Tom")
            db.session.add(ParentRegistration(
                schueler_id=student.id, status="submitted",
                data={"besuchte_kita": "Andere", "besuchte_kita_andere": "Waldkita Ranzel"}))
            db.session.commit()
            seite = _seitentexte(protocol_pdf.build(student))[0]
        self.assertIn("Waldkita Ranzel", seite)

    def test_ohne_formularangabe_bleibt_das_stammdatenfeld(self):
        with self.app.app_context():
            student = self._kind("Conte", "Luca", kita="Kiga Weidenstraße")
            db.session.commit()
            seite = _seitentexte(protocol_pdf.build(student))[0]
        self.assertIn("Kiga Weidenstraße", seite)

    def test_ohne_termin_bleibt_die_zeile_leer_statt_zu_scheitern(self):
        with self.app.app_context():
            student = self._kind("Ohne", "Termin")
            db.session.commit()
            seite = _seitentexte(protocol_pdf.build(student, None))[0]
        self.assertIn("Termin", seite)       # der Vorname
        self.assertIn("Datum", seite)        # die Beschriftung der leeren Zeile

    @staticmethod
    def _kreuz_bei(payload):
        """Die x-Position des gesetzten Kreuzes auf der ersten Seite."""
        funde = []
        PdfReader(BytesIO(payload)).pages[0].extract_text(
            visitor_text=lambda text, cm, tm, fd, size:
            funde.append(tm[4]) if text.strip() == "X" else None)
        return funde

    def test_der_kann_kind_vermerk_wird_angekreuzt(self):
        """Die Vorlage hat das Feld, und den Wert kennt die Anwendung."""
        with self.app.app_context():
            kann = self._kind("Kann", "Kind", kann_kind=True)
            muss = self._kind("Muss", "Kind", kann_kind=False)
            db.session.commit()
            bei_kann = self._kreuz_bei(protocol_pdf.build(kann))
            bei_muss = self._kreuz_bei(protocol_pdf.build(muss))

        ja_x, nein_x = protocol_pdf.KANN_KIND[True][0], protocol_pdf.KANN_KIND[False][0]
        self.assertEqual(len(bei_kann), 1, "genau ein Kreuz")
        self.assertEqual(len(bei_muss), 1)
        # Das Kreuz sitzt mittig im vorgedruckten Kreis, also dicht an dessen Grundlinie.
        self.assertLess(abs(bei_kann[0] - ja_x), 5)
        self.assertLess(abs(bei_muss[0] - nein_x), 5)

    def test_sammeldruck_ueber_die_oberflaeche(self):
        with self.app.app_context():
            student = self._kind("Ammer", "Lina")
            self._termin(student, 9)
            db.session.commit()
        antwort = self._client().get(f"/admin/appointments/{self.event_id}/protokolle.pdf")
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.mimetype, "application/pdf")
        self.assertIn("Protokolle_Anmeldespiel", antwort.headers["Content-Disposition"])

    def test_einzelbogen_ueber_die_oberflaeche(self):
        with self.app.app_context():
            student = self._kind("Ammer", "Lina")
            db.session.commit()
            student_id = student.id
        antwort = self._client().get(f"/admin/appointments/schueler/{student_id}/protokoll.pdf")
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("Lina", _seitentexte(antwort.data)[0])


class AdminProtocolTests(_Fixture, unittest.TestCase):
    """Der Laufzettel der Verwaltungsanmeldung wird selbst gesetzt, nicht überlagert."""

    def _schule(self):
        from sl_office.parent_portal.letterhead import branding
        return branding(self.app.config)

    def test_ein_bogen_passt_auf_eine_seite(self):
        """Mit Unterschriftszeile -- die rutschte beim ersten Entwurf auf Seite 2."""
        from sl_office.appointments import admin_protocol_pdf
        with self.app.app_context():
            student = self._kind("Asanović-Baumgartner", "Leonardo Maximilian")
            self._termin(student, 9)
            db.session.commit()
            seiten = _seitentexte(admin_protocol_pdf.build(
                student, self._schule(), (None, AppointmentSlot.query.one(),
                                          db.session.get(AppointmentEvent, self.event_id))))
        self.assertEqual(len(seiten), 1)
        self.assertIn("Unterschrift Mitarbeiter:in Schule", seiten[0])

    def test_name_und_termin_stehen_darauf(self):
        from sl_office.appointments import admin_protocol_pdf
        from sl_office.appointments.service import slot_label
        with self.app.app_context():
            student = self._kind("Yilmaz", "Ela")
            slot = self._termin(student, 9)
            db.session.commit()
            event = db.session.get(AppointmentEvent, self.event_id)
            erwartet = slot_label(slot, event)
            seite = _seitentexte(admin_protocol_pdf.build(
                student, self._schule(), (None, slot, event)))[0]
        self.assertIn("Ela Yilmaz", seite)
        self.assertIn(erwartet, seite)

    def test_der_wortlaut_der_vorlage_steht_vollstaendig_darauf(self):
        from sl_office.appointments import admin_protocol_pdf
        with self.app.app_context():
            student = self._kind("Ammer", "Lina")
            db.session.commit()
            seite = _seitentexte(admin_protocol_pdf.build(student, self._schule()))[0]
        for abschnitt, punkte in admin_protocol_pdf.ABSCHNITTE:
            self.assertIn(abschnitt, seite)
            for punkt in punkte:
                self.assertIn(punkt.text, seite, punkt.text)
                for option in getattr(punkt, "optionen", ()):
                    self.assertIn(option, seite, option)

    def test_eine_uebermittelte_anmeldung_wird_vermerkt(self):
        from sl_office.appointments import admin_protocol_pdf
        with self.app.app_context():
            student = self._kind("Berg", "Tom")
            db.session.add(ParentRegistration(
                schueler_id=student.id, status="submitted", data={},
                submitted_at=datetime.datetime(2026, 9, 28, tzinfo=datetime.UTC)))
            db.session.commit()
            seite = _seitentexte(admin_protocol_pdf.build(student, self._schule()))[0]
        self.assertIn("elektronisch übermittelt am 28.09.2026", seite)

    def test_ein_entwurf_gilt_noch_nicht_als_uebermittelt(self):
        from sl_office.appointments import admin_protocol_pdf
        with self.app.app_context():
            student = self._kind("Conte", "Luca")
            db.session.add(ParentRegistration(schueler_id=student.id, status="draft", data={}))
            db.session.commit()
            seite = _seitentexte(admin_protocol_pdf.build(student, self._schule()))[0]
        self.assertNotIn("elektronisch übermittelt", seite)

    def test_der_stapel_laesst_jedem_kind_ein_eigenes_blatt(self):
        from sl_office.appointments import admin_protocol_pdf
        with self.app.app_context():
            erste = self._kind("Ammer", "Lina")
            zweite = self._kind("Berg", "Tom")
            db.session.commit()
            seiten = _seitentexte(admin_protocol_pdf.build_many(
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
        stapel = client.get(f"/admin/appointments/{self.event_id}/verwaltungsprotokolle.pdf")
        self.assertEqual(stapel.mimetype, "application/pdf")
        self.assertIn("Verwaltungsanmeldung", stapel.headers["Content-Disposition"])
        einzeln = client.get(
            f"/admin/appointments/schueler/{student_id}/verwaltungsprotokoll.pdf")
        self.assertIn("Lina", _seitentexte(einzeln.data)[0])


class DuplexTests(unittest.TestCase):
    """Beim beidseitigen Druck muss jedes Kind auf einem frischen Blatt beginnen."""

    @staticmethod
    def _vorlage(seiten, pfad):
        from reportlab.pdfgen import canvas
        pdf = canvas.Canvas(str(pfad), pagesize=(595.3, 841.9))
        for nummer in range(seiten):
            pdf.drawString(50, 700, f"Vorlagenseite {nummer + 1}")
            pdf.showPage()
        pdf.save()
        return pfad

    @staticmethod
    def _ebene(seiten):
        from io import BytesIO as _BytesIO
        from reportlab.pdfgen import canvas
        puffer = _BytesIO()
        pdf = canvas.Canvas(puffer, pagesize=(595.3, 841.9))
        for _ in range(seiten):
            pdf.showPage()
        pdf.save()
        return puffer.getvalue()

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.pfad = Path(self.ordner.name)

    def test_ungerade_vorlage_bekommt_eine_leerseite(self):
        from sl_office.services.pdf_forms import stack
        vorlage = self._vorlage(9, self.pfad / "ungerade.pdf")
        ebenen = [self._ebene(9), self._ebene(9)]
        seiten = _seitentexte(stack(vorlage, ebenen, doppelseitig=True))
        self.assertEqual(len(seiten), 20)
        self.assertEqual(seiten[9].strip(), "", "zehnte Seite ist leer")
        self.assertIn("Vorlagenseite 1", seiten[10], "das zweite Kind beginnt auf Blatt 6")

    def test_gerade_vorlage_bleibt_unveraendert(self):
        from sl_office.services.pdf_forms import stack
        vorlage = self._vorlage(8, self.pfad / "gerade.pdf")
        seiten = _seitentexte(stack(vorlage, [self._ebene(8)] * 2, doppelseitig=True))
        self.assertEqual(len(seiten), 16)
        self.assertIn("Vorlagenseite 1", seiten[8])

    def test_ohne_die_option_wird_nichts_ergaenzt(self):
        from sl_office.services.pdf_forms import stack
        vorlage = self._vorlage(9, self.pfad / "ungerade.pdf")
        self.assertEqual(len(_seitentexte(stack(vorlage, [self._ebene(9)] * 2))), 18)


class RegistrationBulkTests(_Fixture, unittest.TestCase):
    def _anmeldung(self, student, status="submitted", **daten):
        eintrag = ParentRegistration(schueler_id=student.id, status=status,
                                     data={"kind_vorname": student.vorname,
                                           "kind_nachname": student.nachname, **daten})
        db.session.add(eintrag)
        return eintrag

    def test_jede_anmeldung_steht_auf_eigenen_seiten(self):
        with self.app.app_context():
            self._anmeldung(self._kind("Ammer", "Lina"))
            self._anmeldung(self._kind("Berg", "Tom"))
            db.session.commit()
            paare = [(eintrag.data, db.session.get(Schueler, eintrag.schueler_id))
                     for eintrag in ParentRegistration.query.all()]
            seiten = _seitentexte(registration_pdf.build_many(paare))

        vorlage_seiten = len(PdfReader(str(registration_pdf.TEMPLATE)).pages)
        self.assertEqual(len(seiten), 2 * vorlage_seiten)
        self.assertIn("Lina", seiten[0])
        self.assertNotIn("Tom", seiten[0])
        self.assertIn("Tom", seiten[vorlage_seiten])

    def test_entwuerfe_bleiben_aussen_vor(self):
        with self.app.app_context():
            self._anmeldung(self._kind("Fertig", "Abgegeben"))
            self._anmeldung(self._kind("Entwurf", "InArbeit"), status="draft")
            db.session.commit()
        seiten = _seitentexte(self._client().get("/admin/anmeldungen/formulare.pdf").data)
        gesamt = "\n".join(seiten)
        self.assertIn("Abgegeben", gesamt)
        self.assertNotIn("InArbeit", gesamt)

    def test_der_statusfilter_der_uebersicht_gilt_auch_fuer_den_druck(self):
        with self.app.app_context():
            self._anmeldung(self._kind("Fertig", "Abgegeben"))
            self._anmeldung(self._kind("Entwurf", "InArbeit"), status="draft")
            db.session.commit()
        seiten = _seitentexte(
            self._client().get("/admin/anmeldungen/formulare.pdf?status=draft").data)
        gesamt = "\n".join(seiten)
        self.assertIn("InArbeit", gesamt)
        self.assertNotIn("Abgegeben", gesamt)

    def test_nach_nachnamen_sortiert(self):
        with self.app.app_context():
            self._anmeldung(self._kind("Zander", "Letzte"))
            self._anmeldung(self._kind("Ammer", "Erste"))
            db.session.commit()
        seiten = _seitentexte(self._client().get("/admin/anmeldungen/formulare.pdf").data)
        vorlage_seiten = len(PdfReader(str(registration_pdf.TEMPLATE)).pages)
        self.assertIn("Erste", seiten[0])
        self.assertIn("Letzte", seiten[vorlage_seiten])

    def test_ohne_anmeldungen_kommt_eine_meldung_statt_eines_leeren_pdf(self):
        antwort = self._client().get("/admin/anmeldungen/formulare.pdf", follow_redirects=True)
        self.assertIn("keine übermittelten Anmeldungen", antwort.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
