# Macro Regime Intelligence Engine

Local-first U.S. macro regime engine for turning FRED data into transparent
macro regime diagnostics.

This project is an experimental v0.8 release candidate moving through v0.9
daily operating trials. It is not investment advice, trading guidance,
allocation guidance, or portfolio sizing guidance. Historical outputs use
revised FRED data and are not ALFRED/vintage point-in-time backtests.

v0.2 adds an experimental sector macro mapper that translates stored macro
regime probabilities and dimension scores into sector tailwind/headwind
diagnostics. Sector scores are not investment recommendations.

v0.3 adds an AI-assisted news/event diagnostic overlay. It ingests local or
manual text items, classifies them into structured macro themes and sector
impacts, aggregates those classifications into deterministic news scores, and
optionally combines bounded sector news scores with v0.2 sector macro scores.
The combined output is separate from v0.1 macro scoring and v0.2 sector macro
scoring.

v0.4 adds real-news pilot monitoring. It tracks input quality, source balance,
classification success/retry/repair rates, and whether the bounded news overlay
changes sector diagnostics too aggressively. It does not tune news scoring
formulas.

v0.5 adds daily operating workflow and accumulation tracking. It can run the
macro, sector, news, combined, and monitoring steps from one command, archive
daily reports, and summarize whether enough real-news history exists for later
validation work.

v0.6 adds source coverage and scheduled-run support. It helps track whether
real-news collection is balanced across source groups and provides scripts and a
runbook for repeatable daily operation.

v0.7 and v0.8 add a read-only dashboard and lightweight operating history. The
dashboard displays backend-generated JSON only; it does not score data, call AI
providers, or make market-action decisions.

## What It Does

The engine fetches a controlled U.S. macro source set, stores raw observations,
builds normalized features, scores macro dimensions, converts dimensions into
regime probabilities, applies a small reported-transition filter, and writes
JSON/Markdown reports.

Current production model core:

```text
FRED sources
-> raw observations
-> transformed/normalized features
-> monthly as-of feature alignment
-> dimension scores
-> raw regime probabilities
-> reported regime state
-> revised-data diagnostics
-> JSON/Markdown reports
```

The optional v0.2 sector layer runs after the macro pipeline:

```text
stored macro outputs
-> sector regime priors
-> sector dimension exposures
-> sector macro scores
-> sector ranking report
```

The optional v0.3 news layer is an additive overlay:

```text
local news/event text
-> AI or mock classification
-> macro themes and sector impacts
-> deterministic news theme and sector scores
-> experimental combined macro-sector-news diagnostics
```

Structured macro data remains scored by deterministic Python/config logic.
Sector macro mapping remains deterministic. AI is used only to interpret
unstructured text into structured, auditable signals; aggregation and combined
diagnostics remain transparent and component-based.

The v0.4 monitoring layer wraps the news workflow with operating-quality checks:

```text
local real-news file or sample
-> input quality and balance checks
-> AI/mock classification quality checks
-> news score and combined overlay checks
-> monitoring report
```

The model preserves two separate regime views:

- Raw monthly signal: unsmoothed monthly regime probabilities and raw dominant
  regime.
- Reported regime state: transition-filtered state used for human-readable
  timeline/reporting.

The reported state changes only when the raw leader clears the configured
confidence threshold. This reduces low-confidence label whipsaws while keeping
raw probabilities visible.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Create a local `.env` file:

```powershell
Copy-Item .env.example .env
```

Then set:

```text
FRED_API_KEY=your_key_here
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_MODEL=deepseek-v4-flash
```

Never commit `.env`.

## Core Commands

Validate production config:

```powershell
python -m macro_engine.cli validate-config
```

Run tests and lint:

```powershell
python -m pytest
python -m ruff check .
```

Run the full live pipeline:

```powershell
python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml
```

Inspect current regime:

```powershell
python -m macro_engine.cli current-regime
```

Inspect diagnostics:

```powershell
python -m macro_engine.cli diagnostic-summary
python -m macro_engine.cli regime-transitions
python -m macro_engine.cli regime-timeline
```

