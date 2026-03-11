#!/usr/bin/env python3
"""
fetch_roman_profile.py — Layer 3 Profile Fetcher

Scrapes Roman's LinkedIn profile and posts via Apify, saves complete raw output.
Called automatically by run_layer3_pipeline.py or manually before run_layer3.py.

Actors used (both PAY_PER_EVENT, no rental required):
  harvestapi/linkedin-profile-scraper  — full profile, career, projects, skills
  harvestapi/linkedin-profile-posts    — recent posts and engagement

Saves:
  data/raw/roman_profile.json              — complete raw profile (never loaded into context)
  data/raw/roman_posts.json               — raw posts list (never loaded into context)
  data/summaries/roman_profile_summary.json — compressed extract for Layer 3 synthesis

Usage:
    python3 scripts/fetch_roman_profile.py

Dependencies:
    pip install apify-client
    export APIFY_API_TOKEN=...

Actor input schemas (confirmed 2026-03-10):
    linkedin-profile-scraper: { urls: [str], maxItems: int }
    linkedin-profile-posts:   { profileUrl: str, maxResults: int }  ← field TBD,
                               falls back to probing if first attempt returns nothing
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from apify_client import ApifyClient

# ── Configuration ─────────────────────────────────────────────────────────────

APIFY_TOKEN  = os.environ.get("APIFY_API_TOKEN", "")
ROMAN_URL    = "https://www.linkedin.com/in/roman-licursi-3aab2a160/"

ACTOR_PROFILE = "harvestapi/linkedin-profile-scraper"
ACTOR_POSTS   = "harvestapi/linkedin-profile-posts"

ROOT          = Path(__file__).parent.parent
RAW_DIR       = ROOT / "data" / "raw"
SUMMARIES_DIR = ROOT / "data" / "summaries"
PROFILE_PATH  = RAW_DIR / "roman_profile.json"
POSTS_PATH    = RAW_DIR / "roman_posts.json"
SUMMARY_PATH  = SUMMARIES_DIR / "roman_profile_summary.json"

# ── Helpers ───────────────────────────────────────────────────────────────────

def run_actor(client: ApifyClient, actor: str, run_input: dict,
              label: str, timeout: int = 120) -> tuple[list[dict], dict]:
    """Run an actor, return (items, run_meta). Raises on failure."""
    print(f"  [{label}] Starting actor: {actor}")
    run = client.actor(actor).call(run_input=run_input, timeout_secs=timeout)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

    # Collect cost metadata from run record
    run_record = client.run(run["id"]).get() or {}
    usage = run_record.get("stats", {})
    cost  = run_record.get("usageTotalUsd") or run_record.get("costUsd")

    meta = {
        "run_id":        run["id"],
        "actor":         actor,
        "status":        run_record.get("status", "UNKNOWN"),
        "items_returned": len(items),
        "cost_usd":      cost,
        "compute_units": usage.get("computeUnits"),
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
    }
    print(f"  [{label}] ✓ {len(items)} item(s) | cost: ${cost or 'N/A'}")
    return items, meta


def probe_posts_input(client: ApifyClient) -> list[dict]:
    """
    linkedin-profile-posts input schema isn't documented. Try likely field names
    in order, return items from whichever works first.
    """
    candidates = [
        {"profileUrl": ROMAN_URL, "maxResults": 20},
        {"profileUrls": [ROMAN_URL], "maxResults": 20},
        {"urls": [ROMAN_URL], "maxResults": 20},
        {"url": ROMAN_URL, "maxResults": 20},
        {"linkedinUrl": ROMAN_URL, "maxResults": 20},
    ]
    for inp in candidates:
        field = list(inp.keys())[0]
        try:
            run = client.actor(ACTOR_POSTS).call(run_input=inp, timeout_secs=90)
            items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
            log   = client.log(run["id"]).get() or ""
            # Treat "no query" / "nothing to scrape" style logs as misses
            if items:
                print(f"  [posts] Input field '{field}' worked — {len(items)} posts")
                return items
            if any(w in log.lower() for w in ["no url", "no profile", "nothing", "skip"]):
                continue
            # Ran but returned nothing (profile may have no posts)
            print(f"  [posts] Input field '{field}' accepted, 0 posts returned (no activity?)")
            return []
        except Exception as e:
            if "permissions" in str(e).lower() or "rent" in str(e).lower():
                raise
    print("  [posts] Could not determine correct input field — returning empty list")
    return []

# ── Profile compression ───────────────────────────────────────────────────────

def compress_profile(profile: dict, posts: list[dict]) -> None:
    """
    Extract synthesis-relevant fields from raw profile + posts into a lean summary.
    Saves to data/summaries/roman_profile_summary.json.
    Raw files remain in data/raw/ and are never passed to Claude directly.
    """
    def _date_text(d: dict | None) -> str:
        return (d or {}).get("text") or "?"

    experience = []
    for e in profile.get("experience") or []:
        experience.append({
            "title":       e.get("position") or "",
            "company":     e.get("companyName") or "",
            "start_date":  _date_text(e.get("startDate")),
            "end_date":    _date_text(e.get("endDate")),
            "duration":    e.get("duration") or "",
            "description": (e.get("description") or "")[:600],
        })

    projects = []
    for p in profile.get("projects") or []:
        projects.append({
            "title":       p.get("title") or "",
            "duration":    p.get("duration") or "",
            "description": (p.get("description") or "")[:800],
        })

    certifications = [
        {"name": c.get("name") or "", "authority": c.get("authority") or ""}
        for c in profile.get("certifications") or []
    ]

    skills = [
        (s.get("name") if isinstance(s, dict) else s)
        for s in (profile.get("skills") or [])[:40]
    ]

    recent_posts = []
    for p in posts[:10]:
        text = (p.get("text") or p.get("content") or p.get("commentary") or "")[:300]
        if text:
            recent_posts.append({
                "text":      text,
                "date":      p.get("postedAt") or p.get("date") or "",
                "reactions": p.get("totalReactionCount") or p.get("reactions") or 0,
            })

    location = profile.get("location") or {}
    summary = {
        "_compressed_at": datetime.now(timezone.utc).isoformat(),
        "name":           f"{profile.get('firstName','')} {profile.get('lastName','')}".strip(),
        "headline":       profile.get("headline") or "",
        "location":       location.get("city") or location.get("countryCode") or "",
        "connections":    profile.get("connectionsCount"),
        "top_skills":     profile.get("topSkills") or "",
        "about":          profile.get("about") or "",
        "experience":     experience,
        "projects":       projects,
        "certifications": certifications,
        "skills":         skills,
        "recent_posts":   recent_posts,
    }

    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  [compress] Profile summary → {SUMMARY_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = ApifyClient(APIFY_TOKEN)

    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"\n── Fetching Roman's LinkedIn profile  ({timestamp}) ──")

    # ── Profile scrape ──
    profile_items, profile_meta = run_actor(
        client, ACTOR_PROFILE,
        run_input={"urls": [ROMAN_URL], "maxItems": 1},
        label="profile",
    )

    if not profile_items:
        print("ERROR: Profile scrape returned no items. Check actor or URL.")
        sys.exit(1)

    profile_data = profile_items[0]
    profile_data["_fetch_meta"] = profile_meta

    with open(PROFILE_PATH, "w") as f:
        json.dump(profile_data, f, indent=2)
    print(f"  [profile] Saved → {PROFILE_PATH}")

    # ── Posts scrape ──
    print(f"  [posts] Starting actor: {ACTOR_POSTS}")
    try:
        posts_items = probe_posts_input(client)
    except Exception as e:
        print(f"  [posts] ERROR: {e} — saving empty array")
        posts_items = []

    posts_output = {
        "_fetch_meta": {
            "actor":      ACTOR_POSTS,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "count":      len(posts_items),
        },
        "posts": posts_items,
    }

    with open(POSTS_PATH, "w") as f:
        json.dump(posts_output, f, indent=2)
    print(f"  [posts] Saved → {POSTS_PATH}  ({len(posts_items)} posts)")

    # ── Compress raw → summary (this is what Layer 3 actually reads) ──
    compress_profile(profile_data, posts_items)

    print(f"\n✓  Profile fetch complete.")
    print(f"   Profile cost: ${profile_meta.get('cost_usd', 'N/A')}")
    print(f"   Next: python3 scripts/run_layer3.py")


if __name__ == "__main__":
    main()
