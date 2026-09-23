"""S6.3: fitted sector exposures, published in shadow.

This module is the production home of the estimator `scripts/probes/sector_ceiling.py`
measured (MRI_PROBES_APPROVAL.md S3: "S6.3 is cheap ... because the probe IS most of
sectors/fit.py"). The probe's leakage-safe kernel (`fit_linear_model`,
`prepare_cross_section_matrices`, `compute_in_sample_ceiling`, `compute_out_of_sample`,
`compute_baseline`, `spearman_ic`, `summarize_ic_series`) lives here now; the probe imports
it rather than carrying a second copy.

The frozen specification (MRI_PROBES_APPROVAL.md S6.3-c, binding over
P0_0_MRI_TARGET_ARCHITECTURE.md S6.3's own wording where the two disagree):

- Plain OLS per sector, no shrinkage (`shrinkage_lambda=0.0`), shrink target zero -- not the
  hand-set exposures in `config/sector_exposures.yaml`.
- The 5 core regime dimensions only: growth_momentum, inflation_pressure, policy_stance,
  credit_liquidity, yield_curve.
- Cross-section pinned to the 11 published GICS sectors, never a subset (S1.5: dropping one
  sector "passes" at t 2.73; `prepare_cross_section_matrices` refuses a non-11 sector_ids
  argument unless the caller explicitly opts into a labelled sensitivity check).
- Refit cadence: annual, each January (operator ruling, since MRI_PROBES_APPROVAL.md left the
  choice open and only measured monthly [0.070, t 1.96] vs. yearly [0.050, t 1.42]).
- Vintage-built history: every historical fitted score comes from parameters fit only on
  training rows realised by the vintage date (d + horizon <= vintage_date), which is always
  on or before the date being scored -- never a full-sample fit.
- Gate only on out-of-sample dates, from the first legitimate vintage onward.
- Annual evaluation: the acceptance table (and the promotion verdict) is recomputed only when
  the active vintage advances (i.e. once a year, at the January refit), not every run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from macro_engine.reports.config import load_report_config
from macro_engine.sectors.config import SectorConfig, load_sector_config
from macro_engine.sectors.validation import newey_west_t
from macro_engine.storage.duckdb_store import DuckDBStore

SECTOR_FIT_DISCLAIMER = (
    "This is a shadow diagnostic. Fitted sector exposures are published as information only "
    "(mode: shadow, affects_quotas: false). They do not drive the screener's research quotas "
    "today; current_sector_ranking.json's validation block continues to describe the hand-set "
    "exposures until this shadow's acceptance measurement passes its gate (S6.3-d). Not "
    "investment advice."
)

# ── Frozen specification constants (S6.3-c) ────────────────────────────────────

GICS_11_SECTOR_IDS = [
    "communication_services",
    "consumer_discretionary",
    "consumer_staples",
    "energy",
    "financials",
    "health_care",
    "industrials",
    "information_technology",
    "materials",
    "real_estate",
    "utilities",
]

SUB_INDUSTRY_SECTOR_IDS = frozenset(
    {"semiconductors", "software", "banks", "biotech", "oil_gas_ep", "homebuilders"}
)

CORE_DIMENSION_IDS = [
    "growth_momentum",
    "inflation_pressure",
    "policy_stance",
    "credit_liquidity",
    "yield_curve",
]

GATING_HORIZON_MONTHS = 3
PUBLISHED_HORIZONS_MONTHS = [1, 3]
MIN_TRAINING_MONTHS = 60
MIN_SECTOR_OBSERVATIONS_TO_FIT = 10
REFIT_MONTH = 1  # January (operator ruling, stated in advance -- S6.3-c leaves this open)
_ROUND_NDIGITS = 6

# S6.3-d promotion thresholds -- each ships with the measurement that produced it
# (MRI_PROBES_APPROVAL.md S3, "Recommendation: S6.1-S6.3"):
PROMOTION_T_MIN = 2.0
PROMOTION_NON_OVERLAPPING_T_MIN = 1.5


# ── Kernel: leakage-safe estimation and evaluation (reused by scripts/probes/sector_ceiling.py) ──


def fit_linear_model(
    X: np.ndarray,
    y: np.ndarray,
    *,
    shrinkage_lambda: float = 0.0,
) -> tuple[float, np.ndarray]:
    """Fit a linear model with ridge-style shrinkage of loadings toward zero.

    The intercept alpha is unpenalized; loadings beta are shrunk by penalty lambda.
    When shrinkage_lambda == 0.0, this is ordinary least squares (OLS) -- the S6.3-c spec:
    no shrinkage, shrink target zero. The probe's own Table C sweeps shrinkage_lambda for a
    sensitivity comparison; the production fit (`build_annual_vintages`) always calls this
    with shrinkage_lambda=0.0.

    Returns:
        (alpha, beta) where alpha is a scalar intercept and beta is a loading vector.
    """
    n, p = X.shape
    if n == 0:
        return 0.0, np.zeros(p)

    x_mean = np.mean(X, axis=0)
    y_mean = float(np.mean(y))

    X_c = X - x_mean
    y_c = y - y_mean

    if shrinkage_lambda <= 0.0:
        beta, _, _, _ = np.linalg.lstsq(X_c, y_c, rcond=None)
    else:
        A = X_c.T @ X_c + shrinkage_lambda * np.eye(p)
        b = X_c.T @ y_c
        beta = np.linalg.solve(A, b)

    alpha = y_mean - float(np.dot(x_mean, beta))
    return alpha, beta


def spearman_ic(left: np.ndarray, right: np.ndarray) -> float | None:
    """Spearman rank correlation between two vectors, or None if degenerate."""
    mask = ~(np.isnan(left) | np.isnan(right))
    if np.sum(mask) < 2:
        return None
    x = left[mask]
    y = right[mask]

    r_x = pd.Series(x).rank().values
    r_y = pd.Series(y).rank().values

    std_x = np.std(r_x)
    std_y = np.std(r_y)
    if std_x <= 1e-12 or std_y <= 1e-12:
        return None

    corr = np.corrcoef(r_x, r_y)[0, 1]
    return None if np.isnan(corr) else float(corr)


def summarize_ic_series(
    ics: list[float],
    horizon_months: int,
    *,
    lag: int | None = None,
) -> dict[str, Any]:
    """Summarize a series of per-date rank ICs with Newey-West standard errors.

    `lag` defaults to horizon_months - 1 (the published gate's convention,
    `sectors/validation.py:597`); an explicit `lag` is used only by the S6.3-e NW-lag-3
    sensitivity row.
    """
    if not ics:
        return {
            "mean_ic": None,
            "sd_ic": None,
            "t_stat": None,
            "positive_share": None,
            "n_dates": 0,
        }

    arr = np.asarray(ics, dtype=float)
    n = len(arr)
    mean_ic = float(np.mean(arr))
    sd_ic = float(np.std(arr, ddof=1)) if n > 1 else None

    effective_lag = max(0, horizon_months - 1) if lag is None else max(0, lag)
    t_nw, _ = newey_west_t(list(arr), lag=effective_lag)
    pos_share = float(np.mean(arr > 0)) if n > 0 else None

    return {
        "mean_ic": _r(mean_ic),
        "sd_ic": _r(sd_ic),
        "t_stat": _r(t_nw),
        "positive_share": _r(pos_share),
        "n_dates": n,
    }


def _naive_t(values: list[float]) -> tuple[float | None, int]:
    """Naive (uncorrected) t-statistic, used only for the non-overlapping-quarter phases --
    each phase's series has no return-window overlap by construction, so Newey-West is not
    needed (MRI_PROBES_APPROVAL.md S1.4 reports these as "naive t")."""
    n = len(values)
    if n < 2:
        return None, n
    arr = np.asarray(values, dtype=float)
    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    if sd == 0:
        return None, n
    return mean / (sd / math.sqrt(n)), n


def prepare_cross_section_matrices(
    validation_returns: pd.DataFrame,
    dimension_scores: pd.DataFrame,
    *,
    dimension_ids: list[str],
    horizon_months: int,
    sector_ids: list[str] = GICS_11_SECTOR_IDS,
    feature_lag_months: int = 0,
    allow_subset: bool = False,
) -> tuple[list[pd.Timestamp], list[str], np.ndarray, np.ndarray, np.ndarray]:
    """Prepare aligned matrices for evaluation dates, sectors, returns, features, and
    hand-set baseline scores.

    The cross-section is pinned to `sector_ids` (the 11 published GICS sectors by default) --
    it is never derived from whichever sector ids happen to have valid rows on a given date.
    A caller cannot silently narrow the gate cross-section: passing fewer/more than the 11
    published ids without `allow_subset=True` raises. The S6.3-e leave-one-sector-out
    sensitivity row is the one caller that sets `allow_subset=True`, and its result is never
    used to decide promotion (S1.5: dropping information_technology alone "passes" at t 2.73).

    `feature_lag_months` shifts the dimension-score index forward by that many months before
    reindexing to the evaluation dates, so date T is scored on the dimension snapshot that was
    actually dated T - feature_lag_months (the S6.3-e "features one month older" row).

    Returns:
        dates: sorted evaluation timestamps
        sectors: `sector_ids`, in the order given (fixed, not data-derived)
        returns_mat: (n_dates, n_sectors) realised relative forward returns
        features_mat: (n_dates, n_features) dimension scores known at each evaluation date
        baseline_scores_mat: (n_dates, n_sectors) hand-set scores, for the same dates/sectors
    """
    if not allow_subset and set(sector_ids) != set(GICS_11_SECTOR_IDS):
        raise ValueError(
            "the gate cross-section is pinned to the 11 published GICS sectors "
            f"({sorted(GICS_11_SECTOR_IDS)}); got {sorted(sector_ids)}. Pass allow_subset=True "
            "for an explicit, separately-labelled sensitivity check that is never used to "
            "decide promotion (MRI_PROBES_APPROVAL.md S1.5)."
        )

    col = f"relative_forward_{horizon_months}m_return"
    sectors = list(sector_ids)
    g_returns = validation_returns[
        validation_returns["sector_id"].isin(set(sectors)) & validation_returns[col].notna()
    ].copy()

    g_returns["score_date"] = pd.to_datetime(g_returns["score_date"])
    dates = sorted(g_returns["score_date"].unique())

    dim_frame = dimension_scores.copy()
    dim_frame["date"] = pd.to_datetime(dim_frame["date"], errors="coerce")
    dim_pivot = dim_frame.pivot_table(index="date", columns="dimension_id", values="score", aggfunc="first")
    if feature_lag_months:
        dim_pivot = dim_pivot.copy()
        dim_pivot.index = dim_pivot.index + pd.DateOffset(months=feature_lag_months)

    features_mat = dim_pivot.reindex(index=dates)[dimension_ids].values

    returns_pivot = g_returns.pivot(index="score_date", columns="sector_id", values=col)
    returns_pivot = returns_pivot.reindex(index=dates, columns=sectors)
    returns_mat = returns_pivot.values

    score_col = "confidence_adjusted_score"
    scores_pivot = g_returns.pivot(index="score_date", columns="sector_id", values=score_col)
    scores_pivot = scores_pivot.reindex(index=dates, columns=sectors)
    baseline_scores_mat = scores_pivot.values

    return dates, sectors, returns_mat, features_mat, baseline_scores_mat


def compute_in_sample_ceiling(
    returns_mat: np.ndarray,
    features_mat: np.ndarray,
    horizon_months: int,
) -> dict[str, Any]:
    """Full-sample OLS fit per sector, scored on the data it was fit on (upper bound; used by
    the probe's Table A, never by the production S6.3 harness)."""
    n_dates, n_sectors = returns_mat.shape
    predicted_scores = np.full((n_dates, n_sectors), np.nan)

    for s_idx in range(n_sectors):
        y_sec = returns_mat[:, s_idx]
        valid_mask = ~np.isnan(y_sec)
        if np.sum(valid_mask) < MIN_SECTOR_OBSERVATIONS_TO_FIT:
            predicted_scores[:, s_idx] = 0.0
            continue

        X_valid = features_mat[valid_mask]
        y_valid = y_sec[valid_mask]

        alpha, beta = fit_linear_model(X_valid, y_valid, shrinkage_lambda=0.0)
        predicted_scores[:, s_idx] = alpha + features_mat @ beta

    ics: list[float] = []
    for t_idx in range(n_dates):
        ic = spearman_ic(predicted_scores[t_idx], returns_mat[t_idx])
        if ic is not None:
            ics.append(ic)

    return summarize_ic_series(ics, horizon_months)


def compute_out_of_sample(
    dates: list[pd.Timestamp],
    returns_mat: np.ndarray,
    features_mat: np.ndarray,
    horizon_months: int,
    *,
    shrinkage_lambda: float = 0.0,
    min_training_months: int = MIN_TRAINING_MONTHS,
) -> dict[str, Any]:
    """Monthly-refit expanding-window out-of-sample skill (the probe's Table B/C harness).

    Prevents lookahead and training-window leakage: for evaluation date T, an earlier date d
    is eligible for training if and only if d + horizon <= T. Kept for the probe's own
    monthly-cadence measurement; the production S6.3 fit uses `build_annual_vintages` instead
    (annual refit cadence, S6.3-c).
    """
    n_dates, n_sectors = returns_mat.shape
    dates_arr = np.array(dates)
    cutoff_dates = [d - pd.DateOffset(months=horizon_months) for d in dates]

    predicted_scores = np.full((n_dates, n_sectors), np.nan)
    evaluated_dates_mask = np.zeros(n_dates, dtype=bool)

    for t_idx, _ in enumerate(dates):
        train_mask = dates_arr <= cutoff_dates[t_idx]
        if np.sum(train_mask) < min_training_months:
            continue

        evaluated_dates_mask[t_idx] = True
        X_tr = features_mat[train_mask]
        x_te = features_mat[t_idx]

        for s_idx in range(n_sectors):
            y_tr_sec = returns_mat[train_mask, s_idx]
            valid_sec = ~np.isnan(y_tr_sec)

            if np.sum(valid_sec) < MIN_SECTOR_OBSERVATIONS_TO_FIT:
                predicted_scores[t_idx, s_idx] = 0.0
            else:
                alpha, beta = fit_linear_model(
                    X_tr[valid_sec],
                    y_tr_sec[valid_sec],
                    shrinkage_lambda=shrinkage_lambda,
                )
                predicted_scores[t_idx, s_idx] = alpha + float(np.dot(x_te, beta))

    ics: list[float] = []
    for t_idx in range(n_dates):
        if not evaluated_dates_mask[t_idx]:
            continue
        ic = spearman_ic(predicted_scores[t_idx], returns_mat[t_idx])
        if ic is not None:
            ics.append(ic)

    return summarize_ic_series(ics, horizon_months)


def compute_baseline(
    returns_mat: np.ndarray,
    baseline_scores_mat: np.ndarray,
    horizon_months: int,
    *,
    active_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """Rank IC of the hand-set scores, optionally restricted to `active_mask` dates."""
    n_dates = len(returns_mat)
    ics: list[float] = []
    for t_idx in range(n_dates):
        if active_mask is not None and not active_mask[t_idx]:
            continue
        ic = spearman_ic(baseline_scores_mat[t_idx], returns_mat[t_idx])
        if ic is not None:
            ics.append(ic)
    return summarize_ic_series(ics, horizon_months)


# ── Production estimator: annual vintages, no shrinkage, shrink target zero (S6.3-c) ──────


@dataclass(frozen=True)
class VintageFit:
    """One annual refit: parameters fit once, in January, using only training rows realised
    by `vintage_date` (d + horizon <= vintage_date). Active for every evaluation date from
    `vintage_date` up to (not including) the next vintage's date -- see `score_series_from_vintages`.
    """

    vintage_date: pd.Timestamp
    training_start: pd.Timestamp
    training_end: pd.Timestamp
    n_training_dates: int
    alphas: dict[str, float]
    betas: dict[str, dict[str, float]]
    n_obs: dict[str, int]
    status: dict[str, str]


def build_annual_vintages(
    dates: list[pd.Timestamp],
    sectors: list[str],
    dimension_ids: list[str],
    returns_mat: np.ndarray,
    features_mat: np.ndarray,
    horizon_months: int,
    *,
    min_training_months: int = MIN_TRAINING_MONTHS,
    refit_month: int = REFIT_MONTH,
    shrinkage_lambda: float = 0.0,
) -> list[VintageFit]:
    """Build the vintage-built history (S6.3-a): one OLS fit per sector at each legitimate
    January, using only rows with d + horizon <= vintage_date, requiring at least
    `min_training_months` eligible training dates. A vintage before that minimum is never
    built at all -- never a full-sample fit, and a date with no eligible vintage carries no
    fitted score.

    Structurally truncation-safe: fitting at `vintage_date` never inspects any date in `dates`
    later than `vintage_date` itself (it only ever reads `dates_arr <= cutoff`), so appending
    or removing dates after a given cut never changes an earlier vintage's parameters.
    """
    dates_arr = np.array(dates)
    candidate_dates = sorted({pd.Timestamp(d) for d in dates if pd.Timestamp(d).month == refit_month})

    vintages: list[VintageFit] = []
    for v_date in candidate_dates:
        cutoff = v_date - pd.DateOffset(months=horizon_months)
        train_mask = dates_arr <= cutoff
        n_training = int(np.sum(train_mask))
        if n_training < min_training_months:
            continue

        X_tr_all = features_mat[train_mask]
        training_dates_used = dates_arr[train_mask]
        alphas: dict[str, float] = {}
        betas: dict[str, dict[str, float]] = {}
        n_obs: dict[str, int] = {}
        status: dict[str, str] = {}

        # A row is eligible for fitting only when both its return and every one of its
        # features are observed -- `feature_lag_months` (S6.3-e "one month older") shifts the
        # feature index and leaves NaN rows at the boundary, which np.linalg.lstsq cannot
        # accept (it raises rather than silently propagating NaN).
        feature_valid = ~np.isnan(X_tr_all).any(axis=1)
        for s_idx, sector_id in enumerate(sectors):
            y_tr = returns_mat[train_mask, s_idx]
            valid = ~np.isnan(y_tr) & feature_valid
            n_obs[sector_id] = int(np.sum(valid))
            if n_obs[sector_id] < MIN_SECTOR_OBSERVATIONS_TO_FIT:
                status[sector_id] = "insufficient_sector_observations"
                alphas[sector_id] = 0.0
                betas[sector_id] = {dim_id: 0.0 for dim_id in dimension_ids}
                continue
            alpha, beta = fit_linear_model(
                X_tr_all[valid], y_tr[valid], shrinkage_lambda=shrinkage_lambda
            )
            alphas[sector_id] = float(alpha)
            betas[sector_id] = {dim_id: float(b) for dim_id, b in zip(dimension_ids, beta)}
            status[sector_id] = "fitted"

        vintages.append(
            VintageFit(
                vintage_date=pd.Timestamp(v_date),
                training_start=pd.Timestamp(training_dates_used.min()),
                training_end=pd.Timestamp(training_dates_used.max()),
                n_training_dates=n_training,
                alphas=alphas,
                betas=betas,
                n_obs=n_obs,
                status=status,
            )
        )
    return vintages


def score_series_from_vintages(
    dates: list[pd.Timestamp],
    sectors: list[str],
    dimension_ids: list[str],
    features_mat: np.ndarray,
    vintages: list[VintageFit],
) -> tuple[np.ndarray, list[pd.Timestamp | None], list[str | None]]:
    """Score every evaluation date under the vintage active on that date (the latest vintage
    with vintage_date <= date). A date before the first legitimate vintage gets no fitted
    score at all (never a full-sample fit standing in for a missing vintage)."""
    n_dates = len(dates)
    n_sectors = len(sectors)
    scores = np.full((n_dates, n_sectors), np.nan)
    active_vintage: list[pd.Timestamp | None] = [None] * n_dates
    reasons: list[str | None] = [None] * n_dates

    if not vintages:
        return scores, active_vintage, ["insufficient_vintage_history"] * n_dates

    vintage_dates = np.array([v.vintage_date for v in vintages])
    for t_idx, date in enumerate(dates):
        idx = int(np.searchsorted(vintage_dates, pd.Timestamp(date), side="right")) - 1
        if idx < 0:
            reasons[t_idx] = "insufficient_vintage_history"
            continue
        vintage = vintages[idx]
        active_vintage[t_idx] = vintage.vintage_date
        reasons[t_idx] = "ok"
        x_te = features_mat[t_idx]
        for s_idx, sector_id in enumerate(sectors):
            if vintage.status.get(sector_id) != "fitted":
                scores[t_idx, s_idx] = 0.0
                continue
            beta_vec = np.array([vintage.betas[sector_id][dim_id] for dim_id in dimension_ids])
            scores[t_idx, s_idx] = vintage.alphas[sector_id] + float(np.dot(x_te, beta_vec))
    return scores, active_vintage, reasons


def score_latest(
    dimension_scores: pd.DataFrame,
    vintages: list[VintageFit],
    dimension_ids: list[str],
    sectors: list[str],
) -> tuple[pd.Timestamp | None, dict[str, float | None], dict[str, str]]:
    """Today's fitted tilt: the vintage active on the most recent dimension-score date,
    applied to that date's own features. This is the cheap "daily" half of S6.3 -- the fit
    itself only changes once a year; scoring the latest snapshot against frozen parameters is
    a dot product per sector."""
    if dimension_scores.empty:
        return None, {s: None for s in sectors}, {s: "no_dimension_scores" for s in sectors}
    frame = dimension_scores.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    pivot = frame.pivot_table(index="date", columns="dimension_id", values="score", aggfunc="first")
    missing_dims = [dim_id for dim_id in dimension_ids if dim_id not in pivot.columns]
    if missing_dims:
        return None, {s: None for s in sectors}, {s: f"missing_dimension:{','.join(missing_dims)}" for s in sectors}
    complete = pivot.dropna(subset=dimension_ids)
    if complete.empty:
        return None, {s: None for s in sectors}, {s: "missing_dimension_score" for s in sectors}
    latest_date = pd.Timestamp(complete.index.max())

    if not vintages:
        return latest_date, {s: None for s in sectors}, {s: "insufficient_vintage_history" for s in sectors}
    vintage_dates = np.array([v.vintage_date for v in vintages])
    idx = int(np.searchsorted(vintage_dates, latest_date, side="right")) - 1
    if idx < 0:
        return latest_date, {s: None for s in sectors}, {s: "insufficient_vintage_history" for s in sectors}
    vintage = vintages[idx]

    x_latest = complete.loc[latest_date, dimension_ids].to_numpy(dtype=float)
    scores: dict[str, float | None] = {}
    reasons: dict[str, str] = {}
    for sector_id in sectors:
        if vintage.status.get(sector_id) != "fitted":
            scores[sector_id] = 0.0
            reasons[sector_id] = vintage.status.get(sector_id, "insufficient_sector_observations")
            continue
        beta_vec = np.array([vintage.betas[sector_id][dim_id] for dim_id in dimension_ids])
        scores[sector_id] = float(vintage.alphas[sector_id] + np.dot(x_latest, beta_vec))
        reasons[sector_id] = "ok"
    return latest_date, scores, reasons


# ── S6.3-b/d: out-of-sample acceptance measurement, evaluated at the annual refit ──────────


def compute_annual_oos_acceptance(
    dates: list[pd.Timestamp],
    sectors: list[str],
    dimension_ids: list[str],
    returns_mat: np.ndarray,
    features_mat: np.ndarray,
    baseline_scores_mat: np.ndarray,
    horizon_months: int,
    *,
    min_training_months: int = MIN_TRAINING_MONTHS,
    refit_month: int = REFIT_MONTH,
    nw_lag: int | None = None,
    include_non_overlapping: bool = True,
) -> dict[str, Any]:
    """S6.3-b: the gate, computed only on out-of-sample dates (from the first legitimate
    vintage onward). S6.3-d/"Annual evaluation": the OOS window's upper bound is the most
    recently completed vintage, not "today" -- re-running the pipeline later in the same
    year (before the next January vintage exists) reproduces the identical acceptance table,
    because the vintage list itself has not changed. That is what makes this "once a year, at
    the vintage refit" rather than a monthly re-check (MRI_PROBES_APPROVAL.md S3, S6.3-d).
    """
    vintages = build_annual_vintages(
        dates,
        sectors,
        dimension_ids,
        returns_mat,
        features_mat,
        horizon_months,
        min_training_months=min_training_months,
        refit_month=refit_month,
    )
    if not vintages:
        return {
            "vintages": [],
            "first_vintage_date": None,
            "latest_vintage_date": None,
            "oos": None,
            "reason": "no_legitimate_vintage",
        }

    scores, _active_vintage, _reasons = score_series_from_vintages(
        dates, sectors, dimension_ids, features_mat, vintages
    )
    dates_arr = np.array(dates)
    first_vintage_date = vintages[0].vintage_date
    latest_vintage_date = vintages[-1].vintage_date
    oos_mask = (dates_arr >= first_vintage_date) & (dates_arr <= latest_vintage_date)

    oos_dates: list[pd.Timestamp] = []
    ic_values: list[float] = []
    for idx in np.nonzero(oos_mask)[0]:
        ic = spearman_ic(scores[idx], returns_mat[idx])
        if ic is not None:
            oos_dates.append(dates[idx])
            ic_values.append(ic)

    summary = summarize_ic_series(ic_values, horizon_months, lag=nw_lag)
    hand_set_full = compute_baseline(returns_mat, baseline_scores_mat, horizon_months, active_mask=oos_mask)

    mid = len(oos_dates) // 2
    halves = {}
    for key, sl in (("first", slice(0, mid)), ("second", slice(mid, None))):
        half_ic = ic_values[sl]
        half_dates = oos_dates[sl]
        half_mask = np.isin(dates_arr, np.array(half_dates)) if half_dates else np.zeros(len(dates_arr), dtype=bool)
        halves[key] = {
            "fitted": summarize_ic_series(half_ic, horizon_months, lag=nw_lag),
            "hand_set_baseline": compute_baseline(
                returns_mat, baseline_scores_mat, horizon_months, active_mask=half_mask
            ),
            "date_start": str(half_dates[0].date()) if half_dates else None,
            "date_end": str(half_dates[-1].date()) if half_dates else None,
        }

    non_overlapping = None
    if include_non_overlapping and horizon_months == GATING_HORIZON_MONTHS:
        non_overlapping = {}
        for phase in range(3):
            phase_ic = ic_values[phase::3]
            t_stat, n = _naive_t(phase_ic)
            non_overlapping[f"phase_{phase}"] = {
                "t_stat": _r(t_stat),
                "mean_ic": _r(float(np.mean(phase_ic))) if phase_ic else None,
                "n_dates": n,
            }

    return {
        "vintages": [_vintage_summary(v) for v in vintages],
        "first_vintage_date": str(first_vintage_date.date()),
        "latest_vintage_date": str(latest_vintage_date.date()),
        "oos": {
            **summary,
            "halves": halves,
            "non_overlapping": non_overlapping,
            "hand_set_baseline": hand_set_full,
        },
    }


def _vintage_summary(vintage: VintageFit) -> dict[str, Any]:
    return {
        "vintage_date": str(vintage.vintage_date.date()),
        "training_start": str(vintage.training_start.date()),
        "training_end": str(vintage.training_end.date()),
        "n_training_dates": vintage.n_training_dates,
        "sectors": {
            sector_id: {
                "status": vintage.status[sector_id],
                "n_obs": vintage.n_obs[sector_id],
                "alpha": _r(vintage.alphas[sector_id]),
                "beta": {dim_id: _r(b) for dim_id, b in vintage.betas[sector_id].items()},
            }
            for sector_id in vintage.alphas
        },
    }


def evaluate_promotion(acceptance_3m: dict[str, Any]) -> dict[str, Any]:
    """S6.3-d: the promotion rule, evaluated only on the 3-month gating horizon's acceptance
    table. This never gates publication -- the shadow artifact always publishes the fitted
    scores and the acceptance table regardless of `passed`; it only decides whether the
    report may describe the fitted exposures as validated."""
    oos = acceptance_3m.get("oos")
    if not oos:
        return {"passed": False, "reason": acceptance_3m.get("reason", "no_legitimate_vintage"), "checks": {}}

    ic = oos["mean_ic"]
    t_stat = oos["t_stat"]
    ic_positive = ic is not None and ic > 0
    t_above_min = t_stat is not None and t_stat >= PROMOTION_T_MIN

    half_checks: dict[str, Any] = {}
    for key, half in oos["halves"].items():
        fitted_ic = half["fitted"]["mean_ic"]
        hand_set_ic = half["hand_set_baseline"]["mean_ic"]
        half_checks[key] = {
            "fitted_ic": fitted_ic,
            "hand_set_ic": hand_set_ic,
            "fitted_positive": fitted_ic is not None and fitted_ic > 0,
            "fitted_above_hand_set": (
                fitted_ic is not None and hand_set_ic is not None and fitted_ic > hand_set_ic
            ),
        }
    halves_pass = all(
        check["fitted_positive"] and check["fitted_above_hand_set"] for check in half_checks.values()
    ) and len(half_checks) == 2

    non_overlapping_checks: dict[str, Any] = {}
    non_overlapping_pass = oos["non_overlapping"] is not None
    if oos["non_overlapping"] is not None:
        for phase, row in oos["non_overlapping"].items():
            passed = row["t_stat"] is not None and row["t_stat"] >= PROMOTION_NON_OVERLAPPING_T_MIN
            non_overlapping_checks[phase] = {"t_stat": row["t_stat"], "passed": passed}
            non_overlapping_pass = non_overlapping_pass and passed

    passed = ic_positive and t_above_min and halves_pass and non_overlapping_pass
    return {
        "passed": passed,
        "checks": {
            "oos_ic_positive": ic_positive,
            "oos_t_stat": t_stat,
            "oos_t_above_min": t_above_min,
            "t_min": PROMOTION_T_MIN,
            "halves": half_checks,
            "halves_pass": halves_pass,
            "non_overlapping": non_overlapping_checks,
            "non_overlapping_pass": non_overlapping_pass,
            "non_overlapping_t_min": PROMOTION_NON_OVERLAPPING_T_MIN,
        },
    }


# ── S6.3-e: sensitivity rows, printed, never gated ─────────────────────────────────────────


def compute_sensitivity_rows(
    validation_returns: pd.DataFrame,
    dimension_scores: pd.DataFrame,
    *,
    horizon_months: int = GATING_HORIZON_MONTHS,
) -> dict[str, Any]:
    """S6.3-e: revised acceptance numbers under four variants, printed beside the frozen
    specification's own result -- never used to decide promotion.

    - no_nfci: drop credit_liquidity (the dimension NFCI feeds; MRI_PROBES_APPROVAL.md S1.1/C9).
    - nw_lag_3: same fit, Newey-West lag 3 instead of horizon - 1 = 2.
    - features_one_month_older: score date T scored on the dimension snapshot dated T - 1m.
    - leave_one_sector_out: the range of the 11-sector-cross-section 3m t-stat across 11 runs,
      each dropping one sector (S1.5's trap: dropping information_technology alone "passes").
    """
    rows: dict[str, Any] = {}

    def _standard(dimension_ids: list[str], feature_lag_months: int = 0, sector_ids: list[str] | None = None, allow_subset: bool = False) -> dict[str, Any]:
        dates, sectors, returns_mat, features_mat, baseline_mat = prepare_cross_section_matrices(
            validation_returns,
            dimension_scores,
            dimension_ids=dimension_ids,
            horizon_months=horizon_months,
            sector_ids=sector_ids or GICS_11_SECTOR_IDS,
            feature_lag_months=feature_lag_months,
            allow_subset=allow_subset,
        )
        return compute_annual_oos_acceptance(
            dates,
            sectors,
            dimension_ids,
            returns_mat,
            features_mat,
            baseline_mat,
            horizon_months,
            include_non_overlapping=False,
        )

    no_nfci_dims = [d for d in CORE_DIMENSION_IDS if d != "credit_liquidity"]
    no_nfci = _standard(no_nfci_dims)
    rows["no_nfci"] = {
        "dimensions": no_nfci_dims,
        "mean_ic": no_nfci["oos"]["mean_ic"] if no_nfci["oos"] else None,
        "t_stat": no_nfci["oos"]["t_stat"] if no_nfci["oos"] else None,
        "n_dates": no_nfci["oos"]["n_dates"] if no_nfci["oos"] else None,
    }

    dates, sectors, returns_mat, features_mat, baseline_mat = prepare_cross_section_matrices(
        validation_returns,
        dimension_scores,
        dimension_ids=CORE_DIMENSION_IDS,
        horizon_months=horizon_months,
    )
    lag3 = compute_annual_oos_acceptance(
        dates,
        sectors,
        CORE_DIMENSION_IDS,
        returns_mat,
        features_mat,
        baseline_mat,
        horizon_months,
        nw_lag=3,
        include_non_overlapping=False,
    )
    rows["nw_lag_3"] = {
        "mean_ic": lag3["oos"]["mean_ic"] if lag3["oos"] else None,
        "t_stat": lag3["oos"]["t_stat"] if lag3["oos"] else None,
        "n_dates": lag3["oos"]["n_dates"] if lag3["oos"] else None,
    }

    older = _standard(CORE_DIMENSION_IDS, feature_lag_months=1)
    rows["features_one_month_older"] = {
        "mean_ic": older["oos"]["mean_ic"] if older["oos"] else None,
        "t_stat": older["oos"]["t_stat"] if older["oos"] else None,
        "n_dates": older["oos"]["n_dates"] if older["oos"] else None,
    }

    loo_t: list[float] = []
    loo_by_sector: dict[str, float | None] = {}
    for dropped in GICS_11_SECTOR_IDS:
        restricted = [s for s in GICS_11_SECTOR_IDS if s != dropped]
        result = _standard(CORE_DIMENSION_IDS, sector_ids=restricted, allow_subset=True)
        t_stat = result["oos"]["t_stat"] if result["oos"] else None
        loo_by_sector[dropped] = t_stat
        if t_stat is not None:
            loo_t.append(t_stat)
    rows["leave_one_sector_out"] = {
        "note": (
            "sensitivity only -- never the gate cross-section (S1.5); reports the drop that "
            "produced each t so a spurious high t is traceable to which sector was removed"
        ),
        "t_stat_by_dropped_sector": {k: _r(v) for k, v in loo_by_sector.items()},
        "t_stat_min": _r(min(loo_t)) if loo_t else None,
        "t_stat_max": _r(max(loo_t)) if loo_t else None,
    }

    return rows


# ── Payload assembly and artifact writer ───────────────────────────────────────────────────


def build_sector_fit_payload(
    *,
    dimension_scores: pd.DataFrame,
    validation_returns: pd.DataFrame,
    sector_config: SectorConfig,
    horizons: list[int] = PUBLISHED_HORIZONS_MONTHS,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    """Pure assembly: no I/O. `write_sector_fit_report` is the thin DB/file wrapper."""
    sector_labels = {s.sector_id: s.label for s in sector_config.sectors if s.sector_id in GICS_11_SECTOR_IDS}

    if dimension_scores.empty or validation_returns.empty:
        return {
            "valid": False,
            "reason": "no_input_data",
            "mode": "shadow",
            "affects_quotas": False,
            "disclaimer": SECTOR_FIT_DISCLAIMER,
        }

    acceptance_by_horizon: dict[str, Any] = {}
    for horizon in horizons:
        dates, sectors, returns_mat, features_mat, baseline_mat = prepare_cross_section_matrices(
            validation_returns,
            dimension_scores,
            dimension_ids=CORE_DIMENSION_IDS,
            horizon_months=horizon,
        )
        acceptance_by_horizon[f"{horizon}m"] = compute_annual_oos_acceptance(
            dates,
            sectors,
            CORE_DIMENSION_IDS,
            returns_mat,
            features_mat,
            baseline_mat,
            horizon,
        )

    gating = acceptance_by_horizon[f"{GATING_HORIZON_MONTHS}m"]
    promotion = evaluate_promotion(gating)

    # Today's fitted tilt: computed from the same vintage list the gating horizon just built,
    # scored on the most recent dimension-score snapshot (cheap -- one dot product per sector).
    gate_dates, gate_sectors, gate_returns, gate_features, _gate_baseline = prepare_cross_section_matrices(
        validation_returns,
        dimension_scores,
        dimension_ids=CORE_DIMENSION_IDS,
        horizon_months=GATING_HORIZON_MONTHS,
    )
    vintages = build_annual_vintages(
        gate_dates, gate_sectors, CORE_DIMENSION_IDS, gate_returns, gate_features, GATING_HORIZON_MONTHS
    )
    latest_date, latest_scores, latest_reasons = score_latest(
        dimension_scores, vintages, CORE_DIMENSION_IDS, GICS_11_SECTOR_IDS
    )

    sensitivity = compute_sensitivity_rows(
        validation_returns, dimension_scores, horizon_months=GATING_HORIZON_MONTHS
    )

    sector_rows = []
    ranked = sorted(
        GICS_11_SECTOR_IDS,
        key=lambda sid: (-(latest_scores.get(sid) if latest_scores.get(sid) is not None else -math.inf), sid),
    )
    for rank, sector_id in enumerate(ranked, start=1):
        score = latest_scores.get(sector_id)
        sector_rows.append(
            {
                "sector_id": sector_id,
                "label": sector_labels.get(sector_id, sector_id),
                "rank": rank if score is not None else None,
                "fitted_score": _r(score) if score is not None else None,
                "exposure_source": "fitted_v2_shadow",
                "status": latest_reasons.get(sector_id, "unknown"),
            }
        )

    reasons: list[str] = []
    if latest_date is None:
        reasons.append("no_current_dimension_snapshot")
    if not vintages:
        reasons.append("no_legitimate_vintage_yet")

    return {
        "valid": True,
        "mode": "shadow",
        "affects_quotas": False,
        "schema_version": 1,
        "artifact": "sector_exposures_fitted",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_run_id": source_run_id,
        "asof": str(latest_date.date()) if latest_date is not None else None,
        "specification": {
            "estimator": "plain OLS per sector, no shrinkage, shrink target zero (S6.3-c)",
            "dimensions": CORE_DIMENSION_IDS,
            "cross_section": "gics_11 (pinned, never a subset)",
            "refit_cadence": "annual_january",
            "min_training_months": MIN_TRAINING_MONTHS,
            "min_sector_observations_to_fit": MIN_SECTOR_OBSERVATIONS_TO_FIT,
            "gating_horizon_months": GATING_HORIZON_MONTHS,
            "published_horizons_months": horizons,
        },
        "sector_ranking": sector_rows,
        "acceptance": acceptance_by_horizon,
        "promotion": {
            "gating_horizon_months": GATING_HORIZON_MONTHS,
            **promotion,
        },
        "sensitivity": sensitivity,
        "reasons": reasons,
        "disclaimer": SECTOR_FIT_DISCLAIMER,
    }


def write_sector_fit_report(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    sector_config_path: str | Path = "config/sectors.yaml",
    exposure_config_path: str | Path = "config/sector_exposures.yaml",
    prior_config_path: str | Path = "config/sector_regime_priors.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    """Write `outputs/sector_exposures_fitted.{json,md}` -- a NEW artifact, deliberately
    separate from `current_sector_ranking.json`. current_sector_ranking.json's `validation`
    block is what the screener's gate reads (P0_0_MRI_TARGET_ARCHITECTURE.md §7.1); this
    writer never opens that file and never calls `write_current_sector_report`, so there is no
    code path by which running (or not running) this shadow build can change a byte of it."""
    report_config = load_report_config(config_path)
    sector_config = load_sector_config(
        macro_config_path=config_path,
        sector_config_path=sector_config_path,
        exposure_config_path=exposure_config_path,
        prior_config_path=prior_config_path,
    )
    store = DuckDBStore(db_path)
    dimension_scores = store.read_table("dimension_scores")
    if not dimension_scores.empty and "valid" in dimension_scores.columns:
        dimension_scores = dimension_scores[dimension_scores["valid"]]
    validation_returns = store.read_table("sector_validation_returns")
    if not validation_returns.empty and "valid" in validation_returns.columns:
        validation_returns = validation_returns[validation_returns["valid"]]

    source_run_id = None
    sector_scores = store.read_table("sector_scores")
    if not sector_scores.empty and "source_run_id" in sector_scores.columns:
        run_ids = sector_scores["source_run_id"].dropna().unique()
        if len(run_ids) == 1:
            source_run_id = str(run_ids[0])

    payload = build_sector_fit_payload(
        dimension_scores=dimension_scores,
        validation_returns=validation_returns,
        sector_config=sector_config,
        source_run_id=source_run_id,
    )
    markdown = sector_fit_report_markdown(payload)

    output_dir = Path(report_config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "sector_exposures_fitted.json"
    markdown_path = output_dir / "sector_exposures_fitted.md"
    json_path.write_text(json.dumps(_clean_payload(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def sector_fit_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# Sector Exposures Fitted (Shadow)\n\nNOT PUBLISHED: {payload.get('reason')}\n\n{payload['disclaimer']}\n"

    gate = payload["acceptance"].get(f"{GATING_HORIZON_MONTHS}m", {})
    oos = gate.get("oos") or {}
    promo = payload["promotion"]
    ranking = "\n".join(
        f"- {row['rank'] if row['rank'] is not None else 'n/a'}. {row['label']} "
        f"({row['sector_id']}): fitted {row['fitted_score']} [{row['status']}]"
        for row in payload["sector_ranking"]
    )
    sensitivity = payload["sensitivity"]
    return f"""# Sector Exposures Fitted (Shadow)

mode: shadow -- affects_quotas: false
asof: {payload["asof"]}
built_at: {payload["built_at"]}

This is a shadow diagnostic. It does not change the screener's quota gate; see
`current_sector_ranking.json.validation` for the live (hand-set) block.

## Specification

{json.dumps(payload["specification"], indent=2)}

## Today's fitted tilt (informational, exposure_source: fitted_v2_shadow)

{ranking}

## Acceptance measurement -- {GATING_HORIZON_MONTHS}m gating horizon

- Out-of-sample window: {gate.get("first_vintage_date")} to {gate.get("latest_vintage_date")}
- Mean IC: {oos.get("mean_ic")}, Newey-West(2) t: {oos.get("t_stat")}, n dates: {oos.get("n_dates")}
- Hand-set baseline over the same dates: {oos.get("hand_set_baseline")}
- Halves: {json.dumps(oos.get("halves"), indent=2)}
- Non-overlapping quarters (naive t, 3 phases): {json.dumps(oos.get("non_overlapping"), indent=2)}

## Promotion (S6.3-d)

**passed: {promo["passed"]}**

{json.dumps(promo["checks"], indent=2)}

## Sensitivity rows (S6.3-e, printed, never gated)

{json.dumps(sensitivity, indent=2)}

{payload["disclaimer"]}
"""


def _r(value: Any, ndigits: int = _ROUND_NDIGITS) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(value), ndigits)


def _clean_payload(obj: Any) -> Any:
    """Recursively convert numpy/pandas scalar types to native Python and NaN to None."""
    if isinstance(obj, dict):
        return {str(k): _clean_payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_payload(item) for item in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        val = float(obj)
        return None if math.isnan(val) else val
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj
