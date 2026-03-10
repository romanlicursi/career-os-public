#!/usr/bin/env python3
"""
layer0.py — Layer 0 Orientation Module

Runs monthly. Questions the direction from scratch: given the current landscape,
what are the highest-leverage career paths for Roman's profile?

Three steps:
  1. Data collection → data/raw/layer0_raw.json
  2. Compression    → data/summaries/layer0_signals.json
  3. Synthesis      → data/summaries/layer0_orientation.md + dated archive

Usage:
    python3 scripts/layer0.py
    python3 scripts/layer0.py --date 2026-03-10   # force archive date

Dependencies:
    export ANTHROPIC_API_KEY=sk-ant-...
    pip install anthropic python-dotenv requests
"""

import argparse
import json
import os
import re
import smtplib
import ssl
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL_COMPRESS    = "claude-sonnet-4-6"
MODEL_SYNTHESIS   = "claude-opus-4-6"

ROOT          = Path(__file__).parent.parent
SUMMARIES_DIR = ROOT / "data" / "summaries"
RAW_DIR       = ROOT / "data" / "raw"
RAW_PATH      = RAW_DIR / "layer0_raw.json"
SIGNALS_PATH  = SUMMARIES_DIR / "layer0_signals.json"
ORIENT_LIVE   = SUMMARIES_DIR / "layer0_orientation.md"
CLAUDE_MD     = ROOT / "CLAUDE.md"

L1_DIGEST = SUMMARIES_DIR / "layer1_digest.json"
L2_DIGEST = SUMMARIES_DIR / "layer2_digest.json"

# ── Step 1 helpers — Data Collection ──────────────────────────────────────────

def fetch_yc_algolia(batches: list[str] = ["W25", "S24", "W24"]) -> list[dict]:
    """
    Fetch YC companies from Algolia search API for given batches.
    Returns list of company dicts with name, one_liner, batch, tags, industries.
    """
    url     = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries"
    headers = {
        "X-Algolia-Application-Id": "45BWZJ1SGC",
        "X-Algolia-API-Key":        "OTU2Mzc1NTM2NWM3MDZlYzA4YmFiOTkxMDIwOWMwNg==",
        "Content-Type":             "application/json",
        "User-Agent":               "CareerOS/1.0",
    }

    companies = []
    for batch in batches:
        try:
            body = {
                "requests": [{
                    "indexName": "YCCompany_production",
                    "params": f"query=&page=0&hitsPerPage=100&filters=batch%3A{batch}",
                }]
            }
            resp = requests.post(url, headers=headers, json=body, timeout=15)
            if resp.status_code != 200:
                print(f"  [yc_algolia] WARNING: batch {batch} returned status {resp.status_code}")
                continue

            results = resp.json().get("results", [{}])[0]
            hits    = results.get("hits", [])
            for h in hits:
                companies.append({
                    "name":        h.get("name", ""),
                    "one_liner":   h.get("one_liner") or (h.get("long_description", "") or "")[:300],
                    "batch":       h.get("batch", batch),
                    "tags":        h.get("tags", []),
                    "industries":  h.get("industries", []),
                })
            print(f"  [yc_algolia] ✓ batch {batch}: {len(hits)} companies")
        except Exception as e:
            print(f"  [yc_algolia] WARNING: batch {batch} failed: {e}")

    return companies


