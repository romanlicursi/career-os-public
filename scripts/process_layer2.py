#!/usr/bin/env python3
"""
process_layer2.py — Layer 2 Digest Processor

Reads a raw layer2 scrape file, extracts structured career path signals from
each batch of 10 profiles via the Claude API, and accumulates results into
data/summaries/layer2_digest.json.

Accumulation is additive — each run merges into the existing digest rather than
overwriting it. The digest gets sharper as more profiles are processed over time.

After processing, the raw file is moved to data/raw/archive/ and never reloaded.

Usage:
    python3 scripts/process_layer2.py                          # process today's scrape
    python3 scripts/process_layer2.py --date 2026-03-10        # process a specific date
    python3 scripts/process_layer2.py --file data/raw/foo.json # process an arbitrary file

Dependencies:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import json
import os
import re
import shutil
from collections import defaultdict
from datetime import date
from pathlib import Path

import anthropic

# ── Configuration ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL             = "claude-sonnet-4-6"
BATCH_SIZE        = 10

ROOT          = Path(__file__).parent.parent
RAW_DIR       = ROOT / "data" / "raw"
ARCHIVE_DIR   = RAW_DIR / "archive"
SUMMARIES_DIR = ROOT / "data" / "summaries"
DIGEST_PATH   = SUMMARIES_DIR / "layer2_digest.json"

# ── Digest schema ─────────────────────────────────────────────────────────────

def empty_digest() -> dict:
    """Returns a fresh digest structure. Used when no prior digest exists."""
    return {
        "meta": {
            "last_updated":             "",
            "total_profiles_processed": 0,
            "run_dates":                [],
        },
        # list of cluster objects: {name, description, defining_moves, profile_count}
        "cohort_clusters": [],
        # bridge_move key → {from_role, to_role, frequency, notes}
        "bridge_moves": {},
        # company_name → {count, titles_launched_from, why_launchpad}
        "launchpad_companies": {},
        # list of anomaly objects: {description, what_they_did_differently}
        "anomalies": [],
        # {cluster_name → median_years_to_target}
        "time_to_target": {},
        # list of distilled lessons from profiles
        "regrets_and_lessons": [],
        # which cluster Roman is closest to — updated each run
        "roman_closest_cluster": "",
        # one-paragraph gap analysis updated each run
        "roman_gap_analysis":   "",
    }


def load_digest() -> dict:
    if DIGEST_PATH.exists():
        with open(DIGEST_PATH) as f:
            return json.load(f)
    return empty_digest()


def save_digest(digest: dict) -> None:
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    with open(DIGEST_PATH, "w") as f:
        json.dump(digest, f, indent=2)

# ── Claude extraction prompt ──────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a career path analyst. You will receive a batch of LinkedIn career histories
for people currently in Strategy & Operations, Growth Operations, Revenue Operations,
or GTM Engineering roles at companies with under 1000 employees.

Your job is to extract structured career path intelligence that helps a 21-year-old
CS student understand how people in these roles actually got there — not what they
claim in a bio, but what their actual career sequence looks like.

Return ONLY valid JSON — no markdown, no commentary, nothing outside the JSON object.

Required structure:
{
  "cohort_clusters": [
    {
      "name": "<short cluster label, e.g. 'BDR-to-Ops Transition'>",
      "description": "<1-2 sentences: what defines this path>",
      "defining_moves": ["<specific transition or role that characterizes this cluster>"],
      "profile_count": <integer — how many profiles in this batch fit this cluster>
    }
  ],
  "bridge_moves": [
    {
      "from_role": "<role title or function>",
      "to_role": "<role title or function>",
      "frequency": <integer — how many times this transition appeared in this batch>,
      "notes": "<what made this transition work — specific pattern, company type, or timing>"
    }
  ],
  "launchpad_companies": [
    {
      "company": "<company name>",
      "titles_launched_from": ["<role titles seen at this company before target role>"],
      "why_launchpad": "<one sentence: what this company gives you that others don't>"
    }
  ],
  "anomalies": [
    {
      "description": "<what made this person's path unusual — be specific>",
      "what_they_did_differently": "<the specific move, timing, or framing that was different>"
    }
  ],
  "time_to_target": [
    {
      "cluster": "<cluster name>",
      "median_years": <number — median years from graduation/start to target role>,
      "range": "<e.g. '2-4 years'>"
    }
  ],
  "regrets_and_lessons": [
    "<distilled insight implied by the career history — what would this person have done differently?>"
  ],
  "roman_assessment": {
    "closest_cluster": "<which cluster Roman is closest to, given CS background + outbound ops + RevOps internship>",
    "gap_analysis": "<one paragraph: what Roman is missing relative to this cluster's typical path, and the 1-2 highest-leverage moves to close the gap>"
  }
}

Extraction rules:
- Ground every insight in something visible in the career history. Do not invent.
- For bridge_moves: focus on the transitions that appear 2+ times. Single occurrences
  are noise unless they're strikingly unusual.
- For launchpad_companies: look for companies that appear repeatedly across profiles
  as a prior employer before the target role.
- For anomalies: flag anyone who reached a target role in under 2 years from a
  non-traditional background, or anyone with an unusual combination of prior roles.
- For time_to_target: count from the first full-time role after education to the
  first occurrence of a target-type role (Strategy & Ops, Growth Ops, RevOps, GTM).
- For roman_assessment: Roman is a 21-year-old CS junior at UW-Madison with
  real-world outbound ops experience at a healthcare nonprofit, a confirmed Revenue
  Operations internship at Donaldson (summer 2026), and is currently building a
  career intelligence system using AI and Python. He is NOT pursuing a default SWE
  path. He is optimizing for autonomy, high leverage, and FIRE alignment.\
"""


