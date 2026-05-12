from __future__ import annotations

import pandas as pd

from macro_engine.config.schemas import DimensionConfig


def build_dimension_scores(
    feature_values: pd.DataFrame,
    dimensions: dict[str, DimensionConfig],
    as_of: str,
) -> pd.DataFrame:
    rows: list[dict] = []
    for dimension_name, dimension in sorted(
        dimensions.items(), key=lambda item: item[1].display_order
    ):
        features = feature_values[
            (feature_values["dimension"] == dimension_name)
            & (feature_values["enabled"])
            & (feature_values["feature_weight"] > 0)
        ].copy()
        usable = features[features["available"] & (features["freshness_score"] > 0)]
        denominator = float(usable["feature_weight"].abs().sum())
        score = 0.0 if denominator == 0 else float(usable["feature_signal"].sum() / denominator)
        score = max(-1.0, min(1.0, score))

        required = features[features["required"]]
        total_required_weight = float(required["feature_weight"].sum())
        available_required_weight = float(
            required[required["available"] & (required["freshness_score"] > 0)]["feature_weight"].sum()
        )
        data_completeness = (
            1.0 if total_required_weight == 0 else available_required_weight / total_required_weight
        )
        freshness = (
            0.0
            if features.empty
            else float(
                (features["freshness_score"] * features["feature_weight"]).sum()
                / max(features["feature_weight"].sum(), 1e-9)
            )
        )
        confidence = max(0.0, min(1.0, data_completeness * freshness))

        top_features = (
            usable.assign(abs_signal=usable["feature_signal"].abs())
            .sort_values("abs_signal", ascending=False)["feature_id"]
            .head(3)
            .tolist()
        )
        rows.append(
            {
                "as_of": as_of,
                "dimension": dimension_name,
                "dimension_type": dimension.dimension_type,
                "required_for_regime": dimension.required_for_regime,
                "score": score,
                "confidence": confidence,
                "data_completeness": data_completeness,
                "freshness_score": freshness,
                "source_count": int(len(usable)),
                "top_features": top_features,
            }
        )
    return pd.DataFrame(rows)
