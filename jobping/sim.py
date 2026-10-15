"""The simulator: pretend to be a customer and watch what the business sends, with a fake clock.

Nothing here sends a real text. It only exists when SIMULATOR is on (the default for local use).
"""
from flask import Blueprint, g, jsonify, redirect, render_template, request, url_for

from . import flow
from .auth import login_required
from .clock import utcnow
from .db import get_db, tx
from .slots import fmt_slot, tzinfo
from .texting import fmt_phone, normalize_phone

bp = Blueprint("sim", __name__)

DEFAULT_PHONE = "+15865550142"
SKIPS = {30: "30 minutes", 60: "1 hour", 180: "3 hours", 1440: "1 day"}


def _wants_json():
    return request.headers.get("X-Requested-With") == "fetch"


def _reply(ok=True, error="", phone=DEFAULT_PHONE):
    if _wants_json():
        return (jsonify(ok=ok, error=error), 200 if ok else 400)
    return redirect(url_for("sim.page", phone=phone))


def _customer_phone():
    return normalize_phone(request.form.get("phone", ""))


@bp.get("/sim")
@login_required
def page():
    phone = normalize_phone(request.args.get("phone", "")) or DEFAULT_PHONE
    return render_template("sim.html", phone=phone, phone_pretty=fmt_phone(phone), skips=SKIPS)


@bp.post("/sim/missed-call")
@login_required
def missed_call():
    phone = _customer_phone()
    if phone is None or phone in (g.biz["owner_cell"], g.biz["text_number"]):
        return _reply(False, "Use a different phone number for the pretend customer.")
    flow.on_missed_call(get_db(), g.biz, phone)
    return _reply(phone=phone)


@bp.post("/sim/answered-call")
@login_required
def answered_call():
    phone = _customer_phone()
    if phone is None:
        return _reply(False, "Enter a valid phone number.")
    flow.on_answered_call(get_db(), g.biz, phone)
    return _reply(phone=phone)


@bp.post("/sim/text")
@login_required
def text():
    phone = _customer_phone()
    body = request.form.get("body", "").strip()
    if phone is None or phone in (g.biz["owner_cell"], g.biz["text_number"]):
        return _reply(False, "Use a different phone number for the pretend customer.")
    if not body:
        return _reply(False, "Type a message.", phone)
    flow.on_inbound_sms(get_db(), g.biz, phone, body)
    return _reply(phone=phone)


@bp.post("/sim/skip")
@login_required
def skip():
    try:
        minutes = int(request.form.get("minutes", ""))
    except ValueError:
        minutes = 0
    if minutes not in SKIPS:
        return _reply(False, "Pick one of the time jumps.")
    db = get_db()
    with tx(db):
        row = db.execute("SELECT value FROM settings WHERE key='sim_offset_minutes'").fetchone()
        total = (int(row["value"]) if row else 0) + minutes
        db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('sim_offset_minutes', ?)", (str(total),))
    flow.run_due(db)
    return _reply(phone=_customer_phone() or DEFAULT_PHONE)


@bp.post("/sim/run")
@login_required
def run():
    flow.run_due(get_db())
    return _reply(phone=_customer_phone() or DEFAULT_PHONE)


@bp.post("/sim/reset-clock")
@login_required
def reset_clock():
    db = get_db()
    with tx(db):
        db.execute("DELETE FROM settings WHERE key='sim_offset_minutes'")
    return _reply(phone=_customer_phone() or DEFAULT_PHONE)


def _bubble(row):
    status = row["status"]
    note = ""
    if status.startswith("blocked:"):
        note = "Not sent: " + status.split(":", 1)[1].replace("_", " ")
    elif status.startswith("failed"):
        note = "Failed to send"
    return {
        "id": row["id"],
        "mine": row["direction"] == "in",  # the pretend customer's own texts sit on the right
        "kind": row["kind"],
        "body": row["body"],
        "note": note,
    }


@bp.get("/sim/feed")
@login_required
def feed():
    db, biz = get_db(), g.biz
    phone = normalize_phone(request.args.get("phone", "")) or DEFAULT_PHONE
    cust = db.execute("SELECT * FROM customers WHERE business_id=? AND phone=?", (biz["id"], phone)).fetchone()
    customer, book_path, state = [], "", ""
    if cust:
        conv = db.execute("SELECT * FROM conversations WHERE customer_id=?", (cust["id"],)).fetchone()
        state, book_path = conv["state"], "/book/" + conv["token"]
        rows = db.execute(
            "SELECT * FROM messages WHERE conversation_id=? AND kind!='alert' ORDER BY id", (conv["id"],)
        ).fetchall()
        customer = [_bubble(r) for r in rows]
    alerts = db.execute(
        "SELECT * FROM messages WHERE business_id=? AND kind='alert' ORDER BY id DESC LIMIT 30", (biz["id"],)
    ).fetchall()
    return jsonify(
        now=fmt_slot(utcnow(), tzinfo(biz)),
        customer=customer,
        owner=[_bubble(r) for r in reversed(alerts)],
        book_path=book_path,
        state=state,
    )
