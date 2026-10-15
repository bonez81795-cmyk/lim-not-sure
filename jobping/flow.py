"""The conversation: missed call, text back, offer times, book the job, remind, ask for a review.

Rules the assistant never breaks (from the launch plan):
  - It never quotes a price.
  - It never promises an arrival time the owner has not set up as an open slot.
  - It never handles an emergency: it tells the owner right away and stops.
  - It stops texting anyone who replies STOP.
Each public function does its database work in one transaction, then sends the texts.
"""
import json
import re
import secrets
import sqlite3
from datetime import datetime, time, timedelta, timezone

from flask import current_app

from . import slots as slotmod
from . import texting
from .clock import iso, parse, utcnow
from .db import tx
from .texting import fmt_phone, normalize_phone

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
START_WORDS = {"start", "unstop", "yes"}
HELP_WORDS = {"help", "info"}
EMERGENCY = re.compile(
    r"(flood(ed|ing)?|\bburst|bursting|gas (leak|smell|odor)|smell(s|ing)? (of )?gas|sewage|"
    r"sewer (back|overflow)|backing up|backed up|overflow(ing)?|\bno water|water (everywhere|pouring|gushing)|"
    r"pouring|gushing|carbon monoxide|ceiling (collaps|caving)|"
    r"water (is |was )?(coming|leaking|running|dripping|seeping) (through|down|out of|from) (the |my )?(ceiling|wall|floor)|"
    r"(ceiling|wall) (is )?(leaking|dripping|bulging|sagging)|"
    r"(pipe|line|water heater|tank) (has |just )?(broke|broken|burst|froze|frozen|exploded|ruptured)|"
    r"frozen pipe|water heater (is )?(leaking|flooding)|toilet (is )?overflowing|sump (pump )?(failed|not working)|"
    r"raw sewage|no hot water and (leak|flood))",
    re.I,
)
PRICING = re.compile(r"(how much|\bprice|\bpricing|\bcost|\bquote|\bestimate|\bcharge|\brates?\b|\bfees?\b|\$)", re.I)
NUMBER_REPLY = re.compile(
    r"\s*(?:option|number|no\.?|#)?\s*([1-9])\s*(?:please|pls|thanks|thank you)?\s*[.!)]*\s*", re.I
)

# The wording customers receive. Kept in one place so the Setup page shows exactly what is sent.
FIRST_TEXT = (
    "Hi, this is the automated assistant for %s. Sorry we missed your call. "
    "Reply with what you need and your address, or pick a time here: %s "
    "Reply STOP to opt out."
)
CONFIRM_TEXT = (
    "Booked with %s for %s. I'll text a reminder the day before. "
    "Reply CHANGE for a different time, or STOP to opt out."
)
REMINDER_TEXT = (
    "Reminder from %s: your appointment is %s at %s. "
    "Reply CHANGE if you need a different time, or STOP to opt out."
)
REVIEW_TEXT = "Thanks for choosing %s! If we did a good job, a quick review helps a lot: %s Reply STOP to opt out."
REQUEST_TEXT = "Thanks! I've asked %s to confirm %s. You'll get a text as soon as they do."
EMERGENCY_TEXT = (
    "This sounds urgent. I've alerted %s and they will contact you as soon as they can. "
    "If anyone is in danger or you smell gas, leave the building and call 911."
)


# ---------------------------------------------------------------- small helpers
# Curly quotes, dashes and emoji push a text into a different encoding that holds far fewer characters
# per message (so it costs two or three times as much). Alerts quote customers, so tidy them first.
_PUNCT = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u2026": "..."}
)
_EMOJI = re.compile("[\U00010000-\U0010ffff\u200d\ufe0f]")


