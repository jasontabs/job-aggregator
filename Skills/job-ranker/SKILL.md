---
name: job-ranker
description: Scores and ranks scraped PM job postings against the user's profile using AI. Filters to jobs with ≥60% match and outputs a ranked JSON file. Use this after the job-scraper runs, or whenever the user wants to rank jobs, see the best matches, filter job results, or find which jobs are most relevant. Trigger for phrases like "rank the jobs", "score the jobs", "which jobs match", "filter for best matches", "run the ranker", "what's a good match", "find my best job leads".
---

# Job Ranker

Reads the latest scraped jobs from `../job-scraper/output/jobs_raw_{YYYY-MM-DD}.json`, applies hard pre-filters, scores qualifying jobs against the candidate profile using Claude Sonnet, filters to ≥60% match, and writes `output/jobs_ranked_{YYYY-MM-DD}.json`.

**Hard filters (applied before scoring — not scored, just dropped):**
- Title must contain one of the target PM keywords
- Location must be remote US (no non-US terms, must contain "remote" / "anywhere" / be empty)
- If salary is listed, `max` must be ≥ $175,000

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
NON_US = ["emea", "europe", " uk", "london", "canada", "australia", "asia", "india", "brazil", "latam"]

def passes_hard_filter(job):
    title = (job.get("title") or "").lower()
    loc   = (job.get("location") or "").lower()
    comp  = job.get("compensation", {})

    if not any(k in title for k in PM_KEYWORDS):
        return False
    if any(t in loc for t in NON_US):
        return False
    is_remote = any(t in loc for t in ["remote", "anywhere", "united states", "usa", "north america"]) or not loc
    if not is_remote:
        return False
    if comp.get("listed") and comp.get("max", 0) < COMP_FLOOR:
        return False
    return True

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

Find the most recent raw jobs file:

```python
files = sorted(glob.glob("../job-scraper/output/jobs_raw_*.json"))
if not files:
    raise FileNotFoundError("No jobs_raw_*.json found — run the job-scraper first.")
source_file = files[-1]
with open(source_file) as f:
    data = json.load(f)
jobs = data["jobs"]
print(f"Scoring {len(jobs)} jobs from {os.path.basename(source_file)}...")
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
