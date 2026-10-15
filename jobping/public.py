"""What the customer sees: the booking page that opens from the text message."""
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

from . import flow, slots as slotmod
from .clock import iso, parse, utcnow
from .db import get_db
from .security import client_ip, limiter

bp = Blueprint("public", __name__)

MAX_SLOTS_SHOWN = 48


def _load(token):
    db = get_db()
    conv = db.execute("SELECT * FROM conversations WHERE token=?", (token,)).fetchone()
    if conv is None:
        abort(404)
    biz = db.execute("SELECT * FROM businesses WHERE id=?", (conv["business_id"],)).fetchone()
    cust = db.execute("SELECT * FROM customers WHERE id=?", (conv["customer_id"],)).fetchone()
    return db, biz, conv, cust


def _upcoming_job(db, conv):
    return db.execute(
        "SELECT * FROM jobs WHERE conversation_id=? AND status='booked' AND starts_at>? ORDER BY starts_at LIMIT 1",
        (conv["id"], iso(utcnow())),
    ).fetchone()


def _page(biz, conv, cust, db, form=None, errors=None, status=200):
    tz = slotmod.tzinfo(biz)
    job = _upcoming_job(db, conv)
    groups, paused = [], not biz["assistant_on"]
    if not job and not paused:
        groups = slotmod.group_by_day(slotmod.open_slots(db, biz, utcnow())[:MAX_SLOTS_SHOWN], tz)
    return (
        render_template(
            "book.html",
            biz=biz,
            groups=groups,
            job=job,
            job_when=slotmod.fmt_slot(parse(job["starts_at"]), tz) if job else "",
            paused=paused,
            form=form or {"name": cust["name"], "address": "", "problem": conv["problem"], "slot": ""},
            errors=errors or [],
        ),
        status,
    )


@bp.get("/book/<token>")
def book(token):
    db, biz, conv, cust = _load(token)
    return _page(biz, conv, cust, db)


@bp.post("/book/<token>")
def book_submit(token):
    db, biz, conv, cust = _load(token)
    if not limiter("book", 10, 600).allow(token):
        return _page(biz, conv, cust, db, errors=["Too many tries. Please call or text us instead."], status=429)
    if not biz["assistant_on"] or _upcoming_job(db, conv):
        return redirect(url_for("public.book", token=token))
    form = {
        "name": " ".join(request.form.get("name", "").split()),
        "address": " ".join(request.form.get("address", "").split()),
        "problem": " ".join(request.form.get("problem", "").split()),
        "slot": request.form.get("slot", ""),
    }
    errors = []
    start = None
    try:
        start = parse(form["slot"])
    except ValueError:
        errors.append("Pick a time.")
    if not 1 <= len(form["name"]) <= 80:
        errors.append("Enter your name.")
    if not 5 <= len(form["address"]) <= 200:
        errors.append("Enter the address where we should come.")
    if len(form["problem"]) > 500:
        errors.append("Keep the description under 500 characters.")
    if not errors:
        job_id = flow.book_from_web(db, biz, conv["id"], iso(start), form["name"], form["address"], form["problem"])
        if job_id is None:
            form["slot"] = ""
            errors.append("Sorry, that time was just taken. Please pick another.")
            return _page(biz, conv, cust, db, form=form, errors=errors, status=409)
        return redirect(url_for("public.done", token=token), 303)
    return _page(biz, conv, cust, db, form=form, errors=errors, status=400)


@bp.get("/book/<token>/done")
def done(token):
    db, biz, conv, cust = _load(token)
    job = db.execute(
        "SELECT * FROM jobs WHERE conversation_id=? AND status='booked' ORDER BY id DESC LIMIT 1", (conv["id"],)
    ).fetchone()
    if job is None:
        return redirect(url_for("public.book", token=token))
    when = slotmod.fmt_slot(parse(job["starts_at"]), slotmod.tzinfo(biz))
    return render_template("book_done.html", biz=biz, job=job, when=when, opted_out=bool(cust["opted_out"]))


@bp.get("/healthz")
def healthz():
    get_db().execute("SELECT 1").fetchone()
    return jsonify(ok=True)