def short(text, limit):
    text = " ".join(_EMOJI.sub("", (text or "").translate(_PUNCT)).split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def who(customer):
    return customer["name"] or fmt_phone(customer["phone"])


def book_link(conv):
    return "%s/book/%s" % (current_app.config["BASE_URL"], conv["token"])


def ticket_link(conv):
    return "%s/tickets/%d" % (current_app.config["BASE_URL"], conv["id"])


def _fresh(db, table, row_id):
    return db.execute("SELECT * FROM %s WHERE id=?" % table, (row_id,)).fetchone()


def _owned_conv(db, biz, conv_id):
    """A conversation that belongs to this business, or LookupError."""
    conv = db.execute("SELECT * FROM conversations WHERE id=? AND business_id=?", (conv_id, biz["id"])).fetchone()
    if conv is None:
        raise LookupError("conversation not found")
    return conv


def _customer_and_conv(db, biz, phone, now):
    cust = db.execute("SELECT * FROM customers WHERE business_id=? AND phone=?", (biz["id"], phone)).fetchone()
    if cust is None:
        cur = db.execute(
            "INSERT INTO customers(business_id, phone, created_at) VALUES (?,?,?)", (biz["id"], phone, iso(now))
        )
        cust = _fresh(db, "customers", cur.lastrowid)
    conv = db.execute("SELECT * FROM conversations WHERE customer_id=?", (cust["id"],)).fetchone()
    if conv is None:
        cur = db.execute(
            "INSERT INTO conversations(business_id, customer_id, token, created_at, updated_at) VALUES (?,?,?,?,?)",
            (biz["id"], cust["id"], secrets.token_urlsafe(16), iso(now), iso(now)),
        )
        conv = _fresh(db, "conversations", cur.lastrowid)
    return cust, conv


_CONV_FIELDS = {"state", "reason", "problem", "offered_json", "strikes", "last_texted_at"}


def _touch(db, conv_id, now, **fields):
    assert set(fields) <= _CONV_FIELDS
    sets = ["updated_at=?"] + ["%s=?" % k for k in fields]
    db.execute("UPDATE conversations SET %s WHERE id=?" % ", ".join(sets), [iso(now), *fields.values(), conv_id])


def _say(db, biz, conv, cust, body, kind):
    return texting.enqueue(db, biz, conv["id"], cust["phone"], body, kind, customer=cust)


def _alert(db, biz, conv, body):
    return texting.enqueue(db, biz, conv["id"], biz["owner_cell"], short(body, 300), "alert", owner_alert=True)


def _reply_hint(count):
    return {1: "Reply 1 to book it.", 2: "Reply 1 or 2 to book."}.get(count, "Reply 1, 2 or 3 to book.")


# ---------------------------------------------------------------- hand-offs and offers
def _handoff(db, biz, cust, conv, text, reason, now):
    """Stop the assistant and put the owner in charge of this customer."""
    ids = []
    problem = short((conv["problem"] + " | " + text).strip(" |") if text else conv["problem"], 600)
    _touch(db, conv["id"], now, state="needs_owner", reason=reason, problem=problem, offered_json="[]")
    name = biz["name"]
    copy = {
        "emergency": EMERGENCY_TEXT % name,
        "no_slots": "Thanks. I don't see an open time right now, so I've asked %s to text you." % name,
        "stuck": "Thanks. I've passed this to %s and they'll reply soon." % name,
        "owner_request": "No problem. I've asked %s to reach out about a new time." % name,
    }[reason]
    ids.append(_say(db, biz, conv, cust, copy, "handoff"))
    label = {
        "emergency": "URGENT",
        "no_slots": "No open times for",
        "stuck": "Needs you:",
        "owner_request": "Change request from",
    }[reason]
    ids.append(
        _alert(db, biz, conv, '%s %s: "%s" %s' % (label, who(cust), short(text, 140), ticket_link(conv)))
    )
    return ids


def _offer(db, biz, cust, conv, now, intro, strikes=0):
    tz = slotmod.tzinfo(biz)
    picks = slotmod.pick_offers(slotmod.open_slots(db, biz, now), tz)
    if not picks:
        return _handoff(db, biz, cust, conv, conv["problem"], "no_slots", now)
    lines = ["%d) %s" % (i + 1, slotmod.fmt_slot(p, tz)) for i, p in enumerate(picks)]
    body = "%s\n%s\n%s\nOr pick here: %s" % (intro, "\n".join(lines), _reply_hint(len(picks)), book_link(conv))
    _touch(db, conv["id"], now, state="offered", offered_json=json.dumps([iso(p) for p in picks]), strikes=strikes)
    return [_say(db, biz, conv, cust, body, "offer")]


# ---------------------------------------------------------------- booking
def _create_job(db, biz, cust, conv, start, name, address, problem, source, now):
    """Book a slot. Returns (job_id, message_ids), or (None, []) if the time is gone."""
    if start not in slotmod.open_slots(db, biz, now):
        return None, []
    end = start + timedelta(minutes=biz["job_minutes"])
    try:
        cur = db.execute(
            "INSERT INTO jobs(business_id, customer_id, conversation_id, starts_at, ends_at, name, address, "
            "problem, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (biz["id"], cust["id"], conv["id"], iso(start), iso(end), name, address, problem, source, iso(now)),
        )
    except sqlite3.IntegrityError:
        return None, []
    if name:
        db.execute("UPDATE customers SET name=? WHERE id=?", (name, cust["id"]))
        cust = _fresh(db, "customers", cust["id"])
    _touch(db, conv["id"], now, state="booked", reason="", problem=short(problem, 600), offered_json="[]", strikes=0)
    when = slotmod.fmt_slot(start, slotmod.tzinfo(biz))
    confirm = CONFIRM_TEXT % (biz["name"], when)
    urgent = "URGENT? " if EMERGENCY.search(problem or "") else ""
    alert = "%sBooked: %s %s. %s %s" % (urgent, who(cust), when, short(problem, 110), short(address, 60))
    ids = [_say(db, biz, conv, cust, confirm, "confirm"), _alert(db, biz, conv, alert)]
    return cur.lastrowid, ids


def _request_booking(db, biz, cust, conv, start, name, address, problem, source, now):
    """Approve mode: hold the customer's pick until the owner says yes. Nothing is booked yet."""
    if start not in slotmod.open_slots(db, biz, now):
        return None, []
    pending = {"start": iso(start), "name": name, "address": address, "problem": problem, "source": source}
    _touch(db, conv["id"], now, state="awaiting_booking", problem=short(problem, 600), offered_json="[]", strikes=0)
    db.execute("UPDATE conversations SET pending_json=? WHERE id=?", (json.dumps(pending), conv["id"]))
    when = slotmod.fmt_slot(start, slotmod.tzinfo(biz))
    ids = [
        _say(db, biz, conv, cust, REQUEST_TEXT % (biz["name"], when), "ack"),
        _alert(db, biz, conv, "Approve this job? %s wants %s. %s %s" % (who(cust), when, short(problem, 100), ticket_link(conv))),
    ]
    return True, ids


def book_from_web(db, biz, conv_id, start_iso, name, address, problem):
    """The booking page. Raises ValueError for a bad time; returns None if the time was just taken."""
    start = parse(start_iso)
    now = utcnow()
    with tx(db):
        conv = _owned_conv(db, biz, conv_id)
        cust = _fresh(db, "customers", conv["customer_id"])
        if biz["mode"] == "approve":
            job_id, ids = _request_booking(db, biz, cust, conv, start, name, address, problem, "web", now)
            if job_id:
                db.execute("UPDATE customers SET name=? WHERE id=?", (name, cust["id"]))
        else:
            job_id, ids = _create_job(db, biz, cust, conv, start, name, address, problem, "web", now)
    texting.deliver(db, ids)
    return job_id


def _choose(db, biz, cust, conv, number, now):
    offered = json.loads(conv["offered_json"])
    if not 1 <= number <= len(offered):
        if conv["strikes"] + 1 >= 2:
            return _handoff(db, biz, cust, conv, str(number), "stuck", now)
        return _offer(db, biz, cust, conv, now, "Please reply with one of these numbers:", conv["strikes"] + 1)
    if biz["mode"] == "approve":
        ok, ids = _request_booking(db, biz, cust, conv, parse(offered[number - 1]), cust["name"], "", conv["problem"], "text", now)
        if not ok:
            return _offer(db, biz, cust, conv, now, "Sorry, that time was just taken. Next open times:")
        return ids
    job_id, ids = _create_job(db, biz, cust, conv, parse(offered[number - 1]), cust["name"], "", conv["problem"], "text", now)
    if job_id is None:
        return _offer(db, biz, cust, conv, now, "Sorry, that time was just taken. Next open times:")
    return ids


# ---------------------------------------------------------------- entry points: calls and texts
def _is_known(db, cust):
    """A customer the owner has already worked for or spoken to."""
    return bool(
        db.execute("SELECT 1 FROM jobs WHERE customer_id=? AND status IN ('booked','done')", (cust["id"],)).fetchone()
        or db.execute("SELECT 1 FROM calls WHERE customer_id=? AND status='answered'", (cust["id"],)).fetchone()
    )


def on_missed_call(db, biz, caller, call_sid=None):
    phone = normalize_phone(caller)
    if not phone or phone in (biz["owner_cell"], biz["text_number"]):
        return
    now = utcnow()
    ids = []
    with tx(db):
        if call_sid and db.execute("SELECT 1 FROM calls WHERE call_sid=?", (call_sid,)).fetchone():
            return
        cust, conv = _customer_and_conv(db, biz, phone, now)
        db.execute(
            "INSERT INTO calls(business_id, customer_id, status, call_sid, created_at) VALUES (?,?,?,?,?)",
            (biz["id"], cust["id"], "missed", call_sid, iso(now)),
        )
        window = timedelta(minutes=current_app.config["MISSED_CALL_DEDUPE_MINUTES"])
        recent = bool(conv["last_texted_at"]) and parse(conv["last_texted_at"]) > now - window
        busy = conv["state"] in ("booked", "owner_handling", "needs_owner", "awaiting_ok", "awaiting_booking")
        mode = biz["mode"]
        known = bool(biz["skip_known"]) and _is_known(db, cust)
        if cust["opted_out"] or not biz["assistant_on"] or recent or busy:
            _touch(db, conv["id"], now)
        elif known:
            # A past customer: the owner calls them back. The assistant stays out of it.
            ids.append(_alert(db, biz, conv, "Missed call from past customer %s. Call them back: %s" % (who(cust), ticket_link(conv))))
            _touch(db, conv["id"], now, last_texted_at=iso(now))
        elif mode == "notify":
            ids.append(_alert(db, biz, conv, "Missed call from %s. Nothing was sent to them. Call or text them: %s" % (who(cust), ticket_link(conv))))
            _touch(db, conv["id"], now, state="new", last_texted_at=iso(now))
        elif mode == "approve":
            ids.append(_alert(db, biz, conv, "Missed call from %s. Tap to send the text-back, or skip it: %s" % (who(cust), ticket_link(conv))))
            _touch(db, conv["id"], now, state="awaiting_ok", last_texted_at=iso(now))
        else:
            body = FIRST_TEXT % (biz["name"], book_link(conv))
            ids.append(_say(db, biz, conv, cust, body, "first_text"))
            _touch(db, conv["id"], now, state="asked", reason="", strikes=0, last_texted_at=iso(now))
    texting.deliver(db, ids)


def on_answered_call(db, biz, caller, call_sid=None):
    phone = normalize_phone(caller)
    if not phone or phone in (biz["owner_cell"], biz["text_number"]):
        return
    now = utcnow()
    with tx(db):
        if call_sid and db.execute("SELECT 1 FROM calls WHERE call_sid=?", (call_sid,)).fetchone():
            return
        cust, _conv = _customer_and_conv(db, biz, phone, now)
        db.execute(
            "INSERT INTO calls(business_id, customer_id, status, call_sid, created_at) VALUES (?,?,?,?,?)",
            (biz["id"], cust["id"], "answered", call_sid, iso(now)),
        )


def on_inbound_sms(db, biz, from_phone, body, provider_id=None):
    phone = normalize_phone(from_phone)
    if not phone or phone in (biz["owner_cell"], biz["text_number"]):
        return
    text = (body or "").strip()[:1000]
    now = utcnow()
    ids = []
    with tx(db):
        if provider_id and db.execute(
            "SELECT 1 FROM messages WHERE direction='in' AND provider_id=?", (provider_id,)
        ).fetchone():
            return  # the phone company sent the same text twice
        cust, conv = _customer_and_conv(db, biz, phone, now)
        db.execute(
            "INSERT INTO messages(business_id, conversation_id, direction, kind, from_phone, to_phone, body, "
            "status, provider_id, simulated, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                biz["id"], conv["id"], "in", "inbound", phone, biz["text_number"] or "", text, "received",
                provider_id, 1 if current_app.config["SMS_PROVIDER"] == "console" else 0, iso(now),
            ),
        )
        ids = _route_inbound(db, biz, cust, conv, text, now)
    texting.deliver(db, ids)


