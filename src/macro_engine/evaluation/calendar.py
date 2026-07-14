from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from macro_engine.evaluation.config import EvaluationCalendarConfig
from macro_engine.features.config import FeatureDefinition
from macro_engine.ingest.schemas import IngestionSource


@dataclass(frozen=True)
class EvaluationBuildResult:
    evaluation_calendar: pd.DataFrame
    asof_feature_values: pd.DataFrame


def build_evaluation_calendar(
    config: EvaluationCalendarConfig,
    features: pd.DataFrame,
) -> pd.DataFrame:
    start = _resolve_start_date(config, features)
    end = _resolve_end_date(config, features)
    if start is None or end is None or start > end:
        return pd.DataFrame(columns=["evaluation_date", "frequency", "valid", "reason"])

    frequency = "MS" if config.date_rule == "month_start" else "ME"
    dates = pd.date_range(start=start, end=end, freq=frequency)
    return pd.DataFrame(
        {
            "evaluation_date": dates,
            "frequency": config.frequency,
            "valid": True,
            "reason": "ok",
        }
    )


def build_asof_feature_values(
    *,
    features: pd.DataFrame,
    feature_definitions: list[FeatureDefinition],
    sources: list[IngestionSource],
    calendar: pd.DataFrame,
    config: EvaluationCalendarConfig,
) -> pd.DataFrame:
    rows: list[dict] = []
    if calendar.empty:
        return pd.DataFrame(rows, columns=_asof_columns())

    source_frequency = {source.series_id: source.frequency for source in sources}
    publication_lag = {source.series_id: source.publication_lag_days for source in sources}
    feature_frame = features.copy()
    if feature_frame.empty:
        feature_frame = pd.DataFrame(columns=["feature_id", "date"])
    feature_frame["date"] = pd.to_datetime(feature_frame["date"], errors="coerce")

    grouped_features = {
        feature_id: frame.sort_values("date")
        for feature_id, frame in feature_frame.groupby("feature_id", dropna=False)
    }

    for evaluation_date in pd.to_datetime(calendar["evaluation_date"], errors="coerce"):
        for feature in feature_definitions:
            if not feature.enabled:
                rows.append(
                    _asof_row(
                        evaluation_date=evaluation_date,
                        feature_id=feature.feature_id,
                        source_observation_date=None,
                        transformed_value=None,
                        normalized_value=None,
                        lag_days=None,
                        valid=False,
                        reason="disabled_feature",
                    )
                )
                continue

            frame = grouped_features.get(feature.feature_id)
            if frame is None or frame.empty:
                rows.append(
                    _asof_row(
                        evaluation_date=evaluation_date,
                        feature_id=feature.feature_id,
                        source_observation_date=None,
                        transformed_value=None,
                        normalized_value=None,
                        lag_days=None,
                        valid=False,
                        reason="missing_feature",
                    )
                )
                continue

            observed = frame[
                (frame["date"] <= evaluation_date)
                & frame["valid"].fillna(False)
                & frame["normalized_value"].notna()
            ]
            # An observation only becomes visible publication_lag_days after
            # its observation date; drop observations not yet released as of
            # the evaluation date.
            available_cutoff = evaluation_date - pd.Timedelta(
                days=int(publication_lag.get(feature.series_id, 0))
            )
            usable = observed[observed["date"] <= available_cutoff]
            if usable.empty:
                rows.append(
                    _asof_row(
                        evaluation_date=evaluation_date,
                        feature_id=feature.feature_id,
                        source_observation_date=None,
                        transformed_value=None,
                        normalized_value=None,
                        lag_days=None,
                        valid=False,
                        reason="not_yet_published"
                        if not observed.empty
                        else "no_prior_valid_feature",
                    )
                )
                continue

            latest = usable.iloc[-1]
            source_date = pd.Timestamp(latest["date"])
            lag_days = int((evaluation_date.normalize() - source_date.normalize()).days)
            frequency = source_frequency.get(feature.series_id, "monthly")
            max_lag = config.max_lag_by_frequency[str(frequency)]
            valid = lag_days <= max_lag
            rows.append(
                _asof_row(
                    evaluation_date=evaluation_date,
                    feature_id=feature.feature_id,
                    source_observation_date=source_date,
                    transformed_value=latest["transformed_value"],
                    normalized_value=latest["normalized_value"],
                    lag_days=lag_days,
                    valid=valid,
                    reason="ok" if valid else "stale_asof_value",
                )
            )

    return pd.DataFrame(rows, columns=_asof_columns())


def asof_values_to_feature_frame(asof_values: pd.DataFrame) -> pd.DataFrame:
    if asof_values.empty:
        return pd.DataFrame(columns=["feature_id", "date", "normalized_value", "valid", "reason"])
    frame = asof_values.copy()
    frame["date"] = frame["evaluation_date"]
    return frame[
        [
            "feature_id",
            "date",
            "transformed_value",
            "normalized_value",
            "valid",
            "reason",
        ]
    ]


def _resolve_start_date(
    config: EvaluationCalendarConfig,
    features: pd.DataFrame,
) -> pd.Timestamp | None:
    if config.start_date:
        return pd.Timestamp(config.start_date)
    if features.empty:
        return None
    dates = pd.to_datetime(features["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return pd.Timestamp(dates.min()).normalize()


def _resolve_end_date(
    config: EvaluationCalendarConfig,
    features: pd.DataFrame,
) -> pd.Timestamp | None:
    if config.end_date:
        return pd.Timestamp(config.end_date)
    if features.empty:
        return None
    dates = pd.to_datetime(features["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return pd.Timestamp(dates.max()).normalize()


def _asof_row(
    *,
    evaluation_date: pd.Timestamp,
    feature_id: str,
    source_observation_date: pd.Timestamp | None,
    transformed_value: float | None,
    normalized_value: float | None,
    lag_days: int | None,
    valid: bool,
    reason: str,
) -> dict:
    return {
        "evaluation_date": evaluation_date,
        "feature_id": feature_id,
        "source_observation_date": source_observation_date,
        "transformed_value": None if pd.isna(transformed_value) else float(transformed_value),
        "normalized_value": None if pd.isna(normalized_value) else float(normalized_value),
        "lag_days": lag_days,
        "valid": bool(valid),
        "reason": reason,
    }


def _asof_columns() -> list[str]:
    return [
        "evaluation_date",
        "feature_id",
        "source_observation_date",
        "transformed_value",
        "normalized_value",
        "lag_days",
        "valid",
        "reason",
    ]
