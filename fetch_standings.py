#!/usr/bin/env python3
"""
League tables for the sports page, written to docs/standings.json.

  Football    KSÍ  Besta deild karla / kvenna   (ksi.is competition pages)
  Handball    HSÍ  Olís deild karla / kvenna    (hsi.is standings feed)
  Basketball  KKÍ  Bónus deild karla / kvenna   (kki.is "Staða" pages, read with the hidden browser)
  Volleyball  BLÍ  Unbrokendeild karla / kvenna (bli-web.dataproject.com standings pages)

If a table can't be read, yesterday's copy of that table is kept. Runs daily after
scrape_events.py in .github/workflows/update-events.yml.
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUTPUT_FILE = Path("docs/standings.json")
USER_AGENT = "IcelandEventsFeed/1.0"
DELAY = 1.5                      # seconds between requests

# ---- Which tables to show. Order = order of the tabs above each table.
# Football: competition name searched on ksi.is (the newest season that has started is used).
KSI_LEAGUES = [("Besta deild karla", "Men"), ("Besta deild kvenna", "Women")]
# Handball: competition name as HSÍ writes it.
HSI_LEAGUES = [("Olís deild karla", "Men"), ("Olís deild kvenna", "Women")]
# Basketball: kki.is league id (the same ids as KKI_LEAGUES in scrape_events.py).
KKI_LEAGUES = [(190, "Bónus deild karla", "Men"), (189, "Bónus deild kvenna", "Women")]
# Volleyball: competition name on BLÍ's site (the newest season is found automatically).
BLI_LEAGUES = [("Unbrokendeild Karla", "Men"), ("Unbrokendeild kvenna", "Women")]

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def get(url):
    time.sleep(DELAY)
    try:
        r = session.get(url, timeout=45)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        print(f"  failed: {url} ({e})")
        return None


def num(text):
    m = re.search(r"-?\d+", text or "")
    return int(m.group(0)) if m else 0


def table(league, label, url, rows, note=None):
    return {"league": league, "label": label, "url": url, "note": note, "rows": rows}


# ------------------------------------------------------------------ KSÍ football
def ksi_competition_ids(name, season):
    """{'main': id, 'upper': id, 'lower': id} for e.g. 'Besta deild karla' in a season."""
    html = get(f"https://www.ksi.is/oll-mot/?tab=leit&name={requests.utils.quote(name)}&season={season}&pageSize=20")
    found = {}
    for a in BeautifulSoup(html or "", "html.parser").select("a[href*='mot?id=']"):
        text = a.get_text(" ", strip=True)
        m = re.search(r"mot\?id=(\d+)", a["href"])
        if not m or not text.lower().startswith(name.lower()) or str(season) not in text:
            continue
        part = "upper" if "efri" in text.lower() else "lower" if "neðri" in text.lower() else "main"
        found.setdefault(part, m.group(1))
    return found


def ksi_rows(comp_id):
    html = get(f"https://www.ksi.is/oll-mot/mot?id={comp_id}")
    tb = BeautifulSoup(html or "", "html.parser").find("table")
    rows = []
    for tr in tb.select("tbody tr") if tb else []:
        cells = tr.find_all("td")
        link = cells[0].find("a") if cells else None
        name = link.find_all("span")[-1].get_text(strip=True) if link and link.find_all("span") else ""
        if len(cells) < 8 or not name:
            continue
        rows.append({"team": name, "played": num(cells[1].text), "won": num(cells[2].text), "drawn": num(cells[3].text),
                     "lost": num(cells[4].text), "diff": num(cells[6].text), "points": num(cells[7].text)})
    return rows


def football():
    year = datetime.now(timezone.utc).year
    out = []
    for name, label in KSI_LEAGUES:
        result = None
        for season in (year, year - 1):          # this season once it has started, else last season's final table
            ids = ksi_competition_ids(name, season)
            if "upper" in ids and "lower" in ids:  # after the split: top six, then bottom six
                upper, lower = ksi_rows(ids["upper"]), ksi_rows(ids["lower"])
                rows, main_id = upper + lower, ids["upper"]
            elif "main" in ids:
                rows, main_id = ksi_rows(ids["main"]), ids["main"]
            else:
                continue
            if rows and any(r["played"] for r in rows):
                done = season < year or datetime.now(timezone.utc).month >= 11
                result = table(name, label, f"https://www.ksi.is/oll-mot/mot?id={main_id}", rows,
                               note=f"{season} final table" if done else f"{season} season")
                break
        print(f"KSÍ {name}: {len(result['rows']) if result else 'not found'}")
        if result:
            out.append(result)
    return out


# ------------------------------------------------------------------ HSÍ handball
def handball():
    feed = requests.get("https://www.hsi.is/api/hsi/matches/M/upcoming?months=6", headers={"User-Agent": USER_AGENT}, timeout=45).json()
    ids = {}
    for m in feed.get("data", []):
        ids.setdefault((m.get("TOURNAMENT_SHORT_NAME") or "").strip().lower(), m.get("TOURNAMENT_ID"))
    out = []
    for name, label in HSI_LEAGUES:
        tid = ids.get(name.lower())
        if not tid:
            print(f"HSÍ {name}: no upcoming matches, table not found")
            continue
        time.sleep(DELAY)
        data = requests.get(f"https://www.hsi.is/api/hsi/tournaments/{tid}/standings",
                            headers={"User-Agent": USER_AGENT}, timeout=45).json()
        teams = ((data.get("data") or {}).get("STANDINGS") or {}).get("TEAM") or []
        if isinstance(teams, dict):
            teams = [teams]
        rows = []
        for t in teams:
            played, drawn, lost = num(t.get("MATCHES")), num(t.get("TIE")), num(t.get("LOSE"))
            rows.append({"team": (t.get("TEAM_SHORT_NAME") or "").strip(), "played": played,
                         "won": played - drawn - lost, "drawn": drawn, "lost": lost,
                         "diff": num(t.get("GOALS_DIFFERENCE")), "points": num(t.get("POINTS"))})
        print(f"HSÍ {name}: {len(rows)} teams")
        if rows:
            out.append(table(name, label, f"https://www.hsi.is/tournament/{tid}", rows))
    return out


# ------------------------------------------------------------------ KKÍ basketball
KKI_TABLE_JS = """
() => { const tb = document.querySelector('table.mbt-complex'); if (!tb) return [];
  return [...tb.querySelectorAll('tr')].slice(1).map(r => {
    const c = [...r.children]; const a = c[1] && c[1].querySelector('a');
    return { team: a ? a.textContent.trim() : '', cells: c.map(x => x.textContent.trim()) }; }); }
