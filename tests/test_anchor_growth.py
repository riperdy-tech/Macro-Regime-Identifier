"""Capital-market anchors: long-run nominal growth (v0.3).

S2 (P0_0_MRI_TARGET_ARCHITECTURE.md §5.2): the regime LABEL no longer moves this
anchor. The inflation-expectation leg is a trailing 12-month mean of the monthly mean
(not the latest spot observation), and the published rung moves only under a dead-band
+ N-consecutive-monthly-build confirmation rule, with state persisted across builds.
These tests cover the arithmetic and the honesty of both changes: the components must
sum to the trend, the rung must not move on a single noisy month, confirmation must
actually release the rung, and the label must have no code path to any numeric field.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from macro_engine.anchors.config import load_anchor_config
from macro_engine.anchors.growth import (
    advance_rung_state,
    build_long_run_growth_anchor,
    log_linear_trend_annualized,
    trailing_12m_mean_of_monthly_mean,
)

AS_OF = pd.Timestamp("2026-04-30")


def _potential_output(rate: float, years: int = 20, start: str = "2006-01-01") -> pd.DataFrame:
    """A potential-output series growing at exactly `rate` per year."""
    dates = pd.date_range(start, periods=years * 4, freq="QS")
    values = [100.0 * math.exp(rate * (index / 4)) for index in range(len(dates))]
    return pd.DataFrame({"date": dates, "value": values})


def _flat_monthly_series(value: float, as_of: pd.Timestamp, months: int = 24) -> pd.DataFrame:
    """A daily-ish series holding one constant value for `months` trailing months."""
    dates = pd.date_range(end=as_of, periods=months * 21, freq="B")
    return pd.DataFrame({"date": dates, "value": value})


def _observations(series: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for series_id, frame in series.items():
        part = frame.copy()
        part["series_id"] = series_id
        frames.append(part)
    frame = pd.concat(frames, ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def _build(observations, *, prior_rung_state=None, as_of=AS_OF):
    return build_long_run_growth_anchor(
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
        as_of=as_of,
        built_at="2026-04-30T00:00:00+00:00",
        prior_rung_state=prior_rung_state,
    )


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


# ── Trailing 12-month mean (§5.2 smoothing) ─────────────────────────────────────────


def test_trailing_12m_mean_averages_across_calendar_months_not_observations():
    """A month with more trading days must not outweigh a thinner one."""
    dates = pd.date_range("2025-05-01", "2026-04-30", freq="B")
    # Every May..March value is 2.00; every April (the month with fewest trading days in
    # this exact window) value is 3.00 -- if days were weighted directly the mean would
    # tilt toward whichever month has the most business days, not toward one-value-per-month.
    values = [3.00 if d.month == 4 and d.year == 2026 else 2.00 for d in dates]
    frame = pd.DataFrame({"date": dates, "value": values})
    value, month_end, n_months = trailing_12m_mean_of_monthly_mean(
        frame["value"], frame["date"], as_of=pd.Timestamp("2026-04-30"), min_months=12
    )
    assert n_months == 12
    # 11 months at 2.00, 1 month at 3.00 -> mean 2.0833...
    assert value == pytest.approx((11 * 2.00 + 1 * 3.00) / 12, abs=1e-9)
    assert month_end == "2026-04-30"


def test_trailing_12m_mean_is_none_with_fewer_than_the_required_months():
    dates = pd.date_range("2026-01-01", "2026-03-31", freq="B")
    frame = pd.DataFrame({"date": dates, "value": 2.0})
    value, month_end, n_months = trailing_12m_mean_of_monthly_mean(
        frame["value"], frame["date"], as_of=pd.Timestamp("2026-03-31"), min_months=12
    )
    assert value is None
    assert month_end is None
    assert n_months == 3


def test_trailing_12m_mean_never_uses_a_month_after_as_of():
    dates = pd.date_range("2025-01-01", "2026-12-31", freq="B")
    values = [5.0 if d <= pd.Timestamp("2026-04-30") else 999.0 for d in dates]
    frame = pd.DataFrame({"date": dates, "value": values})
    value, month_end, _ = trailing_12m_mean_of_monthly_mean(
        frame["value"], frame["date"], as_of=pd.Timestamp("2026-04-30"), min_months=12
    )
    assert value == pytest.approx(5.0)
    assert month_end == "2026-04-30"


# ── Rung dead-band + confirmation (specification C vs E, §5.1/§5.2) ────────────────


def test_first_ever_build_seeds_the_rung_without_confirmation():
    state = advance_rung_state(
        raw_trend_g_clamped=0.0391, as_of=pd.Timestamp("2026-04-30"),
        prior_state=None, round_to=0.0025, confirm_months=3,
    )
    assert state["current_rung"] == pytest.approx(0.0400)
    assert state["candidate_rung"] == pytest.approx(0.0400)
    assert state["months_confirmed"] == 0
    assert state["last_change_date"] is None
    assert state["changes_last_10y"] == 0


def test_rung_does_not_move_inside_the_dead_band():
    """Raw drifting within one rung's width of the current rung changes nothing."""
    state = {"current_rung": 0.0350, "candidate_rung": 0.0350, "months_confirmed": 0,
              "last_change_date": None, "last_evaluated_month": "2026-03", "changes_last_10y": 0,
              "change_history": []}
    moved = advance_rung_state(
        raw_trend_g_clamped=0.0360, as_of=pd.Timestamp("2026-04-30"),
        prior_state=state, round_to=0.0025, confirm_months=3,
    )
    assert moved["current_rung"] == pytest.approx(0.0350)
    assert moved["months_confirmed"] == 0


