import datetime
import os
import unittest
from zoneinfo import ZoneInfo

os.environ["SL_OFFICE_ENV"] = "testing"

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from models import Schueler, User, db  # noqa: E402
from sl_office.appointments import calendar  # noqa: E402
from sl_office.appointments.routes import _planner_state  # noqa: E402
from sl_office.appointments.service import (  # noqa: E402
    BookingError, SlotHasBookings, SlotUnavailable, StudentAlreadyBooked,
    active_booking_for_student, assign_slot, assignable_slots,
    cancel_booking_as_staff, create_slot, delete_slot, generate_slots, move_slot, set_capacity,
)
from sl_office.appointments.models import (  # noqa: E402
    AppointmentBooking, AppointmentEvent, AppointmentSlot,
)


def _naive(value):
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


class _AppointmentFixture:
    """Veranstaltung, zwei Termine, zwei Kinder."""

    def setUp(self):
        self.app = create_app("testing")
        with self.app.app_context():
            self.student = Schueler(vorname="Termin", nachname="Kind")
            self.other_student = Schueler(vorname="Anderes", nachname="Kind")
            start = _naive(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=10)).replace(
                minute=0, second=0, microsecond=0
            )
            event = AppointmentEvent(
                title="Anmeldung", school_year=2027, status="published",
                # Gesprächstage: die Woche um den ersten Termin herum.
                slot_days_from=start.date() - datetime.timedelta(days=1),
                slot_days_until=start.date() + datetime.timedelta(days=5),
            )
            db.session.add_all([self.student, self.other_student, event])
            db.session.flush()
            self.slot = AppointmentSlot(
                event_id=event.id, starts_at=start,
                ends_at=start + datetime.timedelta(minutes=40), capacity=1,
            )
            # Two hours later, leaving room to test an adjacent slot in between.
            self.second_slot = AppointmentSlot(
                event_id=event.id, starts_at=start + datetime.timedelta(hours=2),
                ends_at=start + datetime.timedelta(hours=2, minutes=40), capacity=1,
            )
            db.session.add_all([self.slot, self.second_slot])
            db.session.commit()
            self.event_id = event.id
            self.start = start
            # Die beiden letzten Plätze hielten früher die Elternzugänge.
            self.ids = (self.student.id, self.other_student.id, self.slot.id,
                        self.second_slot.id, None, None)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()


