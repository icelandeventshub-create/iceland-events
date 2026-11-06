#!/usr/bin/env python3
"""
Pull upcoming events in Iceland and write them to docs/events.json for a
Squarespace Code Block to display:
  - music, arts, family and festival events from Tix.is (nationwide) and Harpa.is
  - North Iceland events from Visit Akureyri (visitakureyri.is)
  - Reykjavík city's own events calendar (Hvirfill, reykjavik.is)
  - handball matches from HSÍ (hsi.is)
  - football matches from KSÍ (ksi.is)
  - volleyball matches from BLÍ (bli-web.dataproject.com)
  - basketball games from KKÍ (kki.is), read from its public schedule pages
    with a hidden browser, the way a visitor sees them
  - Kópavogur culture houses (menning.kopavogur.is): Salurinn, Gerðarsafn, library...
  - Hafnarfjörður town events (hafnarfjordur.is): Hafnarborg, library, museum...
  - Iðnó (idno.is) and Sena Live (senalive.is), added only when not already on Tix
  - Þjóðleikhúsið, Gamla bíó, Bíó Paradís (festivals and screening series), Nordic House
  - Iceland Airwaves (festival schedule) and Reykjavík Pride (Hinsegin dagar programme)
  - regional tourism calendars (South, West, North, East, Westfjords, Reykjanes) and town
    calendars: Garðabær, Mosfellsbær, Reykjanesbær, Árborg, Vestmannaeyjar, Ísafjarðarbær,
    Múlaþing, Akranes, Skagafjörður
  - listings sites: Visit Reykjavík (Reykjavík Music City), Grapevine, Vísir (Promogogo)
Every event gets a "region" (Reykjavík area, Reykjanes, West & Westfjords,
North, East, South) for the Where filter.

Run locally:   pip install -r requirements.txt && python scrape_events.py
Runs daily via .github/workflows/update-events.yml
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------- settings
OUTPUT_FILE = Path("docs/events.json")
DAYS_AHEAD = 120                 # how far into the future to include
MAX_TIX_EVENT_PAGES = 400        # cap on detail pages fetched per run
REQUEST_DELAY = 1.0              # seconds between requests (be polite)
TIX_CATEGORIES = {               # Tix nav label -> label shown on your site.
    "Music": "Music",            # An event listed in several categories gets
    "Theater": "Arts & Theatre", # the first one in this list.
    "Family": "Family",
    "Festivals": "Festivals",
    "Christmas": "Christmas",
}
# Optional: add a contact URL or email so site owners can reach you, e.g.
# "IcelandEventsFeed/1.0 (+https://yoursite.is/contact)"
USER_AGENT = "IcelandEventsFeed/1.0"

# Handball (HSÍ): senior competitions whose name contains one of these.
# Add "2. deild" to include the lower league too.
HSI_COMPETITIONS = ["Olís deild", "Grill 66", "Powerade bikar", "Evrópukeppni"]
HSI_NATIONAL_TEAMS = True        # Iceland's senior national teams, home matches

# Football (KSÍ): competitions whose name contains one of these.
# The season runs roughly April-October, so expect few matches in winter.
KSI_COMPETITIONS = ["Besta deild", "Lengjudeild", "Mjólkurbikar", "Lengjubikar",
                    "Meistarakeppni", "Undankeppni", "Þjóðadeild", "Vináttuleik"]

TIX_HOME = "https://tix.is/en"
HARPA_WHATS_ON = "https://www.harpa.is/en/whats-on"
HSI_API = "https://www.hsi.is/api/hsi/matches/{group}/upcoming?months=6"
KSI_PAGES = ["https://www.ksi.is/leikir-og-urslit/felagslid/",
             "https://www.ksi.is/leikir-og-urslit/landslid/"]
AKUREYRI_WHATS_ON = "https://www.visitakureyri.is/en/whats-on"
AKUREYRI_MAX_RANGE_DAYS = 31     # skip long-running exhibitions (e.g. "June 6 - May 31")
HVIRFILL_API = "https://hvirfill.reykjavik.is/find"
HVIRFILL_EVENT_PAGE = "https://reykjavik.is/borginokkar/vidburdir#/event/{id}"

# Volleyball (BLÍ): competitions whose name contains one of these, newest season.
# Add "1. deild" to include the next league down.
BLI_SITE = "https://bli-web.dataproject.com/"
BLI_SEED = BLI_SITE + "CompetitionHome.aspx?ID=142"   # any BLÍ competition page; its menu lists all
BLI_COMPETITIONS = ["Unbrokendeild", "Kjörísbikar"]

# Basketball (KKÍ): league id on kki.is -> name shown on your site.
# The ids appear in kki.is schedule links: .../Leikir?league_id=190
KKI_LEAGUES = {
    190: "Bónus deild karla",
    189: "Bónus deild kvenna",
    191: "1. deild karla",
    231: "1. deild kvenna",
    205: "VÍS bikar karla",
    208: "VÍS bikar kvenna",
}
KKI_SCHEDULE = "https://kki.is/motamal/leikir-og-urslit/motayfirlit/Leikir?league_id={id}"

KOPAVOGUR_API = "https://menning.kopavogur.is/wp-json/wp/v2/"
KOPAVOGUR_PAGES = 5              # 100 newest event posts per page; 3 pages reach back ~5 months
HAFNARFJORDUR_EVENTS = "https://hafnarfjordur.is/mannlif/vidburdir/"
IDNO_PROGRAM = "https://idno.is/dagskra"
SENA_HOME = "https://senalive.is/"

# Seconds to wait between requests to sites that ask for it. Sites that set a
# "Crawl-delay" in robots.txt are also respected automatically (up to 10 s).
HOST_DELAYS = {"hafnarfjordur.is": 3, "visitakureyri.is": 5}

# ---- Venues
LEIKHUSID_AJAX = "https://leikhusid.is/wp-admin/admin-ajax.php"     # Þjóðleikhúsið's own calendar
GAMLABIO_EVENTS = "https://gamlabio.is/vidburdir"
BIOPARADIS_SITE = "https://bioparadis.is/"
BIOPARADIS_SERIES = {           # Bíó Paradís screening series -> subtitle (each film's dates are read)
    "partisyningar": "Party screening · Bíó Paradís",
    "svartir-sunnudagar": "Svartir sunnudagar · Bíó Paradís",
    "midnaetursyningar": "Midnight screening · Bíó Paradís",
}
NORDICHOUSE_EVENTS = "https://nordichouse.is/vidburdur/?date_year={year}"

# ---- Festivals
AIRWAVES_SCHEDULE = "https://icelandairwaves.is/schedule/"
PRIDE_API = "https://hinsegindagar.is/wp-json/wp/v2/program?per_page=100&page={page}&orderby=date&order=desc&_fields=id,link,title,acf"

# ---- Listings sites
GRAPEVINE_CALENDAR = "https://grapevine.is/events/?view=calendar&cal={month}"
GRAPEVINE_MAX_PAGES = 120        # new event pages read per run (venue + picture); later runs fill in the rest
VISITRVK_MORE = "https://visitreykjavik.is/promogogo-events/more?offset={offset}&count=50&window=all&categories={cat}"
VISITRVK_CATEGORIES = {          # Visit Reykjavík (Reykjavík Music City) category -> label on your site
    "music": "Music", "theater": "Arts & Theatre", "standup": "Comedy", "arts": "Arts & Theatre",
    "lecture": "Culture & Learning", "reading": "Culture & Learning",
    "communityEntertainment": "Culture & Learning",
}
VISIR_FEEDS = ["music", "theater", "kids", "christmas", "currentaffairs", "all"]
VISIR_FEED = "https://media.promogogo.com/scripts/feeds/visir/{name}.json?true"

# ---- Regional tourism boards and town calendars that use the "Moya" website system
# (name shown on your site, events page, region if a venue can't be placed)
MOYA_CALENDARS = [
    ("South Iceland", "https://www.south.is/is/ferdaupplysingar/vidburdir", "South"),
    ("West Iceland", "https://www.west.is/is/thjonusta/vidburdir", "West & Westfjords"),
    ("North Iceland", "https://www.northiceland.is/is/ferdaupplysingar/vidburdir", "North"),
    ("East Iceland", "https://www.east.is/is/upplifun/vidburdir-a-austurlandi", "East"),
    ("Westfjords", "https://www.westfjords.is/is/upplifun/vidburdir", "West & Westfjords"),
    ("Visit Reykjanes", "https://www.visitreykjanes.is/is/ferdalagid/vidburdir", "Reykjanes"),
    ("Ísafjarðarbær", "https://www.isafjardarbaer.is/is/vidburdir", "West & Westfjords"),
    ("Múlaþing", "https://www.mulathing.is/is/mannlif/vidburdir", "East"),
    ("Akranes", "https://www.akranes.is/mannlif/afthreying/vidburdadagatal", "West & Westfjords"),
    ("Skagafjörður", "https://www.skagafjordur.is/is/mannlif/vidburdir", "North"),
]
MOYA_DETAIL_PAGES = 12           # new event pages read per site per run (start time, venue); later runs do the rest
MAX_RANGE_DAYS = 31              # multi-day listings longer than this (exhibitions) are only shown on opening day

# ---- Other town calendars
GARDABAER_EVENTS = "https://www.gardabaer.is/vidburdir"
MOSFELLSBAER_EVENTS = "https://mos.is/mannlif/vidburdir/"
REYKJANESBAER_EVENTS = "https://www.reykjanesbaer.is/mannlif/vidburdir"
ARBORG_EVENTS = "https://www.arborg.is/vidburdalisti/"
VESTMANNAEYJAR_EVENTS = "https://www.vestmannaeyjar.is/menning-mannlif/vidburdir-i-vestmannaeyjum/vidburdir"

# Sources that only show the next few days: keep what earlier runs found, so the list builds up.
ACCUMULATE_SOURCES = {"Vísir"}

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
_robots = {}


def allowed(url):
    """Respect each site's robots.txt."""
    host = "{0.scheme}://{0.netloc}".format(urlparse(url))
    if host not in _robots:
        rp = robotparser.RobotFileParser(host + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None
        _robots[host] = rp
    rp = _robots[host]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def crawl_delay(url):
    """Seconds to wait before a request: our default, HOST_DELAYS, or the site's robots.txt Crawl-delay."""
    host = urlparse(url).netloc.removeprefix("www.")
    rp = _robots.get("{0.scheme}://{0.netloc}".format(urlparse(url)))
    try:
        site = float(rp.crawl_delay(USER_AGENT) or 0) if rp else 0
    except (TypeError, ValueError):
        site = 0
    return max(REQUEST_DELAY, HOST_DELAYS.get(host, 0), min(site, 10))


def get(url, data=None):
    """GET a page (or POST `data` to it) and return its text, or None."""
    if not allowed(url):
        print(f"  skipped (robots.txt): {url}")
        return None
    time.sleep(crawl_delay(url))
    for attempt, wait in enumerate((5, 20, None), 1):   # up to 3 tries for flaky connections
        try:
            r = session.post(url, data=data, timeout=45) if data is not None else session.get(url, timeout=45)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(f"{r.status_code} server error", response=r)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            if wait is None or (getattr(e, "response", None) is not None and 400 <= e.response.status_code < 429):
                print(f"  failed: {url} ({e})")
                return None
            print(f"  retrying in {wait}s (attempt {attempt} failed: {e})")
            time.sleep(wait)


# ------------------------------------------------------------ JSON-LD helpers
def find_ld_events(html):
    """Return every schema.org object with a startDate found in the page."""
    soup = BeautifulSoup(html, "html.parser")
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "startDate" in node:
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or ""))
        except (json.JSONDecodeError, TypeError):
            continue
    return found, soup


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def meta(soup, prop):
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return tag.get("content") if tag else None


# Words that mark an event as comedy (stand-up, improv). Used for every source, and for
# Tix events also checked against the event's description.
# Recurring comedy shows whose names don't say "comedy". Add more with | between them.
COMEDY_SHOWS = r"feita hjarta|púðursykur"
COMEDY_WORDS = r"uppistand|stand-?up|\bcomedy|comedian|grínist|\bimprov\b|spunaleik|\bspuni|" + COMEDY_SHOWS


# ----------------------------------------------------------------- regions
CAPITAL, REYKJANES, WEST, NORTH, EAST, SOUTH = (
    "Reykjavík area", "Reykjanes", "West & Westfjords", "North", "East", "South")


def region_from_postcode(code):
    try:
        n = int(str(code).strip()[:3])
    except ValueError:
        return None
    if n == 190 or 230 <= n <= 262:
        return REYKJANES
    if 100 <= n <= 229 or 270 <= n <= 276:
        return CAPITAL
    if 300 <= n <= 499:
        return WEST
    if 500 <= n <= 689:
        return NORTH
    if 690 <= n <= 779:
        return EAST
    if 780 <= n <= 902:
        return SOUTH
    return None