def build_user_message(profiles: list[dict]) -> str:
    parts = []
    for i, p in enumerate(profiles, 1):
        career_str = "\n".join(
            f"  [{r.get('start_date', '?')} – {r.get('end_date', '?')}] "
            f"{r.get('title', '')} @ {r.get('company', '')} "
            f"({r.get('company_size', 'size unknown')} employees, {r.get('location', '')})"
            for r in p.get("career", [])
        )
        parts.append(
            f"--- Profile {i} ---\n"
            f"Headline: {p.get('headline', '')}\n"
            f"Career history:\n{career_str}"
        )
    return "\n\n".join(parts)


def extract_signals(client: anthropic.Anthropic, profiles: list[dict]) -> dict | None:
    """Call Claude API on one batch of profiles. Returns parsed JSON or None on failure."""
    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(profiles)}],
    )
    raw_text = message.content[0].text.strip()

    # Strip any accidental markdown fences
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"    JSON parse error: {e}")
        print(f"    Raw response (first 500 chars): {raw_text[:500]}")
        return None

# ── Accumulation logic ────────────────────────────────────────────────────────

def merge_batch(digest: dict, signals: dict, run_date: str) -> None:
    """Merge one batch's extracted signals into the running digest."""

    # Cohort clusters — merge by name, accumulate profile_count
    existing_clusters = {c["name"]: c for c in digest["cohort_clusters"]}
    for cluster in signals.get("cohort_clusters", []):
        name = cluster.get("name", "").strip()
        if not name:
            continue
        if name in existing_clusters:
            existing_clusters[name]["profile_count"] += cluster.get("profile_count", 0)
            # Merge defining_moves — keep unique
            existing_moves = set(existing_clusters[name]["defining_moves"])
            for move in cluster.get("defining_moves", []):
                if move not in existing_moves:
                    existing_clusters[name]["defining_moves"].append(move)
                    existing_moves.add(move)
        else:
            existing_clusters[name] = {
                "name":          name,
                "description":   cluster.get("description", ""),
                "defining_moves": cluster.get("defining_moves", []),
                "profile_count": cluster.get("profile_count", 0),
            }
    digest["cohort_clusters"] = list(existing_clusters.values())

    # Bridge moves — merge by (from_role, to_role) key, accumulate frequency
    for move in signals.get("bridge_moves", []):
        from_r = move.get("from_role", "").strip()
        to_r   = move.get("to_role", "").strip()
        if not from_r or not to_r:
            continue
        key = f"{from_r.lower()} → {to_r.lower()}"
        if key not in digest["bridge_moves"]:
            digest["bridge_moves"][key] = {
                "from_role": from_r,
                "to_role":   to_r,
                "frequency": 0,
                "notes":     move.get("notes", ""),
            }
        digest["bridge_moves"][key]["frequency"] += move.get("frequency", 1)

    # Launchpad companies — merge by company name, accumulate
    for entry in signals.get("launchpad_companies", []):
        company = entry.get("company", "").strip()
        if not company:
            continue
        key = company.lower()
        if key not in digest["launchpad_companies"]:
            digest["launchpad_companies"][key] = {
                "display_name":       company,
                "count":              0,
                "titles_launched_from": [],
                "why_launchpad":      entry.get("why_launchpad", ""),
            }
        digest["launchpad_companies"][key]["count"] += 1
        existing_titles = set(t.lower() for t in digest["launchpad_companies"][key]["titles_launched_from"])
        for title in entry.get("titles_launched_from", []):
            if title.lower() not in existing_titles:
                digest["launchpad_companies"][key]["titles_launched_from"].append(title)
                existing_titles.add(title.lower())

    # Anomalies — accumulate unique descriptions
    existing_anomaly_descriptions = {a["description"] for a in digest["anomalies"]}
    for anomaly in signals.get("anomalies", []):
        desc = anomaly.get("description", "").strip()
        if desc and desc not in existing_anomaly_descriptions:
            digest["anomalies"].append({
                "description":            desc,
                "what_they_did_differently": anomaly.get("what_they_did_differently", ""),
            })
            existing_anomaly_descriptions.add(desc)

    # Time to target — update by cluster name (keep latest)
    for entry in signals.get("time_to_target", []):
        cluster = entry.get("cluster", "").strip()
        if cluster:
            digest["time_to_target"][cluster] = {
                "median_years": entry.get("median_years"),
                "range":        entry.get("range", ""),
            }

    # Regrets and lessons — accumulate unique items
    existing_lessons = set(digest["regrets_and_lessons"])
    for lesson in signals.get("regrets_and_lessons", []):
        lesson_clean = lesson.strip()
        if lesson_clean and lesson_clean not in existing_lessons:
            digest["regrets_and_lessons"].append(lesson_clean)
            existing_lessons.add(lesson_clean)

    # Roman assessment — overwrite each run (most recent batch has freshest view)
    assessment = signals.get("roman_assessment", {})
    if assessment.get("closest_cluster"):
        digest["roman_closest_cluster"] = assessment["closest_cluster"]
    if assessment.get("gap_analysis"):
        digest["roman_gap_analysis"] = assessment["gap_analysis"]


