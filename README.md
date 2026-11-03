# Iceland Events feed for Squarespace

Every morning a GitHub Action scrapes upcoming **music and arts events** from
**Tix.is** (Music, Theater and Festivals categories) and **Harpa.is**, saves them
to `docs/events.json`, and GitHub Pages serves that file publicly. A Code Block
on your Squarespace page fetches it and shows a filterable, month-by-month list.

```
Tix.is + Harpa.is  ──►  GitHub Action (daily)  ──►  docs/events.json on GitHub Pages  ──►  Squarespace Code Block
```

## Files

| File | What it does |
|---|---|
| `scrape_events.py` | The scraper. Settings (days ahead, categories, page cap) are at the top. |
| `requirements.txt` | Python packages it needs. |
| `.github/workflows/update-events.yml` | Runs the scraper daily at 05:17 and commits the new JSON. |
| `squarespace-embed.html` | Paste into a Squarespace Code Block. |

## Setup (about 15 minutes)

1. **Create a GitHub repo.** On github.com, click *New repository*, name it
   `iceland-events`, make it **Public** (free GitHub Pages needs public), and create it.
2. **Upload the files.** *Add file → Upload files*, drag in everything from this
   folder, including the `.github` folder, and commit. (If your browser hides the
   `.github` folder, create the file `.github/workflows/update-events.yml` through
   *Add file → Create new file* and paste its contents.)
3. **Allow the Action to save changes.** *Settings → Actions → General → Workflow
   permissions* → choose **Read and write permissions** → Save.
4. **Run it once.** *Actions* tab → *Update Iceland events* → **Run workflow**.
   After a minute or two a `docs/events.json` file appears in the repo. Open the
   run's log to see how many events each source returned.
5. **Turn on GitHub Pages.** *Settings → Pages* → Source: *Deploy from a branch*,
   Branch: `main`, folder: `/docs` → Save. Your feed will be at
   `https://<your-username>.github.io/iceland-events/events.json`.
6. **Add it to Squarespace.** Edit the page → add a **Code** block → paste all of
   `squarespace-embed.html` → change `FEED_URL` near the top of the script to
   your feed address → make sure the block's display is set to HTML. Save.
   (Code Blocks that run JavaScript need a Squarespace plan that allows custom
   code; on older plans this means Business or higher.)

From then on the list refreshes itself daily; nothing to do in Squarespace.

## Adjusting it

- **More or fewer categories:** edit `TIX_CATEGORIES` in `scrape_events.py`. The
  keys must match the menu labels on tix.is/en (e.g. add `"Family": "Family"`).
- **How far ahead:** `DAYS_AHEAD` (default 120).
- **Colours:** change `--ie-accent` at the top of the `<style>` in the embed.
- **If a site changes its layout** and returns nothing, the script keeps that
  source's previous events instead of wiping them, and the run log shows
  `-> keeping N previous ... events`. That's the signal the parser needs updating.

## Good manners

The scraper reads each site's `robots.txt`, waits a second between requests, and
fetches at most 150 event pages a day. It links every event back to the original
ticket page. If you plan to republish heavily or commercially, it's worth emailing
Tix (events@tix.is) and Harpa to ask; they may be happy to provide a feed.