def _route_inbound(db, biz, cust, conv, text, now):
    ids = []
    word = re.sub(r"[^a-z]", "", text.lower())
    state = conv["state"]

    if word in STOP_WORDS:
        db.execute("UPDATE customers SET opted_out=1 WHERE id=?", (cust["id"],))
        upcoming = db.execute(
            "SELECT starts_at FROM jobs WHERE conversation_id=? AND status='booked' AND starts_at>?",
            (conv["id"], iso(now)),
        ).fetchone()
        if upcoming:
            when = slotmod.fmt_slot(parse(upcoming["starts_at"]), slotmod.tzinfo(biz))
            ids.append(_alert(db, biz, conv, "%s opted out of texts but has a job booked for %s. Call them to confirm." % (who(cust), when)))
        # Someone who was only being texted back is done; an open emergency or booked job stays as it is.
        _touch(db, conv["id"], now, state="closed" if state in ("new", "asked", "offered") else state)
        return ids
    if cust["opted_out"]:
        if word in START_WORDS:
            db.execute("UPDATE customers SET opted_out=0 WHERE id=?", (cust["id"],))
        _touch(db, conv["id"], now)
        return ids
    if word in HELP_WORDS:
        help_text = "%s: automated booking assistant. We'll follow up shortly. Reply STOP to opt out. Message and data rates may apply." % biz["name"]
        ids.append(_say(db, biz, conv, cust, help_text, "help"))
        _touch(db, conv["id"], now)
        return ids

    urgent = bool(EMERGENCY.search(text))
    if state == "owner_handling":
        ids.append(_alert(db, biz, conv, '%s%s: "%s" %s' % ("URGENT " if urgent else "", who(cust), short(text, 140), ticket_link(conv))))
        _touch(db, conv["id"], now)
        return ids
    if urgent:
        return _handoff(db, biz, cust, conv, text, "emergency", now)
    if state in ("needs_owner", "awaiting_ok", "awaiting_booking"):
        ids.append(_alert(db, biz, conv, '%s replied: "%s" %s' % (who(cust), short(text, 140), ticket_link(conv))))
        _touch(db, conv["id"], now)
        return ids
    if biz["mode"] != "auto" and state in ("new", "closed"):
        # In "tell me" and "approve" modes the assistant never starts a conversation on its own.
        ids.append(_alert(db, biz, conv, '%s texted: "%s" %s' % (who(cust), short(text, 140), ticket_link(conv))))
        _touch(db, conv["id"], now, problem=short(text, 600))
        return ids
    if state == "booked":
        if word == "change":
            return _handoff(db, biz, cust, conv, text, "owner_request", now)
        ids.append(_alert(db, biz, conv, '%s says: "%s" %s' % (who(cust), short(text, 140), ticket_link(conv))))
        ids.append(_say(db, biz, conv, cust, "Got it. I've passed that to %s." % biz["name"], "ack"))
        _touch(db, conv["id"], now)
        return ids

    # New, asked, offered or closed: the customer is describing the job or picking a time.
    if state == "offered":
        match = NUMBER_REPLY.fullmatch(text)
        if match:
            return _choose(db, biz, cust, conv, int(match.group(1)), now)
        strikes = conv["strikes"] + 1
        if strikes >= 2:
            return _handoff(db, biz, cust, conv, text, "stuck", now)
    else:
        strikes = 0
    problem = text if state == "closed" or not conv["problem"] else short(conv["problem"] + " | " + text, 600)
    _touch(db, conv["id"], now, problem=problem)
    conv = _fresh(db, "conversations", conv["id"])
    if PRICING.search(text):
        ids.append(_alert(db, biz, conv, 'Price question from %s: "%s" I did not quote a price. %s' % (who(cust), short(text, 120), ticket_link(conv))))
        intro = (
            "Prices depend on the job, so I can't quote one here. I've asked %s to follow up. "
            "To book a visit, here are the next open times:" % biz["name"]
        )
    elif state == "offered":
        intro = "Just reply with a number to pick a time:"
    else:
        intro = "Thanks, got it. Next open times:"
    ids += _offer(db, biz, cust, conv, now, intro, strikes)
    return ids