# Lower-case word stems found in venue and town names (stems, because Icelandic
# endings change: Akureyri / Akureyrar / Akureyrarkirkja). Towns come before venues.
PLACE_WORDS = [
    (REYKJANES, ["reykjanesb", "keflav", "njarðv", "grindav", "sandgerð", "suðurnesjab",
                 "vogum", "hljómahöll", "hvalsnes", "frumleikhús", "stapasafn", "duus", "reykjanesbær"]),
    (WEST, ["akranes", "borgarn", "stykkish", "grundarfj", "ólafsvík", "snæfells", "búðardal",
            "reykholt", "ísafj", "bolungarv", "patreksfj", "þingeyr", "flateyr", "súðav",
            "hólmav", "tálknafj", "bíldudal", "westfjord", "vestfj", "edinborgarhús",
            "landnámssetr", "grímshús", "hellissand", "dalabygg"]),
    (NORTH, ["akureyr", "menningarhúsið hof", "græni hatt", "freyvang", "samkomuhúsið",
             "lystigarð", "múlaberg", "dalvík", "ólafsfj", "siglufj", "húsav", "mývatn",
             "dimmubor", "jarðböð", "sauðárkr", "blönduós", "skagastr", "hvammstang",
             "varmahl", "hrísey", "grenivík", "eyjafj", "þórshöfn", "kópasker", "raufarh"]),
    (EAST, ["egilsst", "seyðisfj", "neskaupst", "reyðarfj", "eskifj", "fáskrúðsfj",
            "djúpiv", "breiðdal", "borgarfjörður eystri", "borgarfirði eystra", "vopnafj",
            "skaftfell", "hallormsst", "fellabæ"]),
    (SOUTH, ["selfoss", "hveragerð", "þorláksh", "eyrarbakk", "stokkseyr", "hellu", "hella,",
             "hvolsv", "midgard", "mýrdal", "vík í", "kirkjubæjarkl", "vestmannaey", "flúð",
             "laugarvatn", "geysi", "höfn í", "hornafj"]),
    (CAPITAL, ["reykjav", "kópavog", "garðab", "hafnarfj", "mosfells", "seltjarn", "álftanes",
               "kjalarnes", "harpa", "hörpu", "eldborg", "þjóðleikh", "borgarleikh",
               "city theatre", "tjarnarbí", "háskólabí", "iðnó", "gamla bí", "hannesarholt",
               "langholtsk", "hallgrímsk", "laugardal", "salurinn", "smárabí", "bæjarbí",
               "tónabí", "bíó paradís", "kórinn", "egilshöll", "sykursal", "ásvell",
               "ólafssal", "kaplakrik", "guðmundarlund", "gróska", "dansverkst", "norræna h",
               "ráðhús", "borgarbókasafn", "kristskirkj", "grafarvogsk", "fríkirkj",
               "sjálfstæðissal", "tunglið", "rosenberg", "gaukur", "húrra", "mengi", "kex ", "skálinn gufun",
               "dómkirkj", "neskirkj", "bústaðakirkj", "listasafn íslands", "kjarvalsst",
               "hafnarhús", "ásmundarsal", "safamýr", "n1 höll", "origo", "hlíðarend",
               "framheimil", "úlfarsárdal", "víkin", "kuehne", "heklu höll", "myntkaup",
               "hlégarð", "elissa", "nordic house", "lemmy", "nasa", "kolaport", "röntgen",
               "12 tónar", "smekkleysa", "lucky records", "austurbæjarbí", "gerðuberg",
               "árbæjarsafn", "árbær open", "landakot", "listasafn reykjav", "petersen",
               "stóra svið", "kassinn", "kjallarinn", "hafnarborg", "gerðarsafn", "bókasafn kópav"]),
]

# Sports clubs -> region, for matches whose venue is a sponsor's name.
CLUB_REGIONS = {
    CAPITAL: ["haukar", "fh", "valur", "fram", "ír", "víkingur", "fjölnir", "kr", "þróttur",
              "leiknir", "fylkir", "ármann", "grótta", "stjarnan", "hk", "breiðablik",
              "augnablik", "afturelding", "kórdrengir", "hvíti riddarinn", "berserkir",
              "álftanes", "ísland", "kfg", "blakfélag hfj", "blakfélag hafnarfjarðar",
              "þróttur r", "þróttur rvk", "ármann/þróttur", "aþena", "kv", "leiknir r", "smári"],
    REYKJANES: ["grindavík", "keflavík", "njarðvík", "reynir", "reynir s", "víðir", "þróttur v"],
    WEST: ["ía", "hörður", "vestri", "víkingur ó", "skallagrímur", "kári", "snæfell"],
    NORTH: ["ka", "þór", "ka/þór", "þór ak", "þór akureyri", "völsungur", "tindastóll", "kf", "dalvík", "magni"],
    EAST: ["höttur", "fjarðabyggð", "þróttur fjarðabyggð", "þróttur f", "leiknir f", "einherji", "huginn"],
    SOUTH: ["íbv", "selfoss", "hamar", "mílan", "ægir", "kfr", "uppsveitir", "sindri",
            "þór þ", "þór þorlákshöfn", "hrunamenn", "laugdælir", "dímon"],
}


# "-fjörður" towns become "-firði" etc. in other grammatical cases: match both.
PLACE_WORDS = [(r, ws + [w[:-1] + "ir" for w in ws if w.endswith("fj")]) for r, ws in PLACE_WORDS]


def region_from_text(*texts):
    blob = " ".join(t for t in texts if t).lower()
    for region, words in PLACE_WORDS:
        if any(w in blob for w in words):
            return region
    return None


def region_from_club(team):
    """'Stjarnan 2', 'HK B', 'Afturelding U20', 'Þróttur Fjarðabyggð' -> the club's region."""
    name = re.sub(r"(\s+(\d+|[a-d]|u\d{2}))+$", "", (team or "").strip().lower())
    best = None
    for region, clubs in CLUB_REGIONS.items():
        for club in clubs:            # exact name, or the club name followed by more words
            follows = name[len(club):len(club) + 1]
            if name.startswith(club) and not follows.isalnum() and (not best or len(club) > len(best[1])):
                best = (region, club)
    return best[0] if best else None


_unplaced = {}


def note_unplaced(source, venue):
    """Collect venues with no region, so the run log shows what to add above."""
    if venue:
        _unplaced.setdefault(source, set()).add(venue)


# ----------------------------------------------------------------- Tix.is
def tix_event_links():
    """Find event links on the Tix category pages (Music, Theater, ...)."""
    home = get(TIX_HOME)
    if not home:
        return {}
    soup = BeautifulSoup(home, "html.parser")

    category_pages = {}
    for a in soup.find_all("a", href=True):
        label = a.get_text(strip=True)
        if label in TIX_CATEGORIES and label not in category_pages:
            category_pages[label] = urljoin(TIX_HOME, a["href"])

    links = {}  # event id -> (url, category label)
    for label, page_url in category_pages.items():
        print(f"Tix category {label}: {page_url}")
        html = get(page_url)
        if not html:
            continue
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            m = re.search(r"/event/(\d+)", a["href"])
            if m and m.group(1) not in links:
                links[m.group(1)] = (urljoin(page_url, a["href"]), TIX_CATEGORIES[label])
    return links


def scrape_tix(now, until):
    events = []
    links = tix_event_links()
    print(f"Tix: {len(links)} event links found")
    for event_id, (url, category) in list(links.items())[:MAX_TIX_EVENT_PAGES]:
        html = get(url)
        if not html:
            continue
        objs, soup = find_ld_events(html)
        dates = sorted(d for d in (parse_dt(o.get("startDate")) for o in objs) if d and now <= d <= until)
        if not dates:
            continue
        first = next(o for o in objs if parse_dt(o.get("startDate")) == dates[0])
        loc = first.get("location") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        offers = first.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        image = first.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        about = BeautifulSoup(first.get("description") or meta(soup, "og:description")
                              or meta(soup, "description") or "", "html.parser").get_text(" ")[:600]
        name = (first.get("name") or meta(soup, "og:title") or "")
        if category not in ("Family", "Music") and re.search(COMEDY_WORDS, f"{name} {about}", re.I):
            category = "Comedy"        # Tix files stand-up under Theater
        events.append({
            "id": f"tix-{event_id}",
            "source": "Tix.is",
            "title": (first.get("name") or meta(soup, "og:title") or "").strip(),
            "start": dates[0].isoformat(),
            "more_dates": len(dates) - 1,
            "venue": loc.get("name", "") if isinstance(loc, dict) else str(loc),
            "price_from": offers.get("price") if isinstance(offers, dict) else None,
            "currency": offers.get("priceCurrency", "ISK") if isinstance(offers, dict) else "ISK",
            "category": category,
            "region": tix_region(loc),
            "image": image or meta(soup, "og:image"),
            "url": url,
        })
    return events


def tix_region(loc):
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address") if isinstance(loc.get("address"), dict) else {}
    region = (region_from_postcode(addr.get("postalCode", "")) if addr.get("postalCode") else None) \
        or region_from_text(loc.get("name", ""), addr.get("streetAddress", ""), addr.get("addressLocality", ""))
    if not region:
        note_unplaced("Tix.is", loc.get("name", ""))
    return region


# ---------------------------------------------------------------- Harpa.is
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
HARPA_DATE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th) (" + "|".join(MONTHS) + r") (\d{4}), (\d{1,2}):(\d{2})")


def scrape_harpa(now, until):
    html = get(HARPA_WHATS_ON)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    events, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"/events/(\d+)", a["href"])
        if not m or m.group(1) in seen:
            continue
        text = a.get_text(" ", strip=True)
        d = HARPA_DATE.search(text)
        if not d:
            continue  # multi-date or ongoing items ("13 Events") are skipped
        seen.add(m.group(1))
        day, month, year, hh, mm = d.groups()
        start = datetime(int(year), MONTHS[month], int(day), int(hh), int(mm),
                         tzinfo=timezone.utc)  # Iceland is UTC all year
        if not (now <= start <= until):
            continue
        img = a.find("img")
        events.append({
            "id": f"harpa-{m.group(1)}",
            "source": "Harpa",
            "title": text[: d.start()].strip(" -–|"),
            "start": start.isoformat(),
            "more_dates": 0,
            "venue": "Harpa",
            "price_from": None,
            "currency": "ISK",
            "category": "Music & Arts",
            "region": CAPITAL,
            "image": urljoin(HARPA_WHATS_ON, img["src"]) if img and img.get("src") else None,
            "url": urljoin(HARPA_WHATS_ON, a["href"]),
        })
    return events


# ------------------------------------------------------------ HSÍ handball
def get_json(url):
    text = get(url)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"  not JSON: {url}")
        return None


def hsi_event(m, label, national=False):
    day = (m.get("GAMES_ACTUAL_MATCHDAY") or "").strip()
    clock = (m.get("GAMES_MATCHDAY_TIME") or "00:00").strip()
    try:
        start = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    home, away = (m.get("HOME") or "").strip(), (m.get("GUESTS") or "").strip()
    return {
        "id": f"hsi-{m.get('MATCH_ID')}",
        "source": "HSÍ",
        "title": f"{home} – {away}",
        "subtitle": label,
        "start": start.isoformat(),
        "more_dates": 0,
        "venue": (m.get("PLAYING_FIELD_NAME") or "").strip(),
        "price_from": None,
        "currency": "ISK",
        "category": "Handball",
        "sport": True,
        "region": region_from_text(m.get("PLAYING_FIELD_NAME")) or region_from_club(home),
        "national": national,
        "image": None,
        "url": f"https://www.hsi.is/match/{m.get('MATCH_ID')}",
    }


