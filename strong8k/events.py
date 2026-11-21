"""
Guide entries for event channels, built from the channel's own name.

Strong 8K renames its event slots to say what's on, in a few formats:

  LIVE | GRAND SLAM OF CURLING | Sat 21 Nov 06:30 EST (US) | 8K EXCLUSIVE | US: ESPN+ PPV 2
  Next | Cheminots vs. Kouilou | Ligue 1 | 2026-11-21 | 13:00 (GMT) | 8K EXCLUSIVE | GB: DAZN PPV 22
  US (ESPN+ 001) | Brisbane Roar FC vs. Sydney FC Nov 21 3:40AM ET (2026-11-21 03:40:07)
  flolive: 2026 Australia vs England - Cricket (2026-11-21 03:15:00)
  NCAAM: Siena vs Bucknell @ Nov 21 11:00 AM :NCAAB  02
  - NO EVENT STREAMING - | 8K EXCLUSIVE | US: ESPN+ PPV 527

parse() turns a name into (slot name, event title, start) or a "no event" marker.
Times without a zone are US Eastern, which is what the names that do give a zone use.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
ZONES = {
    "ET": EASTERN, "EST": EASTERN, "EDT": EASTERN,
    "CT": ZoneInfo("America/Chicago"), "CST": ZoneInfo("America/Chicago"),
    "CDT": ZoneInfo("America/Chicago"),
    "MT": ZoneInfo("America/Denver"), "MST": ZoneInfo("America/Denver"),
    "MDT": ZoneInfo("America/Denver"),
    "PT": ZoneInfo("America/Los_Angeles"), "PST": ZoneInfo("America/Los_Angeles"),
    "PDT": ZoneInfo("America/Los_Angeles"),
    "GMT": dt.timezone.utc, "UTC": dt.timezone.utc, "BST": ZoneInfo("Europe/London"),
    "UK": ZoneInfo("Europe/London"), "CET": ZoneInfo("Europe/Paris"),
    "CEST": ZoneInfo("Europe/Paris"), "AEST": ZoneInfo("Australia/Sydney"),
    "AEDT": ZoneInfo("Australia/Sydney"),
}
MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}

NO_EVENT = object()

_STATUS = r"(?:LIVE|ENDED|END|NEXT|UPCOMING|SOON|STARTING SOON)"
# LIVE | title | Sat 21 Nov 06:30 EST (US) | ... | slot
RE_A = re.compile(
    rf"^\s*{_STATUS}\s*\|\s*(?P<title>.+?)\s*\|\s*(?:[A-Za-z]{{3}}\s+)?(?P<d>\d{{1,2}})\s+"
    rf"(?P<mon>[A-Za-z]{{3}})\s+(?P<hm>\d{{1,2}}:\d{{2}})\s*(?P<tz>[A-Z]{{2,4}})?\b", re.I)
# Next | title | league | 2026-11-21 | 13:00 (GMT) | ... | slot   (or 21-11-2026)
RE_B = re.compile(
    rf"^\s*{_STATUS}\s*\|\s*(?P<title>.+?)\s*\|.*?\|\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4})\s*\|\s*(?P<hm>\d{1,2}:\d{2})\s*"
    r"\((?P<tz>[A-Z]{2,4})\)", re.I)
# ... (2026-11-21 03:40:07)
RE_C = re.compile(r"^(?P<head>.*?)\s*\((?P<iso>\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?::\d{2})?\)\s*$")
# title @ Nov 21 11:00 AM :NCAAB  02
RE_D = re.compile(
    r"^(?P<title>.+?)\s*@\s*(?P<mon>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<hm>\d{1,2}:\d{2})"
    r"\s*(?P<ap>AM|PM)?\s*:(?P<slot>.+)$", re.I)
RE_NOEVENT = re.compile(r"no event|no stream|^\s*:|^\s*#+|\(2098-", re.I)
RE_SLOT_PREFIX = re.compile(
    r"^\s*(?P<slot>(?:(?:[A-Z]{2}\s*)?\([^)|]{1,30}\)\s*)+)\|\s*(?P<rest>.*)$")
RE_TRAILING_TIME = re.compile(
    r"\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{1,2}:\d{2}\s*(?:AM|PM)?\s*(?:ET|EST|EDT|PT|CT)?\s*$", re.I)


def _year_for(month: int, day: int, now: dt.datetime) -> int:
    y = now.year
    cand = dt.date(y, month, day)
    if (cand - now.date()).days > 180:
        return y - 1
    if (now.date() - cand).days > 180:
        return y + 1
    return y


def _at(y, mo, d, h, mi, tz) -> dt.datetime | None:
    try:
        return dt.datetime(y, mo, d, h, mi, tzinfo=tz or EASTERN)
    except ValueError:
        return None


def _hm(hm: str, ap: str | None = None) -> tuple[int, int]:
    h, m = (int(x) for x in hm.split(":"))
    if ap:
        ap = ap.upper()
        if ap == "PM" and h < 12:
            h += 12
        if ap == "AM" and h == 12:
            h = 0
    return h, m


def _slot_tail(name: str) -> str:
    """'… | 8K EXCLUSIVE | US: ESPN+ PPV 2' -> 'US: ESPN+ PPV 2'."""
    return name.rsplit("|", 1)[-1].strip() if "|" in name else name.strip()


def _clean(title: str) -> str:
    title = RE_TRAILING_TIME.sub("", title)
    title = re.sub(r"\s+", " ", title).strip(" |-:")
    return title


def parse(name: str, now: dt.datetime):
    """Returns (slot, title, start) for an event, (slot, NO_EVENT, None), or None.
    slot is None when the name has no slot label of its own."""
    if not name or not name.strip():
        return None
    n = name.strip()

    m = RE_A.match(n)
    if m:
        mon = MONTHS.get(m["mon"].lower())
        if mon:
            h, mi = _hm(m["hm"])
            tz = ZONES.get((m["tz"] or "").upper())
            start = _at(_year_for(mon, int(m["d"]), now), mon, int(m["d"]), h, mi, tz)
            if start:
                return _slot_tail(n), _clean(m["title"]), start

    m = RE_B.match(n)
    if m:
        ds = m["date"]
        y, mo, d = (int(ds[:4]), int(ds[5:7]), int(ds[8:10])) if ds[4] == "-" else \
            (int(ds[6:10]), int(ds[3:5]), int(ds[0:2]))
        h, mi = _hm(m["hm"])
        start = _at(y, mo, d, h, mi, ZONES.get(m["tz"].upper(), dt.timezone.utc))
        if start:
            return _slot_tail(n), _clean(m["title"]), start

    if RE_NOEVENT.search(n):
        slot = _slot_tail(n)
        if n.lstrip().startswith(":"):
            slot = n.strip(" :")
        m = RE_SLOT_PREFIX.match(n)
        if m:
            slot = m["slot"]
        return (re.sub(r"\s+", " ", slot), NO_EVENT, None) if not n.lstrip().startswith("#") else None

    m = RE_C.match(n)
    if m:
        head = m["head"]
        slot = None
        sp = RE_SLOT_PREFIX.match(head)
        if sp:
            slot, head = sp["slot"], sp["rest"]
        title = _clean(head)
        if title:
            iso = m["iso"]
            start = _at(int(iso[:4]), int(iso[5:7]), int(iso[8:10]),
                        int(iso[11:13]), int(iso[14:16]), EASTERN)
            if start:
                return (slot.strip() if slot else None), title, start

    m = RE_D.match(n)
    if m:
        mon = MONTHS.get(m["mon"].lower())
        if mon:
            h, mi = _hm(m["hm"], m["ap"])
            start = _at(_year_for(mon, int(m["d"]), now), mon, int(m["d"]), h, mi, EASTERN)
            if start:
                return re.sub(r"\s+", " ", m["slot"]).strip(), _clean(m["title"]), start
    return None


def programmes(parsed, lo: dt.datetime, hi: dt.datetime, length: dt.timedelta):
    """(start, stop, title, is_event) blocks covering the guide window for one channel."""
    slot, title, start = parsed
    if title is not NO_EVENT and start + length < lo - dt.timedelta(days=1):
        title = NO_EVENT                      # a slot still named after an old event
    if title is NO_EVENT:
        return [(lo, hi, "No event scheduled", False)]
    end = start + length
    if end <= lo or start >= hi:
        return [(lo, hi, f"Ended: {title}" if end <= lo else f"Next: {title}", False)]
    out = []
    if start > lo:
        out.append((lo, start, f"Next: {title}", False))
    out.append((max(start, lo), min(end, hi), title, True))
    if end < hi:
        out.append((end, hi, f"Ended: {title}", False))
    return out
