"""The owner's screens: the board, a customer's ticket, setup and the calendar feed."""
import json
import re
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, url_for

from . import flow, texting
from .auth import login_required
from .clock import iso, parse, utcnow
from .db import get_db, tx
from .ics import build_calendar, feed_jobs
from .security import limiter, new_token
from .slots import DAY_NAMES, DAYS, fmt_slot, tzinfo
from .texting import normalize_us

bp = Blueprint("owner", __name__)

FILTERS = [
    ("needs", "Needs you"),
    ("open", "Open"),
    ("booked", "Booked"),
    ("closed", "Closed"),
    ("all", "All"),
]
FILTER_STATES = {
    "needs": ("needs_owner", "awaiting_ok", "awaiting_booking"),
    "open": ("new", "asked", "offered", "owner_handling"),
    "booked": ("booked",),
    "closed": ("closed",),
}
TIMEZONES = [
    ("America/New_York", "Eastern (Detroit, New York)"),
    ("America/Detroit", "Eastern, Michigan"),
    ("America/Chicago", "Central"),
    ("America/Denver", "Mountain"),
    ("America/Phoenix", "Arizona (no daylight saving)"),
    ("America/Los_Angeles", "Pacific"),
    ("America/Anchorage", "Alaska"),
    ("Pacific/Honolulu", "Hawaii"),
]
JOB_LENGTHS = [30, 45, 60, 90, 120, 180, 240]
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _conv_or_404(db, conv_id):
    row = db.execute(
        "SELECT c.*, cu.name AS cust_name, cu.phone AS cust_phone, cu.opted_out AS cust_opted_out "
        "FROM conversations c JOIN customers cu ON cu.id=c.customer_id WHERE c.id=? AND c.business_id=?",
        (conv_id, g.biz["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return row


# ---------------------------------------------------------------- board
@bp.route("/")
def home():
    return redirect(url_for("owner.dashboard" if g.get("biz") else "auth.login"))


@bp.get("/dashboard")
@login_required
def dashboard():
    db, biz = get_db(), g.biz
    show = request.args.get("show", "needs")
    if show not in dict(FILTERS):
        show = "needs"

    counts = {key: 0 for key, _ in FILTERS}
    for row in db.execute("SELECT state, COUNT(*) AS n FROM conversations WHERE business_id=? GROUP BY state", (biz["id"],)):
        for key, states in FILTER_STATES.items():
            if row["state"] in states:
                counts[key] += row["n"]
        counts["all"] += row["n"]
    if show == "needs" and counts["needs"] == 0 and counts["all"]:
        show = "open" if counts["open"] else "all"

    sql = (
        "SELECT c.id, c.state, c.reason, c.problem, c.updated_at, cu.name, cu.phone, cu.opted_out, "
        "(SELECT starts_at FROM jobs j WHERE j.conversation_id=c.id AND j.status='booked' ORDER BY starts_at LIMIT 1) AS next_job "
        "FROM conversations c JOIN customers cu ON cu.id=c.customer_id WHERE c.business_id=?"
    )
    args = [biz["id"]]
    if show != "all":
        states = FILTER_STATES[show]
        sql += " AND c.state IN (%s)" % ",".join("?" * len(states))
        args += states
    sql += " ORDER BY CASE c.state WHEN 'needs_owner' THEN 0 WHEN 'awaiting_booking' THEN 1 WHEN 'awaiting_ok' THEN 2 ELSE 3 END, c.updated_at DESC LIMIT 100"
    tickets = db.execute(sql, args).fetchall()

    now = utcnow()
    tz = tzinfo(biz)
    week_ago = iso(now - timedelta(days=7))
    stats = {
        "missed": db.execute(
            "SELECT COUNT(*) FROM calls WHERE business_id=? AND status='missed' AND created_at>?", (biz["id"], week_ago)
        ).fetchone()[0],
        "texted": db.execute(
            "SELECT COUNT(*) FROM messages WHERE business_id=? AND kind='first_text' AND status NOT LIKE 'blocked%' "
            "AND created_at>?",
            (biz["id"], week_ago),
        ).fetchone()[0],
        "booked": db.execute(
            "SELECT COUNT(*) FROM jobs WHERE business_id=? AND created_at>?", (biz["id"], week_ago)
        ).fetchone()[0],
    }
    today = now.astimezone(tz).date()
    today_jobs = [
        r
        for r in db.execute("SELECT starts_at FROM jobs WHERE business_id=? AND status='booked' AND starts_at>?", (biz["id"], iso(now - timedelta(days=1))))
        if parse(r["starts_at"]).astimezone(tz).date() == today
    ]
    return render_template(
        "dashboard.html",
        tickets=tickets,
        show=show,
        filters=FILTERS,
        counts=counts,
        stats=stats,
        now_iso=iso(now),
        needs=counts["needs"],
        today_jobs=len(today_jobs),
    )


# ---------------------------------------------------------------- one customer
@bp.get("/tickets/<int:conv_id>")
@login_required
def ticket(conv_id):
    db = get_db()
    conv = _conv_or_404(db, conv_id)
    messages = db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,)).fetchall()
    jobs = db.execute("SELECT * FROM jobs WHERE conversation_id=? ORDER BY starts_at DESC", (conv_id,)).fetchall()
    calls = db.execute(
        "SELECT status, created_at FROM calls WHERE customer_id=? ORDER BY id DESC LIMIT 5", (conv["customer_id"],)
    ).fetchall()
    pending, pending_when = {}, ""
    if conv["state"] == "awaiting_booking" and conv["pending_json"]:
        pending = json.loads(conv["pending_json"])
        pending_when = fmt_slot(parse(pending["start"]), tzinfo(g.biz))
    return render_template(
        "ticket.html", conv=conv, messages=messages, jobs=jobs, calls=calls, pending=pending, pending_when=pending_when
    )


def _back(conv_id):
    return redirect(url_for("owner.ticket", conv_id=conv_id))


def _job_on_ticket(db, conv_id, job_id):
    """The job must belong to the ticket named in the address, not just to this business."""
    return db.execute(
        "SELECT 1 FROM jobs WHERE id=? AND conversation_id=? AND business_id=?", (job_id, conv_id, g.biz["id"])
    ).fetchone() is not None


@bp.post("/tickets/<int:conv_id>/take-over")
@login_required
def take_over(conv_id):
    _conv_or_404(get_db(), conv_id)
    flow.take_over(get_db(), g.biz, conv_id)
    flash("You're handling this one. The assistant won't reply to this customer.")
    return _back(conv_id)


@bp.post("/tickets/<int:conv_id>/resolve")
@login_required
def resolve(conv_id):
    _conv_or_404(get_db(), conv_id)
    flow.resolve(get_db(), g.biz, conv_id)
    flash("Handed back. The assistant picks up again if this customer texts.")
    return _back(conv_id)


@bp.post("/tickets/<int:conv_id>/send")
@login_required
def send(conv_id):
    conv = _conv_or_404(get_db(), conv_id)
    body = " ".join(request.form.get("body", "").split())
    if not body:
        flash("Type a message first.")
    elif len(body) > 320:
        flash("Keep it under 320 characters.")
    elif not limiter("manual-%d" % g.biz["id"], 20, 60).allow("send"):
        flash("Slow down: that's a lot of texts in a minute.")
    elif conv["cust_opted_out"]:
        flash("This customer replied STOP, so you can't text them. Call them instead.")
    else:
        flow.send_manual(get_db(), g.biz, conv_id, body)
        flash("Text sent.")
    return _back(conv_id)


@bp.post("/tickets/<int:conv_id>/approve-text")
@login_required
def approve_text(conv_id):
    _conv_or_404(get_db(), conv_id)
    if flow.approve_text(get_db(), g.biz, conv_id):
        flash("Sent. The customer got the text-back.")
    else:
        flash("That text can't be sent. They may have replied STOP, or it was already handled.")
    return _back(conv_id)


@bp.post("/tickets/<int:conv_id>/skip-text")
@login_required
def skip_text(conv_id):
    _conv_or_404(get_db(), conv_id)
    flow.skip_text(get_db(), g.biz, conv_id)
    flash("Skipped. Nothing was sent to this caller.")
    return _back(conv_id)


@bp.post("/tickets/<int:conv_id>/approve-booking")
@login_required
def approve_booking(conv_id):
    _conv_or_404(get_db(), conv_id)
    if flow.approve_booking(get_db(), g.biz, conv_id):
        flash("Approved. The customer is being told. If the time was taken, they got new times instead.")
    else:
        flash("There is nothing waiting for your approval on this customer.")
    return _back(conv_id)


@bp.post("/tickets/<int:conv_id>/decline-booking")
@login_required
def decline_booking(conv_id):
    _conv_or_404(get_db(), conv_id)
    if flow.decline_booking(get_db(), g.biz, conv_id):
        flash("Declined. The customer was offered the next open times.")
    else:
        flash("There is nothing waiting for your approval on this customer.")
    return _back(conv_id)


@bp.post("/busy")
@login_required
def busy():
    """One tap to stop new bookings for the rest of today or tomorrow. Nothing already booked changes."""
    choice = request.form.get("until", "")
    tz = tzinfo(g.biz)
    local = utcnow().astimezone(tz)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=tz)
    if choice == "today":
        until = midnight
    elif choice == "tomorrow":
        until = midnight + timedelta(days=1)
    elif choice == "clear":
        until = None
    else:
        abort(400)
    with tx(get_db()):
        get_db().execute(
            "UPDATE businesses SET busy_until=? WHERE id=?", (iso(until.astimezone(timezone.utc)) if until else "", g.biz["id"])
        )
    flash("Back open. New bookings can use any open time." if until is None else "Blocked. Customers won't be offered times before then.")
    return redirect(url_for("owner.dashboard"))


