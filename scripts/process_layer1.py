#!/usr/bin/env python3
"""
process_layer1.py — Layer 1 Digest Processor

Reads a raw scrape file, extracts structured signals from each batch of 10
postings via the Claude API, and accumulates results into
data/summaries/layer1_digest.json.

Accumulation is additive — each run merges into the existing digest rather
than overwriting it. This means the digest gets sharper as weeks of data pile up.

After processing, the raw file is moved to data/raw/archive/ and never reloaded.

Usage:
    python scripts/process_layer1.py                          # process today's scrape
    python scripts/process_layer1.py --date 2026-03-10        # process a specific date
    python scripts/process_layer1.py --file data/raw/foo.json # process an arbitrary file

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

ROOT         = Path(__file__).parent.parent
RAW_DIR      = ROOT / "data" / "raw"
ARCHIVE_DIR  = RAW_DIR / "archive"
SUMMARIES_DIR = ROOT / "data" / "summaries"
DIGEST_PATH  = SUMMARIES_DIR / "layer1_digest.json"

# ── Digest schema ─────────────────────────────────────────────────────────────

def empty_digest() -> dict:
    """Returns a fresh digest structure. Used when no prior digest exists."""
    return {
        "meta": {
            "last_updated":            "",
            "total_postings_processed": 0,
            "run_dates":               [],
        },
        # tool_name → {count, classification, first_seen, last_seen}
        "tools": {},
        # verb → count
        "workflow_ownership_verbs": {},
        # stage → count  (pre_seed / seed / series_a / series_b / series_c_plus / growth / enterprise / unknown)
        "company_stage_breakdown": {},
        # {automatable: [...], durable: [...], ai_amplified: [...]}
        "ai_exposure": {
            "automatable":  [],
            "durable":      [],
            "ai_amplified": [],
        },
        # role → {samples: [{min, max, date}], avg_min, avg_max}
        "compensation": {},
        # company_name → {posting_count, titles_seen: [...], last_seen}
        "company_velocity": {},
        # list of distinctive phrases from job descriptions
        "operator_persona_language": [],
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
You are a career intelligence analyst specializing in Revenue Operations, GTM,
and Sales Operations roles. You will receive a batch of job postings. Your job
is to extract structured signals that help a 21-year-old CS student understand
what skills, tools, and framings are in demand right now.

Return ONLY valid JSON — no markdown, no commentary, nothing outside the JSON object.

Required structure:
{
  "tools": {
    "<tool_name>": {
      "count": <integer — how many postings in this batch mention it>,
      "classification": "core" | "differentiating" | "emerging"
    }
  },
  "workflow_verbs": {
    "<verb>": <integer — count of postings using this verb to describe responsibilities>
  },
  "company_stages": {
    "<stage>": <integer — count of postings from companies at this stage>
  },
  "ai_exposure": {
    "automatable":  ["<specific task or skill AI will replace>"],
    "durable":      ["<specific task or skill that stays valuable alongside AI>"],
    "ai_amplified": ["<specific task or skill that AI makes dramatically more powerful>"]
  },
  "compensation": [
    { "role": "<job title as written>", "min": <integer or null>, "max": <integer or null> }
  ],
  "persona_language": [
    "<distinctive phrase from postings that reveals how the company frames this role>"
  ]
}

Classification guide:
- tools/core: table-stakes tools mentioned across most postings in the batch
- tools/differentiating: mentioned by specific high-growth or top-tier companies
- tools/emerging: mentioned rarely but in newer or VC-backed company postings

Company stages — use only these values:
  pre_seed, seed, series_a, series_b, series_c_plus, growth, enterprise, unknown

Workflow verbs — focus on ownership verbs: own, build, manage, architect, implement,
  optimize, partner, lead, drive, enable, design, scale, operationalize, streamline

AI exposure — ground every item in something actually mentioned in the postings.
  Do not invent hypotheticals.

Persona language — 4-8 phrases that reveal how the company thinks about this hire.
  Example: "revenue engine", "systems thinker", "cross-functional operator",
  "data-driven decision maker". Extract the actual phrases, don't paraphrase.

Compensation — extract only if a salary range is explicitly stated. Normalize to
  annual USD integers. If only hourly/monthly is given, convert. Null if absent.\
"""


