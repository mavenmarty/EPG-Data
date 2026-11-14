#!/usr/bin/env python3
"""
Build an XMLTV guide for adult channels from filmweb.pl TV listings.

Usage:
    python grab.py                      # all channels, 7 days -> public/guide.xml(.gz)
    python grab.py --days 3
    python grab.py --channel 153        # one channel (filmweb id)
    python grab.py --debug-html debug/  # also save raw HTML of every page fetched
    python grab.py --from-html file.html --channel 153 --date 2026-11-14   # offline parse test
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import random
import re
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = "https://www.filmweb.pl/program-tv"
TZ = ZoneInfo("Europe/Warsaw")
ROOT = Path(__file__).resolve().parent
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")
AGE_RE = re.compile(r"\s*\(\s*\+?18\s*\)\s*$")
# Text that marks the end of the schedule block on a channel page
STOP_MARKERS = (
    "Kanały o podobnej tematyce",
    "Opis kanału",
    "Lista kanałów TV",
    "Historia ",
)
TYPE_WORDS = {"film", "serial", "program", "magazyn", "dokument", "rozrywka"}
DEFAULT_LAST_DURATION = dt.timedelta(minutes=30)
MAX_GAP = dt.timedelta(hours=6)  # stop times beyond this are treated as unknown


# --------------------------------------------------------------------------- fetch

def day_path(offset: int) -> str:
    """Filmweb day URL suffix: today='', tomorrow='/jutro', day N (>=2)='/N'."""
    if offset == 0:
        return ""
    if offset == 1:
        return "/jutro"
    return f"/{offset}"


def fetch(session: requests.Session, url: str, retries: int = 3) -> str | None:
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            return r.text
        except requests.RequestException as e:
            wait = 2 ** attempt + random.random()
            print(f"  ! {url}: {e} (retry in {wait:.0f}s)", file=sys.stderr)
            time.sleep(wait)
    return None


# --------------------------------------------------------------------------- parse

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_jsonld(soup: BeautifulSoup) -> list[dict]:
    """Use schema.org BroadcastEvent/TVEpisode JSON-LD if the page ever ships it."""
    items = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data])
        for n in nodes:
            if not isinstance(n, dict):
                continue
            start = n.get("startDate")
            name = n.get("name") or (n.get("workPerformed") or {}).get("name")
            if start and name:
                items.append({"start_iso": start, "end_iso": n.get("endDate"),
                              "title": _clean(name), "type": None, "genre": None})
    return items


def parse_text(soup: BeautifulSoup) -> list[dict]:
    """
    Layout-agnostic parser. A channel page renders each slot as:
        HH:MM / Title (+18) / Type (e.g. "Film") / Genre (e.g. "film erotyczny")
    We walk the visible text lines and pick those groups out.
    """
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    lines = [_clean(x) for x in soup.get_text("\n").split("\n")]
    lines = [x for x in lines if x]

    items: list[dict] = []
    cur: dict | None = None
    started = False
    for line in lines:
        if started and any(line.startswith(m) for m in STOP_MARKERS):
            break
        m = TIME_RE.match(line)
        if m:
            started = True
            if cur and cur.get("title"):
                items.append(cur)
            cur = {"hm": (int(m.group(1)), int(m.group(2))),
                   "title": None, "type": None, "genre": None}
            continue
        if cur is None:
            continue
        if cur["title"] is None:
            cur["title"] = AGE_RE.sub("", line)
            cur["adult"] = bool(AGE_RE.search(line))
        elif cur["type"] is None and line.lower() in TYPE_WORDS:
            cur["type"] = line
        elif cur["genre"] is None and len(line) <= 40:
            cur["genre"] = line
    if cur and cur.get("title"):
        items.append(cur)
    return items


def parse_logo(soup: BeautifulSoup) -> str | None:
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return og["content"]
    return None


def to_programmes(items: list[dict], day: dt.date) -> list[dict]:
    """Turn HH:MM items into absolute datetimes, handling the after-midnight rollover."""
    out = []
    cur_date = day
    prev_minutes = None
    for it in items:
        if "start_iso" in it:
            start = dt.datetime.fromisoformat(it["start_iso"]).astimezone(TZ)
            stop = dt.datetime.fromisoformat(it["end_iso"]).astimezone(TZ) if it.get("end_iso") else None
        else:
            h, mi = it["hm"]
            minutes = h * 60 + mi
            if prev_minutes is not None and minutes < prev_minutes:
                cur_date += dt.timedelta(days=1)
            prev_minutes = minutes
            start = dt.datetime(cur_date.year, cur_date.month, cur_date.day, h, mi, tzinfo=TZ)
            stop = None
        out.append({**it, "start": start, "stop": stop})
    return out


def parse_page(html: str, day: dt.date) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    logo = parse_logo(soup)
    items = parse_jsonld(soup) or parse_text(soup)
    return to_programmes(items, day), logo


# --------------------------------------------------------------------------- merge

def finalize(progs: list[dict]) -> list[dict]:
    """Sort, dedupe, and fill stop times from the next programme's start."""
    progs.sort(key=lambda p: p["start"])
    dedup: list[dict] = []
    for p in progs:
        if dedup and dedup[-1]["start"] == p["start"]:
            continue
        dedup.append(p)
    for i, p in enumerate(dedup):
        if p["stop"]:
            continue
        nxt = dedup[i + 1]["start"] if i + 1 < len(dedup) else None
        if nxt and nxt - p["start"] <= MAX_GAP:
            p["stop"] = nxt
        else:
            p["stop"] = p["start"] + DEFAULT_LAST_DURATION
    return dedup


