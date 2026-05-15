---
name: job-scraper
description: Scrapes tech job postings for Product Manager, Staff PM, and Principal PM roles from Greenhouse ATS, Ashby ATS, and Lever ATS across thousands of companies. Outputs a structured JSON file for downstream ranking and emailing. Use this whenever the user wants to find jobs, run the job scraper, refresh job listings, check for new PM openings, or aggregate job postings. Trigger for phrases like "find me jobs", "run the scraper", "check for new jobs", "what's out there", "scrape jobs", "look for PM jobs".
---

# Job Scraper

Fetches remote US Product Manager job postings from three ATS platforms — Greenhouse, Ashby, and Lever — querying the full list of known tech companies on each. Company token lists live in `data/`. Results are persisted in a local SQLite database (`db/jobs.db`) with a 30-day TTL. Writes today's active jobs to `output/jobs_raw_{YYYY-MM-DD}.json`.

Read `references/schemas.md` for the exact output JSON schema.

## Implementation

Write and run a Python script. Use `ThreadPoolExecutor(max_workers=80)` for all three platforms. Expected runtime: ~3 minutes.

### Shared helpers

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import json, urllib.request, datetime, re, os, sqlite3, time

NOW        = datetime.datetime.now(datetime.timezone.utc)
CUTOFF_7D  = NOW - datetime.timedelta(days=7)   # only accept jobs posted within 7 days
CUTOFF_30D = NOW - datetime.timedelta(days=30)  # expire jobs older than 30 days

PM_KEYWORDS = [
    "product manager", "staff pm", "principal pm", "group product manager",
    "senior product manager", "head of product", "staff product manager",
    "principal product manager",
]
NON_US = ["emea", "europe", " uk", "london", "canada", "australia", "asia", "india", "brazil", "latam"]

def is_pm(title):
    return any(k in (title or "").lower() for k in PM_KEYWORDS)

def is_us_remote(loc, is_remote=False, wtype=""):
    l = (loc or "").lower()
    if any(t in l for t in NON_US): return False
    if is_remote or (wtype or "").lower() == "remote": return True
    return any(t in l for t in ["remote", "anywhere", "united states", "usa", "north america"]) or not l

def parse_dt(s):
    """Parse ISO 8601 string to aware datetime, or None if unparseable."""
    if not s: return None
    try:
        s = re.sub(r'(\+\d{2}):?(\d{2})$', r'+\1:\2', s.rstrip("Z") + ("Z" if s.endswith("Z") else ""))
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def is_recent(posted_at_str):
    """True if the job was posted within the last 7 days. Include if date is unknown."""
    dt = parse_dt(posted_at_str)
    if dt is None: return True   # no date info — don't discard
    return dt >= CUTOFF_7D
```

### Greenhouse

`GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs`

Token list: `data/greenhouse_companies.json` (~7,300 tokens)

Keep a job if `title` matches a PM keyword AND `location.name` passes `is_us_remote()` AND `is_recent(job.updated_at)`.

Extract: `id = "gh-{job.id}"`, `title`, `company = token`, `location = job.location.name`, `url = job.absolute_url`, `apply_url = job.absolute_url`, `posted_at = job.updated_at`, `source = "greenhouse"`, `source_token = token`.

### Ashby

`GET https://api.ashbyhq.com/posting-api/job-board/{token}`

Token list: `data/ashby_companies.json` (~2,800 tokens). Response key is `jobs`.

Keep a job if `title` matches AND (`isRemote == true` OR `workplaceType == "Remote"`) AND location doesn't contain non-US terms AND `is_recent(job.publishedAt)`.

Extract: `id = "ab-{job.id}"`, `title`, `company = token`, `location = job.location`, `url = job.jobUrl`, `apply_url = job.applyUrl`, `posted_at = job.publishedAt`, `source = "ashby"`.

### Lever

`GET https://api.lever.co/v0/postings/{token}?mode=json`

Token list: `data/lever_companies.json` (~4,100 tokens). Response is a JSON array.

Keep a job if `title` (field: `text`) matches AND location passes `is_us_remote()` AND `is_recent()` on `posting.createdAt` (convert Unix ms → ISO 8601 first).

