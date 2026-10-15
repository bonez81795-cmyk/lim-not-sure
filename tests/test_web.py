"""The website and the phone-company webhooks: logins, forms, security, the booking page, the simulator."""
import base64
import hashlib
import hmac
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from jobping import flow
from jobping.auth import create_business
from jobping.db import get_db

from .helpers import C1, C2, C3, EMAIL, LINE, NOW, OWNER, PASSWORD, AppBase

TEMPLATES = Path(__file__).resolve().parent.parent / "jobping" / "templates"


class WebCase(AppBase):
    def setUp(self):
        super().setUp()
        self.app = self.make_app()
        self.c = self.app.test_client()
        with self.app.app_context():
            create_business(get_db(), "Test Plumbing", EMAIL, PASSWORD, OWNER, LINE)

    # --- helpers
    def q(self, sql, args=()):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, args).fetchall()]

    def act(self, fn, biz_id=1):
        with self.app.app_context():
            db = get_db()
            biz = db.execute("SELECT * FROM businesses WHERE id=?", (biz_id,)).fetchone()
            return fn(db, biz)

    def call(self, phone):
        self.act(lambda db, biz: flow.on_missed_call(db, biz, phone))

    def text(self, phone, body):
        self.act(lambda db, biz: flow.on_inbound_sms(db, biz, phone, body))

    def token_of(self, phone):
        return self.q("SELECT c.token FROM conversations c JOIN customers u ON u.id=c.customer_id WHERE u.phone=?", (phone,))[0]["token"]

    def conv_id(self, phone):
        return self.q("SELECT c.id FROM conversations c JOIN customers u ON u.id=c.customer_id WHERE u.phone=?", (phone,))[0]["id"]

    def sent(self, kind=None, to=None):
        sql, args = "SELECT * FROM messages WHERE direction='out' AND status='sent'", []
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if to:
            sql += " AND to_phone=?"
            args.append(to)
        return self.q(sql, args)

    def csrf(self, client=None):
        client = client or self.c
        with client.session_transaction() as s:
            s["csrf"] = "known-test-token"
        return "known-test-token"

    def post(self, path, data=None, client=None, **kw):
        client = client or self.c
        data = dict(data or {})
        data.setdefault("csrf", self.csrf(client))
        return client.post(path, data=data, **kw)

    def test_messages_page_shows_every_text(self):
        self.login()
        r = self.c.get("/messages")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        for needle in ("automated assistant", "Reply STOP to opt out", "Booked with", "Reminder from", "This sounds urgent"):
            self.assertIn(needle, body)

    def test_messages_page_needs_login(self):
        self.assertIn(self.app.test_client().get("/messages").status_code, (302, 401))

    def login(self, client=None, email=EMAIL, password=PASSWORD):
        return self.post("/login", {"email": email, "password": password}, client)

    def logged_in(self):
        self.assertEqual(self.login().status_code, 302)
        return self.c

    def html(self, response):
        return response.get_data(as_text=True)


