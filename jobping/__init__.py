"""JobPing: when a plumbing shop misses a call, text the customer back and book the job."""
import logging
import threading
import time
from datetime import timedelta

import click
from flask import Flask, g, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, db as dbmod, flow, hooks, owner, public, security, slots as slotmod
from .clock import parse, utcnow
from .config import load_config
from .icons import icon
from .texting import fmt_phone, normalize_us

CSRF_EXEMPT = {"hooks", "public"}  # Twilio has no cookie; booking pages are protected by their secret link

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)


def create_app(overrides=None, env=None):
    app = Flask(__name__)
    cfg = load_config(env=env, overrides=overrides)
    app.config.update(cfg)
    app.secret_key = cfg["SECRET_KEY"]
    app.config.update(
        SESSION_COOKIE_NAME="jobping",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg["COOKIE_SECURE"],
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=64 * 1024,
        TEMPLATES_AUTO_RELOAD=False,
    )
    if cfg["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app.teardown_appcontext(dbmod.close_db)
    with app.app_context():
        dbmod.init_db()

    app.register_blueprint(hooks.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(owner.bp)
    app.register_blueprint(public.bp)
    if cfg["SIMULATOR"]:
        from . import sim

        app.register_blueprint(sim.bp)

    @app.before_request
    def before():
        auth.load_business()
        if request.method == "POST" and request.blueprint not in CSRF_EXEMPT:
            security.check_csrf()

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.blueprint == "public":
            resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        if cfg["COOKIE_SECURE"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.endpoint != "static":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def inject():
        return {
            "app_name": cfg["APP_NAME"],
            "csrf_token": security.csrf_token,
            "simulator_on": cfg["SIMULATOR"],
            "live_texting": cfg["SMS_PROVIDER"] == "twilio",
        }

    app.jinja_env.globals["icon"] = icon

    @app.template_filter("phone")
    def phone_filter(value):
        return fmt_phone(value)

    @app.template_filter("when")
    def when_filter(value, biz=None):
        biz = biz or g.biz
        return slotmod.fmt_slot(parse(value), slotmod.tzinfo(biz)) if value else ""

    @app.template_filter("ago")
    def ago_filter(value):
        if not value:
            return ""
        seconds = int((utcnow() - parse(value)).total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return "%d min ago" % (seconds // 60)
        if seconds < 86400:
            hours = seconds // 3600
            return "%d hour%s ago" % (hours, "" if hours == 1 else "s")
        days = seconds // 86400
        return "%d day%s ago" % (days, "" if days == 1 else "s")

    def error_page(code, title, message):
        return render_template("error.html", title=title, message=message), code

    @app.errorhandler(400)
    def bad_request(exc):
        return error_page(400, "That didn't work", getattr(exc, "description", "") or "Reload the page and try again.")

    @app.errorhandler(403)
    def forbidden(_exc):
        return error_page(403, "Not allowed", "You don't have access to this page.")

    @app.errorhandler(404)
    def not_found(_exc):
        return error_page(404, "Page not found", "That link doesn't go anywhere. Check it and try again.")

    @app.errorhandler(405)
    def wrong_method(_exc):
        return error_page(405, "Page not found", "That link doesn't go anywhere. Check it and try again.")

    @app.errorhandler(413)
    def too_big(_exc):
        return error_page(413, "Too much data", "That request was too large.")

    @app.errorhandler(500)
    def server_error(_exc):
        return error_page(500, "Something broke on our side", "Nothing you did caused this. Try again in a minute.")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Create a demo business with sample customers (simulator only)."""
        from .seed import DEMO_EMAIL, DEMO_PASSWORD, seed_demo

        if seed_demo():
            click.echo("Demo created. Log in with %s / %s" % (DEMO_EMAIL, DEMO_PASSWORD))
        else:
            click.echo("The demo business already exists.")

    @app.cli.command("create-business")
    @click.option("--name", prompt="Business name")
    @click.option("--email", prompt="Owner's email")
    @click.option("--cell", prompt="Owner's cell phone (gets alerts and forwarded calls)")
    @click.option("--text-number", default="", help="The Twilio number customers call and text, like +15865550123.")
    @click.password_option(prompt="Password (10+ characters)")
    def create_business_command(name, email, cell, text_number, password):
        """Add a business. Use this instead of open sign-up when texting is live."""
        db = dbmod.get_db()
        errors, email, cell = auth.check_new_account(db, name, email, cell, password)
        number = None
        if text_number:
            number = normalize_us(text_number)
            if number is None:
                errors.append("The texting number must be a US or Canadian number.")
            elif db.execute("SELECT 1 FROM businesses WHERE text_number=?", (number,)).fetchone():
                errors.append("That texting number is already used by another business.")
        if errors:
            raise click.ClickException("\n".join(errors))
        biz_id = auth.create_business(db, name, email, password, cell, number)
        click.echo("Created business %d. They can log in at %s/login" % (biz_id, cfg["BASE_URL"]))

    @app.cli.command("run-due")
    def run_due_command():
        """Send due reminders and review requests once."""
        click.echo("Texts queued: %d" % flow.run_due(dbmod.get_db()))

    if cfg["SCHEDULER"] and not cfg.get("TESTING"):
        _start_scheduler(app)
    return app


def _start_scheduler(app):
    """Check for due reminders and review requests once a minute."""

    def loop():
        while True:
            time.sleep(60)
            try:
                with app.app_context():
                    flow.run_due(dbmod.get_db())
            except Exception:  # noqa: BLE001 - keep the loop alive
                app.logger.exception("scheduled check failed")

    threading.Thread(target=loop, name="jobping-scheduler", daemon=True).start()