def scrape_hsi(now, until):
    senior = (get_json(HSI_API.format(group="M")) or {}).get("data", [])
    if not senior:
        # Without the club feed we'd publish national games only: treat as a failure
        # so yesterday's matches are kept instead.
        raise RuntimeError("HSÍ club matches feed unavailable")
    wanted = [m for m in senior
              if any(c.lower() in (m.get("TOURNAMENT_SHORT_NAME") or "").lower()
                     for c in HSI_COMPETITIONS)]

    # Icelandic clubs = everyone playing in the domestic leagues. Used to drop
    # European away games that are played abroad.
    domestic = {(m.get(k) or "").strip() for m in senior for k in ("HOME", "GUESTS")
                if "Evrópu" not in (m.get("TOURNAMENT_SHORT_NAME") or "")}

    events = []
    for m in wanted:
        comp = (m.get("TOURNAMENT_SHORT_NAME") or "").strip()
        if "Evrópu" in comp and (m.get("HOME") or "").strip() not in domestic:
            continue
        label = comp.split("|")[-2].strip() if comp.count("|") >= 2 else comp
        events.append(hsi_event(m, label))

    if HSI_NATIONAL_TEAMS:
        everything = (get_json(HSI_API.format(group="A")) or {}).get("data", [])
        # Venues Icelandic clubs play at. At tournaments abroad Iceland can still be
        # listed first, with the host country as the "venue", so check the venue too.
        home_venues = {(m.get("PLAYING_FIELD_NAME") or "").strip().lower()
                       for m in everything if "landslið" not in (m.get("TOURNAMENT_CLASS_NAME") or "")}
        for m in everything:
            cls = m.get("TOURNAMENT_CLASS_NAME") or ""
            venue = (m.get("PLAYING_FIELD_NAME") or "").strip().lower()
            in_iceland = venue in home_venues or any(w in venue for w in ("höll", "laugardal"))
            if cls.startswith("A landslið") and (m.get("HOME") or "").strip() == "Ísland" and in_iceland:
                who = "Women's" if cls.rstrip().endswith("kv") else "Men's"
                comp = (m.get("TOURNAMENT_SHORT_NAME") or "").split("|")[-1].strip()
                events.append(hsi_event(m, f"{who} national team · {comp}", national=True))

    return [e for e in events if e and now <= parse_dt(e["start"]) <= until]


# --------------------------------------------------------------- KSÍ football
IS_MONTHS = {m: i for i, m in enumerate(
    ["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí",
     "ágúst", "september", "október", "nóvember", "desember"], 1)}
KSI_DATE = re.compile(r"(\d{1,2})\.\s*(" + "|".join(IS_MONTHS) + r")\s+(\d{1,2}):(\d{2})", re.I)


YOUTH = re.compile(r"\bU-?\d{2}\b|Under-?\d{2}|Development Tournament|yngri", re.I)
ICELAND_NAMES = ("Ísland", "Iceland")


def scrape_ksi(now, until):
    events, seen = [], set()
    for page in KSI_PAGES:
        html = get(page)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        is_national = "landslid" in page
        rows = []
        # Each match row holds a date (span.body-5) and two team names (span.body-4).
        for date_el in soup.select("span.body-5"):
            if not KSI_DATE.search(date_el.get_text(" ", strip=True)):
                continue
            row = date_el.parent
            while row is not None and len(row.select("span.body-4")) < 2:
                row = row.parent
            if row is not None and all(row is not r for r in rows):
                rows.append(row)

        for row in rows:
            info = [x.get_text(" ", strip=True) for x in row.select("span.body-5")]
            teams = [x.get_text(strip=True) for x in row.select("span.body-4")
                     if x.get_text(strip=True) != "-"]
            if len(teams) < 2 or not info:
                continue
            comp_link = row.find("a", href=re.compile(r"/oll-mot/mot\?id="))
            comp = comp_link.get_text(" ", strip=True) if comp_link else (info[2] if len(info) > 2 else "")
            d = KSI_DATE.search(info[0])
            if not d:
                continue
            day, month, hh, mm = d.groups()
            month_n = IS_MONTHS[month.lower()]
            year = now.year + (1 if month_n < now.month - 1 else 0)  # listings carry no year
            start = datetime(year, month_n, int(day), int(hh), int(mm), tzinfo=timezone.utc)
            if not (now <= start <= until):
                continue

            if is_national:
                if YOUTH.search(" ".join([comp] + teams)):
                    continue                      # youth national teams (U15, U19, U21, ...)
                if teams[0] not in ICELAND_NAMES:
                    continue                      # home matches only
                teams[0] = "Ísland"
                women = re.search(r"women|kvenna", comp, re.I)
                short = re.sub(r"^\d{4}\s+", "", comp).split(" - ")[0].strip()
                subtitle = ("Women's" if women else "Men's") + " national team · " + short
            else:
                if not any(c.lower() in comp.lower() for c in KSI_COMPETITIONS):
                    continue
                subtitle = comp

            link = row.find("a", href=re.compile(r"leikur\?id=\d+"))
            if link:
                match_id = re.search(r"id=(\d+)", link["href"]).group(1)
                url = urljoin(page, link["href"])
            else:                                 # national rows no longer link to a match page
                match_id = "n" + start.strftime("%Y%m%d%H%M") + re.sub(r"\W", "", teams[1].lower())
                url = urljoin(page, comp_link["href"]) if comp_link else page
            if match_id in seen:
                continue
            seen.add(match_id)
            venue = info[1] if len(info) > 1 and info[1] != comp else ""

            events.append({
                "id": f"ksi-{match_id}",
                "source": "KSÍ",
                "title": f"{teams[0]} – {teams[1]}",
                "subtitle": subtitle,
                "start": start.isoformat(),
                "more_dates": 0,
                "venue": venue,
                "price_from": None,
                "currency": "ISK",
                "category": "Football",
                "sport": True,
                "region": region_from_text(venue) or region_from_club(teams[0]),
                "national": is_national,
                "image": None,
                "url": url,
            })
    return events


# ----------------------------------------------------- Visit Akureyri (North)
EN_MONTHS = {m.lower(): i for m, i in MONTHS.items()}
AKUREYRI_CATEGORIES = {          # Visit Akureyri label -> label shown on your site
    "concert": "Music", "theater": "Arts & Theatre", "theatre": "Arts & Theatre",
    "exhibitions": "Arts & Theatre", "dans": "Arts & Theatre", "dance": "Arts & Theatre",
    "kvikmynd": "Arts & Theatre", "film": "Arts & Theatre", "for the kids": "Family",
    "festivals": "Festivals", "fyrirlestur": "Culture & Learning", "lecture": "Culture & Learning",
    "dagsferðir": "Outdoors", "day trips": "Outdoors", "sport": "Outdoors",
}


def guess_year(month, now):
    """Listings without a year: a month well before this one means next year."""
    return now.year + (1 if month < now.month - 1 else 0)


def scrape_akureyri(now, until):
    html = get(AKUREYRI_WHATS_ON)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for box in soup.select("div.event"):
        link = box.select_one(".entryTitle a[href]")
        date_el, time_el = box.select_one(".date"), box.select_one(".time")
        if not link or not date_el:
            continue
        dates = re.findall(r"([A-Za-z]+)\s+(\d{1,2})", date_el.get_text(" ", strip=True))
        dates = [(EN_MONTHS.get(mo.lower()), int(d)) for mo, d in dates if mo.lower() in EN_MONTHS]
        if not dates:
            continue
        times = re.findall(r"(\d{1,2}):(\d{2})", time_el.get_text() if time_el else "")
        hh, mm = (int(times[0][0]), int(times[0][1])) if times else (0, 0)
        (m1, d1) = dates[0]
        first = datetime(guess_year(m1, now), m1, d1, hh, mm, tzinfo=timezone.utc)
        subtitle = None
        if len(dates) > 1:                       # a date range, e.g. "August 30 - March 7"
            m2, d2 = dates[1]
            last = datetime(guess_year(m2, now), m2, d2, 23, 59, tzinfo=timezone.utc)
            if last < first:
                last = last.replace(year=last.year + 1)
            if (last - first).days > AKUREYRI_MAX_RANGE_DAYS or last < now:
                continue
            if first < now:                      # already running: list it for today
                first = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if first < now - timedelta(hours=3):
                    first = now.replace(second=0, microsecond=0)
            subtitle = f"Until {last.day} {last.strftime('%B')}"
        if not (now - timedelta(hours=3) <= first <= until):
            continue
        cats = [c.get_text(" ", strip=True).lower() for c in box.select(".event__category")]
        category = next((AKUREYRI_CATEGORIES[c] for c in cats if c in AKUREYRI_CATEGORIES), "Culture & Learning")
        venue = box.select_one(".location").get_text(" ", strip=True) if box.select_one(".location") else ""
        slug = link["href"].rstrip("/").split("/")[-1]
        events.append({
            "id": f"akureyri-{slug}",
            "source": "Visit Akureyri",
            "title": link.get_text(" ", strip=True),
            "subtitle": subtitle,
            "start": first.isoformat(),
            "more_dates": 0,
            "venue": venue,
            "price_from": None,
            "currency": "ISK",
            "category": category,
            "region": region_from_text(venue) or NORTH,
            "image": None,
            "url": urljoin(AKUREYRI_WHATS_ON, link["href"]),
        })
    return events


# ------------------------------------------------ Reykjavík city (Hvirfill)
HVIRFILL_CATEGORIES = [          # first match wins; checked against categories and types
    ("Music", ["tónlist", "music", "tónleikar", "concert"]),
    ("Family", ["börn", "children", "fjölskyld", "family"]),
    ("Arts & Theatre", ["myndlist", "art", "sýning", "exhibition", "ljósmynd", "leiklist",
                        "theatre", "dans", "kvikmynd", "film", "bókmennt", "literature"]),
    ("Festivals", ["hátíð", "festival"]),
    ("Outdoors", ["náttúra", "nature", "útivist", "outdoor", "íþrótt", "sport"]),
]


def hvirfill_category(e):
    labels = " ".join(" ".join(str(x.get(k, "")) for k in ("name", "name_en", "tag"))
                      for x in (e.get("categories") or []) + (e.get("types") or [])
                      if isinstance(x, dict)).lower()
    for category, words in HVIRFILL_CATEGORIES:
        if any(w in labels for w in words):
            return category
    return "Culture & Learning"


def scrape_hvirfill(now, until):
    items, offset = [], 0
    window = f"{now:%Y-%m-%d},{until:%Y-%m-%d}"
    while offset < 5000:                         # 1000 per page; stop when a page comes back short
        page = get_json(f"{HVIRFILL_API}?format=json&range={window}&sort=start&limit=1000&offset={offset}")
        if not isinstance(page, list):
            break
        items.extend(page)
        if len(page) < 1000:
            break
        offset += 1000

    # The calendar lists every date of a recurring event separately: group them.
    groups = {}
    for e in items:
        start = parse_dt(e.get("start"))
        if not start or not (now - timedelta(hours=3) <= start <= until) or not e.get("active", True):
            continue
        groups.setdefault(e.get("parent") or e.get("id"), []).append((start, e))

    events = []
    for occurrences in groups.values():
        occurrences.sort(key=lambda x: x[0])
        start, e = occurrences[0]
        lang = e.get("language") or {}
        en, is_ = lang.get("en") or {}, lang.get("is") or {}
        title = (en.get("title") or e.get("title_en") or is_.get("title") or e.get("title") or "").strip()
        if not title:
            continue
        venue = (en.get("place") or is_.get("place") or "").strip()
        tickets = ((e.get("media") or {}).get("tickets") or "")
        tix = re.search(r"tix\.is/.*?(\d{4,})", tickets)
        events.append({
            "id": f"rvk-{e.get('id')}",
            "source": "Reykjavík city",
            "title": title,
            "subtitle": None,
            "start": start.isoformat(),
            "more_dates": len(occurrences) - 1,
            "venue": venue,
            "price_from": None,
            "currency": "ISK",
            "category": hvirfill_category(e),
            "region": region_from_postcode(e.get("postal", "")) or region_from_text(e.get("city"), venue) or CAPITAL,
            "image": None,
            "url": HVIRFILL_EVENT_PAGE.format(id=e.get("id")),
            "tix_id": tix.group(1) if tix else None,
        })
    return events


# --------------------------------------------------------- BLÍ volleyball
def bli_competitions():
    """Find this season's wanted competitions from the site's season menu."""
    html = get(BLI_SEED)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for a in soup.find_all("a", href=re.compile(r"CompetitionHome\.aspx\?ID=\d+", re.I)):
        name = a.get_text(" ", strip=True)
        season = re.search(r"(\d{4})\s*-\s*(\d{4})", name)
        if season and any(k.lower() in name.lower() for k in BLI_COMPETITIONS):
            cid = re.search(r"ID=(\d+)", a["href"]).group(1)
            found[cid] = (name, int(season.group(2)))
    if not found:
        return []
    latest = max(year for _, year in found.values())
    return [(cid, name) for cid, (name, year) in found.items() if year == latest]


BLI_DATE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})\D{1,6}(\d{1,2}):(\d{2})")


