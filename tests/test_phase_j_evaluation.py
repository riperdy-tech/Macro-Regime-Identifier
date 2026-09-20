from __future__ import annotations

import re

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.evaluation.calendar import (
    asof_values_to_feature_frame,
    build_asof_feature_values,
    build_evaluation_calendar,
)
from macro_engine.evaluation.config import EvaluationCalendarConfig, load_evaluation_config
from macro_engine.features.config import FeatureDefinition
from macro_engine.ingest.schemas import IngestionSource
from macro_engine.dimensions.service import build_stored_dimensions
from macro_engine.storage.duckdb_store import DuckDBStore


def _feature(feature_id: str, series_id: str) -> FeatureDefinition:
    return FeatureDefinition.model_validate(
        {
            "feature_id": feature_id,
            "series_id": series_id,
            "transform": "level",
            "normalization": "none",
            "direction": "higher_is_test_positive",
            "enabled": True,
            "min_observations": 1,
        }
    )


def _source(series_id: str, frequency: str) -> IngestionSource:
    return IngestionSource.model_validate(
        {
            "series_id": series_id,
            "name": series_id,
            "provider": "FRED",
            "dimension": "test",
            "frequency": frequency,
            "required": True,
            "enabled": True,
            "stale_after_days": 45,
            "unusable_after_days": 120,
        }
    )


def _mixed_frequency_features() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "feature_id": "daily_feature",
                "series_id": "DAILY",
                "date": "2026-04-28",
                "raw_value": 1.0,
                "transformed_value": 1.0,
                "normalized_value": 1.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-04-28",
                "window_end": "2026-04-28",
                "valid": True,
                "reason": "ok",
            },
            {
                "feature_id": "daily_feature",
                "series_id": "DAILY",
                "date": "2026-05-03",
                "raw_value": 99.0,
                "transformed_value": 99.0,
                "normalized_value": 99.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-05-03",
                "window_end": "2026-05-03",
                "valid": True,
                "reason": "ok",
            },
            {
                "feature_id": "weekly_feature",
                "series_id": "WEEKLY",
                "date": "2026-04-24",
                "raw_value": 2.0,
                "transformed_value": 2.0,
                "normalized_value": 2.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-04-24",
                "window_end": "2026-04-24",
                "valid": True,
                "reason": "ok",
            },
            {
                "feature_id": "monthly_feature",
                "series_id": "MONTHLY",
                "date": "2026-04-01",
                "raw_value": 3.0,
                "transformed_value": 3.0,
                "normalized_value": 3.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-04-01",
                "window_end": "2026-04-01",
                "valid": True,
                "reason": "ok",
            },
        ]
    )


def _calendar_config() -> EvaluationCalendarConfig:
    return EvaluationCalendarConfig.model_validate(
        {
            "frequency": "monthly",
            "date_rule": "month_start",
            "start_date": "2026-04-01",
            "end_date": "2026-05-01",
            "as_of_policy": "latest_observation_on_or_before_date",
            "max_lag_by_frequency": {
                "daily": 10,
                "weekly": 21,
                "monthly": 75,
                "quarterly": 140,
                "annual": 450,
            },
        }
    )


def test_evaluation_calendar_config_validates():
    config = load_evaluation_config("config/phase_b_sources.yaml")

    # The shipped basis is a point-in-time HYBRID: vintages from the boundary onward, the calendar
    # rule before it, because ALFRED's archive does not reach back past 2014-02 for every leg
    # (docs/ANCHOR_METHODOLOGY.md §6.2b). Pinned here so weakening it back to a bare
    # `calendar_asof` is a deliberate edit rather than a silent default.
    assert config.scoring_mode == "point_in_time"
    assert config.point_in_time_start == "2014-02-01"
    assert config.evaluation_calendar.frequency == "monthly"
    assert config.evaluation_calendar.max_lag_by_frequency["annual"] == 450


def test_the_hybrid_boundary_switches_the_applied_rule_per_date():
    """Before the boundary the calendar rule applies; from it, point-in-time."""
    config = load_evaluation_config("config/phase_b_sources.yaml")

    assert config.effective_scoring_mode("2013-12-31") == "calendar_asof"
    assert config.effective_scoring_mode("2014-02-01") == "point_in_time"
    assert config.effective_scoring_mode("2026-09-20") == "point_in_time"
    # The provenance string says WHY the earlier date was not point-in-time.
    assert "point_in_time_start=2014-02-01" in config.scoring_mode_applied("1995-06-30")
    assert config.scoring_mode_applied("2024-06-30") == "point_in_time"


def test_a_null_boundary_means_point_in_time_everywhere():
    """`point_in_time_start: null` is the blanket switch, kept available and explicit."""
    config = load_evaluation_config("config/phase_b_sources.yaml").model_copy(
        update={"point_in_time_start": None}
    )

    assert config.effective_scoring_mode("1995-06-30") == "point_in_time"


def test_monthly_calendar_is_deterministic():
    calendar = build_evaluation_calendar(_calendar_config(), _mixed_frequency_features())

    assert calendar["evaluation_date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-04-01",
        "2026-05-01",
    ]
    assert calendar["valid"].tolist() == [True, True]


def test_asof_alignment_uses_present_or_past_only():
    features = _mixed_frequency_features()
    calendar = build_evaluation_calendar(_calendar_config(), features)
    asof = build_asof_feature_values(
        features=features,
        feature_definitions=[
            _feature("daily_feature", "DAILY"),
            _feature("weekly_feature", "WEEKLY"),
            _feature("monthly_feature", "MONTHLY"),
        ],
        sources=[
            _source("DAILY", "daily"),
            _source("WEEKLY", "weekly"),
            _source("MONTHLY", "monthly"),
        ],
        calendar=calendar,
        config=_calendar_config(),
    )
    may = asof[asof["evaluation_date"] == pd.Timestamp("2026-05-01")]

    assert set(may["feature_id"]) == {"daily_feature", "weekly_feature", "monthly_feature"}
    daily = may[may["feature_id"] == "daily_feature"].iloc[0]
    assert daily["source_observation_date"] == pd.Timestamp("2026-04-28")
    assert daily["normalized_value"] == 1.0
    assert daily["lag_days"] == 3
    assert bool(daily["valid"]) is True