Inspect health and intermediate layers:

```powershell
python -m macro_engine.cli health
python -m macro_engine.cli feature-health
python -m macro_engine.cli dimension-health
python -m macro_engine.cli regime-health
```

Write reports from stored outputs:

```powershell
python -m macro_engine.cli write-current-report --config config/phase_b_sources.yaml
python -m macro_engine.cli write-diagnostic-report --config config/phase_b_sources.yaml
```

Build and inspect v0.2 sector diagnostics from stored macro outputs:

```powershell
python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml
python -m macro_engine.cli current-sector-ranking
python -m macro_engine.cli inspect-sector energy
python -m macro_engine.cli sector-health
python -m macro_engine.cli write-sector-report --config config/phase_b_sources.yaml
```

Run v0.2 sector ETF proxy validation when local or provider-backed proxy price
data is available:

```powershell
python -m macro_engine.cli ingest-sector-proxy-prices --config config/sector_validation.yaml
python -m macro_engine.cli run-sector-validation --config config/sector_validation.yaml
python -m macro_engine.cli sector-validation-summary
python -m macro_engine.cli write-sector-validation-report --config config/sector_validation.yaml
```

The default local CSV validation path is:

```text
data/sector_proxy_prices.csv
```

Expected schema:

```csv
ticker,date,close
```

Required tickers:

```text
SPY, XLC, XLY, XLP, XLE, XLF, XLV, XLI, XLK, XLB, XLRE, XLU
```

Run sector calibration experiments without mutating production sector configs:

```powershell
python -m macro_engine.cli run-sector-calibration-experiments --experiment-config config/experiments/sector_calibration_v02_m1.yaml
```

Run v0.3 local news/event ingestion and mock classification:

```powershell
python -m macro_engine.cli ingest-news --config config/news_sources.yaml
python -m macro_engine.cli classify-news --config config/news_ai.yaml
python -m macro_engine.cli classify-news --config config/news_ai.yaml --max-items 25 --only-unclassified
python -m macro_engine.cli news-classification-summary
python -m macro_engine.cli write-news-report --config config/news_ai.yaml
```

`config/news_ai.yaml` defaults to `mock_mode: true` and `enable_live_ai: false`.
Normal tests do not require a live AI key. To use DeepSeek manually, set
`DEEPSEEK_API_KEY` in `.env`, set `mock_mode: false`, and set
`enable_live_ai: true` in a local, intentionally managed config.

Aggregate classified news into deterministic diagnostic scores:

```powershell
python -m macro_engine.cli build-news-scores --config config/news_scoring.yaml
python -m macro_engine.cli current-news-summary
python -m macro_engine.cli inspect-news-score --sector energy
python -m macro_engine.cli inspect-news-score --theme monetary_tightening
python -m macro_engine.cli write-news-score-report --config config/news_scoring.yaml
```

Build the experimental combined macro-sector-news diagnostic:

```powershell
python -m macro_engine.cli build-combined-sector-diagnostics --config config/sector_news_integration.yaml
python -m macro_engine.cli current-combined-sector-ranking
python -m macro_engine.cli inspect-combined-sector energy
python -m macro_engine.cli write-combined-sector-report --config config/sector_news_integration.yaml
```

Validate and run v0.4 real-news monitoring:

```powershell
python -m macro_engine.cli validate-news-monitoring --config config/news_monitoring.yaml
python -m macro_engine.cli run-news-monitoring --config config/news_monitoring.yaml
python -m macro_engine.cli news-monitoring-summary
python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml
```

Run the v0.5 daily operating workflow:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml
```

Useful options:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --run-date 2026-05-18 --mock-ai --archive
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --source-profile pilot_balanced_local_csv --live-ai
```

The default config remains mock-safe for news classification. Live AI must be
enabled intentionally through local configuration and command flags.
Live classification is bounded by `live_ai_safety.max_items_per_run` and uses
`classify_only_unclassified` by default so interrupted runs can resume without
reprocessing completed items.

