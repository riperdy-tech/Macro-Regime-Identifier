"""Long-run nominal growth anchor.

Built from two OBSERVABLE legs, which is what makes it FRED-provable end to end:

    real potential growth   <- the log-linear trend of the observed potential-output
                               series (CBO Real Potential GDP)
    inflation expectation   <- the market-implied long-run forward rate (5y5y), with
                               the 10y breakeven as the documented fallback

    nominal trend = real potential + inflation expectation

Why a UNIFORM terminal rate is right, and what was actually wrong before: no company
can outgrow its economy in perpetuity, so a single perpetual rate across issuers is
physically correct. The defect was never the cross-sectional uniformity — it was that
the constant had no source, no version and no revision path, while feeding every
issuer's `expectations_gap_pts` through the implied-growth solver. This anchor makes
the number sourced, versioned and revisable, and publishes the delta against the
constant it replaces so consumers can see exactly what moved.
"""

from __future__ import annotations

import math

import pandas as pd

from macro_engine.anchors.config import AnchorConfig
from macro_engine.anchors.models import AnchorProvenance, LongRunGrowthAnchor
from macro_engine.anchors.cost_of_capital import _resolve, _series_slice
from macro_engine.evaluation.asof import normalize_asof


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


def build_long_run_growth_anchor(
    *,
    observations: pd.DataFrame,
    config: AnchorConfig,
    as_of: pd.Timestamp,
    built_at: str,
    regime_label: str | None = None,
    scoring_mode: str = "calendar_asof",
    vintages: pd.DataFrame | None = None,
    source_files: list[str] | None = None,
) -> LongRunGrowthAnchor:
    growth = config.growth
    input_dates: dict[str, str] = {}
    reasons: list[str] = []

    real_series = growth.real_potential.series
    real_frame = _series_slice(observations, real_series)
    real_potential, real_date, real_n = log_linear_trend_annualized(
        real_frame["value"] if not real_frame.empty else pd.Series(dtype="float64"),
        real_frame["date"] if not real_frame.empty else pd.Series(dtype="datetime64[ns]"),
        window_years=growth.real_potential.trend_window_years,
        min_observations=growth.real_potential.min_observations,
        as_of=as_of,
    )
    if real_potential is None:
        reasons.append(
            f"real_potential: {real_series} has {real_n} usable observations; "
            f"{growth.real_potential.min_observations} required to fit a trend"
        )
    elif real_date:
        input_dates[real_series] = real_date

    inflation_expectation = None
    inflation_series_used = None
    for ref in growth.inflation_expectation.candidates:
        frame = _series_slice(observations, ref.series)
        row, reason = _resolve(frame, as_of, scoring_mode=scoring_mode, vintages=vintages)
        if row is None:
            reasons.append(f"inflation_expectation: {ref.series} unavailable ({reason})")
            continue
        value = row["value"]
        if value is None or pd.isna(value):
            reasons.append(f"inflation_expectation: {ref.series} latest value is empty")
            continue
        # The declared `units` convention converts the quoted rate to a decimal fraction.
        inflation_expectation = ref.to_decimal(float(value))
        inflation_series_used = ref.series
        input_dates[ref.series] = pd.Timestamp(row["date"]).date().isoformat()
        break
    if inflation_expectation is None:
        reasons.append(
            "inflation_expectation: none of "
            f"{[ref.series for ref in growth.inflation_expectation.candidates]} "
            "produced a value"
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
    suggestion = None
    raw_trend_g = None
    adjustment = None
    if nominal_trend is not None:
        raw_trend_g = nominal_trend * clamp.max_share_of_nominal_trend
        adjustment = float(growth.regime_sensitivity.get(str(regime_label), 0.0))
        candidate = raw_trend_g + adjustment
        bounded = min(max(candidate, clamp.floor), clamp.ceiling)
        suggestion = round(bounded / clamp.round_to) * clamp.round_to
        suggestion = round(min(max(suggestion, clamp.floor), clamp.ceiling), 6)
    if regime_label is not None and regime_label not in growth.regime_sensitivity:
        reasons.append(
            f"regime_sensitivity: no entry for regime {regime_label!r}; "
            "no regime adjustment applied"
        )

    prior = growth.downstream_prior_in_use
    delta = None if suggestion is None or prior is None else round(suggestion - prior, 6)
    degraded = nominal_trend is None

    return LongRunGrowthAnchor(
        asof=as_of.date().isoformat(),
        built_at=built_at,
        nominal_gdp_trend=None if nominal_trend is None else round(nominal_trend, 4),
        components=components,
        terminal_g_suggestion=suggestion,
        regime_sensitivity=dict(growth.regime_sensitivity),
        regime_applied=regime_label,
        regime_adjustment=adjustment,
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
            ]
            + ([f"inflation_expectation leg taken from {inflation_series_used}"]
               if inflation_series_used
               else []),
        ),
    )


def current_regime_label(
    timeline: pd.DataFrame,
    as_of: pd.Timestamp,
) -> str | None:
    """Published regime label at or before `as_of`, from the STORED timeline.

    Read from the timeline rather than current_regime.json because that file is a
    latest-snapshot artifact whose `date` field can be synthetic (a known defect,
    recorded in the repair package and deliberately not fixed here).
    """
    if timeline.empty or "date" not in timeline.columns:
        return None
    frame = timeline.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame[frame["date"] <= normalize_asof(as_of)]
    if frame.empty:
        return None
    row = frame.sort_values("date").iloc[-1]
    for column in ("reported_regime", "dominant_regime"):
        value = row.get(column)
        if isinstance(value, str) and value:
            return value
    return None
