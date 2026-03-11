#!/usr/bin/env python3
"""
monitor.py — Job Monitoring System

Scrapes LinkedIn, Indeed, Glassdoor (via python-jobspy) and Wellfound (via
internal GraphQL API), deduplicates against a committed JSON store, and sends
per-job push notifications to ntfy.sh for every new match.

Inputs:
  criteria.json       — editable config: keywords, blocklist, filters
  seen_jobs.json      — persistent dedup store (committed back each run)
  rejected_jobs.json  — manual rejection log with reason tracking

Output:
  seen_jobs.json      — updated with new job IDs
  stdout              — per-source candidate counts and notification log

Usage:
    python3 scripts/monitor.py
    python3 scripts/monitor.py --dry-run

Dependencies:
    pip install python-jobspy requests
    export NTFY_TOPIC=your-topic-name
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Configuration ─────────────────────────────────────────────────────────────

ROOT             = Path(__file__).parent.parent
CRITERIA_PATH    = ROOT / "criteria.json"
SEEN_PATH        = ROOT / "seen_jobs.json"
REJECTED_PATH    = ROOT / "rejected_jobs.json"

NTFY_TOPIC       = os.environ.get("NTFY_TOPIC", "")
NTFY_BASE_URL    = "https://ntfy.sh"

WELLFOUND_URL    = "https://wellfound.com/api/graphql"

TIER_EMOJI = {
    "PRIORITY":    "🔴",
    "TITLE_MATCH": "🟡",
    "DESC_MATCH":  "⚪",
}

# ── Data helpers ──────────────────────────────────────────────────────────────

def load_criteria(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with open(path) as f:
        data = json.load(f)
    return set(data)


def save_seen(path: Path, ids: set[str]) -> None:
    with open(path, "w") as f:
        json.dump(sorted(ids), f, indent=2)


def load_rejected(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def print_rejection_summary(rejections: list[dict]) -> None:
    if len(rejections) < 5:
        return
    counts = Counter(r.get("reason", "unknown") for r in rejections)
    print(f"\n── Rejection patterns ({len(rejections)} total) ──")
    for reason, count in counts.most_common():
        print(f'  "{reason}": {count} job{"s" if count != 1 else ""}')
    print()


def make_job_id(job: dict) -> str:
    raw = f"{job.get('title','').lower()}|{job.get('company','').lower()}|{job.get('source','')}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ── Scrapers ──────────────────────────────────────────────────────────────────

def scrape_jobspy(criteria: dict) -> list[dict]:
    """Scrape LinkedIn, Indeed, and Glassdoor via python-jobspy."""
    try:
        from jobspy import scrape_jobs  # type: ignore
    except ImportError:
        print("jobspy: not installed — skipping (pip install python-jobspy)")
        return []

    limit = criteria.get("results_per_source", 25)
    keywords = " OR ".join(f'"{k}"' for k in criteria.get("keywords", []))

    results = []
    for site in ["linkedin", "indeed", "glassdoor"]:
        try:
            df = scrape_jobs(
                site_name=site,
                search_term=keywords,
                results_wanted=limit,
                is_remote=criteria.get("remote_only", True),
                job_type="internship",
            )
            jobs = df.to_dict("records") if df is not None and len(df) > 0 else []
            passed = []
            for row in jobs:
                job = {
                    "title":       str(row.get("title", "")),
                    "company":     str(row.get("company", "")),
                    "location":    str(row.get("location", "")),
                    "url":         str(row.get("job_url", row.get("url", ""))),
                    "description": str(row.get("description", "")),
                    "posted_at":   str(row.get("date_posted", "")),
                    "source":      site.capitalize(),
                    "is_remote":   bool(row.get("is_remote", False)),
                }
                passed.append(job)
            print(f"{site.capitalize()}: {len(jobs)} candidates → {len(passed)} passed filters")
            results.extend(passed)
        except Exception as e:
            print(f"{site.capitalize()}: failed ({e})")

    return results


def scrape_wellfound(criteria: dict) -> list[dict]:
    """Scrape Wellfound via internal GraphQL API."""
    limit = criteria.get("results_per_source", 25)

    query = """
    query JobListings($role: String, $remote: Boolean, $jobType: String, $first: Int) {
      jobListings(role: $role, remote: $remote, jobType: $jobType, first: $first) {
        nodes {
          id
          title
          description
          remote
          liveStartAt
          applyUrl
          startup {
            name
          }
          locationNames
        }
      }
    }
    """

    variables = {
        "role": "intern",
        "remote": criteria.get("remote_only", True),
        "jobType": "full_time",
        "first": limit,
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://wellfound.com",
        "Referer": "https://wellfound.com/jobs",
    }

    try:
        resp = requests.post(
            WELLFOUND_URL,
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"Wellfound: failed ({resp.status_code})")
            return []

        data = resp.json()
        nodes = data.get("data", {}).get("jobListings", {}).get("nodes", [])
        if nodes is None:
            print("Wellfound: failed (no nodes in response)")
            return []

        jobs = []
        for node in nodes:
            jobs.append({
                "title":       str(node.get("title", "")),
                "company":     str((node.get("startup") or {}).get("name", "")),
                "location":    ", ".join(node.get("locationNames") or []),
                "url":         str(node.get("applyUrl", "")),
                "description": str(node.get("description", "")),
                "posted_at":   str(node.get("liveStartAt", "")),
                "source":      "Wellfound",
                "is_remote":   bool(node.get("remote", False)),
            })

        print(f"Wellfound: {len(nodes)} candidates → {len(jobs)} passed filters")
        return jobs

    except Exception as e:
        print(f"Wellfound: failed ({e})")
        return []


# ── Matching ──────────────────────────────────────────────────────────────────

def is_match(job: dict, criteria: dict) -> tuple[bool, str | None]:
    """Return (matched, tier) where tier is PRIORITY | TITLE_MATCH | DESC_MATCH | None."""
    company_lower = job.get("company", "").lower()
    title_lower   = job.get("title", "").lower()
    desc_lower    = job.get("description", "").lower()

    # Blocklist check
    for blocked in criteria.get("blocklist_companies", []):
        if blocked.lower() in company_lower:
            return False, None

    # Remote filter
    if criteria.get("remote_only", True):
        loc_lower = job.get("location", "").lower()
        is_remote = job.get("is_remote", False)
        remote_in_loc = "remote" in loc_lower
        if not is_remote and not remote_in_loc:
            return False, None

    # Priority companies — always alert
    for priority in criteria.get("priority_companies", []):
        if priority.lower() in company_lower:
            return True, "PRIORITY"

    # Keyword match in title
    for kw in criteria.get("keywords", []):
        if kw.lower() in title_lower:
            return True, "TITLE_MATCH"

    # Keyword match in description
    for kw in criteria.get("keywords", []):
        if kw.lower() in desc_lower:
            return True, "DESC_MATCH"

    return False, None


def _format_age(posted_at: str) -> str:
    """Convert posted_at string to human-readable age."""
    if not posted_at or posted_at == "None":
        return "unknown"
    try:
        # Try ISO format
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        hours = int(delta.total_seconds() / 3600)
        if hours < 1:
            return "< 1h ago"
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except Exception:
        return posted_at


# ── Notifications ─────────────────────────────────────────────────────────────

def send_notification(job: dict, tier: str, dry_run: bool) -> None:
    emoji  = TIER_EMOJI.get(tier, "⚪")
    title  = f"{emoji} {job.get('title', 'Job')} — {job.get('company', 'Unknown')}"
    age    = _format_age(job.get("posted_at", ""))
    loc    = job.get("location", "Remote") or "Remote"
    source = job.get("source", "")
    url    = job.get("url", "")

    body_lines = [
        f"📍 {loc} | {source}",
        f"📅 {age}",
    ]
    if url:
        body_lines.append(f"🔗 {url}")
    body = "\n".join(body_lines)

    if dry_run:
        print(f"\n[DRY RUN] Notification:")
        print(f"  Title: {title}")
        print(f"  Body:  {body}")
        return

    if not NTFY_TOPIC:
        print(f"  [WARN] NTFY_TOPIC not set — skipping notification for: {title}")
        return

    try:
        requests.post(
            f"{NTFY_BASE_URL}/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "high" if tier == "PRIORITY" else "default",
                "Tags": "briefcase",
            },
            timeout=10,
        )
        print(f"  ✓ Notified: {title}")
    except Exception as e:
        print(f"  ✗ Notification failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Job monitor — scrape and notify")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without sending notifications or updating seen_jobs.json")
    args = parser.parse_args()

    criteria  = load_criteria(CRITERIA_PATH)
    seen      = load_seen(SEEN_PATH)
    rejected  = load_rejected(REJECTED_PATH)

    print_rejection_summary(rejected)
    print(f"── Job Monitor {'(DRY RUN) ' if args.dry_run else ''}────────────────────────────────")
    print(f"Seen jobs in store: {len(seen)}")
    print()

    # ── Scrape all sources ────────────────────────────────────────────────────
    all_jobs: list[dict] = []
    all_jobs.extend(scrape_jobspy(criteria))
    all_jobs.extend(scrape_wellfound(criteria))

    print(f"\nTotal candidates: {len(all_jobs)}")

    # ── Filter and notify ─────────────────────────────────────────────────────
    new_count = 0
    new_seen  = set(seen)

    for job in all_jobs:
        job_id = make_job_id(job)

        if job_id in seen:
            continue

        matched, tier = is_match(job, criteria)
        if not matched:
            continue

        new_count += 1
        print(f"\n[NEW] {tier} — {job.get('title')} @ {job.get('company')} ({job.get('source')})")
        send_notification(job, tier, dry_run=args.dry_run)

        if not args.dry_run:
            new_seen.add(job_id)

    print(f"\n── Summary ──────────────────────────────────────────────────────────")
    print(f"New matches: {new_count}")

    if not args.dry_run and new_seen != seen:
        save_seen(SEEN_PATH, new_seen)
        print(f"seen_jobs.json updated ({len(new_seen)} total)")
    elif args.dry_run:
        print("seen_jobs.json not updated (dry run)")


if __name__ == "__main__":
    main()
