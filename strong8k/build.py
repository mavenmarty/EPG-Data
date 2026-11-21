#!/usr/bin/env python3
"""
Build an XMLTV guide for the Strong 8K live lineup.

1. Read the live channels (tvg-id, name, logo) from the M3U link.
2. Take the provider's own guide (xmltv.php, same login) for those channels.
3. For channels the provider leaves empty or short, fill in from the free public
   guides in sources.json, matched by id first and then by name.
4. Write strong8k/out/guide.xml.gz and strong8k/out/report.json.

The M3U link holds the login, so it comes from the environment
(STRONG8K_M3U_URL) and is never printed.

Usage:
    STRONG8K_M3U_URL='http://host/get.php?username=..&password=..&type=m3u_plus' \
        python strong8k/build.py
    python strong8k/build.py --m3u tests/x.m3u --provider tests/p.xml --source us=tests/s.xml
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from lxml import etree

HERE = Path(__file__).resolve().parent
UA = os.environ.get("STRONG8K_USER_AGENT") or (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
THIN = dt.timedelta(hours=12)       # provider guide ending sooner than this = gap
MAX_GZ_BYTES = 95 * 1024 * 1024     # GitHub refuses files over 100 MB

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
COUNTRY_PREFIX_RE = re.compile(
    r"^\s*[\[(]?\s*(US|USA|UK|GB|CA|CAN)\s*[\])]?\s*[:|\-–]\s*", re.I)
NOISE = {"hd", "fhd", "uhd", "sd", "4k", "8k", "hevc", "h265", "h264", "raw",
         "backup", "vip", "hq", "lq", "50fps", "60fps", "1080p", "720p", "tv"}
COUNTRY_ALIASES = {"usa": "us", "uk": "gb", "can": "ca"}
# short forms used by public guides ("SkySp.Fball", "Cartoon.Netwrk", "Disney.Jr")
ABBREV = {"sp": "sports", "fball": "football", "netwrk": "network", "net": "network",
          "disc": "discovery", "jr": "junior", "inv": "investigation", "plnt": "planet",
          "ev": "event", "sci": "science", "ent": "entertainment"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def mask(*values):
    """Hide login parts in GitHub Actions logs."""
    if os.environ.get("GITHUB_ACTIONS"):
        for v in values:
            if v:
                print(f"::add-mask::{v}", flush=True)


def country(code: str | None) -> str | None:
    if not code:
        return None
    c = code.lower()
    return COUNTRY_ALIASES.get(c, c)


def norm_name(s: str) -> str:
    """'US| ESPN ᴴᴰ' -> 'espn'; 'A&E.us' -> 'a and e'."""
    s = unicodedata.normalize("NFKC", s)
    s = COUNTRY_PREFIX_RE.sub("", s)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)          # SkySpMainEv -> Sky Sp Main Ev
    s = re.sub(r"\.(us|uk|gb|ca)\d*$", "", s, flags=re.I)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("+", " plus ")
    s = re.sub(r"[\[(].*?[\])]", " ", s)
    words = [ABBREV.get(w, w) for w in re.split(r"[^a-z0-9]+", s) if w and w not in NOISE]
    if words[:2] == ["u", "and"] and len(words) > 2:   # UKTV: "U&Dave" -> "dave"
        words = words[2:]
    return " ".join(words)


def name_key(s: str) -> str:
    return norm_name(s).replace(" ", "")


def norm_id(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# --------------------------------------------------------------------------- input

def fetch_to_file(src: str, dest: Path, what: str, retries: int = 3) -> Path:
    """Download a URL (or copy a local path) to dest. Never logs the URL."""
    if not re.match(r"^https?://", src):
        shutil.copyfile(src, dest)
        return dest
    for attempt in range(1, retries + 1):
        try:
            with requests.get(src, headers={"User-Agent": UA}, stream=True,
                              timeout=(20, 120)) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                ctype = r.headers.get("content-type", "?")
            size = dest.stat().st_size
            with open(dest, "rb") as f:
                head = f.read(16)
            shown = re.sub(rb"[^ -~]", b".", head).decode()
            log(f"  {what}: HTTP {r.status_code}, {ctype}, {size:,} bytes, starts {shown!r}")
            return dest
        except requests.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            log(f"  ! {what}: {type(e).__name__}{f' HTTP {code}' if code else ''} "
                f"(try {attempt}/{retries})")
            if attempt == retries:
                raise RuntimeError(f"could not download {what}") from None
            time.sleep(5 * attempt)
    raise AssertionError


def open_maybe_gz(path: Path):
    with open(path, "rb") as f:
        magic = f.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def provider_guide_url(m3u_url: str, header_tvg: str | None) -> str | None:
    if os.environ.get("STRONG8K_XMLTV_URL"):
        return os.environ["STRONG8K_XMLTV_URL"]
    u = urlsplit(m3u_url)
    q = parse_qs(u.query)
    if u.path.endswith("get.php") and "username" in q and "password" in q:
        mask(q["username"][0], q["password"][0], u.netloc)
        base = u.path[: -len("get.php")]
        return (f"{u.scheme}://{u.netloc}{base}xmltv.php?"
                + urlencode({"username": q["username"][0], "password": q["password"][0]}))
    return header_tvg


def read_m3u(path: Path) -> tuple[dict[str, dict], int, str | None]:
    """Live channels grouped by tvg-id. Returns (channels, without_id, header guide url)."""
    channels: dict[str, dict] = {}
    without_id = 0
    header_tvg = None
    pending = None
    with open_maybe_gz(path) as raw:
        for bline in raw:
            line = bline.decode("utf-8", "replace").strip()
            if not line:
                continue
            if line.startswith("#EXTM3U"):
                a = dict(ATTR_RE.findall(line))
                header_tvg = (a.get("url-tvg") or a.get("x-tvg-url") or "").split(",")[0] or None
            elif line.startswith("#EXTINF"):
                head, _, title = line.partition(",")
                pending = dict(ATTR_RE.findall(head))
                pending["_title"] = title.strip()
            elif not line.startswith("#") and pending is not None:
                path_part = urlsplit(line).path.lower()
                is_vod = ("/movie/" in path_part or "/series/" in path_part
                          or path_part.endswith((".mp4", ".mkv", ".avi")))
                if not is_vod:
                    tid = pending.get("tvg-id", "").strip()
                    name = pending.get("tvg-name") or pending["_title"]
                    if not tid:
                        without_id += 1
                        note_no_id(pending.get("group-title", ""), name)
                    else:
                        ch = channels.setdefault(tid, {"names": [], "logo": None,
                                                       "countries": set()})
                        if name and name not in ch["names"]:
                            ch["names"].append(name)
                        ch["logo"] = ch["logo"] or pending.get("tvg-logo") or None
                        m = COUNTRY_PREFIX_RE.match(name or "") or \
                            COUNTRY_PREFIX_RE.match(pending.get("group-title", ""))
                        if m:
                            ch["countries"].add(country(m.group(1)))
                        m = re.search(r"\.([a-z]{2})$", tid, re.I)
                        if m:
                            ch["countries"].add(country(m.group(1)))
                pending = None
    return channels, without_id, header_tvg


def xtream_login(m3u_url: str):
    u = urlsplit(m3u_url)
    q = parse_qs(u.query)
    if not (u.path.endswith("get.php") and "username" in q and "password" in q):
        return None
    base = f"{u.scheme}://{u.netloc}{u.path[:-len('get.php')]}"
    return base, {"username": q["username"][0], "password": q["password"][0]}


NO_ID: dict[str, dict] = {}   # category -> {"count": n, "sample": [names]}


def note_no_id(category: str, name: str):
    c = NO_ID.setdefault(category or "(none)", {"count": 0, "sample": []})
    c["count"] += 1
    if len(c["sample"]) < 4:
        c["sample"].append(name)


def read_player_api(m3u_url: str, tmp: Path) -> tuple[dict[str, dict], int]:
    """Same channel list through player_api.php (what TV apps use)."""
    login = xtream_login(m3u_url)
    if not login:
        return {}, 0
    base, auth = login
    info_path = fetch_to_file(f"{base}player_api.php?{urlencode(auth)}", tmp / "info.json",
                              "account info")
    try:
        info = json.loads(info_path.read_text("utf-8", "replace")).get("user_info", {})
        log(f"  account: status={info.get('status')!r}, auth={info.get('auth')!r}, "
            f"formats={info.get('allowed_output_formats')}, "
            f"connections={info.get('active_cons')}/{info.get('max_connections')}")
    except (ValueError, AttributeError):
        log("  account info isn't JSON")
    cats = {}
    try:
        cp = fetch_to_file(f"{base}player_api.php?{urlencode({**auth, 'action': 'get_live_categories'})}",
                           tmp / "cats.json", "live categories")
        cats = {str(c.get("category_id")): c.get("category_name") or ""
                for c in json.loads(cp.read_text("utf-8", "replace")) or []}
    except (ValueError, RuntimeError, TypeError):
        pass
    sp = fetch_to_file(f"{base}player_api.php?{urlencode({**auth, 'action': 'get_live_streams'})}",
                       tmp / "live.json", "live channels")
    try:
        streams = json.loads(sp.read_text("utf-8", "replace"))
    except ValueError:
        return {}, 0
    channels: dict[str, dict] = {}
    without_id = 0
    for st in streams if isinstance(streams, list) else []:
        tid = (st.get("epg_channel_id") or "").strip()
        name = st.get("name") or ""
        if not tid:
            without_id += 1
            note_no_id(cats.get(str(st.get("category_id")), ""), name)
            continue
        ch = channels.setdefault(tid, {"names": [], "logo": None, "countries": set()})
        if name and name not in ch["names"]:
            ch["names"].append(name)
        ch["logo"] = ch["logo"] or st.get("stream_icon") or None
        for text in (name, cats.get(str(st.get("category_id")), "")):
            m = COUNTRY_PREFIX_RE.match(text)
            if m:
                ch["countries"].add(country(m.group(1)))
        m = re.search(r"\.([a-z]{2})$", tid, re.I)
        if m:
            ch["countries"].add(country(m.group(1)))
    return channels, without_id


# --------------------------------------------------------------------------- xmltv

def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    m = re.match(r"^(\d{14})\s*([+-]\d{4})?", s.strip())
    if not m:
        return None
    t = dt.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    off = m.group(2) or "+0000"
    sign = 1 if off[0] == "+" else -1
    tz = dt.timezone(sign * dt.timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
    return t.replace(tzinfo=tz)


def iter_xmltv(path: Path):
    """Yield ('channel'|'programme', element) without holding the whole file."""
    with open_maybe_gz(path) as f:
        ctx = etree.iterparse(f, events=("end",), tag=("channel", "programme"),
                              recover=True, huge_tree=True)
        for _, el in ctx:
            yield el.tag, el
            el.clear(keep_tail=False)
            while el.getprevious() is not None:
                del el.getparent()[0]


def serialize(el, channel_id: str) -> bytes:
    el.set("channel", channel_id)
    el.tail = None
    return etree.tostring(el, encoding="UTF-8").replace(b"\n", b"&#10;")


class Spool:
    """Programme lines on disk: '<channel id>\\t<programme xml>' per line."""

    def __init__(self, path: Path):
        self.path = path
        self.f = open(path, "wb")
        self.count: dict[str, int] = {}
        self.last_stop: dict[str, dt.datetime] = {}

    def add(self, cid: str, el, stop: dt.datetime):
        self.f.write(cid.encode() + b"\t" + serialize(el, cid) + b"\n")
        self.count[cid] = self.count.get(cid, 0) + 1
        if cid not in self.last_stop or stop > self.last_stop[cid]:
            self.last_stop[cid] = stop

    def close(self):
        self.f.close()


def in_window(el, lo, hi):
    start, stop = parse_time(el.get("start")), parse_time(el.get("stop"))
    if not start:
        return None
    stop = stop or start + dt.timedelta(minutes=30)
    if stop < lo or start > hi:
        return None
    return stop


def take_provider(path: Path, wanted: dict[str, dict], spool: Spool, lo, hi):
    lower = {k.lower(): k for k in wanted}
    seen = set()
    for tag, el in iter_xmltv(path):
        if tag != "programme":
            continue
        cid = lower.get((el.get("channel") or "").lower())
        if not cid:
            continue
        stop = in_window(el, lo, hi)
        if stop is None:
            continue
        key = (cid, el.get("start"))
        if key in seen:
            continue
        seen.add(key)
        spool.add(cid, el, stop)


def match_index(gaps: dict[str, dict], src_country: str | None):
    by_id, by_name = {}, {}
    for cid, ch in gaps.items():
        by_id.setdefault(norm_id(cid), cid)
        if ch["countries"] and src_country and src_country not in ch["countries"]:
            continue  # don't match a UK channel's name against a US guide
        for n in ch["names"] + [cid]:
            key = name_key(n)
            if key:
                by_name.setdefault(key, cid)
                if key.endswith("east"):
                    by_name.setdefault(key[:-4], cid)
    return by_id, by_name


def load_aliases() -> dict[str, str]:
    """aliases.json: {"our tvg-id": "public guide channel id"} for renamed channels."""
    p = HERE / "aliases.json"
    if not p.exists():
        return {}
    return {norm_id(k): v for k, v in json.loads(p.read_text()).items() if not k.startswith("_")}


def take_public(path: Path, src_country: str | None, gaps: dict[str, dict],
                spool: Spool, lo, hi, stats: dict) -> dict[str, str]:
    """Fill gaps from one public guide. Returns {our id: their id}."""
    by_id, by_name = match_index(gaps, src_country)
    aliases = load_aliases()
    by_alias = {norm_id(theirs): c for c in gaps for k, theirs in aliases.items()
                if k == norm_id(c)}
    mapping: dict[str, str] = {}   # their id -> our id
    used: set[str] = set()
    seen = set()
    stats["all"] = {}
    for tag, el in iter_xmltv(path):
        if tag == "channel":
            theirs = el.get("id") or ""
            ours = by_alias.get(norm_id(theirs)) or by_id.get(norm_id(theirs))
            if not ours:
                for dn in [theirs] + [d.text or "" for d in el.findall("display-name")]:
                    ours = by_name.get(name_key(dn))
                    if ours:
                        break
            if ours and ours not in used:
                mapping[theirs] = ours
                used.add(ours)
        elif tag == "programme":
            ours = mapping.get(el.get("channel") or "")
            if not ours:
                continue
            stats["all"][ours] = stats["all"].get(ours, 0) + 1
            stop = in_window(el, lo, hi)
            if stop is None or (ours, el.get("start")) in seen:
                continue
            seen.add((ours, el.get("start")))
            spool.add(ours, el, stop)
    return {o: t for t, o in mapping.items()}


def write_guide(out: Path, wanted: dict[str, dict], parts: list[tuple[Spool, set[str]]]):
    """parts: (spool, ids to take from it). Channels first, then programmes."""
    kept = sorted(set().union(*(ids for _, ids in parts)), key=str.lower)
    with gzip.open(out, "wb", compresslevel=9) as g:
        g.write(b'<?xml version="1.0" encoding="UTF-8"?>\n'
                b'<tv generator-info-name="strong8k-epg">\n')
        for cid in kept:
            ch = etree.Element("channel", id=cid)
            etree.SubElement(ch, "display-name").text = wanted[cid]["names"][0] if wanted[cid]["names"] else cid
            if wanted[cid]["logo"]:
                etree.SubElement(ch, "icon", src=wanted[cid]["logo"])
            g.write(b"  " + etree.tostring(ch, encoding="UTF-8") + b"\n")
        for spool, ids in parts:
            with open(spool.path, "rb") as f:
                for line in f:
                    cid, _, xml = line.partition(b"\t")
                    if cid.decode() in ids:
                        g.write(b"  " + xml)
        g.write(b"</tv>\n")
    return kept


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--m3u", help="M3U path or URL (default: $STRONG8K_M3U_URL)")
    ap.add_argument("--provider", help="provider XMLTV path/URL (default: from the M3U login)")
    ap.add_argument("--source", action="append",
                    help="public guide as country=path/url (default: sources.json)")
    ap.add_argument("--days", type=float, default=float(os.environ.get("STRONG8K_DAYS", 3)))
    ap.add_argument("--past-hours", type=float, default=4)
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--previous-report", help="report.json of the last run (outage guard)")
    ap.add_argument("--min-ratio", type=float, default=0.5,
                    help="refuse to publish if programmes drop below this share of last run")
    a = ap.parse_args(argv)

    m3u_src = (a.m3u or os.environ.get("STRONG8K_M3U_URL") or "").strip()
    if not m3u_src:
        log("Set STRONG8K_M3U_URL (or pass --m3u).")
        return 2
    if m3u_src.startswith("http"):
        u = urlsplit(m3u_src)
        q = parse_qs(u.query)
        mask(u.netloc, *(v[0] for k, v in q.items() if k in ("username", "password")))

    now = dt.datetime.now(dt.timezone.utc)
    lo, hi = now - dt.timedelta(hours=a.past_hours), now + dt.timedelta(days=a.days)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="s8k-"))
    report: dict = {"built_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "days": a.days}

    try:
        log("Channel list")
        channels, without_id, header_tvg = read_m3u(fetch_to_file(m3u_src, tmp / "list.m3u", "M3U"))
        if not channels and m3u_src.startswith("http"):
            log("  M3U has no usable channels; trying player_api.php")
            channels, without_id = read_player_api(m3u_src, tmp)
            report["channel_list_from"] = "player_api"
        if not channels:
            log("No live channels with a tvg-id in the M3U. Nothing to build.")
            return 1
        log(f"  {len(channels)} guide ids, {without_id} live entries without a tvg-id")
        report.update(guide_ids=len(channels), live_entries_without_tvg_id=without_id)

        parts: list[tuple[Spool, set[str]]] = []
        prov = Spool(tmp / "provider.spool")
        prov_src = a.provider or (provider_guide_url(m3u_src, header_tvg)
                                  if m3u_src.startswith("http") else None)
        if prov_src:
            log("Provider guide")
            try:
                take_provider(fetch_to_file(prov_src, tmp / "provider.xml", "provider guide"),
                              channels, prov, lo, hi)
            except (RuntimeError, etree.XMLSyntaxError) as e:
                log(f"  ! provider guide skipped: {e}")
                report["provider_error"] = str(e)
        prov.close()

        horizon = now + THIN
        good = {c for c in prov.count if prov.last_stop[c] >= horizon}
        gaps = {c: channels[c] for c in channels if c not in good}
        log(f"  provider covers {len(good)}; {len(gaps)} empty or short")
        report.update(provider_channels=len(good),
                      provider_short=len([c for c in prov.count if c not in good]))

        if a.source:
            sources = [{"name": s.split("=", 1)[1], "country": s.split("=", 1)[0],
                        "url": s.split("=", 1)[1]} for s in a.source]
        else:
            sources = json.loads((HERE / "sources.json").read_text())
        filled: dict[str, str] = {}
        report["sources"] = []
        for i, s in enumerate(sources):
            if not gaps:
                break
            log(f"Public guide: {s['name']}")
            spool = Spool(tmp / f"src{i}.spool")
            try:
                stats: dict = {}
                got = take_public(fetch_to_file(s["url"], tmp / f"src{i}.xml", s["name"]),
                                  country(s.get("country")), gaps, spool, lo, hi, stats)
                empty = {o: f"{t} ({stats['all'].get(o, 0)} programmes, none in window)"
                         for o, t in got.items() if not spool.count.get(o)}
            except (RuntimeError, etree.XMLSyntaxError) as e:
                log(f"  ! skipped: {e}")
                got, empty = {}, {}
            spool.close()
            ids = {c for c in got if spool.count.get(c)}
            for c in ids:
                filled[c] = s["name"]
                gaps.pop(c)
            (tmp / f"src{i}.xml").unlink(missing_ok=True)
            parts.append((spool, ids))
            log(f"  filled {len(ids)}")
            report["sources"].append({"name": s["name"], "filled": len(ids),
                                      "filled_ids": {o: got[o] for o in sorted(ids)},
                                      "matched_but_empty": empty})

        # provider programmes for every channel it has, unless a public guide replaced it
        parts.insert(0, (prov, {c for c in prov.count if c not in filled}))
        gz = out / "guide.xml.gz"
        kept = write_guide(gz, channels, parts)
        total = sum(sp.count.get(c, 0) for sp, ids in parts for c in ids)

        report.update(channels_in_guide=len(kept), channels_without_guide=len(gaps),
                      programmes=total, gz_mb=round(gz.stat().st_size / 1e6, 1),
                      missing_sample=sorted(gaps)[:200],
                      no_tvg_id_by_category=dict(sorted(NO_ID.items(),
                                                        key=lambda kv: -kv[1]["count"])))
        log(f"Guide: {len(kept)} channels, {total} programmes, {report['gz_mb']} MB")

        if gz.stat().st_size > MAX_GZ_BYTES:
            log("Guide is over 95 MB; lower --days / STRONG8K_DAYS.")
            return 1
        if not total:
            log("Guide is empty; not publishing.")
            return 1
        if a.previous_report and Path(a.previous_report).exists():
            prev = json.loads(Path(a.previous_report).read_text()).get("programmes", 0)
            if prev and total < prev * a.min_ratio:
                log(f"Only {total} programmes vs {prev} last time; looks like an outage, "
                    f"keeping the previous guide.")
                return 1
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
