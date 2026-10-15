"""The conversation: from a missed call to a booked job, and the rules the assistant never breaks."""
import sqlite3
import unittest

from jobping import flow, slots as slotmod
from jobping.clock import parse

from .helpers import C1, C2, C3, C4, LINE, NOW, OWNER, FlowCase


class MissedCallTests(FlowCase):
    def test_missed_call_sends_one_first_text_that_names_the_business_and_how_to_stop(self):
        self.call(C1)
        texts = self.sent(to=C1)
        self.assertEqual([t["kind"] for t in texts], ["first_text"])
        body = texts[0]["body"]
        self.assertIn("Test Plumbing", body)
        self.assertIn("automated assistant", body)
        self.assertIn("STOP", body)
        self.assertIn("/book/" + self.conv(C1)["token"], body)
        self.assertEqual(texts[0]["from_phone"], LINE)
        self.assertEqual(self.conv(C1)["state"], "asked")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM calls WHERE status='missed'").fetchone()[0], 1)

    def test_withheld_or_invalid_caller_is_ignored(self):
        for caller in ("anonymous", "", None, "12345", "+1 (186) 555-0100"):
            self.call(caller)
        self.assertEqual(self.out(), [])

    def test_owner_and_shop_numbers_are_never_texted(self):
        self.call(OWNER)
        self.call(LINE)
        self.assertEqual(self.out(), [])

    def test_repeat_calls_inside_two_hours_get_one_text(self):
        self.call(C1)
        self.at("2026-10-14T15:10:00Z")
        self.call(C1)
        self.at("2026-10-14T16:50:00Z")
        self.call(C1)
        self.assertEqual(len(self.sent(kind="first_text")), 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0], 3)
        self.at("2026-10-14T17:30:00Z")  # more than two hours after the first text
        self.call(C1)
        self.assertEqual(len(self.sent(kind="first_text")), 2)

    def test_same_call_reported_twice_by_the_phone_company_counts_once(self):
        self.call(C1, "CA111")
        self.call(C1, "CA111")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0], 1)
        self.assertEqual(len(self.sent(kind="first_text")), 1)

    def test_assistant_paused_logs_the_call_but_sends_nothing(self):
        self.update_biz(assistant_on=0)
        self.call(C1)
        self.assertEqual(self.out(), [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM calls WHERE status='missed'").fetchone()[0], 1)

    def test_pausing_stops_replies_mid_conversation_too(self):
        self.call(C1)
        self.update_biz(assistant_on=0)
        self.text(C1, "leaking faucet")
        self.assertEqual(self.sent(kind="offer"), [])
        self.assertTrue(any(m["status"] == "blocked:assistant_paused" for m in self.out()))

    def test_caller_who_got_through_is_not_texted(self):
        flow.on_answered_call(self.db, self.biz(), C1, "CA9")
        self.call(C1, "CA9")  # same call id: the owner picked up
        self.assertEqual(self.out(), [])


class OfferAndBookingTests(FlowCase):
    def test_reply_gets_three_open_times_without_stacking_one_day(self):
        self.call(C1)
        self.text(C1, "kitchen faucet is leaking, 12 Elm St")
        offer = self.sent(to=C1, kind="offer")[0]["body"]
        self.assertIn("1) Wed Oct 14, 1:00 PM", offer)  # two hours of notice after 11:00 AM
        self.assertIn("2) Wed Oct 14, 2:00 PM", offer)
        self.assertIn("3) Thu Oct 15, 8:00 AM", offer)
        self.assertIn("Reply 1, 2 or 3", offer)
        self.assertIn("/book/", offer)
        self.assertEqual(self.conv(C1)["state"], "offered")
        self.assertIn("kitchen faucet", self.conv(C1)["problem"])

    def test_picking_a_time_books_it_confirms_and_alerts_the_owner(self):
        job = self.book(C1, "2")
        self.assertEqual(job["starts_at"], "2026-10-14T18:00:00Z")  # 2:00 PM Detroit
        self.assertEqual(job["source"], "text")
        confirm = self.sent(to=C1, kind="confirm")[0]["body"]
        self.assertIn("Wed Oct 14, 2:00 PM", confirm)
        self.assertIn("Test Plumbing", confirm)
        self.assertIn("STOP", confirm)
        self.assertIn("CHANGE", confirm)
        self.assertNotIn("CANCEL", confirm)  # CANCEL is a carrier opt-out word
        self.assertEqual(self.conv(C1)["state"], "booked")
        alert = self.alerts()[-1]["body"]
        self.assertTrue(alert.startswith("Booked:"))
        self.assertIn("Wed Oct 14, 2:00 PM", alert)

    def test_number_replies_in_other_words_still_work(self):
        for i, phrase in enumerate(["Option 1", "#1", "1.", "number 1 please"]):
            phone = "+158655501%02d" % (20 + i)
            self.call(phone)
            self.text(phone, "leak")
            self.text(phone, phrase)
            self.assertEqual(self.conv(phone)["state"], "booked", phrase)

    def test_two_customers_cannot_book_the_same_time(self):
        for phone in (C1, C2):
            self.call(phone)
            self.text(phone, "drain is slow")
        self.text(C1, "1")
        self.text(C2, "1")  # was offered the same 1:00 PM, which is now gone
        booked = self.jobs("booked")
        self.assertEqual(len(booked), 1)
        self.assertEqual(self.conv(C2)["state"], "offered")
        retry = self.sent(to=C2, kind="offer")[-1]["body"]
        self.assertIn("just taken", retry)
        self.assertNotIn("1) Wed Oct 14, 1:00 PM", retry)
        self.assertIn("1) Wed Oct 14, 2:00 PM", retry)

    def test_database_itself_refuses_a_double_booking(self):
        job = self.book(C1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO jobs(business_id, customer_id, conversation_id, starts_at, ends_at, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (self.biz_id, job["customer_id"], job["conversation_id"], job["starts_at"], job["ends_at"], NOW),
            )

    def test_a_longer_visit_cannot_overlap_a_booked_one(self):
        self.call(C1)
        flow.book_from_web(self.db, self.biz(), self.conv(C1)["id"], "2026-10-15T13:00:00Z", "Pat", "1 Main St", "leak")
        self.update_biz(job_minutes=120)  # Thursday's two-hour visits start at 8, 10, 12 and 2
        slots = slotmod.open_slots(self.db, self.biz(), parse(NOW))
        self.assertNotIn(parse("2026-10-15T12:00:00Z"), slots)  # 8-10 AM runs into the booked 9-10 AM visit
        self.assertIn(parse("2026-10-15T14:00:00Z"), slots)  # 10 AM-12 is free
        self.assertIn(parse("2026-10-15T16:00:00Z"), slots)

    def test_book_from_web_rejects_a_taken_or_closed_time(self):
        self.call(C1)
        self.call(C2)
        c1, c2 = self.conv(C1), self.conv(C2)
        first = flow.book_from_web(self.db, self.biz(), c1["id"], "2026-10-14T17:00:00Z", "Pat", "1 Main St", "leak")
        self.assertIsNotNone(first)
        again = flow.book_from_web(self.db, self.biz(), c2["id"], "2026-10-14T17:00:00Z", "Sam", "2 Main St", "leak")
        self.assertIsNone(again)
        night = flow.book_from_web(self.db, self.biz(), c2["id"], "2026-10-14T03:00:00Z", "Sam", "2 Main St", "leak")
        self.assertIsNone(night)
        too_soon = flow.book_from_web(self.db, self.biz(), c2["id"], "2026-10-14T15:00:00Z", "Sam", "2 Main St", "leak")
        self.assertIsNone(too_soon)  # inside the two hours of notice
        self.assertEqual(len(self.jobs("booked")), 1)

    def test_out_of_range_number_gets_one_retry_then_goes_to_the_owner(self):
        self.call(C1)
        self.text(C1, "leak")
        self.text(C1, "9")
        self.assertEqual(self.conv(C1)["state"], "offered")
        self.text(C1, "9")
        self.assertEqual(self.conv(C1)["state"], "needs_owner")
        self.assertEqual(self.conv(C1)["reason"], "stuck")
        self.assertEqual(self.jobs(), [])

    def test_chatter_instead_of_a_number_goes_to_the_owner_on_the_second_try(self):
        self.call(C1)
        self.text(C1, "leak")
        self.text(C1, "my address is 12 Elm")
        self.assertEqual(self.conv(C1)["state"], "offered")
        self.text(C1, "hello??")
        self.assertEqual(self.conv(C1)["state"], "needs_owner")
        self.assertTrue(self.alerts()[-1]["body"].startswith("Needs you:"))

    def test_no_open_times_goes_to_the_owner(self):
        self.update_biz(hours_json='{"mon":[],"tue":[],"wed":[],"thu":[],"fri":[],"sat":[],"sun":[]}')
        self.call(C1)
        self.text(C1, "leak")
        self.assertEqual(self.conv(C1)["reason"], "no_slots")
        self.assertEqual(self.sent(kind="offer"), [])
        self.assertIn("No open times", self.alerts()[-1]["body"])

    def test_inbound_text_delivered_twice_is_handled_once(self):
        self.call(C1)
        self.text(C1, "leak", provider_id="SM1")
        self.text(C1, "leak", provider_id="SM1")
        self.assertEqual(len(self.sent(kind="offer")), 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages WHERE direction='in'").fetchone()[0], 1)

    def test_help_gets_the_required_reply(self):
        self.call(C1)
        self.text(C1, "HELP")
        reply = self.sent(to=C1, kind="help")[0]["body"]
        self.assertIn("STOP", reply)
        self.assertIn("Test Plumbing", reply)

    def test_booked_customer_texting_something_else_reaches_the_owner(self):
        self.book(C1)
        self.text(C1, "can you bring a ladder?")
        self.assertIn("ladder", self.alerts()[-1]["body"])
        self.assertEqual(self.conv(C1)["state"], "booked")

    def test_change_request_hands_off_to_the_owner(self):
        self.book(C1)
        self.text(C1, "CHANGE")
        self.assertEqual(self.conv(C1)["state"], "needs_owner")
        self.assertEqual(self.conv(C1)["reason"], "owner_request")
        self.assertEqual(len(self.jobs("booked")), 1)  # the owner decides what happens to the time


class RulesTheAssistantNeverBreaksTests(FlowCase):
    def test_never_quotes_a_price_and_tells_the_owner(self):
        self.call(C1)
        self.text(C1, "How much to replace a water heater? Do you charge for estimates?")
        everything = " ".join(m["body"] for m in self.out(to=C1))
        self.assertNotIn("$", everything)
        self.assertIn("can't quote", everything)
        self.assertTrue(any(a["body"].startswith("Price question") for a in self.alerts()))
        self.assertEqual(self.conv(C1)["state"], "offered")  # still offers to book a visit

    def test_no_outgoing_text_to_a_customer_contains_a_dollar_sign(self):
        self.call(C1)
        self.text(C1, "what is your rate, price, quote, estimate, fee, cost, $$$")
        self.text(C1, "1")
        for m in self.out(to=C1):
            self.assertNotIn("$", m["body"])

    def test_only_offers_times_the_owner_has_open(self):
        self.call(C1)
        self.text(C1, "leak")
        offer = self.sent(to=C1, kind="offer")[0]["body"]
        tz = slotmod.tzinfo(self.biz())
        for slot in slotmod.pick_offers(slotmod.open_slots(self.db, self.biz(), parse(NOW)), tz):
            self.assertIn(slotmod.fmt_slot(slot, tz), offer)
        for line in offer.splitlines():
            self.assertNotRegex(line, r"(Sat|Sun) ")  # closed on weekends by default

    def test_emergency_words_stop_the_assistant_and_alert_the_owner_at_once(self):
        for phone, words in [
            (C1, "my basement is flooding"),
            (C2, "I smell gas in the kitchen"),
            (C3, "sewage backing up into the tub"),
            (C4, "pipe burst and water is gushing"),
        ]:
            self.call(phone)
            self.text(phone, words)
            self.assertEqual(self.conv(phone)["state"], "needs_owner", words)
            self.assertEqual(self.conv(phone)["reason"], "emergency", words)
            self.assertEqual(self.sent(to=phone, kind="offer"), [], words)
            self.assertIn("911", self.sent(to=phone, kind="handoff")[0]["body"])
        self.assertTrue(all(a["body"].startswith("URGENT") for a in self.alerts()))
        self.assertEqual(len(self.alerts()), 4)

    def test_after_an_emergency_the_assistant_stays_quiet_and_only_alerts(self):
        self.call(C1)
        self.text(C1, "basement flooding")
        before = len(self.sent(to=C1))
        self.text(C1, "hello? anyone?")
        self.text(C1, "please hurry")
        self.assertEqual(len(self.sent(to=C1)), before)  # nothing more goes to the customer
        self.assertEqual(self.conv(C1)["state"], "needs_owner")
        self.assertIn("hurry", self.alerts()[-1]["body"])

    def test_emergency_words_in_a_booked_conversation_alert_the_owner(self):
        self.book(C1)
        self.text(C1, "it's flooding now, come sooner")
        self.assertEqual(self.conv(C1)["state"], "needs_owner")
        self.assertTrue(self.alerts()[-1]["body"].startswith("URGENT"))

    def test_emergency_in_the_booking_description_flags_the_alert(self):
        self.call(C1)
        c1 = self.conv(C1)
        flow.book_from_web(self.db, self.biz(), c1["id"], "2026-10-14T17:00:00Z", "Pat", "1 Main", "pipe burst in wall")
        self.assertTrue(self.alerts()[-1]["body"].startswith("URGENT?"))

    def test_stop_in_any_form_ends_all_texts(self):
        for phone, word in [(C1, "STOP"), (C2, "stop."), (C3, "Unsubscribe"), (C4, " Stop ")]:
            self.call(phone)
            self.text(phone, word)
            self.assertEqual(self.customer(phone)["opted_out"], 1, word)
            self.assertEqual(self.conv(phone)["state"], "closed", word)
        count = len(self.out())
        self.at("2026-10-14T20:00:00Z")  # past the repeat-call window
        for phone in (C1, C2, C3, C4):
            self.call(phone)
            self.text(phone, "actually I need a plumber")
        self.assertEqual(len(self.out()), count)  # nothing new was sent to any of them

    def test_stop_blocks_even_the_owners_manual_texts(self):
        self.call(C1)
        self.text(C1, "STOP")
        flow.send_manual(self.db, self.biz(), self.conv(C1)["id"], "Hi it's the plumber")
        last = self.out(to=C1)[-1]
        self.assertEqual(last["status"], "blocked:opted_out")

    def test_start_turns_texting_back_on(self):
        self.call(C1)
        self.text(C1, "STOP")
        self.text(C1, "START")
        self.assertEqual(self.customer(C1)["opted_out"], 0)

    def test_stop_with_a_booked_job_warns_the_owner_to_call(self):
        self.book(C1)
        self.text(C1, "STOP")
        self.assertIn("opted out", self.alerts()[-1]["body"])
        self.assertIn("Call them", self.alerts()[-1]["body"])
        self.assertEqual(self.conv(C1)["state"], "booked")  # the job still stands

    def test_stop_never_triggers_a_reply_to_the_customer(self):
        self.call(C1)
        before = len(self.out(to=C1))
        self.text(C1, "STOP")
        self.assertEqual(len(self.out(to=C1)), before)


class OwnerActionTests(FlowCase):
    def test_taking_over_silences_the_assistant_but_alerts_the_owner(self):
        self.call(C1)
        self.text(C1, "leak")
        flow.take_over(self.db, self.biz(), self.conv(C1)["id"])
        before = len(self.sent(to=C1))
        self.text(C1, "when can you come?")
        self.assertEqual(len(self.sent(to=C1)), before)
        self.assertIn("when can you come", self.alerts()[-1]["body"])

    def test_texting_a_customer_yourself_takes_over(self):
        self.call(C1)
        flow.send_manual(self.db, self.biz(), self.conv(C1)["id"], "On my way")
        self.assertEqual(self.conv(C1)["state"], "owner_handling")
        self.assertEqual(self.sent(to=C1)[-1]["body"], "On my way")

    def test_handing_back_lets_the_assistant_continue(self):
        self.call(C1)
        self.text(C1, "basement flooding")
        flow.resolve(self.db, self.biz(), self.conv(C1)["id"])
        self.assertEqual(self.conv(C1)["state"], "closed")
        self.text(C1, "faucet drips")
        self.assertEqual(self.conv(C1)["state"], "offered")

    def test_cancelling_frees_the_time_and_can_tell_the_customer(self):
        job = self.book(C1)
        flow.cancel_job(self.db, self.biz(), job["id"], notify=True)
        self.assertEqual(self.jobs("booked"), [])
        self.assertIn(parse(job["starts_at"]), slotmod.open_slots(self.db, self.biz(), parse(NOW)))
        self.assertIn("cancelled", self.sent(to=C1)[-1]["body"])
        self.assertFalse(flow.cancel_job(self.db, self.biz(), job["id"], notify=False))  # already cancelled

    def test_one_business_cannot_touch_another_businesss_customer(self):
        from jobping.auth import create_business

        other_id = create_business(self.db, "Other Plumbing", "other@example.com", "another-long-pass", "+15865550150", "+15865550198")
        other = self.db.execute("SELECT * FROM businesses WHERE id=?", (other_id,)).fetchone()
        self.call(C1)
        conv_id = self.conv(C1)["id"]
        for action in (
            lambda: flow.take_over(self.db, other, conv_id),
            lambda: flow.resolve(self.db, other, conv_id),
            lambda: flow.send_manual(self.db, other, conv_id, "hi"),
            lambda: flow.book_from_web(self.db, other, conv_id, "2026-10-14T17:00:00Z", "x", "1 Main St", ""),
        ):
            with self.assertRaises(LookupError):
                action()
        job = self.book(C2)
        self.assertFalse(flow.mark_done(self.db, other, job["id"]))
        self.assertFalse(flow.cancel_job(self.db, other, job["id"], notify=False))
        self.assertEqual(self.conv(C1)["state"], "asked")


class ReminderTests(FlowCase):
    def booked_thursday_8am(self):
        return self.book(C1, "3")

    def test_reminder_goes_out_at_5pm_the_day_before_and_only_once(self):
        job = self.booked_thursday_8am()
        self.assertEqual(job["starts_at"], "2026-10-15T12:00:00Z")
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="reminder"), [])
        self.at("2026-10-14T20:59:00Z")  # 4:59 PM Wednesday
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="reminder"), [])
        self.at("2026-10-14T21:00:00Z")  # 5:00 PM Wednesday
        flow.run_due(self.db)
        reminder = self.sent(to=C1, kind="reminder")
        self.assertEqual(len(reminder), 1)
        self.assertIn("tomorrow at 8:00 AM", reminder[0]["body"])
        self.assertIn("Test Plumbing", reminder[0]["body"])
        self.assertIn("STOP", reminder[0]["body"])
        self.at("2026-10-14T21:30:00Z")
        flow.run_due(self.db)
        flow.run_due(self.db)
        self.assertEqual(len(self.sent(kind="reminder")), 1)

    def test_job_booked_after_the_reminder_time_gets_no_duplicate_reminder(self):
        self.at("2026-10-14T22:00:00Z")  # 6:00 PM Wednesday
        self.book(C1, "1")
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="reminder"), [])
        self.assertIsNotNone(self.jobs("booked")[0]["reminder_sent_at"])

    def test_reminders_wait_for_morning_instead_of_texting_at_night(self):
        self.call(C1)
        flow.book_from_web(self.db, self.biz(), self.conv(C1)["id"], "2026-10-15T13:00:00Z", "Pat", "1 Main St", "leak")
        self.at("2026-10-15T02:00:00Z")  # 10:00 PM Wednesday: reminder is overdue but it's night
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="reminder"), [])
        self.at("2026-10-15T12:00:00Z")  # 8:00 AM Thursday
        flow.run_due(self.db)
        self.assertIn("today at 9:00 AM", self.sent(kind="reminder")[0]["body"])

    def test_no_reminder_for_a_cancelled_job_or_an_opted_out_customer(self):
        job = self.booked_thursday_8am()
        flow.cancel_job(self.db, self.biz(), job["id"], notify=False)
        self.book(C2, "3")
        self.text(C2, "STOP")
        self.at("2026-10-14T21:00:00Z")
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="reminder"), [])
        self.assertTrue(any(m["status"] == "blocked:opted_out" and m["kind"] == "reminder" for m in self.out(to=C2)))

    def test_no_reminder_when_the_assistant_is_paused(self):
        self.booked_thursday_8am()
        self.update_biz(assistant_on=0)
        self.at("2026-10-14T21:00:00Z")
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="reminder"), [])


