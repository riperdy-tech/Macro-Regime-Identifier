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
                CREATE TABLE IF NOT EXISTS evaluation_calendar (
                    evaluation_date DATE PRIMARY KEY,
                    frequency TEXT,
                    valid BOOLEAN,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS asof_feature_values (
                    evaluation_date DATE,
                    feature_id TEXT,
                    source_observation_date DATE,
                    transformed_value DOUBLE,
                    normalized_value DOUBLE,
                    lag_days INTEGER,
                    valid BOOLEAN,
                    reason TEXT,
                    PRIMARY KEY(evaluation_date, feature_id)
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
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS regime_dimension_contributions (
                    regime_id TEXT,
                    dimension_id TEXT,
                    date DATE,
                    dimension_score DOUBLE,
                    weight DOUBLE,
                    normalized_weight DOUBLE,
                    polarity TEXT,
                    transformed_dimension_value DOUBLE,
                    contribution DOUBLE,
                    valid BOOLEAN,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS regime_scores (
                    regime_id TEXT,
                    date DATE,
                    raw_score DOUBLE,
                    probability DOUBLE,
                    rank INTEGER,
                    valid_dimension_count INTEGER,
                    configured_dimension_count INTEGER,
                    coverage_ratio DOUBLE,
                    valid BOOLEAN,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS regime_health (
                    date DATE PRIMARY KEY,
                    valid BOOLEAN,
                    dominant_regime TEXT,
                    dominant_probability DOUBLE,
                    confidence DOUBLE,
                    entropy DOUBLE,
                    valid_regime_count INTEGER,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_regime_timeline (
                    date DATE PRIMARY KEY,
                    dominant_regime TEXT,
                    dominant_probability DOUBLE,
                    reported_regime TEXT,
                    reported_regime_probability DOUBLE,
                    reported_confidence DOUBLE,
                    raw_dominant_regime TEXT,
                    raw_dominant_probability DOUBLE,
                    raw_confidence DOUBLE,
                    second_regime TEXT,
                    second_probability DOUBLE,
                    confidence DOUBLE,
                    entropy DOUBLE,
                    valid_regime_count INTEGER,
                    valid BOOLEAN,
                    transition_filter_applied BOOLEAN,
                    transition_filter_reason TEXT,
                    reason TEXT
                )
                """
            )
            con.execute(
                "ALTER TABLE historical_regime_timeline ADD COLUMN IF NOT EXISTS reported_regime TEXT"
            )
            con.execute(
                "ALTER TABLE historical_regime_timeline ADD COLUMN IF NOT EXISTS reported_regime_probability DOUBLE"
            )
            con.execute(
                "ALTER TABLE historical_regime_timeline ADD COLUMN IF NOT EXISTS reported_confidence DOUBLE"
            )
            con.execute(
                "ALTER TABLE historical_regime_timeline ADD COLUMN IF NOT EXISTS raw_dominant_regime TEXT"
            )
            con.execute(
                "ALTER TABLE historical_regime_timeline ADD COLUMN IF NOT EXISTS raw_dominant_probability DOUBLE"
            )
            con.execute(
                "ALTER TABLE historical_regime_timeline ADD COLUMN IF NOT EXISTS raw_confidence DOUBLE"
            )
            con.execute(
                "ALTER TABLE historical_regime_timeline ADD COLUMN IF NOT EXISTS transition_filter_applied BOOLEAN"
            )
            con.execute(
                "ALTER TABLE historical_regime_timeline ADD COLUMN IF NOT EXISTS transition_filter_reason TEXT"
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS regime_transitions (
                    transition_date DATE,
                    from_regime TEXT,
                    to_regime TEXT,
                    from_probability DOUBLE,
                    to_probability DOUBLE,
                    confidence DOUBLE,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_summary (
                    start_date TEXT,
                    end_date TEXT,
                    mode TEXT,
                    valid_date_count INTEGER,
                    invalid_date_count INTEGER,
                    regime_switch_count INTEGER,
                    average_regime_duration DOUBLE,
                    average_confidence DOUBLE,
                    dominant_regime_distribution JSON,
                    low_confidence_period_count INTEGER,
                    label TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    config_path TEXT,
                    mode TEXT,
                    status TEXT,
                    failed_step TEXT,
                    warning_count INTEGER,
                    output_dir TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_scores (
                    sector_id TEXT,
                    date DATE,
                    raw_sector_score DOUBLE,
                    confidence_adjusted_score DOUBLE,
                    rank INTEGER,
                    macro_reported_regime TEXT,
                    macro_raw_dominant_regime TEXT,
                    macro_confidence DOUBLE,
                    valid BOOLEAN,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_score_components (
                    sector_id TEXT,
                    date DATE,
                    component_type TEXT,
                    component_id TEXT,
                    input_value DOUBLE,
                    weight_or_exposure DOUBLE,
                    contribution DOUBLE,
                    valid BOOLEAN,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_health (
                    sector_id TEXT,
                    date DATE,
                    valid BOOLEAN,
                    component_count INTEGER,
                    missing_components JSON,
                    warning_count INTEGER,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_proxy_prices (
                    ticker TEXT,
                    date DATE,
                    close DOUBLE,
                    source TEXT,
                    fetched_at TIMESTAMP,
                    PRIMARY KEY(ticker, date)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_validation_returns (
                    sector_id TEXT,
                    proxy_ticker TEXT,
                    score_date DATE,
                    sector_score DOUBLE,
                    confidence_adjusted_score DOUBLE,
                    forward_1m_return DOUBLE,
                    forward_3m_return DOUBLE,
                    relative_forward_1m_return DOUBLE,
                    relative_forward_3m_return DOUBLE,
                    valid BOOLEAN,
                    reason TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_validation_summary (
                    horizon TEXT,
                    observation_count INTEGER,
                    rank_ic_spearman DOUBLE,
                    top_quintile_avg_relative_return DOUBLE,
                    bottom_quintile_avg_relative_return DOUBLE,
                    top_minus_bottom_spread DOUBLE,
                    hit_rate_top_positive DOUBLE,
                    notes TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_items (
                    news_id TEXT PRIMARY KEY,
                    source TEXT,
                    source_url TEXT,
                    title TEXT,
                    body TEXT,
                    published_at TIMESTAMP,
                    ingested_at TIMESTAMP,
                    provider TEXT,
                    raw_metadata_json TEXT,
                    content_hash TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_classifications (
                    classification_id TEXT PRIMARY KEY,
                    news_id TEXT,
                    classified_at TIMESTAMP,
                    ai_provider TEXT,
                    ai_model TEXT,
                    macro_themes_json TEXT,
                    sector_impacts_json TEXT,
                    entities_json TEXT,
                    secular_theme TEXT,
                    time_horizon TEXT,
                    severity DOUBLE,
                    confidence DOUBLE,
                    summary TEXT,
                    raw_ai_response_json TEXT,
                    classification_status TEXT,
                    error_message TEXT
                )
                """
            )
            con.execute(
                "ALTER TABLE news_classifications ADD COLUMN IF NOT EXISTS secular_theme TEXT"
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_theme_scores (
                    news_id TEXT,
                    theme_id TEXT,
                    direction TEXT,
                    severity DOUBLE,
                    confidence DOUBLE,
                    time_horizon TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_sector_impacts (
                    news_id TEXT,
                    sector_id TEXT,
                    impact_direction TEXT,
                    impact_score DOUBLE,
                    confidence DOUBLE,
                    rationale TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_daily_theme_scores (
                    score_date DATE,
                    theme_id TEXT,
                    raw_score DOUBLE,
                    adjusted_score DOUBLE,
                    item_count INTEGER,
                    avg_confidence DOUBLE,
                    avg_severity DOUBLE,
                    top_news_ids_json TEXT,
                    created_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_daily_sector_scores (
                    score_date DATE,
                    sector_id TEXT,
                    raw_news_score DOUBLE,
                    adjusted_news_score DOUBLE,
                    positive_item_count INTEGER,
                    negative_item_count INTEGER,
                    neutral_item_count INTEGER,
                    avg_confidence DOUBLE,
                    avg_severity DOUBLE,
                    top_news_ids_json TEXT,
                    created_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_weekly_theme_scores (
                    week_start_date DATE,
                    theme_id TEXT,
                    raw_score DOUBLE,
                    adjusted_score DOUBLE,
                    item_count INTEGER,
                    avg_confidence DOUBLE,
                    avg_severity DOUBLE,
                    top_news_ids_json TEXT,
                    created_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_weekly_sector_scores (
                    week_start_date DATE,
                    sector_id TEXT,
                    raw_news_score DOUBLE,
                    adjusted_news_score DOUBLE,
                    positive_item_count INTEGER,
                    negative_item_count INTEGER,
                    neutral_item_count INTEGER,
                    avg_confidence DOUBLE,
                    avg_severity DOUBLE,
                    top_news_ids_json TEXT,
                    created_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_score_components (
                    score_date DATE,
                    frequency TEXT,
                    news_id TEXT,
                    theme_id TEXT,
                    sector_id TEXT,
                    component_type TEXT,
                    direction TEXT,
                    raw_component DOUBLE,
                    adjusted_component DOUBLE,
                    severity DOUBLE,
                    confidence DOUBLE,
                    source_weight DOUBLE,
                    freshness_weight DOUBLE,
                    rationale TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_scoring_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    status TEXT,
                    frequency TEXT,
                    item_count INTEGER,
                    scored_item_count INTEGER,
                    error_message TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS combined_sector_diagnostics (
                    diagnostic_date DATE,
                    sector_id TEXT,
                    sector_macro_score DOUBLE,
                    sector_news_score DOUBLE,
                    combined_score DOUBLE,
                    macro_component_weight DOUBLE,
                    news_component_weight DOUBLE,
                    news_item_count INTEGER,
                    news_confidence DOUBLE,
                    diagnostic_confidence DOUBLE,
                    rank INTEGER,
                    created_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS combined_sector_diagnostic_components (
                    diagnostic_date DATE,
                    sector_id TEXT,
                    component_name TEXT,
                    component_value DOUBLE,
                    component_weight DOUBLE,
                    rationale TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_input_quality_runs (
                    run_id TEXT PRIMARY KEY,
                    run_at TIMESTAMP,
                    profile TEXT,
                    raw_item_count INTEGER,
                    unique_item_count INTEGER,
                    duplicate_count INTEGER,
                    source_count INTEGER,
                    date_min TIMESTAMP,
                    date_max TIMESTAMP,
                    short_body_count INTEGER,
                    old_item_count INTEGER,
                    future_item_count INTEGER,
                    warning_count INTEGER,
                    quality_status TEXT,
                    details_json TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_classification_quality_runs (
                    run_id TEXT PRIMARY KEY,
                    run_at TIMESTAMP,
                    total_items INTEGER,
                    success_count INTEGER,
                    failure_count INTEGER,
                    success_rate DOUBLE,
                    retry_count INTEGER,
                    retry_rate DOUBLE,
                    repaired_count INTEGER,
                    repair_rate DOUBLE,
                    provider TEXT,
                    model TEXT,
                    top_failure_modes_json TEXT,
                    quality_status TEXT,
                    details_json TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_overlay_monitoring (
                    run_id TEXT PRIMARY KEY,
                    run_at TIMESTAMP,
                    diagnostic_date DATE,
                    top_news_themes_json TEXT,
                    top_sector_tailwinds_json TEXT,
                    top_sector_headwinds_json TEXT,
                    combined_top_sectors_json TEXT,
                    macro_only_top_sectors_json TEXT,
                    sectors_changed_by_news_json TEXT,
                    max_rank_change INTEGER,
                    avg_abs_rank_change DOUBLE,
                    news_item_count INTEGER,
                    thin_news_warning BOOLEAN,
                    overlay_status TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_diagnostic_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    status TEXT,
                    run_date DATE,
                    macro_status TEXT,
                    sector_status TEXT,
                    news_ingestion_status TEXT,
                    news_classification_status TEXT,
                    news_scoring_status TEXT,
                    combined_status TEXT,
                    monitoring_status TEXT,
                    guardrail_status TEXT,
                    archive_path TEXT,
                    warnings_json TEXT,
                    errors_json TEXT,
                    created_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_accumulation_runs (
                    run_id TEXT PRIMARY KEY,
                    run_date DATE,
                    raw_item_count INTEGER,
                    new_unique_items INTEGER,
                    duplicate_items INTEGER,
                    classified_items INTEGER,
                    failed_items INTEGER,
                    success_rate DOUBLE,
                    source_count INTEGER,
                    source_group_count INTEGER,
                    date_min TIMESTAMP,
                    date_max TIMESTAMP,
                    quality_status TEXT,
                    warning_json TEXT,
                    created_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_score_history_summary (
                    score_date DATE PRIMARY KEY,
                    theme_count INTEGER,
                    sector_count INTEGER,
                    top_themes_json TEXT,
                    top_sector_tailwinds_json TEXT,
                    top_sector_headwinds_json TEXT,
                    classification_count INTEGER,
                    avg_confidence DOUBLE,
                    avg_severity DOUBLE,
                    created_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS combined_diagnostic_history_summary (
                    diagnostic_date DATE PRIMARY KEY,
                    top_combined_sectors_json TEXT,
                    top_macro_only_sectors_json TEXT,
                    top_news_only_sectors_json TEXT,
                    max_rank_change INTEGER,
                    avg_abs_rank_change DOUBLE,
                    news_item_count INTEGER,
                    thin_news_warning BOOLEAN,
                    created_at TIMESTAMP
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

    def record_pipeline_run(self, record: dict[str, Any]) -> None:
        frame = pd.DataFrame([record])
        with self._connect() as con:
            con.register("pipeline_run_frame", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO pipeline_runs
                SELECT * FROM pipeline_run_frame
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

    def replace_evaluation_outputs(
        self,
        calendar: pd.DataFrame,
        asof_values: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM evaluation_calendar")
            con.execute("DELETE FROM asof_feature_values")
            if not calendar.empty:
                con.register("evaluation_calendar_frame", calendar)
                con.execute(
                    """
                    INSERT INTO evaluation_calendar
                    SELECT evaluation_date, frequency, valid, reason
                    FROM evaluation_calendar_frame
                    """
                )
            if not asof_values.empty:
                con.register("asof_feature_values_frame", asof_values)
                con.execute(
                    """
                    INSERT INTO asof_feature_values
                    SELECT evaluation_date, feature_id, source_observation_date,
                           transformed_value, normalized_value, lag_days, valid, reason
                    FROM asof_feature_values_frame
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

    def replace_regime_outputs(
        self,
        contributions: pd.DataFrame,
        scores: pd.DataFrame,
        health: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM regime_dimension_contributions")
            con.execute("DELETE FROM regime_scores")
            con.execute("DELETE FROM regime_health")
            if not contributions.empty:
                con.register("regime_contribution_frame", contributions)
                con.execute(
                    """
                    INSERT INTO regime_dimension_contributions
                    SELECT regime_id, dimension_id, date, dimension_score, weight,
                           normalized_weight, polarity, transformed_dimension_value,
                           contribution, valid, reason
                    FROM regime_contribution_frame
                    """
                )
            if not scores.empty:
                con.register("regime_score_frame", scores)
                con.execute(
                    """
                    INSERT INTO regime_scores
                    SELECT regime_id, date, raw_score, probability, rank,
                           valid_dimension_count, configured_dimension_count,
                           coverage_ratio, valid, reason
                    FROM regime_score_frame
                    """
                )
            if not health.empty:
                con.register("regime_health_frame", health)
                con.execute(
                    """
                    INSERT INTO regime_health
                    SELECT date, valid, dominant_regime, dominant_probability,
                           confidence, entropy, valid_regime_count, reason
                    FROM regime_health_frame
                    """
                )

    def replace_diagnostic_outputs(
        self,
        timeline: pd.DataFrame,
        transitions: pd.DataFrame,
        summary: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM historical_regime_timeline")
            con.execute("DELETE FROM regime_transitions")
            con.execute("DELETE FROM diagnostic_summary")
            if not timeline.empty:
                timeline = _ensure_timeline_columns(timeline)
                con.register("timeline_frame", timeline)
                con.execute(
                    """
                    INSERT INTO historical_regime_timeline (
                        date, dominant_regime, dominant_probability,
                        reported_regime, reported_regime_probability,
                        reported_confidence, raw_dominant_regime,
                        raw_dominant_probability, raw_confidence, second_regime,
                        second_probability, confidence, entropy, valid_regime_count,
                        valid, transition_filter_applied, transition_filter_reason,
                        reason
                    )
                    SELECT date, dominant_regime, dominant_probability,
                           reported_regime, reported_regime_probability,
                           reported_confidence, raw_dominant_regime,
                           raw_dominant_probability, raw_confidence, second_regime,
                           second_probability, confidence, entropy, valid_regime_count,
                           valid, transition_filter_applied, transition_filter_reason,
                           reason
                    FROM timeline_frame
                    """
                )
            if not transitions.empty:
                con.register("transition_frame", transitions)
                con.execute(
                    """
                    INSERT INTO regime_transitions
                    SELECT transition_date, from_regime, to_regime, from_probability,
                           to_probability, confidence, reason
                    FROM transition_frame
                    """
                )
            if not summary.empty:
                con.register("summary_frame", summary)
                con.execute(
                    """
                    INSERT INTO diagnostic_summary
                    SELECT start_date, end_date, mode, valid_date_count,
                           invalid_date_count, regime_switch_count,
                           average_regime_duration, average_confidence,
                           dominant_regime_distribution,
                           low_confidence_period_count, label
                    FROM summary_frame
                    """
                )

    def replace_sector_outputs(
        self,
        scores: pd.DataFrame,
        components: pd.DataFrame,
        health: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM sector_scores")
            con.execute("DELETE FROM sector_score_components")
            con.execute("DELETE FROM sector_health")
            if not scores.empty:
                con.register("sector_score_frame", scores)
                con.execute(
                    """
                    INSERT INTO sector_scores
                    SELECT sector_id, date, raw_sector_score,
                           confidence_adjusted_score, rank, macro_reported_regime,
                           macro_raw_dominant_regime, macro_confidence, valid, reason
                    FROM sector_score_frame
                    """
                )
            if not components.empty:
                con.register("sector_component_frame", components)
                con.execute(
                    """
                    INSERT INTO sector_score_components
                    SELECT sector_id, date, component_type, component_id, input_value,
                           weight_or_exposure, contribution, valid, reason
                    FROM sector_component_frame
                    """
                )
            if not health.empty:
                health_frame = health.copy()
                health_frame["missing_components"] = health_frame["missing_components"].map(
                    json.dumps
                )
                con.register("sector_health_frame", health_frame)
                con.execute(
                    """
                    INSERT INTO sector_health
                    SELECT sector_id, date, valid, component_count, missing_components,
                           warning_count, reason
                    FROM sector_health_frame
                    """
                )

    def upsert_sector_proxy_prices(self, prices: pd.DataFrame) -> None:
        if prices.empty:
            return
        with self._connect() as con:
            con.register("sector_price_frame", prices)
            con.execute(
                """
                DELETE FROM sector_proxy_prices
                USING sector_price_frame
                WHERE sector_proxy_prices.ticker = sector_price_frame.ticker
                  AND sector_proxy_prices.date = sector_price_frame.date
                """
            )
            con.execute(
                """
                INSERT INTO sector_proxy_prices
                SELECT ticker, date, close, source, fetched_at
                FROM sector_price_frame
                """
            )

    def replace_sector_validation_outputs(
        self,
        returns: pd.DataFrame,
        summary: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM sector_validation_returns")
            con.execute("DELETE FROM sector_validation_summary")
            if not returns.empty:
                con.register("sector_validation_return_frame", returns)
                con.execute(
                    """
                    INSERT INTO sector_validation_returns
                    SELECT sector_id, proxy_ticker, score_date, sector_score,
                           confidence_adjusted_score, forward_1m_return,
                           forward_3m_return, relative_forward_1m_return,
                           relative_forward_3m_return, valid, reason
                    FROM sector_validation_return_frame
                    """
                )
            if not summary.empty:
                con.register("sector_validation_summary_frame", summary)
                con.execute(
                    """
                    INSERT INTO sector_validation_summary
                    SELECT horizon, observation_count, rank_ic_spearman,
                           top_quintile_avg_relative_return,
                           bottom_quintile_avg_relative_return,
                           top_minus_bottom_spread, hit_rate_top_positive, notes
                    FROM sector_validation_summary_frame
                    """
                )

    def upsert_news_items(self, items: pd.DataFrame) -> None:
        if items.empty:
            return
        frame = items.copy()
        if "raw_metadata" in frame.columns:
            frame["raw_metadata_json"] = frame["raw_metadata"].map(json.dumps)
        elif "raw_metadata_json" not in frame.columns:
            frame["raw_metadata_json"] = "{}"
        with self._connect() as con:
            con.register("news_item_frame", frame)
            con.execute(
                """
                DELETE FROM news_items
                USING news_item_frame
                WHERE news_items.news_id = news_item_frame.news_id
                   OR news_items.content_hash = news_item_frame.content_hash
                """
            )
            con.execute(
                """
                INSERT INTO news_items
                SELECT news_id, source, source_url, title, body, published_at,
                       ingested_at, provider, raw_metadata_json, content_hash
                FROM news_item_frame
                """
            )

    def replace_news_classifications(
        self,
        classifications: pd.DataFrame,
        theme_scores: pd.DataFrame,
        sector_impacts: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM news_classifications")
            con.execute("DELETE FROM news_theme_scores")
            con.execute("DELETE FROM news_sector_impacts")
            if not classifications.empty:
                frame = classifications.copy()
                if "secular_theme" not in frame.columns:
                    frame["secular_theme"] = None
                frame["macro_themes_json"] = frame["macro_themes"].map(json.dumps)
                frame["sector_impacts_json"] = frame["sector_impacts"].map(json.dumps)
                frame["entities_json"] = frame["entities"].map(json.dumps)
                frame["raw_ai_response_json"] = frame["raw_ai_response"].map(json.dumps)
                con.register("news_classification_frame", frame)
                con.execute(
                    """
                    INSERT INTO news_classifications (
                        classification_id, news_id, classified_at, ai_provider,
                        ai_model, macro_themes_json, sector_impacts_json,
                        entities_json, secular_theme, time_horizon, severity,
                        confidence, summary, raw_ai_response_json,
                        classification_status, error_message
                    )
                    SELECT classification_id, news_id, classified_at, ai_provider,
                           ai_model, macro_themes_json, sector_impacts_json,
                           entities_json, secular_theme, time_horizon, severity,
                           confidence, summary, raw_ai_response_json,
                           classification_status, error_message
                    FROM news_classification_frame
                    """
                )
            if not theme_scores.empty:
                con.register("news_theme_score_frame", theme_scores)
                con.execute(
                    """
                    INSERT INTO news_theme_scores
                    SELECT news_id, theme_id, direction, severity, confidence, time_horizon
                    FROM news_theme_score_frame
                    """
                )
            if not sector_impacts.empty:
                con.register("news_sector_impact_frame", sector_impacts)
                con.execute(
                    """
                    INSERT INTO news_sector_impacts
                    SELECT news_id, sector_id, impact_direction, impact_score,
                           confidence, rationale
                    FROM news_sector_impact_frame
                    """
                )

    def upsert_news_classification_outputs(
        self,
        classifications: pd.DataFrame,
        theme_scores: pd.DataFrame,
        sector_impacts: pd.DataFrame,
    ) -> None:
        if classifications.empty:
            return
        frame = classifications.copy()
        if "secular_theme" not in frame.columns:
            frame["secular_theme"] = None
        news_ids = frame["news_id"].dropna().astype(str).unique().tolist()
        if not news_ids:
            return
        frame["macro_themes_json"] = frame["macro_themes"].map(json.dumps)
        frame["sector_impacts_json"] = frame["sector_impacts"].map(json.dumps)
        frame["entities_json"] = frame["entities"].map(json.dumps)
        frame["raw_ai_response_json"] = frame["raw_ai_response"].map(json.dumps)
        with self._connect() as con:
            news_id_frame = pd.DataFrame({"news_id": news_ids})
            con.register("news_id_frame", news_id_frame)
            con.execute(
                """
                DELETE FROM news_classifications
                USING news_id_frame
                WHERE news_classifications.news_id = news_id_frame.news_id
                """
            )
            con.execute(
                """
                DELETE FROM news_theme_scores
                USING news_id_frame
                WHERE news_theme_scores.news_id = news_id_frame.news_id
                """
            )
            con.execute(
                """
                DELETE FROM news_sector_impacts
                USING news_id_frame
                WHERE news_sector_impacts.news_id = news_id_frame.news_id
                """
            )
            con.register("news_classification_frame", frame)
            con.execute(
                """
                INSERT INTO news_classifications (
                    classification_id, news_id, classified_at, ai_provider,
                    ai_model, macro_themes_json, sector_impacts_json,
                    entities_json, secular_theme, time_horizon, severity,
                    confidence, summary, raw_ai_response_json,
                    classification_status, error_message
                )
                SELECT classification_id, news_id, classified_at, ai_provider,
                       ai_model, macro_themes_json, sector_impacts_json,
                       entities_json, secular_theme, time_horizon, severity,
                       confidence, summary, raw_ai_response_json,
                       classification_status, error_message
                FROM news_classification_frame
                """
            )
            if not theme_scores.empty:
                con.register("news_theme_score_frame", theme_scores)
                con.execute(
                    """
                    INSERT INTO news_theme_scores
                    SELECT news_id, theme_id, direction, severity, confidence, time_horizon
                    FROM news_theme_score_frame
                    """
                )
            if not sector_impacts.empty:
                con.register("news_sector_impact_frame", sector_impacts)
                con.execute(
                    """
                    INSERT INTO news_sector_impacts
                    SELECT news_id, sector_id, impact_direction, impact_score,
                           confidence, rationale
                    FROM news_sector_impact_frame
                    """
                )

    def replace_news_score_outputs(
        self,
        daily_theme_scores: pd.DataFrame,
        daily_sector_scores: pd.DataFrame,
        weekly_theme_scores: pd.DataFrame,
        weekly_sector_scores: pd.DataFrame,
        components: pd.DataFrame,
        runs: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM news_daily_theme_scores")
            con.execute("DELETE FROM news_daily_sector_scores")
            con.execute("DELETE FROM news_weekly_theme_scores")
            con.execute("DELETE FROM news_weekly_sector_scores")
            con.execute("DELETE FROM news_score_components")
            con.execute("DELETE FROM news_scoring_runs")
            if not daily_theme_scores.empty:
                frame = daily_theme_scores.copy()
                frame["top_news_ids_json"] = frame["top_news_ids"].map(json.dumps)
                con.register("news_daily_theme_frame", frame)
                con.execute(
                    """
                    INSERT INTO news_daily_theme_scores
                    SELECT score_date, theme_id, raw_score, adjusted_score,
                           item_count, avg_confidence, avg_severity,
                           top_news_ids_json, created_at
                    FROM news_daily_theme_frame
                    """
                )
            if not daily_sector_scores.empty:
                frame = daily_sector_scores.copy()
                frame["top_news_ids_json"] = frame["top_news_ids"].map(json.dumps)
                con.register("news_daily_sector_frame", frame)
                con.execute(
                    """
                    INSERT INTO news_daily_sector_scores
                    SELECT score_date, sector_id, raw_news_score,
                           adjusted_news_score, positive_item_count,
                           negative_item_count, neutral_item_count,
                           avg_confidence, avg_severity, top_news_ids_json,
                           created_at
                    FROM news_daily_sector_frame
                    """
                )
            if not weekly_theme_scores.empty:
                frame = weekly_theme_scores.copy()
                frame["top_news_ids_json"] = frame["top_news_ids"].map(json.dumps)
                con.register("news_weekly_theme_frame", frame)
                con.execute(
                    """
                    INSERT INTO news_weekly_theme_scores
                    SELECT week_start_date, theme_id, raw_score, adjusted_score,
                           item_count, avg_confidence, avg_severity,
                           top_news_ids_json, created_at
                    FROM news_weekly_theme_frame
                    """
                )
            if not weekly_sector_scores.empty:
                frame = weekly_sector_scores.copy()
                frame["top_news_ids_json"] = frame["top_news_ids"].map(json.dumps)
                con.register("news_weekly_sector_frame", frame)
                con.execute(
                    """
                    INSERT INTO news_weekly_sector_scores
                    SELECT week_start_date, sector_id, raw_news_score,
                           adjusted_news_score, positive_item_count,
                           negative_item_count, neutral_item_count,
                           avg_confidence, avg_severity, top_news_ids_json,
                           created_at
                    FROM news_weekly_sector_frame
                    """
                )
            if not components.empty:
                con.register("news_score_component_frame", components)
                con.execute(
                    """
                    INSERT INTO news_score_components
                    SELECT score_date, frequency, news_id, theme_id, sector_id,
                           component_type, direction, raw_component,
                           adjusted_component, severity, confidence, source_weight,
                           freshness_weight, rationale
                    FROM news_score_component_frame
                    """
                )
            if not runs.empty:
                con.register("news_scoring_run_frame", runs)
                con.execute(
                    """
                    INSERT OR REPLACE INTO news_scoring_runs
                    SELECT run_id, started_at, completed_at, status, frequency,
                           item_count, scored_item_count, error_message
                    FROM news_scoring_run_frame
                    """
                )

    def replace_combined_sector_outputs(
        self,
        diagnostics: pd.DataFrame,
        components: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM combined_sector_diagnostics")
            con.execute("DELETE FROM combined_sector_diagnostic_components")
            if not diagnostics.empty:
                con.register("combined_sector_frame", diagnostics)
                con.execute(
                    """
                    INSERT INTO combined_sector_diagnostics
                    SELECT diagnostic_date, sector_id, sector_macro_score,
                           sector_news_score, combined_score,
                           macro_component_weight, news_component_weight,
                           news_item_count, news_confidence,
                           diagnostic_confidence, rank, created_at
                    FROM combined_sector_frame
                    """
                )
            if not components.empty:
                con.register("combined_sector_component_frame", components)
                con.execute(
                    """
                    INSERT INTO combined_sector_diagnostic_components
                    SELECT diagnostic_date, sector_id, component_name,
                           component_value, component_weight, rationale
                    FROM combined_sector_component_frame
                    """
                )

    def upsert_news_monitoring_outputs(
        self,
        input_quality_runs: pd.DataFrame,
        classification_quality_runs: pd.DataFrame,
        overlay_monitoring: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            if not input_quality_runs.empty:
                con.register("news_input_quality_frame", input_quality_runs)
                con.execute(
                    """
                    INSERT OR REPLACE INTO news_input_quality_runs
                    SELECT run_id, run_at, profile, raw_item_count, unique_item_count,
                           duplicate_count, source_count, date_min, date_max,
                           short_body_count, old_item_count, future_item_count,
                           warning_count, quality_status, details_json
                    FROM news_input_quality_frame
                    """
                )
            if not classification_quality_runs.empty:
                con.register("news_classification_quality_frame", classification_quality_runs)
                con.execute(
                    """
                    INSERT OR REPLACE INTO news_classification_quality_runs
                    SELECT run_id, run_at, total_items, success_count, failure_count,
                           success_rate, retry_count, retry_rate, repaired_count,
                           repair_rate, provider, model, top_failure_modes_json,
                           quality_status, details_json
                    FROM news_classification_quality_frame
                    """
                )
            if not overlay_monitoring.empty:
                con.register("news_overlay_monitoring_frame", overlay_monitoring)
                con.execute(
                    """
                    INSERT OR REPLACE INTO news_overlay_monitoring
                    SELECT run_id, run_at, diagnostic_date, top_news_themes_json,
                           top_sector_tailwinds_json, top_sector_headwinds_json,
                           combined_top_sectors_json, macro_only_top_sectors_json,
                           sectors_changed_by_news_json, max_rank_change,
                           avg_abs_rank_change, news_item_count, thin_news_warning,
                           overlay_status
                    FROM news_overlay_monitoring_frame
                    """
                )

    def upsert_daily_diagnostic_run(self, record: dict[str, Any]) -> None:
        frame = pd.DataFrame([record])
        with self._connect() as con:
            con.register("daily_diagnostic_run_frame", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO daily_diagnostic_runs
                SELECT run_id, started_at, completed_at, status, run_date,
                       macro_status, sector_status, news_ingestion_status,
                       news_classification_status, news_scoring_status,
                       combined_status, monitoring_status, guardrail_status,
                       archive_path, warnings_json, errors_json, created_at
                FROM daily_diagnostic_run_frame
                """
            )

    def upsert_news_accumulation_outputs(
        self,
        runs: pd.DataFrame,
        news_history: pd.DataFrame,
        combined_history: pd.DataFrame,
    ) -> None:
        with self._connect() as con:
            if not runs.empty:
                con.register("news_accumulation_run_frame", runs)
                con.execute(
                    """
                    INSERT OR REPLACE INTO news_accumulation_runs
                    SELECT run_id, run_date, raw_item_count, new_unique_items,
                           duplicate_items, classified_items, failed_items,
                           success_rate, source_count, source_group_count,
                           date_min, date_max, quality_status, warning_json,
                           created_at
                    FROM news_accumulation_run_frame
                    """
                )
            if not news_history.empty:
                con.register("news_score_history_frame", news_history)
                con.execute(
                    """
                    INSERT OR REPLACE INTO news_score_history_summary
                    SELECT score_date, theme_count, sector_count, top_themes_json,
                           top_sector_tailwinds_json, top_sector_headwinds_json,
                           classification_count, avg_confidence, avg_severity,
                           created_at
                    FROM news_score_history_frame
                    """
                )
            if not combined_history.empty:
                con.register("combined_diagnostic_history_frame", combined_history)
                con.execute(
                    """
                    INSERT OR REPLACE INTO combined_diagnostic_history_summary
                    SELECT diagnostic_date, top_combined_sectors_json,
                           top_macro_only_sectors_json, top_news_only_sectors_json,
                           max_rank_change, avg_abs_rank_change, news_item_count,
                           thin_news_warning, created_at
                    FROM combined_diagnostic_history_frame
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

    def read_asof_feature_values(self, feature_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if feature_id:
                return con.execute(
                    """
                    SELECT * FROM asof_feature_values
                    WHERE feature_id = ?
                    ORDER BY evaluation_date
                    """,
                    [feature_id],
                ).fetchdf()
            return con.execute(
                "SELECT * FROM asof_feature_values ORDER BY feature_id, evaluation_date"
            ).fetchdf()

    def read_dimension_scores(self, dimension_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if dimension_id:
                return con.execute(
                    "SELECT * FROM dimension_scores WHERE dimension_id = ? ORDER BY date",
                    [dimension_id],
                ).fetchdf()
            return con.execute("SELECT * FROM dimension_scores ORDER BY dimension_id, date").fetchdf()

    def read_regime_scores(self, regime_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if regime_id:
                return con.execute(
                    "SELECT * FROM regime_scores WHERE regime_id = ? ORDER BY date",
                    [regime_id],
                ).fetchdf()
            return con.execute("SELECT * FROM regime_scores ORDER BY date, rank").fetchdf()

    def read_sector_scores(self, sector_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if sector_id:
                return con.execute(
                    "SELECT * FROM sector_scores WHERE sector_id = ? ORDER BY date",
                    [sector_id],
                ).fetchdf()
            return con.execute("SELECT * FROM sector_scores ORDER BY date, rank").fetchdf()

    def read_sector_components(self, sector_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if sector_id:
                return con.execute(
                    """
                    SELECT * FROM sector_score_components
                    WHERE sector_id = ?
                    ORDER BY date, component_type, component_id
                    """,
                    [sector_id],
                ).fetchdf()
            return con.execute(
                """
                SELECT * FROM sector_score_components
                ORDER BY sector_id, date, component_type, component_id
                """
            ).fetchdf()

    def read_sector_proxy_prices(self, ticker: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if ticker:
                return con.execute(
                    "SELECT * FROM sector_proxy_prices WHERE ticker = ? ORDER BY date",
                    [ticker],
                ).fetchdf()
            return con.execute("SELECT * FROM sector_proxy_prices ORDER BY ticker, date").fetchdf()

    def read_news_items(self, news_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if news_id:
                return con.execute(
                    "SELECT * FROM news_items WHERE news_id = ? ORDER BY published_at",
                    [news_id],
                ).fetchdf()
            return con.execute("SELECT * FROM news_items ORDER BY published_at").fetchdf()

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
                "evaluation_calendar",
                "asof_feature_values",
                "dimension_feature_contributions",
                "dimension_scores",
                "dimension_health",
                "regime_dimension_contributions",
                "regime_scores",
                "regime_health",
                "historical_regime_timeline",
                "regime_transitions",
                "diagnostic_summary",
                "pipeline_runs",
                "sector_scores",
                "sector_score_components",
                "sector_health",
                "sector_proxy_prices",
                "sector_validation_returns",
                "sector_validation_summary",
                "news_items",
                "news_classifications",
                "news_theme_scores",
                "news_sector_impacts",
                "news_daily_theme_scores",
                "news_daily_sector_scores",
                "news_weekly_theme_scores",
                "news_weekly_sector_scores",
                "news_score_components",
                "news_scoring_runs",
                "combined_sector_diagnostics",
                "combined_sector_diagnostic_components",
            ]:
                con.execute(
                    f"COPY {table} TO ? (FORMAT PARQUET)",
                    [str(output_path / f"{table}.parquet")],
                )

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.db_path))


def _ensure_timeline_columns(timeline: pd.DataFrame) -> pd.DataFrame:
    frame = timeline.copy()
    defaults = {
        "reported_regime": frame.get("dominant_regime"),
        "reported_regime_probability": frame.get("dominant_probability"),
        "reported_confidence": frame.get("confidence"),
        "raw_dominant_regime": frame.get("dominant_regime"),
        "raw_dominant_probability": frame.get("dominant_probability"),
        "raw_confidence": frame.get("confidence"),
        "transition_filter_applied": False,
        "transition_filter_reason": "no_filter",
    }
    for column, value in defaults.items():
        if column not in frame.columns:
            frame[column] = value
    return frame
