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