def fetch_rss(url: str, source_label: str, max_items: int = 20) -> list[dict]:
    """
    Fetch and parse an RSS 2.0 or Atom feed.
    Handles both <item> (RSS) and <entry> (Atom) elements.
    Returns [{title, summary, link, source}] or [] on any failure.
    """
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "CareerOS/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"  [{source_label}] WARNING: HTTP {resp.status_code} from {url}")
            return []

        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"  [{source_label}] WARNING: fetch/parse failed: {e}")
        return []

    items = []

    # RSS 2.0: <rss><channel><item>
    for item in root.findall(".//item")[:max_items]:
        title   = (item.findtext("title")       or "").strip()
        summary = (item.findtext("description") or "").strip()[:500]
        link    = (item.findtext("link")        or "").strip()
        if title:
            items.append({"title": title, "summary": summary, "link": link, "source": source_label})

    # Atom: <feed><entry> — may use namespace
    if not items:
        # Detect namespace
        ns_match = re.match(r"\{([^}]+)\}", root.tag)
        ns = f"{{{ns_match.group(1)}}}" if ns_match else ""
        for entry in root.findall(f"{ns}entry")[:max_items]:
            title_el   = entry.find(f"{ns}title")
            summary_el = entry.find(f"{ns}summary") or entry.find(f"{ns}content")
            link_el    = entry.find(f"{ns}link")
            title   = (title_el.text   if title_el   is not None else "").strip()
            summary = (summary_el.text if summary_el is not None else "").strip()[:500]
            link    = ""
            if link_el is not None:
                link = link_el.get("href", "") or (link_el.text or "")
            if title:
                items.append({"title": title, "summary": summary, "link": link, "source": source_label})

    print(f"  [{source_label}] ✓ {len(items)} item(s)")
    return items


