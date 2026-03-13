# Career OS

A six-layer intelligence and action system for career development — built entirely in Claude Code, automated via GitHub Actions, and filtered through a personal Decision Constitution.

Scrapes real job postings weekly, maps real career trajectories, generates a weekly sprint card, and produces targeted outreach drafts — all synthesized by a multi-model AI pipeline and committed automatically to this repo.

---

## What Problem This Solves

Generic career advice fails in a specific way: it optimizes for the average person, which means it's miscalibrated for anyone with non-standard priorities or unusual leverage. The advice is vague, slow to update, and disconnected from live market signals.

Career OS replaces that with a system that:
- Reads actual job postings every week and extracts what the market actually cares about (not what everyone says it cares about)
- Maps how real people in target roles built their careers — at what companies, through what moves, over what timeframe
- Synthesizes that data into a weekly memo that reads like a letter from a well-informed advisor, not a templated report
- Generates a minimal weekly sprint card: one learning priority, three outreach targets, one portfolio task, one positioning reminder
- Drafts high-precision outreach for each target, drawing on dossier research and shared signals — then waits for human review before anything is sent

The system gets sharper over time. Six months of automated runs means six months of market signal accumulation, career path data, and feedback from actual outreach attempts.

---

## Architecture Overview

```
CLAUDE.md (Decision Constitution)
    │
    ├── Layer 0 — Orientation Module (monthly)
    │   Asks: given the current landscape, where does this profile have disproportionate leverage?
    │   Output: career space map with viability scores, displacement risk, autonomy ceiling
    │
    ├── Layer 1 — Market Signal Module (weekly)
    │   Scrapes ~50 job postings via Apify → extracts trending tools, ownership verbs, company stages
    │   Lagging signal (postings) + leading signal (YC batches, VC theses, newsletter velocity)
    │   Output: layer1_digest.json — living skills taxonomy
    │
    ├── Layer 2 — Qualitative Intelligence Module (monthly)
    │   Scrapes LinkedIn profiles of people 3–5 years ahead in target roles
    │   Extracts: cohort clusters, launchpad companies, anomalies, what people wish they'd known
    │   Output: layer2_digest.json — career path clusters and bridge moves
    │
    ├── Layer 3 — Synthesis Engine (bi-weekly)
    │   Combines Layer 1 + Layer 2 + journal entries → generative memo
    │   Not a templated report. Asks: what's interesting? What's changing? What should change?
    │   Output: synthesis_memo.md — read like a letter from a well-informed advisor
    │
    ├── Layer 4 — Action Output (weekly)
    │   Translates synthesis memo into a minimal weekly sprint card
    │   One learning priority. Three outreach targets. One portfolio task. One positioning reminder.
    │   Output: sprints/sprint_YYYY-MM-DD.md
    │
    ├── Layer 5 — Network Intelligence (manual trigger)
    │   For each outreach target: researches dossier, identifies shared signals, drafts message
    │   Human reviews and sends. Not autonomous. 10–15 high-quality messages beats 500 automated ones.
    │   Output: data/contacts/{slug}.json + data/crm.json
    │
    └── Layer 6 — Feedback Loop (continuous + monthly compression)
        Journal: plain text, date + two sentences, appended manually after each meaningful event
        Monthly: compresses entries older than 60 days into a rolling summary
        Output: feeds back into every synthesis run — grounds AI output in lived experience
```

---

## The Decision Constitution (CLAUDE.md)

Every layer is filtered through a persistent `CLAUDE.md` file that functions as an operating briefing for Claude Code across all sessions. It contains:

