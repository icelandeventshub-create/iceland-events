# Iceland Events feed for Squarespace

Every morning a GitHub Action collects upcoming events in Iceland, saves them to
`docs/events.json`, and GitHub Pages serves that file publicly. Code Blocks on
your Squarespace pages fetch it and show filterable, month-by-month lists.

**Ticket sellers and venues**
- **Tix.is** (Music, Theater, Family, Festivals, Christmas) and **Harpa**
- **Þjóðleikhúsið** (National Theatre): every performance, including shows on tour
- **Gamla bíó**, **Iðnó**, **Sena Live**
- **Bíó Paradís**: film festivals, the LOTR marathon and the like, plus party screenings,
  Svartir sunnudagar and midnight screenings (ordinary daily screenings are left out)
- **Nordic House** (Norræna húsið), **Kópavogur culture houses** (Salurinn, Gerðarsafn, library; exhibitions left out)

**Festivals**
- **Iceland Airwaves**: the official schedule, one listing per venue per night with the line-up and times
- **Reykjavík Pride / Hinsegin dagar**: the programme, which is published each summer for August

**Town and regional calendars**
- Reykjavík city calendar (Hvirfill), Hafnarfjörður, Garðabær, Mosfellsbær, Reykjanesbær, Árborg,
  Vestmannaeyjar, Ísafjarðarbær, Múlaþing, Akranes, Skagafjörður
- The regional tourism boards: South, West, North, East, Westfjords and Reykjanes, plus Visit Akureyri

**Listings sites**
- **Visit Reykjavík's events** (the Reykjavík Music City concert calendar)
- **Reykjavík Grapevine** events calendar
- **Vísir's events page** (Promogogo). Only the next few days are public, so each day's
  finds are added to what earlier days found and the list builds up over time.

**Sports:** HSÍ handball, KSÍ football, BLÍ volleyball, KKÍ basketball (see the settings in the script).

When the same show is listed by several sources it appears once: ticket sellers and
venues come first, then the town calendars, then the listings sites.

```
Tix · Harpa · venues · festivals · towns · regions · listings sites · sports ──► GitHub Action (daily) ──► events.json on GitHub Pages ──► Squarespace pages
```

## Files

| File | What it does |
|---|---|
| `scrape_events.py` | The scraper. Settings (days ahead, categories, sources) are at the top. |
| `requirements.txt` | Python packages it needs. |
| `.github/workflows/update-events.yml` | Runs the scraper daily at 05:17 and commits the new JSON. |
| `extra-events.txt` | Events you type in yourself (instructions at the top of the file). |
| `squarespace-embed*.html` | Code Blocks for your Squarespace pages. |

## How long a run takes

About 20–30 minutes. Several sites ask visitors to wait 5 seconds between pages
(the regional and town calendars), and the script respects that. The first run after
adding new sources is the slowest; after that it reuses what it already knows.

## Adjusting it

- **Which sites are read:** the settings block near the top of `scrape_events.py`.
  `MOYA_CALENDARS` lists the regional and town calendars; to add another town whose
  events page looks like south.is or isafjardarbaer.is, add a line with its name, the
  events page address and its region.
- **Visit Reykjavík categories:** `VISITRVK_CATEGORIES` (guided tours, workshops and
  exhibitions are left out).
- **Vísir:** `VISIR_FEEDS` and `VISIR_SKIP` (courses, conferences, church services and
  long-running exhibitions are left out).
- **Long exhibitions:** listings that run more than `MAX_RANGE_DAYS` (31) days are shown
  only on their opening day.
- **How far ahead:** `DAYS_AHEAD` (default 120).
- **If a site changes its layout** and returns nothing, the script keeps that source's
  previous events, and the run log shows `-> keeping N previous ... events`. That's
  the signal the reader for that site needs updating.
- **Venues without a region:** the run log lists them ("no region for N venue(s): …");
  add a word for each to `PLACE_WORDS`.

## Good manners

The scraper reads each site's `robots.txt` (including any requested wait between pages),
waits at least a second between requests, and links every event back to its original
page. If you plan to republish heavily or commercially, consider asking the bigger
sources whether they offer an official feed. Promogogo (which runs the event databases
behind Vísir and Visit Reykjavík) offers partner feeds that would give you everything
they have, not just the next few days.