# --------------------------------------------------------------------------- xmltv

def fmt(t: dt.datetime) -> str:
    return t.strftime("%Y%m%d%H%M%S %z")


def build_xmltv(channels: list[dict], schedule: dict[str, list[dict]]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
           '<tv generator-info-name="adult-epg (filmweb.pl)" source-info-url="https://www.filmweb.pl/program-tv">']
    for ch in channels:
        out.append(f'  <channel id={quoteattr(ch["xmltv_id"])}>')
        out.append(f'    <display-name lang="en">{escape(ch["name"])}</display-name>')
        if ch.get("logo"):
            out.append(f'    <icon src={quoteattr(ch["logo"])}/>')
        out.append(f'    <url>{BASE}/{ch["filmweb_id"]}</url>')
        out.append("  </channel>")
    for ch in channels:
        for p in schedule.get(ch["xmltv_id"], []):
            out.append(f'  <programme start="{fmt(p["start"])}" stop="{fmt(p["stop"])}" '
                       f'channel={quoteattr(ch["xmltv_id"])}>')
            out.append(f'    <title>{escape(p["title"])}</title>')
            if p.get("type"):
                out.append(f'    <category lang="pl">{escape(p["type"])}</category>')
            if p.get("genre"):
                out.append(f'    <category lang="pl">{escape(p["genre"])}</category>')
            out.append('    <category lang="en">Adult</category>')
            out.append('    <rating system="PL"><value>18</value></rating>')
            out.append("  </programme>")
    out.append("</tv>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channels", default=str(ROOT / "channels.json"))
    ap.add_argument("--days", type=int, default=7, help="days ahead incl. today (max 13)")
    ap.add_argument("--channel", type=int, action="append", help="only this filmweb id (repeatable)")
    ap.add_argument("--output", default=str(ROOT / "public" / "guide.xml"))
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    ap.add_argument("--debug-html", help="directory to save fetched HTML")
    ap.add_argument("--from-html", help="parse a saved HTML file instead of fetching")
    ap.add_argument("--date", help="date for --from-html (YYYY-MM-DD), default today")
    args = ap.parse_args()

    channels = json.loads(Path(args.channels).read_text(encoding="utf-8"))
    if args.channel:
        channels = [c for c in channels if c["filmweb_id"] in args.channel]
    if not channels:
        print("No channels selected.", file=sys.stderr)
        return 1

    today = dt.datetime.now(TZ).date()
    schedule: dict[str, list[dict]] = {}

    if args.from_html:
        day = dt.date.fromisoformat(args.date) if args.date else today
        progs, logo = parse_page(Path(args.from_html).read_text(encoding="utf-8"), day)
        ch = channels[0]
        ch["logo"] = logo
        schedule[ch["xmltv_id"]] = finalize(progs)
        channels = [ch]
    else:
        session = requests.Session()
        session.headers.update({"User-Agent": UA, "Accept-Language": "pl,en;q=0.8"})
        debug = Path(args.debug_html) if args.debug_html else None
        if debug:
            debug.mkdir(parents=True, exist_ok=True)
        days = max(1, min(args.days, 13))
        for ch in channels:
            progs: list[dict] = []
            for off in range(days):
                url = f"{BASE}/{ch['filmweb_id']}{day_path(off)}"
                html = fetch(session, url)
                time.sleep(args.delay)
                if not html:
                    continue
                if debug:
                    (debug / f"{ch['filmweb_id']}_{off}.html").write_text(html, encoding="utf-8")
                day_progs, logo = parse_page(html, today + dt.timedelta(days=off))
                ch.setdefault("logo", logo)
                progs.extend(day_progs)
            schedule[ch["xmltv_id"]] = finalize(progs)
            print(f"{ch['name']:<22} {len(schedule[ch['xmltv_id']]):>5} programmes")

    xml = build_xmltv(channels, schedule)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8")
    with gzip.open(str(out) + ".gz", "wb") as f:
        f.write(xml.encode("utf-8"))
    total = sum(len(v) for v in schedule.values())
    print(f"Wrote {out} (+.gz): {len(channels)} channels, {total} programmes")
    if total == 0:
        print("WARNING: nothing parsed - run with --debug-html and check the saved pages.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
