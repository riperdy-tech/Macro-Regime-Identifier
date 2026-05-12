from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from macro_engine.config.schemas import ModelConfig, SourceHealthReport


def build_output_payload(
    *,
    as_of: str,
    model_config: ModelConfig,
    regime_result: dict,
    confidence: float,
    dimension_scores: pd.DataFrame,
    top_drivers: list[str],
    watchlist: list[str],
    source_health: SourceHealthReport,
) -> dict:
    return {
        "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": model_config.version,
        "historical_mode": model_config.historical_mode,
        "primary_regime": regime_result["primary_regime"],
        "secondary_regime": regime_result["secondary_regime"],
        "transition_zone": regime_result["transition_zone"],
        "confidence": confidence,
        "regime_probabilities": regime_result["regime_probabilities"],
        "dimension_scores": {
            row["dimension"]: {
                "score": row["score"],
                "confidence": row["confidence"],
                "dimension_type": row["dimension_type"],
                "top_features": row["top_features"],
            }
            for row in dimension_scores.to_dict(orient="records")
        },
        "top_drivers": top_drivers,
        "watchlist": watchlist,
        "source_health": source_health.model_dump(),
        "disclaimer": (
            "This is an experimental macro regime classification based on structured "
            "macroeconomic and market-implied data. It is not investment advice."
        ),
    }


def write_json(payload: dict, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
