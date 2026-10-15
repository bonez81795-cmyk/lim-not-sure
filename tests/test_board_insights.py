"""The board and ticket extras: repeat customers, call counts, text-back speed, addresses."""
from .helpers import C1, C2
from .test_web import WebCase


class BoardInsightTests(WebCase):
    def book_and_finish(self, phone):
        self.call(phone)
        self.text(phone, "kitchen faucet is leaking, 12 Elm St")
        self.text(phone, "1")
        cid = self.conv_id(phone)
        job_id = self.q("SELECT id FROM jobs WHERE conversation_id=?", (cid,))[0]["id"]
        self.post("/tickets/%d/jobs/%d/done" % (cid, job_id))
        return cid

    def test_a_customer_with_a_finished_job_gets_a_repeat_badge_and_a_new_one_does_not(self):
        self.logged_in()
        self.book_and_finish(C1)
        self.call(C2)
        board = self.html(self.c.get("/dashboard?show=all"))
        self.assertEqual(board.count("Repeat customer"), 1)
        ticket = self.html(self.c.get("/tickets/%d" % self.conv_id(C1)))
        self.assertIn("Repeat customer", ticket)
        self.assertIn("finished 1 job for them before", ticket)
        self.assertNotIn("Repeat customer", self.html(self.c.get("/tickets/%d" % self.conv_id(C2))))

    def test_a_booked_job_alone_is_not_a_repeat_customer(self):
        self.logged_in()
        self.call(C1)
        self.text(C1, "faucet leaks, 12 Elm St")
        self.text(C1, "1")
        self.assertNotIn("Repeat customer", self.html(self.c.get("/dashboard?show=all")))

    def test_board_shows_how_often_they_called_and_where_a_booked_job_is(self):
        self.logged_in()
        self.call(C1)
        self.call(C1)
        self.assertIn("Called 2 times", self.html(self.c.get("/dashboard?show=all")))
        self.call(C2)
        self.assertEqual(self.html(self.c.get("/dashboard?show=all")).count("Called 1 times"), 0)
        self.text(C2, "toilet is running")
        self.text(C2, "1")
        # Only the booking page saves a separate address; a text booking keeps it in the message.
        self.assertNotIn(" at 9 Oak Ave", self.html(self.c.get("/dashboard?show=booked")))
        with self.app.app_context():
            from jobping.db import get_db

            get_db().execute("UPDATE jobs SET address='9 Oak Ave'")
        self.assertIn(" at 9 Oak Ave", self.html(self.c.get("/dashboard?show=booked")))

    def test_ticket_counts_calls_and_how_many_were_missed(self):
        self.logged_in()
        self.call(C1)
        self.call(C1)
        page = self.html(self.c.get("/tickets/%d" % self.conv_id(C1)))
        self.assertIn("2 calls in all, 2 missed", page)

    def test_text_back_speed_shows_on_the_ticket_and_as_a_weekly_average(self):
        self.logged_in()
        self.call(C1)
        self.call(C2)
        with self.app.app_context():
            from jobping.db import get_db

            db = get_db()
            db.execute("UPDATE messages SET created_at='2026-10-14T15:00:40Z' WHERE kind='first_text' AND to_phone=?", (C1,))
            db.execute("UPDATE messages SET created_at='2026-10-14T15:00:20Z' WHERE kind='first_text' AND to_phone=?", (C2,))
        ticket = self.html(self.c.get("/tickets/%d" % self.conv_id(C1)))
        self.assertIn("Texted back 40 seconds after the missed call", ticket)
        self.assertIn("Average text-back time: 30 seconds", self.html(self.c.get("/dashboard?show=all")))

    def test_booked_after_text_is_measured_from_the_text_back(self):
        self.logged_in()
        self.call(C1)
        self.text(C1, "faucet leaks, 12 Elm St")
        self.text(C1, "1")
        with self.app.app_context():
            from jobping.db import get_db

            db = get_db()
            db.execute("UPDATE messages SET created_at='2026-10-14T15:00:10Z' WHERE kind='first_text'")
            db.execute("UPDATE jobs SET created_at='2026-10-14T15:12:10Z'")
        page = self.html(self.c.get("/tickets/%d" % self.conv_id(C1)))
        self.assertIn("Booked 12 minutes after that text", page)

    def test_an_instant_reply_reads_naturally(self):
        self.logged_in()
        self.call(C1)
        self.assertIn("Average text-back time: a few seconds", self.html(self.c.get("/dashboard?show=all")))

    def test_no_speed_line_when_nothing_was_texted(self):
        self.logged_in()
        self.post("/setup", __import__("tests.test_web", fromlist=["setup_form"]).setup_form(mode="notify"))
        self.call(C1)
        page = self.html(self.c.get("/tickets/%d" % self.conv_id(C1)))
        self.assertNotIn("Texted back", page)
        self.assertNotIn("Average text-back time", self.html(self.c.get("/dashboard?show=all")))

    def test_opted_out_customers_get_a_call_instead_warning_on_the_board(self):
        self.logged_in()
        self.call(C1)
        self.text(C1, "STOP")
        self.assertIn("Opted out of texts. Call instead.", self.html(self.c.get("/dashboard?show=all")))
