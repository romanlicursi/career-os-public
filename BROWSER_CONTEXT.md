BROWSER_CONTEXT.md
Career OS — Bridge File for claude.ai Sessions Last updated: March 2026

Section 1: Project Snapshot
What this is: Career OS is a six-layer intelligence and action system built in Claude Code. It scrapes and synthesizes job market data, maps real career paths, generates weekly sprint cards, and produces targeted outreach — all filtered through Roman's Decision Constitution.
Who it's for: Roman Licursi. 21, CS junior at UW-Madison. Gap semester in Prague through late April 2026. Confirmed RevOps internship at Donaldson, summer 2026. Real GTM ops experience via CAUHEC Connect. Optimizing for: FIRE, high autonomy, high-leverage work, career capital that compounds. Not optimizing for: prestige, bureaucracy, AI-replaceable tasks, generic paths.
Strategic identity: Someone who builds and improves the systems that make revenue, growth, and operations work — combining technical depth, business judgment, and AI leverage to create measurable impact.
Tools in use: Claude Code (primary build environment), claude.ai (strategic sessions), Apify (scraping), BROWSER_CONTEXT.md (cross-session bridge).
The meta-point: Building this system is itself the portfolio piece. A partially-built, well-documented Career OS is already career capital.

Section 2: Decision Log
#
Decision
Rationale
1
Use Apify for scraping (not custom scraper)
LinkedIn/Indeed block direct scraping; Apify abstracts this reliably at low cost (~$5/mo at 50 postings/run)
2
Raw data never enters context — summaries only
Prevents context bloat; raw job postings at scale exceed context limits fast
3
Batch Layer 1 processing at 10 postings per call
Keeps each Claude Code call within manageable token range; prevents single-call failure
4
Journal compression: last 14 entries full + rolling summary
Fixed token cost for journal regardless of log length; low friction to write
5
CLAUDE.md = Decision Constitution only (Parts 1 + 3)
Keep persistent context lean and stable; architecture lives in ARCHITECTURE.md, loaded on demand
6
Build Layer 6 (journal) immediately after Layer 1
Feedback loop should be live from the start; costs nothing to maintain
7
Network layer built last
Highest quality bar; outreach quality depends on signal from layers 1–4
8
Start with 50 postings/run, scale after v1 validated
Validate signal quality before investing in infrastructure; cost-controlled from day one
9
Use apimaestro/linkedin-jobs-scraper-api for Layer 1 scraping
No auth required, PAY_PER_EVENT pricing, full descriptions and salary data; practicaltools and intelligent_yaffle both failed (permissions or no results)
10
Don't automate Monday scraper runs until accumulation logic is validated across 3+ weekly runs
Validate before infrastructure per build order principle
11
Use harvestapi/linkedin-profile-search for Layer 2 scraping (not quick_kirigami or Proxycurl/NinjaPear)
Single actor returns full career history in one pass, PAY_PER_EVENT pricing works on free Apify tier
12
Drop education data from Layer 2 scraping
Layer 2 extractions are career-move based; education adds noise without answering any core questions
13
Automated Roman's profile input via Apify scraper (harvestapi/linkedin-profile-scraper)
Fully automated, $0.004/run, captures all LinkedIn sections including projects and posts; validated March 10 2026
14
Automation runs on GitHub Actions, not local cron
Free tier, no laptop dependency, secrets managed securely, workflows commit data back to repo
15
Raw files not committed to GitHub — ephemeral per run
Keeps repo clean; summaries in data/summaries/ are source of truth between runs
16
Build Layer 4 before Layer 5
Sprint card is the weekly forcing function; Layer 5 quality depends on Layers 1–4 validated first
17
Stub outreach targets in Layer 4 (name + one-line rationale only)
Layer 5 not yet built; stub keeps card functional without over-engineering prematurely


Section 3: Current State
Last updated: March 10, 2026

Current State:
- Layer 4 fully live — sprint card format locked (5 fields: learning priority, 3 outreach targets, portfolio task, positioning reminder), first card generated from synthesis_memo.md, output at sprints/sprint_2026-03-10.md
- Outreach targets stubbed (role + one-line rationale) pending Layer 5 — intentional; Target 3 (Donaldson contact) is actionable now
- GitHub Actions automation live for Layers 1–3 + 6; email delivery via Gmail SMTP added to all workflows, GMAIL_APP_PASSWORD secret needed to activate
- Cost validated at ~$1.60/month across all layers, within $5/month target

Next steps:
- Trigger manual Layer 1 workflow run from GitHub Actions tab to confirm full pipeline works end-to-end in the cloud
- Add GMAIL_APP_PASSWORD secret to GitHub repo to activate email delivery
- Begin Layer 5 design once Layer 4 produces at least 2–3 validated sprint cards