def test_specification_c_moves_immediately_once_the_dead_band_clears_one_month_at_a_time():
    """confirm_months=1 is specification C: 25 bp per departure, no waiting."""
    state = {"current_rung": 0.0450, "candidate_rung": 0.0450, "months_confirmed": 0,
              "last_change_date": None, "last_evaluated_month": "2026-01", "changes_last_10y": 0,
              "change_history": []}
    state = advance_rung_state(
        raw_trend_g_clamped=0.0420, as_of=pd.Timestamp("2026-02-28"),
        prior_state=state, round_to=0.0025, confirm_months=1,
    )
    assert state["current_rung"] == pytest.approx(0.0425)
    state = advance_rung_state(
        raw_trend_g_clamped=0.0390, as_of=pd.Timestamp("2026-03-31"),
        prior_state=state, round_to=0.0025, confirm_months=1,
    )
    assert state["current_rung"] == pytest.approx(0.0400)
    assert state["changes_last_10y"] == 2


def test_specification_e_waits_for_three_consecutive_departures_then_can_release_multiple_rungs():
    """confirm_months=3: a long hold during a fast move releases several rungs at once,
    the E-vs-C distinction the architecture measured (2009-06, +75 bp after a hold)."""
    state = {"current_rung": 0.0450, "candidate_rung": 0.0450, "months_confirmed": 0,
              "last_change_date": None, "last_evaluated_month": "2025-12", "changes_last_10y": 0,
              "change_history": []}
    # Month 1 of departure: raw already three rungs below current, but nothing moves yet.
    state = advance_rung_state(
        raw_trend_g_clamped=0.0375, as_of=pd.Timestamp("2026-01-31"),
        prior_state=state, round_to=0.0025, confirm_months=3,
    )
    assert state["current_rung"] == pytest.approx(0.0450)
    assert state["months_confirmed"] == 1
    # Month 2: still held.
    state = advance_rung_state(
        raw_trend_g_clamped=0.0372, as_of=pd.Timestamp("2026-02-28"),
        prior_state=state, round_to=0.0025, confirm_months=3,
    )
    assert state["current_rung"] == pytest.approx(0.0450)
    assert state["months_confirmed"] == 2
    # Month 3: confirmed -- releases straight to the LATEST candidate, not one rung at a time.
    state = advance_rung_state(
        raw_trend_g_clamped=0.0370, as_of=pd.Timestamp("2026-03-31"),
        prior_state=state, round_to=0.0025, confirm_months=3,
    )
    assert state["current_rung"] == pytest.approx(0.0375)
    assert state["months_confirmed"] == 0
    assert state["last_change_date"] == "2026-03-01"
    assert state["changes_last_10y"] == 1


def test_a_departure_that_reverts_before_confirming_resets_the_counter():
    state = {"current_rung": 0.0450, "candidate_rung": 0.0450, "months_confirmed": 0,
              "last_change_date": None, "last_evaluated_month": "2025-12", "changes_last_10y": 0,
              "change_history": []}
    state = advance_rung_state(
        raw_trend_g_clamped=0.0410, as_of=pd.Timestamp("2026-01-31"),
        prior_state=state, round_to=0.0025, confirm_months=3,
    )
    assert state["months_confirmed"] == 1
    # Back inside the dead band the next month: no confirmation credit survives.
    state = advance_rung_state(
        raw_trend_g_clamped=0.0455, as_of=pd.Timestamp("2026-02-28"),
        prior_state=state, round_to=0.0025, confirm_months=3,
    )
    assert state["current_rung"] == pytest.approx(0.0450)
    assert state["months_confirmed"] == 0


