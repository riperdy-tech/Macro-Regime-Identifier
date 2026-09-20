"""Cost-of-capital anchor: the market's cost of EQUITY, decomposed and sourced.

What this module can prove from FRED (its actual remit):
    the whole risk-free curve — nominal 10y, TIPS real 10y, the breakeven between
    them, and a term premium — plus the inflation backdrop.

What needs an equity-side aggregate (which MRI does not hold):
    the IMPLIED equity risk premium (solve r such that an aggregate DCF equals
    aggregate market capitalisation, Damodaran-style). When that input is absent the
    module publishes the curve plus a regime-conditional percentile band drawn from a
    documented, versioned ERP history, marks `market_implied_erp` unavailable, and
    says so in `degradation_reasons`. It never invents a number.

WACC is deliberately absent. The cash-flow definition downstream is
``NI + D&A - capex`` — already net of interest — discounted against MARKET CAP.
Pairing a levered flow with a firm-level WACC and enterprise value double-counts the
debt claim; that was tried downstream on 2026-08-07 and reverted. Re-introducing it
here would be a regression, so this anchor is a cost of equity by construction.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.anchors.config import (
    AnchorConfig,
    CostOfCapitalConfig,
    RiskFreeConsistencyConfig,
    SeriesRef,
)
from macro_engine.anchors.models import AnchorProvenance, CostOfCapitalAnchor
from macro_engine.evaluation.asof import (
    latest_observation_on_or_before_date,
    normalize_asof,
    point_in_time_observation,
)

WACC_EXCLUSION_NOTE = (
    "Cost of EQUITY, not WACC: the downstream cash flow (NI + D&A - capex) is levered "
    "and is compared against market cap, so a firm-level WACC would double-count the "
    "debt claim."
)

# A Gordon terminal value is undefined at or below perpetual growth, so the implied-rate
# search starts strictly above it.
MIN_RATE_ABOVE_TERMINAL = 1e-4


def _resolve(
    observations: pd.DataFrame,
    as_of: pd.Timestamp,
    *,
    scoring_mode: str,
    vintages: pd.DataFrame | None,
    series_id: str,
) -> tuple[pd.Series | None, str]:
    """Latest observation for `series_id` under the configured as-of rule.

    `series_id` is required, not inferred: in point-in-time mode the resolver reads the shared
    vintage table, and a caller that does not name its series gets whichever one sorts last.
    See evaluation.asof._single_series for what that cost.
    """
    if scoring_mode == "point_in_time":
        if vintages is None or vintages.empty:
            return None, "pit_vintage_missing"
        row = point_in_time_observation(vintages, as_of, series_id=series_id)
        return (row, "ok") if row is not None else (None, "not_yet_published")
    row = latest_observation_on_or_before_date(observations, as_of)
    return (row, "ok") if row is not None else (None, "not_yet_published")


def _series_slice(observations: pd.DataFrame, series_id: str) -> pd.DataFrame:
    if observations.empty or "series_id" not in observations.columns:
        return observations.iloc[0:0]
    frame = observations[observations["series_id"] == series_id].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    return frame.dropna(subset=["date", "value"]).sort_values("date")


def _latest_value(
    observations: pd.DataFrame,
    ref: SeriesRef,
    as_of: pd.Timestamp,
    *,
    scoring_mode: str,
    vintages: pd.DataFrame | None,
    input_dates: dict[str, str],
    reasons: list[str],
    label: str,
) -> float | None:
    """Latest value of `ref`, converted from its declared quoting convention.

    Conversion happens here, at the single point of entry, so every downstream
    calculation is in decimal fractions and no caller has to remember which FRED
    series happens to be quoted in percent.
    """
    frame = _series_slice(observations, ref.series)
    row, reason = _resolve(
        frame, as_of, scoring_mode=scoring_mode, vintages=vintages, series_id=ref.series
    )
    if row is None:
        reasons.append(f"{label}: {ref.series} unavailable ({reason})")
        return None
    input_dates[ref.series] = pd.Timestamp(row["date"]).date().isoformat()
    value = row["value"]
    if value is None or pd.isna(value):
        reasons.append(f"{label}: {ref.series} latest value is empty")
        return None
    return ref.to_decimal(float(value))


def yoy_change(
    observations: pd.DataFrame,
    series_id: str,
    as_of: pd.Timestamp,
    *,
    periods: int = 12,
    scoring_mode: str = "calendar_asof",
    vintages: pd.DataFrame | None = None,
) -> tuple[float | None, str | None]:
    """Year-over-year percent change of an index level, as of `as_of`.

    Returns (value, observation_date). A non-positive index level is corruption, not
    a rate, so it is refused rather than turned into a nonsense percentage.

    In point-in-time mode the whole history is read from the vintage current on
    `as_of`, so the prior-year leg is the prior-year figure AS THEN PUBLISHED. That
    matters: CPI re-seasonalisation and benchmark revisions rewrite the base, and
    using a revised base against an as-published current level is precisely the
    look-ahead this mode exists to remove.
    """
    if scoring_mode == "point_in_time":
        frame = _visible_vintage_series(vintages, series_id, as_of)
    else:
        frame = _series_slice(observations, series_id)
        row, _ = _resolve(
            frame, as_of, scoring_mode="calendar_asof", vintages=None, series_id=series_id
        )
        if row is None:
            return None, None
        frame = frame[frame["date"] <= pd.Timestamp(row["date"])]
    if frame.empty:
        return None, None

    latest_date = pd.Timestamp(frame["date"].max())
    prior_date = latest_date - pd.DateOffset(months=int(periods))
    prior = frame[frame["date"] <= prior_date]
    if prior.empty:
        return None, None
    current_value = float(frame[frame["date"] == latest_date].iloc[-1]["value"])
    prior_value = float(prior.iloc[-1]["value"])
    if current_value <= 0 or prior_value <= 0:
        return None, None
    return (current_value / prior_value - 1.0), latest_date.date().isoformat()


def _visible_vintage_series(
    vintages: pd.DataFrame | None,
    series_id: str,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """The series as known on `as_of`, one row per observation period."""
    if vintages is None or vintages.empty or "series_id" not in vintages.columns:
        return pd.DataFrame(columns=["date", "value"])
    frame = vintages[vintages["series_id"] == series_id].copy()
    if frame.empty:
        return pd.DataFrame(columns=["date", "value"])
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["realtime_start"] = pd.to_datetime(frame["realtime_start"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["date", "realtime_start"])
    moment = normalize_asof(as_of)
    # Newest vintage published by `as_of`, one row per observation period (see
    # evaluation.asof.point_in_time_series for why the bracket test is wrong for ALFRED rows).
    frame = frame[frame["realtime_start"] <= moment]
    if frame.empty:
        return pd.DataFrame(columns=["date", "value"])
    frame = frame.sort_values(["date", "realtime_start"])
    return frame.groupby("date", as_index=False).tail(1).sort_values("date")[
        ["date", "value"]
    ].reset_index(drop=True)


def term_premium(
    *,
    observations: pd.DataFrame,
    config: CostOfCapitalConfig,
    as_of: pd.Timestamp,
    scoring_mode: str,
    vintages: pd.DataFrame | None,
    input_dates: dict[str, str],
    reasons: list[str],
) -> tuple[float | None, str | None]:
    """Observed term premium if published, else a clearly-labelled computed proxy."""
    for ref in config.risk_free.term_premium_candidates:
        frame = _series_slice(observations, ref.series)
        row, _ = _resolve(
            frame, as_of, scoring_mode=scoring_mode, vintages=vintages, series_id=ref.series
        )
        if row is not None and not pd.isna(row["value"]):
            input_dates[ref.series] = pd.Timestamp(row["date"]).date().isoformat()
            return ref.to_decimal(float(row["value"])), f"observed:{ref.series}"

    proxy = config.risk_free.term_premium_proxy
    nominal_ref, short_ref = proxy.get("nominal_series"), proxy.get("short_series")
    if nominal_ref is None or short_ref is None:
        reasons.append("term_premium: no observed series available and no proxy configured")
        return None, None
    nominal = _latest_value(
        observations, nominal_ref, as_of, scoring_mode=scoring_mode, vintages=vintages,
        input_dates=input_dates, reasons=reasons, label="term_premium proxy (nominal)",
    )
    short = _latest_value(
        observations, short_ref, as_of, scoring_mode=scoring_mode, vintages=vintages,
        input_dates=input_dates, reasons=reasons, label="term_premium proxy (short rate)",
    )
    if nominal is None or short is None:
        reasons.append("term_premium: proxy inputs unavailable")
        return None, None
    reasons.append(
        "term_premium: no observed term-premium series available; published the "
        f"{nominal_ref.series} - {short_ref.series} constant-short-rate proxy instead"
    )
    return nominal - short, f"proxy:{nominal_ref.series}-{short_ref.series}"


# ── Implied ERP ───────────────────────────────────────────────────────────────


def two_stage_value(
    cash_flow: float,
    growth: float,
    rate: float,
    terminal_growth: float,
    *,
    stage1_years: int = 5,
    fade_years: int = 5,
) -> float | None:
    """Same two-stage fade-to-terminal shape the downstream DCF uses.

    Reimplemented rather than imported: MRI must not depend on the consumer's code.
    Kept numerically identical in form (stage-1 constant growth, linear fade, Gordon
    terminal) so the implied rate this solves for means the same thing downstream.
    """
    if rate <= terminal_growth:
        return None
    pv = 0.0
    cf = float(cash_flow)
    year = 0
    for _ in range(stage1_years):
        year += 1
        cf *= 1 + growth
        pv += cf / (1 + rate) ** year
    for index in range(1, fade_years + 1):
        year += 1
        step = growth + (terminal_growth - growth) * index / fade_years
        cf *= 1 + step
        pv += cf / (1 + rate) ** year
    pv += (cf * (1 + terminal_growth) / (rate - terminal_growth)) / (1 + rate) ** year
    return pv


def solve_implied_rate(
    *,
    constituents: list[dict[str, float]],
    target_value: float,
    terminal_growth: float,
    rate_bounds: tuple[float, float],
    iterations: int,
) -> tuple[float | None, str]:
    """Bisection for the rate at which the aggregate DCF equals `target_value`.

    The search domain is clipped to rates strictly above `terminal_growth`, because a
    Gordon terminal value is undefined at or below perpetual growth — a configured
    lower bound below it names a rate at which the model does not exist, not a rate the
    market could disagree with. Clipping RESTRICTS the domain to where the function is
    defined; it never widens the search to reach an answer.

    Returns (rate, status). `no_solution_in_bounds` is a real answer: it means the
    market's price is not reachable by any discount rate in the valid domain, which is
    a finding about the inputs, not a reason to widen the bounds silently.
    """
    if not constituents or target_value <= 0:
        return None, "no_inputs"
    low = max(float(rate_bounds[0]), float(terminal_growth) + MIN_RATE_ABOVE_TERMINAL)
    high = float(rate_bounds[1])
    if high <= low:
        return None, "no_solution_in_bounds"

    def aggregate(rate: float) -> float | None:
        total = 0.0
        for item in constituents:
            value = two_stage_value(
                item["cash_flow"], item["growth"], rate, terminal_growth
            )
            if value is None:
                return None
            total += value
        return total

    low_value, high_value = aggregate(low), aggregate(high)
    if low_value is None or high_value is None:
        return None, "no_solution_in_bounds"
    if not (low_value >= target_value >= high_value):
        # DCF is monotonically decreasing in the rate; if the target sits outside the
        # bracketed range there is no root to find.
        return None, "no_solution_in_bounds"
    for _ in range(iterations):
        mid = (low + high) / 2
        mid_value = aggregate(mid)
        if mid_value is None:
            return None, "no_solution_in_bounds"
        if mid_value > target_value:
            low = mid
        else:
            high = mid
    return (low + high) / 2, "ok"


def load_equity_aggregate(path: str | Path) -> dict[str, Any]:
    """Read the equity-side aggregate the implied-ERP solve needs.

    Schema (see docs/ANCHOR_METHODOLOGY.md §4):
        {
          "asof": "YYYY-MM-DD",
          "source": "human-readable provenance",
          "growth": 0.045,                     # index-level trend, used when a
                                               # constituent carries no own growth
          "constituents": [                    # preferred, per-name solve
            {"ticker": "AAA", "cash_flow": 1.2e9, "market_cap": 3.0e10,
             "growth": 0.06}
          ],
          "coverage_share": 0.85               # share of index cap represented
        }
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("equity aggregate must be a JSON object")
    return payload


