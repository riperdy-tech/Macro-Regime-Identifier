from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

from macro_engine.regimes.config import RegimeDefinition, RegimeScoringConfig


@dataclass(frozen=True)
class RegimeBuildResult:
    contributions: pd.DataFrame
    regime_scores: pd.DataFrame
    regime_health: pd.DataFrame


def build_regimes_from_dimensions(
    dimension_scores: pd.DataFrame,
    regimes: list[RegimeDefinition],
    scoring: RegimeScoringConfig,
) -> RegimeBuildResult:
    dimensions = dimension_scores.copy()
    if dimensions.empty:
        dimensions = pd.DataFrame(columns=["dimension_id", "date", "score", "valid", "reason"])
    dimensions["date"] = pd.to_datetime(dimensions["date"], errors="coerce")

    contribution_records: list[dict] = []
    score_records: list[dict] = []
    for regime in regimes:
        if not regime.enabled:
            continue
        contributions = _build_regime_contributions(dimensions, regime)
        contribution_records.extend(contributions.to_dict(orient="records"))
        scores = _build_raw_regime_scores(contributions, regime)
        score_records.extend(scores.to_dict(orient="records"))

    contribution_frame = pd.DataFrame(contribution_records, columns=_contribution_columns())
    score_frame = pd.DataFrame(score_records, columns=_score_columns())
    score_frame = _apply_probabilities(score_frame, scoring)
    health_frame = _build_regime_health(score_frame)
    return RegimeBuildResult(
        contributions=contribution_frame,
        regime_scores=score_frame,
        regime_health=health_frame,
    )


def transform_dimension_value(value: float | None, polarity: str) -> float | None:
    if value is None:
        return None
    if polarity == "positive":
        return value
    if polarity == "negative":
        return -value
    if polarity == "positive_only":
        return max(value, 0.0)
    if polarity == "negative_only":
        return max(-value, 0.0)
    if polarity == "penalize_positive_only":
        return -max(value, 0.0)
    if polarity == "penalize_negative_only":
        return -max(-value, 0.0)
    if polarity == "reward_near_zero":
        return -abs(value)
    raise ValueError(f"unsupported regime polarity {polarity}")


def _build_regime_contributions(
    dimensions: pd.DataFrame,
    regime: RegimeDefinition,
) -> pd.DataFrame:
    rows: list[dict] = []
    configured = {dimension.dimension_id for dimension in regime.dimensions}
    regime_dimensions = dimensions[dimensions["dimension_id"].isin(configured)].copy()
    all_dates = sorted(regime_dimensions["date"].dropna().unique())
    if not all_dates:
        return pd.DataFrame(rows, columns=_contribution_columns())
    latest_by_dimension_date = {
        (row["dimension_id"], row["date"]): row
        for row in regime_dimensions.sort_values("date").to_dict(orient="records")
    }
    for date in all_dates:
        for regime_dimension in regime.dimensions:
            row = latest_by_dimension_date.get((regime_dimension.dimension_id, date))
            if row is None:
                rows.append(
                    _contribution_row(
                        regime.regime_id,
                        regime_dimension.dimension_id,
                        date,
                        None,
                        regime_dimension.weight,
                        0.0,
                        regime_dimension.polarity,
                        None,
                        0.0,
                        False,
                        "missing_dimension",
                    )
                )
                continue
            dimension_valid = bool(row["valid"]) and pd.notna(row["score"])
            dimension_score = None if pd.isna(row["score"]) else float(row["score"])
            transformed = (
                transform_dimension_value(dimension_score, regime_dimension.polarity)
                if dimension_valid
                else None
            )
            rows.append(
                _contribution_row(
                    regime.regime_id,
                    regime_dimension.dimension_id,
                    date,
                    dimension_score,
                    regime_dimension.weight,
                    0.0,
                    regime_dimension.polarity,
                    transformed,
                    0.0,
                    dimension_valid,
                    "ok" if dimension_valid else row.get("reason", "invalid_dimension"),
                )
            )

    frame = pd.DataFrame(rows, columns=_contribution_columns())
    for _, group in frame.groupby(["regime_id", "date"], dropna=False):
        valid_group = group[group["valid"]]
        used_weight = float(valid_group["weight"].sum())
        if used_weight <= 0:
            continue
        indexes = valid_group.index
        frame.loc[indexes, "normalized_weight"] = frame.loc[indexes, "weight"] / used_weight
        frame.loc[indexes, "contribution"] = (
            frame.loc[indexes, "transformed_dimension_value"]
            * frame.loc[indexes, "normalized_weight"]
        )
    return frame


