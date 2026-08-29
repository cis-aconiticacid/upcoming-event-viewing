#!/usr/bin/env python3
"""
Generic event importer: CSV → ICS files in vdir/

Usage:
    python import_events.py <config.json>

Config JSON schema:
{
    "csv_file": "path/to/events.csv",
    "event_type": "recurring" | "oneshot",
    "source_name": "Some Seminar Series",
    "source_url": "https://example.com/seminars",
    "semester_start": "2026-01-20",   # required if recurring
    "semester_end":   "2026-05-10",   # required if recurring
    "mapping": {
        "SUMMARY":      "{Title}",
        "LOCATION":     "{Location}",
        "DESCRIPTION":  "Organizers: {Organizers}",
        "RRULE_BYDAY":  "{DAY}",           # recurring only
        "RRULE_START":  "regex:{Time}:^(\\d+:\\d+(?:am|pm)):1",
        "RRULE_END":    "regex:{Time}:-(\\d+:\\d+(?:am|pm))$:1",
        "DTSTART_DATE": "{Date}",          # oneshot only
        "DTSTART_TIME": "{StartTime}",     # oneshot only
        "DTEND_TIME":   "{EndTime}"        # oneshot only
    }
}
"""

import csv
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

VDIR = Path(__file__).parent.parent / "vdir"

DAY_MAP = {
    "M": "MO", "T": "TU", "W": "WE", "R": "TH", "F": "FR",
    "MO": "MO", "TU": "TU", "WE": "WE", "TH": "TH", "FR": "FR",
    "SA": "SA", "SU": "SU",
}

WEEKDAY_ISO = {
    "MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6,
}


def resolve_field(template: str, row: dict) -> str:
    """Resolve a mapping template against a CSV row."""
    if template.startswith("regex:"):
        # regex:{ColName}:{pattern}:{group}
        parts = template.split(":", 3)
        _, col, pattern, group = parts
        value = row.get(col, "")
        m = re.search(pattern, value)
        return m.group(int(group)) if m else ""

    # {DAY} conversion
    result = template
    for key, val in row.items():
        result = result.replace(f"{{{key}}}", val)

    # {DAY} → RFC 5545 weekday
    def day_sub(m):
        return DAY_MAP.get(m.group(1).upper(), m.group(1))
    result = re.sub(r"\{DAY\}", lambda _: DAY_MAP.get(result, result), result, count=0)

    return result


def resolve_day(template: str, row: dict) -> str:
    raw = resolve_field(template, row).strip().upper()
    return DAY_MAP.get(raw, raw)


def parse_time_12h(s: str) -> tuple[int, int]:
    """Parse '3:30pm' or '12:00 pm' → (hour24, minute)."""
    s = s.strip().lower().replace(" ", "")
    fmt = "%I:%M%p" if ":" in s else "%I%p"
    dt = datetime.strptime(s, fmt)
    return dt.hour, dt.minute


def parse_date(s: str, year: int = None) -> date:
    """Parse various date strings. Handles 'Apr 7', '2026-01-20', 'January 7', etc."""
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%B %d", "%b %d", "%b. %d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            d = datetime.strptime(s, fmt).date()
            if d.year == 1900 and year:
                d = d.replace(year=year)
            return d
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def first_weekday_on_or_after(start: date, byday: str) -> date:
    """Return the first date >= start whose weekday matches byday (e.g. 'MO')."""
    target = WEEKDAY_ISO[byday]
    delta = (target - start.weekday()) % 7
    return start + timedelta(days=delta)


def ics_datetime(dt: datetime, tz: ZoneInfo) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def make_uid(source_name: str, summary: str, dtstart: str) -> str:
    raw = f"{source_name}|{summary}|{dtstart}"
    return hashlib.sha1(raw.encode()).hexdigest() + "@upcoming_event_viewing"