def implied_erp(
    *,
    aggregate: dict[str, Any],
    nominal_10y: float | None,
    config: CostOfCapitalConfig,
    terminal_growth: float | None,
) -> tuple[float | None, float | None, str, str | None]:
    """(erp, implied_coe, status, note) from an equity-side aggregate.

    The ERP is the discount rate at which aggregate cash flows justify aggregate
    market value, MINUS the observed risk-free rate. Subtracting the observable is
    what makes it a premium rather than a second discount rate in disguise.
    """
    solve = config.erp.implied_solve
    constituents_raw = aggregate.get("constituents") or []
    default_growth = aggregate.get("growth")
    growth_low, growth_high = solve.growth_bounds

    constituents: list[dict[str, float]] = []
    total_cap = 0.0
    for item in constituents_raw:
        cash_flow = item.get("cash_flow")
        market_cap = item.get("market_cap")
        if not isinstance(cash_flow, int | float) or not isinstance(market_cap, int | float):
            continue
        if cash_flow <= 0 or market_cap <= 0:
            continue
        growth = item.get("growth", default_growth)
        if not isinstance(growth, int | float):
            continue
        constituents.append(
            {
                "cash_flow": float(cash_flow),
                "market_cap": float(market_cap),
                "growth": float(min(max(growth, growth_low), growth_high)),
            }
        )
        total_cap += float(market_cap)

    if len(constituents) < solve.min_constituents:
        return (
            None,
            None,
            "insufficient_constituents",
            f"equity aggregate carries {len(constituents)} usable constituents; "
            f"{solve.min_constituents} required",
        )
    coverage = aggregate.get("coverage_share")
    if isinstance(coverage, int | float) and float(coverage) < solve.min_coverage_share:
        return (
            None,
            None,
            "insufficient_coverage",
            f"equity aggregate covers {float(coverage):.0%} of index cap; "
            f"{solve.min_coverage_share:.0%} required",
        )
    if nominal_10y is None:
        return None, None, "no_risk_free", "nominal 10y unavailable; ERP level undefined"
    if terminal_growth is None:
        return None, None, "no_terminal_growth", "terminal growth unavailable"

    coefficient = float(aggregate.get("cash_flow_to_market_cap_scale") or 1.0)
    target = total_cap / coefficient
    rate, status = solve_implied_rate(
        constituents=[
            {"cash_flow": item["cash_flow"], "growth": item["growth"]}
            for item in constituents
        ],
        target_value=target,
        terminal_growth=terminal_growth,
        rate_bounds=solve.rate_bounds,
        iterations=solve.iterations,
    )
    if rate is None:
        return None, None, status, "aggregate DCF reached no root inside the configured rate bounds"
    coe = rate
    erp = rate - nominal_10y
    note = (
        f"implied from {len(constituents)} constituents covering "
        f"{float(coverage):.0%} of the aggregate's own capitalisation"
        if isinstance(coverage, int | float)
        else f"implied from {len(constituents)} constituents"
    )
    return erp, coe, "ok", note