# ---------------------------------------------------------------- owner actions
def take_over(db, biz, conv_id):
    with tx(db):
        _owned_conv(db, biz, conv_id)
        _touch(db, conv_id, utcnow(), state="owner_handling")


def resolve(db, biz, conv_id):
    """The owner says they've dealt with it. The assistant picks up again if the customer texts."""
    now = utcnow()
    with tx(db):
        _owned_conv(db, biz, conv_id)
        booked = db.execute(
            "SELECT 1 FROM jobs WHERE conversation_id=? AND status='booked'", (conv_id,)
        ).fetchone()
        _touch(db, conv_id, now, state="booked" if booked else "closed", reason="", offered_json="[]", strikes=0)


def send_manual(db, biz, conv_id, body):
    now = utcnow()
    with tx(db):
        conv = _owned_conv(db, biz, conv_id)
        cust = _fresh(db, "customers", conv["customer_id"])
        mid = texting.enqueue(db, biz, conv["id"], cust["phone"], body, "manual", customer=cust)
        _touch(db, conv["id"], now, state="owner_handling")
    texting.deliver(db, [mid])


def mark_done(db, biz, job_id):
    now = utcnow()
    with tx(db):
        job = db.execute("SELECT * FROM jobs WHERE id=? AND business_id=? AND status='booked'", (job_id, biz["id"])).fetchone()
        if job is None:
            return False
        db.execute("UPDATE jobs SET status='done', done_at=? WHERE id=?", (iso(now), job_id))
        _touch(db, job["conversation_id"], now, state="closed", reason="", offered_json="[]")
    return True


