# Iceland Events feed for Squarespace

Every morning a GitHub Action collects upcoming events in Iceland, saves them to
`docs/events.json`, and GitHub Pages serves that file publicly. Code Blocks on
your Squarespace pages fetch it and show filterable, month-by-month lists.

## Sources

**Ticket sellers and venues**
- **Tix.is** (Music, Theater, Family, Festivals, Christmas) and **Harpa.is**
- **Þjóðleikhúsið** (National Theatre): every performance from its own calendar, including touring shows
- **Gamla bíó**, **Iðnó** (repeat dates grouped), **Sena Live** (big concerts, stand-up)
- **Bíó Paradís**: film festivals and one-off events, plus party screenings, Svartir sunnudagar
  and midnight screenings (ordinary daily screenings are left out)
- **Nordic House** (Norræna húsið)
- **Kópavogur culture houses:** Salurinn, Gerðarsafn, the library and more (exhibitions left out)

**Festivals**
- **Iceland Airwaves**: the official schedule, one listing per venue per night with the line-up and set times
- **Reykjavík Pride / Hinsegin dagar**: the programme (published each summer for August)

**Town calendars:** Reykjavík (Hvirfill), Hafnarfjörður, Garðabær, Mosfellsbær, Reykjanesbær,
Árborg, Vestmannaeyjar, Ísafjarðarbær, Múlaþing, Akranes, Skagafjörður

**Regional calendars:** the tourism boards for South, West, North, East, Westfjords and
Reykjanes, plus Visit Akureyri

**Listings sites**
- **Visit Reykjavík's events** (the Reykjavík Music City concert calendar): music, theatre,
  stand-up, talks and readings
- **Reykjavík Grapevine** events calendar
- **Vísir's events page** (Promogogo). Only the next few days are public, so each day's finds
  are added to earlier days' and the list builds up over time.

**Sports**
- **Handball:** HSÍ (Olís deild, Grill 66, Powerade bikar, European home games,
  Iceland national team home games)
- **Volleyball:** BLÍ (Unbrokendeild karla/kvenna, Kjörísbikar), found automatically each season
- **Basketball:** KKÍ (Bónus deild, 1. deild, VÍS bikar, men's and women's). KKÍ loads its
  schedules with JavaScript, so the workflow opens its public schedule pages in a hidden
  browser (Playwright) and reads the games the way a visitor sees them. League ids are in
  `KKI_LEAGUES`.
- **Football:** KSÍ (Besta deild, Lengjudeild, Mjólkurbikar, Lengjubikar,
  Iceland national team home games). The season runs roughly April–October, so
  expect few or no football matches in winter.

When the same show is listed by several sources it appears once. Ticket sellers and venues
win, then town calendars, then the listings sites.

**Looked at but not used:** proevents.is (a corporate event planner with no public events
list), kalenda.is (a hand-built page copying other sites, with stale entries; its original
sources are used instead) and eventa.is (no public feed; its data is a private copy of
Facebook events).

```
Tix · Harpa · venues · Airwaves · Pride · towns · regions · Visit Reykjavík · Grapevine · Vísir · HSÍ · KSÍ · BLÍ · KKÍ ──► GitHub Action (daily) ──► events.json on GitHub Pages ──► Squarespace pages
```

## Files

| File | What it does |
|---|---|
| `scrape_events.py` | The scraper. Settings (days ahead, categories, sources, competitions) are at the top. |
| `requirements.txt` | Python packages it needs. |
| `.github/workflows/update-events.yml` | Runs the scraper daily at 05:17 and commits the new JSON. |
| `extra-events.txt` | Events you type in yourself. |
| `squarespace-embed-events.html` | Code Block for the music & arts page (hides sports). |
| `squarespace-embed-sports.html` | Code Block for the sports page. |
| `squarespace-embed-header.html` | Homepage header: logo, intro line, live event count and quick buttons. |
| `squarespace-embed-picks.html` | Code Block for the "This week's picks" carousel. |
| `squarespace-embed-home.html` | Homepage events list: search box, three dropdown filters, 14 specific categories incl. Family & Kids. |
| `featured.txt` | This week's picks for the homepage carousel (you edit this weekly). |
| `fetch_featured.py` | Reads each pick's link for its title, date, venue and picture. |
| `.github/workflows/update-featured.yml` | Runs it whenever `featured.txt` is saved. |

Feed address: `https://icelandeventshub-create.github.io/iceland-events/events.json`
(already filled in to the embeds).

## The homepage, top to bottom

Your Squarespace site header (menu and logo) stays as it is. Below it, three Code Blocks:

1. **Header** (`squarespace-embed-header.html`)
2. **This week's picks** (`squarespace-embed-picks.html`)
3. **Events list** (`squarespace-embed-home.html`)

Each is pasted whole into its own Code Block (mode: HTML).

## Setup

1. **Create a GitHub repo** named `iceland-events`, **Public**.
2. **Upload the files.** On an empty repo use the *uploading an existing file*
   link. The upload skips the `.github` folder, so create
   `.github/workflows/update-events.yml` with *Add file → Create new file* and
   paste its contents.
