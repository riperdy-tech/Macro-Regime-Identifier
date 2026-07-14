"""Tests for publication-lag-aware calendar as-of alignment."""

from __future__ import annotations

import pandas as pd
import yaml

from macro_engine.evaluation.calendar import build_asof_feature_values
from macro_engine.evaluation.config import EvaluationCalendarConfig
from macro_engine.features.config import FeatureDefinition
from macro_engine.ingest.schemas import IngestionSource


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


def _source(series_id: str, frequency: str, publication_lag_days: int = 0) -> IngestionSource:
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
            "publication_lag_days": publication_lag_days,
        }
    )


def _feature_row(feature_id: str, date: str, value: float) -> dict:
    return {
        "feature_id": feature_id,
        "series_id": feature_id.upper(),
        "date": date,
        "raw_value": value,
        "transformed_value": value,
        "normalized_value": value,
        "transform": "level",
        "normalization": "none",
        "window_start": date,
        "window_end": date,
        "valid": True,
        "reason": "ok",
    }


def _calendar(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "evaluation_date": pd.to_datetime(dates),
            "frequency": "monthly",
            "valid": True,
            "reason": "ok",
        }
    )


def _config() -> EvaluationCalendarConfig:
    return EvaluationCalendarConfig.model_validate(
        {
            "frequency": "monthly",
            "date_rule": "month_start",
            "start_date": "2026-05-01",
            "end_date": "2026-07-01",
        }
    )


def test_zero_lag_keeps_observation_date_behavior():
    features = pd.DataFrame(
        [_feature_row("cpi", "2026-06-01", 1.0), _feature_row("cpi", "2026-07-01", 2.0)]
    )
    asof = build_asof_feature_values(
        features=features,
        feature_definitions=[_feature("cpi", "CPI")],
        sources=[_source("CPI", "monthly", publication_lag_days=0)],
        calendar=_calendar(["2026-07-01"]),
        config=_config(),
    )
    row = asof.iloc[0]
    assert row["valid"]
    assert pd.Timestamp(row["source_observation_date"]) == pd.Timestamp("2026-07-01")


def test_lag_excludes_unpublished_observation():
    # June CPI (observation date 2026-06-01) with a 44-day publication lag is
    # not public until mid-July, so the 2026-07-01 evaluation must fall back
    # to the May observation.
    features = pd.DataFrame(
        [_feature_row("cpi", "2026-05-01", 1.0), _feature_row("cpi", "2026-06-01", 2.0)]
    )
    asof = build_asof_feature_values(
        features=features,
        feature_definitions=[_feature("cpi", "CPI")],
        sources=[_source("CPI", "monthly", publication_lag_days=44)],
        calendar=_calendar(["2026-07-01"]),
        config=_config(),
    )
    row = asof.iloc[0]
    assert row["valid"]
    assert pd.Timestamp(row["source_observation_date"]) == pd.Timestamp("2026-05-01")
    assert float(row["normalized_value"]) == 1.0


def test_lag_boundary_observation_is_included_on_release_day():
    # Observation + lag exactly equal to the evaluation date counts as
    # published (release day itself is visible).
    features = pd.DataFrame([_feature_row("pmi", "2026-06-01", 5.0)])
    asof = build_asof_feature_values(
        features=features,
        feature_definitions=[_feature("pmi", "PMI")],
        sources=[_source("PMI", "monthly", publication_lag_days=30)],
        calendar=_calendar(["2026-07-01"]),
        config=_config(),
    )
    row = asof.iloc[0]
    assert row["valid"]
    assert pd.Timestamp(row["source_observation_date"]) == pd.Timestamp("2026-06-01")


def test_all_observations_unpublished_reports_not_yet_published():
    features = pd.DataFrame([_feature_row("m2", "2026-06-01", 5.0)])
    asof = build_asof_feature_values(
        features=features,
        feature_definitions=[_feature("m2", "M2")],
        sources=[_source("M2", "monthly", publication_lag_days=55)],
        calendar=_calendar(["2026-07-01"]),
        config=_config(),
    )
    row = asof.iloc[0]
    assert not row["valid"]
    assert row["reason"] == "not_yet_published"


def test_only_future_observations_keep_no_prior_valid_feature_reason():
    # Observation exists but is dated after the evaluation date with no lag:
    # that is the plain "nothing prior" case, not "not_yet_published".
    features = pd.DataFrame([_feature_row("m2", "2026-08-01", 5.0)])
    asof = build_asof_feature_values(
        features=features,
        feature_definitions=[_feature("m2", "M2")],
        sources=[_source("M2", "monthly", publication_lag_days=0)],
        calendar=_calendar(["2026-07-01"]),
        config=_config(),
    )
    m2_row = asof[asof["feature_id"] == "m2"].iloc[0]
    assert not m2_row["valid"]
    assert m2_row["reason"] == "no_prior_valid_feature"


def test_production_config_declares_lags_for_enabled_sources():
    with open("config/phase_b_sources.yaml", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    for source in data["sources"]:
        if not source.get("enabled", True):
            continue
        assert (
            source.get("publication_lag_days", 0) > 0
        ), f"enabled source {source['series_id']} must declare publication_lag_days"