Track accumulated news history:

```powershell
python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml
python -m macro_engine.cli news-accumulation-summary
python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml
```

Accumulation readiness labels are operating coverage labels, not validation
claims:

```text
insufficient_history  fewer than 5 run dates or fewer than 100 classified items
early_history         5 to 20 run dates
monitor_ready         20+ run dates with reasonable source coverage
validation_candidate 60+ run dates with stable source coverage
```

Mock daily runs are useful for release checks and plumbing, but they do not
validate signal quality. Real validation requires repeated balanced real-news
runs, stable source coverage, and enough accumulated history for later review.

Validate source coverage and daily operating prerequisites:

```powershell
python -m macro_engine.cli validate-news-sources --config config/news_source_watchlist.yaml
python -m macro_engine.cli news-source-coverage --config config/news_source_watchlist.yaml
python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml
python -m macro_engine.cli daily-health-check --config config/daily_pipeline.yaml
```

Scheduled/manual script entrypoints:

```powershell
.\scripts\run_daily_diagnostic.ps1
```

```bash
./scripts/run_daily_diagnostic.sh
```

Set `MACRO_ENGINE_LIVE_AI=1` only for intentional live AI runs. Logs are written
under `logs/daily/` and remain ignored by git.

For a balanced real-news pilot, place a local file at:

```text
data/news_pilot/news_items_balanced.csv
```

Expected schema:

```csv
title,body,source,source_url,published_at,source_group
```

Optional columns include `query_group`, `region`, `sectors_hint`, and
`raw_metadata_json`. `source_group` should use one of the configured v0.6
groups. If it is absent, the ingestion layer may use an explicit `query_group`
or an audited `source_group_rules` entry in `config/news_sources.yaml`; otherwise
the item remains `unmapped` and the coverage report warns. Local pilot data
stays ignored by git unless a tiny public example is intentionally added.

The source coverage report tracks:

```text
source_group_count
unmapped_item_count / unmapped_pct
old_item_count / old_item_pct
missing groups
stale groups
single-group concentration
```

These are operating-quality checks. They do not validate predictive usefulness
and they do not justify scoring calibration by themselves.

## Dashboard

v0.7 adds a local read-only dashboard under `dashboard/`. It displays generated
backend JSON snapshots and does not calculate macro, sector, news, or combined
scores in the frontend.

Dashboard architecture:

```text
Python backend -> generated JSON outputs -> export-dashboard-data -> dashboard/public/data -> React display
```

The dashboard does not run ingestion, classify news, call AI providers, store
API keys, or write diagnostic state.

Refresh dashboard data:

```powershell
python -m macro_engine.cli export-dashboard-data
```

Run the dashboard locally:

```powershell
cd dashboard
npm install
npm run dev
```

Build the dashboard:

```powershell
cd dashboard
npm run build
```

GitHub Pages:

```text
https://riperdy-tech.github.io/Macro-Regime-Identifier/
```

The Pages workflow builds the dashboard from `dashboard/` and uses the project
base path `/Macro-Regime-Identifier/`. It deploys committed sample fixtures, not
local generated dashboard data.

