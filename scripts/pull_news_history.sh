#!/usr/bin/env sh
# N1.4: pull the durable news-history snapshot from the `run-history` branch and
# hydrate it into the LOCAL store. Insert-if-absent, precedence-protected -- the
# 207 real local classifications can never be displaced. Data flows one way,
# cloud -> run-history -> local; this script never pushes.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PULL_DIR="data/news_history_pull"
DB_PATH="${MACRO_ENGINE_DB_PATH:-data/macro_engine.duckdb}"

echo "Fetching run-history branch..."
git fetch origin run-history

rm -rf "$PULL_DIR"
mkdir -p "$PULL_DIR"
git archive origin/run-history outputs/news_history | tar -x -C "$PULL_DIR"

echo "Hydrating $DB_PATH from $PULL_DIR/outputs/news_history..."
python -m macro_engine.cli import-news-history \
  --db-path "$DB_PATH" \
  --snapshot-dir "$PULL_DIR/outputs/news_history"