def test_candidate_change_during_departure_resets_the_counter_under_candidate_rule():
    """Specification E: candidate confirmation resets when candidate changes."""
    state = {"current_rung": 0.0450, "candidate_rung": 0.0450, "months_confirmed": 0,
              "last_change_date": None, "last_evaluated_month": "2025-12", "changes_last_10y": 0,
              "change_history": []}
    # Month 1: raw 0.0402 -> cand 0.0400
    s1 = advance_rung_state(
        raw_trend_g_clamped=0.0402, as_of=pd.Timestamp("2026-01-01"),
        prior_state=state, round_to=0.0025, confirm_months=3, rule="candidate",
    )
    assert s1["months_confirmed"] == 1
    assert s1["candidate_rung"] == pytest.approx(0.0400)

    # Month 2: raw drops further to 0.0374 -> cand becomes 0.0375
    s2 = advance_rung_state(
        raw_trend_g_clamped=0.0374, as_of=pd.Timestamp("2026-02-01"),
        prior_state=s1, round_to=0.0025, confirm_months=3, rule="candidate",
    )
    # Counter must reset to 1 because candidate changed!
    assert s2["months_confirmed"] == 1
    assert s2["candidate_rung"] == pytest.approx(0.0375)
    assert s2["current_rung"] == pytest.approx(0.0450)

    # Month 3: raw 0.0374 -> cand holds 0.0375
    s3 = advance_rung_state(
        raw_trend_g_clamped=0.0374, as_of=pd.Timestamp("2026-03-01"),
        prior_state=s2, round_to=0.0025, confirm_months=3, rule="candidate",
    )
    assert s3["months_confirmed"] == 2
    assert s3["current_rung"] == pytest.approx(0.0450)

    # Month 4: raw 0.0374 -> cand holds 0.0375 for 3rd month -> CONFIRMS
    s4 = advance_rung_state(
        raw_trend_g_clamped=0.0374, as_of=pd.Timestamp("2026-04-01"),
        prior_state=s3, round_to=0.0025, confirm_months=3, rule="candidate",
    )
    assert s4["months_confirmed"] == 0
    assert s4["current_rung"] == pytest.approx(0.0375)
    assert s4["last_change_date"] == "2026-04-01"


def test_departure_rule_advances_count_even_when_candidate_changes():
    """Alternative 'departure' rule: counts departures from current rung regardless of candidate."""
    state = {"current_rung": 0.0450, "candidate_rung": 0.0450, "months_confirmed": 0,
              "last_change_date": None, "last_evaluated_month": "2025-12", "changes_last_10y": 0,
              "change_history": []}
    s1 = advance_rung_state(
        raw_trend_g_clamped=0.0402, as_of=pd.Timestamp("2026-01-01"),
        prior_state=state, round_to=0.0025, confirm_months=3, rule="departure",
    )
    assert s1["months_confirmed"] == 1
    assert s1["candidate_rung"] == pytest.approx(0.0400)

    s2 = advance_rung_state(
        raw_trend_g_clamped=0.0374, as_of=pd.Timestamp("2026-02-01"),
        prior_state=s1, round_to=0.0025, confirm_months=3, rule="departure",
    )
    # Under departure rule, departure streak continues!
    assert s2["months_confirmed"] == 2
    assert s2["candidate_rung"] == pytest.approx(0.0375)

    s3 = advance_rung_state(
        raw_trend_g_clamped=0.0374, as_of=pd.Timestamp("2026-03-01"),
        prior_state=s2, round_to=0.0025, confirm_months=3, rule="departure",
    )
    # Under departure rule, confirms on month 3!
    assert s3["months_confirmed"] == 0
    assert s3["current_rung"] == pytest.approx(0.0375)
    assert s3["last_change_date"] == "2026-03-01"