Typical local review flow:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
python -m macro_engine.cli export-dashboard-data
cd dashboard
npm run dev
```

The dashboard first looks for exported files in:

```text
dashboard/public/data/
```

If exported data is missing, it falls back to safe synthetic fixtures in:

```text
dashboard/public/sample-data/
```

Real exported dashboard data is generated and ignored by git. The frontend is a
display layer only: no API keys, AI calls, or scoring logic belong in the
dashboard.

v0.8 adds daily operating aids and lightweight history visibility:

```text
docs/operations/daily_dashboard_checklist.md
docs/operations/dashboard_issue_log_template.md
dashboard/public/data/history_index.json
```

The history index is derived from archived daily summaries under
`outputs/archive/`. It is read-only dashboard context, not a new scoring model.
If there are too few archived runs, the dashboard shows that there is not enough
history yet.

v0.8 release position: the dashboard operating loop is release-candidate ready
for daily local use. It remains display-only and does not validate predictive
performance.

Safe to commit:

```text
dashboard source code
dashboard package manifests
synthetic sample fixtures under dashboard/public/sample-data/
dashboard documentation
```

Do not commit:

```text
real generated dashboard data under dashboard/public/data/
outputs/
API keys or .env files
dashboard/node_modules/
dashboard/dist/
```

## Production Source Set

Production config: `config/phase_b_sources.yaml`

Enabled production sources:

```text
INDPRO        Industrial Production Total Index
PAYEMS        All Employees, Total Nonfarm
UNRATE        Unemployment Rate
CPIAUCSL      Consumer Price Index
PCEPI         PCE Price Index
FEDFUNDS      Effective Federal Funds Rate
DGS10         10-Year Treasury Rate
BAA10Y        Baa corporate spread relative to 10-year Treasury
NFCI          Chicago Fed National Financial Conditions Index
T10Y2Y        10-Year Treasury minus 2-Year Treasury spread
ICSA          Initial jobless claims
BAMLH0A0HYM2  ICE BofA US High Yield OAS
```

Disabled health-test source:

```text
USSLIND       Disabled because it is stale/discontinued for live v0.1 use
```

Not promoted:

```text
RSAFS
T5YIE
```

## Model Configuration

Important production settings:

```yaml
scoring_mode: calendar_asof

regime_scoring:
  probability_method: softmax
  softmax_temperature: 0.6

historical_diagnostic:
  mode: revised_data
  transition_filter:
    enabled: true
    min_confidence_to_switch: 0.02
