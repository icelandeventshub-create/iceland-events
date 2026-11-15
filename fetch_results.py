#!/usr/bin/env python3
"""
Results of past matches for the sports page's History tab, written to docs/results.json.

  Handball    HSÍ  "previous matches" feed (the same leagues as the events list, plus national teams)
  Football    KSÍ  latest results on ksi.is (clubs and senior national teams)
  Basketball  KKÍ  latest results on each league's page (read with the hidden browser)
  Volleyball  BLÍ  finished matches on each competition's match pages

Some sources only show their latest few results, so each day's finds are added to the
results already saved, and the history builds up. Results older than KEEP_DAYS are dropped.
Runs daily after scrape_events.py in .github/workflows/update-events.yml.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import scrape_events as se     # shares the scraper's settings, polite fetching and region guessing

OUTPUT_FILE = Path("docs/results.json")
EVENTS_FILE = Path("docs/events.json")
KEEP_DAYS = 180                  # how far back the History tab goes
NATIONAL_AWAY_RESULTS = True     # also show Iceland's away results (the matches list only has home games)

EVENT_BY_ID = {}                 # yesterday's upcoming matches by id: venue and region for results that lack them


def result(rid, sport, home, away, sh, sa, start, *, subtitle="", venue="", url="", national=False, region=None):
    known = EVENT_BY_ID.get(rid) or {}
    venue = venue or known.get("venue", "")
    return {
        "id": rid, "category": sport, "sport": True, "home": home.strip(), "away": away.strip(),
        "score_home": int(sh), "score_away": int(sa), "title": f"{home.strip()} – {away.strip()}",
        "subtitle": subtitle or known.get("subtitle", ""), "start": start.isoformat(), "venue": venue,
        "region": region or known.get("region") or se.region_from_text(venue) or se.region_from_club(home),
        "national": national, "url": url or known.get("url", ""),
    }


# ------------------------------------------------------------------ HSÍ handball
def handball(since):
    out = []
    clubs = (se.get_json("https://www.hsi.is/api/hsi/matches/M/previous?months=2") or {}).get("data", [])
    for m in clubs:
        comp = (m.get("TOURNAMENT_SHORT_NAME") or "").strip()
        if not any(c.lower() in comp.lower() for c in se.HSI_COMPETITIONS):
            continue
        label = comp.split("|")[-2].strip() if comp.count("|") >= 2 else comp
        r = hsi_result(m, label, since)
        if r:
            out.append(r)
    nat = (se.get_json("https://www.hsi.is/api/hsi/matches/A/previous?months=6") or {}).get("data", [])
    for m in nat:
        cls = m.get("TOURNAMENT_CLASS_NAME") or ""
        home, away = (m.get("HOME") or "").strip(), (m.get("GUESTS") or "").strip()
        if not cls.startswith("A landslið") or "Ísland" not in (home, away):
            continue
        if home != "Ísland" and not NATIONAL_AWAY_RESULTS:
            continue
        who = "Women's" if cls.rstrip().endswith("kv") else "Men's"
        comp = (m.get("TOURNAMENT_SHORT_NAME") or "").split("|")[-1].strip()
        r = hsi_result(m, f"{who} national team · {comp}", since, national=True)
        if r:
            out.append(r)
    return out


def hsi_result(m, label, since, national=False):
    sh, sa = (m.get("GAMES_RESULT_HOME") or "").strip(), (m.get("GAMES_RESULT_GUEST") or "").strip()
    if not (sh.isdigit() and sa.isdigit()):
        return None
    try:
        start = datetime.fromisoformat(f"{m.get('GAMES_ACTUAL_MATCHDAY')}T{(m.get('GAMES_MATCHDAY_TIME') or '00:00').strip()}").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if start < since:
        return None
    venue = (m.get("PLAYING_FIELD_NAME") or "").strip()
    return result(f"hsi-{m.get('MATCH_ID')}", "Handball", m.get("HOME") or "", m.get("GUESTS") or "", sh, sa, start,
                  subtitle=label, venue=venue, url=f"https://www.hsi.is/match/{m.get('MATCH_ID')}", national=national)


# ------------------------------------------------------------------ KSÍ football
SCORE = re.compile(r"^\s*(\d+)\s*[-–]\s*(\d+)\s*$")


def football(since, now):
    out = []
    for page in ("https://www.ksi.is/leikir-og-urslit/felagslid/?toggle=results",
                 "https://www.ksi.is/leikir-og-urslit/landslid/?toggle=results"):
        html = se.get(page)
        if not html:
            continue
        national = "landslid" in page
        soup = BeautifulSoup(html, "html.parser")
        rows = []
        for date_el in soup.select("span.body-5"):
            if not se.KSI_DATE.search(date_el.get_text(" ", strip=True)):
                continue
            row = date_el.parent
            while row is not None and len(row.select("span.body-4")) < 2:
                row = row.parent
            if row is not None and all(row is not r for r in rows):
                rows.append(row)
        for row in rows:
            info = [x.get_text(" ", strip=True) for x in row.select("span.body-5")]
            parts = [x.get_text(" ", strip=True) for x in row.select("span.body-4")]
            score = next((SCORE.match(p) for p in parts if SCORE.match(p)), None)
            teams = [p for p in parts if p and not SCORE.match(p) and p != "-"]
            if not score or len(teams) < 2 or not info:
                continue
            d = se.KSI_DATE.search(info[0])
            day, month, hh, mm = d.groups()
            month_n = se.IS_MONTHS[month.lower()]
            year = now.year - (1 if month_n > now.month + 1 else 0)    # results are in the past
            start = datetime(year, month_n, int(day), int(hh), int(mm), tzinfo=timezone.utc)
            if not (since <= start <= now):
                continue
            comp_link = row.find("a", href=re.compile(r"/oll-mot/mot\?id="))
            comp = comp_link.get_text(" ", strip=True) if comp_link else (info[2] if len(info) > 2 else "")
            if national:
                if se.YOUTH.search(" ".join([comp] + teams)):
                    continue
                if not any(t in se.ICELAND_NAMES for t in teams[:2]):
                    continue
                if teams[0] not in se.ICELAND_NAMES and not NATIONAL_AWAY_RESULTS:
                    continue
                women = re.search(r"women|kvenna", comp, re.I)
                short = re.sub(r"^\d{4}\s+", "", comp).split(" - ")[0].strip()
                subtitle = ("Women's" if women else "Men's") + " national team · " + short
            else:
                if not any(c.lower() in comp.lower() for c in se.KSI_COMPETITIONS):
                    continue
                subtitle = comp
            link = row.find("a", href=re.compile(r"leikur\?id=\d+"))
            mid = re.search(r"id=(\d+)", link["href"]).group(1) if link else start.strftime("%Y%m%d%H%M") + re.sub(r"\W", "", teams[1].lower())
            venue = info[1] if len(info) > 1 and info[1] != comp else ""
            out.append(result(f"ksi-{mid}", "Football", teams[0], teams[1], score.group(1), score.group(2), start,
                              subtitle=subtitle, venue=venue, url=urljoin(page, link["href"]) if link else page,
                              national=national, region=se.region_from_text(venue) if national else None))
    return out


# ------------------------------------------------------------------ KKÍ basketball
KKI_RESULTS_JS = """
() => [...document.querySelectorAll('tr')].filter(r => r.querySelector('a[game_id]') && r.children.length >= 4
        && /\\d+\\s*:\\s*\\d+/.test(r.children[2].textContent)).map(r => {
  const c = [...r.children], teams = [...r.querySelectorAll('a[team_id]')].map(a => a.textContent.trim());
  const g = r.querySelector('a[game_id]');
  return { game_id: g.getAttribute('game_id'), when: g.textContent.trim(), href: g.getAttribute('href'),
           home: teams[0] || '', away: teams[1] || '', score: c[2].textContent.trim() };
})
"""


def basketball(since):
    from playwright.sync_api import sync_playwright
    out, seen = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="is-IS")
        try:
            for lid, label in se.KKI_LEAGUES.items():
                url = f"https://kki.is/motamal/leikir-og-urslit/motayfirlit/Deildir-flokkar?league_id={lid}"
                time.sleep(se.REQUEST_DELAY)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("tr a[game_id]", timeout=25000)
                    rows = page.evaluate(KKI_RESULTS_JS)
                except Exception as e:
                    print(f"KKÍ {label}: results not read ({type(e).__name__})")
                    continue
                n = 0
                for r in rows:
                    when, score = se.KKI_DATE.search(r["when"]), re.match(r"\s*(\d+)\s*:\s*(\d+)", r["score"])
                    if not (when and score and r["home"] and r["away"]) or r["game_id"] in seen:
                        continue
                    d, mo, y, hh, mm = (int(x) for x in when.groups())
                    start = datetime(y, mo, d, hh, mm, tzinfo=timezone.utc)
                    if start < since:
                        continue
                    seen.add(r["game_id"])
                    n += 1
                    out.append(result(f"kki-{r['game_id']}", "Basketball", r["home"], r["away"], score.group(1), score.group(2),
                                      start, subtitle=label, url=urljoin("https://kki.is/", r["href"] or "")))
                print(f"KKÍ {label}: {n} results")
        finally:
            browser.close()
    return out


# ------------------------------------------------------------------ BLÍ volleyball
def volleyball(since, now):
    out = []
    for cid, name in se.bli_competitions():
        home_page = se.get(f"{se.BLI_SITE}CompetitionHome.aspx?ID={cid}") or ""
        pids = sorted(set(re.findall(rf"CompetitionMatches\.aspx\?ID={cid}&(?:amp;)?PID=(\d+)", home_page, re.I)))
        label = re.sub(r"\s*\d{4}\s*-\s*\d{4}\s*", " ", name).strip()
        for pid in pids:
            page_url = f"{se.BLI_SITE}CompetitionMatches.aspx?ID={cid}&PID={pid}"
            html = se.get(page_url) or ""
            for chunk in re.split(r'id="[^"]*RADLIST_Matches_ctrl\d+_MatchRow"', html)[1:]:
                def field(*names):
                    for n in names:
                        m = re.search(r'_ctrl\d+_' + n + r'"[^>]*>([^<]*)<', chunk)
                        if m and m.group(1).strip():
                            return BeautifulSoup(m.group(1), "html.parser").get_text().strip()
                    return ""
                mid = re.search(r"mID=(\d+)", chunk)
                when = se.BLI_DATE.search(field("LB_DataOra"))
                sh, sa = field("LB_SetCasa"), field("LB_SetOspiti")
                home, away = field("Label2", "LBL_HomeTeamName"), field("Label4", "LBL_GuestTeamName")
                if not (mid and when and sh.isdigit() and sa.isdigit() and home and away):
                    continue
                d, mo, y, hh, mm = (int(x) for x in when.groups())
                start = datetime(y, mo, d, hh, mm, tzinfo=timezone.utc)
                if not (since <= start <= now):
                    continue
                out.append(result(f"bli-{mid.group(1)}", "Volleyball", home, away, sh, sa, start,
                                  subtitle=label, venue=field("LB_Palasport"), url=page_url))
    return out


# ------------------------------------------------------------------ main
def main():
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=KEEP_DAYS)
    try:
        EVENT_BY_ID.update({e["id"]: e for e in json.loads(EVENTS_FILE.read_text())["events"]})
    except Exception:
        pass
    try:
        previous = json.loads(OUTPUT_FILE.read_text())["results"] if OUTPUT_FILE.exists() else []
    except Exception:
        previous = []

    found = []
    for sport, collect in [("Handball", lambda: handball(since)), ("Football", lambda: football(since, now)),
                           ("Basketball", lambda: basketball(since)), ("Volleyball", lambda: volleyball(since, now))]:
        try:
            got = collect()
        except Exception as e:                    # one site's trouble shouldn't stop the others
            print(f"{sport}: error {e!r}")
            got = []
        print(f"{sport}: {len(got)} results")
        found.extend(got)

    merged = {r["id"]: r for r in previous if r.get("start", "") >= since.isoformat()}
    merged.update({r["id"]: r for r in found})            # today's copy wins (scores can be corrected)
    results = sorted(merged.values(), key=lambda r: r["start"], reverse=True)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps({"generated_at": now.isoformat(), "count": len(results), "results": results},
                                      ensure_ascii=False, indent=1))
    print(f"Wrote {len(results)} results ({len(found)} read today) to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
