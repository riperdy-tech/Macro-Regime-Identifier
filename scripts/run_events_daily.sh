#!/usr/bin/env sh
# run_events_daily.sh - twin of run_events_daily.ps1: import check, hydration (pull_news_history.sh), then
# `events-daily`. Logs to logs/events/; on failure exits non-zero and writes logs/events/last_failure.txt.
# Never pushes, never registers a scheduled task.
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
  PY="$REPO_ROOT/.venv/Scripts/python.exe"
else
  PY="$REPO_ROOT/.venv/bin/python"
fi
LOG_DIR="$REPO_ROOT/logs/events"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/events_daily_$(date +%Y%m%d_%H%M%S).log"
FAIL="$LOG_DIR/last_failure.txt"
DB_PATH="${MACRO_ENGINE_DB_PATH:-data/macro_engine.duckdb}"
MAX_AGE="${NEWS_HYDRATION_MAX_AGE_HOURS:-48}"

fail() {
  printf 'events_daily FAILED at step %s (exit %s) on %s\nlog: %s\nThe previous outputs/event_register.json was left in place; outputs/event_register_run.json says what failed.\n' \
    "$1" "$2" "$(date '+%Y-%m-%d %H:%M:%S')" "$LOG" > "$FAIL"
  echo "FAILED at $1 (exit $2)" >> "$LOG"
  exit "$2"
}

echo "events_daily start; repo $REPO_ROOT" >> "$LOG"
"$PY" "$SCRIPT_DIR/check_macro_engine_import.py" >> "$LOG" 2>&1 || fail import_check $?
echo "step: hydration" >> "$LOG"
sh "$SCRIPT_DIR/pull_news_history.sh" >> "$LOG" 2>&1 || fail hydration $?
echo "step: events-daily" >> "$LOG"
"$PY" -m macro_engine.cli events-daily --db-path "$DB_PATH" --outputs-dir outputs --max-age-hours "$MAX_AGE" >> "$LOG" 2>&1 || fail events-daily $?
rm -f "$FAIL"
echo "events_daily ok" >> "$LOG"
