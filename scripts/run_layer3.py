#!/usr/bin/env python3
"""
run_layer3.py — Layer 3 Synthesis Engine

Loads all upstream data, calls Claude with the synthesis prompt, writes memo.

Inputs loaded (per ARCHITECTURE.md selective context loading spec):
  data/summaries/layer1_digest.json
  data/summaries/layer2_digest.json
  data/raw/roman_profile.json
  data/raw/roman_posts.json
  Last 14 entries from journal.txt
  journal_summary.txt (if exists)

Outputs:
  data/summaries/synthesis_memo.md         — live memo (always overwritten)
  data/summaries/synthesis_memo_YYYY-MM-DD.md — versioned archive

Usage:
    python3 scripts/run_layer3.py
    python3 scripts/run_layer3.py --date 2026-03-10   # force archive date

Dependencies:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic

# ── Configuration ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL             = "claude-opus-4-6"   # Layer 3 synthesis warrants the best model
JOURNAL_ENTRIES   = 14                  # per ARCHITECTURE.md

ROOT          = Path(__file__).parent.parent
SUMMARIES_DIR = ROOT / "data" / "summaries"
RAW_DIR       = ROOT / "data" / "raw"

L1_DIGEST       = SUMMARIES_DIR / "layer1_digest.json"
L2_DIGEST       = SUMMARIES_DIR / "layer2_digest.json"
PROFILE_SUMMARY = SUMMARIES_DIR / "roman_profile_summary.json"  # summary, not raw
JOURNAL_PATH    = ROOT / "journal.txt"
SUMMARY_PATH    = ROOT / "journal_summary.txt"

MEMO_LIVE    = SUMMARIES_DIR / "synthesis_memo.md"

# ── Context builders ──────────────────────────────────────────────────────────

def load_json(path: Path, label: str) -> dict | list:
    if not path.exists():
        print(f"  WARNING: {label} not found at {path} — skipping")
        return {}
    with open(path) as f:
        return json.load(f)


def build_layer1_context(digest: dict) -> str:
    if not digest:
        return "Layer 1 digest: not available."

    tools_sorted = sorted(
        digest.get("tools", {}).items(),
        key=lambda x: x[1].get("count", 0),
        reverse=True,
    )
    top_tools = "\n".join(
        f"  {name} ({d['count']}x, {d['classification']})"
        for name, d in tools_sorted[:20]
    )

    verbs_sorted = sorted(
        digest.get("workflow_ownership_verbs", {}).items(),
        key=lambda x: x[1], reverse=True
    )
    top_verbs = ", ".join(f"{v}({c})" for v, c in verbs_sorted[:12])

    stages = digest.get("company_stage_breakdown", {})
    stage_str = ", ".join(f"{k}: {v}" for k, v in sorted(stages.items(), key=lambda x: -x[1]))

    ai = digest.get("ai_exposure", {})
    automatable  = "\n".join(f"  - {x}" for x in ai.get("automatable", [])[:8])
    ai_amplified = "\n".join(f"  - {x}" for x in ai.get("ai_amplified", [])[:8])

    comp = digest.get("compensation", {})
    comp_str = "\n".join(
        f"  {role}: ${d['avg_min']:,}–${d['avg_max']:,}" if d.get("avg_max") else f"  {role}: ${d['avg_min']:,}+"
        for role, d in list(comp.items())[:10]
        if d.get("avg_min")
    )

    persona = "\n".join(f"  \"{p}\"" for p in digest.get("operator_persona_language", [])[:12])

    meta = digest.get("meta", {})
    return f"""## Layer 1 — Market Signal Digest
Processed: {meta.get('total_postings_processed', '?')} postings | Runs: {', '.join(meta.get('run_dates', []))}

### Top tools by mention count:
{top_tools}

### Ownership verbs (frequency):
{top_verbs}

### Company stage breakdown:
{stage_str}

### AI exposure — automatable (first to go):
{automatable}

### AI exposure — AI-amplified (where to position):
{ai_amplified}

### Compensation samples:
{comp_str}

