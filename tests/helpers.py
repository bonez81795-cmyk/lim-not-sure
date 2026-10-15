"""Shared set-up for the tests. Every test gets a fresh database in a temp folder."""
import os
import shutil
import tempfile
import unittest

from jobping import create_app
from jobping.auth import create_business
from jobping.db import get_db
from jobping.texting import ConsoleProvider

OWNER = "+15865550100"  # the shop owner's cell
LINE = "+15865550199"  # the shop's texting number
C1, C2, C3, C4 = "+15865550111", "+15865550112", "+15865550113", "+15865550114"
NOW = "2026-10-14T15:00:00Z"  # Wednesday 11:00 AM in Detroit (EDT)
PASSWORD = "correct-horse-1"
EMAIL = "owner@example.com"


class AppBase(unittest.TestCase):
    def make_app(self, **overrides):
        cfg = {
            "TESTING": True,
            "SCHEDULER": False,
            "DATABASE": os.path.join(self.tmp, "t-%d.db" % len(os.listdir(self.tmp))),
            "SECRET_KEY": "test-secret-key-for-unit-tests-0123456789",
            "NOW_OVERRIDE": NOW,
            "SMS_PROVIDER": "console",
            "SIMULATOR": True,
        }
        cfg.update(overrides)
        app = create_app(cfg, env={})
        if app.config["SMS_PROVIDER"] == "twilio":
            app.extensions["jobping_provider"] = ConsoleProvider()  # never touch the network
        return app

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jobping-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class FlowCase(AppBase):
    """Calls the conversation engine directly, inside an app context."""

    def setUp(self):
        super().setUp()
        self.app = self.make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.db = get_db()
        self.biz_id = create_business(self.db, "Test Plumbing", EMAIL, PASSWORD, OWNER, LINE)

    # --- helpers
    def biz(self):
        return self.db.execute("SELECT * FROM businesses WHERE id=?", (self.biz_id,)).fetchone()

    def at(self, iso_text):
        self.app.config["NOW_OVERRIDE"] = iso_text

    def update_biz(self, **fields):
        sets = ", ".join("%s=?" % k for k in fields)
        self.db.execute("UPDATE businesses SET %s WHERE id=?" % sets, [*fields.values(), self.biz_id])

    def call(self, phone, sid=None):
        from jobping import flow

        flow.on_missed_call(self.db, self.biz(), phone, sid)

    def text(self, phone, body, provider_id=None):
        from jobping import flow

        flow.on_inbound_sms(self.db, self.biz(), phone, body, provider_id)

    def out(self, to=None, kind=None):
        sql, args = "SELECT * FROM messages WHERE direction='out'", []
        if to:
            sql += " AND to_phone=?"
            args.append(to)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id", args)]

    def sent(self, to=None, kind=None):
        return [m for m in self.out(to, kind) if m["status"] == "sent"]

    def alerts(self):
        return [m for m in self.sent(to=OWNER) if m["kind"] == "alert"]

    def conv(self, phone):
        return self.db.execute(
            "SELECT c.* FROM conversations c JOIN customers u ON u.id=c.customer_id WHERE u.phone=?", (phone,)
        ).fetchone()

    def customer(self, phone):
        return self.db.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()

    def jobs(self, status=None):
        sql = "SELECT * FROM jobs"
        if status:
            return self.db.execute(sql + " WHERE status=? ORDER BY starts_at", (status,)).fetchall()
        return self.db.execute(sql + " ORDER BY starts_at").fetchall()

    def book(self, phone, number="1"):
        """Run the normal path: missed call, describe the problem, pick an offered time."""
        self.call(phone)
        self.text(phone, "kitchen faucet is leaking, 12 Elm St")
        self.text(phone, number)
        return self.jobs("booked")[-1]
