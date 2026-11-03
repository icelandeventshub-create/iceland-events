#!/usr/bin/env python3
"""
Pull upcoming music & arts events in Iceland from Tix.is and Harpa.is
and write them to docs/events.json for a Squarespace Code Block to display.

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
MAX_TIX_EVENT_PAGES = 150        # cap on detail pages fetched per run
REQUEST_DELAY = 1.0              # seconds between requests (be polite)
TIX_CATEGORIES = {               # Tix nav label -> label shown on your site
    "Music": "Music",
    "Theater": "Arts & Theatre",
    "Festivals": "Festivals",
}
# Optional: add a contact URL or email so site owners can reach you, e.g.
# "IcelandEventsFeed/1.0 (+https://yoursite.is/contact)"
USER_AGENT = "IcelandEventsFeed/1.0"

TIX_HOME = "https://tix.is/en"
HARPA_WHATS_ON = "https://www.harpa.is/en/whats-on"

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
    try:
        r = session.get(url, timeout=30)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        print(f"  failed: {url} ({e})")
        return None


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
            "image": image or meta(soup, "og:image"),
            "url": url,
        })
    return events


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
            "image": urljoin(HARPA_WHATS_ON, img["src"]) if img and img.get("src") else None,
            "url": urljoin(HARPA_WHATS_ON, a["href"]),
        })
    return events


# ------------------------------------------------------------------ main
def dedupe(events):
    """Harpa sells through Tix and shares its event IDs; keep the Tix copy."""
    out, keys = [], set()
    for e in sorted(events, key=lambda e: e["source"] != "Tix.is"):
        num = e["id"].split("-", 1)[1]
        key2 = (e["title"].lower(), e["start"][:16])
        if num in keys or key2 in keys:
            continue
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

    results = {"Tix.is": scrape_tix(now, until), "Harpa": scrape_harpa(now, until)}

    all_events = []
    for source, found in results.items():
        print(f"{source}: {len(found)} events")
        if not found:
            # A site changed or was down: keep last good data for that source.
            kept = [e for e in previous if e["source"] == source and parse_dt(e["start"]) >= now]
            print(f"  -> keeping {len(kept)} previous {source} events")
            found = kept
        all_events.extend(found)

    events = dedupe(all_events)
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