def parse_bli_matches(html):
    """Match rows on a DataProject 'CompetitionMatches' page, found by the ids of their labels."""
    matches = []
    for chunk in re.split(r'id="[^"]*RADLIST_Matches_ctrl\d+_MatchRow"', html)[1:]:
        def field(name):
            m = re.search(r'_ctrl\d+_' + name + r'"[^>]*>([^<]*)<', chunk)
            return BeautifulSoup(m.group(1), "html.parser").get_text().strip() if m else ""
        mid = re.search(r"mID=(\d+)", chunk)
        when = BLI_DATE.search(field("LB_DataOra"))
        home, away = field("Label2"), field("Label4")
        if not (mid and when and home and away):
            continue
        matches.append({"id": mid.group(1), "when": when, "home": home, "away": away,
                        "venue": field("LB_Palasport"), "played": bool(re.search(r"\d", field("LB_SetCasa")))})
    return matches


def scrape_bli(now, until):
    competitions = bli_competitions()
    if not competitions:
        raise RuntimeError("BLÍ competition list unavailable")
    events = []
    for cid, name in competitions:
        home_page = get(f"{BLI_SITE}CompetitionHome.aspx?ID={cid}") or ""
        pids = sorted(set(re.findall(rf"CompetitionMatches\.aspx\?ID={cid}&(?:amp;)?PID=(\d+)", home_page, re.I)))
        label = re.sub(r"\s*\d{4}\s*-\s*\d{4}\s*", " ", name).strip()
        print(f"BLÍ {label}: phases {pids or 'none found'}")
        for pid in pids:
            page_url = f"{BLI_SITE}CompetitionMatches.aspx?ID={cid}&PID={pid}"
            html = get(page_url)
            if not html:
                continue
            rows = parse_bli_matches(html)
            print(f"  phase {pid}: {len(rows)} matches on page")
            for m in rows:
                d, mo, y, hh, mm = (int(x) for x in m["when"].groups())
                start = datetime(y, mo, d, hh, mm, tzinfo=timezone.utc)
                if m["played"] or not (now <= start <= until):
                    continue
                events.append({
                    "id": f"bli-{m['id']}",
                    "source": "BLÍ",
                    "title": f"{m['home']} – {m['away']}",
                    "subtitle": label,
                    "start": start.isoformat(),
                    "more_dates": 0,
                    "venue": m["venue"],
                    "price_from": None,
                    "currency": "ISK",
                    "category": "Volleyball",
                    "sport": True,
                    "region": region_from_text(m["venue"]) or region_from_club(m["home"]),
                    "national": False,
                    "image": None,
                    "url": page_url,
                })
    return events


# ------------------------------------------------------------ KKÍ basketball
# Runs inside the loaded page: one entry per game row in KKÍ's schedule.
KKI_ROWS_JS = """
() => [...document.querySelectorAll('tr[id*="schedule-line-container"]')].map(r => {
  const date = r.querySelector('a[game_id]');
  const teams = [...r.querySelectorAll('a[team_id]')].map(a => a.textContent.trim());
  const arena = r.querySelector('a[arena_id]');
  return { game_id: date ? date.getAttribute('game_id') : null,
           when: date ? date.textContent.trim() : '',
           href: date ? date.getAttribute('href') : '',
           home: teams[0] || '', away: teams[1] || '',
           venue: arena ? arena.textContent.trim() : '' };
})
"""
KKI_DATE = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2}):(\d{2})")


def kki_event(row, label, now, until):
    when = KKI_DATE.search(row.get("when") or "")
    if not (row.get("game_id") and when and row.get("home") and row.get("away")):
        return None
    d, mo, y, hh, mm = (int(x) for x in when.groups())
    start = datetime(y, mo, d, hh, mm, tzinfo=timezone.utc)
    if not (now <= start <= until):
        return None
    return {
        "id": f"kki-{row['game_id']}",
        "source": "KKÍ",
        "title": f"{row['home']} – {row['away']}",
        "subtitle": label,
        "start": start.isoformat(),
        "more_dates": 0,
        "venue": row.get("venue", ""),
        "price_from": None,
        "currency": "ISK",
        "category": "Basketball",
        "sport": True,
        "region": region_from_text(row.get("venue")) or region_from_club(row["home"]),
        "national": False,
        "image": None,
        "url": urljoin("https://kki.is/", row.get("href") or ""),
    }


def scrape_kki(now, until):
    from playwright.sync_api import sync_playwright   # installed by the workflow
    events, seen = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="is-IS")
        try:
            for league_id, label in KKI_LEAGUES.items():
                url = KKI_SCHEDULE.format(id=league_id)
                if not allowed(url):
                    print(f"  skipped (robots.txt): {url}")
                    continue
                time.sleep(REQUEST_DELAY)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector('tr[id*="schedule-line-container"]', timeout=25000)
                    rows = page.evaluate(KKI_ROWS_JS)
                except Exception as e:      # no games listed, or the page was slow
                    print(f"KKÍ {label}: no games read ({type(e).__name__})")
                    continue
                found = [e for e in (kki_event(r, label, now, until) for r in rows) if e and e["id"] not in seen]
                seen.update(e["id"] for e in found)
                print(f"KKÍ {label}: {len(found)} upcoming games")
                events.extend(found)
        finally:
            browser.close()
    return events


# ---------------------------------------------- Kópavogur culture houses
KOPAVOGUR_HOUSE_CATEGORY = {"Salurinn": "Music", "Gerðarsafn": "Arts & Theatre"}


def tix_id_from(link):
    m = re.search(r"tix\.is/.*?(?:tickets|event)/(\d{4,})", link or "")
    return m.group(1) if m else None


def scrape_kopavogur(now, until):
    houses = {h["id"]: h["name"] for h in (get_json(KOPAVOGUR_API + "cultural_house?per_page=100&_fields=id,name") or [])}
    posts = []
    for page in range(1, KOPAVOGUR_PAGES + 1):
        batch = get_json(f"{KOPAVOGUR_API}event?per_page=100&page={page}&_fields=id,link,title,acf,cultural_house")
        if not isinstance(batch, list) or not batch:
            break
        posts.extend(batch)
    if not posts:
        raise RuntimeError("Kópavogur events feed unavailable")

    events = []
    for e in posts:
        acf = e.get("acf") or {}
        if not isinstance(acf, dict) or acf.get("event_type") == "exhibition":
            continue                                   # long-running exhibitions
        dates = [(d.get("date"), d.get("time_start")) for d in (acf.get("multiday_schedule") or []) if isinstance(d, dict)] \
            or [(acf.get("start_date"), acf.get("start_time"))]
        starts = []
        for day, clock in dates:
            try:
                starts.append(datetime.fromisoformat(f"{day}T{(clock or '00:00')[:5]}").replace(tzinfo=timezone.utc))
            except (TypeError, ValueError):
                pass
        starts = sorted(t for t in starts if now - timedelta(hours=3) <= t <= until)
        if not starts:
            continue
        house = next((houses.get(h) for h in (e.get("cultural_house") or []) if houses.get(h)), "")
        place = (acf.get("location") or "").strip()
        venue = place if place and (not house or place.lower() in house.lower() or house.lower() in place.lower()) \
            else ", ".join(x for x in (place, house) if x)
        title = BeautifulSoup((e.get("title") or {}).get("rendered", ""), "html.parser").get_text().strip()
        category = KOPAVOGUR_HOUSE_CATEGORY.get(house, "Culture & Learning")
        if re.search(r"barn|fjölskyld|krakk|foreldr", title + " " + place, re.I):
            category = "Family"
        price = re.search(r"(\d{1,3}(?:\.\d{3})+|\d{3,})", acf.get("price") or "")
        events.append({
            "id": f"kop-{e.get('id')}",
            "source": "Kópavogur",
            "title": title,
            "subtitle": None,
            "start": starts[0].isoformat(),
            "more_dates": len(starts) - 1,
            "venue": venue or "Kópavogur",
            "price_from": int(price.group(1).replace(".", "")) if price else None,
            "currency": "ISK",
            "category": category,
            "region": CAPITAL,
            "image": None,
            "url": e.get("link"),
            "tix_id": tix_id_from(acf.get("action_button_link")),
        })
    return events


# ------------------------------------------------------------ Hafnarfjörður
IS_MONTH_ABBR = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "maí", "jún", "júl", "ágú", "sep", "okt", "nóv", "des"], 1)}
HAFNARFJORDUR_CATEGORY = [("Music", ["tónleik", "tónlist"]), ("Family", ["fjölskyld", "barn", "krakk"]),
                          ("Arts & Theatre", ["hafnarborg", "sýning", "list", "leikhús", "gallerí"])]


def scrape_hafnarfjordur(now, until):
    html = get(HAFNARFJORDUR_EVENTS)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for card in soup.select(".card-item"):
        link = card.select_one("h3 a[href*='/vidburdur/']")
        date_el = card.select_one(".date")
        if not link or not date_el:
            continue
        d = re.search(r"(\d{1,2})\.?\s*(" + "|".join(IS_MONTH_ABBR) + ")", date_el.get_text(" ", strip=True).lower())
        if not d:
            continue
        footer = [li.get_text(" ", strip=True) for li in card.select(".footer li")]
        clock = next((re.search(r"(\d{1,2}):(\d{2})", f) for f in footer if re.search(r"\d{1,2}:\d{2}", f)), None)
        month = IS_MONTH_ABBR[d.group(2)]
        start = datetime(guess_year(month, now), month, int(d.group(1)),
                         int(clock.group(1)) if clock else 0, int(clock.group(2)) if clock else 0, tzinfo=timezone.utc)
        if not (now - timedelta(hours=3) <= start <= until):
            continue
        venue = next((f for f in footer if not re.search(r"\d{1,2}:\d{2}", f)), "")
        tags = " ".join(a.get_text(" ", strip=True) for a in card.select(".actions-list a"))
        title = link.get_text(" ", strip=True)
        category = next((c for c, words in HAFNARFJORDUR_CATEGORY
                         if any(w in (tags + " " + title).lower() for w in words)), "Culture & Learning")
        events.append({
            "id": "hfj-" + link["href"].rstrip("/").split("/")[-1],
            "source": "Hafnarfjörður",
            "title": title,
            "subtitle": None,
            "start": start.isoformat(),
            "more_dates": 0,
            "venue": f"{venue}, Hafnarfirði" if venue and "hafnarf" not in venue.lower() else (venue or "Hafnarfjörður"),
            "price_from": None,
            "currency": "ISK",
            "category": category,
            "region": CAPITAL,
            "image": None,
            "url": link["href"],
        })
    return events


# -------------------------------------------------------------------- Iðnó
IS_MONTHS_FULL = {m: i for i, m in enumerate(
    ["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí", "ágúst",
     "september", "október", "nóvember", "desember"], 1)}
IS_FULL_DATE = re.compile(r"(\d{1,2})\.\s*(" + "|".join(IS_MONTHS_FULL) + r")(?:\s+(\d{4}))?", re.I)


def scrape_idno(now, until):
    html = get(IDNO_PROGRAM)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    shows = {}                                    # one entry per show, extra dates counted
    for a in soup.select("a[href*='/vidburdur/']"):
        h2, line = a.find("h2"), a.find("p")
        if not h2 or not line:
            continue
        text = line.get_text(" ", strip=True)       # "Iðnó · 20:00 · sunnudagur, 15. nóvember 2026"
        d, t = IS_FULL_DATE.search(text), re.search(r"(\d{1,2}):(\d{2})", text)
        if not d:
            continue
        month = IS_MONTHS_FULL[d.group(2).lower()]
        year = int(d.group(3)) if d.group(3) else guess_year(month, now)
        start = datetime(year, month, int(d.group(1)), int(t.group(1)) if t else 20,
                         int(t.group(2)) if t else 0, tzinfo=timezone.utc)
        if not (now - timedelta(hours=3) <= start <= until):
            continue
        img = a.find("img")
        tix = re.search(r"(?:Poster|Event)_(\d{4,})", img.get("src", "")) if img else None
        title = h2.get_text(" ", strip=True)
        key = tix.group(1) if tix else title.lower()
        show = shows.setdefault(key, {
            "id": "idno-" + a["href"].strip("/").split("/")[1] if a["href"].count("/") >= 2 else "idno-" + key,
            "source": "Iðnó", "title": title, "subtitle": None, "start": start.isoformat(), "more_dates": -1,
            "venue": "Iðnó", "price_from": None, "currency": "ISK", "category": "Music & Arts",
            "region": CAPITAL, "image": None, "url": urljoin(IDNO_PROGRAM, a["href"]),
            "tix_id": tix.group(1) if tix else None})
        show["more_dates"] += 1
        if start.isoformat() < show["start"]:
            show["start"] = start.isoformat()
    return list(shows.values())


# --------------------------------------------------------------- Sena Live
SENA_VENUES = ["Eldborg", "Norðurljós", "Silfurberg", "Harpa", "Laugardalshöll", "Höllin", "Hljómahöll",
               "Hof", "Háskólabíó", "Kaplakriki", "Egilshöll", "Gamla bíó", "Bæjarbíó", "Iðnó"]


