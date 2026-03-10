#!/usr/bin/env python3
"""
run_layer3_pipeline.py — Layer 3 Pipeline Entry Point

Runs the full Layer 3 pipeline in sequence:
  1. fetch_roman_profile.py  — scrape Roman's LinkedIn profile + posts
  2. run_layer3.py           — synthesize all data into memo

Usage:
    python3 scripts/run_layer3_pipeline.py
    python3 scripts/run_layer3_pipeline.py --skip-fetch   # use existing profile data
    python3 scripts/run_layer3_pipeline.py --date 2026-03-10

Dependencies:
    export ANTHROPIC_API_KEY=sk-ant-...
    export APIFY_API_TOKEN=...   (or hardcoded fallback in fetch_roman_profile.py)
"""

import argparse
import sys
from pathlib import Path

# Allow importing sibling scripts directly
sys.path.insert(0, str(Path(__file__).parent))

import fetch_roman_profile
import run_layer3


def main(skip_fetch: bool = False, run_date: str | None = None) -> None:
    print("══════════════════════════════════════════")
    print("  Career OS — Layer 3 Pipeline")
    print("══════════════════════════════════════════")

    if not skip_fetch:
        print("\n[Step 1/2] Fetching Roman's LinkedIn profile...")
        fetch_roman_profile.main()
    else:
        print("\n[Step 1/2] Skipped (--skip-fetch). Using existing profile data.")

    print("\n[Step 2/2] Running synthesis...")
    run_layer3.main(run_date=run_date)

    print("\n══════════════════════════════════════════")
    print("  Pipeline complete.")
    print("══════════════════════════════════════════")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Layer 3 pipeline — fetch profile then synthesize")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip profile fetch, use existing data/raw/roman_profile.json")
    parser.add_argument("--date", help="Override memo date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()
    main(skip_fetch=args.skip_fetch, run_date=args.date)
