# xhs crawler

Local-only scraper for Xiaohongshu (小红书) notes — for competitive content analysis.

## What it scrapes

Per note: ID, URL, title, body, type (image/video), author info, publish time, IP location,
like/collect/comment/share counts, image URLs (downloaded locally), video URL, tags, @-mentions.
Per comment: ID, user, body, sub-comments, likes, IP location, attached images.

**Not scraped**: view count (`view_count` / 浏览量) — Xiaohongshu does not expose this on the web; only the note's author sees it in their creator dashboard.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

You also need Chrome installed (DrissionPage drives it). It will use a dedicated profile under `state/profile/` so it doesn't touch your normal browsing.

## First-run login

```powershell
python -m crawler.main login
```

This opens a Chrome window with the xhs login page. Scan the QR code with your purchased account's xhs app. Cookies persist under `state/profile/` for subsequent runs.

## Usage

```powershell
# A. Keyword search — collect notes from a list of keywords
python -m crawler.main search --keywords "穿搭,夏季,显瘦" --pages 20 --sort hot

# B. By creator
python -m crawler.main author --ids "5f...xx,6a...yy" --max-per-user 200

# C. Topic / tag page
python -m crawler.main topic --names "穿搭,通勤" --pages 20

# D. URL list (one note URL per line; URLs MUST include xsec_token)
python -m crawler.main urls --file targets.txt

# After discovery: scrape full details + comments for everything queued
python -m crawler.main detail --workers 1 --comment-pages 5

# Or run discover + detail in one shot
python -m crawler.main search --keywords "穿搭" --pages 10 --then-detail
```

## Data layout

- `data/xhs.db` — SQLite, the source of truth. Tables: `notes`, `comments`, `authors`, `images`, `discover_queue`, `crawl_log`.
- `data/images/<note_id>/<index>.jpg` — downloaded images.
- `data/exports/` — JSON/CSV dumps you create via the export command.

## Anti-ban behavior

- Random 5–15s delay between note requests, 30–60s between batches of 20.
- On HTTP 461 / risk-control prompt: pause 5min, then resume.
- Single-worker by default. Bumping `--workers` increases ban risk fast.
- Account is single-purpose; do not log into it in another browser during crawl.

## Known limits

- `xsec_token` is required for note detail. If you have raw URLs without tokens, discover them via search/author first.
- Comments deeper than the 2nd reply level get truncated by xhs's API; we capture parent + first level of replies.
- Topic discovery uses the topic page feed; trending order varies by request.
- View count is unavailable — see above.

## Re-running

Discovery is idempotent (UNIQUE on `note_id`). Detail scraping skips notes already marked
`status='done'` unless you pass `--force`. Failures stay in queue with `status='error'` and
a retry counter; pass `--retry-errors` to revisit them.