Extract: `id = "lv-{posting.id}"`, `title = posting.text`, `company = token`, `location = posting.categories.location`, `url = posting.hostedUrl`, `apply_url = posting.applyUrl`, `posted_at` = ISO 8601 from `posting.createdAt`, `source = "lever"`.

### Deduplication

Deduplicate scraped results by normalized URL before inserting into the database:
```python
key = re.sub(r'[?#].*$', '', job["url"].lower().rstrip("/"))
```

### Compensation filtering

- **Exclude** jobs where `compensation.listed == True` AND `compensation.max < 175000`
- **Include** all jobs where `compensation.listed == False`

### Persistence (SQLite)

Database path: `db/jobs.db`. Create the directory and table if they don't exist.

```python
os.makedirs("db", exist_ok=True)
con = sqlite3.connect("db/jobs.db")
con.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id           TEXT PRIMARY KEY,
        title        TEXT,
        company      TEXT,
        location     TEXT,
        url          TEXT,
        apply_url    TEXT,
        posted_at    TEXT,
        source       TEXT,
        source_token TEXT,
        comp_listed  INTEGER DEFAULT 0,
        comp_min     INTEGER,
        comp_max     INTEGER,
        first_seen_at TEXT NOT NULL,
        expires_at    TEXT NOT NULL
    )
""")
con.commit()
```

**Upsert** each scraped job (insert, ignore if `id` already exists — don't overwrite `first_seen_at`):
```python
con.execute("""
    INSERT OR IGNORE INTO jobs
        (id, title, company, location, url, apply_url, posted_at,
         source, source_token, first_seen_at, expires_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
""", (job["id"], job["title"], job["company"], job["location"],
      job["url"], job["apply_url"], job["posted_at"],
      job["source"], job["source_token"],
      NOW.isoformat(), (NOW + datetime.timedelta(days=30)).isoformat()))
```

**Expire** jobs whose 30-day window has closed:
```python
deleted = con.execute(
    "DELETE FROM jobs WHERE expires_at < ?", (NOW.isoformat(),)
).rowcount
con.commit()
```

**Read** all surviving jobs for the output file:
```python
rows = con.execute("SELECT * FROM jobs ORDER BY first_seen_at DESC").fetchall()
```

### Output

Write `output/jobs_raw_{YYYY-MM-DD}.json` from the database rows (not just today's scrape — all jobs still within their 30-day window). See `references/schemas.md` for exact structure.

Print run summary:
```
Scraped {date}
  Greenhouse : {N} queried, {N} new (≤7d), {N} errors  ({elapsed}s)
  Ashby      : {N} queried, {N} new (≤7d), {N} errors  ({elapsed}s)
  Lever      : {N} queried, {N} new (≤7d), {N} errors  ({elapsed}s)
  Duplicates removed:  {N}
  Expired (>30d):      {N} deleted from DB
  Active in DB:        {N} jobs
  Written: output/jobs_raw_{date}.json
```

## Refreshing the Company Lists

The token lists in `data/` were downloaded from [github.com/Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator). To refresh them (e.g., monthly):

```python
import urllib.request, json, re, os

NON_TECH = [
    "health","medical","clinic","hospital","care","therapy","therapist",
    "nurse","nursing","dental","pharma","rehab","wellness","behavioral",
    "physician","surgery","radiology","urgent","staffing","recruiting",
    "recruiter","placement","workforce","manpower","outsourc",
    "university","college","school","district","county","state","gov",
    "nonprofit","foundation","church","ministry","restaurant","hotel",
    "hospitality","retail","grocery","food","bakery","catering","salon",
    "spa","fitness","gym","realty","realestate","mortgage","insurance",
    "broker","dealership","sandbox","test","demo","example","fellows",
    "gameday","course",
]

for platform in ["greenhouse", "ashby", "lever"]:
    url = f"https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/{platform}_companies.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        all_tokens = json.loads(r.read().decode())
    filtered = [t for t in all_tokens
                if not any(kw in t.lower() for kw in NON_TECH)
                and len(t) <= 40
                and not re.match(r'^\d+$', t)]
    with open(f"data/{platform}_companies.json", "w") as f:
        json.dump(sorted(filtered), f, indent=2)
    print(f"{platform}: {len(all_tokens)} → {len(filtered)}")
```