def scrape_sena(now, until):
    html = get(SENA_HOME)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = {}
    for a in soup.select("a[href*='/vidburdir/']"):
        title = a.get_text(" ", strip=True)
        if title and title.lower() not in ("nánar", "kaupa miða", "miðar"):
            links.setdefault(a["href"], title)
    events = []
    for url, title in links.items():
        page = get(url)
        if not page:
            continue
        body = BeautifulSoup(page, "html.parser").find("main") or BeautifulSoup(page, "html.parser")
        text = body.get_text(" ", strip=True)
        if re.search(r"liðinn viðburður", text, re.I):
            continue                               # marked as already happened
        d = IS_FULL_DATE.search(text)
        if not d:
            continue
        month = IS_MONTHS_FULL[d.group(2).lower()]
        year = int(d.group(3)) if d.group(3) else guess_year(month, now)
        after = text[d.end():d.end() + 120]
        t = re.search(r"(?:kl\.?\s*)?(\d{1,2})[:.](\d{2})", after)
        start = datetime(year, month, int(d.group(1)), int(t.group(1)) if t else 20,
                         int(t.group(2)) if t else 0, tzinfo=timezone.utc)
        if not (now - timedelta(hours=3) <= start <= until):
            continue
        venue = next((v for v in SENA_VENUES if re.search(r"\b" + v, text[:800], re.I)), "")
        category = ("Comedy" if re.search(r"uppistand|grín|comedy|stand-?up", text[:800], re.I)
                    else "Arts & Theatre" if re.search(r"leiksýning|söngleik", text[:800], re.I) else "Music")
        events.append({
            "id": "sena-" + url.rstrip("/").split("/")[-1],
            "source": "Sena Live", "title": title, "subtitle": "Sena Live", "start": start.isoformat(),
            "more_dates": 0, "venue": venue, "price_from": None, "currency": "ISK", "category": category,
            "region": region_from_text(text[:600]) or CAPITAL, "image": None, "url": url,
        })
    return events


def _words(title):
    return {w for w in re.findall(r"\w{4,}", (title or "").lower())}


# ===================================================================
#  Shared helpers for the sources below
# ===================================================================
UTC = timezone.utc
PREVIOUS_BY_URL = {}             # yesterday's events by link, filled in by main(): saves re-reading pages

IS_MON3 = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maí": 5, "mai": 5, "may": 5, "jún": 6, "jun": 6,
           "júl": 7, "jul": 7, "ágú": 8, "agu": 8, "aug": 8, "sep": 9, "okt": 10, "oct": 10,
           "nóv": 11, "nov": 11, "des": 12, "dec": 12}
_WORD = r"[A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð]{3,}"
DAY_MONTH = re.compile(r"(?<!\d)(\d{1,2})\.?\s*(" + _WORD + r")\.?(?:,?\s+(\d{4}))?")
MONTH_DAY = re.compile(r"\b(" + _WORD + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?")
NUM_DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})")
SAME_MONTH = re.compile(r"(?<!\d)(\d{1,2})\.?\s*[-–]\s*(\d{1,2})\.?\s*(" + _WORD + ")")
CLOCK = re.compile(r"(?<![\d.])(\d{1,2}):(\d{2})(?!\d)")


def month_no(word):
    w = (word or "").lower().strip(".")
    return IS_MON3.get(w[:3]) if len(w) >= 3 else None


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


def text_of(el, sep=" "):
    return el.get_text(sep, strip=True) if el is not None else ""


def date_span(text, now, english=False):
    """First and last date in a listing's date text, as dates:
    '7. nóv.', '24. okt - 4. nóv', '5.- 9. nóv', '30.10.2026 - 15.11.2026', '14. nóvember 2026',
    'Nov 4th, 2026', 'Friday, September 25 – Saturday, November 7'. Returns (first, last) or None."""
    t = (text or "").replace("\xa0", " ")
    found = [(int(d), int(m), int(y)) for d, m, y in NUM_DATE.findall(t) if 1 <= int(m) <= 12]
    if not found:
        same = SAME_MONTH.search(t)
        if same and month_no(same.group(3)):
            mo = month_no(same.group(3))
            found = [(int(same.group(1)), mo, None), (int(same.group(2)), mo, None)]
    if not found:
        pattern = MONTH_DAY if english else DAY_MONTH
        for m in pattern.finditer(t):
            word, day, year = (m.group(1), m.group(2), m.group(3)) if english else (m.group(2), m.group(1), m.group(3))
            mo = month_no(word)
            if mo and 1 <= int(day) <= 31:
                found.append((int(day), mo, int(year) if year else None))
    if not found:
        return None
    (d1, m1, y1), (d2, m2, y2) = found[0], found[-1]
    try:
        if y2 is None:
            y2 = y1 if (y1 and (m2, d2) >= (m1, d1)) else guess_year(m2, now)
        if y1 is None:
            y1 = y2 if (m1, d1) <= (m2, d2) else y2 - 1
        first, last = datetime(y1, m1, d1).date(), datetime(y2, m2, d2).date()
    except ValueError:
        return None
    return first, last


def clock_of(text, default=(0, 0)):
    m = CLOCK.search(text or "")
    if m and int(m.group(1)) < 24:
        return int(m.group(1)), int(m.group(2))
    return default


def listing_start(first, last, hh, mm, now):
    """When to list an event with these dates, plus an 'Until ...' note for runs of
    several days. None when it is over, or is a long exhibition that has already opened."""
    start = datetime(first.year, first.month, first.day, hh, mm, tzinfo=UTC)
    if not last or last <= first:
        return (start, None) if start >= now - timedelta(hours=3) else (None, None)
    end = datetime(last.year, last.month, last.day, 23, 59, tzinfo=UTC)
    if end < now:
        return None, None
    note = f"Until {last.day} {last.strftime('%B')}"
    if (last - first).days > MAX_RANGE_DAYS:
        return (start, note) if start >= now - timedelta(hours=3) else (None, None)
    if start < now - timedelta(hours=3):
        start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if start < now - timedelta(hours=3):
            start = now.replace(second=0, microsecond=0)
    return start, note


CATEGORY_WORDS = [               # first match wins; checked against title, labels and description
    ("Comedy", COMEDY_WORDS),
    ("Christmas", r"jól(?!a?tónl|asöngv|atónleik)|aðvent|christmas|yule"),
    ("Music", r"tónleik|tónlist|concert|music|\bdj\b|jazz|djass|\bkór|choir|sinfón|symphon|söngv|\bband\b|ópera|opera|rokk|"
              r"orchestra|hljómsveit|tríó|\btrio\b|kvartett|quartet|gospel|\brapp|\bblús|blues|plötusnú"),
    ("Family", r"\bbarn|\bbörn|fjölskyld|krakk|\bkids?\b|children|family|sögustund|leikskól|unglinga"),
    ("Festivals", r"hátíð|festival|\bfest\b|bæjarhátíð"),
    ("Arts & Theatre", r"leikrit|leiksýn|leikhús|theat|söngleik|sýning|exhibition|myndlist|\bart\b|\barts\b|gallerí|gallery|"
                       r"\bdans|dance|kvikmynd|\bfilm|cinema|\bbíó|kabarett|cabaret"),
    ("Outdoors", r"útivist|gönguf|ganga\b|hike|hiking|\bwalk|hlaup|\brun\b|náttúr|nature|outdoor|fjall"),
]


def guess_category(text, default="Culture & Learning"):
    t = (text or "").lower()
    for category, pattern in CATEGORY_WORDS:
        if re.search(pattern, t):
            return category
    return default


def make_event(source, key, title, start, url, *, venue="", category="Culture & Learning", region=None,
               subtitle=None, more_dates=0, price=None, image=None, tix_id=None, unplaced_default=None):
    """One event in the shape the pages expect."""
    if region is None:
        region = region_from_text(venue)
    if region is None:
        if unplaced_default is None:
            note_unplaced(source, venue)
        region = unplaced_default
    event = {
        "id": key, "source": source, "title": (title or "").strip(), "subtitle": subtitle,
        "start": start.isoformat(), "more_dates": more_dates, "venue": (venue or "").strip(),
        "price_from": price, "currency": "ISK", "category": category, "region": region,
        "image": image, "url": url,
    }
    if tix_id:
        event["tix_id"] = tix_id
    return event


def price_of(text):
    """'12.900 kr.' -> 12900 (lowest number that looks like a price), else None."""
    nums = [int(n.replace(".", "")) for n in re.findall(r"(\d{1,3}(?:\.\d{3})+|\d{3,6})\s*(?:kr|isk)", (text or "").lower())]
    return min(nums) if nums else None


def group_occurrences(items):
    """[(start, event), ...] for repeat performances -> one event per show, first date + count."""
    shows = {}
    for start, e in sorted(items, key=lambda x: x[0]):
        key = (e["title"].lower(), e.get("venue", "").lower())
        if key in shows:
            shows[key]["more_dates"] += 1
        else:
            shows[key] = e
    return list(shows.values())


def postcode_in(text):
    m = re.search(r"\b(\d{3})\s+[A-ZÁÉÍÓÚÝÞÆÖ]", text or "")
    return region_from_postcode(m.group(1)) if m else None


# ===================================================================
#  Þjóðleikhúsið (National Theatre) — its own performance calendar
# ===================================================================
def scrape_leikhusid(now, until):
    html = get(LEIKHUSID_AJAX, data={
        "action": "available_per_month", "date[from]": f"{now:%Y-%m-%d}", "date[to]": f"{until:%Y-%m-%d}",
        "date[month]": str(now.month - 1), "date[setActiveFirstDay]": "false"})
    if not html:
        return []
    try:
        listing = json.loads(html).get("list") or ""
    except (json.JSONDecodeError, AttributeError):
        print("  Þjóðleikhúsið: unexpected answer")
        return []
    occurrences = []
    for group in soup_of(listing).select(".list__group"):
        day_el = group.select_one("[data-day]")
        if not day_el:
            continue
        for item in group.select(".list__item"):
            title_el = item.select_one(".item__content--title")
            if not title_el:
                continue
            spans = [text_of(s) for s in item.select(".item__content--time span") if text_of(s)]
            hh, mm = clock_of(" ".join(spans), (20, 0))
            try:
                start = datetime.fromisoformat(day_el["data-day"]).replace(hour=hh, minute=mm, tzinfo=UTC)
            except ValueError:
                continue
            if not (now - timedelta(hours=3) <= start <= until):
                continue
            stage = next((s for s in spans if not CLOCK.search(s)), "")
            outside = stage and not re.search(r"svið|kassinn|kjallar", stage, re.I)
            venue = stage if outside else ("Þjóðleikhúsið – " + stage if stage else "Þjóðleikhúsið")
            buy = item.select_one("a.tix-link[href]")
            title = text_of(title_el)
            category = guess_category(title, "Arts & Theatre")
            if category in ("Culture & Learning", "Outdoors"):
                category = "Arts & Theatre"
            occurrences.append((start, make_event(
                "Þjóðleikhúsið", "leikhusid-" + re.sub(r"\W+", "-", title.lower()).strip("-")[:40], title, start,
                title_el.get("href") or "https://leikhusid.is/", venue=venue, category=category,
                region=region_from_text(venue) or CAPITAL, subtitle="Þjóðleikhúsið",
                price=price_of(text_of(item)), tix_id=tix_id_from(buy["href"]) if buy else None)))
    return group_occurrences(occurrences)


# ===================================================================
#  Gamla bíó
# ===================================================================
GAMLABIO_TYPES = {"tónleikar": "Music", "stand-up": "Comedy", "uppistand": "Comedy",
                  "leiksýning": "Arts & Theatre", "barviðburður": "Music & Arts"}


def scrape_gamlabio(now, until):
    html = get(GAMLABIO_EVENTS)
    if not html:
        return []
    events = []
    for card in soup_of(html).select("article"):
        link = card.select_one("h3 a[href]")
        lines = [text_of(p) for p in card.select("p")]
        span = date_span(lines[0] if lines else "", now)
        if not link or not span:
            continue
        hh, mm = clock_of(lines[1] if len(lines) > 1 else "", (20, 0))
        start, note = listing_start(span[0], span[1], hh, mm, now)
        if not start or start > until:
            continue
        labels = [text_of(s) for s in card.select("div.mb-3 span")]
        kind = (labels[1] if len(labels) > 1 else "").lower()
        buy = next((a["href"] for a in card.select("a[href^='http']")), None)
        title = text_of(link)
        events.append(make_event(
            "Gamla bíó", "gamlabio-" + link["href"].rstrip("/").split("/")[-1], title, start,
            urljoin(GAMLABIO_EVENTS, link["href"]), venue="Gamla bíó", region=CAPITAL, subtitle=note,
            category=GAMLABIO_TYPES.get(kind) or guess_category(title + " " + kind, "Music & Arts"),
            tix_id=tix_id_from(buy)))
    return events


