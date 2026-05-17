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

# Non-US country / city tokens. Expand liberally — false-positive rejects are cheap.
NON_US = [
    "emea", "europe", "european union", " uk", "united kingdom", "london",
    "ireland", "dublin", "canada", "toronto", "vancouver",
    "australia", "sydney", "india", "bangalore", "mumbai", "singapore",
    "japan", "tokyo", "china", "hong kong", "philippines", "indonesia",
    "brazil", "latam", "mexico", "argentina",
    "germany", "berlin", "france", "paris", "spain", "madrid", "netherlands", "amsterdam",
    "poland", "warsaw", "portugal", "lisbon", "switzerland", "zurich",
    "sweden", "denmark", "norway", "finland", "israel", "tel aviv",
    "dubai", "uae", "russia", "ukraine", "turkey", "apac", "asia-pacific",
    # ... see run.py for the full list
]

# Explicit hybrid / onsite signals in the location text.
HYBRID_TERMS = ["hybrid", "on-site", "onsite", "on site", "in-office", "in office", "in-person", "in person"]

# Phrases in description text that explicitly confirm fully remote.
REMOTE_DESC_PHRASES = [
    "fully remote", "100% remote", "remote-first", "work from anywhere",
    "remote position", "remote role", "this role is remote",
    "us remote", "remote within the us", "work remotely",
    # ... see run.py for the full list
]

# Phrases in description text that disqualify (clearly NOT fully remote).
HYBRID_DESC_PHRASES = [
    "hybrid role", "hybrid position", "hybrid work", "hybrid schedule",
    "this is a hybrid", "days in the office", "days per week in the office",
    "must be willing to work from our",
    # ... see run.py for the full list
]

def is_pm(title):
    return any(k in (title or "").lower() for k in PM_KEYWORDS)

def passes_scrape_filter(loc, workplace_type=""):
    """Cheap pre-enrichment filter — drops clearly non-US or clearly hybrid jobs.
    The strict fully-remote check runs after enrichment when description text is available."""
    l = (loc or "").lower()
    wt = (workplace_type or "").lower()
    if any(t in l for t in NON_US): return False
    if any(t in l for t in HYBRID_TERMS): return False
    if wt in ("hybrid", "onsite", "on-site", "office", "in-office"): return False
    return True

def is_us_fully_remote(loc, desc, ats_remote_flag=False, workplace_type=""):
    """Strict post-enrichment check — must explicitly be fully remote AND US-eligible.

    Accept only if at least ONE positive signal AND no disqualifying signal:
      Positive: ATS remote flag, 'remote'/'anywhere' in location, or fully-remote phrase in description.
      Reject:   NON_US match, HYBRID_TERMS in location, hybrid workplaceType, or HYBRID_DESC_PHRASES in description.
    """
    l, d, wt = (loc or "").lower(), (desc or "").lower(), (workplace_type or "").lower()
    # Hard rejects
    if any(t in l for t in NON_US):                return False
    if any(t in l for t in HYBRID_TERMS):          return False
    if wt in ("hybrid", "onsite", "on-site", "office", "in-office"): return False
    if any(p in d for p in HYBRID_DESC_PHRASES):   return False
    # Positive signals
    if ats_remote_flag or wt == "remote":          return True
    if any(t in l for t in ["remote", "anywhere", "work from home", "wfh"]): return True
    if any(p in d for p in REMOTE_DESC_PHRASES):   return True
    return False

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

def strip_html(html):
    text = re.sub(r'<[^>]+>', ' ', html or '')
    for ent, ch in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&nbsp;',' '),('&#39;',"'"),('&quot;','"')]:
        text = text.replace(ent, ch)
    return re.sub(r'\s+', ' ', text).strip()

SALARY_RE = re.compile(r'\$([\d]+)\s*[kK]?\s*[-–—]+\s*\$?([\d]+)\s*[kK]?', re.I)

def extract_salary(text):
    """Parse annual salary range from plain text. Returns compensation dict."""
    if not text: return {"listed": False}
    m = SALARY_RE.search(text.replace(',', ''))
    if not m: return {"listed": False}
    lo, hi = int(m.group(1)), int(m.group(2))
    if lo < 1000: lo *= 1000
    if hi < 1000: hi *= 1000
    if lo < 50000 or hi < 50000: return {"listed": False}
    return {"min": min(lo, hi), "max": max(lo, hi), "currency": "USD", "listed": True}