def build_user_message(postings: list[dict]) -> str:
    parts = []
    for i, p in enumerate(postings, 1):
        parts.append(
            f"--- Posting {i} ---\n"
            f"Title: {p.get('title', '')}\n"
            f"Company: {p.get('company', '')}\n"
            f"Location: {p.get('location', '')}\n"
            f"Salary: {p.get('salary_text', '') or 'not stated'}\n"
            f"Description:\n{p.get('description', '')[:2000]}"  # cap per-posting at 2k chars
        )
    return "\n\n".join(parts)


def extract_signals(client: anthropic.Anthropic, postings: list[dict]) -> dict | None:
    """Call Claude API on one batch of postings. Returns parsed JSON or None on failure."""
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(postings)}],
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

    # Tools
    for tool, data in signals.get("tools", {}).items():
        tool_key = tool.lower().strip()
        if tool_key not in digest["tools"]:
            digest["tools"][tool_key] = {
                "count":          0,
                "classification": data.get("classification", "unknown"),
                "first_seen":     run_date,
                "last_seen":      run_date,
            }
        digest["tools"][tool_key]["count"]      += data.get("count", 1)
        digest["tools"][tool_key]["last_seen"]   = run_date
        # If classification was promoted (e.g., emerging → core), keep the higher signal
        classification_rank = {"core": 3, "differentiating": 2, "emerging": 1, "unknown": 0}
        existing_rank = classification_rank.get(digest["tools"][tool_key]["classification"], 0)
        new_rank      = classification_rank.get(data.get("classification", "unknown"), 0)
        if new_rank > existing_rank:
            digest["tools"][tool_key]["classification"] = data["classification"]

    # Workflow verbs
    for verb, count in signals.get("workflow_verbs", {}).items():
        verb_key = verb.lower().strip()
        digest["workflow_ownership_verbs"][verb_key] = (
            digest["workflow_ownership_verbs"].get(verb_key, 0) + count
        )

    # Company stages
    for stage, count in signals.get("company_stages", {}).items():
        stage_key = stage.lower().strip()
        digest["company_stage_breakdown"][stage_key] = (
            digest["company_stage_breakdown"].get(stage_key, 0) + count
        )

    # AI exposure — accumulate unique items per category
    for category in ("automatable", "durable", "ai_amplified"):
        existing = set(digest["ai_exposure"][category])
        for item in signals.get("ai_exposure", {}).get(category, []):
            item_clean = item.strip()
            if item_clean and item_clean not in existing:
                digest["ai_exposure"][category].append(item_clean)
                existing.add(item_clean)

    # Compensation — add samples, recompute running averages
    for entry in signals.get("compensation", []):
        role = entry.get("role", "unknown").strip()
        lo   = entry.get("min")
        hi   = entry.get("max")
        if lo is None and hi is None:
            continue
        if role not in digest["compensation"]:
            digest["compensation"][role] = {"samples": [], "avg_min": None, "avg_max": None}
        digest["compensation"][role]["samples"].append(
            {"min": lo, "max": hi, "date": run_date}
        )
        # Recompute averages from all samples
        mins = [s["min"] for s in digest["compensation"][role]["samples"] if s["min"] is not None]
        maxs = [s["max"] for s in digest["compensation"][role]["samples"] if s["max"] is not None]
        digest["compensation"][role]["avg_min"] = round(sum(mins) / len(mins)) if mins else None
        digest["compensation"][role]["avg_max"] = round(sum(maxs) / len(maxs)) if maxs else None

    # Persona language — accumulate unique phrases
    existing_phrases = set(p.lower() for p in digest["operator_persona_language"])
    for phrase in signals.get("persona_language", []):
        phrase_clean = phrase.strip()
        if phrase_clean and phrase_clean.lower() not in existing_phrases:
            digest["operator_persona_language"].append(phrase_clean)
            existing_phrases.add(phrase_clean.lower())


