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
