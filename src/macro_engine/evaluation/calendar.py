from __future__ import annotations

import bisect
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
    scoring_mode: str = "calendar_asof",
    publication_index: pd.DataFrame | None = None,
    point_in_time_start: str | None = None,
    answered_asofs: dict[str, list[pd.Timestamp]] | None = None,
    max_evidence_lag_days: int = 7,
) -> pd.DataFrame:
    """As-of feature values for every evaluation date in `calendar`.

    `point_in_time_start` makes the point-in-time rule a HYBRID: dates on or after it resolve
    from ALFRED vintages, earlier dates from the calendar rule. Without a per-date switch, a
    blanket `point_in_time` would leave pre-archive dates with a rule that cannot answer for
    them at all (no vintage exists), which reads as "the data was never published" rather than
    "this rule does not reach back that far" -- two very different statements to put in a
    historical diagnostic.
    """
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
    point_in_time_configured = scoring_mode == "point_in_time"
    boundary = pd.Timestamp(point_in_time_start) if point_in_time_start else None
    first_known = _first_known_lookup(publication_index) if point_in_time_configured else {}
    answered_lookup: dict[str, list[pd.Timestamp]] | None = None
    if answered_asofs is not None:
        answered_lookup = {
            s: sorted(set(pd.Timestamp(t).normalize() for t in ts))
            for s, ts in answered_asofs.items()
        }

    for evaluation_date in pd.to_datetime(calendar["evaluation_date"], errors="coerce"):
        # The configured basis is point-in-time; the APPLIED basis depends on this date.
        point_in_time = point_in_time_configured and (
            boundary is None or pd.isna(evaluation_date) or evaluation_date >= boundary
        )
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

            if point_in_time and answered_lookup is not None and not pd.isna(evaluation_date):
                series_asofs = answered_lookup.get(feature.series_id, [])
                window_start = evaluation_date.normalize() - pd.Timedelta(days=max_evidence_lag_days)
                window_end = evaluation_date.normalize()
                idx = bisect.bisect_left(series_asofs, window_start)
                has_evidence = idx < len(series_asofs) and series_asofs[idx] <= window_end
                if not has_evidence:
                    rows.append(
                        _asof_row(
                            evaluation_date=evaluation_date,
                            feature_id=feature.feature_id,
                            source_observation_date=None,
                            transformed_value=None,
                            normalized_value=None,
                            lag_days=None,
                            valid=False,
                            reason="pit_vintage_pending",
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
            if point_in_time:
                # The vintage says what had actually been released, so the fixed
                # publication-lag approximation is replaced by the measured first
                # publication date. A series with no vintages is NOT silently
                # approximated -- it is reported as unusable evidence.
                usable, reason = _point_in_time_usable(
                    observed,
                    series_id=feature.series_id,
                    evaluation_date=evaluation_date,
                    first_known=first_known,
                )
            else:
                # An observation only becomes visible publication_lag_days after
                # its observation date; drop observations not yet released as of
                # the evaluation date.
                available_cutoff = evaluation_date - pd.Timedelta(
                    days=int(publication_lag.get(feature.series_id, 0))
                )
                usable = observed[observed["date"] <= available_cutoff]
                reason = ""
            if usable.empty:
                if point_in_time and reason:
                    failure_reason = reason
                else:
                    failure_reason = (
                        "not_yet_published" if not observed.empty else "no_prior_valid_feature"
                    )
                rows.append(
                    _asof_row(
                        evaluation_date=evaluation_date,
                        feature_id=feature.feature_id,
                        source_observation_date=None,
                        transformed_value=None,
                        normalized_value=None,
                        lag_days=None,
                        valid=False,
                        reason=failure_reason,
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


def _first_known_lookup(publication_index: pd.DataFrame | None) -> dict[tuple[str, pd.Timestamp], pd.Timestamp]:
    if publication_index is None or publication_index.empty:
        return {}
    frame = publication_index.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["first_known_date"] = pd.to_datetime(frame["first_known_date"], errors="coerce")
    frame = frame.dropna(subset=["series_id", "date", "first_known_date"])
    return {
        (str(row.series_id), pd.Timestamp(row.date)): pd.Timestamp(row.first_known_date)
        for row in frame.itertuples(index=False)
    }


def _point_in_time_usable(
    observed: pd.DataFrame,
    *,
    series_id: str,
    evaluation_date: pd.Timestamp,
    first_known: dict[tuple[str, pd.Timestamp], pd.Timestamp],
) -> tuple[pd.DataFrame, str]:
    """Rows whose source period was already published on the evaluation date.

    Returns (usable_rows, failure_reason). failure_reason is non-empty only when the
    vintage evidence itself is missing, so the caller can say `pit_vintage_missing`
    instead of the misleading `not_yet_published`.
    """
    if not first_known:
        return observed.iloc[0:0], "pit_vintage_missing"
    known_dates = [date for (sid, date) in first_known if sid == series_id]
    if not known_dates:
        return observed.iloc[0:0], "pit_vintage_missing"
    published = observed["date"].map(lambda value: first_known.get((series_id, pd.Timestamp(value))))
    usable = observed[published.notna() & (published <= evaluation_date)]
    return usable, ""


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
