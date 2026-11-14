import datetime as dt
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grab  # noqa: E402

HTML = (Path(__file__).parent / "fixture_channel.html").read_text(encoding="utf-8")
DAY = dt.date(2026, 11, 14)


def test_parse_and_rollover():
    progs, logo = grab.parse_page(HTML, DAY)
    progs = grab.finalize(progs)
    assert logo.endswith("example-logo.png")
    assert [p["title"] for p in progs] == [
        "Morning Title One", "Second & Title", "Late Title",
        "Pre-Midnight Title", "After Midnight Title", "Last Title",
    ]
    assert progs[0]["start"].date() == DAY
    assert progs[4]["start"].date() == DAY + dt.timedelta(days=1)  # 00:20 rolls over
    assert progs[0]["stop"] == progs[1]["start"]
    assert progs[-1]["stop"] - progs[-1]["start"] == grab.DEFAULT_LAST_DURATION
    assert progs[0]["type"] == "Film" and progs[0]["genre"] == "film erotyczny"
    assert progs[0]["start"].utcoffset() == dt.timedelta(hours=1)  # CET in November


def test_xmltv_valid():
    progs, logo = grab.parse_page(HTML, DAY)
    ch = {"filmweb_id": 1, "name": "Example TV", "xmltv_id": "ExampleTV.eu", "logo": logo}
    xml = grab.build_xmltv([ch], {"ExampleTV.eu": grab.finalize(progs)})
    root = ET.fromstring(xml.split("\n", 2)[2])
    assert root.find("channel").get("id") == "ExampleTV.eu"
    assert len(root.findall("programme")) == 6
    assert root.find("programme").get("start") == "20261114060500 +0100"


def test_day_paths():
    assert grab.day_path(0) == ""
    assert grab.day_path(1) == "/jutro"
    assert grab.day_path(5) == "/5"