class ReviewRequestTests(FlowCase):
    def done_job(self, phone=C1, when="2026-10-15T20:00:00Z"):
        job = self.book(phone, "3")
        self.at(when)
        flow.mark_done(self.db, self.biz(), job["id"])
        return job

    def test_review_request_goes_out_two_hours_after_done_and_only_once(self):
        self.update_biz(review_url="https://g.page/r/test-shop/review")
        self.done_job()
        self.at("2026-10-15T21:59:00Z")
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="review"), [])
        self.at("2026-10-15T22:00:00Z")  # 6:00 PM local
        flow.run_due(self.db)
        review = self.sent(to=C1, kind="review")
        self.assertEqual(len(review), 1)
        self.assertIn("https://g.page/r/test-shop/review", review[0]["body"])
        self.assertIn("STOP", review[0]["body"])
        flow.run_due(self.db)
        self.assertEqual(len(self.sent(kind="review")), 1)

    def test_no_review_request_without_a_review_link(self):
        self.done_job()
        self.at("2026-10-15T23:00:00Z")
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="review"), [])

    def test_review_request_waits_for_daytime(self):
        self.update_biz(review_url="https://g.page/r/test-shop/review")
        self.done_job(when="2026-10-15T23:00:00Z")  # 7 PM local, so two hours later is 9 PM
        self.at("2026-10-16T01:00:00Z")
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="review"), [])
        self.at("2026-10-16T13:00:00Z")  # 9 AM next morning
        flow.run_due(self.db)
        self.assertEqual(len(self.sent(kind="review")), 1)

    def test_stale_jobs_are_not_asked_for_a_review(self):
        self.update_biz(review_url="https://g.page/r/test-shop/review")
        self.done_job()
        self.at("2026-10-20T15:00:00Z")  # five days later, for example after the app was off
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="review"), [])

    def test_no_review_request_for_a_customer_who_opted_out(self):
        self.update_biz(review_url="https://g.page/r/test-shop/review")
        self.done_job()
        self.text(C1, "STOP")
        self.at("2026-10-15T22:30:00Z")
        flow.run_due(self.db)
        self.assertEqual(self.sent(kind="review"), [])


