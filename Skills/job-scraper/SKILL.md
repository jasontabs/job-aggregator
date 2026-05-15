---
name: job-scraper
description: Scrapes tech job postings for Product Manager, Staff PM, and Principal PM roles from Greenhouse ATS, Ashby ATS, and Lever ATS across thousands of companies. Outputs a structured JSON file for downstream ranking and emailing. Use this whenever the user wants to find jobs, run the job scraper, refresh job listings, check for new PM openings, or aggregate job postings. Trigger for phrases like "find me jobs", "run the scraper", "check for new jobs", "what's out there", "scrape jobs", "look for PM jobs".
---

# Job Scraper

Fetches remote US Product Manager job postings from three ATS platforms — Greenhouse, Ashby, and Lever — querying the full list of known tech companies on each. Company token lists live in `data/` (sourced from [Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator), pre-filtered for tech companies). Writes output to `output/jobs_raw_{YYYY-MM-DD}.json`.

Read `references/schemas.md` for the exact output JSON schema.

## Implementation

Write and run a Python script. Use `ThreadPoolExecutor(max_workers=80)` for all three platforms — querying them sequentially (Greenhouse → Ashby → Lever) with full parallelism within each. Expected runtime: ~3 minutes total.

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import json, urllib.request, datetime, re, os, time

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
```

### Greenhouse

`GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs`

Token list: `data/greenhouse_companies.json` (~7,300 tokens)

Keep a job if `title` matches a PM keyword AND `location.name` passes `is_us_remote()`.

Extract: `id = "gh-{job.id}"`, `title`, `company = token`, `location = job.location.name`, `url = job.absolute_url`, `apply_url = job.absolute_url`, `posted_at = job.updated_at`, `source = "greenhouse"`, `source_token = token`.

For compensation: parse `$` amounts from `job.content` if fetching with `?content=true`; set `compensation.listed = false` if nothing found.

### Ashby

`GET https://api.ashbyhq.com/posting-api/job-board/{token}`

Token list: `data/ashby_companies.json` (~2,800 tokens). Response key is `jobs`.

Keep a job if `title` matches AND (`isRemote == true` OR `workplaceType == "Remote"`) AND location doesn't contain non-US terms.

Extract: `id = "ab-{job.id}"`, `title`, `company = token`, `location = job.location`, `url = job.jobUrl`, `apply_url = job.applyUrl`, `posted_at = job.publishedAt`, `source = "ashby"`.

### Lever

`GET https://api.lever.co/v0/postings/{token}?mode=json`

Token list: `data/lever_companies.json` (~4,100 tokens). Response is a JSON array.

Keep a job if `title` (field: `text`) matches AND `categories.location` or `categories.commitment` passes `is_us_remote()`.

Extract: `id = "lv-{posting.id}"`, `title = posting.text`, `company = token`, `location = posting.categories.location`, `url = posting.hostedUrl`, `apply_url = posting.applyUrl`, `posted_at` = ISO 8601 from `posting.createdAt` (Unix ms), `source = "lever"`.

### Deduplication

After collecting from all three platforms, deduplicate by normalized URL:
```python
key = re.sub(r'[?#].*$', '', job["url"].lower().rstrip("/"))
```
Keep first occurrence.

### Compensation Filtering

- **Exclude** jobs where `compensation.listed == True` AND `compensation.max < 175000`
- **Include** all jobs where `compensation.listed == False` (most jobs don't list salary)

### Output

Write `output/jobs_raw_{YYYY-MM-DD}.json`. See `references/schemas.md` for exact structure.

Print run summary:
```
Scraped {date}
  Greenhouse : {N} queried, {N} kept, {N} errors  ({elapsed}s)
  Ashby      : {N} queried, {N} kept, {N} errors  ({elapsed}s)
  Lever      : {N} queried, {N} kept, {N} errors  ({elapsed}s)
  Duplicates removed: {N}
  Total written: {N} jobs → output/jobs_raw_{date}.json
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
