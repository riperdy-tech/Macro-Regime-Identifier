from __future__ import annotations

import pandas as pd

from macro_engine.diagnostics.config import HistoricalDiagnosticConfig
from macro_engine.diagnostics.runner import run_historical_diagnostic
from macro_engine.regimes.config import load_regime_config
from macro_engine.reports.config import ReportConfig
from macro_engine.reports.writer import build_current_regime_report, current_report_markdown
from tests.test_phase_g_reports import (
    _dimension_contributions,
    _dimension_scores,
    _feature_health,
    _regime_contributions,
    _source_health,
)


def test_production_tightening_formula_matches_phase_r_candidate():
    config = load_regime_config("config/phase_b_sources.yaml")
    tightening = next(regime for regime in config.regimes if regime.regime_id == "tightening")

    assert {dimension.dimension_id: dimension.weight for dimension in tightening.dimensions} == {
        "growth_momentum": 0.18,
        "inflation_pressure": 0.27,
        "credit_liquidity": 0.10,
        "policy_stance": 0.32,
        "yield_curve": 0.13,
    }
    assert {dimension.dimension_id: dimension.polarity for dimension in tightening.dimensions} == {
        "growth_momentum": "positive",
        "inflation_pressure": "positive",
        "credit_liquidity": "negative",
        "policy_stance": "negative",
        "yield_curve": "negative",
    }


def test_reported_state_does_not_switch_below_threshold_but_raw_signal_is_preserved():
    result = run_historical_diagnostic(
        _filter_regime_scores(),
        _filter_regime_health(confidence_on_switch=0.019),
        _filter_config(),
    )
    timeline = result.timeline.set_index("date")
    feb = timeline.loc[pd.Timestamp("2026-02-01").date()]

    assert feb["raw_dominant_regime"] == "tightening"
    assert feb["dominant_regime"] == "goldilocks"
    assert feb["reported_regime"] == "goldilocks"
    assert feb["transition_filter_reason"] == "held_below_min_confidence"
    assert result.transitions.empty


def test_reported_state_switches_at_or_above_threshold():
    result = run_historical_diagnostic(
        _filter_regime_scores(),
        _filter_regime_health(confidence_on_switch=0.020),
        _filter_config(),
    )
    timeline = result.timeline.set_index("date")
    feb = timeline.loc[pd.Timestamp("2026-02-01").date()]

    assert feb["raw_dominant_regime"] == "tightening"
    assert feb["reported_regime"] == "tightening"
    assert feb["transition_filter_reason"] == "switch_confirmed"
    assert len(result.transitions) == 1
    assert result.transitions.iloc[0]["to_regime"] == "tightening"


def test_high_confidence_crisis_transition_is_not_delayed():
    result = run_historical_diagnostic(
        _crisis_regime_scores(),
        _crisis_regime_health(),
        _filter_config(start_date="2020-02-01", end_date="2020-03-01"),
    )
    transition = result.transitions.iloc[0]

    assert transition["transition_date"] == pd.Timestamp("2020-03-01").date()
    assert transition["to_regime"] == "recession"


def test_current_report_includes_raw_and_reported_regime_information():
    regime_scores = _filter_regime_scores()
    regime_health = _filter_regime_health(confidence_on_switch=0.019)
    diagnostic = run_historical_diagnostic(regime_scores, regime_health, _filter_config())

    payload = build_current_regime_report(
        regime_scores=regime_scores,
        regime_health=regime_health,
        regime_contributions=_regime_contributions(),
        dimension_scores=_dimension_scores(),
        dimension_contributions=_dimension_contributions(),
        feature_health=_feature_health(),
        source_health=_source_health(),
        config=ReportConfig(max_contributors=3),
        timeline=diagnostic.timeline,
    )
    markdown = current_report_markdown(payload)

    assert payload["raw_dominant_regime"] == "tightening"
    assert payload["reported_regime"] == "goldilocks"
    assert payload["transition_filter_applied"] is True
    assert "Raw Monthly Signal" in markdown
    assert "Reported regime" in markdown


def test_low_confidence_switch_requires_two_month_confirmation():
    config = _confirmation_config()
    scores = pd.DataFrame(
        [
            _score_row("2026-01-01", "goldilocks", 0.60, 1),
            _score_row("2026-01-01", "tightening", 0.30, 2),
            _score_row("2026-02-01", "tightening", 0.51, 1),
            _score_row("2026-02-01", "goldilocks", 0.49, 2),
            _score_row("2026-03-01", "tightening", 0.52, 1),
            _score_row("2026-03-01", "goldilocks", 0.48, 2),
        ]
    )
    health = pd.DataFrame(
        [
            _health_row("2026-01-01", "goldilocks", 0.60, 0.30),
            _health_row("2026-02-01", "tightening", 0.51, 0.10),
            _health_row("2026-03-01", "tightening", 0.52, 0.10),
        ]
    )
    result = run_historical_diagnostic(scores, health, config)
    timeline = result.timeline.set_index("date")

    feb = timeline.loc[pd.Timestamp("2026-02-01").date()]
    assert feb["reported_regime"] == "goldilocks"
    assert feb["raw_dominant_regime"] == "tightening"
    assert feb["transition_filter_reason"] == "awaiting_confirmation"

    march = timeline.loc[pd.Timestamp("2026-03-01").date()]
    assert march["reported_regime"] == "tightening"
    assert march["transition_filter_reason"] == "switch_confirmed"
    assert len(result.transitions) == 1


