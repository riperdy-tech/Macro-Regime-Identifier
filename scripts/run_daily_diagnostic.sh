#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Fail fast if the venv's editable install resolves `macro_engine` outside this repo (a stale
# .pth after a reorg silently stopped the daily diagnostic for four months last time). `set -e`
# above means a non-zero exit here aborts the script with the check's own message on stderr.
python "$SCRIPT_DIR/check_macro_engine_import.py"

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
python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml >> "$LOG_PATH" 2>&1
python -m macro_engine.cli build-secular-theme-scores --config config/news_scoring.yaml >> "$LOG_PATH" 2>&1
# S1.7 / S0.7a: the report writer is a separate step from the builder, so a stale report can
# silently disagree with the store (MRI_S0_APPROVAL.md §1 row 14 caught `sector_validation.json`
# doing exactly this). Both run every day so neither can go stale between runs.
python -m macro_engine.cli run-sector-validation --config config/sector_validation.yaml >> "$LOG_PATH" 2>&1
python -m macro_engine.cli write-sector-validation-report --config config/sector_validation.yaml >> "$LOG_PATH" 2>&1
python -m macro_engine.cli run-nber-benchmark --benchmark-config config/nber_recessions.yaml >> "$LOG_PATH" 2>&1
python -m macro_engine.cli write-regime-status >> "$LOG_PATH" 2>&1
python -m macro_engine.cli export-dashboard-data >> "$LOG_PATH" 2>&1
python -m macro_engine.cli write-automation-summary >> "$LOG_PATH" 2>&1
python -m macro_engine.cli export-dashboard-data >> "$LOG_PATH" 2>&1
echo "Daily diagnostic completed. See $LOG_PATH"
