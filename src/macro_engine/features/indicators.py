from __future__ import annotations

from datetime import date

import pandas as pd

from macro_engine.config.schemas import ModelConfig, SourceConfig
from macro_engine.normalize.transforms import apply_normalization, apply_transform


def _as_date(value: str | date | None) -> pd.Timestamp:
    if value is None or value == "today":
        return pd.Timestamp.today().normalize()
    return pd.Timestamp(value).normalize()


def _freshness_score(source: SourceConfig, last_observation: pd.Timestamp, as_of: pd.Timestamp) -> float:
    age_days = (as_of - last_observation).days
    if age_days <= source.stale_after_days:
        return 1.0
    if age_days <= source.unusable_after_days:
        return 0.5
    return 0.0


def build_feature_values(
    raw_observations: pd.DataFrame,
    sources: list[SourceConfig],
    model_config: ModelConfig,
    as_of: str | None = None,
) -> pd.DataFrame:
    as_of_date = _as_date(as_of)
    raw = raw_observations.copy()
    if raw.empty:
        raw = pd.DataFrame(columns=["series_id", "date", "value"])
    raw["date"] = pd.to_datetime(raw.get("date"))
    rows: list[dict] = []

    for source in sources:
        if not source.enabled:
            rows.append(
                {
                    "as_of": as_of_date.date().isoformat(),
                    "feature_id": source.feature_id,
                    "series_id": source.series_id,
                    "dimension": source.dimension,
                    "transform": source.transform,
                    "normalization": source.normalization,
                    "raw_value": None,
                    "transformed_value": None,
                    "z_score": None,
                    "bounded_score": None,
                    "direction": source.direction,
                    "direction_multiplier": 1 if source.direction == "direct" else -1,
                    "feature_weight": source.weight,
                    "feature_signal": 0.0,
                    "source_tier": source.source_tier,
                    "required": source.required,
                    "enabled": False,
                    "available": False,
                    "freshness_score": 0.0,
                    "confidence": 0.0,
                    "last_observation": None,
                    "status": "disabled",
                    "reason": source.reason_disabled,
                }
            )
            continue

        series = raw[(raw["series_id"] == source.series_id) & (raw["date"] <= as_of_date)].copy()
        series = series.dropna(subset=["value"]).sort_values("date")
        if series.empty:
            rows.append(_missing_row(source, as_of_date, "missing", None))
            continue

        values = pd.to_numeric(series["value"], errors="coerce")
        transformed = apply_transform(values, source)
        z_score, bounded = apply_normalization(
            transformed,
            source,
            model_config.normalization.minimum_observations,
            model_config.normalization.zscore_clip,
            model_config.normalization.bounded_transform_divisor,
        )
        series = series.assign(
            transformed_value=transformed,
            z_score=z_score,
            bounded_score=bounded,
        ).dropna(subset=["bounded_score"])
        if series.empty:
            rows.append(_missing_row(source, as_of_date, "insufficient_history", None))
            continue

        latest = series.iloc[-1]
        last_observation = pd.Timestamp(latest["date"]).normalize()
        freshness = _freshness_score(source, last_observation, as_of_date)
        status = "fresh" if freshness == 1.0 else "stale" if freshness == 0.5 else "unusable"
        direction_multiplier = 1 if source.direction == "direct" else -1
        bounded_score = float(latest["bounded_score"])
        feature_signal = bounded_score * direction_multiplier * source.weight if freshness > 0 else 0.0
        rows.append(
            {
                "as_of": as_of_date.date().isoformat(),
                "feature_id": source.feature_id,
                "series_id": source.series_id,
                "dimension": source.dimension,
                "transform": source.transform,
                "normalization": source.normalization,
                "raw_value": float(latest["value"]),
                "transformed_value": float(latest["transformed_value"]),
                "z_score": float(latest["z_score"]),
                "bounded_score": bounded_score,
                "direction": source.direction,
                "direction_multiplier": direction_multiplier,
                "feature_weight": source.weight,
                "feature_signal": feature_signal,
                "source_tier": source.source_tier,
                "required": source.required,
                "enabled": source.enabled,
                "available": freshness > 0,
                "freshness_score": freshness,
                "confidence": freshness,
                "last_observation": last_observation.date().isoformat(),
                "status": status,
                "reason": None,
            }
        )

    return pd.DataFrame(rows)


def _missing_row(
    source: SourceConfig,
    as_of_date: pd.Timestamp,
    status: str,
    last_observation: str | None,
) -> dict:
    return {
        "as_of": as_of_date.date().isoformat(),
        "feature_id": source.feature_id,
        "series_id": source.series_id,
        "dimension": source.dimension,
        "transform": source.transform,
        "normalization": source.normalization,
        "raw_value": None,
        "transformed_value": None,
        "z_score": None,
        "bounded_score": None,
        "direction": source.direction,
        "direction_multiplier": 1 if source.direction == "direct" else -1,
        "feature_weight": source.weight,
        "feature_signal": 0.0,
        "source_tier": source.source_tier,
        "required": source.required,
        "enabled": source.enabled,
        "available": False,
        "freshness_score": 0.0,
        "confidence": 0.0,
        "last_observation": last_observation,
        "status": status,
        "reason": status,
    }
