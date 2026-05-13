# Macro Regime Intelligence Engine

Experimental local-first U.S. macro regime classification based on structured
macroeconomic and market-implied data.

This is not investment advice. Phase A is a contract-first prototype: it validates
the corrected configuration schema, computes source health, and runs a tiny offline
classification path from toy data before any full live FRED ingestion.

## Phase A Scope

- Corrected YAML config contract.
- `feature_id` based feature definitions.
- Separate `transform` and `normalization`.
- `core`, `context`, and `experimental` dimensions.
- Source health metadata and validation.
- Context-only fiscal, commodity, and safe-haven dimensions.
- Disabled stale candidate source example.
- Tiny offline end-to-end classification path.
- Tests for config, source health, scoring, and output shape.

Historical evaluation in v0.1 is labeled `historical_revised_data_diagnostic`.
It is not a point-in-time vintage backtest.

## Quickstart

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
macro-engine validate-config
macro-engine run-toy --as-of 2026-05-08
```

## Phase B: Ingestion And Storage Only

Phase B proves the data foundation before any new scoring work. It fetches a
small controlled FRED source set, stores canonical raw tables locally, computes
source health, and exports Parquet files.

Controlled Phase B source file:

```text
config/phase_b_sources.yaml
```

Initial enabled series:

```text
INDPRO, PAYEMS, UNRATE, CPIAUCSL, PCEPI, FEDFUNDS, DGS10, BAA10Y, NFCI, T10Y2Y
```

Commands:

```powershell
python -m macro_engine.cli ingest --config config/phase_b_sources.yaml
python -m macro_engine.cli ingest --series FEDFUNDS
python -m macro_engine.cli health
python -m macro_engine.cli inspect-series FEDFUNDS
```

Storage artifacts:

```text
data/macro_engine.duckdb
data/raw/fred/ingestion_runs.parquet
data/raw/fred/series_metadata.parquet
data/raw/fred/raw_observations.parquet
data/raw/fred/source_health.parquet
```

Phase B does not implement transforms, normalization, dimension scoring, regime
scoring, backtesting, or reports.

## Phase C: Feature Layer Only

Phase C turns stored raw observations into inspectable feature rows. It does not
combine features into dimensions, classify regimes, run backtests, or produce
macro reports.

Feature definitions live alongside the controlled Phase B sources:

```text
config/phase_b_sources.yaml
```

Commands:

```powershell
python -m macro_engine.cli build-features --config config/phase_b_sources.yaml
python -m macro_engine.cli inspect-feature unemployment_6m_change_z
python -m macro_engine.cli feature-health
```

Canonical feature outputs:

```text
features:
  feature_id
  series_id
  date
  raw_value
  transformed_value
  normalized_value
  transform
  normalization
  window_start
  window_end
  valid
  reason

feature_health:
  feature_id
  series_id
  enabled
  valid_count
  invalid_count
  latest_valid_date
  usable
  reason
  reason_counts
```

Initial transforms:

```text
level, diff_3m, diff_6m, diff_12m, yoy_pct_change, spread
```

Initial normalizations:

```text
none, rolling_z_3y, rolling_z_5y, rolling_z_10y, expanding_z
```

## Phase D: Dimension Scoring Only

Phase D combines normalized feature rows into transparent macro dimension scores.
It does not produce regime probabilities, backtests, markdown reports, trading
logic, or final macro labels.

Commands:

```powershell
python -m macro_engine.cli build-dimensions --config config/phase_b_sources.yaml
python -m macro_engine.cli inspect-dimension growth_momentum
python -m macro_engine.cli dimension-health
```

Canonical dimension outputs:

```text
dimension_feature_contributions:
  dimension_id
  feature_id
  date
  normalized_value
  weight
  normalized_weight
  polarity
  signed_value
  contribution
  valid
  reason

dimension_scores:
  dimension_id
  date
  score
  valid_feature_count
  configured_feature_count
  total_configured_weight
  used_weight
  coverage_ratio
  valid
  reason

dimension_health:
  dimension_id
  date
  valid
  valid_feature_count
  required_feature_count
  missing_features
  invalid_features
  reason
```

## Phase E: Regime Scoring Only

Phase E converts dimension scores into transparent regime contributions, raw
regime scores, probabilities, and regime health. It does not run backtests,
write markdown reports, make trading recommendations, or perform portfolio
sizing.

Initial regimes:

```text
goldilocks, reflation, stagflation, recession, tightening
```

Commands:

```powershell
python -m macro_engine.cli build-regimes --config config/phase_b_sources.yaml
python -m macro_engine.cli inspect-regime goldilocks
python -m macro_engine.cli regime-health
python -m macro_engine.cli current-regime
```

Canonical regime outputs:

```text
regime_dimension_contributions:
  regime_id
  dimension_id
  date
  dimension_score
  weight
  normalized_weight
  polarity
  transformed_dimension_value
  contribution
  valid
  reason

regime_scores:
  regime_id
  date
  raw_score
  probability
  rank
  valid_dimension_count
  configured_dimension_count
  coverage_ratio
  valid
  reason

regime_health:
  date
  valid
  dominant_regime
  dominant_probability
  confidence
  entropy
  valid_regime_count
  reason
