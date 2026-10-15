"""The owner's screens for ask-me-first and tell-me-only."""
from .helpers import C1, C2
from .test_web import WebCase, setup_form


class ModeScreenTests(WebCase):
    def test_setup_saves_the_mode_and_past_customer_choice(self):
        self.logged_in()
        self.assertEqual(self.post("/setup", setup_form(mode="approve", skip_known="1")).status_code, 302)
        row = self.q("SELECT mode, skip_known FROM businesses")[0]
        self.assertEqual((row["mode"], row["skip_known"]), ("approve", 1))
        page = self.html(self.c.get("/setup"))
        self.assertIn('value="approve" checked', page)

    def test_setup_rejects_an_unknown_mode(self):
        self.logged_in()
        r = self.post("/setup", setup_form(mode="chaos"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.q("SELECT mode FROM businesses")[0]["mode"], "auto")

    def test_old_form_without_a_mode_keeps_the_current_one(self):
        self.logged_in()
        self.post("/setup", setup_form(mode="notify"))
        self.post("/setup", setup_form())
        self.assertEqual(self.q("SELECT mode FROM businesses")[0]["mode"], "notify")

    def test_one_tap_buttons_send_the_text_and_approve_the_job(self):
        self.logged_in()
        self.post("/setup", setup_form(mode="approve"))
        self.call(C1)
        cid = self.conv_id(C1)
        page = self.html(self.c.get("/tickets/%d" % cid))
        self.assertIn("Send the text", page)
        self.assertEqual(self.post("/tickets/%d/approve-text" % cid).status_code, 302)
        self.assertEqual(len(self.q("SELECT 1 FROM messages WHERE kind='first_text' AND status='sent'")), 1)
        self.text(C1, "faucet leaks, 12 Elm St")
        self.text(C1, "1")
        page = self.html(self.c.get("/tickets/%d" % cid))
        self.assertIn("Approve this job?", page)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM jobs")[0]["n"], 0)
        self.post("/tickets/%d/approve-booking" % cid)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM jobs WHERE status='booked'")[0]["n"], 1)

    def test_pending_items_show_under_needs_you_and_on_the_board(self):
        self.logged_in()
        self.post("/setup", setup_form(mode="approve"))
        self.call(C1)
        page = self.html(self.c.get("/dashboard"))
        self.assertIn("Your OK", page)
        self.assertIn("1 customer needs you", page)

    def test_another_shops_ticket_cannot_be_approved(self):
        self.logged_in()
        self.assertEqual(self.post("/tickets/999/approve-text").status_code, 404)

    def test_report_page_needs_login_and_counts_calls(self):
        self.assertIn(self.app.test_client().get("/report").status_code, (302, 401))
        self.logged_in()
        self.call(C1)
        self.call(C2)
        page = self.html(self.c.get("/report"))
        self.assertIn("<strong>2</strong> missed calls", page)


class BusyTests(WebCase):
    def test_blocking_today_removes_todays_times_and_clearing_restores_them(self):
        self.logged_in()
        from jobping import slots as slotmod
        from jobping.clock import parse
        from jobping.db import get_db
        from .helpers import NOW

        def first_slot():
            with self.app.app_context():
                db = get_db()
                biz = db.execute("SELECT * FROM businesses").fetchone()
                return slotmod.open_slots(db, biz, parse(NOW))[0].astimezone(slotmod.tzinfo(biz)).date()

        today = first_slot()
        self.assertEqual(self.post("/busy", {"until": "today"}).status_code, 302)
        self.assertGreater(first_slot(), today)
        self.assertIn("booked up", self.html(self.c.get("/dashboard")))
        self.post("/busy", {"until": "clear"})
        self.assertEqual(first_slot(), today)

    def test_bad_choice_is_refused(self):
        self.logged_in()
        self.assertEqual(self.post("/busy", {"until": "forever"}).status_code, 400)