3. **Allow the Action to save files.** *Settings → Actions → General → Workflow
   permissions* → **Read and write permissions** → Save.
4. **Run it once.** *Actions* → *Update Iceland events* → **Run workflow**. Takes
   about 20–30 minutes (several town and regional sites ask for 5 seconds between
   pages). The run log shows how many events each source returned.
5. **Turn on GitHub Pages.** *Settings → Pages* → *Deploy from a branch*, `main`,
   `/docs` → Save.
6. **Squarespace.** On each page, add a **Code** block (mode: HTML) and paste the
   matching embed file's full contents.

From then on everything refreshes daily; nothing to do in Squarespace.

## Updating the scraper

To replace `scrape_events.py` with a new version: open the repo on GitHub → *Add file →
Upload files* → drag in the new file → *Commit changes*. Then *Actions* → *Update Iceland
events* → **Run workflow** to see the result straight away.

## Adding your own events

`extra-events.txt` holds events you type in yourself, e.g. a national volleyball home
game or a weekly bar night. One block per event (title, date, time, venue, category,
link…), with an empty line between events; instructions and examples are at the top of
the file. Use `repeat: weekly` (+ `until:`) for weekly events and `national: yes` to put
a match in the national team box. Saving the file on GitHub starts an update right away,
so the site changes once the run finishes.

## Homepage header

`squarespace-embed-header.html` goes at the very top of the homepage. It shows your logo, the
line "Everything happening in Iceland, in one place", a short intro, a live count from the
feed ("630 upcoming events · 31 today · 92 this weekend"; sports aren't counted) and four
quick buttons: **Today**, **This weekend**, **Family & Kids** and **Search all events**. Each
button sets the filters on the events list further down and scrolls to it; Search puts the
cursor in the search box.

The words are ordinary HTML: edit the text between the tags. If your Squarespace site header
already shows the logo, delete the `<img class="ieh-hero-logo" …>` line to avoid showing it twice.

## This week's picks (homepage carousel)

A carousel near the top of the homepage shows your hand-picked recommendations, each with a
picture, date, venue, a **Why we picked it** line and a ticket button.

**Setting it up (once)**
1. Upload `featured.txt` and `fetch_featured.py` to the repo (*Add file → Upload files*).
2. Create `.github/workflows/update-featured.yml` with *Add file → Create new file* and
   paste its contents (the upload skips the `.github` folder).
3. On the homepage in Squarespace, add a **Code** block below the header (mode: HTML) and
   paste all of `squarespace-embed-picks.html`.

**Every week**
1. On GitHub, open `featured.txt` → pencil icon.
2. Replace the picks: a `link:` and a `why:` for each, with an empty line between picks.
3. *Commit changes*. A few minutes later the carousel shows the new picks.

The title, date, venue and picture are read from each link (the same details a link shows
when shared on social media). If one comes out wrong, add `title:`, `date:`, `venue:` or
`image:` under that pick to set it yourself; `button:` changes the button text (e.g.
`Free entry`). Instagram and Facebook links can't be read, so give those a title and image.

Pictures are copied into `docs/featured/`, so the carousel doesn't depend on the other
site. If a page can't be reached, last run's details for that link are kept. A pick with no
picture shows your logo instead. Picks whose date has passed drop off on their own, and the
carousel hides itself if nothing is left. *Actions → Update this week's picks* shows what was
found for each link.

Settings at the top of the embed's script: `AUTOPLAY_SECONDS` (7; 0 stops it sliding on its
own) and `BUTTON_TEXT`.

## Homepage events list

`squarespace-embed-home.html` goes below the carousel. Above the list is one row: a **search
box** and three **dropdowns**: When (Any date, Today, Tomorrow, This weekend, Next 7 days,
This month, Next month), Category and Where. Each option shows how many events it has; a
dropdown that's in use turns dark, and **Clear filters** resets everything. On a phone the
search box sits above the three dropdowns.

The list shows every upcoming event, grouped by day: up to 6 per day ("Show N more" opens the
rest), 10 days at a time ("Show more days" loads the next 10). While searching, every match
for a day is shown.

**Links that open the list already filtered** (handy for Instagram stories and bio links):
- `icelandeventshub.com/?when=weekend` (also `today`, `tomorrow`, `week`, `month`, `nextmonth`)
- `icelandeventshub.com/?cat=Comedy` (any category name; `&` is written `%26`, e.g. `?cat=Family%20%26%20Kids`)
- `icelandeventshub.com/?q=Akureyri` (a search)
- combined: `icelandeventshub.com/?when=weekend&cat=Concerts%20%26%20Music`