class AuthTests(WebCase):
    def test_pages_that_need_a_login_redirect_to_the_login_page(self):
        for path in ("/dashboard", "/setup", "/tickets/1", "/sim"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/login", r.headers["Location"], path)

    def test_post_routes_also_need_a_login(self):
        self.call(C1)
        cid = self.conv_id(C1)
        for path in ("/setup", "/setup/test-alert", "/tickets/%d/take-over" % cid, "/sim/missed-call"):
            r = self.post(path, {})
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/login", r.headers["Location"], path)

    def test_wrong_password_and_unknown_email_look_the_same(self):
        a = self.login(password="wrong-password-1")
        b = self.login(email="nobody@example.com")
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        for response in (a, b):
            self.assertRegex(self.html(response), r"That email and password don(&#39;|')t match")

    def test_login_then_logout(self):
        r = self.login()
        self.assertEqual(r.headers["Location"], "/dashboard")
        self.assertEqual(self.c.get("/dashboard").status_code, 200)
        self.assertEqual(self.post("/logout").status_code, 302)
        self.assertEqual(self.c.get("/dashboard").status_code, 302)

    def test_session_cookie_is_httponly_and_samesite(self):
        cookie = self.login().headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_login_will_not_redirect_to_another_site(self):
        for evil in ("https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)"):
            c = self.app.test_client()
            r = self.post("/login?next=" + quote(evil), {"email": EMAIL, "password": PASSWORD}, c)
            self.assertEqual(r.headers["Location"], "/dashboard", evil)
        c = self.app.test_client()
        r = self.post("/login?next=" + quote("/setup"), {"email": EMAIL, "password": PASSWORD}, c)
        self.assertEqual(r.headers["Location"], "/setup")

    def test_repeated_wrong_passwords_get_locked_out(self):
        for _ in range(8):
            self.assertEqual(self.login(password="wrong-password-1").status_code, 401)
        self.assertEqual(self.login(password="wrong-password-1").status_code, 429)
        self.assertEqual(self.login().status_code, 429)  # even the right password waits

    def test_signup_creates_an_account_and_signs_in(self):
        c = self.app.test_client()
        r = self.post(
            "/signup",
            {"name": "Rivera Rooter", "email": "Rivera@Example.com", "cell": "(586) 555-0177", "password": "a-long-password-1"},
            c,
        )
        self.assertEqual(r.status_code, 302)
        row = self.q("SELECT * FROM businesses WHERE owner_email='rivera@example.com'")[0]
        self.assertEqual(row["owner_cell"], "+15865550177")
        self.assertTrue(row["text_number"].startswith("+158655501"))  # made-up simulator number
        self.assertNotEqual(row["password_hash"], "a-long-password-1")
        self.assertEqual(c.get("/dashboard").status_code, 200)

    def test_signup_rejects_bad_input_and_duplicate_email(self):
        good = {"name": "Rivera Rooter", "email": "rivera@example.com", "cell": "586 555 0177", "password": "a-long-password-1"}
        for change, message in [
            ({"password": "short"}, "at least 10"),
            ({"email": "not-an-email"}, "valid email"),
            ({"cell": "12"}, "cell phone"),
            ({"name": "x"}, "business name"),
            ({"email": EMAIL}, "already exists"),
        ]:
            c = self.app.test_client()
            r = self.post("/signup", {**good, **change}, c)
            self.assertEqual(r.status_code, 400, change)
            self.assertIn(message, self.html(r), change)

    def test_signup_can_be_closed(self):
        app = self.make_app(ALLOW_SIGNUP=False)
        self.assertEqual(app.test_client().get("/signup").status_code, 403)


class SecurityTests(WebCase):
    def test_forms_without_the_csrf_token_are_refused(self):
        self.assertEqual(self.c.post("/login", data={"email": EMAIL, "password": PASSWORD}).status_code, 400)
        self.logged_in()
        self.call(C1)
        for path in ("/setup", "/setup/test-alert", "/setup/new-calendar-link", "/tickets/1/take-over", "/logout", "/sim/missed-call"):
            r = self.c.post(path, data={})
            self.assertEqual(r.status_code, 400, path)
        r = self.c.post("/setup/new-calendar-link", data={"csrf": "wrong"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.q("SELECT id FROM businesses")), 1)

    def test_every_page_sends_security_headers(self):
        self.call(C1)
        paths = ["/login", "/signup", "/book/" + self.token_of(C1)]
        for path in paths:
            r = self.c.get(path)
            csp = r.headers["Content-Security-Policy"]
            self.assertIn("script-src 'self'", csp, path)
            self.assertIn("frame-ancestors 'none'", csp, path)
            self.assertNotIn("unsafe-inline", csp, path)
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff", path)
            self.assertEqual(r.headers["Referrer-Policy"], "no-referrer", path)
            self.assertIn("no-store", r.headers["Cache-Control"], path)
        self.assertIn("noindex", self.c.get(paths[2]).headers["X-Robots-Tag"])

    def test_templates_have_no_inline_scripts_styles_or_event_handlers(self):
        for path in TEMPLATES.glob("*.html"):
            text = path.read_text()
            self.assertNotRegex(text, r"<script(?![^>]*\bsrc=)", path.name)
            self.assertNotRegex(text, r"\sstyle\s*=", path.name)
            self.assertNotRegex(text, r"\son[a-z]+\s*=", path.name)
            self.assertNotRegex(text, r"javascript:", path.name)

    def test_text_a_customer_sends_cannot_inject_html_into_the_owners_pages(self):
        payload = '<script>alert(1)</script><img src=x onerror=alert(2)>'
        self.call(C1)
        self.text(C1, payload)
        self.logged_in()
        for path in ("/dashboard?show=all", "/tickets/%d" % self.conv_id(C1)):
            body = self.html(self.c.get(path))
            self.assertNotIn("<script>alert(1)", body, path)
            self.assertNotIn("<img src=x", body, path)
            self.assertIn("&lt;script&gt;", body, path)

    def test_name_typed_on_the_booking_page_is_escaped_on_every_page(self):
        self.call(C1)
        token = self.token_of(C1)
        r = self.c.post(
            "/book/" + token,
            data={"slot": "2026-10-14T17:00:00Z", "name": "<b>Mallory</b>", "address": "1 Main St <i>", "problem": "<u>leak</u>"},
        )
        self.assertEqual(r.status_code, 303)
        self.assertNotIn("<b>Mallory", self.html(self.c.get("/book/%s/done" % token)))
        self.logged_in()
        body = self.html(self.c.get("/tickets/%d" % self.conv_id(C1)))
        self.assertNotIn("<b>Mallory", body)
        self.assertIn("&lt;b&gt;Mallory", body)

    def test_one_business_cannot_see_or_change_anothers_customers(self):
        self.call(C1)
        cid = self.conv_id(C1)
        with self.app.app_context():
            create_business(get_db(), "Other Plumbing", "other@example.com", "another-long-pass", "+15865550150", "+15865550198")
        other = self.app.test_client()
        self.assertEqual(self.login(client=other, email="other@example.com", password="another-long-pass").status_code, 302)
        self.assertEqual(other.get("/tickets/%d" % cid).status_code, 404)
        for action in ("take-over", "resolve", "send"):
            r = self.post("/tickets/%d/%s" % (cid, action), {"body": "hi"}, other)
            self.assertEqual(r.status_code, 404, action)
        self.assertNotIn("(586) 555-0111", self.html(other.get("/dashboard?show=all")))
        self.assertEqual(self.q("SELECT state FROM conversations WHERE id=?", (cid,))[0]["state"], "asked")

    def test_job_actions_only_work_on_the_ticket_the_job_belongs_to(self):
        self.call(C1)
        self.call(C2)
        job_id = self.act(lambda db, biz: flow.book_from_web(db, biz, self.conv_id(C1), "2026-10-14T17:00:00Z", "A", "1 Main St", ""))
        self.logged_in()
        for action in ("done", "cancel"):
            self.post("/tickets/%d/jobs/%d/%s" % (self.conv_id(C2), job_id, action))  # wrong customer's ticket
            self.assertEqual(self.q("SELECT status FROM jobs WHERE id=?", (job_id,))[0]["status"], "booked", action)
        self.assertEqual(self.post("/tickets/999/jobs/%d/done" % job_id).status_code, 404)
        self.post("/tickets/%d/jobs/%d/done" % (self.conv_id(C1), job_id))
        self.assertEqual(self.q("SELECT status FROM jobs WHERE id=?", (job_id,))[0]["status"], "done")

    def test_unknown_pages_show_a_plain_error_page(self):
        r = self.c.get("/book/not-a-real-token")
        self.assertEqual(r.status_code, 404)
        self.assertIn("Page not found", self.html(r))
        self.assertNotIn("Traceback", self.html(r))

    def test_healthz(self):
        self.assertEqual(self.c.get("/healthz").get_json(), {"ok": True})


class OwnerScreenTests(WebCase):
    def test_board_counts_and_filters(self):
        self.call(C1)
        self.call(C2)
        self.text(C2, "basement flooding")
        self.logged_in()
        body = self.html(self.c.get("/dashboard"))
        self.assertIn("1 customer needs you.", body)
        self.assertIn("(586) 555-0112", body)
        self.assertNotIn("(586) 555-0111", body)  # the "needs you" list is just the urgent one
        self.assertIn("(586) 555-0111", self.html(self.c.get("/dashboard?show=all")))
        self.assertEqual(self.c.get("/dashboard?show=bogus").status_code, 200)

    def test_empty_board_explains_what_to_do(self):
        body = self.html(self.logged_in().get("/dashboard"))
        self.assertIn("No customers yet", body)
        self.assertIn("Nothing needs you right now.", body)

    def test_board_says_when_the_assistant_is_paused(self):
        self.act(lambda db, biz: db.execute("UPDATE businesses SET assistant_on=0"))
        self.assertIn("The assistant is paused", self.html(self.logged_in().get("/dashboard")))

    def test_ticket_page_shows_the_thread_and_marks_blocked_texts(self):
        self.call(C1)
        self.text(C1, "STOP")
        self.logged_in()
        self.post("/tickets/%d/take-over" % self.conv_id(C1))
        body = self.html(self.c.get("/tickets/%d" % self.conv_id(C1)))
        self.assertIn("automated assistant", body)
        self.assertIn("STOP", body)
        self.assertIn("replied STOP", body)  # the "can't text them" explanation

    def test_owner_can_send_a_text_mark_done_and_cancel(self):
        self.logged_in()
        self.call(C1)
        cid = self.conv_id(C1)
        r = self.post("/tickets/%d/send" % cid, {"body": "  We can come at 2   today "})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.sent("manual", C1)[0]["body"], "We can come at 2 today")
        self.assertEqual(self.q("SELECT state FROM conversations WHERE id=?", (cid,))[0]["state"], "owner_handling")
        for body in ("", "x" * 400):
            self.post("/tickets/%d/send" % cid, {"body": body})
        self.assertEqual(len(self.sent("manual")), 1)

        job_id = self.act(lambda db, biz: flow.book_from_web(db, biz, cid, "2026-10-14T17:00:00Z", "Pat", "1 Main St", "leak"))
        self.post("/tickets/%d/jobs/%d/cancel" % (cid, job_id), {"notify": "1"})
        self.assertEqual(self.q("SELECT status FROM jobs WHERE id=?", (job_id,))[0]["status"], "cancelled")
        self.assertIn("cancelled", self.sent("manual", C1)[-1]["body"])
        job2 = self.act(lambda db, biz: flow.book_from_web(db, biz, cid, "2026-10-14T18:00:00Z", "Pat", "1 Main St", "leak"))
        self.post("/tickets/%d/jobs/%d/done" % (cid, job2))
        self.assertEqual(self.q("SELECT status FROM jobs WHERE id=?", (job2,))[0]["status"], "done")

    def test_no_texting_a_customer_who_opted_out(self):
        self.call(C1)
        self.text(C1, "STOP")
        self.logged_in()
        self.post("/tickets/%d/send" % self.conv_id(C1), {"body": "hello"})
        self.assertEqual(self.sent("manual"), [])


