"""One place that says what time it is, so tests and the simulator can move it."""
from datetime import datetime, timedelta, timezone

from flask import current_app

FMT = "%Y-%m-%dT%H:%M:%SZ"


def iso(dt):
    return dt.astimezone(timezone.utc).strftime(FMT)


def parse(text):
    return datetime.strptime(text, FMT).replace(tzinfo=timezone.utc)


def utcnow():
    cfg = current_app.config
    if cfg.get("TESTING") and cfg.get("NOW_OVERRIDE"):
        return parse(cfg["NOW_OVERRIDE"])
    base = datetime.now(timezone.utc).replace(microsecond=0)
    if cfg.get("SIMULATOR"):
        from .db import get_db

        row = get_db().execute("SELECT value FROM settings WHERE key = 'sim_offset_minutes'").fetchone()
        if row:
            base += timedelta(minutes=int(row["value"]))
    return base
