from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from macro_engine.dimensions.composition import CompositionRegistry
from macro_engine.dimensions.config import DimensionDefinition


class DuplicateFeatureRows(Exception):
    """More than one stored row exists for one (feature_id, date) key.

    A database persisted across repeated runs has been observed accumulating up to 11
    rows per key, with materially disagreeing z-scores (0.5-0.75 z spread, up to 4.4 z)
    and nothing recording which row was current. Picking one arbitrarily (the previous
    behavior here: the last row after `sort_values("date")`) silently changes the score
    depending on run history. Scoring must stop and name the key instead.
    """

    def __init__(self, feature_id: str, date: object, n: int) -> None:
        self.feature_id = feature_id
        self.date = date
        self.n = n
        super().__init__(
            f"duplicate feature rows for feature_id={feature_id!r} date={date!r}: "
            f"{n} rows stored, expected 1"
        )


@dataclass(frozen=True)
class DimensionBuildResult:
    contributions: pd.DataFrame
    dimension_scores: pd.DataFrame
    dimension_health: pd.DataFrame


def build_dimensions_from_features(
    features: pd.DataFrame,
    dimensions: list[DimensionDefinition],
    composition: CompositionRegistry | None = None,
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
        dimension_contributions = _build_dimension_contributions(feature_frame, dimension, composition)
        contribution_records.extend(dimension_contributions.to_dict(orient="records"))
        scores = _build_dimension_scores(dimension_contributions, dimension, composition)
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
    composition: CompositionRegistry | None = None,
) -> pd.DataFrame:
    rows: list[dict] = []
    configured = {feature.feature_id: feature for feature in dimension.features}
    dimension_features = features[features["feature_id"].isin(configured)].copy()
    all_dates = sorted(dimension_features["date"].dropna().unique())
    if not all_dates:
        return pd.DataFrame(rows, columns=_contribution_columns())
    duplicate_counts = dimension_features.groupby(["feature_id", "date"]).size()
    duplicated = duplicate_counts[duplicate_counts > 1]
    if not duplicated.empty:
        feature_id, date = duplicated.index[0]
        raise DuplicateFeatureRows(feature_id, date, int(duplicated.iloc[0]))
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
            reason = "ok" if valid else row.get("reason", "invalid_feature")
            # S1.4 (P0_0 §2.6): a feature that validates outside its declared composition
            # window is an undeclared re-specification of the dimension (the HY OAS case,
            # S0.2), not an ordinary data point. It never SILENTLY widens the valid set --
            # the feature is excluded here and the whole date is invalidated below, in
            # `_build_dimension_scores`, once every row for the date is visible.
            if valid and composition is not None:
                declared = composition.declared_feature_ids(
                    dimension.dimension_id, pd.Timestamp(date).date()
                )
                if declared is not None and dimension_feature.feature_id not in declared:
                    valid = False
                    reason = f"undeclared_composition:extra:{dimension_feature.feature_id}"
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
                    reason,
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
    composition: CompositionRegistry | None = None,
) -> pd.DataFrame:
    rows: list[dict] = []
    total_weight = sum(feature.weight for feature in dimension.features)
    configured_count = len(dimension.features)
    for date, group in contributions.groupby("date", dropna=False):
        valid_group = group[group["valid"]]
        valid_count = int(len(valid_group))
        used_weight = float(valid_group["weight"].sum())
        coverage = 0.0 if total_weight == 0 else used_weight / total_weight
        # S1.4 (P0_0 §2.6): an undeclared-composition feature invalidates the whole
        # dimension for this date, not just its own contribution -- an unexpected feature
        # entering the valid set is a re-specification of the dimension, which coverage
        # renormalization would otherwise absorb silently.
        composition_reasons = [
            str(value)
            for value in group["reason"]
            if str(value).startswith("undeclared_composition:")
        ]
        valid = (
            not composition_reasons
            and valid_count >= dimension.min_valid_features
            and coverage >= dimension.min_coverage_ratio
            and used_weight > 0
        )
        if not valid:
            reason = (
                composition_reasons[0]
                if composition_reasons
                else "below_min_valid_features"
                if valid_count < dimension.min_valid_features
                else "below_min_coverage_ratio"
            )
            score = None
        else:
            reason = "ok"
            score = float(valid_group["contribution"].sum())
        # C3/C9 (MRI_S1_APPROVAL.md S8 item 2, S9 C3): publish the S1.4 composition registry's
        # id for this (dimension, date), so `current_regime.json`'s `factors[dim].composition_id`
        # names what was actually declared, not just whether it validated. None when the
        # dimension carries no declared composition (registration is additive, not required
        # of every dimension) -- same "not registered" case `declared_feature_ids` returns None
        # for.
        composition_id = (
            composition.composition_id(dimension.dimension_id, pd.Timestamp(date).date())
            if composition is not None
            else None
        )
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
                "composition_id": composition_id,
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