# ===================================================================
#  Bíó Paradís — film festivals and special screening series
# ===================================================================
def bioparadis_showtimes(html, now):
    """(date, 'HH:MM') for every screening on a film page, in page order."""
    out, day = [], None
    page = soup_of(html)
    for other in page.select(".screening-today"):     # today's other films, listed on every page
        other.decompose()
    for el in page.find_all(class_=["date", "top-showtime"]):
        classes = el.get("class") or []
        if "date" in classes:
            d = re.search(r"(\d{1,2})\.\s*(" + _WORD + ")", text_of(el))
            day = (int(d.group(1)), month_no(d.group(2))) if d and month_no(d.group(2)) else None
        elif day:
            hh, mm = clock_of(text_of(el), (None, None))
            if hh is not None:
                d, mo = day
                try:
                    out.append(datetime(guess_year(mo, now), mo, d, hh, mm, tzinfo=UTC))
                except ValueError:
                    pass
    return out


def scrape_bioparadis(now, until):
    events = []
    html = get(urljoin(BIOPARADIS_SITE, "vidburdir/"))
    if html:                                       # festivals, marathons: a few days each
        for li in soup_of(html).select("li.film"):
            link, title_el = li.select_one("a[href]"), li.select_one("h4")
            dates = li.select_one(".vidburdir-date-from-to")
            span = date_span(text_of(dates), now) if dates else None
            if not (link and title_el and span) or (span[1] - span[0]).days > MAX_RANGE_DAYS:
                continue
            start, note = listing_start(span[0], span[1], 0, 0, now)
            if not start or start > until:
                continue
            title = text_of(title_el)
            events.append(make_event(
                "Bíó Paradís", "bioparadis-" + link["href"].rstrip("/").split("/")[-1], title, start,
                link["href"], venue="Bíó Paradís", region=CAPITAL, subtitle=note,
                category="Festivals" if re.search(r"hátíð|dagar|fest|veisla|maraþon|marathon", title, re.I) else "Arts & Theatre"))
    for series, label in BIOPARADIS_SERIES.items():
        page = get(urljoin(BIOPARADIS_SITE, f"vidburdir/{series}/"))
        if not page:
            continue
        films = []
        for a in soup_of(page).select(f"a[href*='/mynd/'][href*='event={series}']"):
            if a["href"] not in films:
                films.append(a["href"])
        for href in films[:25]:
            film_url = urljoin(BIOPARADIS_SITE, href)
            film = get(film_url)
            if not film:
                continue
            fs = soup_of(film)
            title = (meta(fs, "og:title") or text_of(fs.find("h1"))).split("|")[0].split(" - Bíó")[0].strip()
            for start in bioparadis_showtimes(film, now):
                if now - timedelta(hours=3) <= start <= until and title:
                    events.append(make_event(
                        "Bíó Paradís", f"bioparadis-{series}-{start:%Y%m%d%H%M}", title, start, film_url.split("?")[0],
                        venue="Bíó Paradís", region=CAPITAL, subtitle=label, category="Arts & Theatre",
                        image=meta(fs, "og:image")))
    return events


# ===================================================================
#  Nordic House (Norræna húsið)
# ===================================================================
NORDIC_TERMS = [("Music", "tónleik"), ("Family", "börn"), ("Festivals", "hátíð"), ("Arts & Theatre", "sýning"),
                ("Arts & Theatre", "kvikmynd"), ("Arts & Theatre", "leikhús")]


def scrape_nordichouse(now, until):
    events = []
    for year in sorted({now.year, until.year}):
        html = get(NORDICHOUSE_EVENTS.format(year=year))
        if not html:
            continue
        for art in soup_of(html).select("article.event-list-item"):
            link = art.select_one("a[href]")
            when = art.select_one("time")
            if not link or not when:
                continue
            days = [text_of(x) for x in when.select(".big-number")]
            months = [text_of(x) for x in when.select(".start-month, .end-month")]
            if days and months:
                text = " - ".join(f"{d}. {m} {year}" for d, m in zip(days, months))
            else:
                text = text_of(when)               # exhibitions: '13.09.2025–01.02.2026'
            span = date_span(text, now)
            if not span:
                continue
            hh, mm = clock_of(text_of(when.select_one(".time")), (0, 0))
            start, note = listing_start(span[0], span[1], hh, mm, now)
            if not start or start > until:
                continue
            title = text_of(art.select_one("h1, h2, h3"))
            terms = text_of(art.select_one("[data-term]")).lower()
            category = next((c for c, w in NORDIC_TERMS if w in terms), None) or guess_category(title + " " + terms)
            room = text_of(art.select_one(".locations"))
            events.append(make_event(
                "Nordic House", "nordic-" + link["href"].rstrip("/").split("/")[-1], title, start, link["href"],
                venue="Norræna húsið" + (f" – {room}" if room else ""), region=CAPITAL, subtitle=note,
                category=category, price=0 if "ókeypis" in text_of(art).lower() else None))
    seen, out = set(), []
    for e in events:
        if e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    return out


# ===================================================================
#  Iceland Airwaves — one listing per venue per festival day
# ===================================================================
def scrape_airwaves(now, until):
    html = get(AIRWAVES_SCHEDULE)
    if not html:
        return []
    data = None
    for tag in soup_of(html).find_all("script", type="application/json"):
        try:
            candidate = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("events"), list):
            data = candidate
            break
    if not data:
        print("  Airwaves: schedule data not found on the page")
        return []
    nights = {}
    for slot in data["events"]:
        start = parse_dt(slot.get("start"))
        if not start or not slot.get("artist") or not (now - timedelta(hours=3) <= start <= until):
            continue
        nights.setdefault((slot.get("festival_day") or f"{start:%Y-%m-%d}", slot.get("venue") or ""), []).append((start, slot))
    events = []
    for (day, venue), slots in nights.items():
        slots.sort(key=lambda x: x[0])
        name = venue.title()
        lineup = " · ".join(f"{s['artist']} {t:%H:%M}" for t, s in slots)
        slug = re.sub(r"\W+", "", venue.lower())
        events.append(make_event(
            "Iceland Airwaves", f"airwaves-{day}-{slug}", f"Iceland Airwaves at {name}",
            slots[0][0], AIRWAVES_SCHEDULE, venue=name, region=CAPITAL, category="Music",
            subtitle=("Iceland Airwaves · " + lineup)[:220]))
    return events


# ===================================================================
#  Reykjavík Pride (Hinsegin dagar) — the August programme
# ===================================================================
PRIDE_KINDS = [("Music", "tónl"), ("Family", "fjölskyld"), ("Family", "barn"), ("Arts & Theatre", "sýning"),
               ("Arts & Theatre", "list"), ("Culture & Learning", "fræðsl"), ("Culture & Learning", "fyrirl")]


def scrape_pride(now, until):
    events, seen = [], set()
    for page in (1, 2, 3):
        items = get_json(PRIDE_API.format(page=page))
        if not isinstance(items, list) or not items:
            break
        for p in items:
            acf = p.get("acf") or {}
            if p.get("id") in seen or not isinstance(acf, dict) or not re.fullmatch(r"\d{8}", acf.get("dagsetning") or ""):
                continue
            seen.add(p.get("id"))
            hh, mm = clock_of(acf.get("timasetning") or "", (20, 0))
            d = acf["dagsetning"]
            try:
                start = datetime(int(d[:4]), int(d[4:6]), int(d[6:]), hh, mm, tzinfo=UTC)
            except ValueError:
                continue
            if not (now - timedelta(hours=3) <= start <= until):
                continue
            maps = acf.get("google_maps") if isinstance(acf.get("google_maps"), dict) else {}
            venue = (acf.get("stadsetning") or maps.get("name") or "").strip()
            kinds = " ".join(acf.get("flokkur_vidburdar") or []).lower()
            title = BeautifulSoup((p.get("title") or {}).get("rendered", ""), "html.parser").get_text().strip()
            category = next((c for c, w in PRIDE_KINDS if w in kinds), "Festivals")
            events.append(make_event(
                "Reykjavík Pride", f"pride-{p.get('id')}", title, start, p.get("link") or "https://hinsegindagar.is/dagskra/",
                venue=venue, category=category, subtitle="Reykjavík Pride · Hinsegin dagar",
                region=region_from_postcode(maps.get("post_code") or "") or region_from_text(venue, maps.get("city")) or CAPITAL,
                price=price_of((acf.get("verd") or "") + " kr") if acf.get("verd") else None,
                tix_id=tix_id_from(acf.get("midasolu_linkur"))))
    return events


