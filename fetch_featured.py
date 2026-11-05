#!/usr/bin/env python3
"""
This week's picks: reads featured.txt, visits each link, and writes
docs/featured.json (plus a copy of each picture in docs/featured/) for the
homepage carousel.

Only the Python standard library is used, so nothing needs installing.

For each link it reads, in this order:
  1. what you typed in featured.txt (title:, date:, venue:, image:) - always wins
  2. the event page's own event details (schema.org "Event" data, used by
     Tix, Harpa, most ticket sites)
  3. the page's sharing preview (og:title, og:image), the same thing
     Facebook and WhatsApp show when you share the link
  4. the matching event in docs/events.json, if the daily scraper has it
If a page can't be reached this run, last run's details for that link are kept.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "featured.txt")
OUT_DIR = os.path.join(HERE, "docs")
OUT_JSON = os.path.join(OUT_DIR, "featured.json")
IMG_DIR = os.path.join(OUT_DIR, "featured")
EVENTS_JSON = os.path.join(OUT_DIR, "events.json")

# Public address of the docs folder (GitHub Pages), used for picture addresses.
PAGES_BASE = "https://icelandeventshub-create.github.io/iceland-events/"

USER_AGENT = "Mozilla/5.0 (compatible; IcelandEventsHub/1.0; +https://www.icelandeventshub.com)"
MAX_PICKS = 10            # the carousel is made for 5, but allows a few more
MAX_IMAGE_BYTES = 6_000_000
TIMEOUT = 25


# ---------------------------------------------------------------- featured.txt
FIELDS = {"link", "url", "why", "title", "date", "venue", "image", "button", "place", "where"}


def read_source(path):
    heading, picks, cur, last_key = "", [], {}, None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            s = line.strip()
            if s.startswith("#"):
                continue
            if not s:
                if cur:
                    picks.append(cur)
                cur, last_key = {}, None
                continue
            m = re.match(r"^([A-Za-zÁÉÍÓÚÝÞÆÖáéíóúýþæöð]+)\s*:\s*(.*)$", s)
            key = m.group(1).lower() if m else None
            if key == "heading" and not cur:
                heading = m.group(2).strip()
                continue
            if key in FIELDS:
                key = {"url": "link", "place": "venue", "where": "venue"}.get(key, key)
                cur[key] = m.group(2).strip()
                last_key = key
                continue
            if re.match(r"^https?://", s) and "link" not in cur:
                cur["link"], last_key = s, "link"      # a bare link line works too
            elif last_key == "why":
                cur["why"] = (cur.get("why", "") + " " + s).strip()   # why: can run over lines
            else:
                print(f"  ! featured.txt: didn't understand this line, skipped: {s}")
    if cur:
        picks.append(cur)
    picks = [p for p in picks if p.get("link") or p.get("title")]
    if len(picks) > MAX_PICKS:
        print(f"  ! {len(picks)} picks found, only the first {MAX_PICKS} are used")
    return heading, picks[:MAX_PICKS]


# ---------------------------------------------------------------- fetching
def fetch(url, accept="text/html,application/xhtml+xml"):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en,is;q=0.8"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read(MAX_IMAGE_BYTES + 1)
        return r.geturl(), r.headers, data


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.ld, self.title = {}, [], ""
        self._in_ld = self._in_title = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "link" and "image_src" in a.get("rel", "").lower() and a.get("href"):
            self.meta.setdefault("image_src", a["href"])
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_ld, self._buf = True, []
        elif tag == "title":
            self._in_title, self._buf = True, []

    def handle_data(self, data):
        if self._in_ld or self._in_title:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld.append("".join(self._buf))
        elif tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._buf).split())


def walk_ld(obj):
    """Yield every dict inside a JSON-LD blob (handles @graph and lists)."""
    if isinstance(obj, list):
        for x in obj:
            yield from walk_ld(x)
    elif isinstance(obj, dict):
        yield obj
        for v in obj.values():
            if isinstance(v, (list, dict)):
                yield from walk_ld(v)


def first_str(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        for x in v:
            s = first_str(x)
            if s:
                return s
    if isinstance(v, dict):
        return first_str(v.get("url") or v.get("contentUrl") or v.get("name") or "")
    return ""


def find_event(lds):
    for blob in lds:
        try:
            data = json.loads(blob.strip().rstrip(";"))
        except Exception:
            continue
        for d in walk_ld(data):
            t = d.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(isinstance(x, str) and x.endswith("Event") for x in types):
                return d
    return None


def clean_title(t, site=""):
    t = html.unescape(t or "").strip()
    # drop " | Tix.is", " - Harpa" style site names at the end
    for sep in (" | ", " – ", " — ", " - ", " · "):
        if sep in t:
            head, tail = t.rsplit(sep, 1)
            if len(tail) <= 30 and len(head) >= 4:
                t = head.strip()
    return t


def read_page(url):
    final, headers, body = fetch(url)
    ctype = headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ctype)
    text = body.decode(m.group(1) if m else "utf-8", errors="replace")
    p = PageParser()
    try:
        p.feed(text)
    except Exception:
        pass
    info = {}
    ev = find_event(p.ld)
    if ev:
        info["title"] = first_str(ev.get("name"))
        info["start"] = first_str(ev.get("startDate"))
        loc = ev.get("location")
        if isinstance(loc, list):
            loc = loc[0] if loc else None
        if isinstance(loc, dict):
            name = first_str(loc.get("name"))
            addr = loc.get("address")
            town = addr.get("addressLocality", "") if isinstance(addr, dict) else ""
            info["venue"] = name + (", " + town if town and town.lower() not in name.lower() else "") if name else town
        elif isinstance(loc, str):
            info["venue"] = loc
        info["image"] = first_str(ev.get("image"))
    mt = p.meta
    info["title"] = clean_title(info.get("title") or mt.get("og:title") or mt.get("twitter:title") or p.title)
    info["image"] = (mt.get("og:image:secure_url") or mt.get("og:image") or mt.get("twitter:image")
                     or mt.get("twitter:image:src") or info.get("image") or mt.get("image_src") or "")
    if info["image"]:
        info["image"] = urllib.parse.urljoin(final, html.unescape(info["image"]))
    info["start"] = info.get("start") or mt.get("event:start_time") or mt.get("startdate") or ""
    return info


def save_image(img_url, link):
    """Copy the picture to docs/featured/ so the site doesn't depend on the other site."""
    final, headers, data = fetch(img_url, accept="image/avif,image/webp,image/*,*/*;q=0.8")
    ctype = headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not ctype.startswith("image/") or len(data) > MAX_IMAGE_BYTES or len(data) < 300:   # skip tracking pixels
        raise ValueError(f"not a usable picture ({ctype or 'unknown type'}, {len(data)} bytes)")
    ext = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
           "image/gif": ".gif", "image/avif": ".avif"}.get(ctype, ".jpg")
    name = hashlib.sha1((link + "|" + img_url).encode()).hexdigest()[:16] + ext
    os.makedirs(IMG_DIR, exist_ok=True)
    with open(os.path.join(IMG_DIR, name), "wb") as f:
        f.write(data)
    return "featured/" + name