### Operator persona language (how companies frame this hire):
{persona}"""


def build_layer2_context(digest: dict) -> str:
    if not digest:
        return "Layer 2 digest: not available."

    clusters = digest.get("cohort_clusters", [])
    clusters_str = ""
    for c in sorted(clusters, key=lambda x: x.get("profile_count", 0), reverse=True)[:8]:
        moves = "; ".join(c.get("defining_moves", [])[:3])
        clusters_str += f"\n  [{c.get('profile_count', 0)} profiles] **{c['name']}**: {c.get('description','')} | Key moves: {moves}"

    bridges = digest.get("bridge_moves", {})
    bridges_str = ""
    for key, b in sorted(bridges.items(), key=lambda x: x[1].get("frequency", 0), reverse=True)[:8]:
        bridges_str += f"\n  ({b['frequency']}x) {b['from_role']} → {b['to_role']}: {b.get('notes','')}"

    launchpads = digest.get("launchpad_companies", {})
    launchpads_str = ""
    for name, l in sorted(launchpads.items(), key=lambda x: x[1].get("count", 0), reverse=True)[:8]:
        titles = ", ".join(l.get("titles_launched_from", [])[:3])
        launchpads_str += f"\n  [{l.get('count',0)}x] **{l.get('display_name', name)}**: {l.get('why_launchpad','')} | Titles: {titles}"

    anomalies = digest.get("anomalies", [])
    anomalies_str = "\n".join(
        f"  - {a['description']} → {a.get('what_they_did_differently','')}"
        for a in anomalies[:5]
    )

    tte = digest.get("time_to_target", {})
    tte_str = "\n".join(f"  {cluster}: {v.get('range','?')} (median {v.get('median_years','?')} yrs)"
                        for cluster, v in tte.items())

    lessons = "\n".join(f"  - {l}" for l in digest.get("regrets_and_lessons", [])[:6])

    meta = digest.get("meta", {})
    return f"""## Layer 2 — Career Path Intelligence
Profiles analyzed: {meta.get('total_profiles_processed', '?')} | Runs: {', '.join(meta.get('run_dates', []))}

### Cohort clusters (sorted by frequency):{clusters_str}

### Recurring bridge moves (transitions seen 2+ times):{bridges_str}

### Launchpad companies (appear repeatedly as prior employers):{launchpads_str}

### Anomalies (people who moved unusually fast or differently):
{anomalies_str}

### Time-to-target by cluster:
{tte_str}

### Lessons implied by career histories:
{lessons}

### Roman's closest cluster: {digest.get('roman_closest_cluster', 'not yet assessed')}
### Gap analysis (last batch assessment): {digest.get('roman_gap_analysis', 'not yet assessed')}"""


def build_profile_context(summary: dict) -> str:
    """
    Builds context string from roman_profile_summary.json (compressed extract).
    Never loads raw profile data directly — raw files stay in data/raw/.
    """
    if not summary:
        return "Roman's LinkedIn profile: not available. Run fetch_roman_profile.py first."

    experience_str = ""
    for e in summary.get("experience", []):
        experience_str += (
            f"\n  [{e.get('start_date','?')} – {e.get('end_date','?')}] "
            f"{e.get('title','')} @ {e.get('company','')} ({e.get('duration','')})"
        )
        if e.get("description"):
            desc = e["description"][:300].replace("\n", " ")
            experience_str += f"\n    {desc}..."

    certs_str = "\n".join(
        f"  - {c.get('name','')} ({c.get('authority','')})"
        for c in summary.get("certifications", [])
    )

    projects_str = ""
    for p in summary.get("projects", []):
        projects_str += f"\n  **{p.get('title','')}** ({p.get('duration','')})\n"
        desc = (p.get("description") or "")[:400].replace("\n", " ")
        if desc:
            projects_str += f"  {desc}...\n"

    skills_str = ", ".join(s for s in summary.get("skills", [])[:30] if s)

    posts = summary.get("recent_posts", [])
    if posts:
        posts_str = "\n".join(
            f"  [{p.get('date','')}] {p.get('text','')[:240]} ({p.get('reactions',0)} reactions)"
            for p in posts
        )
        posts_section = f"\n\n### Recent posts:\n{posts_str}"
    else:
        posts_section = "\n\n### Recent posts: none yet."

    return f"""## Roman's LinkedIn Profile (summary)
**{summary.get('name','')}** | {summary.get('headline','')}
Location: {summary.get('location','')} | Connections: {summary.get('connections','?')}
Top skills: {summary.get('top_skills','')}

### About:
{summary.get('about','')}

### Experience:{experience_str}

### Certifications:
{certs_str}

### Projects:{projects_str}
### Full skill set (top 30):
{skills_str}{posts_section}"""


def load_journal_entries(path: Path, n: int) -> str:
    if not path.exists():
        return "Journal: no entries yet."
    with open(path) as f:
        lines = [l.rstrip() for l in f if l.strip()]
    recent = lines[-n:]
    if not recent:
        return "Journal: no entries yet."
    return "## Journal (last {} entries)\n{}".format(n, "\n".join(recent))


def load_journal_summary(path: Path) -> str:
    if not path.exists():
        return ""
    with open(path) as f:
        content = f.read().strip()
    if not content:
        return ""
    return f"## Journal Summary (entries older than 60 days)\n{content}"

# ── Synthesis prompt ──────────────────────────────────────────────────────────

