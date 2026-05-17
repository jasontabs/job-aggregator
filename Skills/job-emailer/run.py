#!/usr/bin/env python3
"""
Compose and send the daily ranked-jobs digest via Mailgun API.

Reads:
  ../job-ranker/output/jobs_ranked_{TODAY}.json   (ranker output, may be missing)
  ../job-scraper/output/jobs_new_{TODAY}.json     (today's scrape delta)
  ../../Linkedin-connections/Connections.csv      (optional; adds warm-intro callouts)

Env vars required:
  MAILGUN_API_KEY  - API key from mailgun.com
  MAILGUN_DOMAIN   - sending domain (e.g. sandboxXXX.mailgun.org)
  TO_EMAIL         - recipient address (defaults to jasontabaczynski@gmail.com)
"""

import os, json, datetime, urllib.request, urllib.error, urllib.parse, base64, csv, re
from html import escape

def _strip_html(text):
    text = re.sub(r'<[^>]+>', ' ', text or '')
    for ent, ch in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&nbsp;',' '),('&#39;',"'"),('&quot;','"')]:
        text = text.replace(ent, ch)
    return re.sub(r'\s+', ' ', text).strip()

TODAY       = datetime.date.today().isoformat()
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT   = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
RANKER_OUT  = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "job-ranker", "output"))
SCRAPER_OUT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "job-scraper", "output"))
CONNECTIONS_CSV = os.path.join(REPO_ROOT, "Linkedin-connections", "Connections.csv")

SOURCE_LABELS = {"greenhouse": "Greenhouse", "ashby": "Ashby", "lever": "Lever", "remoteok": "Remote OK"}

MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY")
MAILGUN_DOMAIN  = os.environ.get("MAILGUN_DOMAIN")
TO_EMAIL        = os.environ.get("TO_EMAIL", "jasontabaczynski@gmail.com")

if not MAILGUN_API_KEY:
    raise SystemExit("ERROR: MAILGUN_API_KEY must be set (add to ~/.job_aggregator_env)")
if not MAILGUN_DOMAIN:
    raise SystemExit("ERROR: MAILGUN_DOMAIN must be set (add to ~/.job_aggregator_env)")


def classify_title(title):
    t = (title or "").lower()
    if "head of product" in t:       return "Head of Product"
    if "principal" in t:             return "Principal PM"
    if "staff" in t:                 return "Staff PM"
    if "group product manager" in t: return "Group PM"
    if "senior" in t:                return "Senior PM"
    return "Product Manager"


# ── LinkedIn connections lookup ──────────────────────────────────────────────
_STRIP = re.compile(
    r'\b(inc|llc|corp|ltd|co|company|group|technologies|technology|'
    r'solutions|services|consulting|software|systems|global|holdings)\.?\b'
)

def _norm(name):
    n = (name or "").lower()
    n = _STRIP.sub('', n)
    n = re.sub(r'[^\w\s]', '', n)
    return re.sub(r'\s+', ' ', n).strip()

def load_connections(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline='', encoding='utf-8') as f:
        lines = f.readlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("First Name")), None)
    if start is None:
        return {}
    result = {}
    for row in csv.DictReader(lines[start:]):
        company  = (row.get("Company") or "").strip()
        first    = (row.get("First Name") or "").strip()
        last     = (row.get("Last Name") or "").strip()
        position = (row.get("Position") or "").strip()
        url      = (row.get("URL") or "").strip()
        if not company or not first:
            continue
        result.setdefault(_norm(company), []).append((f"{first} {last}".strip(), position, url))
    return result

def find_connections(job_company, conn_map):
    key = _norm(job_company)
    if not key:
        return []
    if key in conn_map:
        return conn_map[key]
    for co_key, people in conn_map.items():
        if key in co_key or co_key in key:
            return people
    return []

connections_map = load_connections(CONNECTIONS_CSV)
connections_loaded = bool(connections_map)
print(f"Connections: {'loaded ' + str(sum(len(v) for v in connections_map.values())) + ' people at ' + str(len(connections_map)) + ' companies' if connections_loaded else 'CSV not found — skipping warm-intro callouts'}")


