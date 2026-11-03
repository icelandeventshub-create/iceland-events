#!/usr/bin/env python3
"""
Pull upcoming events in Iceland and write them to docs/events.json for a
Squarespace Code Block to display:
  - music, arts, family and festival events from Tix.is (nationwide) and Harpa.is
  - North Iceland events from Visit Akureyri (visitakureyri.is)
  - Reykjavík city's own events calendar (Hvirfill, reykjavik.is)
  - handball matches from HSÍ (hsi.is)
  - football matches from KSÍ (ksi.is)
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


def get(url):
    if not allowed(url):
        print(f"  skipped (robots.txt): {url}")
        return None
    time.sleep(REQUEST_DELAY)
    for attempt, wait in enumerate((5, 20, None), 1):   # up to 3 tries for flaky connections
        try:
            r = session.get(url, timeout=45)
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
                 "vogum", "hljómahöll", "hvalsnes", "frumleikhús"]),
    (WEST, ["akranes", "borgarn", "stykkish", "grundarfj", "ólafsvík", "snæfells", "búðardal",
            "reykholt", "ísafj", "bolungarv", "patreksfj", "þingeyr", "flateyr", "súðav",
            "hólmav", "tálknafj", "bíldudal", "westfjord", "vestfj"]),
    (NORTH, ["akureyr", "menningarhúsið hof", "græni hatt", "freyvang", "samkomuhúsið",
             "lystigarð", "múlaberg", "dalvík", "ólafsfj", "siglufj", "húsav", "mývatn",
             "dimmubor", "jarðböð", "sauðárkr", "blönduós", "skagastr", "hvammstang",
             "varmahl", "hrísey", "grenivík", "eyjafj", "þórshöfn", "kópasker", "raufarh"]),
    (EAST, ["egilsst", "seyðisfj", "neskaupst", "reyðarfj", "eskifj", "fáskrúðsfj",
            "djúpiv", "breiðdal", "borgarfjörður eystri", "borgarfirði eystra", "vopnafj"]),
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
               "framheimil", "úlfarsárdal", "víkin", "kuehne", "heklu höll", "myntkaup"]),
]

# Sports clubs -> region, for matches whose venue is a sponsor's name.
CLUB_REGIONS = {
    CAPITAL: ["haukar", "fh", "valur", "fram", "ír", "víkingur", "fjölnir", "kr", "þróttur",
              "leiknir", "fylkir", "ármann", "grótta", "stjarnan", "hk", "breiðablik",
              "augnablik", "afturelding", "kórdrengir", "hvíti riddarinn", "berserkir",
              "álftanes", "ísland", "kfg"],
    REYKJANES: ["grindavík", "keflavík", "njarðvík", "reynir", "víðir", "þróttur v"],
    WEST: ["ía", "hörður", "vestri", "víkingur ó", "skallagrímur", "kári"],
    NORTH: ["ka", "þór", "ka/þór", "völsungur", "tindastóll", "kf", "dalvík", "magni"],
    EAST: ["höttur", "fjarðabyggð", "leiknir f", "einherji", "huginn"],
    SOUTH: ["íbv", "selfoss", "hamar", "mílan", "ægir", "kfr", "uppsveitir", "sindri"],
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
    name = re.sub(r"\s+\d+$", "", (team or "").strip().lower())   # "Stjarnan 2" -> "stjarnan"
    for region, clubs in CLUB_REGIONS.items():
        if name in clubs:
            return region
    return None


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


def scrape_ksi(now, until):
    events, seen = [], set()
    for page in KSI_PAGES:
        html = get(page)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.find_all("a", href=re.compile(r"leikur\?id=\d+")):
            match_id = re.search(r"id=(\d+)", link["href"]).group(1)
            if match_id in seen:
                continue
            # Climb to the match row: the nearest box holding both team names.
            row = link.parent
            while row is not None and len(row.select("span.body-4")) < 2:
                row = row.parent
            if row is None:
                continue
            seen.add(match_id)

            info = [s.get_text(" ", strip=True) for s in row.select("span.body-5")]
            teams = [s.get_text(strip=True) for s in row.select("span.body-4")
                     if s.get_text(strip=True) != "-"]
            comp_link = row.find("a", href=re.compile(r"/oll-mot/mot\?id="))
            comp = comp_link.get_text(" ", strip=True) if comp_link else (info[2] if len(info) > 2 else "")
            if len(teams) < 2 or not info:
                continue
            if not any(c.lower() in comp.lower() for c in KSI_COMPETITIONS):
                continue
            is_national = "landslid" in page
            if is_national and re.search(r"\bU-?\d{2}\b", " ".join([comp] + teams)):
                continue  # youth national teams (U17, U21, ...)
            if is_national and teams[0] != "Ísland":
                continue  # home matches only

            d = KSI_DATE.search(info[0])
            if not d:
                continue
            day, month, hh, mm = d.groups()
            month_n = IS_MONTHS[month.lower()]
            year = now.year + (1 if month_n < now.month - 1 else 0)  # listings carry no year
            start = datetime(year, month_n, int(day), int(hh), int(mm), tzinfo=timezone.utc)
            if not (now <= start <= until):
                continue

            events.append({
                "id": f"ksi-{match_id}",
                "source": "KSÍ",
                "title": f"{teams[0]} – {teams[1]}",
                "subtitle": f"National team · {comp}" if is_national else comp,
                "start": start.isoformat(),
                "more_dates": 0,
                "venue": info[1] if len(info) > 1 else "",
                "price_from": None,
                "currency": "ISK",
                "category": "Football",
                "region": region_from_text(info[1] if len(info) > 1 else "") or region_from_club(teams[0]),
                "national": is_national,
                "image": None,
                "url": urljoin(page, link["href"]),
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


# ------------------------------------------------------------------ main
def dedupe(events):
    """Harpa sells through Tix and shares its event IDs; keep the Tix copy."""
    out, keys = [], set()
    for e in sorted(events, key=lambda e: e["source"] != "Tix.is"):
        prefix, num = e["id"].split("-", 1)
        if prefix not in ("tix", "harpa"):   # only Tix and Harpa share ID numbers
            num = e["id"]
        key2 = (e["title"].lower(), e["start"][:16])
        if num in keys or key2 in keys or (e.get("tix_id") and e["tix_id"] in keys):
            continue   # already listed (the city calendar often links to the same Tix event)
        keys.update({num, key2})
        out.append(e)
    return sorted(out, key=lambda e: e["start"])


def main():
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=DAYS_AHEAD)

    previous = []
    if OUTPUT_FILE.exists():
        try:
            previous = json.loads(OUTPUT_FILE.read_text())["events"]
        except Exception:
            previous = []

    results = {}
    for source, scraper in [("Tix.is", scrape_tix), ("Harpa", scrape_harpa),
                            ("Visit Akureyri", scrape_akureyri), ("Reykjavík city", scrape_hvirfill),
                            ("HSÍ", scrape_hsi), ("KSÍ", scrape_ksi)]:
        try:
            results[source] = scraper(now, until)
        except Exception as e:  # one broken site shouldn't stop the others
            print(f"{source}: error {e!r}")
            results[source] = []

    all_events = []
    for source, found in results.items():
        print(f"{source}: {len(found)} events")
        kept = [e for e in previous if e["source"] == source and parse_dt(e["start"]) >= now]
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

    events = dedupe(all_events)
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
