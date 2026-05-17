#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor, as_completed
import json, urllib.request, datetime, re, os, sqlite3, time

NOW        = datetime.datetime.now(datetime.timezone.utc)
CUTOFF_7D  = NOW - datetime.timedelta(days=7)
TODAY      = datetime.date.today().isoformat()

PM_KEYWORDS = [
    "product manager", "staff pm", "principal pm", "group product manager",
    "senior product manager", "head of product", "staff product manager",
    "principal product manager",
]

NON_US = [
    "emea", "europe", "european union", "eu only", "eu-only",
    " uk", "united kingdom", "u.k.", "london", "manchester", "edinburgh", "scotland", "wales",
    "ireland", "dublin",
    "canada", "toronto", "vancouver", "montreal", "calgary", "ottawa", "alberta", "ontario", "quebec",
    "australia", "sydney", "melbourne", "brisbane", "perth", "new zealand", "auckland",
    "india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai", "gurgaon", "noida",
    "singapore", "japan", "tokyo", "osaka", "china", "shanghai", "beijing", "shenzhen", "hong kong",
    "korea", "seoul", "philippines", "manila", "vietnam", "hanoi", "indonesia", "jakarta",
    "malaysia", "kuala lumpur", "thailand", "bangkok", "taiwan", "taipei",
    "brazil", "são paulo", "sao paulo", "rio de janeiro",
    "latam", "latin america", "mexico", "mexico city", "argentina", "buenos aires",
    "chile", "santiago", "colombia", "bogotá", "bogota", "peru", "lima", "costa rica",
    "germany", "berlin", "munich", "hamburg", "frankfurt",
    "france", "paris", "lyon", "marseille",
    "spain", "madrid", "barcelona", "valencia",
    "italy", "rome", "milan", "turin",
    "netherlands", "amsterdam", "rotterdam", "the hague",
    "belgium", "brussels", "antwerp",
    "poland", "warsaw", "krakow",
    "portugal", "lisbon", "porto",
    "switzerland", "zurich", "geneva", "bern",
    "sweden", "stockholm", "gothenburg",
    "denmark", "copenhagen", "norway", "oslo", "finland", "helsinki",
    "austria", "vienna", "czech", "prague", "hungary", "budapest",
    "romania", "bucharest", "bulgaria", "sofia", "greece", "athens",
    "israel", "tel aviv", "jerusalem", "haifa",
    "dubai", "uae", "u.a.e.", "abu dhabi", "saudi arabia", "riyadh",
    "egypt", "cairo", "south africa", "cape town", "johannesburg", "nigeria", "lagos", "kenya", "nairobi",
    "russia", "moscow", "ukraine", "kyiv", "kiev", "turkey", "istanbul",
    "apac", "asia-pacific", "asia pacific",
]

# Explicit hybrid / onsite signals in the location text.
HYBRID_TERMS = ["hybrid", "on-site", "onsite", "on site", "in-office", "in office", "in-person", "in person"]

# Phrases in the description text that explicitly confirm fully remote.
REMOTE_DESC_PHRASES = [
    "fully remote", "100% remote", "100 percent remote", "fully-remote",
    "remote-first", "remote first",
    "work from anywhere", "work-from-anywhere",
    "remote position", "remote role", "remote opportunity", "remote employee",
    "this role is remote", "this position is remote",
    "this is a remote role", "this is a remote position", "this is a fully remote",
    "us remote", "us-remote", "remote within the us", "remote in the us",
    "remote within the united states", "remote (us", "remote, us",
    "remote — us", "remote – us",
    "work remotely", "work from home permanently",
]

