#!/usr/bin/env python3
"""
layer5.py — Layer 5 Network Intelligence

Reads the most recent sprint card, scrapes each outreach target's LinkedIn
profile via Apify, generates a contact dossier + outreach drafts via Claude,
updates data/crm.json, and sends a single digest email via Gmail SMTP.

Sprint card outreach target format (required):
    1. **Name** | Role | Company | https://linkedin.com/in/... | Rationale

Usage:
    python3 scripts/layer5.py

Dependencies:
    export ANTHROPIC_API_KEY=...
    export APIFY_API_TOKEN=...
    export GMAIL_APP_PASSWORD=...

Notes:
    - Uses harvestapi/linkedin-profile-scraper for direct URL lookup (same actor
      as fetch_roman_profile.py, confirmed working). harvestapi/linkedin-profile-search
      is a search actor and does not support direct URL lookup.
    - One Claude call per contact: dossier + both drafts + follow-up in one shot.
    - Existing CRM contacts are skipped — no re-drafting for someone already tracked.
    - Raw Apify output is ephemeral — not saved to repo.
    - Sprint card outreach targets may use MISSING for linkedin_url (produced by layer4.py).
      When MISSING, layer5.py searches for the URL via harvestapi/linkedin-profile-search
      before scraping. If search returns nothing, the contact is included in the email
      with a note and processing continues.
    - Email subject: "Career OS — Weekly Digest [date]". Sprint card is prepended to the
      email body so the full digest arrives in one message.
"""

import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
from apify_client import ApifyClient

# ── Configuration ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
APIFY_TOKEN        = os.environ.get("APIFY_API_TOKEN", "REDACTED_APIFY_TOKEN")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_USER         = "romanlicursi@gmail.com"

MODEL         = "claude-sonnet-4-6"
ACTOR_PROFILE = "harvestapi/linkedin-profile-scraper"   # direct URL lookup, confirmed working
ACTOR_SEARCH  = "harvestapi/linkedin-profile-search"    # search by name+company when URL is MISSING

ROOT          = Path(__file__).parent.parent
SPRINTS_DIR   = ROOT / "sprints"
SUMMARIES_DIR = ROOT / "data" / "summaries"
CONTACTS_DIR  = ROOT / "data" / "contacts"
CRM_PATH      = ROOT / "data" / "crm.json"
CLAUDE_MD     = ROOT / "CLAUDE.md"
L1_DIGEST     = SUMMARIES_DIR / "layer1_digest.json"

# ── Sprint card parsing ────────────────────────────────────────────────────────

def find_latest_sprint_card() -> Path:
    cards = sorted(SPRINTS_DIR.glob("sprint_*.md"))
    if not cards:
        print("ERROR: No sprint cards found in sprints/. Run Layer 4 first.")
        sys.exit(1)
    return cards[-1]


def parse_outreach_targets(card_text: str) -> list[dict]:
    """
    Parse outreach targets from sprint card.
    Required format per target line:
        **Name** | Role | Company | https://linkedin.com/in/... | Rationale
        **Name** | Role | Company | MISSING | Rationale   ← produced by layer4.py

    MISSING is a valid placeholder — layer5.py will search for the URL before scraping.
    Exits only if the pipe-delimited structure itself is malformed (wrong number of fields
    or a non-URL / non-MISSING value in the linkedin_url slot).
    """
    match = re.search(r"## Outreach Targets\n(.*?)(?:\n## |\Z)", card_text, re.DOTALL)
    if not match:
        print("ERROR: Sprint card has no '## Outreach Targets' section.")
        sys.exit(1)

    section   = match.group(1).strip()
    raw_items = re.findall(r"^\d+\.\s+(.+)$", section, re.MULTILINE)
    if not raw_items:
        print("ERROR: No numbered outreach targets found in sprint card.")
        sys.exit(1)

    targets = []
    errors  = []

    for i, item in enumerate(raw_items, 1):
        parts = [p.strip() for p in item.split("|")]

        if len(parts) < 5:
            errors.append(
                f"  Target {i}: only {len(parts)} field(s) found, 5 required.\n"
                f"    Got:      {item}\n"
                f"    Expected: **Name** | Role | Company | MISSING | Rationale\n"
                f"              **Name** | Role | Company | https://linkedin.com/in/... | Rationale"
            )
            continue

        name         = re.sub(r"\*\*(.+?)\*\*", r"\1", parts[0]).strip()
        role         = parts[1].strip()
        company      = parts[2].strip()
        linkedin_url = parts[3].strip()
        rationale    = parts[4].strip()

        url_missing = linkedin_url.upper() == "MISSING"

        if not url_missing and (not linkedin_url.startswith("http") or "linkedin.com" not in linkedin_url):
            errors.append(
                f"  Target {i} ({name}): linkedin_url is neither a valid URL nor MISSING.\n"
                f"    Got: '{linkedin_url}'\n"
                f"    Use a full URL (https://linkedin.com/in/...) or the literal word MISSING."
            )
            continue

        targets.append({
            "name":         name,
            "role":         role,
            "company":      company,
            "linkedin_url": "" if url_missing else linkedin_url,
            "url_missing":  url_missing,
            "rationale":    rationale,
        })

    if errors:
        print("ERROR: Sprint card outreach targets are malformed.\n")
        print("Issues found:")
        for e in errors:
            print(e)
        print("\nUpdate the sprint card and re-run Layer 5.")
        sys.exit(1)

    return targets


