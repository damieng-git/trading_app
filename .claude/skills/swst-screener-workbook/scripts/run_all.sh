#!/usr/bin/env bash
# Re-run every saved paste in ~/swst_reports/inputs through the pipeline, one list at
# a time (Yahoo rate limits; each run rebuilds the hub). Takes run only when the Claude
# token file exists. Log: ~/swst_reports/data/run_all.log
set -u
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
PY=/home/damien/damiverse_apps/trading_app/main/.venv/bin/python
LOG="$HOME/swst_reports/data/run_all.log"
TAKES="--no-takes"
[ -s "$HOME/.config/swst-screener/claude_oauth_token" ] && TAKES=""
mkdir -p "$(dirname "$LOG")"; cd "$SKILL" || exit 1
: > "$LOG"; echo "takes: ${TAKES:-on}" >> "$LOG"
ok=0; fail=0
for f in "$HOME"/swst_reports/inputs/*.txt; do
  echo "=== $(basename "$f") $(date +%H:%M:%S)" >> "$LOG"
  if timeout 900 "$PY" scripts/run_pipeline.py "$f" $TAKES >> "$LOG" 2>&1; then
    ok=$((ok+1)); echo "--- OK" >> "$LOG"
  else
    fail=$((fail+1)); echo "--- FAILED" >> "$LOG"
  fi
done
echo "=== DONE ok=$ok failed=$fail $(date +%H:%M:%S)" >> "$LOG"