def update_company_velocity(digest: dict, postings: list[dict], run_date: str) -> None:
    """Track how often each company appears across all runs. Pure Python — no Claude needed."""
    for p in postings:
        company = p.get("company", "").strip()
        if not company:
            continue
        key = company.lower()
        if key not in digest["company_velocity"]:
            digest["company_velocity"][key] = {
                "display_name":  company,
                "posting_count": 0,
                "titles_seen":   [],
                "last_seen":     run_date,
            }
        digest["company_velocity"][key]["posting_count"] += 1
        digest["company_velocity"][key]["last_seen"]      = run_date
        title = p.get("title", "").strip()
        if title and title not in digest["company_velocity"][key]["titles_seen"]:
            digest["company_velocity"][key]["titles_seen"].append(title)

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
    return RAW_DIR / f"{target_date}_scrape.json"


def main(run_date: str | None = None, file_override: str | None = None) -> None:
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        print("       export ANTHROPIC_API_KEY=sk-ant-...")
        raise SystemExit(1)

    raw_path = resolve_raw_path(run_date, file_override)
    if not raw_path.exists():
        print(f"ERROR: Raw file not found: {raw_path}")
        print("       Run scrape_layer1.py first.")
        raise SystemExit(1)

    with open(raw_path) as f:
        raw_data = json.load(f)

    postings   = raw_data.get("postings", [])
    scrape_date = raw_data.get("run_date", date.today().isoformat())
    print(f"\n── Processing {len(postings)} postings from {raw_path.name} ──")
    print(f"   Batch size: {BATCH_SIZE}  |  Batches: {-(-len(postings) // BATCH_SIZE)}")

    digest = load_digest()
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Company velocity doesn't need Claude — run it first across all postings
    update_company_velocity(digest, postings, scrape_date)

    # Process in batches of BATCH_SIZE
    failed_batches = 0
    for batch_start in range(0, len(postings), BATCH_SIZE):
        batch     = postings[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total     = -(-len(postings) // BATCH_SIZE)
        print(f"\n  Batch {batch_num}/{total}  (postings {batch_start + 1}–{batch_start + len(batch)})")

        signals = extract_signals(client, batch)
        if signals is None:
            print(f"    ✗ Batch {batch_num} failed — skipping")
            failed_batches += 1
            continue

        merge_batch(digest, signals, scrape_date)
        print(f"    ✓ Merged: {len(signals.get('tools', {}))} tools, "
              f"{len(signals.get('workflow_verbs', {}))} verbs, "
              f"{sum(len(v) for v in signals.get('ai_exposure', {}).values())} AI signals")

    # Update metadata
    digest["meta"]["last_updated"]             = scrape_date
    digest["meta"]["total_postings_processed"] += len(postings)
    if scrape_date not in digest["meta"]["run_dates"]:
        digest["meta"]["run_dates"].append(scrape_date)
    digest["meta"]["run_dates"].sort()

    save_digest(digest)
    print(f"\n✓  Digest updated → {DIGEST_PATH}")
    print(f"   Total postings in digest: {digest['meta']['total_postings_processed']}")
    print(f"   Unique tools tracked:     {len(digest['tools'])}")
    print(f"   Run dates:                {', '.join(digest['meta']['run_dates'])}")

    if failed_batches:
        print(f"\n⚠  {failed_batches} batch(es) failed. Digest still saved with successful batches.")
        print(f"   Raw file NOT archived so you can retry.")
    else:
        archive_raw_file(raw_path)
        print("\nDone. Raw file archived. Run process_layer1.py again next week after the next scrape.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Layer 1 processor — extracts signals via Claude API")
    parser.add_argument("--date", help="Process scrape for YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--file", help="Process a specific raw file path (overrides --date).")
    args = parser.parse_args()
    main(run_date=args.date, file_override=args.file)
