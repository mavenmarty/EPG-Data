"""Run: python -m pytest strong8k/tests (fixtures are regenerated relative to now)."""
import datetime as dt
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import build  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)


def _t(t):
    return t.strftime("%Y%m%d%H%M%S +0000")


def progs(ch, hours, title):
    return "".join(
        f'<programme start="{_t(NOW + dt.timedelta(hours=h))}" '
        f'stop="{_t(NOW + dt.timedelta(hours=h + 1))}" channel="{ch}">'
        f"<title>{title}</title></programme>\n" for h in range(-6, hours))


def run(tmp, extra=()):
    tv = lambda body: f'<?xml version="1.0"?><tv>{body}</tv>'
    (tmp / "p.xml").write_text(tv(progs("ESPN.us", 72, "prov") + progs("AMC.us", 3, "prov")))
    (tmp / "us.xml").write_text(tv(
        '<channel id="AMC.HD.us2"><display-name>AMC HD</display-name></channel>'
        '<channel id="HBO.us2"><display-name>HBO</display-name></channel>'
        '<channel id="BBC.One.us"><display-name>BBC One</display-name></channel>'
        + progs("AMC.HD.us2", 72, "us") + progs("HBO.us2", 72, "us")
        + progs("BBC.One.us", 72, "wrong")))
    (tmp / "gb.xml").write_text(tv(
        '<channel id="BBCOne.uk"><display-name>BBC One</display-name></channel>'
        + progs("BBCOne.uk", 72, "gb")))
    out = tmp / "out"
    rc = build.main(["--m3u", str(HERE / "lineup.m3u"), "--provider", str(tmp / "p.xml"),
                     "--source", f"us={tmp / 'us.xml'}", "--source", f"gb={tmp / 'gb.xml'}",
                     "--out", str(out), *extra])
    return rc, out


def test_builds_merged_guide(tmp_path):
    rc, out = run(tmp_path)
    assert rc == 0
    t = etree.parse(gzip.open(out / "guide.xml.gz"))
    ids = [c.get("id") for c in t.findall("channel")]
    assert ids == ["AMC.us", "BBCOne.uk", "ESPN.us", "hbo.east"]
    got = Counter((p.get("channel"), p.findtext("title")) for p in t.findall("programme"))
    assert set(got) == {("ESPN.us", "prov"), ("AMC.us", "us"),
                        ("hbo.east", "us"), ("BBCOne.uk", "gb")}
    rep = json.loads((out / "report.json").read_text())
    assert rep["live_entries_without_tvg_id"] == 1
    assert rep["channels_without_guide"] == 0


def test_outage_guard_keeps_previous_guide(tmp_path):
    prev = tmp_path / "prev.json"
    prev.write_text(json.dumps({"programmes": 10_000}))
    rc, _ = run(tmp_path, ["--previous-report", str(prev)])
    assert rc == 1


def test_guide_url_from_m3u_login():
    url = build.provider_guide_url(
        "http://h.example:8080/get.php?username=u&password=p&type=m3u_plus&output=ts", None)
    assert url == "http://h.example:8080/xmltv.php?username=u&password=p"


def test_names():
    assert build.norm_name("US| ESPN ᴴᴰ") == "espn"
    assert build.norm_name("A&E.us") == "a and e"
