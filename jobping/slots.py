"""Which times can a customer book? Hours, job length and lead time minus jobs already booked."""
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .clock import iso, parse

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DEFAULT_HOURS = {d: [["08:00", "17:00"]] for d in DAYS[:5]}
DEFAULT_HOURS.update({"sat": [], "sun": []})


def tzinfo(biz):
    return ZoneInfo(biz["timezone"])


def open_slots(db, biz, now):
    """Every bookable start time (UTC) from now through the booking horizon, earliest first."""
    tz = tzinfo(biz)
    hours = json.loads(biz["hours_json"])
    step = timedelta(minutes=biz["job_minutes"])
    earliest = now + timedelta(hours=biz["lead_hours"])
    if biz["busy_until"]:
        earliest = max(earliest, parse(biz["busy_until"]))  # the owner blocked off time
    booked = [
        (parse(r["starts_at"]), parse(r["ends_at"]))
        for r in db.execute(
            "SELECT starts_at, ends_at FROM jobs WHERE business_id=? AND status='booked' AND ends_at>?",
            (biz["id"], iso(now)),
        )
    ]
    today = now.astimezone(tz).date()
    found = []
    for offset in range(biz["horizon_days"] + 1):
        day = today + timedelta(days=offset)
        for start_text, end_text in hours.get(DAYS[day.weekday()], []):
            cursor = datetime.combine(day, time.fromisoformat(start_text), tzinfo=tz)
            window_end = datetime.combine(day, time.fromisoformat(end_text), tzinfo=tz)
            while cursor + step <= window_end:
                start = cursor.astimezone(timezone.utc)
                end = (cursor + step).astimezone(timezone.utc)
                taken = any(start < b_end and end > b_start for b_start, b_end in booked)
                if start >= earliest and not taken:
                    found.append(start)
                cursor += step
    return found


def pick_offers(slots, tz, count=3, per_day=2):
    """Choose a few early times without stacking them all on one day."""
    picks, per = [], {}
    for slot in slots:
        day = slot.astimezone(tz).date()
        if per.get(day, 0) >= per_day:
            continue
        picks.append(slot)
        per[day] = per.get(day, 0) + 1
        if len(picks) == count:
            break
    return picks


def clock_text(local):
    return "%d:%02d %s" % (local.hour % 12 or 12, local.minute, "AM" if local.hour < 12 else "PM")


def fmt_slot(dt_utc, tz):
    """For example 'Thu Oct 15, 9:00 AM' (no platform-specific format codes)."""
    local = dt_utc.astimezone(tz)
    return "%s %s %d, %s" % (local.strftime("%a"), local.strftime("%b"), local.day, clock_text(local))


def group_by_day(slots, tz):
    """For the booking page: [(day label, [(iso start, time label), ...]), ...]."""
    groups = []
    for slot in slots:
        local = slot.astimezone(tz)
        label = "%s, %s %d" % (local.strftime("%A"), local.strftime("%B"), local.day)
        if not groups or groups[-1][0] != label:
            groups.append((label, []))
        groups[-1][1].append((iso(slot), clock_text(local)))
    return groups