# ── Load pipeline data ───────────────────────────────────────────────────────
ranked_file         = os.path.join(RANKER_OUT, f"jobs_ranked_{TODAY}.json")
new_file            = os.path.join(SCRAPER_OUT, f"jobs_new_{TODAY}.json")
scraper_status_file = os.path.join(SCRAPER_OUT, f"scraper_status_{TODAY}.json")
ranker_status_file  = os.path.join(RANKER_OUT,  f"ranker_status_{TODAY}.json")

scrape_total = 0
if os.path.exists(new_file):
    with open(new_file) as f:
        scrape_total = json.load(f).get("total_jobs", 0)

matches         = []
total_evaluated = 0
total_passed    = 0
if os.path.exists(ranked_file):
    with open(ranked_file) as f:
        ranked = json.load(f)
    total_evaluated = ranked.get("total_evaluated", 0)
    total_passed    = ranked.get("total_passed", 0)
    matches         = ranked.get("jobs", [])

# ── Collect pipeline issues ──────────────────────────────────────────────────
issues = []

scraper_status = {}
if os.path.exists(scraper_status_file):
    with open(scraper_status_file) as f:
        scraper_status = json.load(f)
    for src, info in scraper_status.get("sources", {}).items():
        st = info.get("status", "ok")
        label = SOURCE_LABELS.get(src, src.title())
        if st == "failed":
            issues.append(
                f"{label}: all {info.get('errors', '?')} tokens errored — no jobs scraped from this source"
            )
        elif st == "degraded":
            issues.append(
                f"{label}: {info.get('errors', '?')} of {info.get('companies_queried', '?')} tokens errored (degraded)"
            )
elif not os.path.exists(new_file) and not os.path.exists(os.path.join(SCRAPER_OUT, f"jobs_raw_{TODAY}.json")):
    issues.append("Scraper: no output files found — scraper may have crashed before writing")

ranker_status = {}
if os.path.exists(ranker_status_file):
    with open(ranker_status_file) as f:
        ranker_status = json.load(f)
    rst = ranker_status.get("status", "ok")
    if rst == "failed":
        err_msg = ranker_status.get("error", "unknown error")
        issues.append(f"Ranker: failed to run — {err_msg[:200]}")
elif not os.path.exists(ranked_file):
    issues.append("Ranker: no output file found — ranker may not have run")


# ── Subject ─────────────────────────────────────────────────────────────────
issue_flag = " ⚠ pipeline issues" if issues else ""
if scrape_total == 0:
    subject = f"Job Digest {TODAY} — no new jobs scraped{issue_flag}"
elif total_passed == 0:
    subject = f"Job Digest {TODAY} — {scrape_total} scraped, 0 matches{issue_flag}"
else:
    plural = "es" if total_passed != 1 else ""
    subject = f"Job Digest {TODAY} — {total_passed} match{plural} (of {scrape_total} scraped){issue_flag}"


