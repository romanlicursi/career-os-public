Implementation Constraints & Design Decisions
Established March 2026. Append updates here as new decisions are made.

Core Engineering Principle
Raw data never enters context. Every layer processes inputs into compressed summaries. Summaries are what all downstream layers and synthesis runs load — never the source files.

File Structure
career-os/
├── CLAUDE.md                   # Decision Constitution (Parts 1 + 3). Always auto-loaded.
├── ARCHITECTURE.md             # This file. Reference only — never auto-loaded.
├── journal.txt                 # Raw journal log. Append-only. Two sentences per entry.
├── journal_summary.txt         # Auto-generated rolling summary of entries older than 60 days.
├── BROWSER_CONTEXT.md          # Bridge file for claude.ai sessions. Updated end of every chat.
├── data/
│   ├── raw/                    # Raw scraper outputs. Never loaded into context after processing.
│   ├── summaries/              # Compressed layer outputs. What actually gets loaded.
│   │   ├── layer0_signals.json       # Compressed Layer 0 signals (emerging titles, YC, VC themes).
│   │   ├── layer0_orientation.md     # Latest Layer 0 orientation memo. Overwrites each monthly run.
│   │   ├── layer0_orientation_{date}.md  # Dated archive of each orientation run.
│   │   ├── layer1_digest.json        # Skill/tool trends, workflow verbs, stage signals, persona language.
│   │   ├── layer2_digest.json        # Career path clusters, bridge moves, launchpad environments.
│   │   ├── roman_profile_summary.json  # Compressed Layer 3 LinkedIn profile (raw profile never enters context).
│   │   ├── synthesis_memo.md         # Latest Layer 3 output. Input to sprint card generation.
│   │   └── synthesis_memo_{date}.md  # Dated archive of each synthesis run.
│   └── crm.json                # Network layer contacts and conversation history.
├── scripts/                    # One script per layer. Each script defines its own load pattern.
└── sprints/                    # Sprint card history. One file per week.


Selective Context Loading — Per Task
Each script or session loads only what it needs. No session loads everything.
Task
Files Loaded
Orientation (Layer 0)
CLAUDE.md + layer0_signals.json + layer1_digest + layer2_digest
Weekly synthesis (Layer 3)
CLAUDE.md + layer1_digest + layer2_digest + last 14 journal entries + journal_summary
Layer 1 scrape + process
CLAUDE.md + batch of 10 raw job postings at a time
Sprint card (Layer 4)
CLAUDE.md + synthesis_memo + previous sprint card
Outreach drafting (Layer 5)
CLAUDE.md + target contact dossier + relevant layer1 signal snippet
Journal compression
Raw journal entries older than 60 days → output to journal_summary.txt
claude.ai strategic session
BROWSER_CONTEXT.md only (contains distilled project state)


Scraper Architecture (Layer 1)
Platform: Apify (pre-built LinkedIn Jobs + Indeed actors)
Volume: ~50 postings per weekly run — intentionally constrained to control cost and token load
Cost target: under $5/month at this volume; scale after v1 validated
Raw output saves to: data/raw/YYYY-MM-DD_scrape.json
Processing: batches of 10 postings per Claude Code call (never one giant call)
Each batch contributes to an updated data/summaries/layer1_digest.json
Digest structure: trending tools, workflow ownership verbs, company stage breakdown, operator persona language, AI-exposure classification (automatable / durable / AI-amplified)
Raw files are archived after processing — never reloaded

Journal Compression Model
Raw entries accumulate in journal.txt — plain text, date + two sentences, appended manually
Every synthesis run loads: last 14 entries in full + journal_summary.txt for older history
journal_summary.txt is regenerated monthly by Claude Code from entries older than 60 days
Entries older than 60 days are moved to data/raw/journal_archive/ after summary is generated
Token cost of journal context: fixed and predictable regardless of total log length

BROWSER_CONTEXT.md Bridge Convention
Used to carry context across claude.ai sessions (which have no persistent memory).
Section 1: Project Snapshot — stable overview of what the system is and Roman's profile
Section 2: Decision Log — key architectural and strategic decisions with one-line rationale
Section 3: Current State — updated at the end of every claude.ai chat using Template 2
Template 2 (end-of-chat prompt):
"Before I close this chat, generate an updated BROWSER_CONTEXT.md Section 3 block. Based on everything we discussed: Current State (2–4 bullets), Open questions / next steps (1–3 bullets), any new Decision Log entries. Under 200 words. Format as a markdown block I can paste directly."
Template 3 (opening prompt for new chat):
"Here is my Career OS context file. Read it fully before responding. Treat it as your operating briefing — do not summarize it back to me. My question: [question]" [paste full BROWSER_CONTEXT.md]

