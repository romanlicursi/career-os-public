# Job Monitor — Deploy Guide

Automated internship job monitor. Scrapes LinkedIn, Indeed, Glassdoor, and Wellfound on an hourly schedule, deduplicates, and sends instant phone notifications via ntfy.sh for every new match.

---

## Deploy in 5 minutes

### 1. Install ntfy on your phone

Download the ntfy app (iOS or Android) and subscribe to a topic name you invent — e.g., `roman-jobs-abc123`. Keep it unguessable (anyone who knows the topic name can see your notifications).

### 2. Add the secret to GitHub

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|------|-------|
| `NTFY_TOPIC` | your topic name (e.g. `roman-jobs-abc123`) |

### 3. Tune `criteria.json`

Edit `criteria.json` in the repo root. Key fields:

| Field | Effect |
|-------|--------|
| `keywords` | Job title/description must match at least one (case-insensitive) |
| `priority_companies` | Always alert, regardless of keyword match |
| `blocklist_companies` | Never alert. Partial company name match. |
| `remote_only` | Skip jobs without a remote flag |
| `max_hours_old` | Skip jobs posted more than N hours ago |
| `results_per_source` | Max fetched per source per run (keep ≤ 25) |

### 4. Test locally before deploying

```bash
pip install python-jobspy requests
python3 scripts/monitor.py --dry-run
```

`--dry-run` prints matches to stdout without sending notifications or writing to `seen_jobs.json`.

### 5. Trigger manually to verify

In GitHub: **Actions → Job Monitor → Run workflow**

Check that:
- The run completes without errors
- `seen_jobs.json` is committed back (look for a `chore: update seen_jobs` commit)
- A notification arrives on your phone

---

## How it works

```
Every hour (GitHub Actions cron)
  └── scrape LinkedIn + Indeed + Glassdoor (python-jobspy)
  └── scrape Wellfound (GraphQL API)
        ↓
  Deduplicate against seen_jobs.json
        ↓
  Filter: keyword match + remote + blocklist
        ↓
  For each new match:
    → push notification to ntfy.sh (per job)
    → add to seen_jobs.json
        ↓
  Commit seen_jobs.json back to repo
```

## Notification tiers

| Emoji | Tier | Trigger |
|-------|------|---------|
| 🔴 | PRIORITY | Company is in `priority_companies` list |
| 🟡 | TITLE_MATCH | Keyword found in job title |
| ⚪ | DESC_MATCH | Keyword found in description only |

## Rejection tracking

To log a manually rejected job, append an entry to `rejected_jobs.json`:

```json
{"title": "GTM Intern", "company": "Acme Staffing", "reason": "staffing agency"}
```

Once 5+ entries exist, the monitor prints a pattern summary on each run:

```
── Rejection patterns (8 total) ──
  "wrong seniority": 4 jobs
  "not remote": 3 jobs
  "staffing agency": 1 job
```

Use this to tighten `blocklist_companies` or add keywords.

## Zero cost

- GitHub Actions free tier: 2,000 min/month — this uses ~2 min/run × 24 runs/day × 30 days = ~1,440 min/month
- ntfy.sh: free for self-hosted topics
- No API keys required (jobspy scrapes directly; Wellfound uses public GraphQL)