class SafetyCapTests(FlowCase):
    def test_per_number_daily_cap(self):
        self.app.config["MAX_TEXTS_PER_NUMBER_PER_DAY"] = 2
        self.call(C1)
        self.text(C1, "leak")
        self.text(C1, "1")  # the confirmation would be the third text today
        self.assertEqual(len(self.sent(to=C1)), 2)
        self.assertEqual(self.out(to=C1)[-1]["status"], "blocked:number_cap")
        self.assertEqual(len(self.jobs("booked")), 1)  # the booking itself still happened
        self.assertTrue(self.alerts())  # and the owner still heard about it

    def test_business_wide_automatic_text_cap(self):
        self.app.config["MAX_AUTO_TEXTS_PER_BUSINESS_PER_DAY"] = 2
        for phone in (C1, C2, C3):
            self.call(phone)
        self.assertEqual(len(self.sent(kind="first_text")), 2)
        self.assertEqual(self.out(to=C3)[-1]["status"], "blocked:business_cap")

    def test_first_text_cap(self):
        self.app.config["MAX_FIRST_TEXTS_PER_BUSINESS_PER_DAY"] = 1
        self.call(C1)
        self.call(C2)
        self.assertEqual(len(self.sent(kind="first_text")), 1)
        self.assertEqual(self.out(to=C2)[-1]["status"], "blocked:first_text_cap")

    def test_blocked_texts_do_not_use_up_the_cap(self):
        self.app.config["MAX_FIRST_TEXTS_PER_BUSINESS_PER_DAY"] = 1
        self.call(C1)
        for phone in (C2, C3, C4):
            self.call(phone)
        self.assertEqual(len(self.sent(kind="first_text")), 1)
        self.at("2026-10-15T16:00:00Z")  # a day later the allowance is back
        self.call(C2)
        self.assertEqual(len(self.sent(kind="first_text")), 2)

    def test_owner_alert_cap(self):
        self.app.config["MAX_OWNER_ALERTS_PER_BUSINESS_PER_DAY"] = 1
        for phone in (C1, C2):
            self.call(phone)
            self.text(phone, "basement flooding")
        self.assertEqual(len(self.alerts()), 1)
        self.assertTrue(any(m["status"] == "blocked:alert_cap" for m in self.out(to=OWNER)))

    def test_master_switch_stops_everything(self):
        self.app.config["SMS_ENABLED"] = False
        self.call(C1)
        self.text(C1, "basement flooding")
        self.assertEqual(self.sent(), [])
        self.assertTrue(all(m["status"] == "blocked:disabled" for m in self.out()))