def test_a_same_calendar_month_rebuild_does_not_double_count_a_confirmation_month():
    """The daily pipeline may call build-anchors more than once a month; the
    architecture's 'three consecutive monthly builds' means three months, not three
    pipeline runs."""
    state = {"current_rung": 0.0450, "candidate_rung": 0.0450, "months_confirmed": 2,
              "last_change_date": None, "last_evaluated_month": "2026-02", "changes_last_10y": 0,
              "change_history": []}
    same_month = advance_rung_state(
        raw_trend_g_clamped=0.0370, as_of=pd.Timestamp("2026-02-15"),
        prior_state=state, round_to=0.0025, confirm_months=3,
    )
    assert same_month["months_confirmed"] == 2
    assert same_month["current_rung"] == pytest.approx(0.0450)


def test_changes_last_10y_drops_changes_older_than_the_window():
    state = {"current_rung": 0.0350, "candidate_rung": 0.0350, "months_confirmed": 0,
              "last_change_date": "2015-06-01", "last_evaluated_month": "2015-06",
              "changes_last_10y": 1, "change_history": ["2015-06-01"]}
    moved = advance_rung_state(
        raw_trend_g_clamped=0.0300, as_of=pd.Timestamp("2026-04-30"),
        prior_state=state, round_to=0.0025, confirm_months=1,
    )
    # The 2015 change is now >10y old and must not be counted, even though a new one just fired.
    assert moved["changes_last_10y"] == 1
    assert moved["change_history"] == ["2026-04-01"]



# ── build_long_run_growth_anchor: components, clamp, degradation ───────────────────


def test_components_sum_to_the_nominal_trend():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": _flat_monthly_series(2.30, AS_OF),
        }
    )
    anchor = _build(observations)
    assert anchor.components["real_potential"] == pytest.approx(0.018, abs=1e-6)
    # The percent-quoted forward rate must arrive as a decimal fraction.
    assert anchor.components["inflation_expectation"] == pytest.approx(0.023)
    assert anchor.inflation_expectation_spot == pytest.approx(0.023)
    assert anchor.nominal_gdp_trend == pytest.approx(
        anchor.components["real_potential"] + anchor.components["inflation_expectation"]
    )
    assert anchor.degraded is False
    assert anchor.version == "0.3"


def test_terminal_suggestion_is_the_first_rung_on_the_first_build():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": _flat_monthly_series(2.30, AS_OF),
        }
    )
    anchor = _build(observations)
    assert anchor.raw_trend_g == pytest.approx(anchor.nominal_gdp_trend * 0.85, abs=1e-9)
    expected_rung = round((anchor.nominal_gdp_trend * 0.85) / 0.0025) * 0.0025
    assert anchor.terminal_g_suggestion == pytest.approx(expected_rung, abs=1e-9)
    assert anchor.terminal_g_rung == anchor.terminal_g_suggestion
    assert anchor.rung_state["current_rung"] == pytest.approx(expected_rung, abs=1e-9)


def test_ceiling_clamp_binds_on_an_implausibly_high_trend():
    """A perpetuity growth rate can never run away from the economy."""
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.15),
            "T5YIFR": _flat_monthly_series(9.0, AS_OF),
        }
    )
    anchor = _build(observations)
    assert anchor.nominal_gdp_trend > 0.20
    assert anchor.terminal_g_suggestion == pytest.approx(0.05)
    assert anchor.clamp["ceiling"] == pytest.approx(0.05)


def test_floor_clamp_binds_on_a_deeply_low_trend():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.002),
            "T5YIFR": _flat_monthly_series(0.20, AS_OF),
        }
    )
    anchor = _build(observations)
    assert anchor.terminal_g_suggestion is not None
    assert anchor.terminal_g_suggestion >= anchor.clamp["floor"]


def test_delta_is_disclosed_against_the_constant_it_replaces():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.021),
            "T5YIFR": _flat_monthly_series(2.40, AS_OF),
        }
    )
    anchor = _build(observations)
    assert anchor.downstream_prior_in_use == pytest.approx(0.025)
    assert anchor.delta == pytest.approx(anchor.terminal_g_suggestion - 0.025)


def test_missing_potential_output_degrades_to_null_not_to_the_old_constant():
    """The replacement constant must not silently become the constant it replaced."""
    observations = _observations({"T5YIFR": _flat_monthly_series(2.30, AS_OF)})
    anchor = _build(observations)
    assert anchor.nominal_gdp_trend is None
    assert anchor.terminal_g_suggestion is None
    assert anchor.terminal_g_rung is None
    assert anchor.delta is None
    assert anchor.degraded is True
    assert anchor.downstream_prior_in_use == pytest.approx(0.025)
    assert any("GDPPOT" in reason for reason in anchor.provenance.degradation_reasons)