# ── Archive ───────────────────────────────────────────────────────────────────

def archive_raw_file(raw_path: Path) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / raw_path.name
    shutil.move(str(raw_path), str(dest))
    print(f"  Archived raw file → {dest}")

# ── Main ──────────────────────────────────────────────────────────────────────

def resolve_raw_path(run_date: str | None, file_override: str | None) -> Path:
    if file_override:
        return Path(file_override)
    target_date = run_date or date.today().isoformat()
    return RAW_DIR / f"{target_date}_layer2_scrape.json"


def main(run_date: str | None = None, file_override: str | None = None) -> None:
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        print("       export ANTHROPIC_API_KEY=sk-ant-...")
        raise SystemExit(1)

    raw_path = resolve_raw_path(run_date, file_override)
    if not raw_path.exists():
        print(f"ERROR: Raw file not found: {raw_path}")
        print("       Run scrape_layer2.py first.")
        raise SystemExit(1)

    with open(raw_path) as f:
        raw_data = json.load(f)

    profiles    = raw_data.get("profiles", [])
    scrape_date = raw_data.get("run_date", date.today().isoformat())

    print(f"\n── Processing {len(profiles)} profiles from {raw_path.name} ──")
    print(f"   Batch size: {BATCH_SIZE}  |  Batches: {-(-len(profiles) // BATCH_SIZE)}")

    digest = load_digest()
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    failed_batches = 0
    for batch_start in range(0, len(profiles), BATCH_SIZE):
        batch     = profiles[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total     = -(-len(profiles) // BATCH_SIZE)
        print(f"\n  Batch {batch_num}/{total}  (profiles {batch_start + 1}–{batch_start + len(batch)})")

        signals = extract_signals(client, batch)
        if signals is None:
            print(f"    ✗ Batch {batch_num} failed — skipping")
            failed_batches += 1
            continue

        merge_batch(digest, signals, scrape_date)
        print(
            f"    ✓ Merged: "
            f"{len(signals.get('cohort_clusters', []))} clusters, "
            f"{len(signals.get('bridge_moves', []))} bridge moves, "
            f"{len(signals.get('launchpad_companies', []))} launchpads, "
            f"{len(signals.get('anomalies', []))} anomalies"
        )

    # Update metadata
    digest["meta"]["last_updated"]              = scrape_date
    digest["meta"]["total_profiles_processed"] += len(profiles)
    if scrape_date not in digest["meta"]["run_dates"]:
        digest["meta"]["run_dates"].append(scrape_date)
    digest["meta"]["run_dates"].sort()

    save_digest(digest)

    print(f"\n✓  Digest updated → {DIGEST_PATH}")
    print(f"   Total profiles in digest: {digest['meta']['total_profiles_processed']}")
    print(f"   Cohort clusters:          {len(digest['cohort_clusters'])}")
    print(f"   Bridge moves tracked:     {len(digest['bridge_moves'])}")
    print(f"   Launchpad companies:      {len(digest['launchpad_companies'])}")
    print(f"   Run dates:                {', '.join(digest['meta']['run_dates'])}")

    if failed_batches:
        print(f"\n⚠  {failed_batches} batch(es) failed. Digest still saved with successful batches.")
        print(f"   Raw file NOT archived so you can retry.")
    else:
        archive_raw_file(raw_path)
        print("\nDone. Raw file archived. Run process_layer2.py again after the next scrape.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Layer 2 processor — extracts career path signals via Claude API")
    parser.add_argument("--date", help="Process scrape for YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--file", help="Process a specific raw file path (overrides --date).")
    args = parser.parse_args()
    main(run_date=args.date, file_override=args.file)
