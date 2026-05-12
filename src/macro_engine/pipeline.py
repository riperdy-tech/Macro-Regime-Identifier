from __future__ import annotations

from macro_engine.config.schemas import LoadedConfig
from macro_engine.features.dimensions import build_dimension_scores
from macro_engine.features.indicators import build_feature_values
from macro_engine.features.source_health import calculate_source_health
from macro_engine.models.baseline_rules import extract_top_drivers, score_regimes
from macro_engine.models.confidence import calculate_overall_confidence
from macro_engine.outputs.json_writer import build_output_payload
from macro_engine.outputs.report import build_watchlist


def classify_observations(raw_observations, config: LoadedConfig, as_of: str) -> dict:
    feature_values = build_feature_values(raw_observations, config.sources, config.model, as_of)
    source_health = calculate_source_health(feature_values)
    dimension_scores = build_dimension_scores(feature_values, config.dimensions, as_of)
    regime_result = score_regimes(dimension_scores, config.regimes, config.model)
    confidence = calculate_overall_confidence(
        dimension_scores, regime_result["regime_probabilities"], config.model
    )
    top_drivers = extract_top_drivers(
        regime_result["primary_regime"],
        dimension_scores,
        config.regimes,
        regime_result["regime_contributions"],
    )
    dimension_map = {
        row["dimension"]: row["score"] for row in dimension_scores.to_dict(orient="records")
    }
    watchlist = build_watchlist(dimension_map)
    payload = build_output_payload(
        as_of=as_of,
        model_config=config.model,
        regime_result=regime_result,
        confidence=confidence,
        dimension_scores=dimension_scores,
        top_drivers=top_drivers,
        watchlist=watchlist,
        source_health=source_health,
    )
    return {
        "payload": payload,
        "feature_values": feature_values,
        "dimension_scores": dimension_scores,
        "source_health": source_health,
        "regime_result": regime_result,
    }
