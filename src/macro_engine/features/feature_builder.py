from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from macro_engine.features.config import (
    FeatureDefinition,
    NormalizationName,
    TransformName,
    enabled_source_ids,
    source_frequency_by_id,
)
from macro_engine.ingest.schemas import Frequency, IngestionSource


@dataclass(frozen=True)
class FeatureBuildResult:
    features: pd.DataFrame
    feature_health: pd.DataFrame


def build_features_from_raw(
    raw_observations: pd.DataFrame,
    sources: list[IngestionSource],
    feature_definitions: list[FeatureDefinition],
) -> FeatureBuildResult:
    raw = raw_observations.copy()
    if raw.empty:
        raw = pd.DataFrame(columns=["series_id", "date", "value"])
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw["value"] = pd.to_numeric(raw["value"], errors="coerce")

    source_frequency = source_frequency_by_id(sources)
    active_sources = enabled_source_ids(sources)
    rows: list[dict] = []

    for feature in feature_definitions:
        if not feature.enabled:
            rows.append(_invalid_feature_row(feature, "disabled_feature"))
            continue
        if feature.series_id not in active_sources:
            rows.append(_invalid_feature_row(feature, "disabled_source"))
            continue

        source_rows = raw[raw["series_id"] == feature.series_id].copy()
        source_rows = source_rows.dropna(subset=["date"])
        # Databases written before the (series_id, date) upsert fix can hold
        # duplicate vintages of the same observation; keep the latest fetch
        # per date so positional transforms and rolling windows stay correct.
        sort_columns = (
            ["date", "fetched_at"] if "fetched_at" in source_rows.columns else ["date"]
        )
        source_rows = source_rows.sort_values(sort_columns).drop_duplicates(
            subset=["date"], keep="last"
        )
        if source_rows.empty:
            rows.append(_invalid_feature_row(feature, "missing_source_data"))
            continue

        frequency = source_frequency[feature.series_id]
        try:
            feature_rows = _build_one_feature(source_rows, feature, frequency)
        except Exception as exc:  # pragma: no cover - defensive guard surfaced in health.
            rows.append(_invalid_feature_row(feature, f"transform_failure: {exc}"))
            continue
        rows.extend(feature_rows)

    feature_frame = pd.DataFrame(rows)
    health = build_feature_health(feature_frame, feature_definitions)
    return FeatureBuildResult(features=feature_frame, feature_health=health)


def build_feature_health(
    features: pd.DataFrame,
    feature_definitions: list[FeatureDefinition],
) -> pd.DataFrame:
    rows: list[dict] = []
    for feature in feature_definitions:
        frame = features[features["feature_id"] == feature.feature_id]
        valid_count = int(frame["valid"].fillna(False).sum()) if not frame.empty else 0
        invalid_count = int((~frame["valid"].fillna(False)).sum()) if not frame.empty else 0
        latest_valid = frame[frame["valid"]]["date"].max() if valid_count else pd.NaT
        reason_counts = (
            frame[~frame["valid"].fillna(False)]["reason"].value_counts().to_dict()
            if not frame.empty
            else {"missing_feature_output": 1}
        )
        rows.append(
            {
                "feature_id": feature.feature_id,
                "series_id": feature.series_id,
                "enabled": feature.enabled,
                "valid_count": valid_count,
                "invalid_count": invalid_count,
                "latest_valid_date": latest_valid,
                "usable": bool(feature.enabled and valid_count > 0),
                "reason": "ok" if feature.enabled and valid_count > 0 else next(iter(reason_counts)),
                "reason_counts": reason_counts,
            }
        )
    return pd.DataFrame(rows)