@bp.get("/report")
@login_required
def report():
    days = 30 if request.args.get("days") != "7" else 7
    return render_template("report.html", r=flow.missed_report(get_db(), g.biz, days), days=days)


@bp.post("/tickets/<int:conv_id>/jobs/<int:job_id>/done")
@login_required
def job_done(conv_id, job_id):
    _conv_or_404(get_db(), conv_id)
    if _job_on_ticket(get_db(), conv_id, job_id) and flow.mark_done(get_db(), g.biz, job_id):
        if g.biz["review_url"]:
            flash("Marked done. A review request goes out in about 2 hours.")
        else:
            flash("Marked done. Add your review link in Setup to send review requests.")
    else:
        flash("That job isn't on the schedule anymore.")
    return _back(conv_id)


@bp.post("/tickets/<int:conv_id>/jobs/<int:job_id>/cancel")
@login_required
def job_cancel(conv_id, job_id):
    _conv_or_404(get_db(), conv_id)
    if _job_on_ticket(get_db(), conv_id, job_id) and flow.cancel_job(get_db(), g.biz, job_id, notify=request.form.get("notify") == "1"):
        flash("Job cancelled and the time is open again.")
    else:
        flash("That job isn't on the schedule anymore.")
    return _back(conv_id)


# ---------------------------------------------------------------- setup
def _parse_hours(form, errors):
    hours = {}
    for day, name in zip(DAYS, DAY_NAMES):
        if form.get("open_" + day) != "1":
            hours[day] = []
            continue
        start, end = form.get("start_" + day, ""), form.get("end_" + day, "")
        if not (TIME.match(start) and TIME.match(end)):
            errors.append("%s: enter the opening and closing time." % name)
            hours[day] = []
        elif start >= end:
            errors.append("%s: closing time must be after opening time." % name)
            hours[day] = []
        else:
            hours[day] = [[start, end]]
    return hours


