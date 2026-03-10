#!/usr/bin/env python3
"""
layer4.py — Layer 4 Action Output

Reads synthesis_memo.md and the previous sprint card, generates a weekly
sprint card via Claude, and writes it to sprints/sprint_YYYY-MM-DD.md.

Inputs (per ARCHITECTURE.md):
  data/summaries/synthesis_memo.md — primary signal source
  sprints/sprint_*.md              — most recent previous card (for carry-forward)
  CLAUDE.md                        — always loaded
  journal.txt                      — last 7 entries (carry-forward assessment only)

Output:
  sprints/sprint_YYYY-MM-DD.md

Outreach targets are generated in Layer 5-compatible stub format:
    **Name** | Role | Company | MISSING | Rationale
Layer 5 discovers LinkedIn URLs automatically via search.

Usage:
    python3 scripts/layer4.py
    python3 scripts/layer4.py --date 2026-03-17

Dependencies:
    pip install anthropic
    export ANTHROPIC_API_KEY=...
"""

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic

# ── Configuration ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY         = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL                     = "claude-opus-4-6"   # Judgment call, not bulk processing
JOURNAL_ENTRIES_FOR_CARRY = 7

ROOT          = Path(__file__).parent.parent
SUMMARIES_DIR = ROOT / "data" / "summaries"
SPRINTS_DIR   = ROOT / "sprints"
CLAUDE_MD     = ROOT / "CLAUDE.md"
MEMO_PATH     = SUMMARIES_DIR / "synthesis_memo.md"
JOURNAL_PATH  = ROOT / "journal.txt"

# ── Prompt ────────────────────────────────────────────────────────────────────

LAYER4_SYSTEM = """\
You are the Layer 4 Action Output module of Career OS — a personal career intelligence system.

Your job is simple and constrained by design. You read the synthesis memo and produce a weekly sprint card. Nothing more. Decision fatigue is the enemy of consistency — a bloated card is a failed card.

─── SPRINT CARD FORMAT — EXACT. DO NOT ADD FIELDS. DO NOT REMOVE FIELDS. ───

# Sprint Card — {DATE}

## Learning Priority
[One specific, completable learning task this week. Not a topic — an action. 'Complete X module and produce Y artifact' is the format. Must trace directly to a signal in the synthesis memo. Cite the signal in one parenthetical.]

## Outreach Targets
1. **Name** | Role | Company | MISSING | [One sentence: why this person, why this week, what the ask is.]
2. **Name** | Role | Company | MISSING | [Same]
3. **Name** | Role | Company | MISSING | [Same]

## Portfolio Task
[One concrete improvement or new start. Specific enough that done/not-done is unambiguous at end of week. If carrying from last week, label [CARRY] and add one sentence on why it still matters.]

## Positioning Reminder
[One sentence. How to describe yourself this week based on current market signals from the memo. Not a bio — a framing lens for conversations, messages, and applications this week.]

─── OUTREACH TARGET RULES ───

Always use exactly this format: **Name** | Role | Company | MISSING | Rationale
The linkedin_url field is always the literal string MISSING — Layer 5 discovers URLs automatically.

Name field options (in order of preference):
  1. A real, specific person's name if you can identify one from your knowledge (e.g., someone you know works in RevOps at a growth-stage SaaS company)
  2. If you cannot confidently name a real person: use [Find: Role at Company] as the name — e.g., "[Find: RevOps Manager at Gong]"
Role: their current or most recent known role.
Company: their current employer.
Rationale: one sentence connecting this specific person to a signal from the synthesis memo.

─── CARRY-FORWARD RULES ───

Read the previous sprint card and the recent journal entries provided.
If an item (learning priority or portfolio task) shows no evidence of completion in the journal, carry it forward with a [CARRY] label and one sentence on why it still matters.
Do not carry more than 2 items total. If 3 or more items are unfinished, output this warning on a line by itself BEFORE the sprint card:
  ⚠ COMPLETION PROBLEM: [list the unfinished items]. Resolve these before taking on new work.
Outreach targets are never carried — new targets each week.

─── GENERAL RULES ───

Every item must trace to something specific in the synthesis memo or previous card. No generic career advice.
The positioning reminder must shift as signals shift — it is not a static tagline.
Do not add commentary, preamble, or explanation outside the card format.
Output ONLY the sprint card (and the completion warning if needed). Nothing before it, nothing after it.\
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_previous_sprint_card() -> tuple[str, str]:
    """Returns (filename, content) of the most recent sprint card, or ('', '') if none."""
    cards = sorted(SPRINTS_DIR.glob("sprint_*.md"))
    if not cards:
        return "", ""
    path = cards[-1]
    return path.name, path.read_text()


def load_journal_tail(n: int) -> str:
    if not JOURNAL_PATH.exists():
        return "Journal: no entries yet."
    with open(JOURNAL_PATH) as f:
        lines = [line.rstrip() for line in f if line.strip()]
    recent = lines[-n:]
    if not recent:
        return "Journal: no entries yet."
    return "## Recent journal entries (for carry-forward assessment only):\n" + "\n".join(recent)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(run_date: str | None = None) -> None:
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    target_date = run_date or date.today().isoformat()
    output_path = SPRINTS_DIR / f"sprint_{target_date}.md"

    if output_path.exists():
        print(f"Sprint card already exists for {target_date}: {output_path}")
        print("Delete it or pass --date with a different date to regenerate.")
        sys.exit(1)

    if not MEMO_PATH.exists():
        print(f"ERROR: synthesis_memo.md not found at {MEMO_PATH}. Run Layer 3 first.")
        sys.exit(1)

    if not CLAUDE_MD.exists():
        print(f"ERROR: CLAUDE.md not found at {CLAUDE_MD}.")
        sys.exit(1)

    print(f"\n── Layer 4 Action Output  ({target_date}) ──")
    print("  Loading inputs...")

    claude_md_text           = CLAUDE_MD.read_text()
    memo_text                = MEMO_PATH.read_text()
    prev_name, prev_card     = load_previous_sprint_card()
    journal_ctx              = load_journal_tail(JOURNAL_ENTRIES_FOR_CARRY)

    prev_section = (
        f"## Previous sprint card ({prev_name}):\n{prev_card}"
        if prev_card
        else "## Previous sprint card: none — this is the first run."
    )

    user_msg = f"""\
Today's date: {target_date}

---

{claude_md_text}

---

## Synthesis memo (primary signal source):
{memo_text}

---

{prev_section}

---

{journal_ctx}
"""

    est_tokens = (len(LAYER4_SYSTEM) + len(user_msg)) // 4
    print(f"  Context assembled (~{est_tokens:,} tokens estimated)")
    print(f"  Calling {MODEL}...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    t0     = datetime.now(timezone.utc)

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=LAYER4_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    elapsed   = (datetime.now(timezone.utc) - t0).total_seconds()
    card_text = message.content[0].text.strip()
    usage     = message.usage

    print(f"  ✓ Done in {elapsed:.1f}s | tokens: {usage.input_tokens:,} in / {usage.output_tokens:,} out")

    SPRINTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(card_text + "\n")

    print(f"  Saved → {output_path}")
    print(f"\n✓  Layer 4 complete.")
    print(f"   cat sprints/sprint_{target_date}.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Layer 4 sprint card generator")
    parser.add_argument("--date", help="Override sprint date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()
    main(run_date=args.date)
