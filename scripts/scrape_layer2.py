#!/usr/bin/env python3
"""
scrape_layer2.py — Layer 2 Qualitative Intelligence Scraper (Stages 1 + 2)

Actor: harvestapi/linkedin-profile-search (PAY_PER_EVENT, no rental required)
  - Takes searchQuery + maxItems
  - Returns full profile data including career history in one call
  - Stages 1 and 2 are combined: discovery and scraping happen together

Stage 1+2 — Discovery + Career Scraping (harvestapi/linkedin-profile-search):
    Searches LinkedIn for people in target roles. Each result includes full
    career history (positions, companies, dates). Profiles from companies with
    >1000 employees are dropped post-hoc via headline/role heuristics since the
    actor does not expose company employee count directly.

    Saves:
      - Profile URLs → data/raw/layer2_profiles.json
      - Normalized career histories → data/raw/YYYY-MM-DD_layer2_scrape.json

Usage:
    python3 scripts/scrape_layer2.py              # run full scrape
    python3 scripts/scrape_layer2.py --date 2026-03-10
    python3 scripts/scrape_layer2.py --dry-run    # print config, don't call Apify

Dependencies:
    pip install apify-client

Actor input schema (confirmed via test run 2026-03-10):
    searchQuery: str   — search keywords
    maxItems:    int   — max profiles to return per run
    location:    str   — optional location filter (not always respected)

Output fields used:
    linkedinUrl, firstName, lastName, headline
    experience[].position, .companyName, .startDate.text, .endDate.text,
    .duration, .location
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
ACTOR       = "harvestapi/linkedin-profile-search"

# Titles that define the target cohort
TARGET_TITLES = [
    "Strategy and Operations",
    "Growth Operations",
    "Revenue Operations",
    "GTM Engineer",
]

# Profiles requested per title query. 4 titles × 8 = 32 before dedup.
PER_QUERY        = 8
COMPANY_SIZE_CAP = 1000  # approximate filter via headline heuristics (see below)

# Keywords that suggest a large-enterprise employer — used to soft-filter profiles
LARGE_ENTERPRISE_SIGNALS = [
    "amazon", "google", "microsoft", "meta", "apple", "netflix", "salesforce",
    "oracle", "sap", "ibm", "accenture", "deloitte", "mckinsey", "bain", "bcg",
]

ROOT          = Path(__file__).parent.parent
RAW_DIR       = ROOT / "data" / "raw"
PROFILES_PATH = RAW_DIR / "layer2_profiles.json"

# ── Normalization ──────────────────────────────────────────────────────────────

def normalize_experience(raw_exp: list[dict]) -> list[dict]:
    """Convert harvestapi experience items to canonical career format."""
    career = []
    for pos in raw_exp:
        start = pos.get("startDate") or {}
        end   = pos.get("endDate") or {}
        career.append({
            "title":        pos.get("position") or "",
            "company":      pos.get("companyName") or "",
            "start_date":   start.get("text") or "?",
            "end_date":     end.get("text") or "present",
            "duration":     pos.get("duration") or "",
            "location":     pos.get("location") or "",
        })
    return career


def is_large_enterprise(profile: dict) -> bool:
    """
    Heuristic filter for large employers. Since the actor doesn't return company
    employee count, we flag profiles whose current company name matches a known
    large-enterprise signal. This keeps the cohort focused on <1000-employee orgs.
    """
    current = profile.get("currentPosition") or []
    company = (current[0].get("companyName") or "").lower() if current else ""
    headline = (profile.get("headline") or "").lower()
    return any(sig in company or sig in headline for sig in LARGE_ENTERPRISE_SIGNALS)


def normalize_profile(raw: dict) -> dict | None:
    """Extract career-only profile. Returns None if profile should be excluded."""
    experience = raw.get("experience") or []
    if not experience:
        return None
    if is_large_enterprise(raw):
        return None
    return {
        "profile_url": raw.get("linkedinUrl") or "",
        "name":        f"{raw.get('firstName','')} {raw.get('lastName','')}".strip(),
        "headline":    raw.get("headline") or "",
        "career":      normalize_experience(experience),
    }

# ── Actor call ────────────────────────────────────────────────────────────────

def run_search(client: ApifyClient, title: str, max_items: int) -> list[dict]:
    run_input = {
        "searchQuery": title,
        "maxItems":    max_items,
    }
    print(f"  '{title}'  (requesting {max_items})")
    run   = client.actor(ACTOR).call(run_input=run_input, timeout_secs=300)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    print(f"    → {len(items)} returned")
    return items

# ── Main ──────────────────────────────────────────────────────────────────────

def main(run_date: str | None = None, dry_run: bool = False) -> None:
    target_date = run_date or date.today().isoformat()
    out_path    = RAW_DIR / f"{target_date}_layer2_scrape.json"

    if out_path.exists():
        print(f"Scrape file already exists for {target_date}: {out_path}")
        print("Delete it or pass a different --date to re-scrape.")
        sys.exit(1)

    print(f"\n── Layer 2 Scrape  ({len(TARGET_TITLES)} queries × {PER_QUERY} = ~{len(TARGET_TITLES) * PER_QUERY} profiles) ──")
    print(f"   Actor: {ACTOR}")
    if dry_run:
        print("   DRY RUN — not calling Apify")
        return

    client = ApifyClient(APIFY_TOKEN)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    seen_urls:     set[str] = set()
    all_urls:      list[str] = []
    all_profiles:  list[dict] = []
    skipped = 0

    for title in TARGET_TITLES:
        try:
            raw_items = run_search(client, title, PER_QUERY)
        except Exception as e:
            print(f"  ERROR on '{title}': {e}")
            continue

        for raw in raw_items:
            url = (raw.get("linkedinUrl") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            all_urls.append(url)

            profile = normalize_profile(raw)
            if profile is None:
                skipped += 1
            else:
                all_profiles.append(profile)

    print(f"\n  Kept {len(all_profiles)} profiles, skipped {skipped} "
          f"(no career data or large-enterprise filter)")

    if not all_profiles:
        print("No profiles to save. Check actor output or filter settings.")
        sys.exit(1)

    # Save profile URLs (Stage 1 artifact)
    with open(PROFILES_PATH, "w") as f:
        json.dump({
            "run_date":       target_date,
            "profile_count":  len(all_urls),
            "profile_urls":   all_urls,
        }, f, indent=2)
    print(f"  Profile URLs → {PROFILES_PATH}")

    # Save normalized career data (Stage 2 artifact)
    with open(out_path, "w") as f:
        json.dump({
            "run_date":       target_date,
            "actor":          ACTOR,
            "total_profiles": len(all_profiles),
            "profiles":       all_profiles,
        }, f, indent=2)

    print(f"  Career data  → {out_path}")
    print(f"\n✓  {len(all_profiles)} profiles ready. Next: python3 scripts/process_layer2.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Layer 2 scraper — discovers and scrapes LinkedIn profiles via Apify"
    )
    parser.add_argument("--date",    help="Override run date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--dry-run", action="store_true", help="Print config without calling Apify.")
    args = parser.parse_args()
    main(run_date=args.date, dry_run=args.dry_run)
