"""Sending texts safely.

Every outgoing text goes through enqueue(), which applies the safety limits and
writes a row to the messages table. deliver() then sends the queued rows. A text
that is blocked is still recorded, with the reason, so nothing disappears quietly.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from flask import current_app

from .clock import iso, utcnow

log = logging.getLogger("jobping.texting")

# Texts the assistant sends on its own. Pausing the assistant stops all of these.
AUTOMATIC_KINDS = {"first_text", "offer", "handoff", "reminder", "review", "help", "ack"}


def normalize_phone(raw):
    """Return a +E.164 number, or None when it does not look like a real phone number."""
    if not raw:
        return None
    text = str(raw).strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        number = "+" + digits
    elif len(digits) == 10:
        number = "+1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        number = "+" + digits
    else:
        return None
    if number.startswith("+1"):
        if len(number) != 12 or number[2] not in "23456789":
            return None
    elif not 9 <= len(number) <= 16:
        return None
    return number


def normalize_us(raw):
    """A US or Canadian number you can safely forward calls and send texts to, or None."""
    number = normalize_phone(raw)
    if number is None or not number.startswith("+1") or number[2:5] == "900":
        return None
    return number


def fmt_phone(number):
    if number and number.startswith("+1") and len(number) == 12:
        return "(%s) %s-%s" % (number[2:5], number[5:8], number[8:])
    return number or ""


class SendError(Exception):
    def __init__(self, code=None, message=""):
        super().__init__(message or str(code))
        self.code = code


class ConsoleProvider:
    """Pretends to send. Used for the simulator and for tests."""

    name = "console"

    def send(self, from_phone, to_phone, body):
        return "sim_" + secrets.token_hex(6)


class TwilioProvider:
    name = "twilio"

    def __init__(self, account_sid, auth_token):
        self.sid = account_sid
        self.token = auth_token

    def send(self, from_phone, to_phone, body):
        url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % self.sid
        data = urllib.parse.urlencode({"From": from_phone, "To": to_phone, "Body": body}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        auth = base64.b64encode(("%s:%s" % (self.sid, self.token)).encode()).decode()
        req.add_header("Authorization", "Basic " + auth)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.load(resp)["sid"]
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}")
            except ValueError:
                detail = {}
            raise SendError(detail.get("code"), detail.get("message", "")) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SendError(None, "network error") from exc


def get_provider():
    ext = current_app.extensions
    if "jobping_provider" not in ext:
        cfg = current_app.config
        if cfg["SMS_PROVIDER"] == "twilio":
            ext["jobping_provider"] = TwilioProvider(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])
        else:
            ext["jobping_provider"] = ConsoleProvider()
    return ext["jobping_provider"]


def twilio_signature_ok(auth_token, url, form, signature):
    """Check that a webhook really came from Twilio (X-Twilio-Signature)."""
    if not signature or not auth_token:
        return False
    text = url
    for key in sorted(form.keys()):
        for value in sorted(form.getlist(key)):
            text += key + value
    digest = hmac.new(auth_token.encode(), text.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def _count_recent(db, sql, args, since):
    return db.execute(sql, args + (iso(since),)).fetchone()[0]


def enqueue(db, biz, conv_id, to_phone, body, kind, *, customer=None, owner_alert=False):
    """Record a text to send. Call inside a transaction. Returns the message id."""
    cfg = current_app.config
    now = utcnow()
    day_ago = now - timedelta(days=1)
    from_phone = biz["text_number"] or ""
    blocked = None
    if not cfg["SMS_ENABLED"]:
        blocked = "disabled"
    elif not to_phone.startswith(cfg["ALLOWED_PREFIX"]):
        blocked = "region"
    elif owner_alert:
        used = _count_recent(
            db,
            "SELECT COUNT(*) FROM messages WHERE business_id=? AND kind='alert' "
            "AND status NOT LIKE 'blocked%' AND created_at>?",
            (biz["id"],),
            day_ago,
        )
        if used >= cfg["MAX_OWNER_ALERTS_PER_BUSINESS_PER_DAY"]:
            blocked = "alert_cap"
    else:
        if customer is not None and customer["opted_out"]:
            blocked = "opted_out"
        elif kind in AUTOMATIC_KINDS and not biz["assistant_on"]:
            blocked = "assistant_paused"
        else:
            to_count = _count_recent(
                db,
                "SELECT COUNT(*) FROM messages WHERE business_id=? AND to_phone=? AND direction='out' "
                "AND kind!='alert' AND status NOT LIKE 'blocked%' AND created_at>?",
                (biz["id"], to_phone),
                day_ago,
            )
            auto_count = 0
            if kind in AUTOMATIC_KINDS:
                auto_count = _count_recent(
                    db,
                    "SELECT COUNT(*) FROM messages WHERE business_id=? AND direction='out' AND kind IN (%s) "
                    "AND status NOT LIKE 'blocked%%' AND created_at>?" % ",".join("?" * len(AUTOMATIC_KINDS)),
                    (biz["id"],) + tuple(sorted(AUTOMATIC_KINDS)),
                    day_ago,
                )
            if to_count >= cfg["MAX_TEXTS_PER_NUMBER_PER_DAY"]:
                blocked = "number_cap"
            elif auto_count >= cfg["MAX_AUTO_TEXTS_PER_BUSINESS_PER_DAY"]:
                blocked = "business_cap"
            elif kind == "first_text":
                firsts = _count_recent(
                    db,
                    "SELECT COUNT(*) FROM messages WHERE business_id=? AND kind='first_text' "
                    "AND status NOT LIKE 'blocked%' AND created_at>?",
                    (biz["id"],),
                    day_ago,
                )
                if firsts >= cfg["MAX_FIRST_TEXTS_PER_BUSINESS_PER_DAY"]:
                    blocked = "first_text_cap"
    if not blocked and cfg["SMS_PROVIDER"] == "twilio" and not from_phone:
        blocked = "no_number"
    status = "blocked:" + blocked if blocked else "queued"
    if blocked:
        log.warning("text blocked (%s) for business %s", blocked, biz["id"])
    cur = db.execute(
        "INSERT INTO messages(business_id, conversation_id, direction, kind, from_phone, to_phone, body, "
        "status, simulated, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            biz["id"],
            conv_id,
            "out",
            "alert" if owner_alert else kind,
            from_phone,
            to_phone,
            body[:600],
            status,
            1 if cfg["SMS_PROVIDER"] == "console" else 0,
            iso(now),
        ),
    )
    return cur.lastrowid


def deliver(db, message_ids):
    """Send queued messages. Never raises: a failed send is recorded, not thrown."""
    provider = get_provider()
    for mid in [m for m in message_ids if m]:
        claimed = db.execute("UPDATE messages SET status='sending' WHERE id=? AND status='queued'", (mid,))
        if claimed.rowcount != 1:
            continue
        row = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        try:
            provider_id = provider.send(row["from_phone"], row["to_phone"], row["body"])
            db.execute("UPDATE messages SET status='sent', provider_id=? WHERE id=?", (provider_id, mid))
        except SendError as exc:
            db.execute("UPDATE messages SET status=? WHERE id=?", ("failed:%s" % (exc.code or "error"), mid))
            if str(exc.code) == "21610":  # Twilio: this number replied STOP
                db.execute(
                    "UPDATE customers SET opted_out=1 WHERE business_id=? AND phone=?",
                    (row["business_id"], row["to_phone"]),
                )
            log.error("text %s failed: %s", mid, exc)
        except Exception:  # noqa: BLE001 - one bad send must not stop the rest
            db.execute("UPDATE messages SET status='failed:error' WHERE id=?", (mid,))
            log.exception("text %s failed", mid)


def retry_stuck(db):
    """Send texts that were queued but never delivered, for example after a crash."""
    cutoff = utcnow() - timedelta(seconds=90)
    ids = [
        r["id"]
        for r in db.execute("SELECT id FROM messages WHERE status='queued' AND created_at<?", (iso(cutoff),))
    ]
    deliver(db, ids)
    return len(ids)


