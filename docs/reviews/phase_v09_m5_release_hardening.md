# v0.9-M5 Replay And Operating Trial Release Hardening Review

Verdict: pass.

Release decision: v0.9 is release-ready as an operating-trial and historical replay release.

## What v0.9 Adds

v0.9 adds operating-trial and replay capabilities:

- live daily operating trial review
- dashboard issue review and operating polish
- `replay-news-history` command
- historical news-date replay support
- replay summaries under `outputs/replay/`
- per-replay-date archives under `outputs/archive/<replay-date>/<run_id>/`
- dashboard History support for replay runs
- replay date metadata in daily summaries
- replay documentation and release checklist

## What v0.9 Does Not Do

v0.9 does not:

- add frontend scoring logic
- add frontend AI calls
- change macro formulas
- change sector assumptions
- change news scoring formulas
- change combined diagnostic formulas
- validate model performance
- create a trading system
- create an allocation system
- provide investment advice

Replay is an operating replay, not a predictive backtest.

Macro data is not vintage unless separately supported.

## Validation Command Results

Commands run:

```powershell
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli export-dashboard-data
cd dashboard
npm install
npm run build
```

Results:

```text
pytest: 171 passed, 2 skipped
ruff: passed
validate-config: passed
export-dashboard-data: passed
npm install: previously completed in dashboard workspace
npm run build: passed
```

Config validation reported:

```text
13 sources, 11 dimensions, 6 regimes
```

Dashboard export reported:

```text
data_status: complete
missing_files: none
latest_run_date: 2026-05-03
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-21
```

## Replay Summary

30-day replay command:

```powershell
python -m macro_engine.cli replay-news-history --config config/daily_pipeline.yaml --news-file data/news_pilot/news_items_last_30_days.csv --start-date 2026-04-22 --end-date 2026-05-21 --archive --max-items-per-replay-day 10 --mock-ai
```

Replay result:

```text
status: success
date range: 2026-04-22 to 2026-05-21
replay days: 30
source file rows: 144
selected replay items: 295
successful classifications: 295
failed classifications: 0
classification success rate: 100%
failed daily replay runs: 0
classification mode: mock
```

Days with no raw same-day news items:

```text
2026-04-25
2026-05-03
2026-05-10
```

Archive behavior:

```text
outputs/archive/<replay-date>/<run_id>/
```

Each replay date wrote a separate archive with replay metadata.

## Bugs Fixed During Replay

Replay DB isolation:

- Before: replay daily runs could classify unrelated unclassified rows from the shared local database.
- After: each replay date uses an isolated temporary DuckDB file, keeping replay classification bounded to that replay slice.

Empty news-sector fallback:

- Before: combined diagnostics could fail when a day had macro theme news scores but no sector news score rows.
- After: empty daily/weekly sector-news score frames preserve expected columns and combined diagnostics fall back cleanly to macro-only behavior.

## Dashboard History Behavior

Dashboard History can display replay runs because replay summaries include:

```text
run_mode: replay
replay.replay_mode: true
replay.replay_date
```

The dashboard uses this metadata to distinguish replay rows from live or mock daily operating rows.

## Accumulation Readiness

Latest accumulation refresh after replay reported:

```text
run rows: 1
news history rows: 577
combined history rows: 309
readiness label: insufficient_history
```

The readiness label remains honest. The 30-day replay is operational history in mock mode with query-selected RSS-derived data. It is not enough to claim validation.

## Guardrail Audit

Scanned:

- generated Markdown reports under `outputs/`
- replay Markdown report under `outputs/replay/`
- dashboard source/static text
- dashboard sample fixtures
- v0.9-M4 and v0.9-M5 docs touched in this milestone

Forbidden market-action language scan result:

```text
passed
```

## Repo Hygiene

`git status --short` before staging showed:

```text
M README.md
M docs/model_limitations.md
?? docs/release_checklist_v0_9.md
?? docs/reviews/phase_v09_m5_release_hardening.md
?? .claude/
?? outputs/
```

`.claude/` and `outputs/` are local-only and were not staged.

Confirmed not staged:

- `.env`
- API keys
- `data/news_pilot/`
- `outputs/`
- `outputs/replay/`
- `outputs/archive/`
- `dashboard/public/data/*.json`
- `dashboard/node_modules/`
- `dashboard/dist/`
- `logs/`
- DuckDB files
- caches
- `.claude/`

## Known Limitations

- The 30-day replay used mock classification, not live AI.
- The replay CSV was RSS-derived and query-selected.
- Replay source data may be biased by query design, feed behavior, and snippets.
- Replay does not use vintage macro data.
- Replay does not validate predictive performance.
- The dashboard History view displays operating summaries only.
- The accumulation readiness label remains `insufficient_history`.

## Release Blockers

None.

## Non-Blocking Follow-Ups

- Run a bounded live AI replay on a smaller window before considering a full live 30-day replay.
- Continue collecting balanced real daily news.
- Improve source breadth and stale-group coverage.
- Consider a v1.0 full-system release-readiness review.
- Defer validation or calibration until enough real, balanced, repeated history exists.

## Release Decision

v0.9 is release-ready as an operating-trial and historical replay release.

v0.9 is not a trading system.

v0.9 is not an allocation system.

v0.9 does not provide investment advice.

v0.9 replay is not a predictive backtest.

v0.9 does not validate model performance.

Macro data is not vintage unless separately supported.
