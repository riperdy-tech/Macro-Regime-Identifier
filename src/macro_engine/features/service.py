from __future__ import annotations

from pathlib import Path

from macro_engine.features.config import load_feature_config
from macro_engine.features.feature_builder import FeatureBuildResult, build_features_from_raw
from macro_engine.storage.duckdb_store import DuckDBStore


def build_stored_features(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
) -> FeatureBuildResult:
    config = load_feature_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    raw = store.read_raw_observations()
    result = build_features_from_raw(raw, config.sources, config.features)
    store.upsert_features(result.features)
    store.upsert_feature_health(result.feature_health)
    store.export_parquet(parquet_dir)
    return result
