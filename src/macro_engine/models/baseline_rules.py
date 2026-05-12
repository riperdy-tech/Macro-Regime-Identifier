from __future__ import annotations

import math

import pandas as pd

from macro_engine.config.schemas import ModelConfig, RegimeConfig, RegimeDimensionRule


def apply_scoring_rule(score: float, rule: RegimeDimensionRule) -> float:
    if rule.mode == "linear":
        return score * rule.weight
    if rule.mode == "reward_positive":
        return max(score, 0.0) * rule.weight
    if rule.mode == "reward_negative":
        return max(-score, 0.0) * rule.weight
    if rule.mode == "penalize_positive":
        return -max(score, 0.0) * rule.weight
    if rule.mode == "penalize_negative":
        return -max(-score, 0.0) * rule.weight
    if rule.mode == "neutral_band":
        target = rule.target or 0.0
        return -abs(score - target) * rule.weight
    raise ValueError(f"unsupported scoring mode {rule.mode}")


def score_regimes(
    dimension_scores: pd.DataFrame,
    regimes: dict[str, RegimeConfig],
    model_config: ModelConfig,
) -> dict:
    score_map = {
        row["dimension"]: float(row["score"])
        for row in dimension_scores[dimension_scores["required_for_regime"]].to_dict(orient="records")
    }
    regime_scores: dict[str, float] = {}
    contributions: dict[str, dict[str, float]] = {}

    for regime_name, regime in regimes.items():
        regime_total = 0.0
        regime_contributions: dict[str, float] = {}
        for dimension, rule in regime.scoring.items():
            contribution = apply_scoring_rule(score_map.get(dimension, 0.0), rule)
            regime_contributions[dimension] = contribution
            regime_total += contribution
        regime_scores[regime_name] = regime_total
        contributions[regime_name] = regime_contributions

    probabilities = _softmax(regime_scores, model_config.classification.softmax_temperature)
    ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    primary, top_probability = ranked[0]
    secondary, second_probability = ranked[1]
    transition_zone = (
        top_probability < model_config.classification.low_conviction_threshold
        or top_probability - second_probability
        < model_config.classification.transition_zone_probability_gap
    )
    return {
        "regime_scores": regime_scores,
        "regime_probabilities": probabilities,
        "regime_contributions": contributions,
        "primary_regime": primary,
        "secondary_regime": secondary,
        "transition_zone": transition_zone,
    }


def extract_top_drivers(
    primary_regime: str,
    dimension_scores: pd.DataFrame,
    regimes: dict[str, RegimeConfig],
    contributions: dict[str, dict[str, float]],
    limit: int = 4,
) -> list[str]:
    dimension_map = {
        row["dimension"]: row for row in dimension_scores.to_dict(orient="records")
    }
    ranked = sorted(
        contributions[primary_regime].items(),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    drivers: list[str] = []
    for dimension, contribution in ranked:
        if len(drivers) >= limit:
            break
        row = dimension_map.get(dimension)
        if not row:
            continue
        label = dimension.replace("_", " ")
        score = float(row["score"])
        plural = label.endswith("s")
        verb = "are" if plural else "is"
        support_verb = "support" if plural else "supports"
        weigh_verb = "weigh" if plural else "weighs"
        if contribution >= 0 and score >= 0:
            drivers.append(f"{label.capitalize()} {verb} positive and {support_verb} {primary_regime}.")
        elif contribution >= 0 and score < 0:
            drivers.append(f"{label.capitalize()} {verb} negative and {support_verb} {primary_regime}.")
        elif score >= 0:
            drivers.append(f"{label.capitalize()} {verb} positive but {weigh_verb} against {primary_regime}.")
        else:
            drivers.append(f"{label.capitalize()} {verb} negative and {weigh_verb} against {primary_regime}.")

    templates = regimes[primary_regime].driver_templates.positive
    if templates:
        merged = list(dict.fromkeys([*templates, *drivers]))
        return merged[:limit]
    return drivers


def _softmax(scores: dict[str, float], temperature: float) -> dict[str, float]:
    max_score = max(scores.values())
    exp_scores = {
        name: math.exp((score - max_score) / temperature)
        for name, score in scores.items()
    }
    denominator = sum(exp_scores.values())
    return {name: value / denominator for name, value in exp_scores.items()}
