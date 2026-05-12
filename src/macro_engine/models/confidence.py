from __future__ import annotations

import pandas as pd

from macro_engine.config.schemas import ModelConfig


def calculate_overall_confidence(
    dimension_scores: pd.DataFrame,
    regime_probabilities: dict[str, float],
    model_config: ModelConfig,
) -> float:
    required = dimension_scores[dimension_scores["required_for_regime"]]
    data_completeness = float(required["data_completeness"].mean()) if not required.empty else 0.0
    data_freshness = float(required["freshness_score"].mean()) if not required.empty else 0.0
    signal_agreement = float(required["score"].abs().mean()) if not required.empty else 0.0
    ranked = sorted(regime_probabilities.values(), reverse=True)
    gap = ranked[0] - ranked[1] if len(ranked) > 1 else ranked[0]
    regime_decisiveness = min(1.0, gap / 0.35)
    weights = model_config.confidence.weights
    confidence = (
        weights["data_completeness"] * data_completeness
        + weights["data_freshness"] * data_freshness
        + weights["signal_agreement"] * signal_agreement
        + weights["regime_decisiveness"] * regime_decisiveness
    )
    return max(0.0, min(1.0, confidence))