```

## Phase F: Historical Revised-Data Diagnostic

Phase F consumes stored regime scores and regime health to build a historical
timeline, transition history, and summary diagnostics. This is explicitly a
revised-data diagnostic, not an ALFRED/vintage point-in-time backtest.

Commands:

```powershell
python -m macro_engine.cli run-historical-diagnostic --config config/phase_b_sources.yaml
python -m macro_engine.cli regime-timeline
python -m macro_engine.cli regime-transitions
python -m macro_engine.cli diagnostic-summary
```

Canonical diagnostic outputs:

```text
historical_regime_timeline:
  date
  dominant_regime
  dominant_probability
  second_regime
  second_probability
  confidence
  entropy
  valid_regime_count
  valid
  reason

regime_transitions:
  transition_date
  from_regime
  to_regime
  from_probability
  to_probability
  confidence
  reason

diagnostic_summary:
  start_date
  end_date
  mode
  valid_date_count
  invalid_date_count
  regime_switch_count
  average_regime_duration
  average_confidence
  dominant_regime_distribution
  low_confidence_period_count
  label
```

## Phase G: Human-Readable Reports

Phase G writes JSON and Markdown reports from stored feature, dimension, regime,
and diagnostic outputs. It does not recompute model scores and does not provide
trading, allocation, portfolio sizing, or investment recommendations.

Commands:

```powershell
python -m macro_engine.cli write-current-report --config config/phase_b_sources.yaml
python -m macro_engine.cli write-diagnostic-report --config config/phase_b_sources.yaml
```

Outputs:

```text
outputs/current_regime.json
outputs/current_regime.md
outputs/historical_diagnostic.json
outputs/historical_diagnostic.md
```

Current reports include the dominant regime, probability, confidence, regime
probability table, contribution-backed supporting/opposing dimensions, invalid
or missing dimensions, data health warnings, and an investment-advice disclaimer.

Historical diagnostic reports include revised-data mode, date range, regime
distribution, switch count, average duration, average confidence, low-confidence
period count, latest transitions, invalid date count, and a clear note that the
diagnostic is not a point-in-time vintage backtest.

## Phase H: End-To-End Pipeline Runner

Phase H adds operational orchestration. The pipeline runs existing layers in
sequence and does not duplicate scoring, report generation, or model logic.

Command:

```powershell
python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml
```

Live ingestion requires `FRED_API_KEY`. Normal tests use mocked ingestion and do
not call FRED.

Pipeline order:

```text
ingest
build-features
build-asof-features
build-dimensions
build-regimes
run-historical-diagnostic
write-current-report
write-diagnostic-report
```

Run summaries are stored in `pipeline_runs`:

```text
run_id
started_at
completed_at
config_path
mode
status
failed_step
warning_count
output_dir
```

## Phase J: Evaluation Calendar And As-Of Alignment

Phase J adds a deliberate macro evaluation calendar so mixed-frequency data can
be scored on coherent dates. The normal v0.1 scoring mode is now
`calendar_asof`: stored feature rows are aligned to monthly evaluation dates
using the latest valid observation on or before each evaluation date. This is
still revised-data mode, not an ALFRED/vintage backtest.

Commands:

```powershell
python -m macro_engine.cli build-evaluation-calendar --config config/phase_b_sources.yaml
python -m macro_engine.cli build-asof-features --config config/phase_b_sources.yaml
python -m macro_engine.cli inspect-asof-feature unemployment_6m_change_z
```

Canonical Phase J outputs:

```text
evaluation_calendar:
  evaluation_date
  frequency
  valid
  reason

asof_feature_values:
  evaluation_date
  feature_id
  source_observation_date
  transformed_value
  normalized_value
  lag_days
  valid
  reason
```

Default calendar policy:

```text
frequency: monthly
date_rule: month_start
as_of_policy: latest_observation_on_or_before_date
max_lag_by_frequency:
  daily: 10
  weekly: 21
  monthly: 75
  quarterly: 140
  annual: 450
```

`same_date` scoring is preserved behind `scoring_mode: same_date` for diagnostic
comparisons, but normal pipeline runs should use `calendar_asof`.

## Phase L: Calibration Experiments

Phase L adds an experiment harness for comparing calibration variants against
the same stored calendar-aligned dimension scores. Experiments do not overwrite
production regime tables, reports, or pipeline outputs.

Command:

```powershell
python -m macro_engine.cli run-calibration-experiments --experiment-config config/experiments/phase_l.yaml
```

Experiment outputs:

```text
outputs/experiments/phase_l/
  baseline.json
  temperature_0_8.json
  temperature_0_6.json
  temperature_0_5.json
  sharper_stagflation.json
  sharper_tightening.json
  sharper_reflation.json
  stronger_recession_credit_curve.json
  combined_formula_sharpening.json
  comparison.json
  comparison.md
```

The Phase L harness compares softmax temperature variants, formula-sharpening
variants, historical confidence, transition behavior, dominant regime
distribution, and pairwise raw-score correlations. It is a calibration diagnostic
only: no source expansion, trading logic, allocation logic, or production formula
replacement is performed by the runner.

## Phase O: Formula Experiments

Phase O uses the same experiment harness to test targeted formula ideas from the
Phase N design review. It keeps production formulas unchanged and writes outputs
separately.

Command:

```powershell
python -m macro_engine.cli run-calibration-experiments --experiment-config config/experiments/phase_o.yaml
```

Experiment outputs:

```text
outputs/experiments/phase_o/
  baseline.json
  tightening_growth_resilience.json
  stagflation_interaction_reduced_additive.json
  reflation_inflation_cap.json
  recession_growth_confirmation.json
  policy_tightening_heavy.json
  policy_stagflation_less_policy.json
  combined_overlap_reduction.json
  comparison.json
  comparison.md
```

Phase O is still experimental review work. It does not promote formula variants
to production.
