"""Sector exposure guardrails: anomaly flags only, never score overrides."""

from __future__ import annotations

import pandas as pd

from macro_engine.news.guardrails import (
    SectorGuardrailConfig,
    check_sector_guardrails,
    load_sector_guardrail_config,
)


def _config():
    return SectorGuardrailConfig.model_validate(
        {
            "min_confidence_to_flag": 0.6,
            "expectations": [
                {"theme_id": "energy_supply_shock", "sector_id": "energy", "expected": "positive"},
                {"theme_id": "energy_supply_shock", "sector_id": "consumer_discretionary",
                 "expected": "negative"},
            ],
        }
    )


def _themes(direction="positive"):
    return pd.DataFrame(
        [{"news_id": "n1", "theme_id": "energy_supply_shock", "direction": direction,
          "severity": 0.8, "confidence": 0.9, "time_horizon": "short_term"}]
    )


def _impact(direction, confidence=0.9, sector="energy"):
    return pd.DataFrame(
        [{"news_id": "n1", "sector_id": sector, "impact_direction": direction,
          "impact_score": 0.5, "confidence": confidence, "rationale": ""}]
    )


def test_flags_contradicting_call():
    # supply shock positive -> energy expected tailwind; LLM says headwind -> flag.
    out = check_sector_guardrails(_themes(), _impact("headwind"), _config())
    assert len(out) == 1
    assert out.iloc[0]["expected_direction"] == "tailwind"
    assert out.iloc[0]["impact_direction"] == "headwind"


def test_consistent_call_not_flagged():
    out = check_sector_guardrails(_themes(), _impact("tailwind"), _config())
    assert out.empty


def test_theme_negative_flips_expectation():
    # supply shock NEGATIVE -> energy expected headwind; LLM tailwind -> flag.
    out = check_sector_guardrails(_themes("negative"), _impact("tailwind"), _config())
    assert len(out) == 1
    assert out.iloc[0]["expected_direction"] == "headwind"


def test_low_confidence_not_flagged():
    out = check_sector_guardrails(_themes(), _impact("headwind", confidence=0.4), _config())
    assert out.empty


def test_unmapped_pair_ignored():
    out = check_sector_guardrails(_themes(), _impact("headwind", sector="utilities"), _config())
    assert out.empty


def test_neutral_impact_ignored():
    out = check_sector_guardrails(_themes(), _impact("neutral"), _config())
    assert out.empty


def test_empty_inputs_safe():
    cfg = _config()
    assert check_sector_guardrails(pd.DataFrame(), _impact("headwind"), cfg).empty
    assert check_sector_guardrails(_themes(), pd.DataFrame(), cfg).empty


def test_shipped_config_loads_and_is_directional():
    cfg = load_sector_guardrail_config("config/sector_exposure_guardrails.yaml")
    assert cfg.expectations
    assert 0.0 <= cfg.min_confidence_to_flag <= 1.0
    # every expectation maps to a +/-1 sign
    assert set(cfg.expectation_map().values()) <= {1.0, -1.0}