def test_max_lag_invalidates_stale_asof_values():
    config = _calendar_config().model_copy(
        update={
            "max_lag_by_frequency": {
                "daily": 1,
                "weekly": 21,
                "monthly": 75,
                "quarterly": 140,
                "annual": 450,
            }
        }
    )
    calendar = build_evaluation_calendar(config, _mixed_frequency_features())
    asof = build_asof_feature_values(
        features=_mixed_frequency_features(),
        feature_definitions=[_feature("daily_feature", "DAILY")],
        sources=[_source("DAILY", "daily")],
        calendar=calendar,
        config=config,
    )
    may_daily = asof[
        (asof["evaluation_date"] == pd.Timestamp("2026-05-01"))
        & (asof["feature_id"] == "daily_feature")
    ].iloc[0]

    assert bool(may_daily["valid"]) is False
    assert may_daily["reason"] == "stale_asof_value"


def test_asof_values_can_feed_dimension_feature_frame_without_future_leakage():
    calendar = pd.DataFrame(
        {
            "evaluation_date": [pd.Timestamp("2026-05-01")],
            "frequency": ["monthly"],
            "valid": [True],
            "reason": ["ok"],
        }
    )
    asof = build_asof_feature_values(
        features=_mixed_frequency_features(),
        feature_definitions=[_feature("daily_feature", "DAILY")],
        sources=[_source("DAILY", "daily")],
        calendar=calendar,
        config=_calendar_config(),
    )
    feature_frame = asof_values_to_feature_frame(asof)

    assert feature_frame.iloc[0]["date"] == pd.Timestamp("2026-05-01")
    assert feature_frame.iloc[0]["normalized_value"] == 1.0


def test_evaluation_cli_commands_work(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    config_path = tmp_path / "config.yaml"
    source_config = open("config/phase_b_sources.yaml", encoding="utf-8").read()
    source_config = source_config.replace('start_date: "1990-01-01"', 'start_date: "2026-01-01"')
    source_config = source_config.replace("end_date: null", 'end_date: "2026-01-01"', 1)
    config_path.write_text(source_config, encoding="utf-8")

    store = DuckDBStore(db_path)
    store.initialize()
    features = pd.DataFrame(
        [
            {
                "feature_id": "industrial_production_yoy_z",
                "series_id": "INDPRO",
                "date": "2026-01-01",
                "raw_value": 1.0,
                "transformed_value": 1.0,
                "normalized_value": 1.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-01-01",
                "window_end": "2026-01-01",
                "valid": True,
                "reason": "ok",
            }
        ]
    )
    store.upsert_features(features)
    runner = CliRunner()

    build_result = runner.invoke(
        app,
        [
            "build-asof-features",
            "--config",
            str(config_path),
            "--db-path",
            str(db_path),
            "--parquet-dir",
            str(tmp_path / "fred"),
        ],
    )
    inspect_result = runner.invoke(
        app,
        [
            "inspect-asof-feature",
            "industrial_production_yoy_z",
            "--db-path",
            str(db_path),
        ],
    )

    assert build_result.exit_code == 0
    assert inspect_result.exit_code == 0
    assert "industrial_production_yoy_z" in inspect_result.output


def test_same_date_mode_still_consumes_stored_features(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    config_path = tmp_path / "config.yaml"
    source_config = open("config/phase_b_sources.yaml", encoding="utf-8").read()
    # Rewrite the `scoring_mode:` line by pattern: the shipped value is a point-in-time hybrid
    # now, so replacing a literal string silently produced no change at all.
    source_config = re.sub(
        r"(?m)^scoring_mode:.*$", "scoring_mode: same_date", source_config
    )
    assert "scoring_mode: same_date" in source_config
    config_path.write_text(source_config, encoding="utf-8")

    store = DuckDBStore(db_path)
    store.initialize()
    features = pd.DataFrame(
        [
            {
                "feature_id": "industrial_production_yoy_z",
                "series_id": "INDPRO",
                "date": "2026-01-01",
                "raw_value": 1.0,
                "transformed_value": 1.0,
                "normalized_value": 1.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-01-01",
                "window_end": "2026-01-01",
                "valid": True,
                "reason": "ok",
            },
            {
                "feature_id": "payrolls_yoy_z",
                "series_id": "PAYEMS",
                "date": "2026-01-01",
                "raw_value": 1.0,
                "transformed_value": 1.0,
                "normalized_value": 1.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-01-01",
                "window_end": "2026-01-01",
                "valid": True,
                "reason": "ok",
            },
            {
                "feature_id": "unemployment_6m_change_z",
                "series_id": "UNRATE",
                "date": "2026-01-01",
                "raw_value": 1.0,
                "transformed_value": 1.0,
                "normalized_value": 1.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-01-01",
                "window_end": "2026-01-01",
                "valid": True,
                "reason": "ok",
            },
        ]
    )
    store.upsert_features(features)

    result = build_stored_dimensions(
        config_path=config_path,
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
    )

    growth = result.dimension_scores[
        result.dimension_scores["dimension_id"] == "growth_momentum"
    ].iloc[0]
    assert bool(growth["valid"]) is True
    assert store.read_table("asof_feature_values").empty
