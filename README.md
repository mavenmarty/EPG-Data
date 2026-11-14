# EPG-Data

XMLTV guide for **adult (18+) channels only**, built from the public TV listings on
[filmweb.pl](https://www.filmweb.pl/program-tv). It's rebuilt daily by GitHub Actions.

## Guide URL

After you push this repo to GitHub and the workflow has run once:

```
https://raw.githubusercontent.com/mavenmarty/EPG-Data/main/public/guide.xml.gz
```

Add that URL as an XMLTV / EPG source in TiviMate, Kodi (PVR IPTV Simple), Jellyfin,
Channels DVR, etc.

## Channels (26)

Adult Channel, Babes TV, Bang U, Barely Legal TV, Beate-Uhse.TV, Blue Hustler,
Brazzers TV Europe, Desire TV, Dorcel TV, Dorcel XXX, Erox, Eroxxx, Hustler HD,
Hustler TV, Penthouse Gold, Penthouse Passion, Penthouse Reality TV, Playboy TV,
Private TV, Pure Babes TV HD, Reality Kings TV, Redlight, TinyTeen,
True Amateurs TV, Vivid Red HD, Vivid Touch.

These are edited in `channels.json`:

| field        | meaning                                                   |
|--------------|-----------------------------------------------------------|
| `filmweb_id` | number in `filmweb.pl/program-tv/<id>`                     |
| `name`       | display name written to the guide                         |
| `xmltv_id`   | channel id in the guide – **must match your M3U `tvg-id`** |

To add a channel, find it on the Filmweb channel list
(`/program-tv/lista-kanalow-tv`), copy its id, and add a line.

## Matching your playlist

Either rename the `tvg-id` values in your M3U to the `xmltv_id` values above, or change
`xmltv_id` in `channels.json` to whatever your provider already uses:

```
#EXTINF:-1 tvg-id="BrazzersTVEurope.eu" group-title="Adult",Brazzers TV Europe
```

## Running locally

```bash
pip install -r requirements.txt
python grab.py                 # 7 days, all channels -> public/guide.xml + .gz
python grab.py --days 3 --channel 153 --channel 279
```

Days: Filmweb shows today, tomorrow, and about 11 more days (`--days` max 13).
Times are Europe/Warsaw and are written with the correct UTC offset, so players
convert them to your local time automatically.

## If the guide comes out empty

Filmweb can change its page layout. Run:

```bash
python grab.py --channel 153 --days 1 --debug-html debug/
python grab.py --from-html debug/153_0.html --channel 153
```

and check `debug/153_0.html`. The parser lives in `parse_text()` in `grab.py`. It reads
the visible text in order (time → title → type → genre) and stops at the
"Kanały o podobnej tematyce" / "Opis kanału" sections.

## Notes

- Data is limited to what Filmweb shows: start time, title, type and genre. Stop times
  are taken from the next programme's start.
- The grabber waits 1.5 s between requests (`--delay`). A full 7-day run is about
  180 requests and takes about 5 minutes.
- Listings belong to Filmweb and the broadcasters. Check filmweb.pl's terms before you
  redistribute the guide publicly. Keeping the repo private is the safe default.
- Every programme is tagged `<category>Adult</category>` and rated 18, so players with
  parental controls can lock it.