def setup_form(**changes):
    form = {
        "name": "Test Plumbing", "owner_cell": "(586) 555-0100", "timezone": "America/Detroit",
        "job_minutes": "60", "lead_hours": "2", "horizon_days": "7", "services": "Leaks and drains",
        "service_area": "Macomb County", "review_url": "", "assistant_on": "1",
    }
    for day in ("mon", "tue", "wed", "thu", "fri"):
        form.update({"open_" + day: "1", "start_" + day: "08:00", "end_" + day: "17:00"})
    form.update(changes)
    return {k: v for k, v in form.items() if v is not None}


class SetupTests(WebCase):
    def test_saving_valid_settings(self):
        self.logged_in()
        r = self.post("/setup", setup_form(name="Hall & Sons Plumbing", review_url="https://g.page/r/abc123/review", job_minutes="120", open_sat="1", start_sat="09:00", end_sat="12:00"))
        self.assertEqual(r.status_code, 302)
        row = self.q("SELECT * FROM businesses")[0]
        self.assertEqual(row["name"], "Hall & Sons Plumbing")
        self.assertEqual(row["job_minutes"], 120)
        self.assertEqual(json.loads(row["hours_json"])["sat"], [["09:00", "12:00"]])
        self.assertEqual(json.loads(row["hours_json"])["sun"], [])
        self.assertEqual(row["review_url"], "https://g.page/r/abc123/review")

    def test_unchecking_the_assistant_pauses_it(self):
        self.logged_in()
        self.post("/setup", setup_form(assistant_on=None))
        self.assertEqual(self.q("SELECT assistant_on FROM businesses")[0]["assistant_on"], 0)

    def test_bad_settings_are_rejected_with_a_reason_and_nothing_is_saved(self):
        self.logged_in()
        cases = [
            (dict(start_mon="17:00", end_mon="08:00"), "closing time must be after"),
            (dict(start_mon="8am"), "opening and closing time"),
            (dict(review_url="http://g.page/r/abc"), "https://"),
            (dict(review_url="javascript:alert(1)"), "https://"),
            (dict(timezone="Mars/Phobos"), "time zone"),
            (dict(job_minutes="7"), "between 30 and 240"),
            (dict(job_minutes="75"), "from the list"),
            (dict(lead_hours="-1"), "between 0 and 72"),
            (dict(horizon_days="99"), "between 1 and 30"),
            (dict(owner_cell="123"), "cell phone"),
            (dict(name=""), "Business name"),
            (dict(open_mon=None, open_tue=None, open_wed=None, open_thu=None, open_fri=None), "at least one day"),
        ]
        for change, message in cases:
            r = self.post("/setup", setup_form(**change))
            self.assertEqual(r.status_code, 400, change)
            self.assertIn(message, self.html(r), change)
        self.assertEqual(self.q("SELECT name, review_url, job_minutes FROM businesses")[0], {"name": "Test Plumbing", "review_url": "", "job_minutes": 60})

    def test_test_alert_goes_to_the_owners_cell_and_is_rate_limited(self):
        self.logged_in()
        self.post("/setup/test-alert")
        alerts = self.sent("alert", OWNER)
        self.assertEqual(len(alerts), 1)
        self.assertIn("test", alerts[0]["body"].lower())
        for _ in range(5):
            self.post("/setup/test-alert")
        self.assertEqual(len(self.sent("alert", OWNER)), 3)


