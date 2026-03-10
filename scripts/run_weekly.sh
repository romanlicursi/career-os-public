#!/usr/bin/env bash
# run_weekly.sh — Weekly automation pipeline (runs every Monday night)
#
# Steps:
#   1. Layer 1: scrape 50 job postings
#   2. Layer 1: process raw scrape → accumulate layer1_digest.json
#   3. Layer 3: synthesize memo (skips Roman's profile fetch — use --full on 1st of month)
#
# Called by cron. Logs to data/pipeline_log.txt.
# Usage: bash scripts/run_weekly.sh [--full]
#   --full  Also fetch Roman's fresh LinkedIn profile (used by monthly pipeline)

set -euo pipefail

FULL_FETCH=false
if [[ "${1:-}" == "--full" ]]; then
    FULL_FETCH=true
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/data/pipeline_log.txt"
mkdir -p "$ROOT/data"

# Load env vars from shell profile (local use only — GitHub Actions uses repository secrets)
# shellcheck disable=SC1090
source ~/.zshrc 2>/dev/null || true

echo "" >> "$LOG"
echo "════════════════════════════════════" >> "$LOG"
echo "  Weekly pipeline — $(date '+%Y-%m-%d %H:%M')" >> "$LOG"
echo "════════════════════════════════════" >> "$LOG"

cd "$ROOT"

echo "[1/3] Scraping Layer 1 job postings..." | tee -a "$LOG"
python3 scripts/scrape_layer1.py >> "$LOG" 2>&1

echo "[2/3] Processing Layer 1 → digest..." | tee -a "$LOG"
python3 scripts/process_layer1.py >> "$LOG" 2>&1

echo "[3/3] Running Layer 3 synthesis..." | tee -a "$LOG"
if [[ "$FULL_FETCH" == "true" ]]; then
    python3 scripts/run_layer3_pipeline.py >> "$LOG" 2>&1
else
    python3 scripts/run_layer3_pipeline.py --skip-fetch >> "$LOG" 2>&1
fi

echo "✓ Weekly pipeline complete — $(date '+%Y-%m-%d %H:%M')" | tee -a "$LOG"