class SlotTests(FlowCase):
    def test_slots_follow_the_owners_hours_and_time_zone(self):
        tz = slotmod.tzinfo(self.biz())
        found = slotmod.open_slots(self.db, self.biz(), parse(NOW))
        for slot in found:
            local = slot.astimezone(tz)
            self.assertLess(local.weekday(), 5)
            self.assertTrue(8 <= local.hour <= 16)
        self.assertEqual(found[0], parse("2026-10-14T17:00:00Z"))

    def test_clocks_going_back_do_not_shift_the_local_time(self):
        # US daylight time ended on Sunday Nov 1, 2026. 8 AM Monday is 13:00 UTC, not 12:00.
        found = slotmod.open_slots(self.db, self.biz(), parse("2026-11-01T12:00:00Z"))
        self.assertEqual(found[0], parse("2026-11-02T13:00:00Z"))
        self.assertEqual(slotmod.fmt_slot(found[0], slotmod.tzinfo(self.biz())), "Mon Nov 2, 8:00 AM")

    def test_lead_time_hides_times_that_are_too_soon(self):
        self.update_biz(lead_hours=5)
        first = slotmod.open_slots(self.db, self.biz(), parse(NOW))[0]
        self.assertEqual(first, parse("2026-10-14T20:00:00Z"))  # 4:00 PM, the first start at least 5 hours out

    def test_booking_window_limits_how_far_ahead(self):
        self.update_biz(horizon_days=2)
        last = slotmod.open_slots(self.db, self.biz(), parse(NOW))[-1]
        self.assertLessEqual(last, parse("2026-10-16T21:00:00Z"))


