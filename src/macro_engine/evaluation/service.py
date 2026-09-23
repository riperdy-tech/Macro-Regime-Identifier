from __future__ import annotations

from pathlib import Path

import pandas as pd

from macro_engine.evaluation.asof import build_publication_index
from macro_engine.evaluation.calendar import (
    EvaluationBuildResult,
    build_asof_feature_values,
    build_evaluation_calendar,
)
from macro_engine.evaluation.config import load_evaluation_config
from macro_engine.features.config import load_feature_config
from macro_engine.storage.duckdb_store import DuckDBStore


def build_stored_evaluation_calendar(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
) -> EvaluationBuildResult:
    evaluation_config = load_evaluation_config(config_path)
    feature_config = load_feature_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    features = store.read_features()
    calendar = build_evaluation_calendar(evaluation_config.evaluation_calendar, features)
    # Only read the vintage store when the configured mode needs it: the calendar
    # path must not acquire a dependency (or a cost) it does not use. The boundary does
    # not change that -- dates before it fall back per-date inside the builder.
    publication_index = None
    answered_asofs = None
    if evaluation_config.scoring_mode == "point_in_time":
        publication_index = build_publication_index(store.read_raw_observation_vintages())
        keys_df = store.read_vintage_keys()
        absent_df = store.read_vintage_absence()
        answered_asofs = {}
        combined_frames = []
        if not keys_df.empty:
            combined_frames.append(keys_df[["series_id", "realtime_start"]])
        if not absent_df.empty:
            combined_frames.append(absent_df[["series_id", "realtime_start"]])
        if combined_frames:
            combined = pd.concat(combined_frames, ignore_index=True)
            for series_id, group in combined.groupby("series_id"):
                answered_asofs[str(series_id)] = [
                    pd.Timestamp(t) for t in group["realtime_start"]
                ]
    asof_values = build_asof_feature_values(
        features=features,
        feature_definitions=feature_config.features,
        sources=feature_config.sources,
        calendar=calendar,
        config=evaluation_config.evaluation_calendar,
        scoring_mode=evaluation_config.scoring_mode,
        publication_index=publication_index,
        point_in_time_start=evaluation_config.point_in_time_start,
        answered_asofs=answered_asofs,
        max_evidence_lag_days=evaluation_config.point_in_time_max_lag_days,
    )
    store.replace_evaluation_outputs(calendar, asof_values)
    store.export_parquet(parquet_dir)
    return EvaluationBuildResult(calendar, asof_values)


def build_stored_asof_features(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    parquet_dir: str | Path = "data/raw/fred",
) -> EvaluationBuildResult:
    return build_stored_evaluation_calendar(
        config_path=config_path,
        db_path=db_path,
        parquet_dir=parquet_dir,
    )

