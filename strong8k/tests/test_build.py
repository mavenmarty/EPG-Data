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


def test_public_short_forms_match():
    pairs = [("skysportsfootball.uk", "SkySp.Fball.HD.uk"),
             ("skysportsmainevent.uk", "SkySpMainEvHD.uk"),
             ("Dave.uk", "U.and.Dave.HD.uk"),
             ("DisneyJunior.uk", "Disney.Jr.HD.uk"),
             ("FoxBusiness.us", "Fox.Business.HD.us2")]
    for ours, theirs in pairs:
        assert build.name_key(ours) == build.name_key(theirs)
    assert build.name_key("NBCSports.us") != build.name_key("NBC.Sports.HD.1.us2")


def test_timeshift_and_pacific_copies_are_not_used(tmp_path):
    gaps = {"Alibi.uk": {"names": ["UK| ALIBI HD"], "logo": None, "countries": {"gb"}},
            "HBO2.us": {"names": ["US| HBO 2"], "logo": None, "countries": {"us"}}}
    (tmp_path / "s.xml").write_text(
        '<tv><channel id="alibi+1.uk"><display-name>Alibi</display-name></channel>'
        '<channel id="HBO2.(Pacific).us2"><display-name>HBO2</display-name></channel>'
        + progs("alibi+1.uk", 5, "x") + progs("HBO2.(Pacific).us2", 5, "x") + "</tv>")
    lo, hi = NOW - dt.timedelta(hours=4), NOW + dt.timedelta(days=1)
    spool = build.Spool(tmp_path / "sp")
    got = build.take_public(tmp_path / "s.xml", None, gaps, spool, lo, hi, {})
    spool.close()
    assert got == {}


def test_plain_feed_beats_earlier_plus_one(tmp_path):
    gaps = {"Alibi.uk": {"names": ["UK| ALIBI", "UK| ALIBI +1"], "logo": None,
                         "countries": {"gb"}}}
    (tmp_path / "s.xml").write_text(
        '<tv><channel id="alibi+1.uk"><display-name>Alibi</display-name></channel>'
        '<channel id="U.and.alibi.HD.uk"><display-name>U&amp;alibi</display-name></channel>'
        + progs("alibi+1.uk", 5, "x") + progs("U.and.alibi.HD.uk", 5, "x") + "</tv>")
    lo, hi = NOW - dt.timedelta(hours=4), NOW + dt.timedelta(days=1)
    spool = build.Spool(tmp_path / "sp")
    got = build.take_public(tmp_path / "s.xml", "gb", gaps, spool, lo, hi, {})
    spool.close()
    assert got == {"Alibi.uk": "U.and.alibi.HD.uk"}


def test_local_call_signs():
    assert build.name_key("KSDK.us") == build.name_key("KSDK-DT.us_locals1") == "ksdk"
