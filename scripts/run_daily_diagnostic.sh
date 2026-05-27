#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="$REPO_ROOT/logs/daily"
mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_PATH="$LOG_DIR/daily_diagnostic_$TIMESTAMP.log"

ARGS="-m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --archive"
if [ "${MACRO_ENGINE_LIVE_AI:-0}" = "1" ]; then
  ARGS="$ARGS --live-ai"
else
  ARGS="$ARGS --mock-ai"
fi

if [ -n "${MACRO_ENGINE_SOURCE_PROFILE:-}" ]; then
  ARGS="$ARGS --source-profile $MACRO_ENGINE_SOURCE_PROFILE"
fi

echo "Running daily diagnostic. Log: $LOG_PATH"
# shellcheck disable=SC2086
python $ARGS > "$LOG_PATH" 2>&1
python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml >> "$LOG_PATH" 2>&1
python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml >> "$LOG_PATH" 2>&1
python -m macro_engine.cli build-secular-theme-scores --config config/news_scoring.yaml >> "$LOG_PATH" 2>&1
python -m macro_engine.cli write-regime-status >> "$LOG_PATH" 2>&1
python -m macro_engine.cli export-dashboard-data >> "$LOG_PATH" 2>&1
python -m macro_engine.cli write-automation-summary >> "$LOG_PATH" 2>&1
python -m macro_engine.cli export-dashboard-data >> "$LOG_PATH" 2>&1
echo "Daily diagnostic completed. See $LOG_PATH"
