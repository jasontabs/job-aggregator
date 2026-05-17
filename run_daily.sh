#!/bin/zsh
# Daily job scrape + rank pipeline.
# Logs go to: <project>/logs/run_YYYY-MM-DD.log

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LOG="$LOG_DIR/run_$(date +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

{
  echo "=== Job Aggregator run started: $(date) ==="

  # Load ANTHROPIC_API_KEY
  if [ -f "$HOME/.job_aggregator_env" ]; then
    source "$HOME/.job_aggregator_env"
  elif [ -f "$HOME/.zshrc" ]; then
    source "$HOME/.zshrc" 2>/dev/null
  fi

  if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY not set. Add it to ~/.job_aggregator_env"
    exit 1
  fi

  echo "--- Scraper ---"
  /opt/homebrew/bin/python3 "$PROJECT_DIR/Skills/job-scraper/run.py"
  SCRAPER_EXIT=$?
  [ $SCRAPER_EXIT -ne 0 ] && echo "WARNING: Scraper exited with code $SCRAPER_EXIT — continuing to emailer"

  echo ""
  echo "--- Ranker ---"
  /opt/homebrew/bin/python3 "$PROJECT_DIR/Skills/job-ranker/run.py"
  RANKER_EXIT=$?
  [ $RANKER_EXIT -ne 0 ] && echo "WARNING: Ranker exited with code $RANKER_EXIT — continuing to emailer"

  echo ""
  echo "--- Emailer ---"
  /opt/homebrew/bin/python3 "$PROJECT_DIR/Skills/job-emailer/run.py"
  EMAILER_EXIT=$?

  echo ""
  echo "=== Done: $(date) — scraper=$SCRAPER_EXIT ranker=$RANKER_EXIT emailer=$EMAILER_EXIT ==="
} >> "$LOG" 2>&1