def parse_sprint_card(path: Path) -> dict:
    text = path.read_text()

    date_match = re.search(r"# Sprint Card — (\d{4}-\d{2}-\d{2})", text)
    sprint_date = date_match.group(1) if date_match else path.stem.replace("sprint_", "")

    pr_match = re.search(r"## Positioning Reminder\n(.+?)(?:\n## |\Z)", text, re.DOTALL)
    positioning_reminder = pr_match.group(1).strip() if pr_match else ""

    targets = parse_outreach_targets(text)

    return {
        "sprint_date":          sprint_date,
        "positioning_reminder": positioning_reminder,
        "targets":              targets,
        "card_text":            text,   # included verbatim in the combined digest email
    }

# ── Layer 1 signal picker ──────────────────────────────────────────────────────

def pick_l1_signal(digest_path: Path, contact_role: str) -> str:
    """
    Pick one relevant signal from layer1_digest.json for use in the drafting call.
    Loads only what's needed — not the full digest.
    """
    if not digest_path.exists():
        return "No Layer 1 digest available."

    with open(digest_path) as f:
        digest = json.load(f)

    # Top 3 tools by mention count
    tools = digest.get("tools", {})
    top_tools = sorted(tools.items(), key=lambda x: x[1].get("count", 0), reverse=True)[:3]
    tools_str = ", ".join(f"{name} ({d['count']}x)" for name, d in top_tools)

    # One AI-amplified signal most relevant to RevOps/GTM
    ai_amplified = digest.get("ai_exposure", {}).get("ai_amplified", [])
    ai_signal = ai_amplified[0] if ai_amplified else ""

    # One operator persona phrase
    persona = digest.get("operator_persona_language", [])
    persona_phrase = persona[0] if persona else ""

    lines = [f"Top tools in market right now: {tools_str}."]
    if ai_signal:
        lines.append(f"AI-amplified capability companies are hiring for: {ai_signal}.")
    if persona_phrase:
        lines.append(f"How companies describe this hire: \"{persona_phrase}\".")

    return " ".join(lines)

# ── Apify profile scrape ───────────────────────────────────────────────────────

