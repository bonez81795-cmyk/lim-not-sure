"""Webhooks that Twilio calls: a phone call came in, a call ended, a text came in."""
import hmac
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

from flask import Blueprint, Response, abort, current_app, request

from . import flow
from .db import get_db
from .texting import normalize_phone, twilio_signature_ok

bp = Blueprint("hooks", __name__)

EMPTY = "<Response/>"


def twiml(inner=""):
    body = '<?xml version="1.0" encoding="UTF-8"?><Response>%s</Response>' % inner
    return Response(body, mimetype="text/xml")


def verify_request():
    """Only Twilio may call these. Locally, the simulator mode accepts unsigned calls."""
    cfg = current_app.config
    if cfg["SMS_PROVIDER"] == "twilio":
        url = cfg["BASE_URL"] + request.path
        if request.query_string:
            url += "?" + request.query_string.decode("utf-8", "replace")
        if not twilio_signature_ok(
            cfg["TWILIO_AUTH_TOKEN"], url, request.form, request.headers.get("X-Twilio-Signature", "")
        ):
            abort(403)
    elif not cfg["SIMULATOR"]:
        abort(403)


def _business(number):
    phone = normalize_phone(number)
    if not phone:
        return None
    return get_db().execute("SELECT * FROM businesses WHERE text_number=?", (phone,)).fetchone()


def _business_by_id(raw):
    try:
        return get_db().execute("SELECT * FROM businesses WHERE id=?", (int(raw),)).fetchone()
    except (TypeError, ValueError):
        return None


@bp.post("/hooks/twilio/voice")
def voice():
    """A customer called the business's number. Ring the owner's cell for 20 seconds."""
    verify_request()
    biz = _business(request.form.get("To") or request.form.get("Called"))
    if biz is None:
        return twiml("<Reject/>")
    base = current_app.config["BASE_URL"] + "/hooks/twilio"
    caller = quote(request.form.get("From", ""), safe="")
    cell = escape(biz["owner_cell"])
    if current_app.config["SCREEN_CALLS"]:
        screen = quoteattr("%s/screen?b=%d&c=%s" % (base, biz["id"], caller))
        number = "<Number url=%s>%s</Number>" % (screen, cell)
    else:
        number = "<Number>%s</Number>" % cell
    action = quoteattr(base + "/voice-done")
    return twiml('<Dial timeout="20" answerOnBridge="true" action=%s>%s</Dial>' % (action, number))


@bp.post("/hooks/twilio/screen")
def screen():
    """Plays to the owner when their phone picks up: press a key to take the call.
    Voicemail cannot press a key, so a call that lands in voicemail still counts as missed."""
    verify_request()
    biz = _business_by_id(request.args.get("b"))
    if biz is None:
        return twiml("<Hangup/>")
    base = current_app.config["BASE_URL"] + "/hooks/twilio"
    action = quoteattr("%s/screen-ok?b=%d&c=%s" % (base, biz["id"], quote(request.args.get("c", ""), safe="")))
    say = escape("Call for %s. Press any key to answer." % biz["name"])
    return twiml('<Gather numDigits="1" timeout="6" action=%s><Say>%s</Say></Gather><Hangup/>' % (action, say))


@bp.post("/hooks/twilio/screen-ok")
def screen_ok():
    """The owner pressed a key, so a person is on the line: record the call as answered."""
    verify_request()
    biz = _business_by_id(request.args.get("b"))
    if biz is not None:
        flow.on_answered_call(get_db(), biz, request.args.get("c", ""), request.form.get("ParentCallSid"))
    return twiml()  # an empty reply tells Twilio to connect the two people


@bp.post("/hooks/twilio/voice-done")
def voice_done():
    """The ring ended. If nobody took the call, text the caller."""
    verify_request()
    biz = _business(request.form.get("To") or request.form.get("Called"))
    if biz is None:
        return twiml()
    db = get_db()
    caller, sid = request.form.get("From", ""), request.form.get("CallSid")
    if not current_app.config["SCREEN_CALLS"] and request.form.get("DialCallStatus") == "completed":
        flow.on_answered_call(db, biz, caller, sid)
        return twiml()
    flow.on_missed_call(db, biz, caller, sid)  # does nothing if the owner already took this call
    say = "Sorry, we can't come to the phone. You'll get a text from us in a moment so you can book a time."
    return twiml("<Say>%s</Say><Hangup/>" % escape(say))


@bp.post("/hooks/twilio/sms")
def sms():
    verify_request()
    biz = _business(request.form.get("To"))
    if biz is not None:
        flow.on_inbound_sms(
            get_db(), biz, request.form.get("From", ""), request.form.get("Body", ""), request.form.get("MessageSid")
        )
    return Response(EMPTY, mimetype="text/xml")  # an empty reply: we send our own texts


@bp.post("/internal/tick")
def tick():
    """For a scheduler outside the app (a cron job pinging this every few minutes)."""
    secret = current_app.config["CRON_SECRET"]
    if not secret:
        abort(404)
    sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(sent.encode(), secret.encode()):
        abort(403)
    count = flow.run_due(get_db())
    return {"ok": True, "texts_queued": count}
