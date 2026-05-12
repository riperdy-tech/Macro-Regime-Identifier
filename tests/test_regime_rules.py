import pandas as pd

from macro_engine.config.loader import load_all_configs
from macro_engine.models.baseline_rules import score_regimes


def _dimension_frame(scores: dict[str, float]) -> pd.DataFrame:
    rows = []
    for dimension, score in scores.items():
        rows.append(
            {
                "as_of": "2026-05-08",
                "dimension": dimension,
                "dimension_type": "core",
                "required_for_regime": True,
                "score": score,
                "confidence": 1.0,
                "data_completeness": 1.0,
                "freshness_score": 1.0,
                "source_count": 1,
                "top_features": [],
            }
        )
    return pd.DataFrame(rows)


def test_high_inflation_weak_growth_favors_inflationary_slowdown():
    config = load_all_configs("config")
    frame = _dimension_frame(
        {
            "inflation_pressure": 0.8,
            "growth_momentum": -0.5,
            "labor_tightness": 0.1,
            "policy_stance": -0.5,
            "financial_conditions": -0.4,
            "market_risk_appetite": -0.2,
        }
    )

    result = score_regimes(frame, config.regimes, config.model)

    assert result["primary_regime"] == "inflationary_slowdown"


def test_easy_policy_low_inflation_recovery_setup():
    config = load_all_configs("config")
    frame = _dimension_frame(
        {
            "inflation_pressure": -0.5,
            "growth_momentum": 0.2,
            "labor_tightness": -0.1,
            "policy_stance": 0.7,
            "financial_conditions": 0.5,
            "market_risk_appetite": 0.5,
        }
    )

    result = score_regimes(frame, config.regimes, config.model)

    assert result["primary_regime"] == "policy_easing_recovery"