# Phrases in the description text that disqualify (clearly NOT fully remote).
HYBRID_DESC_PHRASES = [
    "hybrid role", "hybrid position", "hybrid work", "hybrid schedule", "hybrid arrangement",
    "hybrid model", "this is a hybrid",
    "days in the office", "days in office", "days per week in the office",
    "days a week in the office", "days/week in office", "days a week in office",
    "days per week in office", "days in our office",
    "in-office days", "in office days",
    "must be willing to work from our",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def is_pm(title):
    return any(k in (title or "").lower() for k in PM_KEYWORDS)


def passes_scrape_filter(loc, workplace_type=""):
    """Cheap pre-enrichment filter — drops clearly non-US or clearly hybrid jobs.

    The full fully-remote check runs after enrichment (once we have description text).
    """
    l = (loc or "").lower()
    wt = (workplace_type or "").lower()
    if any(t in l for t in NON_US): return False
    if any(t in l for t in HYBRID_TERMS): return False
    if wt in ("hybrid", "onsite", "on-site", "on_site", "office", "in-office", "in_office"): return False
    return True


def is_us_fully_remote(loc, desc, ats_remote_flag=False, workplace_type=""):
    """Strict post-enrichment check — the job must explicitly be fully remote and US-eligible.

    Accept only if at least ONE of:
      - ATS reports remote (Ashby isRemote=true or workplaceType="Remote")
      - Location text contains 'remote' / 'anywhere' / 'work from home'
      - Description contains an explicit fully-remote phrase (see REMOTE_DESC_PHRASES)

    Reject (regardless of accept signals) if:
      - Location matches a non-US term
      - Location text or workplaceType says hybrid / onsite / in-office
      - Description contains an explicit hybrid phrase (see HYBRID_DESC_PHRASES)
    """
    l  = (loc or "").lower()
    d  = (desc or "").lower()
    wt = (workplace_type or "").lower()

    # Hard rejects
    if any(t in l for t in NON_US):                                return False
    if any(t in l for t in HYBRID_TERMS):                          return False
    if wt in ("hybrid", "onsite", "on-site", "on_site", "office",
              "in-office", "in_office"):                            return False
    if any(p in d for p in HYBRID_DESC_PHRASES):                   return False

    # Positive signals
    if ats_remote_flag or wt == "remote":                          return True
    if any(t in l for t in ["remote", "anywhere", "work from home", "wfh"]):
        return True
    if any(p in d for p in REMOTE_DESC_PHRASES):                   return True

    # No remote signal anywhere — reject (covers bare "United States", empty location, US cities)
    return False

def parse_dt(s):
    if not s: return None
    try:
        s = re.sub(r'(\+\d{2}):?(\d{2})$', r'+\1:\2', s.rstrip("Z") + ("Z" if s.endswith("Z") else ""))
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def is_recent(posted_at_str):
    dt = parse_dt(posted_at_str)
    if dt is None: return True
    return dt >= CUTOFF_7D

def strip_html(html):
    text = re.sub(r'<[^>]+>', ' ', html or '')
    for ent, ch in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&nbsp;',' '),('&#39;',"'"),('&quot;','"')]:
        text = text.replace(ent, ch)
    return re.sub(r'\s+', ' ', text).strip()

SALARY_RE = re.compile(r'\$([\d]+)\s*[kK]?\s*[-–—]+\s*\$?([\d]+)\s*[kK]?', re.I)

def extract_salary(text):
    if not text: return {"listed": False}
    m = SALARY_RE.search(text.replace(',', ''))
    if not m: return {"listed": False}
    lo, hi = int(m.group(1)), int(m.group(2))
    if lo < 1000: lo *= 1000
    if hi < 1000: hi *= 1000
    if lo < 50000 or hi < 50000: return {"listed": False}
    return {"min": min(lo, hi), "max": max(lo, hi), "currency": "USD", "listed": True}

def make_synopsis(text):
    if not text: return ""
    text = strip_html(text)
    sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.split()) > 6]
    markers = ["we are looking", "we're looking", "we're hiring", "as a ", "you will",
               "in this role", "this role", "seeking a", "this position"]
    for i, s in enumerate(sents):
        if any(m in s.lower() for m in markers):
            return ' '.join(sents[i:i+2])
    return ' '.join(sents[1:3]) if len(sents) >= 3 else text[:300]


# Load company token lists
with open(os.path.join(SCRIPT_DIR, "data/greenhouse_companies.json")) as f:
    GH_TOKENS = json.load(f)
with open(os.path.join(SCRIPT_DIR, "data/ashby_companies.json")) as f:
    ASHBY_TOKENS = json.load(f)
with open(os.path.join(SCRIPT_DIR, "data/lever_companies.json")) as f:
    LEVER_TOKENS = json.load(f)


