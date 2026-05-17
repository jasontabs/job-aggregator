---
name: job-ranker
description: Scores and ranks scraped PM job postings against the user's profile using AI. Filters to jobs with ≥60% match and outputs a ranked JSON file. Use this after the job-scraper runs, or whenever the user wants to rank jobs, see the best matches, filter job results, or find which jobs are most relevant. Trigger for phrases like "rank the jobs", "score the jobs", "which jobs match", "filter for best matches", "run the ranker", "what's a good match", "find my best job leads".
---

# Job Ranker

Reads today's *new-jobs-only* delta from `../job-scraper/output/jobs_new_{YYYY-MM-DD}.json` if it exists, otherwise falls back to the most recent `jobs_raw_*.json`. Applies hard pre-filters, scores qualifying jobs against the candidate profile using Claude Sonnet, filters to ≥60% match, and writes `output/jobs_ranked_{YYYY-MM-DD}.json`.

The daily cron pipeline scores only jobs newly added to the DB that morning — typical runs are 50-200 jobs, not the full ~1,300-job active set. The `jobs_raw_*.json` fallback exists for ad-hoc re-rankings of the full active set.

**Hard filters (applied before scoring — not scored, just dropped):**
- Title must contain one of the target PM keywords
- Must be explicitly fully remote AND US-eligible (see remote signal logic below)
- If salary is listed, `max` must be ≥ $175,000

**Remote signal logic (mirrors the scraper's strict filter):**
- Drop if location matches any token in `NON_US` (Europe, UK, Canada, India, Australia, ~50 more)
- Drop if location or description contains hybrid/onsite signals (`hybrid role`, `days in the office`, etc.)
- Accept only if at least one of: `remote` / `anywhere` / `work from home` in location, OR an explicit fully-remote phrase in description (`fully remote`, `100% remote`, `remote-first`, `work from anywhere`, `remote position`, etc.)
- Bare `"United States"`, empty location, and US cities without a remote signal are all rejected

See `references/profile.md` for the candidate profile and scoring rubric.
See `references/schema.md` for the exact output JSON schema.

**Requires**: `ANTHROPIC_API_KEY` environment variable.

## Implementation

Write and run a Python script. Use `ThreadPoolExecutor(max_workers=10)` for parallel API calls. Expected runtime: ~5 minutes for 1,348 jobs with Sonnet.

```python
import anthropic, json, os, glob, datetime, re
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
# Expand NON_US, HYBRID_TERMS, REMOTE_DESC_PHRASES, and HYBRID_DESC_PHRASES
# the same way the scraper does — see run.py for the full lists.

def passes_hard_filter(job):
    title = (job.get("title") or "").lower()
    loc   = (job.get("location") or "").lower()
    desc  = (job.get("description") or "").lower()
    comp  = job.get("compensation", {})

    if not any(k in title for k in PM_KEYWORDS):                return False
    if comp.get("listed") and comp.get("max", 0) < COMP_FLOOR:  return False

    # Hard rejects
    if any(t in loc for t in NON_US):                  return False
    if any(t in loc for t in HYBRID_TERMS):            return False
    if any(p in desc for p in HYBRID_DESC_PHRASES):    return False

    # Require explicit fully-remote signal
    if any(t in loc for t in ["remote", "anywhere", "work from home", "wfh"]):
        return True
    if any(p in desc for p in REMOTE_DESC_PHRASES):
        return True
    return False

with open("references/profile.md") as f:
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
```

### Loading input

Prefer today's new-only delta; fall back to the most recent full file:

```python
new_file  = f"../job-scraper/output/jobs_new_{TODAY}.json"
raw_files = sorted(glob.glob("../job-scraper/output/jobs_raw_*.json"))

if os.path.exists(new_file):
    source_file, mode = new_file, "new-only"
elif raw_files:
    source_file, mode = raw_files[-1], "full"
else:
    raise FileNotFoundError("No jobs files found — run the job-scraper first.")

with open(source_file) as f:
    data = json.load(f)
jobs = data["jobs"]

if not jobs:
    print(f"No new jobs today ({os.path.basename(source_file)}). Nothing to rank.")
    raise SystemExit(0)

print(f"Ranking {len(jobs)} jobs from {os.path.basename(source_file)} (mode: {mode})...")
```

### Hard pre-filter

Apply before scoring — jobs that fail are dropped entirely, not scored:

```python
qualified    = [j for j in jobs if passes_hard_filter(j)]
disqualified = len(jobs) - len(qualified)
print(f"  Hard-filtered: {disqualified} jobs (wrong title / non-remote / salary below floor)")
print(f"  Scoring {len(qualified)} qualifying jobs...")
```

### Parallel scoring

```python
client  = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env
results = []
errors  = 0

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
    futs = {ex.submit(score_job, client, j): j for j in qualified}
    for i, fut in enumerate(as_completed(futs), 1):
        r = fut.result()
        results.append(r)
        if r.get("match_error"):
            errors += 1
        if i % 100 == 0:
            print(f"  {i}/{len(qualified)} scored...")
```

### Filter and sort

```python
passed = [
    r for r in results
    if r.get("match_score") is not None and r["match_score"] >= THRESHOLD
]
passed.sort(key=lambda x: x["match_score"], reverse=True)
```

### Output

Write `output/jobs_ranked_{YYYY-MM-DD}.json`. See `references/schema.md` for exact structure.

```python
os.makedirs("output", exist_ok=True)
out_path = f"output/jobs_ranked_{TODAY}.json"
output = {
    "ranked_at":       datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "source_file":     os.path.basename(source_file),
    "threshold":       THRESHOLD,
    "total_evaluated": len(results),
    "total_passed":    len(passed),
    "errors":          errors,
    "jobs":            passed,
}
with open(out_path, "w") as f:
    json.dump(output, f, indent=2)
```

Print run summary:
```
Ranked {date}
  Source         : {source_file}
  Total jobs     : {N}
  Hard-filtered  : {N} (wrong title / non-remote / below salary floor)
  Scored         : {N}
  Passed (≥60%)  : {N}
  Errors         : {N}
  Written        : output/jobs_ranked_{date}.json

Top 5 matches:
  1. {score}  {title} @ {company}
  2. ...
```

## Updating the Profile

Edit `references/profile.md` to adjust scoring weights, add new strengths, or change the threshold. The SYSTEM_PROMPT is built from that file at runtime — no script changes needed.

## Threshold

Default is 60% (`THRESHOLD = 0.60`). The user can override by passing a different value when invoking the skill.
