from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from macro_engine.dimensions.config import DimensionDefinition


@dataclass(frozen=True)
class DimensionBuildResult:
    contributions: pd.DataFrame
    dimension_scores: pd.DataFrame
    dimension_health: pd.DataFrame


def build_dimensions_from_features(
    features: pd.DataFrame,
    dimensions: list[DimensionDefinition],
) -> DimensionBuildResult:
    feature_frame = features.copy()
    if feature_frame.empty:
        feature_frame = pd.DataFrame(
            columns=["feature_id", "date", "normalized_value", "valid", "reason"]
        )
    feature_frame["date"] = pd.to_datetime(feature_frame["date"], errors="coerce")

    contribution_records: list[dict] = []
    score_rows: list[dict] = []
    health_rows: list[dict] = []

    for dimension in dimensions:
        if not dimension.enabled:
            continue
        dimension_contributions = _build_dimension_contributions(feature_frame, dimension)
        contribution_records.extend(dimension_contributions.to_dict(orient="records"))
        scores = _build_dimension_scores(dimension_contributions, dimension)
        score_rows.extend(scores.to_dict(orient="records"))
        health_rows.extend(
            _build_dimension_health(scores, dimension_contributions, dimension).to_dict(
                orient="records"
            )
        )

    contributions = pd.DataFrame(contribution_records, columns=_contribution_columns())
    scores = pd.DataFrame(score_rows)
    health = pd.DataFrame(health_rows)
    return DimensionBuildResult(
        contributions=contributions,
        dimension_scores=scores,
        dimension_health=health,
    )


def _build_dimension_contributions(
    features: pd.DataFrame,
    dimension: DimensionDefinition,
) -> pd.DataFrame:
    rows: list[dict] = []
    configured = {feature.feature_id: feature for feature in dimension.features}
    dimension_features = features[features["feature_id"].isin(configured)].copy()
    all_dates = sorted(dimension_features["date"].dropna().unique())
    if not all_dates:
        return pd.DataFrame(rows, columns=_contribution_columns())
    latest_by_feature_date = {
        (row["feature_id"], row["date"]): row
        for row in dimension_features.sort_values("date").to_dict(orient="records")
    }
    for date in all_dates:
        for dimension_feature in dimension.features:
            row = latest_by_feature_date.get((dimension_feature.feature_id, date))
            if row is None:
                rows.append(
                    _contribution_row(
                        dimension.dimension_id,
                        dimension_feature.feature_id,
                        date,
                        None,
                        dimension_feature.weight,
                        0.0,
                        dimension_feature.polarity,
                        None,
                        0.0,
                        False,
                        "missing_feature",
                    )
                )
                continue
            valid = bool(row["valid"]) and pd.notna(row["normalized_value"])
            normalized_value = (
                None if pd.isna(row["normalized_value"]) else float(row["normalized_value"])
            )
            signed_value = _signed_value(normalized_value, dimension_feature.polarity) if valid else None
            rows.append(
                _contribution_row(
                    dimension.dimension_id,
                    dimension_feature.feature_id,
                    date,
                    normalized_value,
                    configured[dimension_feature.feature_id].weight,
                    0.0,
                    dimension_feature.polarity,
                    signed_value,
                    0.0,
                    valid,
                    "ok" if valid else row.get("reason", "invalid_feature"),
                )
            )

    frame = pd.DataFrame(rows, columns=_contribution_columns())
    if frame.empty:
        return frame
    for date, group in frame.groupby("date", dropna=False):
        valid_mask = group["valid"]
        used_weight = float(group.loc[valid_mask, "weight"].sum())
        if used_weight <= 0:
            continue
        indexes = group.loc[valid_mask].index
        frame.loc[indexes, "normalized_weight"] = frame.loc[indexes, "weight"] / used_weight
        frame.loc[indexes, "contribution"] = (
            frame.loc[indexes, "signed_value"] * frame.loc[indexes, "normalized_weight"]
        )
    return frame