SYNTHESIS_PROMPT = """\
You are the Layer 3 synthesis engine for Career OS.

You are a thinking partner, not a report generator. Do not produce a templated output or ranked list. Write a conversational memo — the kind a well-informed advisor would write to a 21-year-old they genuinely want to help. Surface what's unexpected. Say what's actually interesting. Be direct.

You have access to:
- Layer 1 digest: what the market is actually asking for right now across RevOps, GTM Ops, Revenue Analytics, and Sales Ops roles
- Layer 2 digest: how people a few years ahead of Roman actually built their careers — cohort clusters, bridge moves, launchpad companies, anomalies
- Roman's full LinkedIn profile: experience, projects, skills, certifications, about section, posts
- Roman's journal: his first-person log of what's been happening, what he's noticed, what's working

Roman's context:
- 21 years old, CS junior at UW-Madison, gap semester in Prague through late April 2026
- Confirmed RevOps internship at Donaldson, Summer 2026
- Real GTM ops experience at CAUHEC Connect: outbound sequences, CRM pipeline, ReachInbox, Clay, Monday.com, Zapier
- Target: post-grad role in RevOps, GTM Analytics, or Revenue Engineering at a Series B-D SaaS or high-growth company
- Optimizing for: FIRE, high autonomy, high-leverage work, career capital that compounds
- Not optimizing for: prestige, bureaucracy, AI-replaceable tasks

Answer these questions — but only if the data gives you something real to say:
- What is the intersection of market demand and proven paths? That intersection is the learning priority.
- What's the delta between what job postings ask for and what people who actually got hired have?
- Which companies appear in both job postings AND career path profiles? Those are the highest-conviction targets.
- Given Roman's specific background, what's the narrative that makes this combination sound intentional?
- What should Roman stop doing because it no longer matters?
- Where is Roman under-positioned relative to his actual capability?
- Given what Roman has logged in his journal since last time — what's working, what isn't, what should change?
- Is the current direction still the highest-expected-value path, or is something worth reconsidering?

Write like you're sending a letter. Be specific. Reference actual signals from the data. Don't pad. If something isn't interesting, skip it.\
"""


def build_user_message(l1: str, l2: str, profile: str,
                        journal: str, journal_summary: str) -> str:
    sections = [l1, l2, profile, journal]
    if journal_summary:
        sections.append(journal_summary)
    return "\n\n---\n\n".join(sections)

# ── Main ──────────────────────────────────────────────────────────────────────

def main(run_date: str | None = None) -> None:
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        print("       export ANTHROPIC_API_KEY=sk-ant-...")
        raise SystemExit(1)

    target_date = run_date or date.today().isoformat()
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n── Layer 3 Synthesis  ({target_date}) ──")
    print("  Loading inputs...")

    l1_digest      = load_json(L1_DIGEST,       "layer1_digest")
    l2_digest      = load_json(L2_DIGEST,       "layer2_digest")
    profile_summary = load_json(PROFILE_SUMMARY, "roman_profile_summary")

    l1_ctx      = build_layer1_context(l1_digest)
    l2_ctx      = build_layer2_context(l2_digest)
    profile_ctx = build_profile_context(profile_summary)
    journal_ctx = load_journal_entries(JOURNAL_PATH, JOURNAL_ENTRIES)
    summary_ctx = load_journal_summary(SUMMARY_PATH)

    user_msg    = build_user_message(l1_ctx, l2_ctx, profile_ctx,
                                     journal_ctx, summary_ctx)

    # Rough token estimate (4 chars ≈ 1 token)
    est_tokens = (len(SYNTHESIS_PROMPT) + len(user_msg)) // 4
    print(f"  Context assembled (~{est_tokens:,} tokens estimated)")
    print(f"  Calling {MODEL}...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    t0     = datetime.now(timezone.utc)

    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYNTHESIS_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    elapsed     = (datetime.now(timezone.utc) - t0).total_seconds()
    memo_text   = message.content[0].text
    usage       = message.usage
    input_tok   = usage.input_tokens
    output_tok  = usage.output_tokens

    print(f"  ✓ Done in {elapsed:.1f}s | tokens: {input_tok:,} in / {output_tok:,} out")

    # Build memo with header
    header = (
        f"# Synthesis Memo\n"
        f"Generated: {target_date} | Model: {MODEL} | "
        f"Tokens: {input_tok:,} in / {output_tok:,} out\n\n"
        f"---\n\n"
    )
    full_memo = header + memo_text

    # Save live memo (always overwrites)
    with open(MEMO_LIVE, "w") as f:
        f.write(full_memo)
    print(f"  Saved → {MEMO_LIVE}")

    # Save versioned archive
    archive_path = SUMMARIES_DIR / f"synthesis_memo_{target_date}.md"
    with open(archive_path, "w") as f:
        f.write(full_memo)
    print(f"  Saved → {archive_path}")

    print(f"\n✓  Layer 3 complete. Read the memo:")
    print(f"   cat data/summaries/synthesis_memo.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Layer 3 synthesis engine")
    parser.add_argument("--date", help="Override memo date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()
    main(run_date=args.date)