def erp_history_band(
    *,
    history_path: str | Path | None,
    config: CostOfCapitalConfig,
    as_of: pd.Timestamp,
) -> tuple[dict[str, float | None] | None, str | None, str | None]:
    """The documented ERP history as a distribution, plus its percentile framing.

    This is the anchor's documented DEGRADED mode for the equity risk premium: with no equity
    aggregate there is no implied ERP to publish, but a citable, versioned history still says
    what the premium has actually been. That is a materially different object from an implied
    value and is labelled as one -- `erp_source` becomes `regime_estimate` and
    `market_implied_erp` stays null.

    Returns (band, source_label, error_reason).
    """
    if not history_path:
        return None, None, "no ERP history path configured"
    path = Path(history_path)
    if not path.exists():
        return None, None, f"no ERP history file at {path}"
    frame = pd.read_csv(path)
    if "date" not in frame.columns or "erp" not in frame.columns:
        raise ValueError(f"ERP history {path} must have date and erp columns")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["erp"] = pd.to_numeric(frame["erp"], errors="coerce")
    frame = frame.dropna(subset=["date", "erp"])
    window_start = as_of - pd.DateOffset(years=config.erp.regime_estimate.lookback_years)
    frame = frame[frame["date"] >= window_start]
    if len(frame) < config.erp.regime_estimate.min_points:
        return None, None, (
            f"ERP history holds {len(frame)} points inside the "
            f"{config.erp.regime_estimate.lookback_years}y lookback; "
            f"{config.erp.regime_estimate.min_points} required"
        )
    values = frame["erp"].astype(float)
    quantiles = values.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    source = config.erp.regime_estimate.source_label
    if not source and "source" in frame.columns:
        labels = {str(v) for v in frame["source"].dropna().unique()}
        source = "; ".join(sorted(labels)) or None
    band = {
        "n": int(len(values)),
        "start": frame["date"].min().date().isoformat(),
        "end": frame["date"].max().date().isoformat(),
        "p10": round(float(quantiles.iloc[0]), 6),
        "p25": round(float(quantiles.iloc[1]), 6),
        "median": round(float(quantiles.iloc[2]), 6),
        "p75": round(float(quantiles.iloc[3]), 6),
        "p90": round(float(quantiles.iloc[4]), 6),
        "latest": round(float(values.iloc[-1]), 6),
        "min": round(float(values.min()), 6),
        "max": round(float(values.max()), 6),
    }
    return band, source, None