class AppointmentServiceTests(_AppointmentFixture, unittest.TestCase):
    # --- booking rules ---

    def test_capacity_prevents_second_booking(self):
        student, other, slot, _, access, other_access = self.ids
        with self.app.app_context():
            assign_slot(slot, student)
            db.session.commit()
            with self.assertRaises(SlotUnavailable):
                assign_slot(slot, other)

    def test_capacity_allows_parallel_interviews(self):
        student, other, slot, _, access, other_access = self.ids
        with self.app.app_context():
            set_capacity(slot, 4)
            assign_slot(slot, student)
            assign_slot(slot, other)
            db.session.commit()
            self.assertEqual(db.session.query(AppointmentBooking).count(), 2)

    def test_student_cannot_book_two_slots_in_event(self):
        student, _, slot, second_slot, access, _ = self.ids
        with self.app.app_context():
            assign_slot(slot, student)
            db.session.commit()
            with self.assertRaises(StudentAlreadyBooked):
                assign_slot(second_slot, student)

    # --- planning rules ---

    def test_slots_may_overlap(self):
        # The school runs staggered interviews, so windows are allowed to share time.
        with self.app.app_context():
            created = create_slot(self.event_id, self.start + datetime.timedelta(minutes=10),
                                  self.start + datetime.timedelta(minutes=50))
            self.assertIsNotNone(created.id)

    def test_windows_of_different_length_may_share_a_start(self):
        with self.app.app_context():
            created = create_slot(self.event_id, self.start,
                                  self.start + datetime.timedelta(minutes=50))
            self.assertIsNotNone(created.id)
            self.assertEqual(created.starts_at, self.slot.starts_at)

    def test_identical_window_is_refused(self):
        with self.app.app_context():
            with self.assertRaises(BookingError):
                create_slot(self.event_id, self.start, self.start + datetime.timedelta(minutes=40))

    def test_slot_directly_after_another_is_allowed(self):
        with self.app.app_context():
            created = create_slot(self.event_id, self.start + datetime.timedelta(minutes=40),
                                  self.start + datetime.timedelta(minutes=80))
            self.assertIsNotNone(created.id)

    def test_only_the_two_interview_lengths_are_accepted(self):
        with self.app.app_context():
            for index, minutes in enumerate((40, 50)):
                offset = datetime.timedelta(hours=4 + index)
                slot = create_slot(self.event_id, self.start + offset,
                                   self.start + offset + datetime.timedelta(minutes=minutes))
                self.assertEqual((slot.ends_at - slot.starts_at).total_seconds() / 60, minutes)
            with self.assertRaises(BookingError):
                create_slot(self.event_id, self.start + datetime.timedelta(days=1),
                            self.start + datetime.timedelta(days=1, minutes=30))

    def test_slot_must_start_on_the_ten_minute_grid(self):
        with self.app.app_context():
            with self.assertRaises(BookingError):
                create_slot(self.event_id, self.start + datetime.timedelta(hours=2, minutes=5),
                            self.start + datetime.timedelta(hours=2, minutes=45))

    def test_slot_must_fall_on_one_of_the_interview_days(self):
        with self.app.app_context():
            with self.assertRaises(BookingError):
                create_slot(self.event_id, self.start + datetime.timedelta(days=30),
                            self.start + datetime.timedelta(days=30, minutes=40))

    def test_a_slot_long_before_the_booking_period_is_fine(self):
        # Der Anmeldezeitraum begrenzt die Fenster nicht: gebucht wird lange
        # vorher, gesprochen an den Gesprächstagen.
        with self.app.app_context():
            event = db.session.get(AppointmentEvent, self.event_id)
            event.booking_opens_at = self.start - datetime.timedelta(days=60)
            event.booking_closes_at = self.start - datetime.timedelta(days=30)
            db.session.commit()
            created = create_slot(self.event_id, self.start + datetime.timedelta(days=1),
                                  self.start + datetime.timedelta(days=1, minutes=40))
            self.assertIsNotNone(created.id)

    def test_bulk_generation_creates_requested_number_and_duration(self):
        with self.app.app_context():
            first = self.start + datetime.timedelta(days=1)
            created = generate_slots(self.event_id, first, 5, 40, 10)
            self.assertEqual(len(created), 5)
            self.assertEqual((created[0].ends_at - created[0].starts_at).total_seconds(), 2400)
            self.assertEqual(created[1].starts_at, first + datetime.timedelta(minutes=50))

    def test_move_slot_persists_new_time(self):
        with self.app.app_context():
            target = self.start + datetime.timedelta(days=1, hours=2)
            moved = move_slot(self.slot.id, target, target + datetime.timedelta(minutes=50))
            db.session.commit()
            self.assertEqual(moved.starts_at, target)
            self.assertEqual(moved.ends_at - moved.starts_at, datetime.timedelta(minutes=50))

    def test_move_slot_rejects_a_day_outside_the_interview_days(self):
        with self.app.app_context():
            target = self.start + datetime.timedelta(days=30)
            with self.assertRaises(BookingError):
                move_slot(self.slot.id, target, target + datetime.timedelta(minutes=40))

    def test_move_slot_may_land_on_an_overlapping_time(self):
        with self.app.app_context():
            target = self.start + datetime.timedelta(hours=2, minutes=10)  # into the second slot
            moved = move_slot(self.slot.id, target, target + datetime.timedelta(minutes=40))
            db.session.commit()
            self.assertEqual(moved.starts_at, target)

    def test_move_slot_rejects_landing_exactly_on_another(self):
        with self.app.app_context():
            target = self.start + datetime.timedelta(hours=2)  # exactly the second slot
            with self.assertRaises(BookingError):
                move_slot(self.slot.id, target, target + datetime.timedelta(minutes=40))

    # --- booked slots are locked ---

    def test_booked_slot_cannot_be_moved(self):
        student, _, slot, _, access, _ = self.ids
        with self.app.app_context():
            assign_slot(slot, student)
            db.session.commit()
            target = self.start + datetime.timedelta(days=1)
            with self.assertRaises(SlotHasBookings):
                move_slot(slot, target, target + datetime.timedelta(minutes=40))

    def test_booked_slot_cannot_be_deleted(self):
        student, _, slot, _, access, _ = self.ids
        with self.app.app_context():
            assign_slot(slot, student)
            db.session.commit()
            with self.assertRaises(SlotHasBookings):
                delete_slot(slot)

    def test_capacity_cannot_drop_below_bookings(self):
        student, other, slot, _, access, other_access = self.ids
        with self.app.app_context():
            set_capacity(slot, 2)
            assign_slot(slot, student)
            assign_slot(slot, other)
            db.session.commit()
            with self.assertRaises(SlotHasBookings):
                set_capacity(slot, 1)

    def test_slot_is_movable_again_after_staff_cancellation(self):
        student, _, slot, _, access, _ = self.ids
        with self.app.app_context():
            booking = assign_slot(slot, student)
            db.session.commit()
            cancel_booking_as_staff(booking.id)
            db.session.commit()
            target = self.start + datetime.timedelta(days=1)
            moved = move_slot(slot, target, target + datetime.timedelta(minutes=40))
            self.assertEqual(moved.starts_at, target)

    # --- staff assignment ---

    def test_staff_can_assign(self):
        student, _, slot, _, _, _ = self.ids
        with self.app.app_context():
            booking = assign_slot(slot, student)
            db.session.commit()
            self.assertEqual(booking.source, "staff")

    def test_staff_assignment_respects_capacity(self):
        student, other, slot, _, _, _ = self.ids
        with self.app.app_context():
            assign_slot(slot, student)
            db.session.commit()
            with self.assertRaises(SlotUnavailable):
                assign_slot(slot, other)

    def test_staff_assignment_works_while_event_is_draft(self):
        student, _, slot, _, _, _ = self.ids
        with self.app.app_context():
            db.session.get(AppointmentEvent, self.event_id).status = "draft"
            db.session.commit()
            booking = assign_slot(slot, student)
            db.session.commit()
            self.assertEqual(booking.status, "confirmed")

    def test_student_cannot_be_assigned_twice(self):
        student, _, slot, second_slot, _, _ = self.ids
        with self.app.app_context():
            assign_slot(slot, student)
            db.session.commit()
            with self.assertRaises(StudentAlreadyBooked):
                assign_slot(second_slot, student)

    def test_active_booking_and_free_slots_reflect_assignment(self):
        student, _, slot, _, _, _ = self.ids
        with self.app.app_context():
            self.assertIsNone(active_booking_for_student(student))
            self.assertEqual(len(assignable_slots(self.event_id)), 2)
            assign_slot(slot, student)
            db.session.commit()
            found = active_booking_for_student(student)
            self.assertIsNotNone(found)
            self.assertEqual(found[0].slot_id, slot)
            self.assertEqual([s.id for s, _ in assignable_slots(self.event_id)], [self.second_slot.id])


