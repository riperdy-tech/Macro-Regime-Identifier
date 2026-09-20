"""Capital-market anchors: long-run nominal growth.

It is tempting to treat this anchor as a constant with extra fields. The point of these
tests is the arithmetic and the honesty: the components must sum to the trend, the
regime adjustment must actually move the answer, the clamp must bind, and an absent
input must produce null rather than the constant it was meant to replace.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from macro_engine.anchors.config import load_anchor_config
from macro_engine.anchors.growth import (
    build_long_run_growth_anchor,
    current_regime_label,
    log_linear_trend_annualized,
)

AS_OF = pd.Timestamp("2026-04-30")


def _potential_output(rate: float, years: int = 20, start: str = "2006-01-01") -> pd.DataFrame:
    """A potential-output series growing at exactly `rate` per year."""
    dates = pd.date_range(start, periods=years * 4, freq="QS")
    values = [100.0 * math.exp(rate * (index / 4)) for index in range(len(dates))]
    return pd.DataFrame({"date": dates, "value": values})


def _observations(series: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for series_id, frame in series.items():
        part = frame.copy()
        part["series_id"] = series_id
        frames.append(part)
    frame = pd.concat(frames, ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def test_log_linear_trend_recovers_a_known_growth_rate():
    frame = _potential_output(0.02)
    rate, date, count = log_linear_trend_annualized(
        frame["value"], frame["date"], window_years=10, min_observations=12, as_of=AS_OF
    )
    assert rate == pytest.approx(0.02, abs=1e-6)
    # ~10 years of quarterly observations inside the window.
    assert 38 <= count <= 42
    assert date == "2025-10-01"


def test_log_linear_trend_refuses_to_fit_a_slope_to_too_few_points():
    """A trend needs a trend's worth of data; a short window is not a trend."""
    frame = _potential_output(0.02).head(4)
    rate, date, count = log_linear_trend_annualized(
        frame["value"], frame["date"], window_years=10, min_observations=12, as_of=AS_OF
    )
    assert rate is None
    assert date is None
    assert count == 4


def test_trend_is_not_dominated_by_endpoint_noise():
    """OLS on logs, not an endpoint CAGR: one bad quarter must not define the trend.

    The comparison is the point, and it is made over the SAME window: within the
    fitted span a single 30% spike in the final quarter moves the endpoint CAGR by
    ~2.7pp while the fitted log trend moves by well under 0.5pp.
    """
    frame = _potential_output(0.02)
    noised = frame.copy()
    noised.loc[noised.index[-1], "value"] = float(noised["value"].iloc[-1]) * 1.30

    base, _, _ = log_linear_trend_annualized(
        frame["value"], frame["date"], window_years=10, min_observations=12, as_of=AS_OF
    )
    shifted, _, _ = log_linear_trend_annualized(
        noised["value"], noised["date"], window_years=10, min_observations=12, as_of=AS_OF
    )

    in_window = frame[frame["date"] >= AS_OF - pd.DateOffset(years=10)]
    noised_window = noised[noised["date"] >= AS_OF - pd.DateOffset(years=10)]
    span_years = (
        pd.Timestamp(in_window["date"].iloc[-1]) - pd.Timestamp(in_window["date"].iloc[0])
    ).days / 365.25
    endpoint_base = (
        float(in_window["value"].iloc[-1]) / float(in_window["value"].iloc[0])
    ) ** (1 / span_years) - 1
    endpoint_shifted = (
        float(noised_window["value"].iloc[-1]) / float(noised_window["value"].iloc[0])
    ) ** (1 / span_years) - 1

    assert abs(endpoint_shifted - endpoint_base) > 0.02
    assert abs(shifted - base) < 0.006
    assert abs(shifted - base) < abs(endpoint_shifted - endpoint_base) / 4


def test_components_sum_to_the_nominal_trend():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": pd.DataFrame({"date": [pd.Timestamp("2026-04-29")], "value": [2.30]}),
        }
    )
    anchor = build_long_run_growth_anchor(
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    assert anchor.components["real_potential"] == pytest.approx(0.018, abs=1e-6)
    # The percent-quoted forward rate must arrive as a decimal fraction.
    assert anchor.components["inflation_expectation"] == pytest.approx(0.023)
    assert anchor.nominal_gdp_trend == pytest.approx(
        anchor.components["real_potential"] + anchor.components["inflation_expectation"]
    )
    assert anchor.degraded is False


def test_terminal_suggestion_is_the_share_capped_trend_when_the_regime_is_neutral():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": pd.DataFrame({"date": [pd.Timestamp("2026-04-29")], "value": [2.30]}),
        }
    )
    anchor = build_long_run_growth_anchor(
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        regime_label="goldilocks",
    )
    # goldilocks sensitivity is 0.0, so the suggestion is the capped, rounded trend.
    assert anchor.regime_adjustment == pytest.approx(0.0)
    assert anchor.raw_trend_g == pytest.approx(anchor.nominal_gdp_trend * 0.85, abs=1e-9)
    assert anchor.terminal_g_suggestion == pytest.approx(
        round((anchor.nominal_gdp_trend * 0.85) / 0.0025) * 0.0025, abs=1e-9
    )