def scrape_greenhouse(token):
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        jobs = []
        for job in data.get("jobs", []):
            title = job.get("title", "")
            loc = (job.get("location") or {}).get("name", "")
            if not is_pm(title): continue
            if not is_recent(job.get("updated_at")): continue
            if not passes_scrape_filter(loc): continue
            jobs.append({
                "id": f"gh-{job['id']}",
                "title": title,
                "company": token,
                "location": loc,
                "url": job.get("absolute_url", ""),
                "apply_url": job.get("absolute_url", ""),
                "posted_at": job.get("updated_at", ""),
                "compensation": {"listed": False},
                "source": "greenhouse",
                "source_token": token,
                "_ats_remote_flag": False,
                "_ats_workplace_type": "",
            })
        return jobs, None
    except Exception as e:
        return [], str(e)

def scrape_ashby(token):
    try:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        jobs = []
        for job in data.get("jobs", []):
            title = job.get("title", "")
            loc = job.get("location", "")
            is_remote = job.get("isRemote", False)
            wtype = job.get("workplaceType", "")
            if not is_pm(title): continue
            if not is_recent(job.get("publishedAt")): continue
            if not passes_scrape_filter(loc, wtype): continue
            jobs.append({
                "id": f"ab-{job['id']}",
                "title": title,
                "company": token,
                "location": loc,
                "url": job.get("jobUrl", ""),
                "apply_url": job.get("applyUrl", ""),
                "posted_at": job.get("publishedAt", ""),
                "compensation": {"listed": False},
                "source": "ashby",
                "source_token": token,
                "_ats_remote_flag": bool(is_remote),
                "_ats_workplace_type": wtype or "",
            })
        return jobs, None
    except Exception as e:
        return [], str(e)

def scrape_lever(token):
    try:
        url = f"https://api.lever.co/v0/postings/{token}?mode=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            postings = json.loads(r.read().decode())
        jobs = []
        for posting in (postings if isinstance(postings, list) else []):
            title = posting.get("text", "")
            loc = (posting.get("categories") or {}).get("location", "")
            wtype = posting.get("workplaceType", "") or ""
            if not is_pm(title): continue
            created_ms = posting.get("createdAt")
            posted_at = ""
            if created_ms:
                dt = datetime.datetime.fromtimestamp(created_ms / 1000, tz=datetime.timezone.utc)
                posted_at = dt.isoformat()
            if not is_recent(posted_at): continue
            if not passes_scrape_filter(loc, wtype): continue
            raw_desc = strip_html(posting.get("descriptionPlain") or posting.get("description", ""))[:5000]
            jobs.append({
                "id": f"lv-{posting['id']}",
                "title": title,
                "company": token,
                "location": loc,
                "url": posting.get("hostedUrl", ""),
                "apply_url": posting.get("applyUrl", ""),
                "posted_at": posted_at,
                "compensation": {"listed": False},
                "source": "lever",
                "source_token": token,
                "_ats_remote_flag": wtype.lower() == "remote",
                "_ats_workplace_type": wtype,
                "_raw_desc": raw_desc,
            })
        return jobs, None
    except Exception as e:
        return [], str(e)


def fetch_description(source, token, job_id):
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


def source_status(jobs_count, errors, total_tokens):
    """Classify a source's health for status reporting."""
    if errors == 0:
        return "ok"
    if jobs_count == 0:
        return "failed"
    return "degraded" if (errors / max(total_tokens, 1)) >= 0.20 else "ok"


# ── Scrape ──────────────────────────────────────────────────────────────────
print(f"Scraping {TODAY}...")

MAX_SCRAPE_WORKERS = int(os.environ.get("SCRAPE_WORKERS", "80"))

t0 = time.time()
gh_jobs, gh_errors, gh_err_samples = [], 0, []
with ThreadPoolExecutor(max_workers=MAX_SCRAPE_WORKERS) as ex:
    futs = {ex.submit(scrape_greenhouse, t): t for t in GH_TOKENS}
    for fut in as_completed(futs):
        token = futs[fut]
        jobs, err = fut.result()
        gh_jobs.extend(jobs)
        if err:
            gh_errors += 1
            if len(gh_err_samples) < 3:
                gh_err_samples.append(f"{token}: {err[:120]}")
gh_elapsed = round(time.time() - t0, 1)