def cancel_job(db, biz, job_id, notify):
    now = utcnow()
    ids = []
    with tx(db):
        job = db.execute("SELECT * FROM jobs WHERE id=? AND business_id=? AND status='booked'", (job_id, biz["id"])).fetchone()
        if job is None:
            return False
        db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
        conv = _fresh(db, "conversations", job["conversation_id"])
        cust = _fresh(db, "customers", job["customer_id"])
        _touch(db, conv["id"], now, state="closed", reason="", offered_json="[]")
        if notify:
            when = slotmod.fmt_slot(parse(job["starts_at"]), slotmod.tzinfo(biz))
            body = "Your appointment with %s on %s was cancelled. Reply here if you'd like to book a new time." % (biz["name"], when)
            ids.append(texting.enqueue(db, biz, conv["id"], cust["phone"], body, "manual", customer=cust))
    texting.deliver(db, ids)
    return True



# ---------------------------------------------------------------- owner approval (approve mode)
def approve_text(db, biz, conv_id):
    """One tap: send the text-back the owner was asked about."""
    now = utcnow()
    ids = []
    with tx(db):
        conv = _owned_conv(db, biz, conv_id)
        if conv["state"] != "awaiting_ok":
            return False
        cust = _fresh(db, "customers", conv["customer_id"])
        if cust["opted_out"]:
            _touch(db, conv_id, now, state="closed")
            return False
        ids.append(_say(db, biz, conv, cust, FIRST_TEXT % (biz["name"], book_link(conv)), "first_text"))
        _touch(db, conv_id, now, state="asked", reason="", strikes=0, last_texted_at=iso(now))
    texting.deliver(db, ids)
    return True