def test_high_confidence_switch_stays_immediate_with_confirmation_enabled():
    config = _confirmation_config()
    scores = pd.DataFrame(
        [
            _score_row("2026-01-01", "goldilocks", 0.60, 1),
            _score_row("2026-01-01", "recession", 0.30, 2),
            _score_row("2026-02-01", "recession", 0.91, 1),
            _score_row("2026-02-01", "goldilocks", 0.08, 2),
        ]
    )
    health = pd.DataFrame(
        [
            _health_row("2026-01-01", "goldilocks", 0.60, 0.30),
            _health_row("2026-02-01", "recession", 0.91, 0.83),
        ]
    )
    result = run_historical_diagnostic(scores, health, config)
    feb = result.timeline.set_index("date").loc[pd.Timestamp("2026-02-01").date()]

    assert feb["reported_regime"] == "recession"
    assert feb["transition_filter_reason"] == "switch_confirmed"


def test_pending_confirmation_resets_when_raw_leader_reverts():
    config = _confirmation_config(end_date="2026-04-01")
    scores = pd.DataFrame(
        [
            _score_row("2026-01-01", "goldilocks", 0.60, 1),
            _score_row("2026-01-01", "tightening", 0.30, 2),
            _score_row("2026-02-01", "tightening", 0.51, 1),
            _score_row("2026-02-01", "goldilocks", 0.49, 2),
            _score_row("2026-03-01", "goldilocks", 0.55, 1),
            _score_row("2026-03-01", "tightening", 0.45, 2),
            _score_row("2026-04-01", "tightening", 0.52, 1),
            _score_row("2026-04-01", "goldilocks", 0.48, 2),
        ]
    )
    health = pd.DataFrame(
        [
            _health_row("2026-01-01", "goldilocks", 0.60, 0.30),
            _health_row("2026-02-01", "tightening", 0.51, 0.10),
            _health_row("2026-03-01", "goldilocks", 0.55, 0.12),
            _health_row("2026-04-01", "tightening", 0.52, 0.10),
        ]
    )
    result = run_historical_diagnostic(scores, health, config)
    timeline = result.timeline.set_index("date")

    # The interrupted February attempt must not carry over to April.
    april = timeline.loc[pd.Timestamp("2026-04-01").date()]
    assert april["reported_regime"] == "goldilocks"
    assert april["transition_filter_reason"] == "awaiting_confirmation"
    assert result.transitions.empty


def test_production_config_enables_two_month_confirmation_below_015():
    from macro_engine.diagnostics.config import load_historical_diagnostic_config

    config = load_historical_diagnostic_config("config/phase_b_sources.yaml")
    assert config.transition_filter.confirmation_months == 2
    assert config.transition_filter.only_when_confidence_below == 0.15
    assert config.transition_filter.min_confidence_to_switch == 0.08


def _confirmation_config(
    start_date: str = "2026-01-01",
    end_date: str = "2026-03-01",
) -> HistoricalDiagnosticConfig:
    return HistoricalDiagnosticConfig(
        start_date=start_date,
        end_date=end_date,
        mode="revised_data",
        min_valid_regimes=2,
        low_confidence_threshold=0.05,
        transition_filter={
            "enabled": True,
            "min_confidence_to_switch": 0.02,
            "confirmation_months": 2,
            "only_when_confidence_below": 0.15,
        },
    )


def _filter_config(
    start_date: str = "2026-01-01",
    end_date: str = "2026-02-01",
) -> HistoricalDiagnosticConfig:
    return HistoricalDiagnosticConfig(
        start_date=start_date,
        end_date=end_date,
        mode="revised_data",
        min_valid_regimes=2,
        low_confidence_threshold=0.05,
        transition_filter={"enabled": True, "min_confidence_to_switch": 0.02},
    )


def _filter_regime_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _score_row("2026-01-01", "goldilocks", 0.60, 1),
            _score_row("2026-01-01", "tightening", 0.30, 2),
            _score_row("2026-02-01", "tightening", 0.51, 1),
            _score_row("2026-02-01", "goldilocks", 0.491, 2),
        ]
    )


def _filter_regime_health(confidence_on_switch: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            _health_row("2026-01-01", "goldilocks", 0.60, 0.30),
            _health_row("2026-02-01", "tightening", 0.51, confidence_on_switch),
        ]
    )


def _crisis_regime_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _score_row("2020-02-01", "stagflation", 0.35, 1),
            _score_row("2020-02-01", "recession", 0.33, 2),
            _score_row("2020-03-01", "recession", 0.91, 1),
            _score_row("2020-03-01", "stagflation", 0.08, 2),
        ]
    )


def _crisis_regime_health() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _health_row("2020-02-01", "stagflation", 0.35, 0.02),
            _health_row("2020-03-01", "recession", 0.91, 0.83),
        ]
    )


def _score_row(date: str, regime_id: str, probability: float, rank: int) -> dict:
    return {
        "regime_id": regime_id,
        "date": date,
        "raw_score": probability,
        "probability": probability,
        "rank": rank,
        "valid_dimension_count": 5,
        "configured_dimension_count": 5,
        "coverage_ratio": 1.0,
        "valid": True,
        "reason": "ok",
    }


def _health_row(
    date: str,
    dominant_regime: str,
    dominant_probability: float,
    confidence: float,
) -> dict:
    return {
        "date": date,
        "valid": True,
        "dominant_regime": dominant_regime,
        "dominant_probability": dominant_probability,
        "confidence": confidence,
        "entropy": 0.9,
        "valid_regime_count": 2,
        "reason": "ok",
    }