def _build_raw_regime_scores(
    contributions: pd.DataFrame,
    regime: RegimeDefinition,
) -> pd.DataFrame:
    rows: list[dict] = []
    total_weight = sum(dimension.weight for dimension in regime.dimensions)
    configured_count = len(regime.dimensions)
    for date, group in contributions.groupby("date", dropna=False):
        valid_group = group[group["valid"]]
        valid_count = int(len(valid_group))
        used_weight = float(valid_group["weight"].sum())
        coverage = 0.0 if total_weight == 0 else used_weight / total_weight
        valid = (
            valid_count >= regime.min_valid_dimensions
            and coverage >= regime.min_coverage_ratio
            and used_weight > 0
        )
        if valid:
            raw_score = float(valid_group["contribution"].sum())
            reason = "ok"
        else:
            raw_score = None
            reason = (
                "below_min_valid_dimensions"
                if valid_count < regime.min_valid_dimensions
                else "below_min_coverage_ratio"
            )
        rows.append(
            {
                "regime_id": regime.regime_id,
                "date": pd.Timestamp(date).date(),
                "raw_score": raw_score,
                "probability": None,
                "rank": None,
                "valid_dimension_count": valid_count,
                "configured_dimension_count": configured_count,
                "coverage_ratio": coverage,
                "valid": valid,
                "reason": reason,
            }
        )
    return pd.DataFrame(rows, columns=_score_columns())


def _apply_probabilities(
    scores: pd.DataFrame,
    scoring: RegimeScoringConfig,
) -> pd.DataFrame:
    frame = scores.copy()
    if frame.empty:
        return frame
    for date, group in frame.groupby("date", dropna=False):
        valid = group[group["valid"] & group["raw_score"].notna()]
        if valid.empty:
            continue
        probabilities = _softmax(
            valid.set_index("regime_id")["raw_score"].astype(float).to_dict(),
            scoring.softmax_temperature,
        )
        ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
        for rank, (regime_id, probability) in enumerate(ranked, start=1):
            mask = (frame["date"] == date) & (frame["regime_id"] == regime_id)
            frame.loc[mask, "probability"] = probability
            frame.loc[mask, "rank"] = rank
    return frame


def _build_regime_health(scores: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    if scores.empty:
        return pd.DataFrame(columns=_health_columns())
    for date, group in scores.groupby("date", dropna=False):
        valid = group[group["valid"] & group["probability"].notna()].copy()
        if valid.empty:
            rows.append(
                {
                    "date": pd.Timestamp(date).date(),
                    "valid": False,
                    "dominant_regime": None,
                    "dominant_probability": None,
                    "confidence": None,
                    "entropy": None,
                    "valid_regime_count": 0,
                    "reason": "no_valid_regimes",
                }
            )
            continue
        valid = valid.sort_values("probability", ascending=False)
        top = valid.iloc[0]
        second_probability = float(valid.iloc[1]["probability"]) if len(valid) > 1 else 0.0
        top_probability = float(top["probability"])
        average_coverage = float(valid["coverage_ratio"].mean())
        confidence = (top_probability - second_probability) * average_coverage
        entropy = _entropy(valid["probability"].astype(float).tolist())
        rows.append(
            {
                "date": pd.Timestamp(date).date(),
                "valid": True,
                "dominant_regime": top["regime_id"],
                "dominant_probability": top_probability,
                "confidence": confidence,
                "entropy": entropy,
                "valid_regime_count": int(len(valid)),
                "reason": "ok",
            }
        )
    return pd.DataFrame(rows, columns=_health_columns())


def _softmax(scores: dict[str, float], temperature: float) -> dict[str, float]:
    max_score = max(scores.values())
    exp_scores = {
        name: math.exp((score - max_score) / temperature)
        for name, score in scores.items()
    }
    total = sum(exp_scores.values())
    return {name: value / total for name, value in exp_scores.items()}


def _entropy(probabilities: list[float]) -> float:
    return float(-sum(probability * math.log(probability) for probability in probabilities if probability > 0))


def _contribution_row(
    regime_id: str,
    dimension_id: str,
    date: pd.Timestamp,
    dimension_score: float | None,
    weight: float,
    normalized_weight: float,
    polarity: str,
    transformed_dimension_value: float | None,
    contribution: float,
    valid: bool,
    reason: str,
) -> dict:
    return {
        "regime_id": regime_id,
        "dimension_id": dimension_id,
        "date": pd.Timestamp(date).date(),
        "dimension_score": dimension_score,
        "weight": float(weight),
        "normalized_weight": float(normalized_weight),
        "polarity": polarity,
        "transformed_dimension_value": transformed_dimension_value,
        "contribution": float(contribution),
        "valid": valid,
        "reason": reason,
    }


def _contribution_columns() -> list[str]:
    return [
        "regime_id",
        "dimension_id",
        "date",
        "dimension_score",
        "weight",
        "normalized_weight",
        "polarity",
        "transformed_dimension_value",
        "contribution",
        "valid",
        "reason",
    ]


def _score_columns() -> list[str]:
    return [
        "regime_id",
        "date",
        "raw_score",
        "probability",
        "rank",
        "valid_dimension_count",
        "configured_dimension_count",
        "coverage_ratio",
        "valid",
        "reason",
    ]


def _health_columns() -> list[str]:
    return [
        "date",
        "valid",
        "dominant_regime",
        "dominant_probability",
        "confidence",
        "entropy",
        "valid_regime_count",
        "reason",
    ]