def skip_text(db, biz, conv_id):
    with tx(db):
        conv = _owned_conv(db, biz, conv_id)
        if conv["state"] != "awaiting_ok":
            return False
        _touch(db, conv_id, utcnow(), state="closed")
    return True


def approve_booking(db, biz, conv_id):
    """One tap: book the time the customer picked. If it was taken meanwhile, the customer gets new times."""
    now = utcnow()
    ids = []
    with tx(db):
        conv = _owned_conv(db, biz, conv_id)
        if conv["state"] != "awaiting_booking" or not conv["pending_json"]:
            return False
        cust = _fresh(db, "customers", conv["customer_id"])
        p = json.loads(conv["pending_json"])
        db.execute("UPDATE conversations SET pending_json='' WHERE id=?", (conv_id,))
        job_id, ids = _create_job(db, biz, cust, conv, parse(p["start"]), p["name"], p["address"], p["problem"], p["source"], now)
        if job_id is None:
            conv = _fresh(db, "conversations", conv_id)
            ids = _offer(db, biz, cust, conv, now, "Sorry, that time was just taken. Next open times:")
    texting.deliver(db, ids)
    return True


def decline_booking(db, biz, conv_id):
    """The owner can't make that time: the customer is offered the next open times."""
    now = utcnow()
    with tx(db):
        conv = _owned_conv(db, biz, conv_id)
        if conv["state"] != "awaiting_booking":
            return False
        cust = _fresh(db, "customers", conv["customer_id"])
        db.execute("UPDATE conversations SET pending_json='' WHERE id=?", (conv_id,))
        ids = _offer(db, biz, cust, conv, now, "Sorry, %s can't make that time. Next open times:" % biz["name"])
    texting.deliver(db, ids)
    return True