def make_synopsis(text):
    """Extract 1-2 sentence role description from plain text."""
    if not text: return ""
    sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.split()) > 6]
    markers = ["we are looking", "we're looking", "we're hiring", "as a ", "you will",
               "in this role", "this role", "seeking a", "this position"]
    for i, s in enumerate(sents):
        if any(m in s.lower() for m in markers):
            return ' '.join(sents[i:i+2])
    return ' '.join(sents[1:3]) if len(sents) >= 3 else text[:300]
```

### Greenhouse

`GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs`

Token list: `data/greenhouse_companies.json` (~7,300 tokens)

Keep a job if `title` matches a PM keyword AND `is_recent(job.updated_at)` AND `passes_scrape_filter(location.name)`.

Extract: `id = "gh-{job.id}"`, `title`, `company = token`, `location = job.location.name`, `url = job.absolute_url`, `apply_url = job.absolute_url`, `posted_at = job.updated_at`, `source = "greenhouse"`, `source_token = token`. Greenhouse has no remote flag — set `_ats_remote_flag = False`, `_ats_workplace_type = ""`.

### Ashby

`GET https://api.ashbyhq.com/posting-api/job-board/{token}`

Token list: `data/ashby_companies.json` (~2,800 tokens). Response key is `jobs`.

Keep a job if `title` matches AND `is_recent(job.publishedAt)` AND `passes_scrape_filter(location, workplaceType)`. **Do NOT** filter by `isRemote` at scrape time — stash it for the post-enrichment check instead. This way an Ashby job with `isRemote == false` but a description that says "fully remote" still gets a fair look.

Extract: `id = "ab-{job.id}"`, `title`, `company = token`, `location = job.location`, `url = job.jobUrl`, `apply_url = job.applyUrl`, `posted_at = job.publishedAt`, `source = "ashby"`, `_ats_remote_flag = bool(job.isRemote)`, `_ats_workplace_type = job.workplaceType`.

### Lever

`GET https://api.lever.co/v0/postings/{token}?mode=json`

Token list: `data/lever_companies.json` (~4,100 tokens). Response is a JSON array.

Keep a job if `title` (field: `text`) matches AND `is_recent()` on `posting.createdAt` (convert Unix ms → ISO 8601 first) AND `passes_scrape_filter(location, workplaceType)`.

Extract: `id = "lv-{posting.id}"`, `title = posting.text`, `company = token`, `location = posting.categories.location`, `url = posting.hostedUrl`, `apply_url = posting.applyUrl`, `posted_at` = ISO 8601 from `posting.createdAt`, `source = "lever"`, `_ats_workplace_type = posting.workplaceType`, `_ats_remote_flag = (workplaceType.lower() == "remote")`.

Lever includes description inline — capture it during extraction (no extra API call needed):
`_raw_desc = strip_html(posting.get("descriptionPlain") or posting.get("description", ""))[:5000]`

### Description enrichment

After all three platforms are scraped and `all_jobs` is assembled, fetch full descriptions for Greenhouse and Ashby jobs in a second parallel pass. Lever descriptions are already inline (captured as `_raw_desc`).

```python
def fetch_description(source, token, job_id):
    """Fetch full job description HTML and return plain text. Returns '' on error."""
    try:
        if source == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"
        elif source == "ashby":
            url = f"https://api.ashbyhq.com/posting-api/job-board/{token}/posting/{job_id}"
        else:
            return ""
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode())
        html = d.get("content") or d.get("descriptionHtml") or d.get("description", "")
        return strip_html(html)[:5000]
    except Exception:
        return ""

def enrich(job):
    raw_id = job["id"].split("-", 1)[1]
    if job["source"] in ("greenhouse", "ashby"):
        text = fetch_description(job["source"], job["source_token"], raw_id)
    else:
        text = job.pop("_raw_desc", "")
    job["description"] = text
    job["synopsis"]    = make_synopsis(text)
    comp = extract_salary(text)
    if comp["listed"]:
        job["compensation"] = comp
    return job

print(f"Enriching {len(all_jobs)} matched jobs with descriptions...")
with ThreadPoolExecutor(max_workers=80) as ex:
    all_jobs = list(ex.map(enrich, all_jobs))
```

