# Output Schema

## jobs_ranked_{YYYY-MM-DD}.json

```json
{
  "ranked_at": "2025-01-15T20:00:00Z",
  "source_file": "jobs_raw_2025-01-15.json",
  "threshold": 0.60,
  "total_evaluated": 1348,
  "total_passed": 87,
  "errors": 2,
  "jobs": [
    {
      "id": "gh-123456",
      "title": "Staff Product Manager, AI Platform",
      "company": "acme",
      "location": "Remote, US",
      "url": "https://boards.greenhouse.io/acme/jobs/123456",
      "apply_url": "https://boards.greenhouse.io/acme/jobs/123456",
      "posted_at": "2025-01-10T00:00:00Z",
      "source": "greenhouse",
      "source_token": "acme",
      "compensation": {
        "min": 200000,
        "max": 250000,
        "currency": "USD",
        "listed": true
      },
      "first_seen_at": "2025-01-15T20:00:00Z",
      "expires_at": "2025-02-14T20:00:00Z",
      "match_score": 0.88,
      "match_reasons": [
        "Staff-level PM role aligns with target seniority",
        "AI Platform domain matches GenAI background",
        "B2B SaaS company profile"
      ],
      "match_gaps": []
    }
  ]
}
```

## Field notes

**match_score**: Float 0.0–1.0. Jobs are sorted descending. Only jobs ≥ threshold are included.

**match_reasons**: 2–3 short phrases explaining why the job is a good fit. Omit array entirely if score < 0.40.

**match_gaps**: 1–2 short phrases on where fit is weaker. Omit array (or use `[]`) if score ≥ 0.80.

**match_error**: Present instead of match_score/reasons/gaps if the scoring API call failed. These jobs are excluded from the ranked output but counted in `errors`.

All other fields are passed through unchanged from `jobs_raw_*.json`.