# ---------------------------------------------------------------- the missed-call count
def missed_report(db, biz, days=30):
    """What the owner's phone missed, and what happened to those callers."""
    since = iso(utcnow() - timedelta(days=days))
    q = lambda sql, *a: db.execute(sql, (biz["id"], since) + a).fetchone()[0]
    missed = q("SELECT COUNT(*) FROM calls WHERE business_id=? AND status='missed' AND created_at>?")
    callers = q("SELECT COUNT(DISTINCT customer_id) FROM calls WHERE business_id=? AND status='missed' AND created_at>?")
    texted = q(
        "SELECT COUNT(DISTINCT conversation_id) FROM messages WHERE business_id=? AND kind='first_text' "
        "AND status NOT LIKE 'blocked%' AND created_at>?"
    )
    replied = q(
        "SELECT COUNT(DISTINCT conversation_id) FROM messages WHERE business_id=? AND direction='in' AND created_at>?"
    )
    booked = q("SELECT COUNT(DISTINCT customer_id) FROM jobs WHERE business_id=? AND created_at>? AND status!='cancelled'")
    return {"days": days, "missed": missed, "callers": callers, "texted": texted, "replied": replied, "booked": booked}

# ---------------------------------------------------------------- scheduled work
def run_due(db):
    """Day-before reminders and review requests. Safe to run often and from several workers."""
    now = utcnow()
    ids = []
    cache = {}

    def biz_of(biz_id):
        if biz_id not in cache:
            cache[biz_id] = _fresh(db, "businesses", biz_id)
        return cache[biz_id]

    with tx(db):
        for job in db.execute(
            "SELECT * FROM jobs WHERE status='booked' AND reminder_sent_at IS NULL AND starts_at>?", (iso(now),)
        ).fetchall():
            biz = biz_of(job["business_id"])
            tz = slotmod.tzinfo(biz)
            start_local = parse(job["starts_at"]).astimezone(tz)
            due = datetime.combine(start_local.date() - timedelta(days=1), time(17, 0), tzinfo=tz).astimezone(timezone.utc)
            local_now = now.astimezone(tz)
            if now < due or not 8 <= local_now.hour < 21:
                continue  # not time yet, or outside the 8 AM to 9 PM window for reminders
            db.execute("UPDATE jobs SET reminder_sent_at=? WHERE id=?", (iso(now), job["id"]))
            if parse(job["created_at"]) >= due:
                continue  # booked after the reminder time, so the confirmation is reminder enough
            conv = _fresh(db, "conversations", job["conversation_id"])
            cust = _fresh(db, "customers", job["customer_id"])
            day = "today" if start_local.date() == local_now.date() else "tomorrow"
            body = REMINDER_TEXT % (biz["name"], day, slotmod.clock_text(start_local))
            ids.append(_say(db, biz, conv, cust, body, "reminder"))

        recent = iso(now - timedelta(days=3))
        for job in db.execute(
            "SELECT * FROM jobs WHERE status='done' AND review_sent_at IS NULL AND done_at>?", (recent,)
        ).fetchall():
            biz = biz_of(job["business_id"])
            if not biz["review_url"] or now < parse(job["done_at"]) + timedelta(hours=2):
                continue
            if not 9 <= now.astimezone(slotmod.tzinfo(biz)).hour < 20:
                continue
            db.execute("UPDATE jobs SET review_sent_at=? WHERE id=?", (iso(now), job["id"]))
            conv = _fresh(db, "conversations", job["conversation_id"])
            cust = _fresh(db, "customers", job["customer_id"])
            body = REVIEW_TEXT % (biz["name"], biz["review_url"])
            ids.append(_say(db, biz, conv, cust, body, "review"))
    texting.deliver(db, ids)
    texting.retry_stuck(db)
    return len(ids)


def preview_texts(biz):
    """What a customer reads, in order, with example times. Uses the same wording the app sends."""
    name, base = biz["name"], current_app.config["BASE_URL"]
    link = "%s/book/EXAMPLE" % base
    return [
        ("Right after a missed call", FIRST_TEXT % (name, link)),
        ("When they describe the problem", "Thanks. Here are the next open times:\n1) Tue 9:00 AM\n2) Tue 1:00 PM\n3) Wed 10:00 AM\n"
         "Reply 1, 2 or 3 to book.\nOr pick here: " + link),
        ("When they pick a time", CONFIRM_TEXT % (name, "Tue 9:00 AM")),
        ("The day before", REMINDER_TEXT % (name, "tomorrow", "9:00 AM")),
        ("If the words sound like an emergency (flood, burst, gas...)", EMERGENCY_TEXT % name),
        ("After the job, if you set a review link", REVIEW_TEXT % (name, "https://example.com/review")),
    ]