# ===================================================================
#  Reykjavík Grapevine events calendar
# ===================================================================
def grapevine_month_pages(now, until):
    month, out = datetime(now.year, now.month, 1), []
    while month <= until.replace(tzinfo=None):
        out.append(f"{month:%Y-%m}")
        month = datetime(month.year + month.month // 12, month.month % 12 + 1, 1)
    return out


def scrape_grapevine(now, until):
    occurrences = {}                              # event url -> {"title", "starts"}
    for month in grapevine_month_pages(now, until):
        html = get(GRAPEVINE_CALENDAR.format(month=month))
        if not html:
            continue
        for cell in soup_of(html).select("td.infl-events-calendar__cell"):
            daylink = cell.select_one("a[href^='#infl-cal-']")
            if not daylink or "is-outside" in (cell.get("class") or []):
                continue
            try:
                day = datetime.strptime(daylink["href"][len("#infl-cal-"):], "%Y-%m-%d")
            except ValueError:
                continue
            for li in cell.select("li.infl-events-calendar__event a[href]"):
                hh, mm = clock_of(text_of(li.select_one(".infl-events-calendar__event-time")), (0, 0))
                start = day.replace(hour=hh, minute=mm, tzinfo=UTC)
                if not (now - timedelta(hours=3) <= start <= until):
                    continue
                entry = occurrences.setdefault(li["href"], {"title": li.get("title") or text_of(li.select_one(
                    ".infl-events-calendar__event-title")), "starts": set()})
                entry["starts"].add(start)
    events, fetched = [], 0
    for url, entry in sorted(occurrences.items(), key=lambda kv: min(kv[1]["starts"])):
        starts = sorted(entry["starts"])
        if len(starts) > 20:
            continue                               # on nearly every day: a long-running exhibition
        prev = PREVIOUS_BY_URL.get(url) or {}
        venue, region, image, about = prev.get("venue", ""), prev.get("region"), prev.get("image"), ""
        if not prev and fetched < GRAPEVINE_MAX_PAGES:
            fetched += 1
            page = get(url)
            objs, ps = find_ld_events(page) if page else ([], None)
            if objs:
                loc = objs[0].get("location") or {}
                loc = loc[0] if isinstance(loc, list) and loc else loc
                addr = loc.get("address") if isinstance(loc, dict) and isinstance(loc.get("address"), dict) else {}
                venue = (loc.get("name") or "") if isinstance(loc, dict) else ""
                if addr.get("addressLocality") and addr["addressLocality"].lower() not in venue.lower():
                    venue = f"{venue}, {addr['addressLocality']}" if venue else addr["addressLocality"]
                region = region_from_postcode(addr.get("postalCode") or "") or region_from_text(venue)
                image = objs[0].get("image") if isinstance(objs[0].get("image"), str) else None
                about = BeautifulSoup(objs[0].get("description") or "", "html.parser").get_text()[:400]
        title = entry["title"]
        category = prev.get("category") or guess_category(title + " " + about, "Music & Arts")
        events.append(make_event(
            "Grapevine", "grapevine-" + url.rstrip("/").split("/")[-1], title, starts[0], url,
            venue=venue, region=region or region_from_text(venue) or CAPITAL, category=category,
            more_dates=len(starts) - 1, image=image))
    if fetched >= GRAPEVINE_MAX_PAGES:
        print(f"  Grapevine: read {fetched} event pages this run; the rest get their venue on later runs")
    return events


# ===================================================================
#  Visit Reykjavík events (Reykjavík Music City's concert calendar)
# ===================================================================
def scrape_visitreykjavik(now, until):
    occurrences = []
    for cat, category in VISITRVK_CATEGORIES.items():
        offset = 0
        while offset < 2000:
            data = get_json(VISITRVK_MORE.format(offset=offset, cat=cat))
            if not isinstance(data, dict) or not data.get("html"):
                break
            rows = soup_of(data["html"]).select("[data-promogogo-event]")
            for row in rows:
                detail = row.select_one("[data-event-detail]")
                if detail:
                    detail.extract()                   # the pop-up copy; read the card itself
                ps = [text_of(p) for p in row.find_all("p")]
                title = text_of(row.select_one("h3"))
                when = ps[0] if ps else ""
                span = date_span(when, now, english=True)
                if not (title and span):
                    continue
                hh, mm = clock_of(when, (20, 0))
                start = datetime(span[0].year, span[0].month, span[0].day, hh, mm, tzinfo=UTC)
                if not (now - timedelta(hours=3) <= start <= until):
                    continue
                venue = ps[1] if len(ps) > 1 else ""
                address = ps[2] if len(ps) > 2 else ""
                links = {text_of(a).lower(): a["href"] for a in (detail.select("a[href]") if detail else [])}
                url = links.get("event page") or links.get("buy tickets") or "https://visitreykjavik.is/see-do/events"
                slug = re.sub(r"\W+", "-", title.lower())[:40]
                occurrences.append((start, make_event(
                    "Visit Reykjavík", f"vrvk-{cat}-{slug}-{start:%Y%m%d}", title, start, url,
                    venue=venue, category=category, region=postcode_in(address) or region_from_text(venue, address) or CAPITAL,
                    tix_id=tix_id_from(" ".join(links.values())))))
            if data.get("end") or not rows or not data.get("next"):
                break
            offset = int(data["next"])
    return group_occurrences(occurrences)


# ===================================================================
#  Vísir's events page (Promogogo) — public "next few days" feeds
# ===================================================================
VISIR_TYPES = {"music": "Music", "concert": "Music", "theater": "Arts & Theatre", "theatre": "Arts & Theatre",
               "film": "Arts & Theatre", "dance": "Arts & Theatre", "arts": "Arts & Theatre", "standup": "Comedy",
               "comedy": "Comedy", "festival": "Festivals", "lecture": "Culture & Learning",
               "educational": "Culture & Learning", "publishingparty": "Culture & Learning", "reading": "Culture & Learning",
               "communityentertainment": "Culture & Learning", "guidedtour": "Culture & Learning", "outdoors": "Outdoors"}
VISIR_SKIP = {"workshop", "exercise", "wellness", "conference", "meeting", "religion", "business", "sport", "course"}


def scrape_visir(now, until):
    occurrences, seen = [], set()
    for name in VISIR_FEEDS:
        data = get_json(VISIR_FEED.format(name=name))
        for e in (data or {}).get("collection", []) if isinstance(data, dict) else []:
            start = parse_dt(e.get("startDate"))
            if not start or not e.get("name") or e.get("@id") in seen or not (now - timedelta(hours=3) <= start <= until):
                continue
            seen.add(e.get("@id"))
            kind = ((e.get("gogo") or {}).get("majorType") or "").lower()
            audience = set(e.get("audience") or [])
            if kind in VISIR_SKIP or (kind == "exhibition" and not re.search(r"opnun|opening", e["name"], re.I)):
                continue
            category = VISIR_TYPES.get(kind) or guess_category(e["name"])
            if category not in ("Music",) and audience & {"kids", "families"}:
                category = "Family"
            if category != "Music" and "christmas" in audience:
                category = "Christmas"
            loc = e.get("location") if isinstance(e.get("location"), dict) else {}
            addr = loc.get("address") if isinstance(loc.get("address"), dict) else {}
            town = addr.get("addressLocality") or ""
            venue = loc.get("name") or ""
            if town and town.lower() not in venue.lower():
                venue = f"{venue}, {town}" if venue else town
            offers = [o.get("price") for o in (e.get("offers") or []) if isinstance(o, dict) and o.get("price")]
            url = e.get("url") or "https://www.visir.is/vidburdir"
            key = "visir-" + re.sub(r"\W+", "-", (e["name"] + " " + venue).lower()).strip("-")[:60]
            occurrences.append((start, make_event(
                "Vísir", key, e["name"], start, url, venue=venue, category=category,
                region=region_from_postcode(addr.get("postalCode") or "") or region_from_text(venue),
                price=int(min(offers)) if offers else None, image=e.get("image") if isinstance(e.get("image"), str) else None,
                tix_id=tix_id_from(url) or tix_id_from(" ".join(str(o.get("url", "")) for o in e.get("offers") or [] if isinstance(o, dict))))))
    # The same show on several days becomes one listing.
    return group_occurrences(occurrences)


# ===================================================================
#  Moya calendars: regional tourism boards and several towns
# ===================================================================
MOYA_CODES = [("Festivals", r"festival|baejarhatid"), ("Music", r"music|tonlist"), ("Family", r"kids|born"),
              ("Outdoors", r"outdoor|utivist"), ("Arts & Theatre", r"arts|leiklist|myndlist"),
              ("Culture & Learning", r"culture|menning|fodd")]
MOYA_SKIP = re.compile(r"\b(namskeid|courses|radstefnur|conference|fundir)\b")


def moya_detail(url, fallback_venue):
    """(hour, minute, venue) from a Moya event page: 'kl. 20:00-21:30' and 'Staðsetning ...'."""
    page = get(url)
    if not page:
        return None, None, fallback_venue
    s = soup_of(page)
    hh, mm = clock_of(text_of(s.select_one(".entry__view--top .date, .entryIntro .date, span.date")), (None, None))
    loc = s.select_one(".item.location, .location")
    venue = re.sub(r"^\s*(Staðsetning|Location)\s*", "", text_of(loc), flags=re.I) if loc else ""
    return hh, mm, venue or fallback_venue


def scrape_moya_site(name, url, default_region, now, until):
    html = get(url)
    if not html:
        return None
    events, seen, details = [], set(), 0
    for card in soup_of(html).select(".event__single, div.event"):
        title_el = card.select_one(".entryTitle")
        link = card.select_one(".entryTitle a[href]") or card.select_one("a[href]")
        date_el = card.select_one(".date")
        if not (title_el and link and date_el):
            continue
        href = urljoin(url, link["href"])
        if "facebook.com" in href:
            href = href.split("?")[0]
        if href in seen:
            continue
        seen.add(href)
        codes = " ".join(card.get("class") or []).lower()
        labels = " ".join(text_of(x) for x in card.select(".category, .event__category"))
        if MOYA_SKIP.search(codes) and not re.search(r"music|culture|kids|festival", codes):
            continue
        span = date_span(text_of(date_el), now)
        if not span:
            continue
        hh, mm = clock_of(text_of(card.select_one(".time")), (None, None))
        venue = text_of(card.select_one(".location"))
        title = text_of(title_el)
        start, note = listing_start(span[0], span[1], hh or 0, mm or 0, now)
        if not start or start > until:
            continue
        if hh is None and urlparse(href).netloc == urlparse(url).netloc:
            prev = PREVIOUS_BY_URL.get(href)
            if prev and prev.get("start", "")[:10] == f"{start:%Y-%m-%d}":
                hh, mm = int(prev["start"][11:13]), int(prev["start"][14:16])
                venue = venue or prev.get("venue", "")
            elif details < MOYA_DETAIL_PAGES and not note:
                details += 1
                hh, mm, venue = moya_detail(href, venue)
            if hh is not None:
                start = start.replace(hour=hh, minute=mm)
        category = next((c for c, pat in MOYA_CODES if re.search(pat, codes)), None)
        category = guess_category(title + " " + labels, category or "Culture & Learning") \
            if category in (None, "Culture & Learning") else category
        region = region_from_postcode((re.search(r"-(\d{3})-iceland", codes) or [None, ""])[1]) \
            or region_from_text(venue, codes.replace("-", " ")) or default_region
        img = card.select_one("img[src], img[data-src]")
        events.append(make_event(
            name, "moya-" + re.sub(r"\W+", "", urlparse(url).netloc.split(".")[-2])[:12] + "-" + href.rstrip("/").split("/")[-1][:60],
            title, start, href, venue=venue, category=category, region=region, subtitle=note,
            image=urljoin(url, img.get("src") or img.get("data-src")) if img else None))
    return events


def scrape_moya(now, until):
    events = []
    for name, url, region in MOYA_CALENDARS:
        try:
            found = scrape_moya_site(name, url, region, now, until)
        except Exception as e:                     # one site's hiccup shouldn't drop the others
            print(f"  {name}: error {e!r}")
            found = None
        if found is None:                          # site down: keep its events from the last run
            found = [e for e in PREVIOUS_BY_URL.values() if e.get("source") == name and parse_dt(e["start"]) >= now]
            print(f"  {name}: not reachable, keeping {len(found)} previous events")
        else:
            print(f"  {name}: {len(found)} events")
        events.extend(found)
    return events


# ===================================================================
#  Other town calendars
# ===================================================================
def scrape_gardabaer(now, until):
    """Garðabær lists its events; each event page carries schema.org Event data."""
    html = get(GARDABAER_EVENTS)
    if not html:
        return []
    links = []
    for a in soup_of(html).select("a[href^='/vidburdir/']"):
        href = urljoin(GARDABAER_EVENTS, a["href"])
        if href not in links and "senda-inn" not in href:
            links.append(href)
    events = []
    for href in links[:40]:
        page = get(href)
        objs, s = find_ld_events(page) if page else ([], None)
        objs = [o for o in objs if o.get("@type") == "Event"]
        if not objs:
            continue
        o = objs[0]
        first, last = parse_dt(o.get("startDate")), parse_dt(o.get("endDate"))
        if not first:
            continue
        start, note = listing_start(first.date(), last.date() if last else None, first.hour, first.minute, now)
        if not start or start > until:
            continue
        loc = o.get("location") if isinstance(o.get("location"), dict) else {}
        venue = loc.get("name") or ""
        image = o.get("image")[0] if isinstance(o.get("image"), list) and o.get("image") else None
        title = o.get("name") or ""
        events.append(make_event("Garðabær", "gbr-" + href.rstrip("/").split("/")[-1], title, start, href,
                                 venue=venue or "Garðabær", region=CAPITAL, subtitle=note, image=image,
                                 category=guess_category(title + " " + (o.get("description") or "")[:300])))
    return events


def scrape_mosfellsbaer(now, until):
    html = get(MOSFELLSBAER_EVENTS)
    if not html:
        return []
    events, seen = [], set()
    for card in soup_of(html).select("a[href*='/mannlif/vidburdir/']"):
        href = card["href"]
        strings = list(card.stripped_strings)
        full = next((x for x in strings if DAY_MONTH.search(x) and re.search(r"\d{4}", x)), "")
        span = date_span(full, now)
        if href in seen or not span:
            continue
        seen.add(href)
        times = next((x for x in strings if CLOCK.search(x)), "")
        hh, mm = clock_of(times)
        start, note = listing_start(span[0], span[1], hh, mm, now)
        if not start or start > until:
            continue
        labels = [text_of(s) for s in card.select("span.label")]
        title = text_of(card.select_one("h1, h2, h3, h4"))
        if not title:                              # card has no heading: take the line before the date
            i = strings.index(full) if full in strings else 0
            title = strings[i - 1] if i > 0 else href.rstrip("/").split("/")[-1].replace("-", " ").capitalize()
        venue = labels[-1] if labels else ""
        events.append(make_event("Mosfellsbær", "mos-" + href.rstrip("/").split("/")[-1], title, start, href,
                                 venue=f"{venue}, Mosfellsbæ" if venue else "Mosfellsbær", region=CAPITAL,
                                 subtitle=note, category=guess_category(title + " " + " ".join(labels))))
    return events


def scrape_reykjanesbaer(now, until):
    html = get(REYKJANESBAER_EVENTS)
    if not html:
        return []
    events = []
    for card in soup_of(html).select("a[class*='eventCard__link']"):
        span = date_span(text_of(card.select_one("time")), now)
        title = text_of(card.select_one("h3, [class*='eventCard__title']"))
        if not (span and title):
            continue
        info = [text_of(p) for p in card.select("[class*='eventCardInfo__text']")]
        hh, mm = clock_of(" ".join(info), (20, 0))
        start, note = listing_start(span[0], span[1], hh, mm, now)
        if not start or start > until:
            continue
        venue = next((x for x in info if not CLOCK.search(x)), "")
        more = text_of(card.select_one("[class*='eventCard__text']"))
        href = urljoin(REYKJANESBAER_EVENTS, card["href"])
        events.append(make_event("Reykjanesbær", "rnb-" + href.rstrip("/").split("/")[-1], title, start, href,
                                 venue=venue or "Reykjanesbær", region=region_from_text(venue) or REYKJANES,
                                 subtitle=note or (more[:120] or None), category=guess_category(title + " " + more)))
    return events


def scrape_arborg(now, until):
    html = get(ARBORG_EVENTS)
    if not html:
        return []
    events = []
    for item in soup_of(html).select("div.item[data-evtid]"):
        link = item.select_one("h3 a[href]")
        span = date_span(text_of(item.select_one(".date")), now)
        if not (link and span):
            continue
        start, note = listing_start(span[0], span[1], *clock_of(text_of(item.select_one(".date"))), now)
        if not start or start > until:
            continue
        venue = text_of(item.select_one(".venue"))
        title = text_of(link)
        events.append(make_event("Árborg", f"arborg-{item.get('data-evtid')}", title, start, urljoin(ARBORG_EVENTS, link["href"]),
                                 venue=venue or "Árborg", region=region_from_text(venue) or SOUTH, subtitle=note,
                                 category=guess_category(title + " " + text_of(item.select_one(".summary")))))
    return events


def scrape_vestmannaeyjar(now, until):
    html = get(VESTMANNAEYJAR_EVENTS)
    if not html:
        return []
    events = []
    for card in soup_of(html).select(".card__block"):
        link = card.select_one(".title a[href]")
        head = card.select_one(".headline")
        if not link or not head or "senda-inn" in link["href"]:
            continue
        span = date_span(" ".join(head.stripped_strings).replace("–", " - "), now)
        if not span:
            continue
        start, note = listing_start(span[0], span[1], 0, 0, now)
        if not start or start > until:
            continue
        title = text_of(link)
        events.append(make_event("Vestmannaeyjar", "vey-" + link["href"].rstrip("/").split("/")[-1], title, start,
                                 urljoin(VESTMANNAEYJAR_EVENTS, link["href"]), venue="Vestmannaeyjar", region=SOUTH,
                                 subtitle=note, category=guess_category(title)))
    return events


# ------------------------------------------------------- extra (hand-added) events
EXTRA_FILE = Path("extra-events.txt")
SPORT_CATEGORIES = {"Football", "Handball", "Volleyball", "Basketball"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def load_extra_events(now, until):
    """Events typed by hand into extra-events.txt (one block per event, blank line between)."""
    if not EXTRA_FILE.exists():
        return []
    events = []
    blocks = re.split(r"\n\s*\n", EXTRA_FILE.read_text(encoding="utf-8"))
    for n, block in enumerate(blocks, 1):
        fields = {}
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
        if not fields.get("title"):
            continue
        try:
            first = datetime.strptime(fields.get("date", ""), "%Y-%m-%d")
            hh, mm = (int(x) for x in (fields.get("time") or "20:00").replace(".", ":").split(":")[:2])
            first = first.replace(hour=hh, minute=mm, tzinfo=timezone.utc)
        except ValueError:
            print(f"  extra-events.txt: skipped \"{fields['title']}\" (date must look like 2026-12-05, time like 19:30)")
            continue
        repeat = (fields.get("repeat") or "").lower()
        last = until
        if fields.get("until"):
            try:
                last = min(until, datetime.strptime(fields["until"], "%Y-%m-%d").replace(
                    hour=23, minute=59, tzinfo=timezone.utc))
            except ValueError:
                pass
        starts = [first]
        if repeat == "weekly":
            t = first
            while t + timedelta(days=7) <= last:
                t += timedelta(days=7)
                starts.append(t)
        starts = [t for t in starts if now - timedelta(hours=3) <= t <= until and t <= last]
        if not starts:
            continue
        category = fields.get("category") or "Music & Arts"
        sport = category in SPORT_CATEGORIES
        national = (fields.get("national") or "").lower() in ("yes", "true", "já", "ja")
        price = re.search(r"\d[\d.]*", fields.get("price") or "")
        venue = fields.get("venue", "")
        slug = re.sub(r"\W+", "-", fields["title"].lower()).strip("-")[:40]
        weekly = repeat == "weekly"
        for i, start in enumerate(starts if weekly else starts[:1]):
            events.append({
                "id": f"extra-{slug}-{start.strftime('%Y%m%d')}",
                "source": "Extra events",
                "title": fields["title"],
                "subtitle": fields.get("subtitle") or ("Every " + WEEKDAYS[start.weekday()].capitalize() if weekly else None),
                "start": start.isoformat(),
                "more_dates": 0,
                "venue": venue,
                "price_from": int(price.group(0).replace(".", "")) if price else None,
                "currency": "ISK",
                "category": category,
                "sport": sport,
                "national": national,
                "region": fields.get("region") or region_from_text(venue) or region_from_club(fields["title"].split("–")[0]) or None,
                "image": None,
                "url": fields.get("link") or "",
            })
    return events


# ------------------------------------------------------------------ main
COMMON_WORDS = set()             # words in many titles ("jólatónleikar", "airwaves"): no help in matching


def similar_listed(e, listed):
    """True when another source already lists an event on the same day whose title shares
    most of this one's distinctive words (half or more of the shorter title's), in the same region."""
    day, words = e["start"][:10], _words(e["title"]) - COMMON_WORDS
    if not words:
        return False
    for o in listed:
        if o["start"][:10] != day or o["source"] == e["source"] or o.get("sport"):
            continue
        if e.get("region") and o.get("region") and e["region"] != o["region"]:
            continue
        theirs = _words(o["title"]) - COMMON_WORDS
        if theirs and len(words & theirs) >= 0.5 * min(len(words), len(theirs)):
            return True
    return False


def dedupe(events):
    """Harpa sells through Tix and shares its event IDs; keep the Tix copy."""
    titles = {e["title"].lower() for e in events}
    counts = {}
    for t in titles:
        for w in _words(t):
            counts[w] = counts.get(w, 0) + 1
    COMMON_WORDS.clear()
    COMMON_WORDS.update(w for w, n in counts.items() if n >= 4)
    out, keys = [], set()
    for e in sorted(events, key=lambda e: e["source"] != "Tix.is"):
        prefix, num = e["id"].split("-", 1)
        if prefix not in ("tix", "harpa"):   # only Tix and Harpa share ID numbers
            num = e["id"]
        key2 = (e["title"].lower(), e["start"][:16])
        if num in keys or key2 in keys or (e.get("tix_id") and e["tix_id"] in keys):
            continue   # already listed (the city calendar often links to the same Tix event)
        if prefix in ("sena", "idno", "kop", "hfj"):
            day, words = e["start"][:10], _words(e["title"])
            if any(o["start"][:10] == day and words & _words(o["title"]) for o in out):
                continue   # same show already listed from Tix or another source
        elif not e.get("sport") and e["source"] not in ("Tix.is", "Extra events") and similar_listed(e, out):
            continue       # same event already listed by a venue or another calendar
        keys.update({num, key2})
        out.append(e)
    return sorted(out, key=lambda e: e["start"])


# ---------------------------------------------------------------- kids' events
# Children's events are often listed by town calendars, libraries and Harpa as music,
# culture or Christmas. Any event whose title (or subtitle) has one of these words is
# moved to Family, so a page can hide or show kids' events with one setting.
# Sports matches and your own extra-events.txt entries are never changed.
KIDS_WORDS = re.compile(
    r"\bbarn|\bbörn|barna|fjölskyld|krakk|krílaf|smábarn|ungbarn|leikskól|foreldramorg|"
    r"sögustund|sunnudagaskól|brúðuleik|"
    r"\bkids?\b|children|\bchild\b|toddler|\bbab(y|ies)\b|story ?time|sunday school|"
    r"puppet|for (the )?(whole )?family|families|family (programme|program|day|show|time|concert|event|storytime|fun)",
    re.I)
KIDS_KEEP_SOURCES = {"Extra events"}
SPORTS = {"Football", "Handball", "Volleyball", "Basketball"}


def retag_comedy(events):
    """Stand-up that a source listed as theatre or culture -> Comedy (checked on titles)."""
    n = 0
    for e in events:
        if e.get("category") in SPORTS or e.get("category") in ("Family", "Comedy") or e.get("source") in KIDS_KEEP_SOURCES:
            continue
        if re.search(COMEDY_WORDS, f"{e.get('title', '')} {e.get('subtitle') or ''}", re.I):
            e["category"] = "Comedy"
            n += 1
    if n:
        print(f"Moved to Comedy (stand-up): {n}")
    return events


def retag_kids(events):
    moved = {}
    for e in events:
        if e.get("category") in SPORTS or e.get("category") == "Family" or e.get("source") in KIDS_KEEP_SOURCES:
            continue
        if KIDS_WORDS.search(f"{e.get('title', '')} {e.get('subtitle') or ''}"):
            moved[e["category"]] = moved.get(e["category"], 0) + 1
            e["category"] = "Family"
    if moved:
        print("Moved to Family (kids' events):", ", ".join(f"{n} from {c}" for c, n in moved.items()))
    return events


def main():
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=DAYS_AHEAD)

    previous = []
    if OUTPUT_FILE.exists():
        try:
            previous = json.loads(OUTPUT_FILE.read_text())["events"]
        except Exception:
            previous = []

    PREVIOUS_BY_URL.update({e.get("url"): e for e in previous if e.get("url")})

    # Order matters: when the same show is listed by several sources, the first one wins,
    # so ticket sellers and venues come before the general listings sites.
    results = {}
    for source, scraper in [("Tix.is", scrape_tix), ("Harpa", scrape_harpa),
                            ("Visit Akureyri", scrape_akureyri), ("Reykjavík city", scrape_hvirfill),
                            ("HSÍ", scrape_hsi), ("KSÍ", scrape_ksi), ("BLÍ", scrape_bli),
                            ("KKÍ", scrape_kki), ("Kópavogur", scrape_kopavogur),
                            ("Hafnarfjörður", scrape_hafnarfjordur), ("Iðnó", scrape_idno),
                            ("Sena Live", scrape_sena),
                            ("Þjóðleikhúsið", scrape_leikhusid), ("Gamla bíó", scrape_gamlabio),
                            ("Bíó Paradís", scrape_bioparadis), ("Nordic House", scrape_nordichouse),
                            ("Iceland Airwaves", scrape_airwaves), ("Reykjavík Pride", scrape_pride),
                            ("Garðabær", scrape_gardabaer), ("Mosfellsbær", scrape_mosfellsbaer),
                            ("Reykjanesbær", scrape_reykjanesbaer), ("Árborg", scrape_arborg),
                            ("Vestmannaeyjar", scrape_vestmannaeyjar), ("Regional calendars", scrape_moya),
                            ("Visit Reykjavík", scrape_visitreykjavik), ("Grapevine", scrape_grapevine),
                            ("Vísir", scrape_visir), ("Extra events", load_extra_events)]:
        try:
            results[source] = scraper(now, until)
        except Exception as e:  # one broken site shouldn't stop the others
            print(f"{source}: error {e!r}")
            results[source] = []

    moya_names = {name for name, _, _ in MOYA_CALENDARS}
    all_events = []
    for source, found in results.items():
        print(f"{source}: {len(found)} events")
        names = moya_names if source == "Regional calendars" else {source}
        kept = [e for e in previous if e["source"] in names and parse_dt(e["start"]) >= now]
        if source == "Extra events":
            kept = []          # your own file: always use exactly what's in it today
        if source in ACCUMULATE_SOURCES and kept:
            # This source only shows the next few days: add today's finds to what's already listed.
            have = {e["id"] for e in found}
            found = found + [e for e in kept if e["id"] not in have]
            print(f"  -> {len(found)} including earlier days' finds")
            all_events.extend(found)
            continue
        if not found:
            # A site changed or was down: keep last good data for that source.
            print(f"  -> keeping {len(kept)} previous {source} events")
            found = kept
        elif len(kept) >= 20 and len(found) < 0.3 * len(kept):
            # A sudden big drop usually means part of a site failed: keep yesterday's
            # upcoming events too, so pages don't suddenly empty.
            have = {e["id"] for e in found}
            extra = [e for e in kept if e["id"] not in have]
            print(f"  -> only {len(found)} vs {len(kept)} yesterday; also keeping {len(extra)} previous events")
            found = found + extra
        all_events.extend(found)

    events = retag_comedy(retag_kids(dedupe(all_events)))
    for e in events:
        e.pop("tix_id", None)

    for source, venues in _unplaced.items():
        print(f"{source}: no region for {len(venues)} venue(s): {', '.join(sorted(venues))}")
    by_region = {}
    for e in events:
        by_region[e.get("region") or "(no region)"] = by_region.get(e.get("region") or "(no region)", 0) + 1
    print("Events per region:", by_region)
    if not events:
        print("No events found at all; leaving the existing file untouched.")
        sys.exit(1)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(
        {"generated_at": now.isoformat(), "count": len(events), "events": events},
        ensure_ascii=False, indent=2))
    print(f"Wrote {len(events)} events to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
