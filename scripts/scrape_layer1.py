#!/usr/bin/env python3
"""
scrape_layer1.py — Layer 1 Market Signal Scraper

Pulls 50 job postings (10 per title × 5 titles) from LinkedIn via Apify.
Actor: apimaestro/linkedin-jobs-scraper-api — returns full descriptions,
skills, salary, work type. No cookies required.

Saves normalized output to data/raw/YYYY-MM-DD_scrape.json.

Usage:
    python3 scripts/scrape_layer1.py
    python3 scripts/scrape_layer1.py --date 2026-03-10

Dependencies:
    pip install apify-client

Note on Indeed: intelligent_yaffle/indeed-jobs-scraper (original choice) requires
elevated Apify permissions. All other tested Indeed actors failed or returned
unfiltered results. apimaestro/linkedin-jobs-scraper-api returns full descriptions
and handles all 50 postings reliably. Revisit Indeed when a reliable actor is found.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from apify_client import ApifyClient

# ── Configuration ─────────────────────────────────────────────────────────────

APIFY_TOKEN = os.environ.get("APIFY_API_TOKEN", "")
ACTOR       = "apimaestro/linkedin-jobs-scraper-api"

JOB_TITLES = [
    "Revenue Operations Analyst",
    "GTM Operations",
    "Sales Operations Analyst",
    "Growth Operations",
    "Business Operations Analyst",
]

PER_TITLE = 10   # 5 titles × 10 = 50 total
LOCATION  = "United States"

ROOT    = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw"

# ── Actor call ────────────────────────────────────────────────────────────────

def run_scrape(client: ApifyClient, title: str, limit: int) -> list[dict]:
    run_input = {
        "keywords":    title,
        "location":    LOCATION,
        "limit":       limit,
        "page_number": 1,
    }
    print(f"  '{title}'  (requesting {limit})")
    run   = client.actor(ACTOR).call(run_input=run_input)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    print(f"    → {len(items)} returned")
    return items

# ── Normalization ─────────────────────────────────────────────────────────────

def normalize(raw: dict, search_query: str) -> dict:
    """Normalize apimaestro/linkedin-jobs-scraper-api output to the common schema."""
    salary = raw.get("salary") or {}
    if isinstance(salary, str):
        salary_text, sal_min, sal_max = salary, None, None
    elif isinstance(salary, dict):
        salary_text = salary.get("text") or salary.get("formatted") or ""
        sal_min     = salary.get("min") or salary.get("from")
        sal_max     = salary.get("max") or salary.get("to")
    else:
        salary_text, sal_min, sal_max = "", None, None

    return {
        "id":              str(raw.get("job_id", "")),
        "title":           raw.get("job_title", ""),
        "company":         raw.get("company", ""),
        "location":        raw.get("location", ""),
        "work_type":       raw.get("work_type", ""),        # Remote / Hybrid / On-site
        "description":     raw.get("description", ""),
        "skills":          raw.get("skills", []),
        "salary_text":     salary_text,
        "salary_min":      sal_min,
        "salary_max":      sal_max,
        "salary_currency": "USD",
        "posted_date":     str(raw.get("posted_at", "")),
        "source":          "linkedin",
        "search_query":    search_query,
        "url":             raw.get("job_url", ""),
    }


def deduplicate(postings: list[dict]) -> list[dict]:
    """Drop duplicate postings by (company, title). Keeps first occurrence."""
    seen, unique = set(), []
    for p in postings:
        key = (p["company"].lower().strip(), p["title"].lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique

# ── Main ──────────────────────────────────────────────────────────────────────

def main(run_date: str | None = None) -> None:
    target_date = run_date or date.today().isoformat()
    out_path    = RAW_DIR / f"{target_date}_scrape.json"

    if out_path.exists():
        print(f"Raw file already exists for {target_date}: {out_path}")
        print("Delete it or pass a different --date to re-scrape.")
        sys.exit(1)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = ApifyClient(APIFY_TOKEN)

    all_postings: list[dict] = []

    print(f"\n── Scraping LinkedIn  ({PER_TITLE}/title × {len(JOB_TITLES)} titles = {PER_TITLE * len(JOB_TITLES)}) ──")
    for title in JOB_TITLES:
        try:
            raw   = run_scrape(client, title, PER_TITLE)
            normd = [normalize(r, title) for r in raw]
            all_postings.extend(normd)
        except Exception as e:
            print(f"  ERROR '{title}': {e}")

    unique = deduplicate(all_postings)

    output = {
        "run_date":      target_date,
        "actor":         ACTOR,
        "total_fetched": len(all_postings),
        "total_unique":  len(unique),
        "titles":        JOB_TITLES,
        "postings":      unique,
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✓  {len(unique)} unique postings ({len(all_postings)} fetched) → {out_path}")
    print(f"   Next: python3 scripts/process_layer1.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Layer 1 scraper — pulls job postings via Apify")
    parser.add_argument("--date", help="Override run date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()
    main(args.date)