Scheduling
Automated runs are handled via GitHub Actions in .github/workflows/ — not local cron.
  layer0.yml  — Monthly cron: 1st of every month at 8am UTC (also triggerable manually via workflow_dispatch)
  layer1.yml  — Every Monday 6am UTC  (Layer 1 scrape + process)
  layer2.yml  — 1st of month 7am UTC  (Layer 2 scrape + process)
  layer3.yml  — Every Tuesday 8am UTC (Layer 3 synthesis, full profile fetch)
  layer4.yml  — Every Tuesday 11am UTC (Layer 4 sprint card generation)
  layer5.yml  — Manual trigger only (workflow_dispatch; Decision #19)
  layer6.yml  — 1st of month 11pm UTC (Layer 6 journal compression)
All workflows commit changed files in data/summaries/ and data/logs/ back to the repo.
data/raw/ is ephemeral per run and never committed.
The repo is the source of truth for all digest and summary files between runs.

Build Order
File structure + CLAUDE.md setup (30 min, one-time)
Apify scraper connection — validate output on 20 postings before building further
Batch processor + layer1_digest generator (Layer 1 live)
journal.txt + compression script (Layer 6 live — just a text file until compression is needed)
First manual synthesis run — Layer 1 digest + journal → synthesis memo → read it, evaluate signal quality
Sprint card generation from synthesis memo (Layer 4 live)
Network layer last — highest quality bar, requires layers 1–4 working first


PART 2: System Architecture
The Career OS is a six-layer intelligence and action system. Each layer feeds the next. The whole system gets sharper over time as data accumulates and the feedback loop closes.

LAYER 0  |  ORIENTATION MODULE

Purpose
Runs before everything else. Asks: given the current landscape, what are the emerging career spaces where someone with Roman's profile has disproportionate leverage? Does not assume RevOps is the answer.

Inputs
Think pieces, investor memos, hiring trend reports
YC batch company hiring patterns
Salary premium signals by skill and role
New functions appearing at companies that didn't exist 3 years ago
AI displacement risk assessments by role category

Outputs
Career space map: 6-8 distinct paths that fit Roman's profile
Each path scored on: viability, 5-year trajectory, AI displacement risk, autonomy ceiling, long-term financial alignment
Honest assessment of where RevOps/GTM sits in the landscape

Implementation note
Three-step pipeline: (1) data collection → data/raw/layer0_raw.json, (2) compression via Sonnet → data/summaries/layer0_signals.json (structured JSON; prevents raw data from entering Opus context), (3) synthesis via Opus → layer0_orientation.md. This matches the same compress-then-synthesize pattern as Layers 1 and 2.

Cadence
Runs monthly. Conclusions from even 30 days ago may already be stale — this layer never assumes its prior output is still correct.

LAYER 1  |  MARKET SIGNAL MODULE

Purpose
Tracks what companies are actually asking for in real-time job postings. Builds a living skills taxonomy that updates weekly. Job postings are 3-6 months stale by the time they're written — so Layer 1 runs two parallel tracks: lagging signal from postings, and leading signal from weak signals that predict where demand is moving before it shows up in job descriptions.

Track A — Job Posting Signal (lagging)
Core tools: table stakes across all postings
Differentiating tools: mentioned specifically by top-tier or high-growth companies
Emerging tools: appearing in newer postings but not yet mainstream — this is the edge
Declining mentions: things aging out — stop investing here
Company velocity: which companies are posting roles repeatedly, signaling active scaling
Compensation ranges by role and seniority

Track B — Weak Signal Sub-Layer (leading)
The more asymmetric edge. Monitors sources that predict what the market will care about before it shows up in postings:
Specific writers and operators whose audience is builders: Kyle Poyar (growth), Lenny Rachitsky (product/growth), Matt Turck (data/AI infrastructure), Jake Dunlap (sales strategy)
YC batch announcements — new company descriptions reveal emerging job functions that don't have titles yet. The person who learns the skill before the job title exists wins.
VC portfolio pages and investment thesis announcements — a new Bessemer or a16z thesis becomes 50 job descriptions in 18 months
Conference talk titles at Dreamforce, SaaStr Annual, RevGenius Summit — whoever is being invited to speak signals what the market is about to care about
LinkedIn creator velocity — when someone goes from 2K to 40K followers in 8 months on a specific topic, that topic is inflecting

Implementation status (as of 2026-03-10): Track B continuous monitoring is not yet implemented in layer1.py. Layer 0 partially covers this function quarterly (RSS from Lenny, SaaStr, a16z, BvP; YC batch data). The distinction: Layer 0 asks "should the direction change?"; Track B asks "what's about to appear in postings that doesn't yet?" Continuous Track B is a future Layer 1 enhancement.

Emerging role watcher
A specific sub-question the Orientation Layer should ask every quarter: what function is being performed informally at high-growth companies right now that will become a named role in 18-24 months? Historically: RevOps itself, Growth Engineering, AI Ops. The person who does the job before it has a name owns the narrative when the title appears. The system should always be scanning for the next one.

Cadence
Job posting scan: weekly, Monday morning digest. Weak signal monitoring: continuous, surfaces notable items in the bi-weekly synthesis memo.

LAYER 2  |  QUALITATIVE INTELLIGENCE MODULE

Purpose
Understands how people a few years ahead of Roman actually built their careers — not just what path they took, but how they thought about it. More anthropological than analytical.

Source material
LinkedIn profiles of 25-27 year olds in target roles
Twitter/X threads where people narrate career decisions candidly
Substack posts from people 3-5 years ahead
Reddit threads (r/sales, r/analytics, r/cscareerquestions) for unfiltered tacit knowledge

Key extractions
Cohort clusters: what are the 3-4 distinct paths that produced people in these roles?
Which cluster is Roman closest to, and what's the median time-to-target for that cluster?
Anomalies: who got there unusually fast, and what did they do differently?
Launchpad companies: which employers appear repeatedly as respected stepping stones?
What do people wish they'd known? What did they optimize for that turned out not to matter?

Cadence
Runs once as a deep pass, then refreshes every 6 months or when the Orientation Layer signals a meaningful direction shift.

LAYER 3  |  SYNTHESIS ENGINE

Purpose
The brain of the system. Combines all upstream data and produces a generative, conversational memo — not a templated report. Surfaces what's unexpected. Functions as a thinking partner, not a report generator.

Key questions it answers
What is the intersection of market demand (Layer 1) and proven paths (Layer 2)? That intersection is the learning priority queue.
What's the delta between what job postings ask for and what people who actually got hired have?
Which companies appear in both job postings AND career path profiles? Those are the highest-conviction targets.
Given the user's specific background — CS, early-stage GTM ops, confirmed RevOps internship — what's the narrative that makes this combination sound intentional?
What should Roman stop doing because it no longer matters?
Where is Roman under-positioned relative to his actual capability?

Design principle
The synthesis prompt is NOT over-programmed. It does not ask for a specific format or a ranked list. It asks: 'Here is everything the system has collected. What's interesting? What's changing? What would you tell a 21-year-old in this position that they probably haven't thought of?' The output is a weekly reflection memo — read like a letter from a well-informed advisor. Treat it as a thinking partner, not a productivity system.

Divergence alerts
The synthesis layer runs a continuous background check: is the current direction still the highest-expected-value path? If the Orientation Layer surfaces a new category with accelerating momentum, AND the qualitative data shows people pivoting away, AND the feedback loop shows cold outreach responses — the system flags it explicitly. The system has no attachment to its previous recommendations. It updates.

Cadence
Bi-weekly synthesis memo. No fixed format. Conversational. Roman reads it, sits with it, responds to it. That dialogue feeds back into Layer 6.

LAYER 4  |  ACTION OUTPUT

Purpose
Translates synthesis into a minimal weekly sprint card. Small and concrete — decision fatigue is the enemy of consistency.

Weekly sprint card contains exactly:
One learning priority — specific, not vague. Not 'learn Salesforce' but 'complete the Salesforce Reporting module and build one practice dashboard this week'
Three outreach targets — with context on why them, why now
One portfolio task — start a new project or make a specific improvement to an existing one
One positioning reminder — one sentence describing how to present yourself this week based on current market signals

Running portfolio brief
A living document that updates automatically as projects and learning milestones are logged. Always reflects current best positioning. Ready to paste into a resume or LinkedIn at any moment.
Not yet implemented (as of 2026-03-10). Sprint cards are generated; auto-updating portfolio brief is a future Layer 4 enhancement.

Public compounding thread
The portfolio layer has two modes: private proof of work (for applications) and public compounding (for inbound). Inbound beats outbound in career development the same way it does in sales. The system should always be asking: what could Roman write, post, or build publicly such that the right people come to him?

Concrete formats that compound publicly:
A specific essay or LinkedIn series with a real, differentiated angle — not 'I'm learning GTM' but 'here is what I learned building outbound automation for a healthcare startup from scratch at 21 with no budget'
Open-sourcing a small, well-documented piece of work with a writeup — a GitHub repo with a real use case is more credible than any credential
Building in public: brief posts that document a real system being built, with specific details that signal genuine expertise

The test for public compounding content: would a hiring manager at an early-stage company, seeing this unprompted, want to reach out? If yes, publish it. The system should generate one public compounding asset per quarter minimum.

Cadence
Weekly sprint card: every Monday. Portfolio brief: updates automatically on project completion. Public compounding asset: one per quarter minimum.

LAYER 5  |  NETWORK INTELLIGENCE

Purpose
Converts the target company list and career path data into warm, precise outreach — and maintains a personal CRM to track the relationships over time.

For each outreach target, Claude Code produces:
A full contact dossier: who they are, what they've built, why they're relevant to Roman specifically
Recent activity they've published that can be referenced authentically
A draft message — short, specific, no fluff, one clear ask
A suggested follow-up if no response in 10 days

Design principle — human in the loop
This is NOT fully autonomous outreach. Roman reviews and sends each message himself. The goal is 10-15 highly targeted, high-quality messages — not 500 automated emails. One wrong message to a warm contact costs more than sending one fewer message. Claude Code drafts; Roman sends.

Personal CRM
Every person contacted, every response, every conversation thread
Surfaces people not followed up with
Flags when a contact posts something relevant to Roman's current direction
Reminds Roman to convert good conversations into coffee chats
The network is a living asset that needs maintenance. The system handles the memory.

Implementation notes (as of 2026-03-10):
- CRM skip logic: if a contact slug already exists in data/crm.json, Layer 5 skips re-drafting. Prevents duplicate Apify + Claude calls on existing contacts.
- MISSING URL handoff: Layer 4 outputs "MISSING" when a contact's LinkedIn URL is unknown. Layer 5 detects this, runs a LinkedIn profile search to resolve it, then proceeds — or flags the contact in the digest email if search returns nothing. This allows Layer 4 to generate sprint cards without blocking on URL lookup. See Decision Log #22.
- Follow-up prompting (surfaces people not followed up with, flags relevant posts, coffee chat reminders) is currently manual — Roman reads the CRM and decides. These are planned enhancements.

LAYER 6  |  FEEDBACK LOOP

Purpose
Closes every loop. Makes the system self-improving by grounding synthesis in Roman's lived experience, not just external data.

The journal
A plain text log file. Every time something happens — outreach sent, response received, conversation had, interview, something read that shifts thinking — Roman adds one entry. Date, what happened, what he noticed. Two sentences max. Friction must be near zero.
Claude Code reads this log as part of every synthesis run. It's not a form or structured database — it's a running journal that the system treats as first-person ground truth.

What the feedback loop enables
Synthesis asks: 'Given what Roman has logged since last time, what's working, what isn't, what should change?'
Outreach response rates inform which message framings resonate
Interview outcomes feed back into which skills and positioning are actually landing
Skill investment vs. market signal alignment: if Roman is spending time on something that's declining in the market, the system flags it

The compounding effect
Six months from now, this system has watched Roman develop in real time, tracked the market in real time, and accumulated a log of every outreach conversation and outcome. The synthesis it produces is no longer drawing only on external data — it's drawing on Roman's history. That's something no career coach, course, or generic AI tool can replicate.

Decision Log
**21** — Drop Levels.fyi from Layer 0 — JS-rendered, not scrapeable via requests; static fallback rejected because hardcoded assumptions are not signals.
**22** — MISSING URL placeholder pattern (Layer 4 → Layer 5 handoff) — Layer 4 outputs "MISSING" for unknown LinkedIn URLs rather than blocking sprint card generation. Layer 5 resolves via profile search at runtime. This keeps the Layer 4 → Layer 5 pipeline non-blocking and separates the concern of URL discovery from sprint card production.
