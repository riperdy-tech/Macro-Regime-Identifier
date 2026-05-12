from __future__ import annotations

from pathlib import Path

from macro_engine.diagnostics.config import load_historical_diagnostic_config
from macro_engine.diagnostics.runner import (
    HistoricalDiagnosticResult,
    run_historical_diagnostic,
    summary_to_frame,
)
from macro_engine.storage.duckdb_store import DuckDBStore


def run_stored_historical_diagnostic(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
) -> HistoricalDiagnosticResult:
    config = load_historical_diagnostic_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    regime_scores = store.read_table("regime_scores")
    regime_health = store.read_table("regime_health")
    result = run_historical_diagnostic(regime_scores, regime_health, config)
    store.replace_diagnostic_outputs(
        result.timeline,
        result.transitions,
        summary_to_frame(result.summary),
    )
    store.export_parquet(parquet_dir)
    return result
