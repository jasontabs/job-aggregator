---
name: job-emailer
description: Sends a daily email digest of newly ranked PM jobs via Gmail SMTP. Reads the most recent jobs_ranked_{date}.json and emails the user a formatted summary with apply links. Use this after the job-ranker runs, or whenever the user wants to email today's matches, send the digest, or get a job email. Trigger for phrases like "send the digest", "email the jobs", "send today's matches", "mail me the jobs", "run the emailer".
---

# Job Emailer

Composes and sends a daily HTML email digest of jobs that scored ≥60% against the candidate profile.

The email includes:
- Number of new jobs scraped that day
- Number that matched ≥60%
- Ranked list (highest score first) with: posting title, company, role classification (Senior PM / Staff PM / Principal PM / Head of Product), 1–2 sentence synopsis, salary if listed, and a direct apply link

**Requires**: `GMAIL_USER` and `GMAIL_APP_PASSWORD` environment variables.

## Gmail setup (one-time)

The user must generate a Gmail app password — their normal account password will NOT work for SMTP. Steps:

1. Enable 2-Step Verification at https://myaccount.google.com/security (required for app passwords)
2. Go to https://myaccount.google.com/apppasswords
3. Create a new app password labeled "Job Aggregator"
4. Add to `~/.job_aggregator_env`:
   ```
   export GMAIL_USER="jasontabaczynski@gmail.com"
   export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
   ```

## Inputs

Reads two files (both produced by the daily cron pipeline):
- `../job-scraper/output/jobs_new_{TODAY}.json` — for `total_jobs` scraped today
- `../job-ranker/output/jobs_ranked_{TODAY}.json` — for `total_passed`, `total_evaluated`, and the ranked job list

If `jobs_ranked_{TODAY}.json` is missing (e.g., ranker exited early because 0 new jobs were found), the email still sends with a "no matches today" body. This confirms the cron pipeline ran.

## Role classification

Maps the full posting title to a short role classification for the digest:

```python
def classify_title(title):
    t = (title or "").lower()
    if "head of product" in t:        return "Head of Product"
    if "principal" in t:              return "Principal PM"
    if "staff" in t:                  return "Staff PM"
    if "group product manager" in t:  return "Group PM"
    if "senior" in t:                 return "Senior PM"
    return "Product Manager"
```

## Subject line

- `Job Digest {YYYY-MM-DD} — N matches (of M scraped)`
- `Job Digest {YYYY-MM-DD} — M scraped, 0 matches` (when nothing cleared 60%)
- `Job Digest {YYYY-MM-DD} — no new jobs scraped` (when scraper found nothing new)

## Implementation

The standalone script lives at `run.py` in this directory. It's invoked by `run_daily.sh` after the ranker. Run manually with:

```bash
/opt/homebrew/bin/python3 Skills/job-emailer/run.py
```

The script uses Python's built-in `smtplib` over `smtp.gmail.com:465` (SSL). No third-party dependencies.