def _build_one_feature(
    source_rows: pd.DataFrame,
    feature: FeatureDefinition,
    frequency: Frequency,
) -> list[dict]:
    values = source_rows["value"].astype(float)
    transformed = apply_feature_transform(values, feature.transform, frequency)
    normalized, window_start = apply_feature_normalization(
        transformed,
        source_rows["date"],
        feature.normalization,
        frequency,
        feature.min_observations,
    )
    rows: list[dict] = []
    for position, (_, row) in enumerate(source_rows.iterrows()):
        raw_value = values.iloc[position]
        transformed_value = transformed.iloc[position]
        normalized_value = normalized.iloc[position]
        valid = bool(pd.notna(transformed_value) and pd.notna(normalized_value))
        reason = "ok"
        if pd.isna(raw_value):
            reason = "missing_raw_value"
        elif pd.isna(transformed_value):
            reason = "insufficient_transform_history"
        elif pd.isna(normalized_value):
            reason = "insufficient_normalization_history"
        rows.append(
            {
                "feature_id": feature.feature_id,
                "series_id": feature.series_id,
                "date": row["date"].date(),
                "raw_value": None if pd.isna(raw_value) else float(raw_value),
                "transformed_value": None
                if pd.isna(transformed_value)
                else float(transformed_value),
                "normalized_value": None if pd.isna(normalized_value) else float(normalized_value),
                "transform": feature.transform,
                "normalization": feature.normalization,
                "window_start": window_start.iloc[position],
                "window_end": row["date"].date(),
                "valid": valid,
                "reason": reason,
            }
        )
    return rows


def apply_feature_transform(
    values: pd.Series,
    transform: TransformName,
    frequency: Frequency,
) -> pd.Series:
    if transform == "level":
        return values
    if transform == "diff_3m":
        return values.diff(_periods_for_months(frequency, 3))
    if transform == "diff_6m":
        return values.diff(_periods_for_months(frequency, 6))
    if transform == "diff_12m":
        return values.diff(_periods_for_months(frequency, 12))
    if transform == "yoy_pct_change":
        return values.pct_change(_periods_for_months(frequency, 12), fill_method=None) * 100
    if transform == "spread":
        return values
    raise ValueError(f"unsupported transform {transform}")


def apply_feature_normalization(
    transformed: pd.Series,
    dates: pd.Series,
    normalization: NormalizationName,
    frequency: Frequency,
    min_observations: int,
) -> tuple[pd.Series, pd.Series]:
    if normalization == "none":
        window_start = dates.where(transformed.notna(), pd.NaT).dt.date
        return transformed, window_start
    if normalization == "expanding_z":
        expanding = transformed.expanding(min_periods=min_observations)
        mean = expanding.mean()
        std = expanding.std(ddof=0).replace(0, np.nan)
        normalized = (transformed - mean) / std
        first_dates = _expanding_window_start(dates, transformed, min_observations)
        return normalized, first_dates

    years = int(normalization.removeprefix("rolling_z_").removesuffix("y"))
    window = _periods_for_years(frequency, years)
    min_periods = min(min_observations, window)
    rolling = transformed.rolling(window=window, min_periods=min_periods)
    mean = rolling.mean()
    std = rolling.std(ddof=0).replace(0, np.nan)
    normalized = (transformed - mean) / std
    window_start = dates.shift(window - 1)
    if window > min_periods:
        window_start = window_start.fillna(dates.shift(min_periods - 1))
    return normalized, window_start.dt.date


def _periods_for_months(frequency: Frequency, months: int) -> int:
    if frequency == "daily":
        return max(1, months * 21)
    if frequency == "weekly":
        return max(1, round(months * 4.33))
    if frequency == "monthly":
        return months
    if frequency == "quarterly":
        return max(1, round(months / 3))
    if frequency == "annual":
        return max(1, round(months / 12))
    raise ValueError(f"unsupported frequency {frequency}")


def _periods_for_years(frequency: Frequency, years: int) -> int:
    if frequency == "daily":
        return years * 252
    if frequency == "weekly":
        return years * 52
    if frequency == "monthly":
        return years * 12
    if frequency == "quarterly":
        return years * 4
    if frequency == "annual":
        return years
    raise ValueError(f"unsupported frequency {frequency}")


def _expanding_window_start(
    dates: pd.Series,
    transformed: pd.Series,
    min_observations: int,
) -> pd.Series:
    valid_positions = transformed.notna().cumsum()
    first_date = dates[transformed.notna()].min()
    return pd.Series(
        [first_date.date() if count >= min_observations else pd.NaT for count in valid_positions],
        index=dates.index,
    )


def _invalid_feature_row(feature: FeatureDefinition, reason: str) -> dict:
    return {
        "feature_id": feature.feature_id,
        "series_id": feature.series_id,
        "date": pd.NaT,
        "raw_value": None,
        "transformed_value": None,
        "normalized_value": None,
        "transform": feature.transform,
        "normalization": feature.normalization,
        "window_start": pd.NaT,
        "window_end": pd.NaT,
        "valid": False,
        "reason": reason,
    }