```

The current regime set is:

```text
goldilocks
reflation
stagflation
recession
tightening
```

The v0.2 sector layer uses 11 GICS-style U.S. sectors. Proxy tickers are
reporting and later validation references only:

```text
communication_services     XLC
consumer_discretionary     XLY
consumer_staples           XLP
energy                     XLE
financials                 XLF
health_care                XLV
industrials                XLI
information_technology     XLK
materials                  XLB
real_estate                XLRE
utilities                  XLU
```

Sector assumptions live in:

```text
config/sectors.yaml
config/sector_exposures.yaml
config/sector_regime_priors.yaml
config/sector_validation.yaml
```

News-layer configs live in:

```text
config/news_sources.yaml
config/news_themes.yaml
config/news_ai.yaml
config/news_scoring.yaml
config/sector_news_integration.yaml
config/news_monitoring.yaml
config/daily_pipeline.yaml
config/news_accumulation.yaml
```

Synthetic news examples live in:

```text
data/examples/sample_news_items.csv
```

## Pipeline Stages

You can run stages independently when debugging:

```powershell
python -m macro_engine.cli ingest --config config/phase_b_sources.yaml
python -m macro_engine.cli build-features --config config/phase_b_sources.yaml
python -m macro_engine.cli build-asof-features --config config/phase_b_sources.yaml
python -m macro_engine.cli build-dimensions --config config/phase_b_sources.yaml
python -m macro_engine.cli build-regimes --config config/phase_b_sources.yaml
python -m macro_engine.cli run-historical-diagnostic --config config/phase_b_sources.yaml
python -m macro_engine.cli write-current-report --config config/phase_b_sources.yaml
python -m macro_engine.cli write-diagnostic-report --config config/phase_b_sources.yaml
```

## Outputs

Generated reports:

```text
outputs/current_regime.json
outputs/current_regime.md
outputs/historical_diagnostic.json
outputs/historical_diagnostic.md
outputs/current_sector_ranking.json
outputs/current_sector_ranking.md
outputs/sector_validation.json
outputs/sector_validation.md
outputs/news_classification_report.json
outputs/news_classification_report.md
outputs/news_score_report.json
outputs/news_score_report.md
outputs/combined_sector_diagnostic.json
outputs/combined_sector_diagnostic.md
outputs/news_monitoring_report.json
outputs/news_monitoring_report.md
outputs/daily_diagnostic_summary.json
outputs/daily_diagnostic_summary.md
outputs/news_accumulation_report.json
outputs/news_accumulation_report.md
outputs/archive/YYYY-MM-DD/<run_id>/
```

Local storage:

```text
data/macro_engine.duckdb
data/raw/fred/*.parquet
```

Generated outputs, local DuckDB files, Parquet exports, caches, and `.env` are
ignored by git. They should be regenerated locally, not committed.

## Reports

The current report includes:

- latest valid date
- reported regime
- reported probability/confidence
- transition filter reason
- raw monthly dominant regime
- raw monthly probability/confidence
- full raw probability table
- supporting/opposing dimension contributions
- source/data health warnings

The historical diagnostic report includes:

- revised-data mode
- date range
- reported regime distribution
- reported transition count
- average regime duration
- average confidence
- low-confidence periods
- invalid date count

The current sector report includes:

- latest valid macro date
- reported macro regime
- raw macro leader
- macro confidence
- sector ranking
- raw and confidence-adjusted sector scores
- supporting/opposing sector score components
- low-confidence warnings
- non-advice disclaimer

The sector validation report is a diagnostic sanity check only. It compares
stored sector scores with future sector ETF proxy returns relative to SPY. It is
not a trading backtest and does not model transaction costs, slippage, execution
constraints, or allocation sizing.

The current v0.2 sector calibration result is weak/mixed. The sector mapper is
release-ready only as an experimental diagnostic layer, not as an empirically
validated ranking or decision system.

The news classification report is also diagnostic only. AI classifications are
interpretive and can be wrong, incomplete, or overly confident. They should be
reviewed before being used in any research workflow.

The news score report aggregates stored classifications into daily and weekly
macro theme and sector news scores. Each aggregate can be traced to
`news_score_components`.

The combined sector diagnostic report is an experimental overlay. It combines
cross-sectionally normalized sector macro scores with bounded sector news
scores. Missing or thin news coverage falls back to macro-only behavior for the
affected sector. Combined validation remains limited until there is enough real
classified news history.

## Experiments

Experiment configs live under:

```text
config/experiments/
```

Experiment outputs are generated under:

```text
outputs/experiments/
```

Experiments should not overwrite production regime tables or mutate production
config unless a later promotion phase explicitly approves the change.

## Troubleshooting

Missing `FRED_API_KEY`:

```text
FRED_API_KEY is required for live pipeline ingestion
```

Fix: create `.env` from `.env.example` and set the key.

Transient FRED HTTP errors:

- Rerun the pipeline. Ingestion is idempotent.
- Check `python -m macro_engine.cli health`.
- Confirm failed/stale sources before interpreting the regime output.

Old or missing current regime:

- Check source health.
- Check feature health.
- Check dimension health.
- Confirm as-of alignment did not mark required features stale.

DuckDB file lock on Windows:

- Avoid running multiple CLI inspection commands against the same `.duckdb` file
  in parallel.
- Rerun commands sequentially if you see a file-in-use error.

Low confidence:

- This is often expected near regime boundaries.
- Read the raw probability table and transition filter reason before
  interpreting the reported label.

## Known Limitations

See `docs/model_limitations.md`.

Key limitations:

- Uses revised FRED data, not vintage point-in-time data.
- No ALFRED/vintage backtesting yet.
- No trading, allocation, or portfolio logic.
- Sector scores are macro diagnostics, not sector recommendations.
- Sector ETF proxy validation is not a trading backtest.
- AI news classification is diagnostic only and can be wrong.
- News score aggregation is deterministic, but depends on AI classification
  quality and source coverage.
- Combined macro-sector-news diagnostics are experimental and not empirically
  validated with real news history yet.
- Simple transparent formulas, not ML.
- U.S.-focused source universe.
- FRED availability and revision behavior can affect outputs.

## Release Checklist

See `docs/release_checklist_v0_1.md`.

For v0.2 sector mapper release checks, see `docs/release_checklist_v0_2.md`.

For v0.3 news and combined diagnostic release checks, see
`docs/release_checklist_v0_3.md`.