def erp_history_percentile(
    *,
    history_path: str | Path | None,
    current_erp: float | None,
    config: CostOfCapitalConfig,
    as_of: pd.Timestamp,
) -> tuple[float | None, str | None]:
    """Percentile of `current_erp` within a documented, versioned ERP history.

    Reads a long-format CSV (``date,erp``). With no history file, or with no measured
    current ERP to place, there is no percentile — and saying so is the answer.
    """
    if history_path is None or current_erp is None:
        return None, None
    path = Path(history_path)
    if not path.exists():
        return None, None
    frame = pd.read_csv(path)
    if "date" not in frame.columns or "erp" not in frame.columns:
        raise ValueError(f"ERP history {path} must have date and erp columns")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["erp"] = pd.to_numeric(frame["erp"], errors="coerce")
    frame = frame.dropna(subset=["date", "erp"])
    window_start = as_of - pd.DateOffset(years=config.erp.regime_estimate.lookback_years)
    frame = frame[frame["date"] >= window_start]
    if len(frame) < config.erp.regime_estimate.min_points:
        return None, (
            f"ERP history holds {len(frame)} points inside the "
            f"{config.erp.regime_estimate.lookback_years}y lookback; "
            f"{config.erp.regime_estimate.min_points} required"
        )
    percentile = float((frame["erp"] <= current_erp).mean())
    return percentile, None


