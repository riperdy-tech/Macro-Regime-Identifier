# Macro Regime Intelligence Engine

Local-first U.S. macro regime engine for turning FRED data into transparent
macro regime diagnostics.

This project is an experimental v0.2 release candidate. It is not investment
advice, trading guidance, allocation guidance, or portfolio sizing guidance.
Historical outputs use revised FRED data and are not ALFRED/vintage
point-in-time backtests.

v0.2 adds an experimental sector macro mapper that translates stored macro
regime probabilities and dimension scores into sector tailwind/headwind
diagnostics. Sector scores are not investment recommendations.

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
- Simple transparent formulas, not ML.
- U.S.-focused source universe.
- FRED availability and revision behavior can affect outputs.

## Release Checklist

See `docs/release_checklist_v0_1.md`.

For v0.2 sector mapper release checks, see `docs/release_checklist_v0_2.md`.
