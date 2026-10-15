"""The calendar feed. Owners subscribe to one private link in Google, Apple or Outlook Calendar."""
from datetime import timedelta

from .clock import parse, utcnow

STAMP = "%Y%m%dT%H%M%SZ"


def _escape(text):
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )


def _fold(line):
    """Lines longer than 75 bytes continue on the next line, starting with one space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    out, chunk = [], b""
    for ch in line:
        piece = ch.encode("utf-8")
        limit = 75 if not out else 74
        if len(chunk) + len(piece) > limit:
            out.append(chunk.decode("utf-8"))
            chunk = b""
        chunk += piece
    out.append(chunk.decode("utf-8"))
    return [out[0]] + [" " + part for part in out[1:]]


def build_calendar(biz, jobs):
    """jobs: rows from the jobs table joined with the customer's phone."""
    stamp = utcnow().strftime(STAMP)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//JobPing//Booked jobs//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + _escape("%s jobs" % biz["name"]),
        "REFRESH-INTERVAL;VALUE=DURATION:PT15M",
    ]
    for job in jobs:
        who = job["name"] or job["phone"]
        details = ["Customer: %s" % who, "Phone: %s" % job["phone"]]
        if job["problem"]:
            details.append("Problem: %s" % job["problem"])
        lines += [
            "BEGIN:VEVENT",
            "UID:job-%d@jobping" % job["id"],
            "DTSTAMP:" + stamp,
            "DTSTART:" + parse(job["starts_at"]).strftime(STAMP),
            "DTEND:" + parse(job["ends_at"]).strftime(STAMP),
            "SUMMARY:" + _escape("%s: %s" % (biz["trade"], who)),
            "LOCATION:" + _escape(job["address"]),
            "DESCRIPTION:" + _escape("\n".join(details)),
            "STATUS:CONFIRMED" if job["status"] != "cancelled" else "STATUS:CANCELLED",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    folded = []
    for line in lines:
        folded.extend(_fold(line))
    return "\r\n".join(folded) + "\r\n"


def feed_jobs(db, biz):
    since = (utcnow() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return db.execute(
        "SELECT j.*, c.phone FROM jobs j JOIN customers c ON c.id=j.customer_id "
        "WHERE j.business_id=? AND j.status IN ('booked','done') AND j.starts_at>? ORDER BY j.starts_at",
        (biz["id"], since),
    ).fetchall()