class CalendarExportTests(unittest.TestCase):
    """Der ICS-Export muss gültig sein und die Zeiten in UTC führen."""

    def _entry(self, summary="Anmeldung: Kind, Termin", **extra):
        start = datetime.datetime(2026, 10, 5, 12, 30)
        return dict({
            "uid": "anmeldetermin-1@sl-office",
            "starts_at": start,
            "ends_at": start + datetime.timedelta(minutes=30),
            "summary": summary,
        }, **extra)

    def test_the_file_is_a_wellformed_calendar(self):
        text = calendar.build_calendar([self._entry()]).decode("utf-8")
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(text.endswith("END:VCALENDAR\r\n"))
        self.assertIn("BEGIN:VEVENT\r\n", text)
        self.assertIn("DTSTART:20261005T123000Z\r\n", text)
        self.assertIn("DTEND:20261005T130000Z\r\n", text)
        # Jede Zeile endet auf CRLF, keine nackten Zeilenumbrüche.
        self.assertEqual(text.count("\n"), text.count("\r\n"))

    def test_special_characters_are_escaped(self):
        text = calendar.build_calendar([self._entry(
            summary="Kind, Test; mit Zeichen",
            description="Erste Zeile\nZweite Zeile")]).decode("utf-8")
        self.assertIn("SUMMARY:Kind\\, Test\\; mit Zeichen", text)
        self.assertIn("Erste Zeile\\nZweite Zeile", text)

    def test_long_lines_are_folded_without_breaking_characters(self):
        text = calendar.build_calendar([self._entry(description="Ärztlich " * 40)]).decode("utf-8")
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        # Der Umbruch darf den Inhalt nicht verändern.
        self.assertIn("Ärztlich", text.replace("\r\n ", ""))

    def test_a_timezone_aware_start_is_converted_to_utc(self):
        aware = datetime.datetime(2026, 10, 5, 14, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        text = calendar.build_calendar([self._entry(starts_at=aware)]).decode("utf-8")
        self.assertIn("DTSTART:20261005T123000Z", text)


class CalendarRouteTests(_AppointmentFixture, unittest.TestCase):
    def _login_staff(self):
        with self.app.app_context():
            user = User(username="chef", password_hash=generate_password_hash("x"),
                        role="Schulleitung")
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        return client

    def test_staff_export_covers_every_confirmed_booking(self):
        student, other, slot, second, access, other_access = self.ids
        with self.app.app_context():
            assign_slot(slot, student)
            cancelled = assign_slot(second, other)
            db.session.commit()
            cancelled_id = cancelled.id
        client = self._login_staff()
        address = f"/admin/appointments/{self.event_id}/buchungen.ics"
        response = client.get(address)
        self.assertEqual(response.mimetype, "text/calendar")
        self.assertEqual(response.get_data(as_text=True).count("BEGIN:VEVENT"), 2)

        with self.app.app_context():
            cancel_booking_as_staff(cancelled_id)
            db.session.commit()
        body = client.get(address).get_data(as_text=True)
        self.assertEqual(body.count("BEGIN:VEVENT"), 1)
        self.assertIn("Kind\\, Termin", body)  # das Komma ist in ICS maskiert

    def test_the_export_needs_a_staff_login(self):
        response = self.app.test_client().get(
            f"/admin/appointments/{self.event_id}/buchungen.ics")
        self.assertEqual(response.status_code, 302)


class SlotWithHistoryTests(_AppointmentFixture, unittest.TestCase):
    """Ein Fenster, an dem stornierte Buchungen hängen, muss weg können.

    Der Fremdschlüssel der Buchungen schützt das Fenster mit RESTRICT, ein
    echtes Löschen scheitert also an der Datenbank. Es wird darum stillgelegt --
    für Planer und Eltern ist es damit verschwunden.
    """

    def _retire(self):
        """Fenster buchen, stornieren, dann löschen -- der gemeldete Fall."""
        student, _, slot, _, access, _ = self.ids
        with self.app.app_context():
            booking = assign_slot(slot, student)
            db.session.commit()
            cancel_booking_as_staff(booking.id)
            db.session.commit()
            delete_slot(slot)
            db.session.commit()
            return booking.id

    def test_a_slot_with_a_cancelled_booking_can_be_removed(self):
        # Zuvor scheiterte das an "FOREIGN KEY constraint failed" und die
        # Oberfläche meldete nur "Die Änderung konnte nicht gespeichert werden".
        self._retire()
        with self.app.app_context():
            slot = db.session.get(AppointmentSlot, self.ids[2])
            self.assertEqual(slot.status, "cancelled")

    def test_the_retired_slot_is_gone_from_the_planner(self):
        self._retire()
        with self.app.app_context():
            event = db.session.get(AppointmentEvent, self.event_id)
            board = _planner_state(event)
            self.assertNotIn(self.ids[2], [entry["id"] for entry in board["slots"]])
            self.assertNotIn(self.ids[2], [slot.id for slot, _ in assignable_slots(self.event_id)])

    def test_the_old_booking_keeps_its_record(self):
        booking_id = self._retire()
        with self.app.app_context():
            booking = db.session.get(AppointmentBooking, booking_id)
            self.assertEqual(booking.status, "cancelled")
            self.assertEqual(booking.slot_id, self.ids[2])

    def test_the_freed_time_can_be_used_again(self):
        # Der Zeitraum ist nur unter den lebenden Fenstern einmalig.
        self._retire()
        with self.app.app_context():
            slot = db.session.get(AppointmentSlot, self.ids[2])
            created = create_slot(self.event_id, slot.starts_at, slot.ends_at)
            db.session.commit()
            self.assertNotEqual(created.id, self.ids[2])
            self.assertEqual(created.status, "available")

    def test_two_live_slots_may_still_not_share_a_period(self):
        with self.app.app_context():
            slot = db.session.get(AppointmentSlot, self.ids[2])
            with self.assertRaises(BookingError):
                create_slot(self.event_id, slot.starts_at, slot.ends_at)

    def test_a_slot_without_history_is_really_deleted(self):
        _, _, _, second_slot, _, _ = self.ids
        with self.app.app_context():
            delete_slot(second_slot)
            db.session.commit()
            self.assertIsNone(db.session.get(AppointmentSlot, second_slot))

    def test_a_slot_with_a_live_appointment_is_still_protected(self):
        student, _, slot, _, _, _ = self.ids
        with self.app.app_context():
            assign_slot(slot, student)
            db.session.commit()
            with self.assertRaises(SlotHasBookings):
                delete_slot(slot)


class StaffCancellationTests(_AppointmentFixture, unittest.TestCase):

    def test_the_school_can_still_cancel_what_it_assigned(self):
        student, _, slot, _, _, _ = self.ids
        with self.app.app_context():
            booking = assign_slot(slot, student)
            db.session.commit()
            cancel_booking_as_staff(booking.id)
            db.session.commit()
            self.assertEqual(db.session.get(AppointmentBooking, booking.id).status, "cancelled")


class AdministrationWindowTests(_AppointmentFixture, unittest.TestCase):

    def test_the_interview_days_are_stored_and_shown_separately(self):
        client = self._login_staff()
        first = self.start.date() - datetime.timedelta(days=1)
        last = self.start.date() + datetime.timedelta(days=5)
        response = client.post(f"/admin/appointments/{self.event_id}", data={
            "action": "slot_days",
            "slot_days_from": first.isoformat(),
            "slot_days_until": last.isoformat(),
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            event = db.session.get(AppointmentEvent, self.event_id)
            self.assertEqual((event.slot_days_from, event.slot_days_until), (first, last))
        body = client.get(f"/admin/appointments/{self.event_id}").get_data(as_text=True)
        self.assertIn(f'value="{first.isoformat()}"', body)

    def test_interview_days_may_not_strand_an_existing_slot(self):
        client = self._login_staff()
        later = self.start.date() + datetime.timedelta(days=3)
        response = client.post(f"/admin/appointments/{self.event_id}", data={
            "action": "slot_days",
            "slot_days_from": later.isoformat(),
            "slot_days_until": (later + datetime.timedelta(days=1)).isoformat(),
        }, follow_redirects=True)
        self.assertIn("liegen bereits Gesprächsfenster", response.get_data(as_text=True))
        with self.app.app_context():
            event = db.session.get(AppointmentEvent, self.event_id)
            self.assertNotEqual(event.slot_days_from, later)

    def _login_staff(self):
        with self.app.app_context():
            user = User(username="fenster", password_hash=generate_password_hash("x"),
                        role="Schulleitung")
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        return client


if __name__ == "__main__":
    unittest.main()