class CalendarTests(WebCase):
    def booked(self):
        self.call(C1)
        self.act(lambda db, biz: flow.book_from_web(db, biz, self.conv_id(C1), "2026-10-14T17:00:00Z", "Pat Lee", "12 Elm St, Apt 4; back door", "Leak, under sink\nring twice"))

    def feed_path(self):
        return "/calendar/%s.ics" % self.q("SELECT ics_token FROM businesses")[0]["ics_token"]

    def test_feed_lists_booked_jobs_and_escapes_text(self):
        self.booked()
        r = self.c.get(self.feed_path())
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.mimetype.startswith("text/calendar"))
        body = self.html(r)
        self.assertTrue(body.startswith("BEGIN:VCALENDAR\r\n"))
        body = body.replace("\r\n ", "")  # undo line folding before looking for text
        self.assertIn("DTSTART:20261014T170000Z", body)
        self.assertIn("DTEND:20261014T180000Z", body)
        self.assertIn("LOCATION:12 Elm St\\, Apt 4\\; back door", body)
        self.assertIn("Leak\\, under sink\\nring twice", body)
        self.assertTrue(body.endswith("END:VCALENDAR\r\n"))

    def test_long_lines_are_folded_to_75_bytes(self):
        self.call(C1)
        self.act(lambda db, biz: flow.book_from_web(db, biz, self.conv_id(C1), "2026-10-14T17:00:00Z", "N" * 80, "é" * 100, "p" * 300))
        for line in self.html(self.c.get(self.feed_path())).split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75)

    def test_cancelled_and_old_jobs_stay_out_and_wrong_token_is_a_404(self):
        self.booked()
        self.assertEqual(self.c.get("/calendar/not-the-token.ics").status_code, 404)
        self.act(lambda db, biz: flow.cancel_job(db, biz, 1, notify=False))
        self.assertNotIn("BEGIN:VEVENT", self.html(self.c.get(self.feed_path())))

    def test_new_link_kills_the_old_one(self):
        old = self.feed_path()
        self.logged_in()
        self.post("/setup/new-calendar-link")
        self.assertEqual(self.c.get(old).status_code, 404)
        self.assertEqual(self.c.get(self.feed_path()).status_code, 200)


class BookingPageTests(WebCase):
    def setUp(self):
        super().setUp()
        self.call(C1)
        self.token = self.token_of(C1)
        self.form = {"slot": "2026-10-14T17:00:00Z", "name": "Pat Lee", "address": "12 Elm St, Clinton Twp", "problem": "Kitchen faucet leaks"}

    def test_page_lists_open_times_grouped_by_day(self):
        body = self.html(self.c.get("/book/" + self.token))
        self.assertIn("Wednesday, October 14", body)
        self.assertIn(">1:00 PM<", body)
        self.assertNotIn("Saturday", body)
        self.assertNotIn("Log out", body)  # a customer never sees the owner's menu

    def test_booking_creates_the_job_confirms_by_text_and_alerts_the_owner(self):
        r = self.c.post("/book/" + self.token, data=self.form)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["Location"].endswith("/book/%s/done" % self.token))
        done = self.html(self.c.get(r.headers["Location"]))
        self.assertIn("You're booked", done)
        self.assertIn("Wed Oct 14, 1:00 PM", done)
        job = self.q("SELECT * FROM jobs")[0]
        self.assertEqual((job["name"], job["source"], job["status"]), ("Pat Lee", "web", "booked"))
        self.assertIn("Wed Oct 14, 1:00 PM", self.sent("confirm", C1)[0]["body"])
        self.assertTrue(self.sent("alert", OWNER)[-1]["body"].startswith("Booked: Pat Lee"))
        self.assertEqual(self.q("SELECT name FROM customers")[0]["name"], "Pat Lee")

    def test_a_booked_customer_sees_their_booking_instead_of_the_form(self):
        self.c.post("/book/" + self.token, data=self.form)
        body = self.html(self.c.get("/book/" + self.token))
        self.assertIn("You're already booked", body)
        self.assertNotIn('name="slot"', body)
        again = self.c.post("/book/" + self.token, data={**self.form, "slot": "2026-10-14T18:00:00Z"})
        self.assertEqual(again.status_code, 302)
        self.assertEqual(len(self.q("SELECT id FROM jobs")), 1)

    def test_taken_time_is_refused_with_a_clear_message(self):
        self.call(C2)
        self.c.post("/book/" + self.token, data=self.form)
        r = self.c.post("/book/" + self.token_of(C2), data=self.form)
        self.assertEqual(r.status_code, 409)
        self.assertIn("just taken", self.html(r))
        self.assertIn("Pat Lee", self.html(r))  # what they typed is kept
        self.assertEqual(len(self.q("SELECT id FROM jobs")), 1)

    def test_times_the_owner_never_offered_are_refused(self):
        for slot in ("2026-10-14T03:00:00Z", "2026-10-17T14:00:00Z", "2026-10-14T17:30:00Z", "2030-01-01T14:00:00Z", "2026-10-14T15:00:00Z"):
            r = self.c.post("/book/" + self.token, data={**self.form, "slot": slot})
            self.assertEqual(r.status_code, 409, slot)  # night, Saturday, off the grid, too far out, too soon
        self.assertEqual(self.q("SELECT id FROM jobs"), [])

    def test_missing_or_bad_fields_are_explained(self):
        for change, message in [
            ({"slot": ""}, "Pick a time"),
            ({"slot": "tomorrow"}, "Pick a time"),
            ({"name": " "}, "Enter your name"),
            ({"address": "x"}, "address"),
            ({"problem": "p" * 501}, "under 500"),
        ]:
            r = self.c.post("/book/" + self.token, data={**self.form, **change})
            self.assertEqual(r.status_code, 400, change)
            self.assertIn(message, self.html(r), change)
        self.assertEqual(self.q("SELECT id FROM jobs"), [])

    def test_unknown_link_is_a_404_and_guessing_is_pointless(self):
        self.assertEqual(self.c.get("/book/aaaaaaaaaaaaaaaaaaaaaa").status_code, 404)
        self.assertEqual(self.c.post("/book/aaaaaaaaaaaaaaaaaaaaaa", data=self.form).status_code, 404)
        self.assertGreaterEqual(len(self.token), 20)

    def test_paused_assistant_closes_online_booking(self):
        self.act(lambda db, biz: db.execute("UPDATE businesses SET assistant_on=0"))
        self.assertIn("paused", self.html(self.c.get("/book/" + self.token)))
        self.c.post("/book/" + self.token, data=self.form)
        self.assertEqual(self.q("SELECT id FROM jobs"), [])

    def test_hammering_one_link_is_rate_limited(self):
        codes = [self.c.post("/book/" + self.token, data={**self.form, "name": ""}).status_code for _ in range(12)]
        self.assertEqual(codes[:10], [400] * 10)
        self.assertEqual(codes[10], 429)

    def test_done_page_is_honest_when_the_customer_opted_out(self):
        self.c.post("/book/" + self.token, data=self.form)
        self.text(C1, "STOP")
        self.assertIn("won't send a reminder", self.html(self.c.get("/book/%s/done" % self.token)))

    def test_done_page_without_a_booking_sends_you_back_to_the_form(self):
        r = self.c.get("/book/%s/done" % self.token)
        self.assertEqual(r.status_code, 302)