- **Identity & Core Orientation** — who the user is, what near-term context shapes priorities
- **Optimization Targets** — explicit ranked list of what matters (with rationale for the ranking)
- **Anti-Targets** — explicit list of what to filter against (as important as the targets)
- **Behavioral Tendencies** — how the user performs best, how they lose momentum (changes the *format* of recommendations, not just content)
- **Core Friction Points** — where the user gets stuck (so the system detects when it's contributing to paralysis)
- **Governing Philosophy** — durable principles that hold regardless of what role or industry
- **Strategic Identity** — the durable description of what the user is building toward

See `CLAUDE.md` for the full sanitized template. `data/sample/sample_orientation_memo.md` shows an example of the output this file produces.

The key insight: without this file, Claude Code gives advice calibrated to an imaginary average user. With it, the system knows what to optimize for, what to filter against, and when its own recommendations are miscalibrated.

---

## Multi-Model Pipeline

Raw data never enters the synthesis model's context directly — doing so at scale would blow context limits and produce incoherent output.

Instead, each layer runs a compress-then-synthesize pattern:
1. **Scraper** (Apify) → raw JSON saved to `data/raw/` (never committed)
2. **Compression** (Claude Sonnet) → structured digest saved to `data/summaries/`
3. **Synthesis** (Claude Opus) → generative memo that can reference the compressed digest without loading raw data

This separation keeps each model call within manageable token ranges, prevents single-call failures, and makes the pipeline cost-predictable. Target: under $10/month at current volume.

---

## Automation Infrastructure

All scheduled runs are handled via GitHub Actions — no laptop dependency, secrets managed in GitHub, workflows commit output files back to the repo after each run.

| Workflow | Schedule | Layer |
|---|---|---|
| `layer0.yml` | 1st of month, 8am UTC | Orientation (monthly) |
| `layer1.yml` | Every Monday, 6am UTC | Market Signal (weekly) |
| `layer2.yml` | 1st of month, 7am UTC | Qualitative Intelligence (monthly) |
| `layer3.yml` | Every Tuesday, 8am UTC | Synthesis Engine (bi-weekly) |
| `layer4.yml` | Every Tuesday, 11am UTC | Action Output (weekly) |
| `layer5.yml` | Manual trigger only | Network Intelligence |
| `layer6.yml` | 1st of month, 11pm UTC | Journal Compression |

The GitHub Actions run history and commit timestamps are the proof of operation. Automated commits with real dates can't be manufactured retroactively.

---

## Core Design Principles

**Raw data never enters context.** Every layer processes inputs into compressed summaries. Summaries are what downstream layers load — never source files. This keeps token costs predictable and prevents context contamination at scale.

**Selective context loading per task.** No session loads everything. Each script defines exactly what it needs:
- Orientation run: Decision Constitution + layer0/1/2 digests
- Weekly synthesis: Decision Constitution + layer1/2 digests + last 14 journal entries
- Sprint card: Decision Constitution + synthesis memo + previous sprint card
- Outreach drafting: Decision Constitution + target dossier + relevant Layer 1 snippet

**Human in the loop at the output layer.** The system drafts outreach — humans send it. One wrong message to a warm contact costs more than sending one fewer message. The goal is 10–15 high-quality, specific messages, not 500 automated emails. Quality over automation at the final step.

**The journal is ground truth.** A plain text log. Date, what happened, what was noticed. Two sentences max. Claude Code reads this as part of every synthesis run and treats it as first-person reality check against external market data.

---

## Sample Outputs

`data/sample/` contains synthetic examples of each output type:

- `sample_contact_dossier.json` — Layer 5 output: research, shared signals, message drafts
- `sample_sprint_card.md` — Layer 4 output: weekly action card format
- `sample_orientation_memo.md` — Layer 0 output: career space map and path assessment

Real outputs follow the same format with real data. Dated archive files in `data/summaries/` (e.g., `layer0_orientation_2026-03-10.md`) prove the system ran on specific dates.

---

## File Structure

```
career-os/
├── CLAUDE.md                    # Decision Constitution — always auto-loaded by Claude Code
├── ARCHITECTURE.md              # Full technical spec and decision log
├── BROWSER_CONTEXT.md           # Bridge file for claude.ai sessions
├── README.md                    # This file
├── scripts/
│   ├── layer0.py                # Orientation: fetch → compress → synthesize
│   ├── layer1.py                # Market signal: scrape → batch process → digest
│   ├── layer2.py                # Qualitative: profile scrape → cluster analysis
│   ├── layer3.py                # Synthesis: combine digests + journal → memo
│   ├── layer4.py                # Sprint card: synthesis memo → action output
│   └── layer5.py                # Network: research → dossier → draft → CRM update
├── .github/workflows/           # GitHub Actions for each layer
├── data/
│   ├── summaries/               # Compressed layer outputs — source of truth between runs
│   │   ├── layer0_signals.json
│   │   ├── layer0_orientation.md
│   │   ├── layer1_digest.json
│   │   ├── layer2_digest.json
│   │   ├── synthesis_memo.md
│   │   └── roman_profile_summary.json
│   ├── crm.json                 # Network layer contacts and conversation history
│   └── sample/                  # Synthetic examples of each output type
│       ├── sample_contact_dossier.json
│       ├── sample_sprint_card.md
│       └── sample_orientation_memo.md
├── sprints/                     # Sprint card history — one file per week
└── journal.txt                  # Plain text log — append-only, two sentences per entry
```

---

## Why Build This

The specific use case is career development. The generalizable skill is: identify a repeating problem, build a system that solves it continuously, instrument it so it gets smarter over time.

---

## Related

- `ARCHITECTURE.md` — full technical spec, layer-by-layer implementation notes, decision log
- `data/sample/` — synthetic output examples showing what each layer produces
