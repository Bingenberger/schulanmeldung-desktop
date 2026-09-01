"""Terminmails: Bestätigung, Hinweis an die Schule, Erinnerung.

Zugestellt wird nichts: der SMTP-Adapter wird durch einen Postausgang im
Speicher ersetzt, sodass die Nachrichten selbst geprüft werden können.
"""

import datetime
import os
import unittest

os.environ["SL_OFFICE_ENV"] = "testing"

from app import create_app  # noqa: E402
from models import Schueler, db  # noqa: E402
from sl_office.appointments import notifications  # noqa: E402
from sl_office.appointments.service import book_slot  # noqa: E402
from sl_office.parent_portal.models import (  # noqa: E402
    AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentAccess,
)


def _naive(value):
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


class _MailFixture:
    """Ein Kind, ein Elternzugang und ein Termin in zehn Tagen."""

    def setUp(self):
        self.app = create_app("testing", {
            "NOTIFY_MAIL": "sekretariat@example.de",
            "SCHOOL_NAME": "Testschule",
            "MAIL_FROM": "noreply@example.de",
        })
        self.outbox = []
        # Der Adapter wird im Modul der Nachrichten nachgeschlagen, also dort ersetzen.
        self._real_send = notifications.send_message
        notifications.send_message = lambda app, message: (self.outbox.append(message), True)[1]

        with self.app.app_context():
            student = Schueler(vorname="Termin", nachname="Kind")
            start = _naive(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=10)).replace(
                minute=0, second=0, microsecond=0)
            event = AppointmentEvent(
                title="Anmeldung 2027", school_year=2027, status="published",
                booking_opens_at=_naive(datetime.datetime.now(datetime.UTC)) - datetime.timedelta(days=1),
                booking_closes_at=start + datetime.timedelta(days=5),
                parent_instructions="Bitte bringen Sie Ihr Kind mit.",
            )
            db.session.add_all([student, event])
            db.session.flush()
            slot = AppointmentSlot(
                event_id=event.id, starts_at=start,
                ends_at=start + datetime.timedelta(minutes=40), capacity=2,
                location="Raum 1",
            )
            access = ParentAccess(schueler_id=student.id, email_normalized="eltern@example.de",
                                  display_name="Familie Kind", status="active")
            db.session.add_all([slot, access])
            db.session.commit()
            self.student_id, self.slot_id, self.access_id = student.id, slot.id, access.id
            self.event_id, self.start = event.id, start

    def tearDown(self):
        notifications.send_message = self._real_send
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _book(self):
        with self.app.app_context():
            booking = book_slot(self.slot_id, self.student_id, self.access_id)
            db.session.commit()
            return booking.id

    def _by_recipient(self, address):
        return [message for message in self.outbox if message["To"] == address]


class BookingConfirmationTests(_MailFixture, unittest.TestCase):

    def test_parents_get_a_confirmation_with_the_appointment_as_ics(self):
        booking_id = self._book()
        with self.app.app_context():
            notifications.confirm_booking(self.app, booking_id)

        (message,) = self._by_recipient("eltern@example.de")
        self.assertIn("Anmeldetermin", message["Subject"])
        body = message.get_body(("plain",)).get_content()
        self.assertIn("Raum 1", body)
        self.assertIn("Bitte bringen Sie Ihr Kind mit.", body)

        (attachment,) = list(message.iter_attachments())
        self.assertEqual(attachment.get_filename(), "Anmeldetermin.ics")
        self.assertEqual(attachment.get_content_type(), "text/calendar")
        calendar = attachment.get_payload(decode=True).decode("utf-8")
        self.assertIn("BEGIN:VEVENT", calendar)
        self.assertIn(f"UID:anmeldetermin-{booking_id}@sl-office", calendar)

    def test_the_school_is_told_about_the_booking(self):
        with self.app.app_context():
            notifications.confirm_booking(self.app, self._book())

        (message,) = self._by_recipient("sekretariat@example.de")
        self.assertIn("Kind, Termin", message["Subject"])
        body = message.get_body(("plain",)).get_content()
        self.assertIn("Anmeldung 2027", body)
        self.assertIn("Familie Kind", body)

    def test_without_a_configured_address_the_school_gets_nothing(self):
        self.app.config["NOTIFY_MAIL"] = ""
        self.app.config["SCHOOL_CONTACT_MAIL"] = ""
        with self.app.app_context():
            notifications.confirm_booking(self.app, self._book())
        self.assertEqual(len(self.outbox), 1)  # nur die Bestätigung an die Eltern

    def test_a_short_notice_booking_does_not_also_trigger_a_reminder(self):
        # Wer innerhalb des Vorlaufs bucht, hat die Eckdaten gerade gelesen.
        with self.app.app_context():
            slot = db.session.get(AppointmentSlot, self.slot_id)
            slot.starts_at = _naive(datetime.datetime.now(datetime.UTC)) + datetime.timedelta(hours=5)
            slot.ends_at = slot.starts_at + datetime.timedelta(minutes=40)
            db.session.commit()
            booking_id = self._book()
            notifications.confirm_booking(self.app, booking_id)
            self.assertIsNotNone(db.session.get(AppointmentBooking, booking_id).reminder_sent_at)
            self.assertEqual(notifications.send_due_reminders(self.app), (0, 0))


