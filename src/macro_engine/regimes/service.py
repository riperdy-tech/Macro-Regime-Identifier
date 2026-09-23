from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from macro_engine.regimes.config import load_regime_config
from macro_engine.regimes.scoring import RegimeBuildResult, build_regimes_from_dimensions
from macro_engine.storage.duckdb_store import DuckDBStore


def build_stored_regimes(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
    run_id: str | None = None,
) -> RegimeBuildResult:
    config = load_regime_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    dimensions = store.read_dimension_scores()
    result = build_regimes_from_dimensions(dimensions, config.regimes, config.scoring)
    health = result.regime_health.copy()
    # C3 (MRI_S1_APPROVAL.md S9): current_regime.json's schema-2 source_run_id -- same
    # per-build stamp pattern as features.source_run_id (features/service.py). The whole
    # table is replaced on every build, so one value covers every row.
    health["source_run_id"] = run_id or datetime.now(timezone.utc).isoformat()
    store.replace_regime_outputs(
        result.contributions,
        result.regime_scores,
        health,
    )
    store.export_parquet(parquet_dir)
    return RegimeBuildResult(
        contributions=result.contributions,
        regime_scores=result.regime_scores,
        regime_health=health,
    )
