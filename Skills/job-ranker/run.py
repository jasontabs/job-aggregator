#!/usr/bin/env python3
import anthropic, json, os, glob, datetime, re, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

THRESHOLD   = 0.60
MAX_WORKERS = 10
MODEL       = "claude-sonnet-4-6"
TODAY       = datetime.date.today().isoformat()
COMP_FLOOR  = 175_000

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

HYBRID_TERMS = ["hybrid", "on-site", "onsite", "on site", "in-office", "in office", "in-person", "in person"]

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


def passes_hard_filter(job):
    """Strict pre-scoring filter. Drops anything that isn't a target PM title,
    isn't explicitly fully remote, or has a listed salary below the floor."""
    title = (job.get("title") or "").lower()
    loc   = (job.get("location") or "").lower()
    desc  = (job.get("description") or "").lower()
    comp  = job.get("compensation", {})

    # Title must match a target PM keyword.
    if not any(k in title for k in PM_KEYWORDS):
        return False

    # Salary floor.
    if comp.get("listed") and comp.get("max", 0) < COMP_FLOOR:
        return False

    # Hard rejects on location/description.
    if any(t in loc for t in NON_US):                return False
    if any(t in loc for t in HYBRID_TERMS):          return False
    if any(p in desc for p in HYBRID_DESC_PHRASES):  return False

    # Need an explicit fully-remote signal somewhere.
    if any(t in loc for t in ["remote", "anywhere", "work from home", "wfh"]):
        return True
    if any(p in desc for p in REMOTE_DESC_PHRASES):
        return True

    return False


with open(os.path.join(SCRIPT_DIR, "references/profile.md")) as f:
    PROFILE = f.read()

SYSTEM_PROMPT = f"""You are a job match evaluator. Score how well a job posting matches this candidate's profile.

{PROFILE}

Return ONLY a JSON object — no explanation, no markdown:
{{"score": <integer 0-100>, "reasons": ["...", "..."], "gaps": ["..."]}}

- "score": integer 0–100
- "reasons": 2–3 short phrases on why it's a good fit (omit key if score < 40)
- "gaps": 1–2 short phrases on weaker fit factors (omit key if score >= 80)
"""

def score_job(client, job):
    title    = job.get("title", "")
    company  = job.get("company", "")
    location = job.get("location", "")
    synopsis = job.get("synopsis", "")
    comp     = job.get("compensation", {})
    comp_str = ""
    if comp.get("listed"):
        comp_str = f"\nSalary: ${comp.get('min', 0):,}–${comp.get('max', 0):,}/yr"

    prompt = f"Title: {title}\nCompany: {company}\nLocation: {location}{comp_str}"
    if synopsis:
        prompt += f"\nRole: {synopsis}"

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=250,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        parsed = json.loads(m.group() if m else raw)
        return {
            **job,
            "match_score":   round(parsed["score"] / 100, 2),
            "match_reasons": parsed.get("reasons", []),
            "match_gaps":    parsed.get("gaps", []),
        }
    except Exception as e:
        return {**job, "match_error": str(e)}


# ── Status file helpers ──────────────────────────────────────────────────────
out_dir      = os.path.join(SCRIPT_DIR, "output")
os.makedirs(out_dir, exist_ok=True)
status_path  = os.path.join(out_dir, f"ranker_status_{TODAY}.json")
_status      = {"run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "status": "ok", "error": None,
                "jobs_evaluated": 0, "jobs_passed": 0}

def _write_status():
    with open(status_path, "w") as _f:
        json.dump(_status, _f, indent=2)

try:
    # ── Load input — prefer today's new-only file ─────────────────────────────
    scraper_output = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "job-scraper", "output"))
    new_file = os.path.join(scraper_output, f"jobs_new_{TODAY}.json")
    raw_files = sorted(glob.glob(os.path.join(scraper_output, "jobs_raw_*.json")))

    if os.path.exists(new_file):
        source_file = new_file
        mode = "new-only"
    elif raw_files:
        source_file = raw_files[-1]
        mode = "full"
    else:
        raise FileNotFoundError("No jobs files found — run the job-scraper first.")

    with open(source_file) as f:
        data = json.load(f)
    jobs = data["jobs"]

    if not jobs:
        print(f"No new jobs today ({os.path.basename(source_file)}). Nothing to rank.")
        _status["status"] = "no_new_jobs"
        _write_status()
        raise SystemExit(0)

    print(f"Ranking {len(jobs)} jobs from {os.path.basename(source_file)} (mode: {mode})...")

    # ── Hard pre-filter ───────────────────────────────────────────────────────
    qualified    = [j for j in jobs if passes_hard_filter(j)]
    disqualified = len(jobs) - len(qualified)
    print(f"  Hard-filtered: {disqualified} jobs (wrong title / non-remote / salary below floor)")
    print(f"  Scoring {len(qualified)} qualifying jobs...")

    # ── Score in parallel ─────────────────────────────────────────────────────
    client  = anthropic.Anthropic()
    results = []
    errors  = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(score_job, client, j): j for j in qualified}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            if r.get("match_error"):
                errors += 1
            if i % 50 == 0:
                print(f"  {i}/{len(qualified)} scored...")

    # ── Filter and sort ───────────────────────────────────────────────────────
    passed = [r for r in results if r.get("match_score") is not None and r["match_score"] >= THRESHOLD]
    passed.sort(key=lambda x: x["match_score"], reverse=True)

    # ── Write ranked output ───────────────────────────────────────────────────
    out_path = os.path.join(out_dir, f"jobs_ranked_{TODAY}.json")
    with open(out_path, "w") as f:
        json.dump({
            "ranked_at":       datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_file":     os.path.basename(source_file),
            "mode":            mode,
            "threshold":       THRESHOLD,
            "total_evaluated": len(results),
            "total_passed":    len(passed),
            "errors":          errors,
            "jobs":            passed,
        }, f, indent=2)

    _status["jobs_evaluated"] = len(results)
    _status["jobs_passed"]    = len(passed)
    if errors > 0:
        _status["scoring_errors"] = errors

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\nRanked {TODAY}")
    print(f"  Source         : {os.path.basename(source_file)} ({mode})")
    print(f"  Total jobs     : {len(jobs)}")
    print(f"  Hard-filtered  : {disqualified} (wrong title / non-remote / below salary floor)")
    print(f"  Scored         : {len(qualified)}")
    print(f"  Passed (≥60%)  : {len(passed)}")
    print(f"  Errors         : {errors}")
    print(f"  Written        : output/jobs_ranked_{TODAY}.json")
    print(f"  Written        : output/ranker_status_{TODAY}.json")

    if passed:
        print(f"\nTop 5 matches:")
        for i, job in enumerate(passed[:5], 1):
            score_pct = int(job['match_score'] * 100)
            print(f"  {i}. {score_pct}%  {job['title']} @ {job['company']}")

except SystemExit:
    _write_status()
    raise
except Exception as exc:
    _status["status"] = "failed"
    _status["error"]  = str(exc)
    _write_status()
    print(f"ERROR: Ranker failed — {exc}", file=sys.stderr)
    sys.exit(1)

_write_status()