# ---------------------------------------------------------------- phone-company webhooks
class HookTests(WebCase):
    def voice(self, **extra):
        data = {"To": LINE, "From": C1, "CallSid": "CA100", **extra}
        return self.c.post("/hooks/twilio/voice", data=data)

    def test_incoming_call_rings_the_owner_with_a_press_a_key_screen(self):
        body = self.html(self.voice())
        self.assertIn("<Dial", body)
        self.assertIn('timeout="20"', body)
        self.assertIn(OWNER, body)
        self.assertIn("/hooks/twilio/screen?b=1&amp;c=%2B15865550111", body)
        self.assertIn("/hooks/twilio/voice-done", body)

    def test_call_nobody_answers_texts_the_caller(self):
        r = self.c.post("/hooks/twilio/voice-done", data={"To": LINE, "From": C1, "CallSid": "CA100", "DialCallStatus": "no-answer"})
        self.assertIn("<Say>", self.html(r))
        self.assertEqual(len(self.sent("first_text", C1)), 1)

    def test_owner_presses_a_key_so_no_text_goes_out(self):
        screen = self.c.post("/hooks/twilio/screen?b=1&c=%2B15865550111", data={"CallSid": "CA101", "ParentCallSid": "CA100"})
        self.assertIn("Press any key", self.html(screen))
        self.assertIn("Test Plumbing", self.html(screen))
        ok = self.c.post("/hooks/twilio/screen-ok?b=1&c=%2B15865550111", data={"CallSid": "CA101", "ParentCallSid": "CA100"})
        self.assertEqual(self.html(ok).count("<Dial"), 0)
        self.c.post("/hooks/twilio/voice-done", data={"To": LINE, "From": C1, "CallSid": "CA100", "DialCallStatus": "completed"})
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.q("SELECT status FROM calls")[0]["status"], "answered")

    def test_carrier_voicemail_picking_up_still_counts_as_missed(self):
        # "completed" with no key press means voicemail (or nobody) answered, not a person.
        self.c.post("/hooks/twilio/voice-done", data={"To": LINE, "From": C1, "CallSid": "CA102", "DialCallStatus": "completed"})
        self.assertEqual(len(self.sent("first_text", C1)), 1)

    def test_without_screening_a_completed_call_counts_as_answered(self):
        app = self.make_app(SCREEN_CALLS=False)
        with app.app_context():
            create_business(get_db(), "Test Plumbing", EMAIL, PASSWORD, OWNER, LINE)
        c = app.test_client()
        self.assertNotIn("<Number url", self.html(c.post("/hooks/twilio/voice", data={"To": LINE, "From": C1, "CallSid": "CA1"})))
        c.post("/hooks/twilio/voice-done", data={"To": LINE, "From": C1, "CallSid": "CA1", "DialCallStatus": "completed"})
        c.post("/hooks/twilio/voice-done", data={"To": LINE, "From": C2, "CallSid": "CA2", "DialCallStatus": "busy"})
        with app.app_context():
            texted = [r["to_phone"] for r in get_db().execute("SELECT to_phone FROM messages WHERE kind='first_text'")]
        self.assertEqual(texted, [C2])

    def test_unknown_number_is_rejected_and_nothing_is_stored(self):
        r = self.c.post("/hooks/twilio/voice", data={"To": "+15865550000", "From": C1, "CallSid": "CA1"})
        self.assertIn("<Reject/>", self.html(r))
        self.c.post("/hooks/twilio/sms", data={"To": "+15865550000", "From": C1, "Body": "hi", "MessageSid": "SM1"})
        self.assertEqual(self.q("SELECT id FROM customers"), [])

    def test_incoming_text_gets_the_offer_and_a_repeat_delivery_is_ignored(self):
        self.call(C1)
        data = {"To": LINE, "From": C1, "Body": "faucet leaking at 12 Elm", "MessageSid": "SM900"}
        r = self.c.post("/hooks/twilio/sms", data=data)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.html(r).strip(), "<Response/>")
        self.c.post("/hooks/twilio/sms", data=data)
        self.assertEqual(len(self.sent("offer", C1)), 1)

    def test_unsigned_webhooks_are_refused_unless_the_simulator_is_on(self):
        app = self.make_app(SIMULATOR=False)
        r = app.test_client().post("/hooks/twilio/sms", data={"To": LINE, "From": C1, "Body": "hi"})
        self.assertEqual(r.status_code, 403)

    def test_tick_needs_the_secret(self):
        self.assertEqual(self.c.post("/internal/tick").status_code, 404)  # no secret set: route is off
        app = self.make_app(CRON_SECRET="s3cret-for-cron")
        c = app.test_client()
        self.assertEqual(c.post("/internal/tick").status_code, 403)
        self.assertEqual(c.post("/internal/tick", headers={"Authorization": "Bearer wrong"}).status_code, 403)
        r = c.post("/internal/tick", headers={"Authorization": "Bearer s3cret-for-cron"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(c.get("/internal/tick").status_code, 405)


def twilio_signature(token, url, params):
    data = url + "".join(key + params[key] for key in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


class SignedHookTests(AppBase):
    """With live texting on, only requests signed by Twilio are accepted."""

    TOKEN = "twilio-auth-token-for-tests"
    BASE = "https://app.example.test"

    def setUp(self):
        super().setUp()
        self.app = self.make_app(
            SMS_PROVIDER="twilio", SIMULATOR=False, BASE_URL=self.BASE, COOKIE_SECURE=True,
            TWILIO_ACCOUNT_SID="ACtest", TWILIO_AUTH_TOKEN=self.TOKEN,
        )
        with self.app.app_context():
            create_business(get_db(), "Test Plumbing", EMAIL, PASSWORD, OWNER, LINE)
        self.c = self.app.test_client()

    def sms(self, signature=None, body="faucet leaks", path="/hooks/twilio/sms", sid="SM1"):
        params = {"To": LINE, "From": C1, "Body": body, "MessageSid": sid}
        headers = {}
        if signature != "none":
            headers["X-Twilio-Signature"] = signature or twilio_signature(self.TOKEN, self.BASE + path, params)
        return self.c.post(path, data=params, headers=headers)

    def count(self, sql):
        with self.app.app_context():
            return get_db().execute(sql).fetchone()[0]

    def test_unsigned_or_wrongly_signed_requests_are_forbidden_and_change_nothing(self):
        self.assertEqual(self.sms("none").status_code, 403)
        self.assertEqual(self.sms("AAAAAAAAAAAAAAAAAAAAAAAAAAA=").status_code, 403)
        wrong_key = twilio_signature("some-other-token", self.BASE + "/hooks/twilio/sms", {"To": LINE, "From": C1, "Body": "faucet leaks", "MessageSid": "SM1"})
        self.assertEqual(self.sms(wrong_key).status_code, 403)
        self.assertEqual(self.count("SELECT COUNT(*) FROM customers"), 0)

    def test_a_signature_for_different_content_is_forbidden(self):
        params = {"To": LINE, "From": C1, "Body": "something else", "MessageSid": "SM1"}
        sig = twilio_signature(self.TOKEN, self.BASE + "/hooks/twilio/sms", params)
        self.assertEqual(self.sms(sig, body="faucet leaks").status_code, 403)

    def test_correctly_signed_request_is_accepted(self):
        self.assertEqual(self.sms().status_code, 200)
        self.assertEqual(self.count("SELECT COUNT(*) FROM messages WHERE direction='in'"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM messages WHERE kind='offer'"), 1)

    def test_query_string_is_part_of_the_signed_url(self):
        path = "/hooks/twilio/screen-ok?b=1&c=%2B15865550111"
        params = {"CallSid": "CA1", "ParentCallSid": "CA0"}
        good = twilio_signature(self.TOKEN, self.BASE + path, params)
        self.assertEqual(self.c.post(path, data=params, headers={"X-Twilio-Signature": good}).status_code, 200)
        tampered = "/hooks/twilio/screen-ok?b=2&c=%2B15865550111"
        self.assertEqual(self.c.post(tampered, data=params, headers={"X-Twilio-Signature": good}).status_code, 403)

    def test_the_simulator_is_not_available_when_texting_is_live(self):
        self.assertEqual(self.c.get("/sim").status_code, 404)

    def test_session_cookie_is_secure_when_live(self):
        c = self.app.test_client()
        page = c.get("/login", base_url=self.BASE).get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        r = c.post("/login", data={"email": EMAIL, "password": PASSWORD, "csrf": token}, base_url=self.BASE)
        self.assertEqual(r.status_code, 302)
        self.assertIn("Secure", r.headers["Set-Cookie"])
        self.assertIn("Strict-Transport-Security", r.headers)


# ---------------------------------------------------------------- simulator
class SimulatorTests(WebCase):
    def setUp(self):
        super().setUp()
        self.logged_in()

    def fetch(self, path, data=None):
        return self.post(path, data or {}, headers={"X-Requested-With": "fetch"})

    def test_a_pretend_customer_can_go_from_missed_call_to_booked(self):
        phone = "(586) 555-0142"
        self.assertTrue(self.fetch("/sim/missed-call", {"phone": phone}).get_json()["ok"])
        self.fetch("/sim/text", {"phone": phone, "body": "toilet is running constantly"})
        self.fetch("/sim/text", {"phone": phone, "body": "2"})
        feed = self.c.get("/sim/feed?phone=" + quote(phone)).get_json()
        self.assertEqual(feed["state"], "booked")
        kinds = [(b["mine"], b["body"][:12]) for b in feed["customer"]]
        self.assertEqual(kinds[0], (False, "Hi, this is "))
        self.assertTrue(any(b["mine"] and b["body"] == "2" for b in feed["customer"]))
        self.assertTrue(any(b["body"].startswith("Booked with") for b in feed["customer"] if not b["mine"]))
        self.assertTrue(any(b["body"].startswith("Booked:") for b in feed["owner"]))
        self.assertEqual(feed["book_path"], "/book/" + self.token_of("+15865550142"))

    def test_simulator_refuses_the_owners_own_number_and_empty_texts(self):
        for path, data in [("/sim/missed-call", {"phone": OWNER}), ("/sim/text", {"phone": "bad", "body": "x"}), ("/sim/text", {"phone": C1, "body": ""})]:
            r = self.fetch(path, data)
            self.assertEqual(r.status_code, 400, path)
            self.assertFalse(r.get_json()["ok"])

    def test_without_javascript_the_buttons_redirect_back(self):
        r = self.post("/sim/missed-call", {"phone": C1})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/sim?phone=", r.headers["Location"])
        self.assertEqual(self.c.get(r.headers["Location"]).status_code, 200)

    def test_jumping_the_clock_moves_the_apps_time_and_can_be_undone(self):
        self.app.config["NOW_OVERRIDE"] = None  # use the real clock plus the simulator's jump
        from jobping.clock import utcnow

        def app_now():
            with self.app.app_context():
                return utcnow()

        real = datetime.now(timezone.utc)
        self.assertLess(abs((app_now() - real).total_seconds()), 5)
        self.assertEqual(self.fetch("/sim/skip", {"minutes": "1440"}).status_code, 200)
        self.assertLess(abs((app_now() - (real + timedelta(days=1))).total_seconds()), 5)
        self.assertEqual(self.fetch("/sim/skip", {"minutes": "5"}).status_code, 400)  # only the offered jumps
        self.assertEqual(self.fetch("/sim/reset-clock").status_code, 200)
        self.assertLess(abs((app_now() - real).total_seconds()), 5)

    def test_jumping_the_clock_also_runs_the_reminders(self):
        self.call(C1)
        cid = self.conv_id(C1)
        self.act(lambda db, biz: flow.book_from_web(db, biz, cid, "2026-10-15T12:00:00Z", "Pat", "1 Main St", "leak"))
        self.app.config["NOW_OVERRIDE"] = "2026-10-14T21:00:00Z"  # 5 PM the day before: reminder is due
        self.assertEqual(self.fetch("/sim/run").status_code, 200)
        self.assertEqual(len(self.sent("reminder", C1)), 1)

    def test_untrusted_text_is_returned_as_data_not_markup(self):
        self.fetch("/sim/missed-call", {"phone": C1})
        self.fetch("/sim/text", {"phone": C1, "body": "<script>alert(1)</script>"})
        feed = self.c.get("/sim/feed?phone=" + quote(C1))
        self.assertEqual(feed.mimetype, "application/json")
        self.assertIn("<script>alert(1)</script>", feed.get_json()["customer"][1]["body"])


if __name__ == "__main__":
    unittest.main()


class LiveModeAccountTests(AppBase):
    """With real texting on, accounts are made by the person running the app, not by strangers."""

    LIVE = dict(
        SMS_PROVIDER="twilio", SIMULATOR=False, BASE_URL="https://app.example.test", COOKIE_SECURE=True,
        TWILIO_ACCOUNT_SID="ACtest", TWILIO_AUTH_TOKEN="tok", ALLOW_SIGNUP=False,
    )

    def test_signup_is_closed_and_the_texting_number_cannot_be_changed_by_an_owner(self):
        app = self.make_app(**self.LIVE)
        with app.app_context():
            create_business(get_db(), "Test Plumbing", EMAIL, PASSWORD, OWNER, LINE)
        c = app.test_client()
        self.assertEqual(c.get("/signup", base_url="https://app.example.test").status_code, 403)
        page = c.get("/login", base_url="https://app.example.test").get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        c.post("/login", data={"email": EMAIL, "password": PASSWORD, "csrf": token}, base_url="https://app.example.test")
        page = c.get("/setup", base_url="https://app.example.test").get_data(as_text=True)
        self.assertNotIn('name="text_number"', page)
        self.assertIn("(586) 555-0199", page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        data = setup_form(csrf=token, text_number="+15865550123")  # an attempt to claim another number
        r = c.post("/setup", data=data, base_url="https://app.example.test")
        self.assertEqual(r.status_code, 302)
        with app.app_context():
            self.assertEqual(get_db().execute("SELECT text_number FROM businesses").fetchone()[0], LINE)

    def test_the_command_line_creates_a_business_and_checks_the_details(self):
        app = self.make_app(**self.LIVE)
        runner = app.test_cli_runner()
        args = ["create-business", "--name", "Hall Plumbing", "--email", "paul@example.com", "--cell", "(586) 555-0188",
                "--text-number", "+15865550177", "--password", "a-long-password-1"]
        result = runner.invoke(args=args)
        self.assertEqual(result.exit_code, 0, result.output)
        with app.app_context():
            row = get_db().execute("SELECT * FROM businesses").fetchone()
        self.assertEqual((row["name"], row["owner_cell"], row["text_number"]), ("Hall Plumbing", "+15865550188", "+15865550177"))
        self.assertNotIn("a-long-password-1", row["password_hash"])
        for bad, message in [
            (["--email", "paul@example.com"], "already exists"),
            (["--cell", "+442079460958", "--email", "new@example.com"], "US or Canadian"),
            (["--text-number", "+15865550177", "--email", "new@example.com"], "already used"),
            (["--text-number", "+442079460958", "--email", "new@example.com"], "US or Canadian"),
            (["--password", "short", "--email", "new@example.com"], "at least 10"),
        ]:
            again = runner.invoke(args=args + bad)  # later options win
            self.assertNotEqual(again.exit_code, 0, bad)
            self.assertIn(message, again.output, bad)

    def test_demo_data_refuses_to_load_where_texting_is_live_or_public(self):
        from jobping.seed import seed_demo

        for overrides in (self.LIVE, dict(BASE_URL="https://public.example.test", COOKIE_SECURE=True)):
            app = self.make_app(**overrides)
            with app.app_context():
                with self.assertRaises(RuntimeError):
                    seed_demo()

    def test_demo_data_loads_every_time_with_six_customers(self):
        # Regression: a made-up texting number that landed on a demo customer's number
        # (about 7 in 100 tries) made the app ignore that customer's call and the demo crashed.
        from jobping.auth import _fake_text_number
        from jobping.seed import CUSTOMERS, OWNER_CELL, seed_demo

        app = self.make_app()
        with app.app_context():
            taken = set(CUSTOMERS.values()) | {OWNER_CELL}
            self.assertFalse({_fake_text_number(get_db()) for _ in range(500)} & taken)
            self.assertTrue(seed_demo())
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 6)

    def test_owner_cell_must_be_a_us_or_canadian_number(self):
        app = self.make_app()
        with app.app_context():
            create_business(get_db(), "Test Plumbing", EMAIL, PASSWORD, OWNER, LINE)
        c = app.test_client()
        with c.session_transaction() as s:
            s["csrf"] = "t"
        c.post("/login", data={"email": EMAIL, "password": PASSWORD, "csrf": "t"})
        with c.session_transaction() as s:
            s["csrf"] = "t"
        for number in ("+442079460958", "+1 900 555 0100"):
            r = c.post("/setup", data={**setup_form(owner_cell=number), "csrf": "t"})
            self.assertEqual(r.status_code, 400, number)
            self.assertIn("US or Canadian", r.get_data(as_text=True))
        with app.app_context():
            self.assertEqual(get_db().execute("SELECT owner_cell FROM businesses").fetchone()[0], OWNER)


class ConsentLineTests(WebCase):
    def test_booking_page_tells_the_customer_what_texts_to_expect(self):
        self.call(C1)
        body = self.html(self.c.get("/book/" + self.token_of(C1)))
        self.assertIn("you agree to get texts from Test Plumbing", body)
        self.assertIn("review request", body)
        self.assertIn("Reply STOP anytime", body)


class LookTests(WebCase):
    """The visual layer: bundled fonts and icons, the phone tab bar, the theme switch, the urgent label."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_stylesheet_only_points_at_files_that_exist_and_load(self):
        css = (self.ROOT / "jobping" / "static" / "app.css").read_text()
        urls = re.findall(r"url\(\"?([^)\"]+)\"?\)", css)
        self.assertTrue(urls)
        for rel in urls:
            self.assertFalse(re.match(r"(?i)(https?:)?//|data:", rel), "no outside files: " + rel)
            r = self.c.get("/static/" + rel)
            self.assertEqual(r.status_code, 200, rel)
            self.assertGreater(len(r.data), 10_000, rel)  # a real font, not an error page
            r.close()

    def test_bundled_fonts_and_icons_ship_with_their_licenses(self):
        for name in ("OFL-BricolageGrotesque.txt", "OFL-AtkinsonHyperlegibleNext.txt"):
            self.assertIn("SIL OPEN FONT LICENSE", (self.ROOT / "jobping" / "static" / "fonts" / name).read_text().upper())
        self.assertIn("MIT", (self.ROOT / "LICENSE-Phosphor-Icons.txt").read_text())

    def test_no_page_loads_anything_from_another_site(self):
        self.call(C1)
        self.logged_in()
        for path in ("/dashboard", "/setup", "/sim", "/tickets/%d" % self.conv_id(C1), "/book/" + self.token_of(C1)):
            body = self.html(self.c.get(path))
            self.assertNotRegex(body, r"(?i)(src|href)=\"(https?:)?//(?!localhost)", path)
            self.assertNotIn("fonts.googleapis", body, path)
        css = (self.ROOT / "jobping" / "static" / "app.css").read_text()
        self.assertNotRegex(css, r"(?i)@import|https?://")

    def test_owner_pages_have_a_tab_bar_and_a_theme_switch_with_names(self):
        self.logged_in()
        body = self.html(self.c.get("/dashboard"))
        self.assertIn('class="mainnav" aria-label="Main"', body)
        self.assertIn('aria-current="page"', body)
        self.assertRegex(body, r"<button[^>]*data-theme-toggle[^>]*aria-label=\"[^\"]+\"")
        self.assertIn("Log out", body)  # the phone layout hides this label visually, never from screen readers
        self.assertIn("theme.js", body)
        self.assertEqual(body.count('aria-hidden="true"'), body.count("<svg"))  # every icon is decoration

    def test_customer_page_follows_the_theme_but_has_no_owner_controls(self):
        self.call(C1)
        body = self.html(self.c.get("/book/" + self.token_of(C1)))
        self.assertIn("theme.js", body)
        self.assertNotIn("data-theme-toggle", body)
        self.assertNotIn("mainnav", body)

    def test_an_emergency_is_labelled_urgent_everywhere_and_a_normal_job_is_not(self):
        self.call(C1)
        self.text(C1, "my basement is flooding, water everywhere")
        self.call(C2)
        self.text(C2, "kitchen faucet drips")
        self.logged_in()
        board = self.html(self.c.get("/dashboard?show=all"))
        self.assertEqual(board.count(">Urgent<"), 1)
        flooded = self.html(self.c.get("/tickets/%d" % self.conv_id(C1)))
        self.assertRegex(flooded, r'class="ticket-head state-needs_owner emergency"')
        self.assertIn("Urgent", flooded)
        self.assertIn("tel:" + C1, flooded)  # one tap to call
        dripping = self.html(self.c.get("/tickets/%d" % self.conv_id(C2)))
        self.assertNotIn("emergency", dripping.split("</header>")[0])
        self.assertNotIn("Urgent", dripping)