def build_recurring_ics(row: dict, cfg: dict) -> tuple[str, str]:
    m = cfg["mapping"]
    tz = ZoneInfo("America/Chicago")

    summary = resolve_field(m["SUMMARY"], row)
    location = resolve_field(m.get("LOCATION", ""), row)
    description = resolve_field(m.get("DESCRIPTION", ""), row)
    url = cfg.get("source_url", "")

    byday = resolve_day(m["RRULE_BYDAY"], row)
    start_str = resolve_field(m["RRULE_START"], row)
    end_str = resolve_field(m["RRULE_END"], row)

    sh, sm = parse_time_12h(start_str)
    eh, em = parse_time_12h(end_str)

    sem_start = date.fromisoformat(cfg["semester_start"])
    sem_end = date.fromisoformat(cfg["semester_end"])

    first_day = first_weekday_on_or_after(sem_start, byday)
    dtstart = datetime(first_day.year, first_day.month, first_day.day, sh, sm, tzinfo=tz)
    dtend = datetime(first_day.year, first_day.month, first_day.day, eh, em, tzinfo=tz)
    until = datetime(sem_end.year, sem_end.month, sem_end.day, 23, 59, 59, tzinfo=tz)

    uid = make_uid(cfg["source_name"], summary, dtstart.strftime("%Y%m%dT%H%M%S"))

    tzid = "America/Chicago"
    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//upcoming_event_viewing//EN
BEGIN:VEVENT
UID:{uid}
SUMMARY:{summary}
LOCATION:{location}
DESCRIPTION:{description}
URL:{url}
DTSTART;TZID={tzid}:{ics_datetime(dtstart, tz)}
DTEND;TZID={tzid}:{ics_datetime(dtend, tz)}
RRULE:FREQ=WEEKLY;BYDAY={byday};UNTIL={until.strftime("%Y%m%dT%H%M%SZ")}
END:VEVENT
END:VCALENDAR
"""
    filename = hashlib.sha1(uid.encode()).hexdigest()[:16] + ".ics"
    return filename, ics


def build_oneshot_ics(row: dict, cfg: dict) -> tuple[str, str]:
    m = cfg["mapping"]
    tz = ZoneInfo("America/Chicago")

    summary = resolve_field(m["SUMMARY"], row)
    location = resolve_field(m.get("LOCATION", ""), row)
    description = resolve_field(m.get("DESCRIPTION", ""), row)
    url = cfg.get("source_url", "")

    date_str = resolve_field(m["DTSTART_DATE"], row)
    start_str = resolve_field(m["DTSTART_TIME"], row)
    end_str = resolve_field(m["DTEND_TIME"], row)

    year = date.today().year
    d = parse_date(date_str, year=year)
    sh, sm = parse_time_12h(start_str)
    eh, em = parse_time_12h(end_str)

    dtstart = datetime(d.year, d.month, d.day, sh, sm, tzinfo=tz)
    dtend = datetime(d.year, d.month, d.day, eh, em, tzinfo=tz)

    uid = make_uid(cfg["source_name"], summary, dtstart.strftime("%Y%m%dT%H%M%S"))

    tzid = "America/Chicago"
    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//upcoming_event_viewing//EN
BEGIN:VEVENT
UID:{uid}
SUMMARY:{summary}
LOCATION:{location}
DESCRIPTION:{description}
URL:{url}
DTSTART;TZID={tzid}:{ics_datetime(dtstart, tz)}
DTEND;TZID={tzid}:{ics_datetime(dtend, tz)}
END:VEVENT
END:VCALENDAR
"""
    filename = hashlib.sha1(uid.encode()).hexdigest()[:16] + ".ics"
    return filename, ics


def run(config_path: str):
    with open(config_path) as f:
        cfg = json.load(f)

    event_type = cfg["event_type"]
    csv_path = cfg["csv_file"]
    written = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items() if k}
            if event_type == "recurring":
                filename, ics = build_recurring_ics(row, cfg)
            elif event_type == "oneshot":
                filename, ics = build_oneshot_ics(row, cfg)
            else:
                raise ValueError(f"Unknown event_type: {event_type!r}")

            out = VDIR / filename
            out.write_text(ics)
            print(f"  wrote {out.name}  ({row.get(list(row.keys())[0], '?')})")
            written += 1

    print(f"\nDone: {written} events written to {VDIR}/")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <config.json>")
        sys.exit(1)
    run(sys.argv[1])
