"""Settings, read from environment variables.

Every setting has a safe default for local use. Going live with real texting
(SMS_PROVIDER=twilio) turns on strict checks: the app refuses to start without a
real secret key, Twilio credentials and an https address.
"""
import os
import secrets


class ConfigError(RuntimeError):
    pass


def _int(env, key, default):
    try:
        return int(env.get(key, default))
    except (TypeError, ValueError):
        return default


def load_config(env=None, overrides=None):
    e = dict(os.environ if env is None else env)
    provider = e.get("SMS_PROVIDER", "console").strip().lower()
    base_url = e.get("BASE_URL", "http://localhost:5000").rstrip("/")
    cfg = {
        "APP_NAME": e.get("APP_NAME", "JobPing"),
        "SECRET_KEY": e.get("SECRET_KEY", ""),
        "DATABASE": e.get("DATABASE_PATH", "jobping.db"),
        "BASE_URL": base_url,
        "SMS_PROVIDER": provider,
        # Master switch: set SMS_ENABLED=0 to stop every outgoing text at once.
        "SMS_ENABLED": e.get("SMS_ENABLED", "1") == "1",
        "SIMULATOR": e.get("SIMULATOR", "1" if provider == "console" else "0") == "1",
        "TWILIO_ACCOUNT_SID": e.get("TWILIO_ACCOUNT_SID", ""),
        "TWILIO_AUTH_TOKEN": e.get("TWILIO_AUTH_TOKEN", ""),
        # Open sign-up is for trying the simulator. With live texting it is off unless you turn it on,
        # because every account can point calls and texts at your Twilio bill.
        "ALLOW_SIGNUP": e.get("ALLOW_SIGNUP", "1" if provider == "console" else "0") == "1",
        # Only texts to numbers starting with this are sent (US and Canada). Stops a flood of fake
        # international callers from turning into texts to expensive numbers.
        "ALLOWED_PREFIX": e.get("ALLOWED_PREFIX", "+1"),
        "COOKIE_SECURE": e.get("COOKIE_SECURE", "1" if base_url.startswith("https://") else "0") == "1",
        "CRON_SECRET": e.get("CRON_SECRET", ""),
        "SCHEDULER": e.get("SCHEDULER", "1") == "1",
        # Behind a hosting proxy (Render, Railway, Fly): trust its forwarded address and scheme.
        "TRUST_PROXY": e.get("TRUST_PROXY", "0") == "1",
        # Ask the owner to press a key when a forwarded call connects, so the owner's carrier
        # voicemail answering can never count as "answered".
        "SCREEN_CALLS": e.get("SCREEN_CALLS", "1") == "1",
        # Limits that stop a loop or a bug from running up a texting bill.
        "MAX_TEXTS_PER_NUMBER_PER_DAY": _int(e, "MAX_TEXTS_PER_NUMBER_PER_DAY", 6),
        "MAX_AUTO_TEXTS_PER_BUSINESS_PER_DAY": _int(e, "MAX_AUTO_TEXTS_PER_BUSINESS_PER_DAY", 200),
        "MAX_FIRST_TEXTS_PER_BUSINESS_PER_DAY": _int(e, "MAX_FIRST_TEXTS_PER_BUSINESS_PER_DAY", 40),
        "MAX_OWNER_ALERTS_PER_BUSINESS_PER_DAY": _int(e, "MAX_OWNER_ALERTS_PER_BUSINESS_PER_DAY", 30),
        "MISSED_CALL_DEDUPE_MINUTES": _int(e, "MISSED_CALL_DEDUPE_MINUTES", 120),
    }
    if overrides:
        cfg.update(overrides)
    _validate(cfg)
    return cfg


def _validate(cfg):
    if cfg["SMS_PROVIDER"] not in ("console", "twilio"):
        raise ConfigError("SMS_PROVIDER must be 'console' or 'twilio'.")
    if cfg["SMS_PROVIDER"] == "twilio":
        problems = []
        if len(cfg["SECRET_KEY"]) < 32:
            problems.append("SECRET_KEY must be set to at least 32 random characters")
        if not cfg["TWILIO_ACCOUNT_SID"] or not cfg["TWILIO_AUTH_TOKEN"]:
            problems.append("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set")
        if not cfg["BASE_URL"].startswith("https://"):
            problems.append("BASE_URL must start with https://")
        if cfg["SIMULATOR"]:
            problems.append("SIMULATOR must be off when texting is live")
        if problems:
            raise ConfigError("Cannot start with live texting: " + "; ".join(problems) + ".")
    elif not cfg["SECRET_KEY"]:
        # Local use only: a fresh key each start just signs everyone out.
        cfg["SECRET_KEY"] = secrets.token_hex(32)
