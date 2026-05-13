from __future__ import annotations

from pathlib import Path

from macro_engine.dimensions.config import load_dimension_config
from macro_engine.dimensions.scoring import DimensionBuildResult, build_dimensions_from_features
from macro_engine.evaluation.calendar import asof_values_to_feature_frame
from macro_engine.evaluation.config import load_evaluation_config
from macro_engine.evaluation.service import build_stored_asof_features
from macro_engine.storage.duckdb_store import DuckDBStore


def build_stored_dimensions(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
) -> DimensionBuildResult:
    config = load_dimension_config(config_path)
    evaluation_config = load_evaluation_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    if evaluation_config.scoring_mode == "calendar_asof":
        asof_values = store.read_asof_feature_values()
        if asof_values.empty:
            asof_values = build_stored_asof_features(
                config_path=config_path,
                db_path=db_path,
                parquet_dir=parquet_dir,
            ).asof_feature_values
        features = asof_values_to_feature_frame(asof_values)
    else:
        features = store.read_features()
    result = build_dimensions_from_features(features, config.dimensions)
    store.replace_dimension_outputs(
        result.contributions,
        result.dimension_scores,
        result.dimension_health,
    )
    store.export_parquet(parquet_dir)
    return result
