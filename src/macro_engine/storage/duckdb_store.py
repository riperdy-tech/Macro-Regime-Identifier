from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


class DuckDBStore:
    def __init__(self, db_path: str | Path = "data/macro_engine.duckdb") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    status TEXT,
                    series_requested INTEGER,
                    series_succeeded INTEGER,
                    series_failed INTEGER,
                    errors JSON
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS series_metadata (
                    series_id TEXT PRIMARY KEY,
                    title TEXT,
                    frequency TEXT,
                    units TEXT,
                    seasonal_adjustment TEXT,
                    last_updated TIMESTAMP,
                    notes_hash TEXT,
                    notes TEXT,
                    fetched_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_observations (
                    series_id TEXT,
                    date DATE,
                    value DOUBLE,
                    realtime_start DATE,
                    realtime_end DATE,
                    source TEXT,
                    fetched_at TIMESTAMP,
                    frequency TEXT,
                    units TEXT,
                    PRIMARY KEY(series_id, date, realtime_start, realtime_end)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS source_health (
                    series_id TEXT PRIMARY KEY,
                    last_observation_date DATE,
                    days_since_last_observation INTEGER,
                    expected_frequency TEXT,
                    stale_flag BOOLEAN,
                    missing_count INTEGER,
                    usable BOOLEAN,
                    reason TEXT,
                    checked_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS features (
                    feature_id TEXT,
                    series_id TEXT,
                    date DATE,
                    raw_value DOUBLE,
                    transformed_value DOUBLE,
                    normalized_value DOUBLE,
                    transform TEXT,
                    normalization TEXT,
                    window_start DATE,
                    window_end DATE,
                    valid BOOLEAN,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS feature_health (
                    feature_id TEXT PRIMARY KEY,
                    series_id TEXT,
                    enabled BOOLEAN,
                    valid_count INTEGER,
                    invalid_count INTEGER,
                    latest_valid_date DATE,
                    usable BOOLEAN,
                    reason TEXT,
                    reason_counts JSON
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS dimension_feature_contributions (
                    dimension_id TEXT,
                    feature_id TEXT,
                    date DATE,
                    normalized_value DOUBLE,
                    weight DOUBLE,
                    normalized_weight DOUBLE,
                    polarity TEXT,
                    signed_value DOUBLE,
                    contribution DOUBLE,
                    valid BOOLEAN,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS dimension_scores (
                    dimension_id TEXT,
                    date DATE,
                    score DOUBLE,
                    valid_feature_count INTEGER,
                    configured_feature_count INTEGER,
                    total_configured_weight DOUBLE,
                    used_weight DOUBLE,
                    coverage_ratio DOUBLE,
                    valid BOOLEAN,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS dimension_health (
                    dimension_id TEXT,
                    date DATE,
                    valid BOOLEAN,
                    valid_feature_count INTEGER,
                    required_feature_count INTEGER,
                    missing_features JSON,
                    invalid_features JSON,
                    reason TEXT
                )
                """
            )

    def record_ingestion_run(self, record: dict[str, Any]) -> None:
        frame = pd.DataFrame([record | {"errors": json.dumps(record.get("errors", []))}])
        with self._connect() as con:
            con.register("run_frame", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO ingestion_runs
                SELECT * FROM run_frame
                """
            )

    def upsert_series_metadata(self, metadata: list[dict[str, Any]]) -> None:
        if not metadata:
            return
        frame = pd.DataFrame(metadata)
        with self._connect() as con:
            con.register("metadata_frame", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO series_metadata
                SELECT * FROM metadata_frame
                """
            )

    def upsert_raw_observations(self, observations: pd.DataFrame) -> None:
        if observations.empty:
            return
        with self._connect() as con:
            con.register("raw_frame", observations)
            con.execute(
                """
                DELETE FROM raw_observations
                USING raw_frame
                WHERE raw_observations.series_id = raw_frame.series_id
                  AND raw_observations.date = raw_frame.date
                  AND raw_observations.realtime_start = raw_frame.realtime_start
                  AND raw_observations.realtime_end = raw_frame.realtime_end
                """
            )
            con.execute(
                """
                INSERT INTO raw_observations
                SELECT series_id, date, value, realtime_start, realtime_end,
                       source, fetched_at, frequency, units
                FROM raw_frame
                """
            )

    def upsert_source_health(self, health: pd.DataFrame) -> None:
        if health.empty:
            return
        with self._connect() as con:
            con.register("health_frame", health)
            con.execute(
                """
                INSERT OR REPLACE INTO source_health
                SELECT * FROM health_frame
                """
            )

    def upsert_features(self, features: pd.DataFrame) -> None:
        if features.empty:
            return
        with self._connect() as con:
            con.register("features_frame", features)
            con.execute(
                """
                DELETE FROM features
                USING features_frame
                WHERE features.feature_id = features_frame.feature_id
                  AND (
                    features.date = features_frame.date
                    OR (features.date IS NULL AND features_frame.date IS NULL)
                  )
                """
            )
            con.execute(
                """
                INSERT INTO features
                SELECT feature_id, series_id, date, raw_value, transformed_value,
                       normalized_value, transform, normalization, window_start,
                       window_end, valid, reason
                FROM features_frame
                """
            )

    def upsert_feature_health(self, health: pd.DataFrame) -> None:
        if health.empty:
            return
        frame = health.copy()
        frame["reason_counts"] = frame["reason_counts"].map(json.dumps)
        with self._connect() as con:
            con.register("feature_health_frame", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO feature_health
                SELECT * FROM feature_health_frame
                """
            )

    def replace_dimension_outputs(
        self,
        contributions: pd.DataFrame,
        scores: pd.DataFrame,
        health: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM dimension_feature_contributions")
            con.execute("DELETE FROM dimension_scores")
            con.execute("DELETE FROM dimension_health")
            if not contributions.empty:
                con.register("dimension_contribution_frame", contributions)
                con.execute(
                    """
                    INSERT INTO dimension_feature_contributions
                    SELECT dimension_id, feature_id, date, normalized_value, weight,
                           normalized_weight, polarity, signed_value, contribution,
                           valid, reason
                    FROM dimension_contribution_frame
                    """
                )
            if not scores.empty:
                con.register("dimension_score_frame", scores)
                con.execute(
                    """
                    INSERT INTO dimension_scores
                    SELECT dimension_id, date, score, valid_feature_count,
                           configured_feature_count, total_configured_weight,
                           used_weight, coverage_ratio, valid, reason
                    FROM dimension_score_frame
                    """
                )
            if not health.empty:
                health_frame = health.copy()
                health_frame["missing_features"] = health_frame["missing_features"].map(json.dumps)
                health_frame["invalid_features"] = health_frame["invalid_features"].map(json.dumps)
                con.register("dimension_health_frame", health_frame)
                con.execute(
                    """
                    INSERT INTO dimension_health
                    SELECT dimension_id, date, valid, valid_feature_count,
                           required_feature_count, missing_features, invalid_features,
                           reason
                    FROM dimension_health_frame
                    """
                )

    def read_table(self, table_name: str) -> pd.DataFrame:
        with self._connect() as con:
            return con.execute(f"SELECT * FROM {table_name}").fetchdf()

    def read_raw_observations(self, series_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if series_id:
                return con.execute(
                    "SELECT * FROM raw_observations WHERE series_id = ? ORDER BY date",
                    [series_id],
                ).fetchdf()
            return con.execute("SELECT * FROM raw_observations ORDER BY series_id, date").fetchdf()

    def read_features(self, feature_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if feature_id:
                return con.execute(
                    "SELECT * FROM features WHERE feature_id = ? ORDER BY date",
                    [feature_id],
                ).fetchdf()
            return con.execute("SELECT * FROM features ORDER BY feature_id, date").fetchdf()

    def read_dimension_scores(self, dimension_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if dimension_id:
                return con.execute(
                    "SELECT * FROM dimension_scores WHERE dimension_id = ? ORDER BY date",
                    [dimension_id],
                ).fetchdf()
            return con.execute("SELECT * FROM dimension_scores ORDER BY dimension_id, date").fetchdf()

    def export_parquet(self, output_dir: str | Path = "data/raw/fred") -> None:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            for table in [
                "ingestion_runs",
                "series_metadata",
                "raw_observations",
                "source_health",
                "features",
                "feature_health",
                "dimension_feature_contributions",
                "dimension_scores",
                "dimension_health",
            ]:
                con.execute(
                    f"COPY {table} TO ? (FORMAT PARQUET)",
                    [str(output_path / f"{table}.parquet")],
                )

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.db_path))
