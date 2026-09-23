"""Long-run nominal growth anchor.

Built from two OBSERVABLE legs, which is what makes it FRED-provable end to end:

    real potential growth   <- the log-linear trend of the observed potential-output
                               series (CBO Real Potential GDP)
    inflation expectation   <- the trailing 12-calendar-month mean of the monthly mean
                               of the market-implied long-run forward rate (5y5y), with
                               the 10y breakeven as the documented fallback

    nominal trend = real potential + inflation expectation

Why a UNIFORM terminal rate is right, and what was actually wrong before: no company
can outgrow its economy in perpetuity, so a single perpetual rate across issuers is
physically correct. The defect was never the cross-sectional uniformity — it was that
the constant had no source, no version and no revision path, while feeding every
issuer's `expectations_gap_pts` through the implied-growth solver. This anchor makes
the number sourced, versioned and revisable, and publishes the delta against the
constant it replaces so consumers can see exactly what moved.

v0.3 (P0_0_MRI_TARGET_ARCHITECTURE.md §5.2, S2.1): the regime LABEL no longer reaches
this anchor. Two things changed:

1. The inflation-expectation leg is now a trailing 12-month mean of the monthly mean of
   the spot series, not the single latest observation -- measured (§5.1) to move a
   perpetuity 89 times in 268 months at spot vs. 19 smoothed. `raw_trend_g` is
   0.85 x the smoothed nominal trend.
2. The published rung moves only under a dead-band + 3-consecutive-monthly-build
   confirmation rule (specification E; `growth.rung.confirm_months` is the one config
   flag that switches to specification C). The label's per-regime adjustment
   (`regime_sensitivity`) is removed from the computation entirely; the keys are kept
   for one release, always null/empty, for a v1/v0.2 reader (see `deprecations`).

The rung state (current rung, candidate, months confirmed, last change, changes in the
trailing 10 years) is a pure function of the previous PUBLISHED state and this build's
smoothed raw trend -- see `advance_rung_state`. It is persisted in `anchor_runs` (the
previous build's `rung_state`, read back by `anchors/service.py`) so a rebuild does not
silently reset the confirmation counter, and the same function replays a full history
offline in `scripts/measure_growth_rung_path.py`.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from macro_engine.anchors.config import AnchorConfig
from macro_engine.anchors.models import AnchorProvenance, LongRunGrowthAnchor
from macro_engine.anchors.cost_of_capital import _resolve, _series_slice
from macro_engine.evaluation.asof import normalize_asof

# v0.3: the label no longer reaches this anchor. Published verbatim into every growth
# anchor's provenance.regime_leg (P0.3, folded into S2 per the operator's instruction).
GROWTH_REGIME_LEG_REMOVED = {"used": False, "reason": "label_channel_removed_v0.3"}


def log_linear_trend_annualized(
    values: pd.Series,
    dates: pd.Series,
    *,
    window_years: int,
    min_observations: int,
    as_of: pd.Timestamp,
) -> tuple[float | None, str | None, int]:
    """Annualised log-linear trend of a level series.

    OLS on log(level) vs time, so the slope IS a continuously-compounded growth rate
    and endpoint noise cannot dominate the estimate the way an endpoint CAGR lets it.
    A trend needs a trend's worth of data: too few points returns None rather than a
    slope fitted to noise.

    Returns (annual_rate, last_observation_iso, n_observations).
    """
    frame = pd.DataFrame({"date": pd.to_datetime(dates, errors="coerce"), "value": values})
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["date", "value"])
    moment = normalize_asof(as_of)
    frame = frame[(frame["date"] <= moment) & (frame["value"] > 0)]
    if frame.empty:
        return None, None, 0
    window_start = moment - pd.DateOffset(years=int(window_years))
    windowed = frame[frame["date"] >= window_start]
    # Fall back to the full history only when the window itself is too thin to fit;
    # a short window is a legitimate configuration, an unfittable one is not.
    if len(windowed) >= min_observations:
        frame = windowed
    if len(frame) < min_observations:
        return None, None, len(frame)

    origin = frame["date"].min()
    x = (frame["date"] - origin).dt.days.astype(float) / 365.25
    y = frame["value"].map(math.log)
    x_mean, y_mean = x.mean(), y.mean()
    denominator = float(((x - x_mean) ** 2).sum())
    if denominator == 0.0:
        return None, None, len(frame)
    slope = float(((x - x_mean) * (y - y_mean)).sum() / denominator)
    if not math.isfinite(slope):
        return None, None, len(frame)
    last_date = frame["date"].max().date().isoformat()
    return slope, last_date, len(frame)


def trailing_12m_mean_of_monthly_mean(
    values: pd.Series,
    dates: pd.Series,
    *,
    as_of: pd.Timestamp,
    min_months: int,
) -> tuple[float | None, str | None, int]:
    """Trailing N-calendar-month mean of the monthly mean of an irregular/daily series.

    Two averaging steps, in order: (1) every observation dated in a calendar month is
    averaged to one monthly value, so a month with more trading days is not over-
    weighted; (2) the last `min_months` monthly values at or before `as_of` are
    averaged again. This is the smoothing P0_0 §5.1 measured: 19 rung changes in 268
    months against 89 for the spot leg it replaces.

    Returns (value, last_month_end_iso, n_months_used). None when fewer than
    `min_months` calendar months of history are available at or before `as_of` -- a
    short window is not a trailing mean, the same discipline as the trend fit above.
    """
    frame = pd.DataFrame({"date": pd.to_datetime(dates, errors="coerce"), "value": values})
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["date", "value"])
    moment = normalize_asof(as_of)
    frame = frame[frame["date"] <= moment]
    if frame.empty:
        return None, None, 0
    monthly = frame.set_index("date")["value"].resample("MS").mean().dropna()
    if len(monthly) < min_months:
        return None, None, len(monthly)
    window = monthly.iloc[-min_months:]
    last_month_end = (window.index.max() + pd.offsets.MonthEnd(0)).date().isoformat()
    return float(window.mean()), last_month_end, len(window)


def advance_rung_state(
    *,
    raw_trend_g_clamped: float,
    as_of: pd.Timestamp,
    prior_state: dict[str, Any] | None,
    round_to: float,
    confirm_months: int,
    rule: str = "candidate",
) -> dict[str, Any]:
    """One step of the dead-band + N-consecutive-monthly-build confirmation rule.

    P0_0_MRI_TARGET_ARCHITECTURE.md §5.2 / §10 Q2 (specification E as ruled,
    confirm_months=3, rule="candidate"; confirm_months=1 is specification C;
    rule="departure" is the departure variant). Pure function of the previously
    PUBLISHED rung state and this build's clamped raw trend, so the exact same
    function drives one live step (from `anchor_runs`) and a full historical replay
    (`scripts/measure_growth_rung_path.py`).

    Dead-band: the rung changes only once the raw trend is beyond the FAR edge of the
    current rung's cell, i.e. `abs(raw - current_rung) > round_to` -- not merely past
    the midpoint to the next rung, which is what an unbanded round-to-nearest already
    does (measured as specification B, 19 changes; the dead-band alone, C, is 11).
    Confirmation: under the candidate rule (default), the candidate rung must hold for
    `confirm_months` CONSECUTIVE monthly builds beyond the dead-band before the rung
    moves; if the candidate changes during a departure, the counter resets. Under the
    departure rule, departure alone advances the count.

    Guarded to at most one step per calendar month (`last_evaluated_month`): the daily
    pipeline may call `build-anchors` more than once inside the same month, and
    "monthly build" in the architecture's own measurement means one evaluation per
    calendar month, not one per pipeline run.
    """
    if rule not in ("candidate", "departure"):
        raise ValueError(f"unknown rung rule: {rule!r}, expected 'candidate' or 'departure'")

    candidate_rung = round(raw_trend_g_clamped / round_to) * round_to
    month_m = pd.Timestamp(as_of).to_period("M").start_time
    as_of_month = month_m.strftime("%Y-%m")
    m_iso = month_m.date().isoformat()

    if not prior_state or prior_state.get("current_rung") is None:
        # First build ever: nothing to confirm against, the rung is seeded.
        gap_bp = round((raw_trend_g_clamped - candidate_rung) * 1e4)
        return {
            "current_rung": candidate_rung,
            "candidate_rung": candidate_rung,
            "months_confirmed": 0,
            "last_change_date": None,
            "last_evaluated_month": as_of_month,
            "changes_last_10y": 0,
            "change_history": [],
            "gap_bp": gap_bp,
            "rule": rule,
            "method_version": 1,
        }

    if prior_state.get("last_evaluated_month") == as_of_month:
        # Same calendar month as the last step already taken (a same-month rebuild):
        # republish, do not advance or re-count the confirmation streak.
        state = dict(prior_state)
        state["candidate_rung"] = candidate_rung
        current = float(state["current_rung"])
        state["gap_bp"] = round((raw_trend_g_clamped - current) * 1e4)
        state["rule"] = rule
        state["method_version"] = 1
        return state

    current_rung = float(prior_state["current_rung"])
    months_confirmed = int(prior_state.get("months_confirmed", 0))
    history = list(prior_state.get("change_history", []))
    last_change_date = prior_state.get("last_change_date")

    departed = abs(raw_trend_g_clamped - current_rung) > round_to + 1e-9

    if rule == "departure":
        months_confirmed = months_confirmed + 1 if departed else 0
        if departed and months_confirmed >= confirm_months and candidate_rung != current_rung:
            current_rung = candidate_rung
            last_change_date = m_iso
            history.append(m_iso)
            months_confirmed = 0
    else:  # "candidate"
        if departed:
            months_confirmed = (
                (months_confirmed + 1)
                if (months_confirmed > 0 and prior_state.get("candidate_rung") == candidate_rung)
                else 1
            )
            if months_confirmed >= confirm_months:
                current_rung = candidate_rung
                last_change_date = m_iso
                history.append(m_iso)
                months_confirmed = 0
        else:
            months_confirmed = 0

    cutoff = month_m - pd.DateOffset(years=10)
    history = [d for d in history if pd.Timestamp(d) > cutoff]
    gap_bp = round((raw_trend_g_clamped - current_rung) * 1e4)

    return {
        "current_rung": current_rung,
        "candidate_rung": candidate_rung,
        "months_confirmed": months_confirmed,
        "last_change_date": last_change_date,
        "last_evaluated_month": as_of_month,
        "changes_last_10y": len(history),
        "change_history": history,
        "gap_bp": gap_bp,
        "rule": rule,
        "method_version": 1,
    }

def compute_raw_trend_g_at_month(
    observations: pd.DataFrame,
    config: AnchorConfig,
    month_m: pd.Timestamp,
    real_frame: pd.DataFrame | None = None,
    candidate_frames: list[tuple[Any, pd.DataFrame]] | None = None,
) -> tuple[float | None, float | None, str | None]:
    """Compute (raw_trend_g_clamped, inflation_smoothed, inflation_series_used) at month M."""
    growth = config.growth
    if real_frame is None:
        real_frame = _series_slice(observations, growth.real_potential.series)
    if candidate_frames is None:
        candidate_frames = [
            (ref, _series_slice(observations, ref.series))
            for ref in growth.inflation_expectation.candidates
        ]

    real_potential, _, _ = log_linear_trend_annualized(
        real_frame["value"] if not real_frame.empty else pd.Series(dtype="float64"),
        real_frame["date"] if not real_frame.empty else pd.Series(dtype="datetime64[ns]"),
        window_years=growth.real_potential.trend_window_years,
        min_observations=growth.real_potential.min_observations,
        as_of=month_m,
    )
    if real_potential is None:
        return None, None, None

    last_complete_month_end = month_m - pd.Timedelta(days=1)
    inflation = None
    series_used = None
    for ref, frame in candidate_frames:
        smoothed, _, _ = trailing_12m_mean_of_monthly_mean(
            frame["value"] if not frame.empty else pd.Series(dtype="float64"),
            frame["date"] if not frame.empty else pd.Series(dtype="datetime64[ns]"),
            as_of=last_complete_month_end,
            min_months=growth.inflation_expectation.min_observations,
        )
        if smoothed is not None:
            inflation = ref.to_decimal(smoothed)
            series_used = ref.series
            break
    if inflation is None:
        return None, None, None

    nominal_trend = real_potential + inflation
    raw = nominal_trend * growth.terminal_g.max_share_of_nominal_trend
    clamped = min(max(raw, growth.terminal_g.floor), growth.terminal_g.ceiling)
    return clamped, inflation, series_used


def resolve_growth_rung_state(
    *,
    observations: pd.DataFrame,
    config: AnchorConfig,
    as_of: pd.Timestamp,
    prior_rung_state: dict[str, Any] | None,
    current_clamped: float | None = None,
) -> dict[str, Any]:
    """Resolve or advance the rung state with full historical replay seeding and missed-month catch-up.

    P0_0_MRI_TARGET_ARCHITECTURE.md §5.2 / MRI_S2_APPROVAL.md §8 C2:
    - When no prior state carries rule == config.growth.rung.rule (e.g. 'candidate') and method_version == 1,
      rebuild the state by replaying from rung.replay_start (2004-06-01) through the current month.
    - When the prior last_evaluated_month is older than the previous month, step through every missed month.
    - When the prior is the current month (same-month rebuild), republish.
    """
    growth = config.growth
    month_m = pd.Timestamp(as_of).to_period("M").start_time
    target_month = month_m.to_period("M")
    round_to = growth.terminal_g.round_to
    confirm_months = growth.rung.confirm_months
    rule = growth.rung.rule

    is_valid_prior = (
        isinstance(prior_rung_state, dict)
        and prior_rung_state.get("rule") == rule
        and prior_rung_state.get("method_version") == 1
        and prior_rung_state.get("current_rung") is not None
        and prior_rung_state.get("last_evaluated_month") is not None
    )

    real_frame = _series_slice(observations, growth.real_potential.series)
    cand_frames = [
        (ref, _series_slice(observations, ref.series))
        for ref in growth.inflation_expectation.candidates
    ]

    if not is_valid_prior:
        # Full replay from replay_start
        start_month = pd.Timestamp(growth.rung.replay_start).to_period("M").start_time
        month_grid = pd.date_range(start_month, month_m, freq="MS")
        state = None
        for m in month_grid:
            if m == month_m and current_clamped is not None:
                r = current_clamped
            else:
                r, _, _ = compute_raw_trend_g_at_month(observations, config, m, real_frame, cand_frames)
            if r is None:
                continue
            state = advance_rung_state(
                raw_trend_g_clamped=r,
                as_of=m,
                prior_state=state,
                round_to=round_to,
                confirm_months=confirm_months,
                rule=rule,
            )
        return state or {}

    prior_period = pd.Period(prior_rung_state["last_evaluated_month"], freq="M")
    if prior_period == target_month:
        # Same-month rebuild
        r = (
            current_clamped
            if current_clamped is not None
            else compute_raw_trend_g_at_month(observations, config, month_m, real_frame, cand_frames)[0]
        )
        if r is None:
            return dict(prior_rung_state)
        return advance_rung_state(
            raw_trend_g_clamped=r,
            as_of=month_m,
            prior_state=prior_rung_state,
            round_to=round_to,
            confirm_months=confirm_months,
            rule=rule,
        )

    if prior_period > target_month:
        # Evaluation date in past relative to prior state
        r = (
            current_clamped
            if current_clamped is not None
            else compute_raw_trend_g_at_month(observations, config, month_m, real_frame, cand_frames)[0]
        )
        if r is None:
            return dict(prior_rung_state)
        return advance_rung_state(
            raw_trend_g_clamped=r,
            as_of=month_m,
            prior_state=None,
            round_to=round_to,
            confirm_months=confirm_months,
            rule=rule,
        )

    # Catch up missed months (and step current month)
    missed_periods = pd.period_range(prior_period + 1, target_month, freq="M")
    state = dict(prior_rung_state)
    for p in missed_periods:
        m = p.start_time
        if m == month_m and current_clamped is not None:
            r = current_clamped
        else:
            r, _, _ = compute_raw_trend_g_at_month(observations, config, m, real_frame, cand_frames)
        if r is None:
            continue
        state = advance_rung_state(
            raw_trend_g_clamped=r,
            as_of=m,
            prior_state=state,
            round_to=round_to,
            confirm_months=confirm_months,
            rule=rule,
        )
    return state


def build_long_run_growth_anchor(
    *,
    observations: pd.DataFrame,
    config: AnchorConfig,
    as_of: pd.Timestamp,
    built_at: str,
    prior_rung_state: dict[str, Any] | None = None,
    scoring_mode: str = "calendar_asof",
    vintages: pd.DataFrame | None = None,
    source_files: list[str] | None = None,
) -> LongRunGrowthAnchor:
    """v0.3: no regime label anywhere in this function's inputs or outputs.

    `prior_rung_state` is the PREVIOUSLY PUBLISHED `rung_state` (read by the caller
    from `anchor_runs`, `None` on the very first build); it is the only piece of state
    this anchor carries across builds. `vintages` is accepted for signature
    compatibility with the other anchor builders but is not used here: both legs
    (the potential-output trend and the trailing 12-month inflation mean) are
    calendar-dated smoothing windows, the same discipline `log_linear_trend_annualized`
    already used before v0.3 -- see docs/ANCHOR_METHODOLOGY.md.
    """
    growth = config.growth
    input_dates: dict[str, str] = {}
    reasons: list[str] = []

    # Month-start grid: evaluation month M is the first calendar day of the build month.
    as_of_dt = pd.Timestamp(as_of)
    month_m = as_of_dt.to_period("M").start_time
    last_complete_month_end = month_m - pd.Timedelta(days=1)

    real_series = growth.real_potential.series
    real_frame = _series_slice(observations, real_series)
    real_potential, real_date, real_n = log_linear_trend_annualized(
        real_frame["value"] if not real_frame.empty else pd.Series(dtype="float64"),
        real_frame["date"] if not real_frame.empty else pd.Series(dtype="datetime64[ns]"),
        window_years=growth.real_potential.trend_window_years,
        min_observations=growth.real_potential.min_observations,
        as_of=month_m,
    )
    if real_potential is None:
        reasons.append(
            f"real_potential: {real_series} has {real_n} usable observations; "
            f"{growth.real_potential.min_observations} required to fit a trend"
        )
    elif real_date:
        input_dates[real_series] = real_date

    # Smoothed leg (drives the trend) and spot leg (published for comparison only),
    # taken from the SAME candidate series so the two numbers describe one input.
    # The inflation leg is the mean over the 12 complete calendar months before month M.
    inflation_expectation = None
    inflation_expectation_spot = None
    inflation_series_used = None
    inflation_months_used = 0
    for ref in growth.inflation_expectation.candidates:
        frame = _series_slice(observations, ref.series)
        smoothed, month_end, n_months = trailing_12m_mean_of_monthly_mean(
            frame["value"] if not frame.empty else pd.Series(dtype="float64"),
            frame["date"] if not frame.empty else pd.Series(dtype="datetime64[ns]"),
            as_of=last_complete_month_end,
            min_months=growth.inflation_expectation.min_observations,
        )
        if smoothed is None:
            reasons.append(
                f"inflation_expectation: {ref.series} has {n_months} monthly observations; "
                f"{growth.inflation_expectation.min_observations} required for the trailing mean"
            )
            continue
        spot_row, spot_reason = _resolve(
            frame, as_of, scoring_mode=scoring_mode, vintages=vintages, series_id=ref.series
        )
        inflation_expectation = ref.to_decimal(smoothed)
        inflation_expectation_spot = (
            ref.to_decimal(float(spot_row["value"]))
            if spot_row is not None and pd.notna(spot_row["value"])
            else None
        )
        inflation_series_used = ref.series
        inflation_months_used = n_months
        input_dates[ref.series] = month_end
        break
    if inflation_expectation is None:
        reasons.append(
            "inflation_expectation: none of "
            f"{[ref.series for ref in growth.inflation_expectation.candidates]} "
            "produced a trailing-12-month mean"
        )

    components = {
        "real_potential": None if real_potential is None else round(real_potential, 4),
        "inflation_expectation": (
            None if inflation_expectation is None else round(inflation_expectation, 4)
        ),
    }
    nominal_trend = (
        None
        if real_potential is None or inflation_expectation is None
        else real_potential + inflation_expectation
    )

    clamp = growth.terminal_g
    raw_trend_g = None
    rung_state: dict[str, Any] = dict(prior_rung_state or {})
    suggestion = None
    if nominal_trend is not None:
        raw_trend_g = nominal_trend * clamp.max_share_of_nominal_trend
        clamped = min(max(raw_trend_g, clamp.floor), clamp.ceiling)
        rung_state = resolve_growth_rung_state(
            observations=observations,
            config=config,
            as_of=month_m,
            prior_rung_state=prior_rung_state,
            current_clamped=clamped,
        )
        suggestion = round(rung_state["current_rung"], 6)
    else:
        reasons.append("terminal_g_rung: no nominal trend, the previously published rung_state is republished unchanged")



    prior = growth.downstream_prior_in_use
    delta = None if suggestion is None or prior is None else round(suggestion - prior, 6)
    degraded = nominal_trend is None

    return LongRunGrowthAnchor(
        asof=as_of.date().isoformat(),
        built_at=built_at,
        nominal_gdp_trend=None if nominal_trend is None else round(nominal_trend, 4),
        components=components,
        inflation_expectation_spot=(
            None if inflation_expectation_spot is None else round(inflation_expectation_spot, 4)
        ),
        terminal_g_suggestion=suggestion,
        terminal_g_rung=suggestion,
        rung_state=rung_state,
        # v0.3 DEPRECATED: the label no longer reaches this anchor (P0_0 §5.2). Kept
        # null/empty for one release; see `deprecations`.
        regime_sensitivity={},
        regime_applied=None,
        regime_adjustment=None,
        raw_trend_g=None if raw_trend_g is None else round(raw_trend_g, 6),
        clamp={
            "max_share_of_nominal_trend": clamp.max_share_of_nominal_trend,
            "floor": clamp.floor,
            "ceiling": clamp.ceiling,
        },
        downstream_prior_in_use=prior,
        delta=delta,
        degraded=degraded,
        source_files=list(source_files or []),
        provenance=AnchorProvenance(
            scoring_mode=scoring_mode,
            input_dates=input_dates,
            degradation_reasons=reasons,
            notes=[
                "A uniform perpetual growth rate across issuers is deliberate: no company "
                "outgrows its economy forever. This anchor replaces an unsourced constant "
                "with a sourced, versioned and revisable one.",
                "v0.3: the regime label no longer moves this anchor. terminal_g_suggestion "
                "is the published rung after a dead-band + "
                f"{growth.rung.confirm_months}-consecutive-monthly-build confirmation rule "
                "over the smoothed (trailing 12-month) inflation-expectation leg.",
            ]
            + (
                [
                    f"inflation_expectation leg taken from {inflation_series_used}, "
                    f"trailing {inflation_months_used} months"
                ]
                if inflation_series_used
                else []
            ),
            regime_leg=dict(GROWTH_REGIME_LEG_REMOVED),
        ),
        deprecations=[
            "regime_sensitivity, regime_applied and regime_adjustment are always empty/null "
            "in v0.3: the regime label was removed from this anchor's computation "
            "(P0_0_MRI_TARGET_ARCHITECTURE.md §5.2). Kept for one release for a v1/v0.2 "
            "reader; rs2_data.anchor_terminal_g() reads only terminal_g_suggestion, "
            "degraded and asof, so it is unaffected.",
        ],
    )
