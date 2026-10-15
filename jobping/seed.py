"""A demo business with a few made-up customers, so the screens have something on them.

Local use only: the login is public (it is in the README), so this refuses to run with live texting.
All phone numbers are in the 555-01xx range reserved for fiction.
"""
from datetime import timedelta

from flask import current_app

from . import flow
from . import slots as slotmod
from .auth import create_business
from .clock import iso, utcnow
from .db import get_db, tx

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password-123"
DEMO_NAME = "Lakeshore Plumbing & Drain"
OWNER_CELL = "+15865550100"

CUSTOMERS = {
    "faucet": "+15865550101",
    "flood": "+15865550102",
    "silent": "+15865550103",
    "price": "+15865550104",
    "done": "+15865550105",
    "stopped": "+15865550106",
}


def seed_demo():
    cfg = current_app.config
    if cfg["SMS_PROVIDER"] != "console" or cfg["BASE_URL"].startswith("https://"):
        raise RuntimeError("seed-demo only runs on your own computer in simulator mode: its login is public.")
    db = get_db()
    if db.execute("SELECT 1 FROM businesses WHERE owner_email=?", (DEMO_EMAIL,)).fetchone():
        return False
    biz_id = create_business(db, DEMO_NAME, DEMO_EMAIL, DEMO_PASSWORD, OWNER_CELL)
    with tx(db):
        db.execute(
            "UPDATE businesses SET services=?, service_area=?, review_url=? WHERE id=?",
            (
                "Leaks, drains, water heaters, toilets, faucets",
                "Clinton Township, Macomb County",
                "https://example.com/leave-a-review",
                biz_id,
            ),
        )
    biz = db.execute("SELECT * FROM businesses WHERE id=?", (biz_id,)).fetchone()

    def conv_of(phone):
        return db.execute(
            "SELECT c.* FROM conversations c JOIN customers u ON u.id=c.customer_id WHERE u.phone=? AND c.business_id=?",
            (phone, biz_id),
        ).fetchone()

    p = CUSTOMERS
    # Kitchen faucet: texted back, picked time 1 from the offer.
    flow.on_missed_call(db, biz, p["faucet"])
    flow.on_inbound_sms(db, biz, p["faucet"], "Kitchen faucet dripping all night. 4410 Harper Ave")
    flow.on_inbound_sms(db, biz, p["faucet"], "1")
    # Flooded basement: handed straight to the owner.
    flow.on_missed_call(db, biz, p["flood"])
    flow.on_inbound_sms(db, biz, p["flood"], "my basement is flooding, water everywhere")
    # Missed call, no reply yet.
    flow.on_missed_call(db, biz, p["silent"])
    # Price question: no price quoted, times offered, owner alerted.
    flow.on_missed_call(db, biz, p["price"])
    flow.on_inbound_sms(db, biz, p["price"], "How much to replace a 40 gallon water heater?")
    # A finished job booked from the web page.
    flow.on_missed_call(db, biz, p["done"])
    flow.on_inbound_sms(db, biz, p["done"], "Toilet keeps running")
    conv = conv_of(p["done"])
    slot = slotmod.open_slots(db, biz, utcnow())[2]
    job_id = flow.book_from_web(db, biz, conv["id"], iso(slot), "Dana Rowe", "218 Cass St, Clinton Twp", "Toilet keeps running")
    flow.mark_done(db, biz, job_id)
    # Someone who opted out.
    flow.on_missed_call(db, biz, p["stopped"])
    flow.on_inbound_sms(db, biz, p["stopped"], "STOP")

    # Spread the activity over the last day so the board does not read "just now" on every row.
    ago = {"flood": 6, "faucet": 38, "price": 95, "silent": 210, "stopped": 480, "done": 1500}
    now = utcnow()
    with tx(db):
        for key, minutes in ago.items():
            conv = conv_of(p[key])
            then = iso(now - timedelta(minutes=minutes))
            db.execute("UPDATE conversations SET updated_at=?, last_texted_at=CASE WHEN last_texted_at IS NULL THEN NULL ELSE ? END WHERE id=?", (then, then, conv["id"]))
            db.execute("UPDATE messages SET created_at=? WHERE conversation_id=?", (then, conv["id"]))
            db.execute("UPDATE calls SET created_at=? WHERE customer_id=?", (then, conv["customer_id"]))
            db.execute("UPDATE jobs SET created_at=? WHERE conversation_id=?", (then, conv["id"]))
        # The finished job happened yesterday, not in the future.
        done = conv_of(p["done"])
        yesterday = now - timedelta(hours=26)
        db.execute(
            "UPDATE jobs SET starts_at=?, ends_at=?, done_at=? WHERE conversation_id=?",
            (iso(yesterday), iso(yesterday + timedelta(hours=1)), iso(yesterday + timedelta(hours=1)), done["id"]),
        )
    return True