def test_missing_inflation_history_degrades_when_fewer_than_12_months_exist():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": pd.DataFrame(
                {"date": pd.date_range("2026-02-01", "2026-04-29", freq="B"), "value": 2.30}
            ),
        }
    )
    anchor = _build(observations)
    assert anchor.components["inflation_expectation"] is None
    assert anchor.degraded is True
    assert any("T5YIFR" in reason for reason in anchor.provenance.degradation_reasons)


def test_inflation_expectation_falls_back_to_the_next_candidate():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            # T5YIFR absent -> the 10y breakeven must be used and named.
            "T10YIE": _flat_monthly_series(2.55, AS_OF),
        }
    )
    anchor = _build(observations)
    assert anchor.components["inflation_expectation"] == pytest.approx(0.0255)
    assert any("T10YIE" in note for note in anchor.provenance.notes)


# ── The label has no code path to any numeric field (§1.2 rule 5, the review's demand) ──


def test_build_long_run_growth_anchor_has_no_regime_label_parameter():
    """The strongest version of 'the label drives no number': there is no parameter to
    smuggle a label through. See the end-to-end mutation test in
    tests/test_anchor_service.py for the full-pipeline version of this guarantee."""
    import inspect

    params = inspect.signature(build_long_run_growth_anchor).parameters
    assert "regime_label" not in params
    assert "regime" not in params


def test_regime_fields_are_always_empty_and_null_in_v03():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": _flat_monthly_series(2.30, AS_OF),
        }
    )
    anchor = _build(observations)
    assert anchor.regime_sensitivity == {}
    assert anchor.regime_applied is None
    assert anchor.regime_adjustment is None
    assert anchor.provenance.regime_leg == {"used": False, "reason": "label_channel_removed_v0.3"}
    assert any("regime" in d.lower() for d in anchor.deprecations)


def test_rung_state_persists_across_builds_via_prior_rung_state():
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": _flat_monthly_series(2.30, AS_OF),
        }
    )
    first = _build(observations, as_of=pd.Timestamp("2026-02-28"))
    second = _build(observations, prior_rung_state=first.rung_state, as_of=pd.Timestamp("2026-03-31"))
    # Same inputs, no departure -> the rung is carried forward unchanged and the
    # confirmation counter is not reset by the second build.
    assert second.rung_state["current_rung"] == pytest.approx(first.rung_state["current_rung"])
    assert second.terminal_g_suggestion == pytest.approx(first.terminal_g_suggestion)


def test_missed_month_catch_up_equals_consecutive_builds():
    """C2: A multi-month gap between builds catches up through missed months and arrives
    at the exact same state as consecutive monthly builds."""
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": _flat_monthly_series(2.30, AS_OF),
        }
    )
    # Build at 2026-01-31
    b1 = _build(observations, as_of=pd.Timestamp("2026-01-31"))

    # Consecutive builds: 2026-02-28, then 2026-03-31
    b2 = _build(observations, prior_rung_state=b1.rung_state, as_of=pd.Timestamp("2026-02-28"))
    b3_consec = _build(observations, prior_rung_state=b2.rung_state, as_of=pd.Timestamp("2026-03-31"))

    # Gap build: skip February, build 2026-03-31 directly with b1's state
    b3_gap = _build(observations, prior_rung_state=b1.rung_state, as_of=pd.Timestamp("2026-03-31"))

    assert b3_gap.rung_state == b3_consec.rung_state
    assert b3_gap.terminal_g_suggestion == pytest.approx(b3_consec.terminal_g_suggestion)


def test_v02_or_unversioned_prior_triggers_replay():
    """C2: A prior rung state lacking rule='candidate' or method_version=1
    triggers a rebuild rather than blindly accepting an invalid prior."""
    observations = _observations(
        {
            "GDPPOT": _potential_output(0.018),
            "T5YIFR": _flat_monthly_series(2.30, AS_OF),
        }
    )
    # v0.2-style prior without rule or method_version
    v02_prior = {
        "current_rung": 0.0500,
        "candidate_rung": 0.0500,
        "last_evaluated_month": "2026-01",
    }
    rebuilt = _build(observations, prior_rung_state=v02_prior, as_of=pd.Timestamp("2026-03-31"))
    # Must carry method_version=1 and rule="candidate"
    assert rebuilt.rung_state["method_version"] == 1
    assert rebuilt.rung_state["rule"] == "candidate"
    assert rebuilt.rung_state["last_evaluated_month"] == "2026-03"
