import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build  # noqa: E402
import events  # noqa: E402

NOW = dt.datetime(2026, 11, 21, 13, 0, tzinfo=dt.timezone.utc)
UTC = dt.timezone.utc


def utc(p):
    return p[2].astimezone(UTC).strftime("%m-%d %H:%M")


def test_formats():
    cases = {
        "LIVE | GRAND SLAM OF CURLING | Sat 21 Nov 06:30 EST (US) | 8K EXCLUSIVE | US: ESPN+ PPV 2":
            ("US: ESPN+ PPV 2", "GRAND SLAM OF CURLING", "11-21 11:30"),
        "Next | Cheminots vs. Kouilou | Ligue 1 | 2026-11-21 | 13:00 (GMT) | 8K EXCLUSIVE | GB: DAZN PPV 22":
            ("GB: DAZN PPV 22", "Cheminots vs. Kouilou", "11-21 13:00"),
        "US (ESPN+ 209) | Real Madrid vs. Celta de Vigo Nov 21 2:30PM ET (2026-11-21 14:30:10)":
            ("US (ESPN+ 209)", "Real Madrid vs. Celta de Vigo", "11-21 19:30"),
        "(US) (BTN+ 068) | Basketball (W): Penn at UCLA (2026-11-29 16:50:00)":
            ("(US) (BTN+ 068)", "Basketball (W): Penn at UCLA", "11-29 21:50"),
        "NCAAM: Siena vs Bucknell @ Nov 21 11:00 AM :NCAAB  02":
            ("NCAAB 02", "NCAAM: Siena vs Bucknell", "11-21 16:00"),
    }
    for name, (slot, title, when) in cases.items():
        p = events.parse(name, NOW)
        assert (p[0], p[1], utc(p)) == (slot, title, when), name


def test_no_event_and_not_events():
    for n in ["- NO EVENT STREAMING - | 8K EXCLUSIVE | US: ESPN+ PPV 527", ":NCAAB  66",
              "US (WNBA 31) |  (2098-12-31 13:00:30)"]:
        assert events.parse(n, NOW)[1] is events.NO_EVENT, n
    for n in ["US: ESPN HD", "24/7: 30 ROCK", "####### ESPN+ PPV #######"]:
        assert events.parse(n, NOW) is None, n


def test_blocks_cover_window():
    lo, hi = NOW - dt.timedelta(hours=4), NOW + dt.timedelta(days=3)
    p = events.parse("NCAAM: Siena vs Bucknell @ Nov 21 11:00 AM :NCAAB  02", NOW)
    b = events.programmes(p, lo, hi, dt.timedelta(hours=3))
    assert [x[2] for x in b] == ["Next: NCAAM: Siena vs Bucknell", "NCAAM: Siena vs Bucknell",
                                 "Ended: NCAAM: Siena vs Bucknell"]
    assert b[0][0] == lo and b[-1][1] == hi


def test_take_events_ids(tmp_path):
    build.LIVE_ROWS[:] = [
        {"sid": "1", "name": "LIVE | A vs B | Sat 21 Nov 08:00 EST (US) | US: ESPN+ PPV 1",
         "logo": None, "tid": "1.espn", "category": "US| ESPN+ PPV"},
        {"sid": "2", "name": "LIVE | C vs D | Sat 21 Nov 09:00 EST (US) | US: ESPN+ PPV 2",
         "logo": None, "tid": "", "category": "US| ESPN+ PPV"},
        {"sid": "3", "name": "LIVE | E vs F | Sat 21 Nov 09:00 EST (US) | US: ESPN+ PPV 3",
         "logo": None, "tid": "HasGuide.us", "category": "US| ESPN+ PPV"},
    ]
    gaps = {"1.espn": {"names": [], "logo": None, "countries": set()}}
    channels = dict(gaps)
    sp = build.Spool(tmp_path / "e")
    st = build.take_events(gaps, channels, sp, NOW, NOW - dt.timedelta(hours=4),
                           NOW + dt.timedelta(days=1), 3)
    sp.close()
    assert set(sp.count) == {"1.espn", "strong8k-2"}
    assert channels["strong8k-2"]["names"] == ["US: ESPN+ PPV 2"]
    assert st["by_id"] == 1 and st["by_stream"] == 1
    build.LIVE_ROWS.clear()
