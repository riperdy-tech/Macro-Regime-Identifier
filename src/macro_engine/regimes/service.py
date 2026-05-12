from __future__ import annotations

from pathlib import Path

from macro_engine.regimes.config import load_regime_config
from macro_engine.regimes.scoring import RegimeBuildResult, build_regimes_from_dimensions
from macro_engine.storage.duckdb_store import DuckDBStore


def build_stored_regimes(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
) -> RegimeBuildResult:
    config = load_regime_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    dimensions = store.read_dimension_scores()
    result = build_regimes_from_dimensions(dimensions, config.regimes, config.scoring)
    store.replace_regime_outputs(
        result.contributions,
        result.regime_scores,
        result.regime_health,
    )
    store.export_parquet(parquet_dir)
    return result