def fetch_wellfound_titles() -> list[dict]:
    """
    Attempt to fetch GTM/RevOps job titles from Wellfound.
    High probability of being blocked — returns [] gracefully.
    """
    try:
        resp = requests.get(
            "https://wellfound.com/jobs?role=operations&remote=true",
            headers={"User-Agent": "CareerOS/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"  [wellfound] WARNING: HTTP {resp.status_code} — likely blocked, skipping")
            return []

        # Rough extraction of job title patterns from HTML
        titles_found = re.findall(
            r'"title"\s*:\s*"([^"]{10,80})"',
            resp.text,
        )
        titles = [{"title": t, "source": "wellfound"} for t in titles_found[:50]]
        print(f"  [wellfound] ✓ {len(titles)} title(s) extracted")
        return titles
    except Exception as e:
        print(f"  [wellfound] WARNING: {e} — skipping")
        return []


def collect_all_signals() -> dict:
    """
    Orchestrate all data sources. Saves raw output to RAW_PATH.
    Returns the raw signals dict.
    """
    collected_at = date.today().isoformat()
    sources_attempted = ["yc_algolia", "a16z", "bessemer", "lenny", "saastr", "wellfound"]
    sources_successful = []

    print("\n[Step 1] Collecting signals...")

    # YC Algolia
    yc_companies = fetch_yc_algolia()
    if yc_companies:
        sources_successful.append("yc_algolia")

    # RSS feeds
    a16z_posts = fetch_rss("https://a16z.com/feed", "a16z")  # trailing slash caused 404
    if a16z_posts:
        sources_successful.append("a16z")

    bvp_posts = fetch_rss("https://www.bvp.com/atlas/feed", "bessemer")  # /rss returned 404
    if bvp_posts:
        sources_successful.append("bessemer")

    lenny_posts = fetch_rss("https://www.lennysnewsletter.com/feed", "lenny")
    if lenny_posts:
        sources_successful.append("lenny")

    saastr_posts = fetch_rss("https://www.saastr.com/feed", "saastr")
    if saastr_posts:
        sources_successful.append("saastr")

    # Wellfound (often blocked)
    wellfound_titles = fetch_wellfound_titles()
    if wellfound_titles:
        sources_successful.append("wellfound")

    raw = {
        "collected_at":        collected_at,
        "sources_attempted":   sources_attempted,
        "sources_successful":  sources_successful,
        "yc_companies":        yc_companies,
        "a16z_posts":          a16z_posts,
        "bvp_posts":           bvp_posts,
        "lenny_posts":         lenny_posts,
        "saastr_posts":        saastr_posts,
        "wellfound_titles":    wellfound_titles,
    }

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with open(RAW_PATH, "w") as f:
        json.dump(raw, f, indent=2)

    print(f"\n  ✓ Raw saved → {RAW_PATH}")
    print(f"  Sources successful: {sources_successful}")
    print(f"  YC companies: {len(yc_companies)} | RSS items: "
          f"a16z={len(a16z_posts)}, bvp={len(bvp_posts)}, "
          f"lenny={len(lenny_posts)}, saastr={len(saastr_posts)}")

    return raw


# ── Step 2 — Compression ──────────────────────────────────────────────────────

COMPRESS_SYSTEM = """\
You are a signal extractor for Career OS Layer 0.

Your job is to compress raw market data into a structured JSON object that a \
synthesis model will use to assess career directions for a CS student \
targeting high-leverage post-grad roles in RevOps, GTM Ops, \
Revenue Engineering, and adjacent emerging functions.

Output ONLY valid JSON — no preamble, no explanation, no markdown fences.

Schema:
{
  "meta": {
    "collected_at": "",
    "sources_successful": []
  },
  "emerging_titles": [
    {"title": "", "source": "", "count": 0, "notes": ""}
  ],
  "title_mutations": [
    {"from": "", "to": "", "signal_strength": "strong|moderate|weak"}
  ],
  "skill_bundles": [
    {"skills": [], "context": "", "source": ""}
  ],
  "vc_thesis_themes": [
    {"theme": "", "source": "", "post_title": "", "summary": ""}
  ],
  "yc_signals": [
    {"company": "", "description": "", "batch": "", "relevance": ""}
  ]
}

Extraction rules:
- emerging_titles: job functions or titles appearing in the data that are genuinely new \
or mutating — not generic ones like "Sales Manager". Only include with visible evidence.
- title_mutations: pairs where an old title is clearly evolving into a new one (e.g., \
"Sales Ops" → "Revenue Engineer"). Only include with visible evidence in the data.
- skill_bundles: clusters of 3+ skills appearing together in a meaningful context \
(a company, a thesis, a job description fragment). Do not fabricate bundles.
- vc_thesis_themes: only from actual investment thesis content or portfolio announcements, \
not generic thought leadership posts.
- yc_signals: only YC companies where the one_liner explicitly involves \
GTM, RevOps, revenue operations, sales automation, CRM, or AI-assisted selling/operations. \
Skip companies unrelated to these functions.
"""


def compress_signals(raw: dict, client: anthropic.Anthropic) -> dict:
    """
    One Sonnet call. Compresses raw signals into structured JSON.
    Truncates yc_companies to 100 items before sending (cost control).
    """
    print("\n[Step 2] Compressing signals...")

    raw_trimmed = dict(raw)
    raw_trimmed["yc_companies"] = raw["yc_companies"][:100]

    user_msg = f"Raw signals data:\n\n{json.dumps(raw_trimmed, indent=2)}"

    est_tokens = (len(COMPRESS_SYSTEM) + len(user_msg)) // 4
    print(f"  Context ~{est_tokens:,} tokens | Calling {MODEL_COMPRESS}...")

    message = client.messages.create(
        model=MODEL_COMPRESS,
        max_tokens=4096,
        system=COMPRESS_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw_text = message.content[0].text.strip()

    # Strip markdown fences if Claude wrapped the JSON
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        signals = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"  ERROR: JSON parse failed: {e}")
        print(f"  Raw response (first 500 chars): {raw_text[:500]}")
        raise

    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    with open(SIGNALS_PATH, "w") as f:
        json.dump(signals, f, indent=2)

    usage = message.usage
    print(f"  ✓ Signals saved → {SIGNALS_PATH} "
          f"({usage.input_tokens:,} in / {usage.output_tokens:,} out)")
    print(f"  Emerging titles: {len(signals.get('emerging_titles', []))} | "
          f"YC signals: {len(signals.get('yc_signals', []))} | "
          f"VC themes: {len(signals.get('vc_thesis_themes', []))}")

    return signals


# ── Step 3 helpers — Context builders ─────────────────────────────────────────

def load_json(path: Path, label: str) -> dict:
    if not path.exists():
        print(f"  WARNING: {label} not found at {path} — skipping")
        return {}
    with open(path) as f:
        return json.load(f)


def build_signals_context(signals: dict) -> str:
    """Format layer0_signals.json into labeled sections for the synthesis prompt."""
    if not signals:
        return "Layer 0 signals: not available."

    meta = signals.get("meta", {})

    emerging = signals.get("emerging_titles", [])
    emerging_str = "\n".join(
        f"  - {t['title']} ({t['source']}, count={t.get('count', '?')}): {t.get('notes', '')}"
        for t in emerging[:15]
    ) or "  (none identified)"

    mutations = signals.get("title_mutations", [])
    mutations_str = "\n".join(
        f"  - {m['from']} → {m['to']} [{m.get('signal_strength', '?')}]"
        for m in mutations[:10]
    ) or "  (none identified)"

    bundles = signals.get("skill_bundles", [])
    bundles_str = "\n".join(
        f"  - [{b['source']}] {', '.join(b['skills'])}: {b.get('context', '')}"
        for b in bundles[:10]
    ) or "  (none identified)"

    themes = signals.get("vc_thesis_themes", [])
    themes_str = "\n".join(
        f"  - [{t['source']}] {t['theme']} | Post: \"{t.get('post_title', '')}\" | {t.get('summary', '')}"
        for t in themes[:10]
    ) or "  (none identified)"

    yc = signals.get("yc_signals", [])
    yc_str = "\n".join(
        f"  - [{s['batch']}] {s['company']}: {s['description']} | Relevance: {s.get('relevance', '')}"
        for s in yc[:15]
    ) or "  (none identified)"

    return f"""## Layer 0 — Market Signals (collected {meta.get('collected_at', '?')})
Sources successful: {', '.join(meta.get('sources_successful', []))}

### Emerging / mutating job titles:
{emerging_str}

### Title mutations (old → new):
{mutations_str}

### Skill bundles (3+ co-occurring skills):
{bundles_str}

### VC thesis themes (investment-backed signals):
{themes_str}

### YC companies with GTM/RevOps relevance:
{yc_str}"""


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
        key=lambda x: x[1], reverse=True,
    )
    top_verbs = ", ".join(f"{v}({c})" for v, c in verbs_sorted[:12])

    stages = digest.get("company_stage_breakdown", {})
    stage_str = ", ".join(f"{k}: {v}" for k, v in sorted(stages.items(), key=lambda x: -x[1]))

    ai = digest.get("ai_exposure", {})
    automatable  = "\n".join(f"  - {x}" for x in ai.get("automatable",  [])[:8])
    ai_amplified = "\n".join(f"  - {x}" for x in ai.get("ai_amplified", [])[:8])

    meta = digest.get("meta", {})
    return f"""## Layer 1 — Market Signal Digest
Processed: {meta.get('total_postings_processed', '?')} postings | Runs: {', '.join(meta.get('run_dates', []))}

### Top tools by mention count:
{top_tools}

### Ownership verbs (frequency):
{top_verbs}

### Company stage breakdown:
{stage_str}

### AI exposure — automatable:
{automatable}

### AI exposure — AI-amplified:
{ai_amplified}"""


def build_layer2_context(digest: dict) -> str:
    if not digest:
        return "Layer 2 digest: not available."

    clusters = digest.get("cohort_clusters", [])
    clusters_str = ""
    for c in sorted(clusters, key=lambda x: x.get("profile_count", 0), reverse=True)[:8]:
        moves = "; ".join(c.get("defining_moves", [])[:3])
        clusters_str += (
            f"\n  [{c.get('profile_count', 0)} profiles] **{c['name']}**: "
            f"{c.get('description', '')} | Key moves: {moves}"
        )

    bridges = digest.get("bridge_moves", {})
    bridges_str = ""
    for key, b in sorted(bridges.items(), key=lambda x: x[1].get("frequency", 0), reverse=True)[:8]:
        bridges_str += (
            f"\n  ({b['frequency']}x) {b['from_role']} → {b['to_role']}: {b.get('notes', '')}"
        )

    launchpads = digest.get("launchpad_companies", {})
    launchpads_str = ""
    for name, l in sorted(launchpads.items(), key=lambda x: x[1].get("count", 0), reverse=True)[:8]:
        titles = ", ".join(l.get("titles_launched_from", [])[:3])
        launchpads_str += (
            f"\n  [{l.get('count', 0)}x] **{l.get('display_name', name)}**: "
            f"{l.get('why_launchpad', '')} | Titles: {titles}"
        )

    meta = digest.get("meta", {})
    return f"""## Layer 2 — Career Path Intelligence
Profiles analyzed: {meta.get('total_profiles_processed', '?')} | Runs: {', '.join(meta.get('run_dates', []))}

### Cohort clusters (sorted by frequency):{clusters_str}

### Recurring bridge moves:{bridges_str}

### Launchpad companies:{launchpads_str}

### User's closest cluster: {digest.get('roman_closest_cluster', 'not yet assessed')}"""


# ── Step 3 — Synthesis ────────────────────────────────────────────────────────

ORIENT_SYSTEM = """\
You are the Layer 0 Orientation Module for Career OS.

Your job is to question the direction, not confirm it.

You have access to:
- Fresh market signals: emerging job titles, title mutations, VC thesis themes, YC company \
patterns, and skill bundles — collected this quarter
- Layer 1 digest: what job postings are asking for right now (if available)
- Layer 2 digest: how people a few years ahead of the user actually built their careers (if available)

Locked constraints (not up for reassessment):
- User is a CS student with a gap period, targeting high-leverage post-grad roles.
- They have a confirmed RevOps internship at a mid-market industrial company, next summer. \
This is fixed — do not suggest undoing this.
- They have real GTM ops experience: early-stage org (outbound sequences, CRM pipeline, Clay, \
ReachInbox, Zapier).

Open question this memo must answer:
Given the current landscape — as evidenced by the signals above — what are the 6–8 \
highest-leverage career paths available to someone with this profile?

The memo must contain:
1. A table or structured list of 6–8 paths, each scored on:
   - Viability (1–5): how realistic is this path from the user's current position?
   - 5-year trajectory: where does this path realistically lead?
   - AI displacement risk (low/medium/high): how exposed to automation in 5 years?
   - Autonomy ceiling: does this path open into consulting, fractional work, \
or entrepreneurship — or does it stay employee-track?
   - Long-term financial alignment (1–5): how well does income ceiling and scalability match \
early financial independence goals?
2. An honest, direct assessment of where RevOps/GTM sits right now: is it crowding? \
bifurcating? Is there a premium tier emerging and a commoditized floor?
3. One paragraph on specific leverage: what makes the combination of CS + GTM ops \
experience + confirmed RevOps internship unusual — and where does that combination have \
disproportionate pull?
4. Any pre-title inflection roles you see emerging in the VC thesis / YC data — \
functions that don't have stable titles yet but will in 18–24 months.
5. One concrete directional recommendation for the next 90 days. Not a list — one thing. \
Make it specific enough that the user knows exactly what to do Monday morning.

Tone and format:
- Write like a well-informed advisor writing a letter, not a report generator.
- Do not pad. If something isn't interesting, skip it.
- Be direct when a path is weak. Do not treat all paths as equally valid.
- Reference specific signals from the data when making claims — not generic assertions.
- The memo should be 600–1000 words. Tight, not exhaustive.\
"""


def synthesize(
    context: str,
    claude_md_text: str,
    client: anthropic.Anthropic,
    target_date: str,
) -> tuple[str, str, int, int]:
    """
    One Opus call. Produces the orientation memo.
    Returns (memo_text, model_used, input_tokens, output_tokens).
    """
    system_prompt = ORIENT_SYSTEM + "\n\n---\n\nDecision Constitution (CLAUDE.md):\n" + claude_md_text

    est_tokens = (len(system_prompt) + len(context)) // 4
    print(f"  Context ~{est_tokens:,} tokens | Calling {MODEL_SYNTHESIS}...")

    t0 = datetime.now(timezone.utc)
    message = client.messages.create(
        model=MODEL_SYNTHESIS,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": context}],
    )
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()

    memo_text  = message.content[0].text
    input_tok  = message.usage.input_tokens
    output_tok = message.usage.output_tokens

    print(f"  ✓ Done in {elapsed:.1f}s | {input_tok:,} in / {output_tok:,} out")
    return memo_text, MODEL_SYNTHESIS, input_tok, output_tok


def save_orientation(
    memo_text: str,
    model: str,
    input_tok: int,
    output_tok: int,
    target_date: str,
) -> None:
    header = (
        f"# Orientation Memo\n"
        f"Generated: {target_date} | Model: {model} | "
        f"Tokens: {input_tok:,} in / {output_tok:,} out\n\n"
        f"---\n\n"
    )
    full_memo = header + memo_text

    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)

    with open(ORIENT_LIVE, "w") as f:
        f.write(full_memo)
    print(f"  Saved → {ORIENT_LIVE}")

    archive_path = SUMMARIES_DIR / f"layer0_orientation_{target_date}.md"
    with open(archive_path, "w") as f:
        f.write(full_memo)
    print(f"  Saved → {archive_path}")