def test_regime_sensitivity_moves_the_suggestion_in_the_configured_direction():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": pd.DataFrame({"date": [pd.Timestamp("2026-04-29")], "value": [2.30]}),
        }
    )
    config = load_anchor_config("config/anchors.yaml")
    goldilocks = build_long_run_growth_anchor(
        observations=observations, config=config, as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00", regime_label="goldilocks",
    )
    recession = build_long_run_growth_anchor(
        observations=observations, config=config, as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00", regime_label="recession",
    )
    stagflation = build_long_run_growth_anchor(
        observations=observations, config=config, as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00", regime_label="stagflation",
    )
    assert recession.terminal_g_suggestion < goldilocks.terminal_g_suggestion
    assert stagflation.terminal_g_suggestion < goldilocks.terminal_g_suggestion
    assert recession.regime_applied == "recession"
    assert recession.regime_adjustment == pytest.approx(-0.0125)
    assert recession.provenance.notes  # provenance still travels with the number


def test_unknown_regime_applies_no_adjustment_and_says_so():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": pd.DataFrame({"date": [pd.Timestamp("2026-04-29")], "value": [2.30]}),
        }
    )
    anchor = build_long_run_growth_anchor(
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        regime_label="not_a_regime",
    )
    assert anchor.regime_adjustment == pytest.approx(0.0)
    assert any(
        "no entry for regime" in reason
        for reason in anchor.provenance.degradation_reasons
    )


def test_ceiling_clamp_binds_on_an_implausibly_high_trend():
    """A perpetuity growth rate can never run away from the economy."""
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.15),
            "T5YIFR": pd.DataFrame({"date": [pd.Timestamp("2026-04-29")], "value": [9.0]}),
        }
    )
    anchor = build_long_run_growth_anchor(
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        regime_label="reflation",
    )
    assert anchor.nominal_gdp_trend > 0.20
    assert anchor.terminal_g_suggestion == pytest.approx(0.05)
    assert anchor.clamp["ceiling"] == pytest.approx(0.05)


def test_floor_clamp_binds_on_a_deeply_negative_regime_adjustment():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.002),
            "T5YIFR": pd.DataFrame({"date": [pd.Timestamp("2026-04-29")], "value": [0.20]}),
        }
    )
    anchor = build_long_run_growth_anchor(
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        regime_label="recession",
    )
    assert anchor.terminal_g_suggestion is not None
    assert anchor.terminal_g_suggestion >= anchor.clamp["floor"]


def test_delta_is_disclosed_against_the_constant_it_replaces():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.021),
            "T5YIFR": pd.DataFrame({"date": [pd.Timestamp("2026-04-29")], "value": [2.40]}),
        }
    )
    anchor = build_long_run_growth_anchor(
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        regime_label="goldilocks",
    )
    assert anchor.downstream_prior_in_use == pytest.approx(0.025)
    assert anchor.delta == pytest.approx(anchor.terminal_g_suggestion - 0.025)


def test_missing_potential_output_degrades_to_null_not_to_the_old_constant():
    """The replacement constant must not silently become the constant it replaced."""
    observations = _observations(
        {"T5YIFR": pd.DataFrame({"date": [pd.Timestamp("2026-04-29")], "value": [2.30]})}
    )
    anchor = build_long_run_growth_anchor(
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    assert anchor.nominal_gdp_trend is None
    assert anchor.terminal_g_suggestion is None
    assert anchor.delta is None
    assert anchor.degraded is True
    assert anchor.downstream_prior_in_use == pytest.approx(0.025)
    assert any("GDPPOT" in reason for reason in anchor.provenance.degradation_reasons)


def test_inflation_expectation_falls_back_to_the_next_candidate():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            # T5YIFR absent -> the 10y breakeven must be used and named.
            "T10YIE": pd.DataFrame({"date": [pd.Timestamp("2026-04-29")], "value": [2.55]}),
        }
    )
    anchor = build_long_run_growth_anchor(
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    assert anchor.components["inflation_expectation"] == pytest.approx(0.0255)
    assert any("T10YIE" in note for note in anchor.provenance.notes)


def test_regime_label_comes_from_the_stored_timeline_not_the_snapshot():
    timeline = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-02-01", "2026-03-01", "2026-04-01"]),
            "reported_regime": ["recession", "recession", "tightening"],
            "dominant_regime": ["recession", "tightening", "tightening"],
        }
    )
    assert current_regime_label(timeline, pd.Timestamp("2026-03-15")) == "recession"
    assert current_regime_label(timeline, pd.Timestamp("2026-04-15")) == "tightening"
    # Nothing at or before the as-of date -> no label, never a future one.
    assert current_regime_label(timeline, pd.Timestamp("2026-01-01")) is None
