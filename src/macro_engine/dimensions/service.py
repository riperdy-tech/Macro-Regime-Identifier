from __future__ import annotations

from pathlib import Path

from macro_engine.dimensions.composition import (
    load_composition_registry,
    validate_registry_against_dimensions,
)
from macro_engine.dimensions.config import load_dimension_config
from macro_engine.dimensions.scoring import DimensionBuildResult, build_dimensions_from_features
from macro_engine.evaluation.calendar import asof_values_to_feature_frame
from macro_engine.evaluation.config import load_evaluation_config
from macro_engine.evaluation.service import build_stored_asof_features
from macro_engine.storage.duckdb_store import DuckDBStore

DEFAULT_MACRO_CONFIG_PATH = "config/phase_b_sources.yaml"
DEFAULT_COMPOSITION_PATH = "config/dimension_composition.yaml"
# Sentinel distinguishing "caller did not pass composition_path" from an explicit None: the
# registry auto-loads only against the real `config/phase_b_sources.yaml`, never against a
# test fixture's own reduced or toy dimension config (same dimension_id, different features
# or weights -- a legitimate test double, not a production drift to flag). A caller that
# wants the registry checked against a non-default config passes composition_path explicitly.
_COMPOSITION_PATH_DEFAULT = object()


def build_stored_dimensions(
    *,
    config_path: str | Path = DEFAULT_MACRO_CONFIG_PATH,
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
    composition_path: str | Path | None = _COMPOSITION_PATH_DEFAULT,  # type: ignore[assignment]
) -> DimensionBuildResult:
    config = load_dimension_config(config_path)
    evaluation_config = load_evaluation_config(config_path)
    if composition_path is _COMPOSITION_PATH_DEFAULT:
        is_production_config = Path(config_path).resolve() == Path(DEFAULT_MACRO_CONFIG_PATH).resolve()
        composition_path = DEFAULT_COMPOSITION_PATH if is_production_config else None
    composition = None
    if composition_path is not None and Path(composition_path).exists():
        composition = load_composition_registry(composition_path).restricted_to(
            {dimension.dimension_id for dimension in config.dimensions}
        )
        validate_registry_against_dimensions(composition, config.dimensions)
    store = DuckDBStore(db_path)
    store.initialize()
    # Both as-of modes consume the as-of feature matrix; they differ only in how an
    # evaluation date decides what it was allowed to see (fixed publication lag vs
    # stored ALFRED vintages). Only `same_date` reads the raw feature frame.
    if evaluation_config.scoring_mode in ("calendar_asof", "point_in_time"):
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
    result = build_dimensions_from_features(features, config.dimensions, composition)
    store.replace_dimension_outputs(
        result.contributions,
        result.dimension_scores,
        result.dimension_health,
    )
    store.export_parquet(parquet_dir)
    return result
