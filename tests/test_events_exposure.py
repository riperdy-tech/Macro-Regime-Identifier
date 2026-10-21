"""E4 sector exposure, MRI side (plan v2 §8): the bar, and guardrail contradictions carried as flags only."""
from __future__ import annotations

from macro_engine.events.exposure import sector_exposure


def test_only_impacts_clearing_both_bars_are_exposure():
    impacts = [
        {"sector_id": "energy", "impact_score": 0.7, "confidence": 0.8, "impact_direction": "tailwind"},
        {"sector_id": "utilities", "impact_score": 0.7, "confidence": 0.5, "impact_direction": "headwind"},   # low confidence
        {"sector_id": "materials", "impact_score": 0.2, "confidence": 0.9, "impact_direction": "tailwind"},   # small impact
        {"sector_id": "industrials", "impact_score": -0.3, "confidence": 0.6, "impact_direction": "headwind"},  # on the bar
        {"sector_id": "", "impact_score": 0.9, "confidence": 0.9},
        {"sector_id": "financials", "impact_score": None, "confidence": 0.9},
    ]
    out = sector_exposure(impacts, {("energy_supply_shock", "positive")})
    assert [e["sector_id"] for e in out] == ["energy", "industrials"]
    assert all(e["flags"] == [] for e in out)   # both agree with the guardrail matrix


def test_contradiction_is_a_flag_not_a_filter():
    impacts = [{"sector_id": "energy", "impact_score": -0.6, "confidence": 0.9, "impact_direction": "headwind"}]
    out = sector_exposure(impacts, {("energy_supply_shock", "positive")})
    assert out[0]["sector_id"] == "energy"
    assert out[0]["flags"] == ["guardrail_contradiction:energy_supply_shock"]
    flipped = sector_exposure(impacts, {("energy_supply_shock", "negative")})   # the expectation flips with the theme
    assert flipped[0]["flags"] == []