# ── Sector loadings ───────────────────────────────────────────────────────────


def sector_loadings(
    *,
    prices: pd.DataFrame,
    proxies: dict[str, str],
    benchmark_ticker: str,
    config: CostOfCapitalConfig,
    as_of: pd.Timestamp,
) -> tuple[dict[str, float], str | None, list[str]]:
    """Sector equity beta vs the benchmark, from the local sector ETF panel.

    Published as INFORMATION. It deliberately does not overwrite any consumer's sector
    spread table: the consumer measured that spreads carry the ranking information
    while level shifts are ranking-neutral, so MRI owns the level and leaves the
    structure alone.

    Gated on a declared provenance. A beta computed off a file whose origin is unknown
    is not evidence, it is a number shaped like evidence, so the default configuration
    publishes none. The gate is checked BEFORE the data, so an unverified panel can
    never leak into a payload by being present.
    """
    reasons: list[str] = []
    if config.sector_loadings.price_panel_source != "stored_sector_proxy_prices":
        return {}, None, [
            "sector_loadings: no market-observed price panel is declared "
            "(cost_of_capital.sector_loadings.price_panel_source is 'none'), so no "
            "beta is derived. Betas from an unverified panel would be fabricated "
            "evidence, not measured evidence."
        ]
    if prices.empty:
        return {}, None, ["sector_loadings: no local sector ETF price panel available"]
    frame = prices.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["ticker", "date", "close"])
    frame = frame[frame["date"] <= as_of]
    window_start = as_of - pd.DateOffset(years=config.sector_loadings.window_years)
    frame = frame[frame["date"] >= window_start]
    if frame.empty:
        return {}, None, ["sector_loadings: price panel holds no observations in the window"]

    wide = frame.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
    returns = wide.pct_change().dropna(how="all")
    if benchmark_ticker not in returns.columns:
        return {}, None, [f"sector_loadings: benchmark {benchmark_ticker} absent from the panel"]
    benchmark = returns[benchmark_ticker]
    minimum = config.sector_loadings.min_return_observations

    loadings: dict[str, float] = {}
    skipped: list[str] = []
    for sector_id, ticker in sorted(proxies.items()):
        if ticker not in returns.columns:
            skipped.append(sector_id)
            continue
        paired = pd.DataFrame({"sector": returns[ticker], "benchmark": benchmark}).dropna()
        if len(paired) < minimum or paired["benchmark"].var(ddof=0) == 0:
            skipped.append(sector_id)
            continue
        # BOTH legs at the same ddof. pandas' cov() defaults to ddof=1, so pairing it
        # with var(ddof=0) inflates beta by a silent n/(n-1) — 0.25% at n=400, and far
        # more on a thin sample.
        covariance = paired["sector"].cov(paired["benchmark"])
        variance = paired["benchmark"].var(ddof=1)
        beta = float(covariance / variance)
        if not math.isfinite(beta):
            skipped.append(sector_id)
            continue
        loadings[sector_id] = round(beta, 3)
    if skipped:
        reasons.append(
            "sector_loadings: no usable beta for "
            f"{len(skipped)} sector(s) (thin or missing history): {', '.join(sorted(skipped))}"
        )
    if not loadings:
        return {}, None, reasons + ["sector_loadings: no sector produced a usable beta"]
    return loadings, f"beta_vs_{benchmark_ticker}_{config.sector_loadings.window_years}y", reasons