# ── Email delivery ────────────────────────────────────────────────────────────

GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_USER         = os.environ.get("GMAIL_USER", "")


def send_orientation_email(
    memo_text: str,
    model: str,
    input_tok: int,
    output_tok: int,
    target_date: str,
) -> None:
    if not GMAIL_APP_PASSWORD:
        print("  WARNING: GMAIL_APP_PASSWORD not set — skipping email delivery.")
        print("  Add the secret to GitHub repo settings to enable delivery.")
        return

    header = (
        f"Generated: {target_date} | Model: {model} | "
        f"Tokens: {input_tok:,} in / {output_tok:,} out\n\n"
        f"{'=' * 56}\n\n"
    )
    body = header + memo_text

    msg = MIMEText(body, "plain")
    msg["Subject"] = f"Career OS — Orientation Memo {target_date}"
    msg["From"]    = f"Career OS <{GMAIL_USER}>"
    msg["To"]      = GMAIL_USER

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)
    print(f"  ✓ Orientation email sent to {GMAIL_USER}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(run_date: str | None = None) -> None:
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    target_date = run_date or date.today().isoformat()

    print("\n══════════════════════════════════════════")
    print("  Career OS — Layer 0 Orientation Module")
    print(f"  Date: {target_date}")
    print("══════════════════════════════════════════")

    if not CLAUDE_MD.exists():
        print("ERROR: CLAUDE.md not found.")
        sys.exit(1)
    claude_md_text = CLAUDE_MD.read_text()

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # ── Step 1: Collect ──
    raw = collect_all_signals()

    # ── Step 2: Compress ──
    signals = compress_signals(raw, client)

    # ── Step 3: Synthesize ──
    print("\n[Step 3] Synthesizing orientation memo...")

    signals_ctx = build_signals_context(signals)
    l1_ctx      = build_layer1_context(load_json(L1_DIGEST, "layer1_digest"))
    l2_ctx      = build_layer2_context(load_json(L2_DIGEST, "layer2_digest"))

    context = "\n\n---\n\n".join([signals_ctx, l1_ctx, l2_ctx])

    memo_text, model, input_tok, output_tok = synthesize(
        context, claude_md_text, client, target_date
    )

    save_orientation(memo_text, model, input_tok, output_tok, target_date)
    send_orientation_email(memo_text, model, input_tok, output_tok, target_date)

    print("\n══════════════════════════════════════════")
    print("  Layer 0 complete. Read the memo:")
    print("  cat data/summaries/layer0_orientation.md")
    print("══════════════════════════════════════════\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Layer 0 orientation module")
    parser.add_argument("--date", help="Override archive date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()
    main(run_date=args.date)
