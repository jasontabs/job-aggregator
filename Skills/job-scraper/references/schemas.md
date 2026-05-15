# Output Schema

## jobs_raw_{YYYY-MM-DD}.json

```json
{
  "scraped_at": "2025-01-15T20:00:00Z",
  "total_jobs": 42,
  "sources": {
    "greenhouse": { "companies_queried": 19, "jobs_kept": 18, "errors": [] },
    "ashby":      { "companies_queried": 6,  "jobs_kept": 8,  "errors": [] },
    "lever":      { "companies_queried": 1,  "jobs_kept": 0,  "errors": [] },
    "builtin":    { "jobs_kept": 0, "note": "JS-rendered, requires headless browser" }
  },
  "jobs": [
    {
      "id": "gh-123456",
      "title": "Staff Product Manager, Payments",
      "company": "Stripe",
      "location": "Remote, US",
      "is_remote": true,
      "url": "https://boards.greenhouse.io/stripe/jobs/123456",
      "apply_url": "https://boards.greenhouse.io/stripe/jobs/123456",
      "posted_at": "2025-01-10T00:00:00Z",
      "description": "Full plain-text job description with HTML stripped...",
      "compensation": {
        "min": 180000,
        "max": 240000,
        "currency": "USD",
        "listed": true
      },
      "source": "greenhouse",
      "source_token": "stripe"
    }
  ]
}
```

## Field notes

**id**: Prefixed by source — `gh-`, `lv-`, `bi-` — to avoid collisions when merging.

**is_remote**: `true` if location text contains `remote` or `anywhere`; `false` otherwise.

**posted_at**: ISO 8601 UTC string. Use the job's last-updated or created timestamp. If unavailable, use the scrape date.

**description**: Plain text only — strip all HTML tags. Truncate at 5000 characters if longer.

**compensation**:
- `min`/`max`: integers (annual USD). If only one number is found, set both `min` and `max` to that value.
- `listed`: `true` if salary was explicitly stated in the description or job metadata; `false` otherwise.
- If `listed` is `false`, omit `min`/`max` entirely (don't set to 0 or null).

**source_token**: The Greenhouse, Ashby, or Lever token used to query. `null` for Built.in jobs.

**id prefixes**: `gh-` = Greenhouse, `ab-` = Ashby, `lv-` = Lever, `bi-` = Built.in.