# ---------------------------------------------------------------- events.json
def norm(u):
    u = (u or "").strip().lower().split("#")[0].split("?")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", u)


def load_feed_events():
    try:
        with open(EVENTS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    items = data.get("events", data) if isinstance(data, dict) else data
    out = {}
    for e in items if isinstance(items, list) else []:
        if not isinstance(e, dict):
            continue
        for k in ("url", "link", "href", "ticket_url"):
            if isinstance(e.get(k), str) and e[k]:
                out.setdefault(norm(e[k]), e)
    return out


def from_feed(e):
    pick = lambda *ks: next((str(e[k]) for k in ks if e.get(k)), "")
    start = pick("start", "start_iso", "datetime", "date")
    t = pick("time")
    if start and t and "T" not in start and len(start) == 10:
        start = f"{start}T{t}"
    return {"title": pick("title", "name"), "start": start,
            "venue": pick("venue", "location", "place"), "image": pick("image", "img", "image_url")}


# ---------------------------------------------------------------- main
def main():
    if not os.path.exists(SOURCE):
        sys.exit("featured.txt not found")
    heading, picks = read_source(SOURCE)
    print(f"{len(picks)} pick(s) in featured.txt")

    previous = {}
    try:
        with open(OUT_JSON, encoding="utf-8") as f:
            for it in json.load(f).get("items", []):
                previous[norm(it.get("link"))] = it
    except Exception:
        pass
    feed = load_feed_events()

    items, keep_images = [], set()
    for i, p in enumerate(picks, 1):
        link = p.get("link", "")
        label = link or p.get("title", "")
        print(f"[{i}] {label}")
        found = {}
        if link and not re.search(r"(instagram|facebook)\.com", link, re.I):
            try:
                found = read_page(link)
                print(f"    title: {found.get('title') or '-'} | date: {found.get('start') or '-'} | "
                      f"venue: {found.get('venue') or '-'} | picture: {'yes' if found.get('image') else 'no'}")
            except Exception as e:
                print(f"    ! couldn't read the page ({e})")
            time.sleep(1)
        elif link:
            print("    Instagram/Facebook link: using only what's typed in featured.txt")

        fe = from_feed(feed[norm(link)]) if link and norm(link) in feed else {}
        prev = previous.get(norm(link), {}) if link else {}
        val = lambda k: p.get(k) or found.get(k) or fe.get(k) or ""

        item = {
            "link": link,
            "title": val("title") or prev.get("title") or "",
            "start": found.get("start") or fe.get("start") or prev.get("start") or "",
            "date": p.get("date", ""),
            "venue": val("venue") or prev.get("venue") or "",
            "why": p.get("why", ""),
            "button": p.get("button", ""),
            "image": "",
        }

        img_src = p.get("image") or found.get("image") or fe.get("image")
        if img_src:
            try:
                item["image"] = save_image(urllib.parse.urljoin(link or "", img_src), link or item["title"])
            except Exception as e:
                print(f"    ! couldn't copy the picture ({e}); linking to it directly")
                item["image"] = img_src if img_src.startswith("https://") else ""
        if not item["image"] and prev.get("image"):
            item["image"] = prev["image"]
            print("    -> keeping last run's picture")
        if not item["image"]:
            print("    ! no picture: the carousel will show your logo. Add an 'image:' line to fix.")
        if not item["title"]:
            print("    ! no title found: add a 'title:' line for this pick.")
        if item["image"].startswith("featured/"):
            keep_images.add(item["image"].split("/", 1)[1])
        items.append(item)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "heading": heading, "base": PAGES_BASE, "items": items}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # tidy up pictures from previous weeks
    if os.path.isdir(IMG_DIR):
        for name in os.listdir(IMG_DIR):
            if name not in keep_images:
                os.remove(os.path.join(IMG_DIR, name))
    print(f"Saved {len(items)} pick(s) to docs/featured.json")


if __name__ == "__main__":
    main()