t0 = time.time()
ab_jobs, ab_errors, ab_err_samples = [], 0, []
with ThreadPoolExecutor(max_workers=MAX_SCRAPE_WORKERS) as ex:
    futs = {ex.submit(scrape_ashby, t): t for t in ASHBY_TOKENS}
    for fut in as_completed(futs):
        token = futs[fut]
        jobs, err = fut.result()
        ab_jobs.extend(jobs)
        if err:
            ab_errors += 1
            if len(ab_err_samples) < 3:
                ab_err_samples.append(f"{token}: {err[:120]}")
ab_elapsed = round(time.time() - t0, 1)

t0 = time.time()
lv_jobs, lv_errors, lv_err_samples = [], 0, []
with ThreadPoolExecutor(max_workers=MAX_SCRAPE_WORKERS) as ex:
    futs = {ex.submit(scrape_lever, t): t for t in LEVER_TOKENS}
    for fut in as_completed(futs):
        token = futs[fut]
        jobs, err = fut.result()
        lv_jobs.extend(jobs)
        if err:
            lv_errors += 1
            if len(lv_err_samples) < 3:
                lv_err_samples.append(f"{token}: {err[:120]}")
lv_elapsed = round(time.time() - t0, 1)

all_jobs = gh_jobs + ab_jobs + lv_jobs


# ── Enrich ──────────────────────────────────────────────────────────────────
print(f"Enriching {len(all_jobs)} matched jobs with descriptions...")
t0 = time.time()
with ThreadPoolExecutor(max_workers=MAX_SCRAPE_WORKERS) as ex:
    all_jobs = list(ex.map(enrich, all_jobs))
enrich_elapsed = round(time.time() - t0, 1)


# ── Strict fully-remote filter ──────────────────────────────────────────────
# Apply the full check now that description text is available. Anything that
# doesn't have an explicit remote signal in location, ATS flags, or description
# gets dropped.
pre_remote = len(all_jobs)
kept = []
for job in all_jobs:
    ats_remote = job.pop("_ats_remote_flag", False)
    ats_wtype  = job.pop("_ats_workplace_type", "")
    if is_us_fully_remote(job.get("location", ""), job.get("description", ""), ats_remote, ats_wtype):
        kept.append(job)
all_jobs = kept
remote_dropped = pre_remote - len(all_jobs)


# ── Dedup ───────────────────────────────────────────────────────────────────
seen_urls = set()
deduped = []
for job in all_jobs:
    key = re.sub(r'[?#].*$', '', job["url"].lower().rstrip("/"))
    if key not in seen_urls:
        seen_urls.add(key)
        deduped.append(job)
dups = len(all_jobs) - len(deduped)
all_jobs = deduped

salary_count = sum(1 for j in all_jobs if j.get("compensation", {}).get("listed"))
all_jobs = [j for j in all_jobs if not (j.get("compensation", {}).get("listed") and j["compensation"].get("max", 0) < 175000)]


# ── Persist ─────────────────────────────────────────────────────────────────
db_dir = os.path.join(SCRIPT_DIR, "db")
os.makedirs(db_dir, exist_ok=True)
con = sqlite3.connect(os.path.join(db_dir, "jobs.db"))
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
for col in ("description TEXT", "synopsis TEXT"):
    try:
        con.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass
con.commit()

for job in all_jobs:
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

deleted = con.execute(
    "DELETE FROM jobs WHERE expires_at < ?", (NOW.isoformat(),)
).rowcount
con.commit()


# ── Build output rows ────────────────────────────────────────────────────────
COLS = ["id","title","company","location","url","apply_url","posted_at",
        "source","source_token","comp_listed","comp_min","comp_max",
        "description","synopsis","first_seen_at","expires_at"]

def row_to_job(row):
    d = dict(zip(COLS, row))
    comp = {"listed": bool(d.pop("comp_listed"))}
    mn, mx = d.pop("comp_min"), d.pop("comp_max")
    if comp["listed"]:
        comp["min"], comp["max"], comp["currency"] = mn, mx, "USD"
    d["compensation"] = comp
    return d

all_active = [row_to_job(r) for r in con.execute(
    "SELECT * FROM jobs ORDER BY first_seen_at DESC").fetchall()]

