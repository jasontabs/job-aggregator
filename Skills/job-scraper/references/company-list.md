# Company Lists

Company tokens are no longer a hand-curated list — the scraper queries the full population of tech companies on each ATS platform using token lists stored in `data/`.

## Source

Token lists are downloaded from [github.com/Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator), which builds them from Common Crawl index data. Refreshed monthly.

| File | Tokens | Covers |
|---|---|---|
| `data/greenhouse_companies.json` | ~7,300 | All tech-filtered Greenhouse boards |
| `data/ashby_companies.json` | ~2,800 | All tech-filtered Ashby boards |
| `data/lever_companies.json` | ~4,100 | All tech-filtered Lever boards |

## Tech Filter Applied

The raw lists (8,180 / 2,985 / 4,368 tokens) are filtered to exclude obvious non-tech tokens containing:

- **Healthcare/staffing**: `health`, `medical`, `clinic`, `hospital`, `care`, `therapy`, `nurse`, `nursing`, `dental`, `pharma`, `rehab`, `staffing`, `recruiting`, `workforce`
- **Government/education**: `university`, `college`, `school`, `district`, `county`, `gov`, `nonprofit`, `foundation`
- **Retail/hospitality**: `restaurant`, `hotel`, `retail`, `grocery`, `food`, `bakery`, `gym`, `spa`
- **Real estate/insurance**: `realty`, `mortgage`, `insurance`, `broker`, `dealership`
- **Garbage tokens**: `sandbox`, `test`, `demo`, `gameday`, tokens >40 chars, pure-numeric tokens

The PM title filter is the primary quality gate — any company without PM jobs is simply skipped. The tech filter reduces API call count by ~10–15%.

## Performance (2026-05-15 baseline)

| Platform | Queried | PM jobs found | Errors (404/timeout) | Time |
|---|---|---|---|---|
| Greenhouse | 7,313 | 820 | 2,393 (33%) | 56s |
| Ashby | 2,824 | 529 | 556 (20%) | 33s |
| Lever | 4,136 | 72 | 2,161 (52%) | 109s |
| **Total** | **14,273** | **1,348** (after dedup) | | **~3 min** |

High error rates are expected — many tokens are defunct boards or companies that have migrated ATS.

## Refreshing

Run the refresh script in SKILL.md to pull updated lists from the GitHub repo. Recommended monthly.

## Companies NOT covered

Companies on proprietary portals (Workday, Taleo, iCIMS) are not covered by these lists. Notable ones: Google, Meta, Apple, Amazon, Microsoft, Salesforce. Built.in.com is the coverage path for those when JS-rendering support is added.