class ReminderTests(_MailFixture, unittest.TestCase):

    def _move_slot(self, delta):
        with self.app.app_context():
            slot = db.session.get(AppointmentSlot, self.slot_id)
            slot.starts_at = _naive(datetime.datetime.now(datetime.UTC)) + delta
            slot.ends_at = slot.starts_at + datetime.timedelta(minutes=40)
            db.session.commit()

    def test_an_appointment_inside_the_window_is_reminded_once(self):
        booking_id = self._book()
        self._move_slot(datetime.timedelta(hours=20))
        with self.app.app_context():
            self.assertEqual(notifications.send_due_reminders(self.app), (1, 0))
            self.assertIsNotNone(db.session.get(AppointmentBooking, booking_id).reminder_sent_at)
            # Ein zweiter Lauf darf nicht erneut zustellen.
            self.assertEqual(notifications.send_due_reminders(self.app), (0, 0))

        (message,) = self._by_recipient("eltern@example.de")
        self.assertIn("Erinnerung", message["Subject"])
        self.assertEqual([part.get_filename() for part in message.iter_attachments()],
                         ["Anmeldetermin.ics"])

    def test_an_appointment_beyond_the_window_waits(self):
        self._book()
        with self.app.app_context():
            self.assertEqual(notifications.send_due_reminders(self.app), (0, 0))
        self.assertEqual(self.outbox, [])

    def test_a_past_appointment_is_not_reminded(self):
        self._book()
        self._move_slot(datetime.timedelta(hours=-2))
        with self.app.app_context():
            self.assertEqual(notifications.send_due_reminders(self.app), (0, 0))
        self.assertEqual(self.outbox, [])

    def test_a_cancelled_appointment_is_not_reminded(self):
        booking_id = self._book()
        self._move_slot(datetime.timedelta(hours=20))
        with self.app.app_context():
            db.session.get(AppointmentBooking, booking_id).status = "cancelled"
            db.session.commit()
            self.assertEqual(notifications.send_due_reminders(self.app), (0, 0))
        self.assertEqual(self.outbox, [])

    def test_both_parents_are_reminded(self):
        with self.app.app_context():
            db.session.add(ParentAccess(schueler_id=self.student_id, status="active",
                                        email_normalized="zweiter@example.de",
                                        display_name="Zweiter Elternteil"))
            db.session.commit()
        self._book()
        self._move_slot(datetime.timedelta(hours=20))
        with self.app.app_context():
            self.assertEqual(notifications.send_due_reminders(self.app), (1, 0))
        self.assertEqual({message["To"] for message in self.outbox},
                         {"eltern@example.de", "zweiter@example.de"})

    def test_a_staff_assigned_appointment_without_access_is_marked_done(self):
        with self.app.app_context():
            other = Schueler(vorname="Ohne", nachname="Zugang")
            db.session.add(other)
            db.session.flush()
            booking = AppointmentBooking(event_id=self.event_id, slot_id=self.slot_id,
                                         schueler_id=other.id, source="staff", status="confirmed")
            db.session.add(booking)
            db.session.commit()
            booking_id = booking.id
        self._move_slot(datetime.timedelta(hours=20))
        with self.app.app_context():
            self.assertEqual(notifications.send_due_reminders(self.app), (0, 1))
            self.assertIsNotNone(db.session.get(AppointmentBooking, booking_id).reminder_sent_at)
        self.assertEqual(self.outbox, [])


if __name__ == "__main__":
    unittest.main()