**Categories.** The Category dropdown has 14 specific categories: Concerts & Music, Theatre &
Dance, Comedy, Film, Art & Exhibitions, Talks & Learning, Workshops & Classes, Clubs & Meetups,
Festivals, Christmas, Food Drink & Markets, Nightlife & Games, Outdoors & Tours, Family & Kids.
They're worked out on the page from words in each event's title (`CATEGORIES` in the script),
so an event can be in several: a Christmas concert is under both Concerts & Music and
Christmas. The small label beside an event is its main category (`PRIORITY` decides which).
When the title gives no clue, the venue decides (Harpa → music, Bíó Paradís → film, galleries →
art), and failing that the feed's own category (`FALLBACK`). Family events appear only under
Family & Kids, so kids' concerts don't fill Concerts & Music. Sports are hidden here (`HIDE`).
To fix an event that lands in the wrong place, add a word to that category's line. Only this
page uses these categories; the scraper and the other pages are unchanged.

**Forgiving search.** It looks in the title, subtitle, venue and source town, and:
- ignores accents and Icelandic letters (`hafnarfjordur` = `Hafnarfjörður`, `thjodleikhusid` = `Þjóðleikhúsið`)
- allows a typo (one in words of 5–8 letters, two in longer words, none in short words)
- matches other endings of the same word (Akureyri / Akureyrar, Kópavogur / Kópavogi,
  Garðabær / Garðabæjar) and the -fjörður / -fjarðar / -firði and -staðir / -stöðum forms
- finds a word inside a compound (`tónleikar` finds *Jólatónleikar*)
- treats the words in `SAME_WORDS` as the same, e.g. concert = tónleikar, Christmas = jól,
  Harpa = Hörpu, film = kvikmynd = bíó. Add a line there for any other pair people use.
Several words must all match (`jazz harpa` finds jazz events at Harpa).

Other settings at the top of its script: `DAYS` (0 = no limit), `PER_DAY` (6),
`DAYS_AT_ONCE` (10; 0 = all at once) and `ALL_EVENTS_URL` (your full events page's address,
or `""` to hide that button).

**Kids' events:** after collecting, the scraper moves any event whose title mentions children
(barna, börn, krakka, fjölskyldu, sögustund, leikskóla, toddler, kids, family programme…)
into Family, so they all land in Family & Kids wherever they came from. The words are in
`KIDS_WORDS` in `scrape_events.py`; the run log shows how many were moved. Sports matches
and your own `extra-events.txt` entries are never changed.

## Filters on the other pages

Each list has three rows: **What** (category buttons with icons), **When** (any
date, today, tomorrow, this weekend (Fri–Sun), next 7 days, this month, or a
picked day) and **Where** (Reykjavík area, Reykjanes, West & Westfjords, North,
East, South). The Where row appears once there are events from at least two regions.

Regions come from postcodes where a source has them, otherwise from venue and town
names (`PLACE_WORDS` in `scrape_events.py`), the regional calendar an event came
from, and, for matches, the home club (`CLUB_REGIONS`). The run log lists any venue
it couldn't place ("no region for N venue(s): …"); add a word for it to
`PLACE_WORDS` and it will be placed from the next run.

## Adjusting it

- **Which categories a page shows:** near the top of each embed's script,
  `SHOW` lists categories to include (empty = all) and `HIDE` lists ones to leave
  out. Category names: Music, Arts & Theatre, Family, Festivals, Christmas,
  Music & Arts, Culture & Learning, Outdoors, Football, Handball, Volleyball, Basketball.
  (The homepage uses its own specific categories instead; see above.)
- **Music & arts categories:** `TIX_CATEGORIES` in `scrape_events.py` (keys must
  match the menu labels on tix.is/en).
- **Regional and town calendars:** `MOYA_CALENDARS` lists the sites built on the same
  system as south.is. Another town with an events page like isafjardarbaer.is/is/vidburdir
  can be added with one line (name, events page address, region).
- **Visit Reykjavík / Vísir:** `VISITRVK_CATEGORIES`, `VISIR_FEEDS` and `VISIR_SKIP`
  (courses, conferences, church services, guided tours and long exhibitions are left out).
- **Long exhibitions:** listings running more than `MAX_RANGE_DAYS` (31) days appear
  only on their opening day.
- **Sports competitions:** `HSI_COMPETITIONS` and `KSI_COMPETITIONS` in
  `scrape_events.py`. A match is included when its competition name contains one
  of the listed words. Youth and school games are left out.
- **How far ahead:** `DAYS_AHEAD` (default 120).
- **Remove events sooner:** events stay listed for 3 hours after they start
  (`3 * 3600e3` in the embed). Change that line to `>= now` to remove them at
  start time.
- **Colours:** `--ie-accent` at the top of the `<style>` in each embed (the homepage
  embeds use your Squarespace button colour).
- **If a site changes its layout** and returns nothing, the script keeps that
  source's previous events, and the run log shows
  `-> keeping N previous ... events`. That's the signal the parser needs updating.

## Good manners

The scraper reads each site's `robots.txt`, including any requested wait between pages,
waits at least a second between requests, and fetches at most 400 Tix event pages a day.
Every event links back to its original page. If you plan to republish heavily or
commercially, consider asking Tix, Harpa, HSÍ and KSÍ whether they offer an official
feed. Promogogo, which runs the event databases behind Vísir and Visit Reykjavík, offers
partner feeds that would give you all their events rather than just the next few days.