def scrape_profile(apify_client: ApifyClient, linkedin_url: str, name: str) -> list[dict]:
    """
    Scrape a LinkedIn profile by URL using harvestapi/linkedin-profile-scraper.
    Returns last 3 career moves as a list of dicts, or empty list on failure.
    """
    print(f"  [apify] Scraping {name} — {linkedin_url}")
    try:
        run   = apify_client.actor(ACTOR_PROFILE).call(
            run_input={"urls": [linkedin_url], "maxItems": 1},
            timeout_secs=120,
        )
        items = list(apify_client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as e:
        print(f"  [apify] ERROR scraping {name}: {e}")
        return []

    if not items:
        print(f"  [apify] WARNING: No data returned for {name}. Proceeding with career data from sprint card only.")
        return []

    profile    = items[0]
    experience = profile.get("experience") or []

    career_moves = []
    for pos in experience[:3]:
        start = (pos.get("startDate") or {}).get("text") or "?"
        end   = (pos.get("endDate")   or {}).get("text") or "present"
        career_moves.append({
            "title":    pos.get("position")    or "",
            "company":  pos.get("companyName") or "",
            "start":    start,
            "end":      end,
            "duration": pos.get("duration")    or "",
        })

    print(f"  [apify] ✓ {len(career_moves)} career move(s) extracted for {name}")
    return career_moves

# ── LinkedIn URL discovery (for MISSING targets) ──────────────────────────────

def find_linkedin_url(apify_client: ApifyClient, name: str, company: str) -> str:
    """
    Search for a person's LinkedIn URL by name + company.
    Uses harvestapi/linkedin-profile-search (search actor, not direct scraper).
    Returns URL string if found, empty string otherwise.

    Handles the [Find: Role at Company] name format produced by layer4.py
    by searching on company alone.
    """
    # [Find: Role at Company] → search by company only
    if name.startswith("[Find:") and "]" in name:
        search_query = company
    else:
        search_query = f"{name} {company}".strip()

    print(f"  [apify] Searching for URL: '{search_query}'")
    try:
        run   = apify_client.actor(ACTOR_SEARCH).call(
            run_input={"searchQuery": search_query, "maxItems": 3},
            timeout_secs=120,
        )
        items = list(apify_client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as e:
        print(f"  [apify] Search error for '{search_query}': {e}")
        return ""

    if not items:
        print(f"  [apify] No search results for '{search_query}'")
        return ""

    url = (items[0].get("linkedinUrl") or items[0].get("profileUrl") or "").strip()
    if url:
        print(f"  [apify] ✓ Found: {url}")
    else:
        print(f"  [apify] Result returned but no URL field found")
    return url


# ── Claude dossier + drafts ────────────────────────────────────────────────────

DOSSIER_PROMPT = """\
You are generating a contact dossier and outreach drafts for Roman Licursi's Career OS.
Roman's background: CS junior at UW-Madison. Built outbound automation at a healthcare \
startup (CAUHEC Connect — outbound sequences, CRM pipeline, Clay, ReachInbox, Zapier). \
Incoming RevOps intern at Donaldson, summer 2026. Frame him as someone who builds \
GTM systems, not someone looking for a job.

Generate a single JSON object. No preamble, no explanation, no markdown. \
Return only valid JSON.

Required fields:
{
  "slug": "firstname-lastname (lowercase, hyphenated)",
  "full_name": "",
  "current_role": "",
  "current_company": "",
  "linkedin_url": "",
  "scraped_at": "",
  "career_moves": [],
  "shared_signals": [],
  "outreach_angle": "",
  "follow_up_draft": "",
  "linkedin_draft": "",
  "email_subject": "",
  "email_draft": ""
}

shared_signals rules:
  - Specific overlaps between this contact's trajectory and Roman's: tools, company stage, \
function, type of problem they've worked on.
  - Must be concrete. "Both work in operations" is not a signal. \
"Both built outbound sequences at early-stage companies" is a signal.
  - If no real overlaps exist, return an empty array. Do not fabricate.

linkedin_draft rules:
  - Under 300 characters if connection request, under 500 if already connected.
  - No flattery opener. First sentence hooks on something specific and real about them \
or their company.
  - Ask proportional to seniority: IC/mid-level → async question; \
Director+ → 20-minute conversation framed around their perspective, not Roman's job search.
  - Must reference at least one detail from shared_signals or their specific career history.
  - Sign off: "— Roman"
  - A message that could have been sent to anyone is a failure.

email_subject rules:
  - Specific. Never "Quick question" or "Connecting." Reference something real.

email_draft rules:
  - Under 150 words.
  - Same hook principle as LinkedIn.
  - One clear ask.
  - No "I came across your profile" language.
  - Must reference at least one detail from shared_signals or their career history.

follow_up_draft rules:
  - Under 100 words.
  - Different angle or new hook — do not just re-state the original message.
  - Not pushy. Assumes good faith from the recipient.\
"""


def generate_dossier(
    claude_client: anthropic.Anthropic,
    contact: dict,
    career_moves: list[dict],
    positioning_reminder: str,
    l1_signal: str,
    claude_md_text: str,
) -> dict:
    """
    One Claude call: generates full dossier + both drafts + follow-up.
    Returns the parsed JSON dict.
    """
    career_str = "\n".join(
        f"  - {m['title']} @ {m['company']} ({m['start']} – {m['end']}, {m['duration']})"
        for m in career_moves
    ) or "  (no career data returned from scrape — draft based on role and rationale only)"

    user_msg = f"""\
Contact: {contact['name']}
Current role: {contact['role']} at {contact['company']}
LinkedIn: {contact['linkedin_url']}

Last career moves (up to 3):
{career_str}

Outreach angle (from sprint card): {contact['rationale']}

Roman's positioning this week: {positioning_reminder}

Market signal (relevant to this contact's stage/role):
{l1_signal}

---
Roman's Decision Constitution (CLAUDE.md):
{claude_md_text}
"""

    scraped_at = datetime.now(timezone.utc).isoformat()

    print(f"  [claude] Generating dossier + drafts for {contact['name']}...")
    message = claude_client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=DOSSIER_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if Claude wrapped the JSON
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        dossier = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [claude] ERROR: JSON parse failed for {contact['name']}: {e}")
        print(f"  Raw response: {raw[:500]}")
        raise

    # Ensure fields present in case Claude omitted any
    dossier.setdefault("scraped_at",    scraped_at)
    dossier.setdefault("linkedin_url",  contact["linkedin_url"])
    dossier.setdefault("career_moves",  career_moves)
    dossier.setdefault("shared_signals", [])
    dossier.setdefault("follow_up_draft", "")
    dossier.setdefault("email_subject",   "")

    print(f"  [claude] ✓ Dossier complete for {contact['name']} "
          f"({len(dossier.get('shared_signals', []))} shared signal(s))")
    return dossier

# ── CRM management ─────────────────────────────────────────────────────────────

def load_crm() -> dict:
    if CRM_PATH.exists():
        with open(CRM_PATH) as f:
            return json.load(f)
    return {"contacts": []}


def save_crm(crm: dict) -> None:
    CRM_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CRM_PATH, "w") as f:
        json.dump(crm, f, indent=2)


def find_existing(crm: dict, slug: str) -> dict | None:
    for c in crm.get("contacts", []):
        if c.get("slug") == slug:
            return c
    return None

# ── Email digest ───────────────────────────────────────────────────────────────

def send_digest_email(processed: list[dict], sprint_date: str, sprint_card_text: str = "") -> None:
    if not GMAIL_APP_PASSWORD:
        print("  WARNING: GMAIL_APP_PASSWORD not set — skipping email delivery.")
        print("  Add the secret to GitHub repo settings to enable delivery.")
        return

    lines = [
        f"Career OS — Weekly Digest | {sprint_date}",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    # ── Sprint card section ──
    if sprint_card_text:
        lines += ["=" * 56, "SPRINT CARD", "=" * 56, ""]
        lines.append(sprint_card_text.strip())
        lines += ["", "=" * 56, "OUTREACH DRAFTS", "=" * 56, ""]
    else:
        lines += ["=" * 56, "OUTREACH DRAFTS", "=" * 56, ""]

    # ── Per-contact sections ──
    for item in processed:
        lines.append("")

        # Contact where URL search failed — include note, no drafts
        if item.get("url_not_found"):
            c = item["contact"]
            lines.append(f"{c['name']} | {c['role']} | {c['company']}")
            lines.append("⚠ LinkedIn URL missing — search returned no results. No drafts generated.")
            lines.append("  To generate drafts: add a LinkedIn URL to the sprint card and re-run Layer 5.")
            lines.append("")
            continue

        d = item["dossier"]
        lines.append(f"{d.get('full_name','')} | {d.get('current_role','')} | {d.get('current_company','')}")
        lines.append(f"LinkedIn: {d.get('linkedin_url','')}")
        lines.append("")

        signals = d.get("shared_signals", [])
        lines.append("SHARED SIGNALS:")
        if signals:
            for s in signals[:2]:
                lines.append(f"  • {s}")
        else:
            lines.append("  No direct overlaps found.")
        lines.append("")

        lines.append("LINKEDIN DRAFT:")
        lines.append(d.get("linkedin_draft", "(none generated)"))
        lines.append("")

        subj = d.get("email_subject", "")
        lines.append(f"EMAIL DRAFT — Subject: {subj}")
        lines.append(d.get("email_draft", "(none generated)"))
        lines.append("")

        lines.append("FOLLOW-UP (send if no reply in 10 days):")
        lines.append(d.get("follow_up_draft", "(none generated)"))
        lines.append("")

    lines += [
        "=" * 56,
        "",
        "CRM: https://github.com/romanlicursi/career-os/blob/main/data/crm.json",
    ]

    body = "\n".join(lines)
    msg  = MIMEText(body, "plain")
    msg["Subject"] = f"Career OS — Weekly Digest {sprint_date}"
    msg["From"]    = f"Career OS <{GMAIL_USER}>"
    msg["To"]      = GMAIL_USER

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)
    print(f"  ✓ Digest email sent to {GMAIL_USER}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    print("\n══════════════════════════════════════════")
    print("  Career OS — Layer 5 Network Intelligence")
    print("══════════════════════════════════════════")

    # Load supporting context
    if not CLAUDE_MD.exists():
        print("ERROR: CLAUDE.md not found.")
        sys.exit(1)
    claude_md_text = CLAUDE_MD.read_text()

    # Parse sprint card
    card_path = find_latest_sprint_card()
    print(f"\n  Sprint card: {card_path.name}")
    card = parse_sprint_card(card_path)
    print(f"  Sprint date: {card['sprint_date']}")
    print(f"  Targets found: {len(card['targets'])}")

    # Pick L1 signal once (same signal context for all contacts)
    l1_signal = pick_l1_signal(L1_DIGEST, "")
    print(f"  L1 signal loaded: {l1_signal[:80]}...")

    # Load CRM
    crm = load_crm()

    # Initialise clients
    apify_client  = ApifyClient(APIFY_TOKEN)
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    CONTACTS_DIR.mkdir(parents=True, exist_ok=True)

    processed: list[dict] = []

    for contact in card["targets"]:
        print(f"\n── Processing: {contact['name']} ──")

        # Tentative slug for CRM check (Claude will confirm exact slug)
        tentative_slug = re.sub(r"[^a-z0-9]+", "-", contact["name"].lower()).strip("-")
        existing = find_existing(crm, tentative_slug)
        if existing:
            print(f"  WARNING: {contact['name']} already exists in CRM (status: {existing.get('status')}). "
                  f"Skipping — not re-drafting for an existing contact.")
            continue

        # Resolve MISSING LinkedIn URL via search before scraping
        if contact.get("url_missing"):
            found_url = find_linkedin_url(apify_client, contact["name"], contact["company"])
            if found_url:
                contact["linkedin_url"] = found_url
                contact["url_missing"]  = False
            else:
                print(f"  Could not find LinkedIn URL for {contact['name']} — including in email with note.")
                processed.append({"contact": contact, "dossier": None, "url_not_found": True})
                continue

        # Scrape profile
        career_moves = scrape_profile(apify_client, contact["linkedin_url"], contact["name"])

        # Generate dossier + drafts (one Claude call)
        try:
            dossier = generate_dossier(
                claude_client=claude_client,
                contact=contact,
                career_moves=career_moves,
                positioning_reminder=card["positioning_reminder"],
                l1_signal=l1_signal,
                claude_md_text=claude_md_text,
            )
        except Exception as e:
            print(f"  ERROR generating dossier for {contact['name']}: {e}. Skipping.")
            continue

        slug = dossier.get("slug") or tentative_slug

        # Save dossier to data/contacts/{slug}.json (committed to repo)
        dossier_path = CONTACTS_DIR / f"{slug}.json"
        with open(dossier_path, "w") as f:
            json.dump(dossier, f, indent=2)
        print(f"  Saved → {dossier_path}")

        # Update CRM
        now = datetime.now(timezone.utc).isoformat()
        crm["contacts"].append({
            "slug":         slug,
            "full_name":    dossier.get("full_name", contact["name"]),
            "current_role": dossier.get("current_role", contact["role"]),
            "current_company": dossier.get("current_company", contact["company"]),
            "status":       "draft",
            "sprint_source": card["sprint_date"],
            "drafted_at":   now,
            "last_updated": now,
        })

        processed.append({"contact": contact, "dossier": dossier})

    # Contacts with successful dossiers (to save to CRM)
    dossier_items = [p for p in processed if p.get("dossier") is not None]

    if not processed:
        print("\n  No new contacts processed (all may already be in CRM).")
    else:
        if dossier_items:
            save_crm(crm)
            print(f"\n  CRM updated → {CRM_PATH} ({len(dossier_items)} new contact(s))")

        print("\n  Sending digest email...")
        send_digest_email(processed, card["sprint_date"], sprint_card_text=card.get("card_text", ""))

    print("\n══════════════════════════════════════════")
    print(f"  Layer 5 complete. {len(processed)} contact(s) processed.")
    print("══════════════════════════════════════════\n")


if __name__ == "__main__":
    main()