### Strict fully-remote filter

Now that description text is in hand, apply the full `is_us_fully_remote()` check. Drop anything that doesn't have an explicit remote signal — ATS flag, remote keyword in location, or fully-remote phrase in the description — and drop anything with a hybrid signal anywhere.

```python
kept = []
for job in all_jobs:
    ats_remote = job.pop("_ats_remote_flag", False)
    ats_wtype  = job.pop("_ats_workplace_type", "")
    if is_us_fully_remote(job.get("location", ""), job.get("description", ""), ats_remote, ats_wtype):
        kept.append(job)
remote_dropped = len(all_jobs) - len(kept)
all_jobs = kept
```

This is the safety net that catches jobs the cheap pre-filter let through — bare `"United States"` locations, empty locations, US cities without an explicit remote signal, and hybrid roles whose hybrid-ness only appears in the description.

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
        id            TEXT PRIMARY KEY,
        title         TEXT,
        company       TEXT,
        location      TEXT,
        url           TEXT,
        apply_url     TEXT,
        posted_at     TEXT,
        source        TEXT,
        source_token  TEXT,
        comp_listed   INTEGER DEFAULT 0,
        comp_min      INTEGER,
        comp_max      INTEGER,
        description   TEXT,
        synopsis      TEXT,
        first_seen_at TEXT NOT NULL,
        expires_at    TEXT NOT NULL
    )
""")
# Migrate existing DBs that predate description/synopsis columns
for col in ("description TEXT", "synopsis TEXT"):
    try:
        con.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass
con.commit()
```

**Upsert** each scraped job (insert, ignore if `id` already exists — don't overwrite `first_seen_at`):
```python
comp = job.get("compensation", {})
con.execute("""
    INSERT OR IGNORE INTO jobs
        (id, title, company, location, url, apply_url, posted_at,
         source, source_token, comp_listed, comp_min, comp_max,
         description, synopsis, first_seen_at, expires_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
""", (job["id"], job["title"], job["company"], job["location"],
      job["url"], job["apply_url"], job["posted_at"],
      job["source"], job["source_token"],
      int(comp.get("listed", False)), comp.get("min"), comp.get("max"),
      job.get("description", ""), job.get("synopsis", ""),
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

**Read** only jobs first seen during *this* run (the daily delta — what the ranker will score):
```python
new_rows = con.execute(
    "SELECT * FROM jobs WHERE first_seen_at = ?", (NOW.isoformat(),)
).fetchall()
```
Match on the exact `NOW.isoformat()` string, not a date range. Every row upserted in this run shares the same `first_seen_at`, and `INSERT OR IGNORE` preserves older timestamps on rows that already existed. Don't use `DATE(first_seen_at) = TODAY` — `first_seen_at` is UTC, so any run that crosses the UTC date boundary will silently report zero new jobs.

### Output

Write **two** files from the database rows (not just today's scrape):
- `output/jobs_raw_{YYYY-MM-DD}.json` — all jobs still within their 30-day window (full active set)
- `output/jobs_new_{YYYY-MM-DD}.json` — only jobs first seen in this run (the daily delta the ranker consumes)

See `references/schemas.md` for exact structure.

Print run summary:
```
Scraped {date}
  Greenhouse : {N} queried, {N} new (≤7d), {N} errors  ({elapsed}s)
  Ashby      : {N} queried, {N} new (≤7d), {N} errors  ({elapsed}s)
  Lever      : {N} queried, {N} new (≤7d), {N} errors  ({elapsed}s)
  Descriptions fetched: {N} ({elapsed}s)
  Remote dropped:      {N} (no fully-remote signal anywhere)
  Salary found:        {N} jobs
  Duplicates removed:  {N}
  Expired (>30d):      {N} deleted from DB
  Active in DB:        {N} jobs
  New today:           {N} jobs
  Written: output/jobs_raw_{date}.json
  Written: output/jobs_new_{date}.json
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
