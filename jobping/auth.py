"""Sign up, log in, log out."""
import json
import re
import secrets
from functools import wraps

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .clock import iso, utcnow
from .db import get_db, tx
from .security import client_ip, limiter, new_token, safe_next
from .slots import DEFAULT_HOURS
from .texting import normalize_us

bp = Blueprint("auth", __name__)

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
# Checked when the email is unknown, so a wrong email and a wrong password take the same time.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def load_business():
    g.biz = None
    biz_id = session.get("biz")
    if biz_id:
        g.biz = get_db().execute("SELECT * FROM businesses WHERE id=?", (biz_id,)).fetchone()
        if g.biz is None:
            session.clear()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("biz") is None:
            target = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.login", next=target))
        return view(*args, **kwargs)

    return wrapped


def _start_session(biz_id):
    session.clear()
    session.permanent = True
    session["biz"] = biz_id


# Simulator only: made-up texting numbers come from the range reserved for fiction (555-0100 to 555-0199).
# Kept clear of the demo shop's owner and customers (0100 to 0106) and of the numbers the tests use,
# because a texting number that equals a customer's number would make the app ignore that customer's calls.
_FAKE_LINES = ["+158655501%02d" % n for n in range(160, 198) if n not in (177, 188)]


def _fake_text_number(db):
    for _ in range(200):
        number = secrets.choice(_FAKE_LINES)
        if not db.execute("SELECT 1 FROM businesses WHERE text_number=?", (number,)).fetchone():
            return number
    return None


def create_business(db, name, email, password, cell, text_number=None):
    """Make a business with sensible defaults. Returns its id."""
    cfg = current_app.config
    if cfg["SMS_PROVIDER"] == "console" and not text_number:
        text_number = _fake_text_number(db)
    with tx(db):
        cur = db.execute(
            "INSERT INTO businesses(name, owner_email, password_hash, owner_cell, text_number, hours_json, "
            "ics_token, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                name,
                email,
                generate_password_hash(password),
                cell,
                text_number,
                json.dumps(DEFAULT_HOURS),
                new_token(),
                iso(utcnow()),
            ),
        )
    return cur.lastrowid


def check_new_account(db, name, email, cell_raw, password):
    """Shared by sign-up and the create-business command. Returns (errors, email, cell)."""
    errors = []
    email = (email or "").strip().lower()
    cell = normalize_us(cell_raw)
    if not 2 <= len(name or "") <= 80:
        errors.append("Enter your business name (2 to 80 characters).")
    if not EMAIL.match(email) or len(email) > 254:
        errors.append("Enter a valid email address.")
    if cell is None:
        errors.append("Enter a US or Canadian cell phone number for job alerts, like (586) 555-0100.")
    if not 10 <= len(password or "") <= 200:
        errors.append("Choose a password with at least 10 characters.")
    if not errors and db.execute("SELECT 1 FROM businesses WHERE owner_email=?", (email,)).fetchone():
        errors.append("An account with that email already exists. Log in instead.")
    return errors, email, cell


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if not current_app.config["ALLOW_SIGNUP"]:
        return render_template("error.html", title="Sign-up is closed", message="Ask the person who runs this app for an account."), 403
    form = {"name": "", "email": "", "cell": ""}
    errors = []
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in ("name", "email", "cell")}
        errors, email, cell = check_new_account(get_db(), form["name"], form["email"], form["cell"], request.form.get("password", ""))
        if not limiter("signup", 5, 3600).allow(client_ip()):
            errors.insert(0, "Too many sign-ups from this connection. Try again in an hour.")
        if not errors:
            biz_id = create_business(get_db(), form["name"], email, request.form.get("password", ""), cell)
            _start_session(biz_id)
            flash("Welcome. Check your business hours below, then try a fake missed call in the Simulator.")
            return redirect(url_for("owner.setup"))
    return render_template("signup.html", form=form, errors=errors), (400 if errors else 200)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("biz"):
        return redirect(url_for("owner.dashboard"))
    error = None
    email = ""
    status = 200
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:254]
        password = request.form.get("password", "")[:200]
        key = "%s|%s" % (client_ip(), email)
        if not limiter("login-ip", 40, 900).allow(client_ip()) or not limiter("login", 8, 900).allow(key):
            error = "Too many tries. Wait 15 minutes and try again."
            status = 429
        else:
            biz = get_db().execute("SELECT * FROM businesses WHERE owner_email=?", (email,)).fetchone()
            ok = check_password_hash(biz["password_hash"] if biz else _DUMMY_HASH, password)
            if biz and ok:
                limiter("login", 8, 900).reset(key)
                _start_session(biz["id"])
                return redirect(safe_next(request.args.get("next")))
            error = "That email and password don't match."
            status = 401
    return render_template("login.html", error=error, email=email), status


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