if __name__ == "__main__":
    unittest.main()


class RegionAndEncodingTests(FlowCase):
    def test_no_texts_to_numbers_outside_the_allowed_region(self):
        self.call("+442079460958")
        self.call("+521555123456")
        self.assertEqual(self.sent(), [])
        self.assertTrue(all(m["status"] == "blocked:region" for m in self.out()))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0], 2)  # the calls are still logged
        self.call(C1)
        self.assertEqual(len(self.sent(kind="first_text")), 1)

    def test_alerts_quote_customers_in_plain_characters(self):
        self.call(C1)
        self.text(C1, "basement’s flooding \U0001F4A7 — help…")
        alert = self.alerts()[-1]["body"]
        self.assertTrue(alert.isascii(), alert)
        self.assertIn("basement's flooding - help...", alert)

    def test_every_text_the_assistant_writes_is_plain_ascii_so_it_costs_one_encoding(self):
        job = self.book(C1)
        flow.mark_done(self.db, self.biz(), job["id"])
        self.update_biz(review_url="https://g.page/r/test-shop/review")
        self.text(C1, "STOP")
        self.call(C2)
        self.text(C2, "How much for a new water heater?")
        self.text(C2, "HELP")
        self.text(C2, "CHANGE")
        for m in self.out():
            self.assertTrue(m["body"].isascii(), m["body"])