# ── Build ─────────────────────────────────────────────────────────────────────


def _risk_free_consistency(
    *,
    risk_free: dict[str, float | None],
    input_dates: dict[str, str],
    series_of: dict[str, str],
    config: RiskFreeConsistencyConfig,
    as_of: pd.Timestamp,
) -> tuple[list[str], bool]:
    """Check that the three curve legs actually describe ONE day's curve.

    Two independent tests, because they fail differently:

    * **Date spread** — the legs come from different series and are read independently, so a
      partially-refreshed store can pair a four-month-stale nominal yield with current TIPS.
      That is a data-freshness failure, and it produces a curve that never existed.
    * **Breakeven identity** — when the legs ARE contemporaneous, ``nominal - real`` must
      reproduce the published breakeven to within timing/liquidity noise. A wider gap means
      the numbers disagree about the same day, which no amount of freshness explains.

    Returns (reasons, inconsistent). `inconsistent` is True only for the identity failure: the
    published decomposition does not describe one curve, so the anchor must degrade rather
    than present the legs as a coherent whole.
    """
    reasons: list[str] = []
    dates = {
        leg: input_dates.get(series)
        for leg, series in series_of.items()
        if input_dates.get(series)
    }
    parsed = {leg: pd.Timestamp(value) for leg, value in dates.items() if value}
    if len(parsed) >= 2:
        spread = (max(parsed.values()) - min(parsed.values())).days
        if spread > config.max_leg_date_spread_days:
            detail = ", ".join(f"{leg}={dates[leg]}" for leg in sorted(dates))
            reasons.append(
                f"risk_free consistency: curve legs span {spread} days "
                f"(limit {config.max_leg_date_spread_days}); they are not one curve. {detail}"
            )

    nominal = risk_free.get("nominal_10y")
    real = risk_free.get("real_10y")
    breakeven = risk_free.get("breakeven_10y")
    if nominal is None or real is None or breakeven is None:
        return reasons, False
    implied = nominal - real
    gap = abs(implied - breakeven)
    if gap > config.breakeven_tolerance:
        reasons.append(
            f"risk_free consistency: nominal - real = {implied:.4f} but the published "
            f"breakeven is {breakeven:.4f} (gap {gap:.4f} > {config.breakeven_tolerance:.4f}); "
            "the decomposition does not describe a single day's curve"
        )
        return reasons, True
    return reasons, False