"""


def basketball():
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="is-IS")
        try:
            for lid, name, label in KKI_LEAGUES:
                url = f"https://kki.is/motamal/leikir-og-urslit/motayfirlit/Stada?league_id={lid}"
                time.sleep(DELAY)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("table.mbt-complex tr", timeout=25000)
                    raw = page.evaluate(KKI_TABLE_JS)
                except Exception as e:
                    print(f"KKÍ {name}: table not read ({type(e).__name__})")
                    continue
                rows = []
                for r in raw:   # columns: Nr. | Lið | Leikir | Unnir | Töp | Stig | Stig/Fen | +/-
                    c = r["cells"]
                    if r["team"] and len(c) >= 8:
                        rows.append({"team": r["team"], "played": num(c[2]), "won": num(c[3]), "drawn": 0,
                                     "lost": num(c[4]), "diff": num(c[7]), "points": num(c[5])})
                print(f"KKÍ {name}: {len(rows)} teams")
                if rows:
                    out.append(table(name, label, url, rows))
        finally:
            browser.close()
    return out


# ------------------------------------------------------------------ BLÍ volleyball
BLI = "https://bli-web.dataproject.com/"


def volleyball():
    menu = get(BLI + "CompetitionHome.aspx?ID=142") or ""
    comps = {}
    for a in BeautifulSoup(menu, "html.parser").select("a[href*='CompetitionHome.aspx?ID=']"):
        text = a.get_text(" ", strip=True)
        season = re.search(r"(\d{4})\s*-\s*(\d{4})", text)
        if season:
            cid = re.search(r"ID=(\d+)", a["href"]).group(1)
            comps.setdefault(re.sub(r"\s*\d{4}\s*-\s*\d{4}.*", "", text).strip().lower(), []).append((int(season.group(2)), cid))
    out = []
    for name, label in BLI_LEAGUES:
        found = sorted(comps.get(name.lower(), []), reverse=True)
        if not found:
            print(f"BLÍ {name}: competition not found")
            continue
        cid = found[0][1]
        home = get(f"{BLI}CompetitionHome.aspx?ID={cid}") or ""
        pids = sorted({int(p) for p in re.findall(rf"CompetitionStandings\.aspx\?ID={cid}&(?:amp;)?PID=(\d+)", home)}, reverse=True)
        rows, url = [], None
        for pid in pids:                          # newest phase that has a real table (skips the one-match Super Cup)
            url = f"{BLI}CompetitionStandings.aspx?ID={cid}&PID={pid}"
            soup = BeautifulSoup(get(url) or "", "html.parser")
            rows = []
            for tr in soup.select("tr[id^='__']"):
                def val(i):
                    el = tr.find(id=i)
                    return el.get_text(strip=True) if el else ""
                if val("TeamName"):
                    won, lost = num(val("WonMatches")), num(val("LostMatches"))
                    rows.append({"team": val("TeamName"), "played": num(val("MatchesPlayed")), "won": won, "drawn": 0,
                                 "lost": lost, "diff": num(val("SetsWon")) - num(val("SetsLost")), "points": num(val("Points"))})
            if len(rows) >= 3:
                break
        print(f"BLÍ {name}: {len(rows)} teams")
        if len(rows) >= 3:
            out.append(table(name, label, url, rows))
    return out


# ------------------------------------------------------------------ main
def main():
    try:
        previous = json.loads(OUTPUT_FILE.read_text())["sports"] if OUTPUT_FILE.exists() else {}
    except Exception:
        previous = {}
    sports = {}
    for sport, collect in [("Football", football), ("Handball", handball),
                           ("Basketball", basketball), ("Volleyball", volleyball)]:
        try:
            tables = collect()
        except Exception as e:                    # one site's trouble shouldn't stop the others
            print(f"{sport}: error {e!r}")
            tables = []
        have = {t["league"] for t in tables}
        kept = [t for t in previous.get(sport, []) if t["league"] not in have]   # yesterday's copy of any table not read today
        if kept:
            print(f"{sport}: keeping yesterday's {', '.join(t['league'] for t in kept)}")
        order = [n for n, *_ in {"Football": KSI_LEAGUES, "Handball": HSI_LEAGUES, "Volleyball": BLI_LEAGUES}.get(sport, [])] \
            or [n for _, n, _ in KKI_LEAGUES]
        all_tables = tables + kept
        all_tables.sort(key=lambda t: order.index(t["league"]) if t["league"] in order else 99)
        if all_tables:
            sports[sport] = all_tables
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "sports": sports},
                                      ensure_ascii=False, indent=1))
    print(f"Wrote tables for {', '.join(sports) or 'no sports'} to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