def _int_in(form, key, low, high, label, errors, default):
    try:
        value = int(form.get(key, ""))
    except ValueError:
        errors.append("%s must be a number." % label)
        return default
    if not low <= value <= high:
        errors.append("%s must be between %d and %d." % (label, low, high))
        return default
    return value


def _setup_context(biz, form=None, errors=None):
    hours = json.loads(biz["hours_json"])
    rows = []
    for day, name in zip(DAYS, DAY_NAMES):
        window = hours.get(day) or []
        start, end = (window[0] if window else ["08:00", "17:00"])
        rows.append({"day": day, "name": name, "open": bool(window), "start": start, "end": end})
    return {
        "rows": rows,
        "timezones": TIMEZONES,
        "job_lengths": JOB_LENGTHS,
        "errors": errors or [],
        "calendar_url": "%s/calendar/%s.ics" % (current_app.config["BASE_URL"], biz["ics_token"]),
        "live": current_app.config["SMS_PROVIDER"] == "twilio",
    }


@bp.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
    db, biz = get_db(), g.biz
    if request.method == "GET":
        return render_template("setup.html", form=biz, **_setup_context(biz))

    f = request.form
    errors = []
    name = f.get("name", "").strip()
    if not 2 <= len(name) <= 80:
        errors.append("Business name must be 2 to 80 characters.")
    cell = normalize_us(f.get("owner_cell", ""))
    if cell is None:
        errors.append("Enter a US or Canadian cell phone where you want job alerts.")
    tz_name = f.get("timezone", "")
    try:
        ZoneInfo(tz_name)
        if tz_name not in dict(TIMEZONES):
            raise ZoneInfoNotFoundError(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append("Choose a time zone from the list.")
        tz_name = biz["timezone"]
    hours = _parse_hours(f, errors)
    job_minutes = _int_in(f, "job_minutes", 30, 240, "Job length", errors, biz["job_minutes"])
    if job_minutes not in JOB_LENGTHS:
        errors.append("Choose a job length from the list.")
    lead_hours = _int_in(f, "lead_hours", 0, 72, "Notice needed", errors, biz["lead_hours"])
    horizon_days = _int_in(f, "horizon_days", 1, 30, "Booking window", errors, biz["horizon_days"])
    if not errors and not any(
        any(
            (int(e[:2]) * 60 + int(e[3:])) - (int(s[:2]) * 60 + int(s[3:])) >= job_minutes for s, e in windows
        )
        for windows in hours.values()
    ):
        errors.append("Open at least one day with enough time for one job.")
    mode = f.get("mode", biz["mode"])
    if mode not in ("auto", "approve", "notify"):
        errors.append("Choose how the assistant should work.")
        mode = biz["mode"]
    skip_known = 1 if f.get("skip_known") == "1" else 0
    services = " ".join(f.get("services", "").split())[:300]
    service_area = " ".join(f.get("service_area", "").split())[:200]

    review_url = f.get("review_url", "").strip()
    if review_url:
        parts = urlparse(review_url)
        if parts.scheme != "https" or not parts.netloc or len(review_url) > 300 or " " in review_url:
            errors.append("The review link must start with https:// (copy it from your Google Business Profile).")

    if errors:
        draft = dict(biz)
        draft.update(name=name, owner_cell=f.get("owner_cell", ""), services=services, service_area=service_area,
                     review_url=review_url, job_minutes=job_minutes, lead_hours=lead_hours,
                     horizon_days=horizon_days, timezone=tz_name,
                     assistant_on=1 if f.get("assistant_on") == "1" else 0, mode=mode, skip_known=skip_known)
        ctx = _setup_context(biz, errors=errors)
        ctx["rows"] = [
            {"day": d, "name": n, "open": f.get("open_" + d) == "1",
             "start": f.get("start_" + d) or "08:00", "end": f.get("end_" + d) or "17:00"}
            for d, n in zip(DAYS, DAY_NAMES)
        ]
        return render_template("setup.html", form=draft, **ctx), 400

    with tx(db):
        db.execute(
            "UPDATE businesses SET name=?, owner_cell=?, timezone=?, hours_json=?, job_minutes=?, lead_hours=?, "
            "horizon_days=?, services=?, service_area=?, review_url=?, assistant_on=?, mode=?, skip_known=? WHERE id=?",
            (name, cell, tz_name, json.dumps(hours), job_minutes, lead_hours, horizon_days, services, service_area,
             review_url, 1 if f.get("assistant_on") == "1" else 0, mode, skip_known, biz["id"]),
        )
    flash("Saved.")
    return redirect(url_for("owner.setup"))


@bp.post("/setup/test-alert")
@login_required
def test_alert():
    db, biz = get_db(), g.biz
    if not limiter("test-alert-%d" % biz["id"], 3, 600).allow("x"):
        flash("You just sent a few test alerts. Wait a few minutes.")
        return redirect(url_for("owner.setup"))
    with tx(db):
        mid = texting.enqueue(db, biz, None, biz["owner_cell"], "JobPing test: job alerts will arrive on this phone.", "alert", owner_alert=True)
    texting.deliver(db, [mid])
    status = db.execute("SELECT status FROM messages WHERE id=?", (mid,)).fetchone()["status"]
    if status == "sent":
        flash("Test alert sent to your cell.")
    elif status.startswith("blocked"):
        flash("Test alert was blocked (%s)." % status.split(":", 1)[1].replace("_", " "))
    else:
        flash("Test alert did not send (%s). Check your texting settings." % status)
    return redirect(url_for("owner.setup"))


@bp.post("/setup/new-calendar-link")
@login_required
def new_calendar_link():
    db = get_db()
    with tx(db):
        db.execute("UPDATE businesses SET ics_token=? WHERE id=?", (new_token(), g.biz["id"]))
    flash("New calendar link created. Re-subscribe in your calendar app; the old link no longer works.")
    return redirect(url_for("owner.setup"))


# ---------------------------------------------------------------- calendar feed
@bp.get("/calendar/<token>.ics")
def calendar(token):
    db = get_db()
    biz = db.execute("SELECT * FROM businesses WHERE ics_token=?", (token,)).fetchone()
    if biz is None:
        abort(404)
    body = build_calendar(biz, feed_jobs(db, biz))
    return Response(body, mimetype="text/calendar", headers={"Content-Disposition": "inline; filename=jobs.ics"})


@bp.get("/messages")
@login_required
def messages():
    """Read-only: every text a customer can receive, in the owner's business name."""
    return render_template("messages.html", items=flow.preview_texts(g.biz))
