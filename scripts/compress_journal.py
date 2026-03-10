#!/usr/bin/env python3
"""
Layer 6 — Journal Compression Script
Runs monthly. Identifies journal entries older than 60 days, summarizes them
via Claude API, archives them, and regenerates journal_summary.txt.
"""

import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict

import anthropic

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "journal.txt"
SUMMARY = ROOT / "journal_summary.txt"
ARCHIVE_DIR = ROOT / "data" / "raw" / "journal_archive"
LOG = ROOT / "data" / "compression_log.txt"

CUTOFF = date.today() - timedelta(days=60)
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):\s*(.*)")


def log(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def parse_journal(path: Path) -> list[tuple[date, str]]:
    """Return list of (entry_date, full_line) tuples."""
    entries = []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            m = DATE_RE.match(line)
            if m:
                entry_date = date.fromisoformat(m.group(1))
                entries.append((entry_date, line))
            else:
                # Attach orphan lines to the last known entry
                if entries:
                    last_date, last_text = entries[-1]
                    entries[-1] = (last_date, last_text + "\n" + line)
    return entries


def summarize(entries: list[tuple[date, str]]) -> str:
    client = anthropic.Anthropic()
    raw_text = "\n".join(line for _, line in entries)

    prompt = f"""You are summarizing a personal career journal for Roman, a 21-year-old CS student building a career in Revenue Operations and GTM systems.

Below are journal entries spanning {entries[0][0]} to {entries[-1][0]}. Each entry is a date + one or two sentences logged in real time.

Produce a concise, compressed summary that preserves:
1. Key decisions made and their stated reasoning
2. Market signals noticed (tools, roles, companies, trends mentioned)
3. Mindset shifts or perspective changes
4. Outreach outcomes and what they revealed

Rules:
- Write in past tense, third person ("Roman noticed...", "Roman decided...")
- Preserve specifics — named tools, companies, people, numbers — do not generalize them away
- Keep the total summary under 400 words
- Do not editorialize or add advice
- Group by theme, not chronologically

Journal entries:
{raw_text}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def write_archive(entries: list[tuple[date, str]]) -> None:
    """Group entries by YYYY-MM and write each group to its archive file."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[str]] = defaultdict(list)
    for entry_date, line in entries:
        key = entry_date.strftime("%Y-%m")
        by_month[key].append(line)

    for month, lines in by_month.items():
        archive_file = ARCHIVE_DIR / f"{month}-archive.txt"
        mode = "a" if archive_file.exists() else "w"
        with open(archive_file, mode) as f:
            f.write("\n".join(lines) + "\n")
        log(f"Archived {len(lines)} entries → {archive_file.name}")


def rewrite_journal(kept: list[tuple[date, str]]) -> None:
    with open(JOURNAL, "w") as f:
        f.write("\n".join(line for _, line in kept) + "\n")


def main() -> None:
    if not JOURNAL.exists():
        log("journal.txt not found — nothing to compress.")
        sys.exit(0)

    entries = parse_journal(JOURNAL)
    if not entries:
        print("journal.txt is empty — nothing to compress.")
        sys.exit(0)

    old = [(d, l) for d, l in entries if d < CUTOFF]
    kept = [(d, l) for d, l in entries if d >= CUTOFF]

    if not old:
        print(
            f"No journal entries older than 60 days (cutoff: {CUTOFF}). Nothing to compress."
        )
        sys.exit(0)

    log(
        f"Compression run started — {len(old)} entries to archive "
        f"(oldest: {old[0][0]}, cutoff: {CUTOFF})"
    )

    # 1. Summarize via Claude
    log("Calling Claude API to generate summary...")
    summary_text = summarize(old)

    # 2. Write/regenerate journal_summary.txt
    with open(SUMMARY, "w") as f:
        f.write(
            f"# Journal Summary\n"
            f"Generated: {date.today().isoformat()}  |  "
            f"Covers: {old[0][0]} → {old[-1][0]}  |  "
            f"Entries: {len(old)}\n\n"
            f"{summary_text}\n"
        )
    log(f"journal_summary.txt regenerated ({len(old)} entries compressed).")

    # 3. Archive old entries by month
    write_archive(old)

    # 4. Rewrite journal.txt with only kept entries
    rewrite_journal(kept)
    log(
        f"journal.txt updated — {len(kept)} recent entries retained, "
        f"{len(old)} archived."
    )

    log("Compression run complete.")


if __name__ == "__main__":
    main()
