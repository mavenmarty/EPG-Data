# Strong 8K guide

An XMLTV guide for the Strong 8K live lineup, rebuilt every 4 hours by
`.github/workflows/strong8k.yml`.

## Guide URL

```
https://raw.githubusercontent.com/mavenmarty/EPG-Data/strong8k-guide/guide.xml.gz
```

Coverage of the last run: `https://raw.githubusercontent.com/mavenmarty/EPG-Data/strong8k-guide/report.json`

The guide uses the provider's own `tvg-id` values, so it matches the playlist with
no renaming.

## How it's built

1. **Channel list:** the live channels in the M3U link (movies and series are skipped),
   grouped by `tvg-id`. Entries without a `tvg-id` can't be matched by a player and are
   only counted.
2. **Provider guide:** `xmltv.php` with the same login as the M3U link.
3. **Gaps:** a channel the provider leaves empty, or whose guide ends within 12 hours, is
   filled from the free guides in `sources.json`, in order (epgshare01: US, US sports,
   US locals, Canada, UK). Matching is by id, then by name with quality tags (HD, FHD,
   4K…) and country prefixes (`US|`, `UK:`…) removed. A channel marked for one country
   is never matched against another country's guide.
4. **Event channels:** PPV/event slots (ESPN+, Flo, DAZN, BTN+, NCAAB…) get listings
   built from the channel's own name ("Next: …", the event, "Ended: …", or "No event
   scheduled"). Times without a zone are US Eastern; events are 3 hours long
   (`STRONG8K_EVENT_HOURS`; `STRONG8K_EVENTS=0` turns this off). A slot with a `tvg-id`
   uses it; a slot without one is listed as `strong8k-<stream number>`, named after the
   slot (e.g. "US: ESPN+ PPV 2"), so a player can match it by hand or by name.
5. **Window:** 4 hours back to 3 days ahead. Change it with the repository variable
   `STRONG8K_DAYS`.

The result is published to the `strong8k-guide` branch, replaced on every run so the
repo doesn't grow by a guide every 4 hours.

## Setup

Repository secrets (Settings › Secrets and variables › Actions):

| secret               | value                                                            |
|----------------------|------------------------------------------------------------------|
| `STRONG8K_M3U_URL`   | the M3U link (`…/get.php?username=…&password=…&type=m3u_plus`)   |
| `STRONG8K_XMLTV_URL` | optional: the provider's guide link, if it isn't `xmltv.php` on the same server |

The login is masked in the logs and never written to the repo.

## Safety checks

- No guide is published if it comes out empty, is over 95 MB, or has less than half
  the programmes of the last published one (provider outage). The previous guide stays
  up and the run is marked failed.
- If the provider refuses GitHub's servers, the run log says which step failed
  (HTTP code only).

## Running locally

```bash
pip install requests lxml
STRONG8K_M3U_URL='…' python strong8k/build.py      # -> strong8k/out/
python -m pytest strong8k/tests
```
