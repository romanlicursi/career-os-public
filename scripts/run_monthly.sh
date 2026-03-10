#!/usr/bin/env bash
# run_monthly.sh — Monthly automation pipeline (runs on the 1st of each month)
#
# Steps:
#   1. Layer 6: compress old journal entries → journal_summary.txt
#   2. Layer 2: scrape ~32 career profiles
#   3. Layer 2: process raw profiles → accumulate layer2_digest.json
#   4. Weekly pipeline (--full): L1 scrape → L1 process → L3 synthesis with fresh profile fetch
#
# Called by cron. Logs to data/pipeline_log.txt.
# Usage: bash scripts/run_monthly.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/data/pipeline_log.txt"
mkdir -p "$ROOT/data"

# Load env vars from shell profile (local use only — GitHub Actions uses repository secrets)
# shellcheck disable=SC1090
source ~/.zshrc 2>/dev/null || true

echo "" >> "$LOG"
echo "════════════════════════════════════" >> "$LOG"
echo "  Monthly pipeline — $(date '+%Y-%m-%d %H:%M')" >> "$LOG"
echo "════════════════════════════════════" >> "$LOG"

cd "$ROOT"

echo "[1/4] Compressing journal (Layer 6)..." | tee -a "$LOG"
python3 scripts/compress_journal.py >> "$LOG" 2>&1

echo "[2/4] Scraping Layer 2 career profiles..." | tee -a "$LOG"
python3 scripts/scrape_layer2.py >> "$LOG" 2>&1

echo "[3/4] Processing Layer 2 → digest..." | tee -a "$LOG"
python3 scripts/process_layer2.py >> "$LOG" 2>&1

echo "[4/4] Running weekly pipeline with fresh profile fetch..." | tee -a "$LOG"
bash scripts/run_weekly.sh --full >> "$LOG" 2>&1

echo "✓ Monthly pipeline complete — $(date '+%Y-%m-%d %H:%M')" | tee -a "$LOG"