def _build_dimension_scores(
    contributions: pd.DataFrame,
    dimension: DimensionDefinition,
) -> pd.DataFrame:
    rows: list[dict] = []
    total_weight = sum(feature.weight for feature in dimension.features)
    configured_count = len(dimension.features)
    for date, group in contributions.groupby("date", dropna=False):
        valid_group = group[group["valid"]]
        valid_count = int(len(valid_group))
        used_weight = float(valid_group["weight"].sum())
        coverage = 0.0 if total_weight == 0 else used_weight / total_weight
        valid = (
            valid_count >= dimension.min_valid_features
            and coverage >= dimension.min_coverage_ratio
            and used_weight > 0
        )
        if not valid:
            reason = (
                "below_min_valid_features"
                if valid_count < dimension.min_valid_features
                else "below_min_coverage_ratio"
            )
            score = None
        else:
            reason = "ok"
            score = float(valid_group["contribution"].sum())
        rows.append(
            {
                "dimension_id": dimension.dimension_id,
                "date": pd.Timestamp(date).date(),
                "score": score,
                "valid_feature_count": valid_count,
                "configured_feature_count": configured_count,
                "total_configured_weight": float(total_weight),
                "used_weight": used_weight,
                "coverage_ratio": coverage,
                "valid": valid,
                "reason": reason,
            }
        )
    return pd.DataFrame(rows)


def _build_dimension_health(
    scores: pd.DataFrame,
    contributions: pd.DataFrame,
    dimension: DimensionDefinition,
) -> pd.DataFrame:
    rows: list[dict] = []
    if scores.empty:
        return pd.DataFrame(
            [
                {
                    "dimension_id": dimension.dimension_id,
                    "date": pd.NaT,
                    "valid": False,
                    "valid_feature_count": 0,
                    "required_feature_count": dimension.min_valid_features,
                    "missing_features": [],
                    "invalid_features": [feature.feature_id for feature in dimension.features],
                    "reason": "no_scores",
                }
            ]
        )
    for row in scores.to_dict(orient="records"):
        date_contributions = contributions[contributions["date"] == row["date"]]
        missing_features = date_contributions[
            date_contributions["reason"] == "missing_feature"
        ]["feature_id"].tolist()
        invalid_features = date_contributions[
            (~date_contributions["valid"]) & (date_contributions["reason"] != "missing_feature")
        ]["feature_id"].tolist()
        rows.append(
            {
                "dimension_id": dimension.dimension_id,
                "date": row["date"],
                "valid": row["valid"],
                "valid_feature_count": row["valid_feature_count"],
                "required_feature_count": dimension.min_valid_features,
                "missing_features": missing_features,
                "invalid_features": invalid_features,
                "reason": row["reason"],
            }
        )
    return pd.DataFrame(rows)


def _signed_value(value: float | None, polarity: str) -> float | None:
    if value is None:
        return None
    return value if polarity == "positive" else -value


def _contribution_row(
    dimension_id: str,
    feature_id: str,
    date: pd.Timestamp,
    normalized_value: float | None,
    weight: float,
    normalized_weight: float,
    polarity: str,
    signed_value: float | None,
    contribution: float,
    valid: bool,
    reason: str,
) -> dict:
    return {
        "dimension_id": dimension_id,
        "feature_id": feature_id,
        "date": pd.Timestamp(date).date(),
        "normalized_value": normalized_value,
        "weight": float(weight),
        "normalized_weight": float(normalized_weight),
        "polarity": polarity,
        "signed_value": signed_value,
        "contribution": float(contribution),
        "valid": valid,
        "reason": reason,
    }


def _contribution_columns() -> list[str]:
    return [
        "dimension_id",
        "feature_id",
        "date",
        "normalized_value",
        "weight",
        "normalized_weight",
        "polarity",
        "signed_value",
        "contribution",
        "valid",
        "reason",
    ]