# ── HTML body ───────────────────────────────────────────────────────────────
STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
         line-height: 1.5; color: #222; max-width: 720px; margin: 16px; }
  h1 { margin: 0 0 8px 0; font-size: 22px; }
  .summary { background: #f4f6f8; padding: 12px 16px; border-radius: 6px; margin-bottom: 24px; }
  .issues { background: #fef2f2; border: 1px solid #fecaca; padding: 12px 16px; border-radius: 6px; margin-bottom: 20px; }
  .issues h2 { margin: 0 0 8px 0; font-size: 14px; color: #b91c1c; }
  .issues ul { margin: 0; padding-left: 18px; font-size: 13px; color: #7f1d1d; }
  .job { border-top: 1px solid #e5e7eb; padding: 16px 0; }
  .job:first-of-type { border-top: none; }
  .score { display: inline-block; background: #2563eb; color: #fff; padding: 2px 8px;
           border-radius: 4px; font-weight: 600; font-size: 13px; vertical-align: middle; margin-right: 8px; }
  .score.lower { background: #6b7280; }
  .title { font-size: 17px; font-weight: 600; margin: 0; }
  .meta { color: #6b7280; font-size: 13px; margin: 4px 0 8px; }
  .meta strong { color: #111; }
  .synopsis { margin: 8px 0 10px; color: #374151; font-size: 14px; }
  .connections { margin: 6px 0 10px; font-size: 13px; color: #1d4ed8;
                 background: #eff6ff; border: 1px solid #bfdbfe;
                 border-radius: 4px; padding: 6px 10px; }
  .connections strong { color: #1e3a8a; }
  .apply { display: inline-block; background: #10b981; color: #fff !important; padding: 6px 14px;
           border-radius: 4px; text-decoration: none; font-weight: 500; font-size: 13px; }
  .compensation { color: #059669; font-weight: 500; }
"""

issues_html = ""
if issues:
    items = "\n".join(f"  <li>{escape(i)}</li>" for i in issues)
    issues_html = f'<div class="issues"><h2>Pipeline Issues</h2><ul>\n{items}\n</ul></div>\n'

parts = [f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{STYLE}</style></head><body>
<h1>Daily Job Digest — {TODAY}</h1>
<div class="summary">
  <strong>{scrape_total}</strong> new job{'s' if scrape_total != 1 else ''} scraped today<br>
  <strong>{total_passed}</strong> match{'es' if total_passed != 1 else ''} ≥60%
</div>
{issues_html}"""]

if not matches:
    if scrape_total == 0:
        parts.append("<p>No new jobs were posted in the last 24 hours.</p>")
    else:
        parts.append("<p>No new jobs cleared the 60% threshold today.</p>")
else:
    for job in matches:
        score       = int(round(job.get("match_score", 0) * 100))
        score_class = "" if score >= 70 else "lower"
        title_full  = escape(job.get("title", "") or "")
        company_raw = job.get("company", "") or ""
        company     = escape(company_raw)
        category    = classify_title(job.get("title", ""))
        synopsis    = escape(_strip_html(job.get("synopsis", "") or "—"))
        apply_url   = job.get("apply_url") or job.get("url", "") or "#"
        comp        = job.get("compensation", {}) or {}
        comp_html   = ""
        if comp.get("listed"):
            comp_html = f' &nbsp;•&nbsp; <span class="compensation">${comp.get("min", 0):,}–${comp.get("max", 0):,}/yr</span>'

        connections_html = ""
        if connections_loaded:
            people = find_connections(company_raw, connections_map)
            if people:
                names = ", ".join(
                    f'<a href="{escape(url)}" style="color:#1d4ed8">{escape(name)}</a>'
                    f' <span style="color:#6b7280">({escape(pos)})</span>'
                    for name, pos, url in people[:3]
                )
                more = f" +{len(people)-3} more" if len(people) > 3 else ""
                connections_html = f'<p class="connections"><strong>Warm intro:</strong> {names}{escape(more)}</p>'

        parts.append(f"""<div class="job">
  <p class="title"><span class="score {score_class}">{score}%</span>{title_full}</p>
  <p class="meta"><strong>{company}</strong> &nbsp;•&nbsp; {category}{comp_html}</p>
  <p class="synopsis">{synopsis}</p>
  {connections_html}<a class="apply" href="{escape(apply_url)}">Apply →</a>
</div>""")

parts.append("</body></html>")
body_html = "\n".join(parts)


# ── Send via Mailgun API ─────────────────────────────────────────────────────
payload = urllib.parse.urlencode({
    "from":    f"Job Digest <mailgun@{MAILGUN_DOMAIN}>",
    "to":      TO_EMAIL,
    "subject": subject,
    "html":    body_html,
}).encode()

credentials = base64.b64encode(f"api:{MAILGUN_API_KEY}".encode()).decode()
req = urllib.request.Request(
    f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
    data=payload,
    method="POST",
    headers={
        "Authorization": f"Basic {credentials}",
        "Content-Type":  "application/x-www-form-urlencoded",
    },
)
try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    print(f"Email sent: {subject}  (id: {result.get('id', '?')})")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    raise SystemExit(f"Mailgun error {e.code}: {body}")
