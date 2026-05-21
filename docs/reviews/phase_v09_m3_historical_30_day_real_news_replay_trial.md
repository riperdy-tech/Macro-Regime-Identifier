# v0.9-M3 Historical 30-Day Real-News Replay Trial

## Decision

v0.9-M3 is implemented, but the real 30-day replay is blocked by missing mapped
real-news data.

The replay command now exists and is covered by deterministic tests. It can
load a mapped CSV, split items by `published_at`, run daily replay dates,
archive each replay day separately, label archived summaries as replay runs, and
export dashboard history. The preferred local data file was not available in
this workspace, so no real multi-week replay result is claimed.

## Replay Data

Preferred file:

```text
data/news_pilot/news_items_last_30_days.csv
```

Status:

```text
missing
```

Required columns:

```text
title
body
source
source_url
published_at
source_group
```

Because the file is missing, the generated replay summary is blocked:

```text
outputs/replay/replay_summary.json
outputs/replay/replay_summary.md
```

Blocked reason:

```text
mapped real-news file not found: data/news_pilot/news_items_last_30_days.csv
```

## Replay Command

Added CLI command:

```powershell
python -m macro_engine.cli replay-news-history `
  --config config/daily_pipeline.yaml `
  --news-file data/news_pilot/news_items_last_30_days.csv `
  --start-date YYYY-MM-DD `
  --end-date YYYY-MM-DD `
  --archive
```

Defaults are conservative:

```text
mock_ai: true
live_ai: false
max_items_per_replay_day: 10
include_prior_items: true
only_unclassified: true
archive: true
```

Live AI replay requires an explicit `--live-ai --no-mock-ai` style run and
should remain bounded.

## Implemented Behavior

The replay implementation supports:

- loading a mapped local CSV
- validating required columns
- parsing and sorting `published_at`
- choosing a replay window
- grouping/filtering items by replay date
- preventing future news from entering earlier replay days
- optional cumulative replay using items up to each date
- per-day temporary news source configs
- per-day daily diagnostic runs
- archive paths under `outputs/archive/<replay-date>/<run_id>/`
- replay metadata in daily summary JSON and Markdown
- replay summary JSON and Markdown under `outputs/replay/`
- dashboard history rows that distinguish `daily` and `replay` run modes

## Date Leakage Review

Unit tests cover date grouping and future-news exclusion. For replay date `D`,
the filtered replay input does not include items after `D`.

Important caveat: this is not vintage macro evaluation. The existing macro
pipeline may still use currently available or revised macro data. This milestone
only controls news-item timing inside the replay input.

## Dashboard History Behavior

Dashboard history export now includes:

```text
run_mode
replay_date
```

The History table displays a Mode column and shows replay dates when present.
This keeps replay runs distinguishable from normal daily runs.

## Archive Behavior

Tests verify that replay runs archive by replay date. Example behavior:

```text
outputs/archive/2026-05-03/<run_id>/daily_diagnostic_summary.json
```

Archived summary files are marked with:

```json
{
  "run_mode": "replay",
  "replay": {
    "replay_mode": true,
    "replay_date": "YYYY-MM-DD"
  }
}
```

## Accumulation and Monitoring

After the blocked replay command, the existing operational reports were
refreshed:

```powershell
python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml
python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml
python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml
python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml
python -m macro_engine.cli export-dashboard-data
```

Current accumulation readiness remains:

```text
insufficient_history
```

That is expected because the real 30-day mapped replay did not run.

## Source Coverage Findings

No new real source coverage was evaluated because the replay data file is
missing. The next real replay should use mapped `source_group` values so daily
source coverage can be reviewed across the replay window.

## Tests

Added tests for:

- replay date grouping
- replay window filtering
- no future news leakage
- missing news file handling
- mock replay path
- replay archive path by replay date
- replay metadata in daily summaries
- dashboard history including replay runs
- CLI blocked missing-file behavior
- no live AI calls in tests

## Validation Results

Validation run:

```text
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli replay-news-history --config config/daily_pipeline.yaml --news-file data/news_pilot/news_items_last_30_days.csv --archive
python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml
python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml
python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml
python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml
python -m macro_engine.cli export-dashboard-data
cd dashboard
npm run build
```

Results:

```text
pytest: 170 passed, 2 skipped
ruff: passed
validate-config: passed
replay-news-history: blocked cleanly by missing mapped 30-day CSV
dashboard export: passed
dashboard build: passed
```

The final dashboard export was restored with the latest normal daily run after
test replay runs exercised archive/history behavior:

```text
latest_run_date: 2026-05-21
latest_run_mode: daily
history_runs: 12
```

## Release Readiness

v0.9 should not proceed to release hardening as a completed real-news replay
until `data/news_pilot/news_items_last_30_days.csv` is supplied or generated.

Next step:

```text
provide mapped 30-day real-news CSV, then rerun replay-news-history in mock mode first
```

Only after the mock replay archives cleanly should bounded live AI replay be
considered.

## Explicit Limits

This is not a trading backtest.

This is not investment advice.

This does not validate predictive performance.

Macro data is not vintage unless separately supported by the backend.

No macro, sector, news, combined, or monitoring scoring formulas were changed.