def build_cost_of_capital_anchor(
    *,
    observations: pd.DataFrame,
    prices: pd.DataFrame,
    proxies: dict[str, str],
    benchmark_ticker: str,
    config: AnchorConfig,
    as_of: pd.Timestamp,
    built_at: str,
    terminal_growth: float | None = None,
    scoring_mode: str = "calendar_asof",
    vintages: pd.DataFrame | None = None,
    source_files: list[str] | None = None,
) -> CostOfCapitalAnchor:
    coc = config.cost_of_capital
    input_dates: dict[str, str] = {}
    reasons: list[str] = []
    notes: list[str] = [WACC_EXCLUSION_NOTE]

    nominal = _latest_value(
        observations, coc.risk_free.nominal_10y, as_of, scoring_mode=scoring_mode,
        vintages=vintages, input_dates=input_dates, reasons=reasons, label="risk_free",
    )
    real = _latest_value(
        observations, coc.risk_free.real_10y, as_of, scoring_mode=scoring_mode,
        vintages=vintages, input_dates=input_dates, reasons=reasons, label="risk_free",
    )
    breakeven = _latest_value(
        observations, coc.risk_free.breakeven_10y, as_of, scoring_mode=scoring_mode,
        vintages=vintages, input_dates=input_dates, reasons=reasons, label="risk_free",
    )
    premium, premium_source = term_premium(
        observations=observations,
        config=coc,
        as_of=as_of,
        scoring_mode=scoring_mode,
        vintages=vintages,
        input_dates=input_dates,
        reasons=reasons,
    )
    if premium is None:
        premium_source = None

    cpi_yoy, cpi_date = yoy_change(
        observations, coc.inflation.cpi, as_of,
        periods=coc.inflation.yoy_periods, scoring_mode=scoring_mode, vintages=vintages,
    )
    pce_yoy, pce_date = yoy_change(
        observations, coc.inflation.pce, as_of,
        periods=coc.inflation.yoy_periods, scoring_mode=scoring_mode, vintages=vintages,
    )
    if cpi_date:
        input_dates[coc.inflation.cpi] = cpi_date
    if pce_date:
        input_dates[coc.inflation.pce] = pce_date
    if cpi_yoy is None:
        reasons.append(f"inflation: {coc.inflation.cpi} yoy unavailable")
    if pce_yoy is None:
        reasons.append(f"inflation: {coc.inflation.pce} yoy unavailable")

    # The growth anchor is built first and its terminal rate passed in, because the
    # implied discount rate is only comparable with the downstream DCF if both use
    # the same perpetual growth assumption.
    terminal = terminal_growth

    erp_source = "unavailable"
    erp_basis = "unavailable"
    implied_erp_value = None
    implied_coe_value = None
    market_implied_erp = None
    market_implied_coe = None
    aggregate_note = None
    aggregate_provenance = None
    aggregate_path = coc.erp.equity_aggregate_path
    if coc.erp.method == "implied" and aggregate_path and Path(aggregate_path).exists():
        try:
            aggregate = load_equity_aggregate(aggregate_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            reasons.append(f"erp: equity aggregate unusable ({exc})")
            aggregate = None
        if aggregate is not None:
            basis = str(aggregate.get("basis") or "universe")
            erp, coe, status, note = implied_erp(
                aggregate=aggregate,
                nominal_10y=nominal,
                config=coc,
                terminal_growth=terminal,
            )
            aggregate_note = note
            aggregate_provenance = {
                "path": str(aggregate_path),
                "basis": basis,
                "source": aggregate.get("source"),
                "coverage_share": aggregate.get("coverage_share"),
                "universe_stats": aggregate.get("universe_stats"),
            }
            if erp is not None and coe is not None:
                erp_source = "implied"
                erp_basis = "index" if basis == "index" else "universe"
                implied_erp_value = round(erp, 4)
                implied_coe_value = round(coe, 4)
                # `market_implied_*` is set ONLY for a true index aggregate. A universe solve
                # is a weaker claim and must not borrow the word "market".
                if erp_basis == "index":
                    market_implied_erp = implied_erp_value
                    market_implied_coe = implied_coe_value
                else:
                    notes.append(
                        f"erp: solved over a {erp_basis} aggregate "
                        f"({aggregate_provenance.get('coverage_share')} of the collapsed "
                        "universe's capitalisation), NOT an index. Published as implied_erp / "
                        "implied_cost_of_equity; market_implied_* stays null."
                    )
            else:
                reasons.append(f"erp: implied solve unavailable ({status}) -- {note}")
    elif coc.erp.method == "implied":
        reasons.append(
            "erp: implied method configured but no equity aggregate is available at "
            f"{aggregate_path!r}; MRI holds no equity data by design"
        )

    percentile, percentile_note = erp_history_percentile(
        history_path=coc.erp.regime_estimate.history_path,
        current_erp=implied_erp_value,
        config=coc,
        as_of=as_of,
    )
    if percentile_note:
        reasons.append(f"erp_percentile: {percentile_note}")

    # The documented degraded ERP: a citable history distribution, published when no implied
    # value could be measured. `market_implied_erp` STAYS NULL -- the whole point is that an
    # implied value and a historical estimate are different objects, and conflating them would
    # put an unmeasured number into a discount rate.
    erp_history, erp_history_source, erp_history_error = erp_history_band(
        history_path=coc.erp.regime_estimate.history_path,
        config=coc,
        as_of=as_of,
    )
    if erp_history is not None and implied_erp_value is None:
        erp_source = "regime_estimate"
        erp_basis = "regime_estimate"
        notes.append(
            "erp: no equity aggregate is available, so no implied ERP could be measured. "
            "Published instead is the documented historical ERP distribution "
            f"({erp_history['n']} observations, {erp_history['start']}..{erp_history['end']}, "
            f"source: {erp_history_source or 'unspecified'}). implied_erp is null by design."
        )
    elif erp_history is None and implied_erp_value is None:
        erp_source = "unavailable"
        erp_basis = "unavailable"
        if erp_history_error:
            reasons.append(f"erp_history: {erp_history_error}")
    if erp_history_error and implied_erp_value is not None:
        reasons.append(f"erp_history: {erp_history_error}")
    if aggregate_note:
        notes.append(f"erp: {aggregate_note}")

    loadings, loading_source, loading_reasons = sector_loadings(
        prices=prices,
        proxies=proxies,
        benchmark_ticker=benchmark_ticker,
        config=coc,
        as_of=as_of,
    )
    reasons.extend(loading_reasons)

    risk_free = {
        "nominal_10y": None if nominal is None else round(nominal, 6),
        "real_10y": None if real is None else round(real, 6),
        "breakeven_10y": None if breakeven is None else round(breakeven, 6),
        "term_premium": None if premium is None else round(premium, 6),
    }
    inflation = {
        "cpi_yoy": None if cpi_yoy is None else round(cpi_yoy, 4),
        "pce_yoy": None if pce_yoy is None else round(pce_yoy, 4),
    }
    curve_reasons, curve_inconsistent = _risk_free_consistency(
        risk_free=risk_free,
        input_dates=input_dates,
        series_of={
            "nominal_10y": coc.risk_free.nominal_10y.series,
            "real_10y": coc.risk_free.real_10y.series,
            "breakeven_10y": coc.risk_free.breakeven_10y.series,
        },
        config=coc.risk_free.consistency,
        as_of=as_of,
    )
    reasons.extend(curve_reasons)
    degraded = (
        any(value is None for value in risk_free.values())
        or erp_source == "unavailable"
        or curve_inconsistent
    )

    return CostOfCapitalAnchor(
        asof=as_of.date().isoformat(),
        built_at=built_at,
        risk_free=risk_free,
        inflation=inflation,
        term_premium_source=premium_source,
        erp_source=erp_source,
        erp_basis=erp_basis,
        implied_erp=implied_erp_value,
        implied_cost_of_equity=implied_coe_value,
        market_implied_erp=market_implied_erp,
        erp_percentile_vs_history=percentile,
        erp_history=erp_history,
        erp_history_source=erp_history_source,
        market_implied_coe=market_implied_coe,
        equity_aggregate=aggregate_provenance,
        sector_loadings=loadings,
        loading_source=loading_source,
        degraded=degraded,
        source_files=list(source_files or []),
        provenance=AnchorProvenance(
            scoring_mode=scoring_mode,
            input_dates=input_dates,
            degradation_reasons=reasons,
            notes=notes,
        ),
    )