new_jobs = [row_to_job(r) for r in con.execute(
    "SELECT * FROM jobs WHERE first_seen_at = ?", (NOW.isoformat(),)).fetchall()]

con.close()


# ── Write output ─────────────────────────────────────────────────────────────
out_dir = os.path.join(SCRIPT_DIR, "output")
os.makedirs(out_dir, exist_ok=True)

gh_status = source_status(len(gh_jobs), gh_errors, len(GH_TOKENS))
ab_status = source_status(len(ab_jobs), ab_errors, len(ASHBY_TOKENS))
lv_status = source_status(len(lv_jobs), lv_errors, len(LEVER_TOKENS))
all_statuses = [gh_status, ab_status, lv_status]
overall_status = "failed" if all(s == "failed" for s in all_statuses) else \
                 "degraded" if any(s in ("failed", "degraded") for s in all_statuses) else "ok"

sources_meta = {
    "greenhouse": {"companies_queried": len(GH_TOKENS), "jobs_kept": len(gh_jobs), "errors": gh_errors, "elapsed_s": gh_elapsed},
    "ashby":      {"companies_queried": len(ASHBY_TOKENS), "jobs_kept": len(ab_jobs), "errors": ab_errors, "elapsed_s": ab_elapsed},
    "lever":      {"companies_queried": len(LEVER_TOKENS), "jobs_kept": len(lv_jobs), "errors": lv_errors, "elapsed_s": lv_elapsed},
}

raw_path = os.path.join(out_dir, f"jobs_raw_{TODAY}.json")
with open(raw_path, "w") as f:
    json.dump({"scraped_at": NOW.isoformat(), "total_jobs": len(all_active), "sources": sources_meta, "jobs": all_active}, f, indent=2)

new_path = os.path.join(out_dir, f"jobs_new_{TODAY}.json")
with open(new_path, "w") as f:
    json.dump({"scraped_at": NOW.isoformat(), "total_jobs": len(new_jobs), "sources": sources_meta, "jobs": new_jobs}, f, indent=2)

status_path = os.path.join(out_dir, f"scraper_status_{TODAY}.json")
with open(status_path, "w") as f:
    json.dump({
        "run_at": NOW.isoformat(),
        "overall": overall_status,
        "sources": {
            "greenhouse": {"status": gh_status, "companies_queried": len(GH_TOKENS), "jobs_found": len(gh_jobs), "errors": gh_errors, "error_samples": gh_err_samples},
            "ashby":      {"status": ab_status, "companies_queried": len(ASHBY_TOKENS), "jobs_found": len(ab_jobs), "errors": ab_errors, "error_samples": ab_err_samples},
            "lever":      {"status": lv_status, "companies_queried": len(LEVER_TOKENS), "jobs_found": len(lv_jobs), "errors": lv_errors, "error_samples": lv_err_samples},
        },
    }, f, indent=2)


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nScraped {TODAY}  [overall: {overall_status}]")
print(f"  Greenhouse     : {len(GH_TOKENS)} queried, {len(gh_jobs)} new (≤7d), {gh_errors} errors  ({gh_elapsed}s)  [{gh_status}]")
print(f"  Ashby          : {len(ASHBY_TOKENS)} queried, {len(ab_jobs)} new (≤7d), {ab_errors} errors  ({ab_elapsed}s)  [{ab_status}]")
print(f"  Lever          : {len(LEVER_TOKENS)} queried, {len(lv_jobs)} new (≤7d), {lv_errors} errors  ({lv_elapsed}s)  [{lv_status}]")
print(f"  Descriptions   : {pre_remote} fetched ({enrich_elapsed}s)")
print(f"  Remote dropped : {remote_dropped} (no fully-remote signal in location, ATS flag, or description)")
print(f"  Salary found   : {salary_count} jobs")
print(f"  Duplicates     : {dups} removed")
print(f"  Expired (>30d) : {deleted} deleted from DB")
print(f"  Active in DB   : {len(all_active)} jobs")
print(f"  New today      : {len(new_jobs)} jobs")
print(f"  Written        : output/jobs_raw_{TODAY}.json")
print(f"  Written        : output/jobs_new_{TODAY}.json")
print(f"  Written        : output/scraper_status_{TODAY}.json")
