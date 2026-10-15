"""The three ways the assistant can work: automatic, ask-me-first, and tell-me-only."""
from jobping import flow

from .helpers import C1, C2, C3, OWNER, FlowCase


class NotifyModeTests(FlowCase):
    def setUp(self):
        super().setUp()
        self.update_biz(mode="notify")

    def test_missed_call_texts_only_the_owner(self):
        self.call(C1)
        self.assertEqual(self.out(to=C1), [])
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn("Nothing was sent", self.alerts()[0]["body"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM calls WHERE status='missed'").fetchone()[0], 1)

    def test_customer_who_texts_first_gets_no_automatic_reply(self):
        self.text(C1, "my sink is clogged")
        self.assertEqual(self.out(to=C1), [])
        self.assertEqual(len(self.alerts()), 1)

    def test_emergency_still_hands_off_and_stop_still_works(self):
        self.text(C1, "water is coming through the ceiling")
        self.assertEqual(len(self.sent(to=C1, kind="handoff")), 1)
        self.text(C2, "stop")
        self.assertEqual(self.customer(C2)["opted_out"], 1)


class ApproveModeTests(FlowCase):
    def setUp(self):
        super().setUp()
        self.update_biz(mode="approve")

    def test_text_back_waits_for_one_tap(self):
        self.call(C1)
        self.assertEqual(self.out(to=C1), [])
        self.assertEqual(self.conv(C1)["state"], "awaiting_ok")
        self.assertIn("Tap to send", self.alerts()[0]["body"])
        self.assertTrue(flow.approve_text(self.db, self.biz(), self.conv(C1)["id"]))
        self.assertEqual([m["kind"] for m in self.sent(to=C1)], ["first_text"])
        self.assertEqual(self.conv(C1)["state"], "asked")

    def test_skip_sends_nothing(self):
        self.call(C1)
        self.assertTrue(flow.skip_text(self.db, self.biz(), self.conv(C1)["id"]))
        self.assertEqual(self.out(to=C1), [])
        self.assertFalse(flow.approve_text(self.db, self.biz(), self.conv(C1)["id"]))

    def test_opted_out_caller_is_never_texted_even_after_approval(self):
        self.call(C1)
        self.db.execute("UPDATE customers SET opted_out=1 WHERE phone=?", (C1,))
        self.assertFalse(flow.approve_text(self.db, self.biz(), self.conv(C1)["id"]))
        self.assertEqual(self.out(to=C1), [])

    def _to_pick(self):
        self.call(C1)
        flow.approve_text(self.db, self.biz(), self.conv(C1)["id"])
        self.text(C1, "faucet leaks, 12 Elm St")
        self.text(C1, "1")

    def test_booking_waits_for_approval_then_confirms(self):
        self._to_pick()
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.conv(C1)["state"], "awaiting_booking")
        self.assertEqual(len(self.sent(to=C1, kind="ack")), 1)
        self.assertEqual(self.sent(to=C1, kind="confirm"), [])
        self.assertIn("Approve this job", self.alerts()[-1]["body"])
        self.assertTrue(flow.approve_booking(self.db, self.biz(), self.conv(C1)["id"]))
        self.assertEqual(len(self.jobs("booked")), 1)
        self.assertEqual(len(self.sent(to=C1, kind="confirm")), 1)
        self.assertEqual(self.conv(C1)["state"], "booked")

    def test_decline_offers_new_times_and_books_nothing(self):
        self._to_pick()
        self.assertTrue(flow.decline_booking(self.db, self.biz(), self.conv(C1)["id"]))
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.conv(C1)["state"], "offered")
        self.assertEqual(len(self.sent(to=C1, kind="offer")), 2)

    def test_time_taken_before_approval_offers_new_times(self):
        self._to_pick()
        start = self.conv(C1)  # pending slot
        import json
        pending = json.loads(start["pending_json"])
        # another job takes the slot while the owner is deciding
        self.db.execute(
            "INSERT INTO customers(business_id, phone, created_at) VALUES (1,?, '2026-10-14T15:00:00Z')", (C3,)
        )
        cid = self.customer(C3)["id"]
        self.db.execute(
            "INSERT INTO conversations(business_id, customer_id, token, created_at, updated_at) VALUES (1,?,?,?,?)",
            (cid, "tok3", "2026-10-14T15:00:00Z", "2026-10-14T15:00:00Z"),
        )
        conv3 = self.conv(C3)["id"]
        self.db.execute(
            "INSERT INTO jobs(business_id, customer_id, conversation_id, starts_at, ends_at, created_at) "
            "VALUES (1,?,?,?,?,?)",
            (cid, conv3, pending["start"], pending["start"], "2026-10-14T15:00:00Z"),
        )
        self.assertTrue(flow.approve_booking(self.db, self.biz(), self.conv(C1)["id"]))
        self.assertEqual(len(self.jobs("booked")), 1)  # only the other customer's
        self.assertIn("just taken", self.sent(to=C1, kind="offer")[-1]["body"])

    def test_customer_texting_first_gets_no_automatic_reply(self):
        self.text(C2, "need a plumber")
        self.assertEqual(self.out(to=C2), [])
        self.assertEqual(len(self.alerts()), 1)

    def test_approving_twice_does_nothing_the_second_time(self):
        self._to_pick()
        cid = self.conv(C1)["id"]
        self.assertTrue(flow.approve_booking(self.db, self.biz(), cid))
        self.assertFalse(flow.approve_booking(self.db, self.biz(), cid))
        self.assertEqual(len(self.jobs("booked")), 1)


class PastCustomerTests(FlowCase):
    def test_past_customer_gets_an_alert_not_a_text(self):
        self.book(C1)
        before = len(self.sent(to=C1))
        self.update_biz(skip_known=1)
        self.at("2026-10-14T20:00:00Z")
        self.db.execute("UPDATE conversations SET last_texted_at=NULL, state='closed'")
        self.call(C1)
        self.assertEqual(len(self.sent(to=C1)), before)
        self.assertIn("past customer", self.alerts()[-1]["body"])

    def test_new_caller_is_still_texted(self):
        self.update_biz(skip_known=1)
        self.call(C2)
        self.assertEqual(len(self.sent(to=C2, kind="first_text")), 1)


class ReportTests(FlowCase):
    def test_counts_missed_texted_replied_booked(self):
        self.book(C1)
        self.call(C2)
        self.call(C2)
        r = flow.missed_report(self.db, self.biz(), 30)
        self.assertEqual((r["missed"], r["callers"], r["texted"], r["booked"]), (3, 2, 2, 1))
        self.assertEqual(r["replied"], 1)

    def test_notify_mode_still_counts_calls(self):
        self.update_biz(mode="notify")
        self.call(C1)
        self.assertEqual(flow.missed_report(self.db, self.biz(), 30)["missed"], 1)
