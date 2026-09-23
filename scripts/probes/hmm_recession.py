#!/usr/bin/env python3
"""
S3.3 offline probe -- HMM recession detection, forward-filtered, out-of-sample.

MEASUREMENT ONLY. This script does not modify src/, config/ or any existing script, and it
opens the live store read-only. It answers one question:

    Does the prototype-anchored sticky Gaussian HMM described in
    docs/review_2026-09-22/P0_0_MRI_TARGET_ARCHITECTURE.md section 2.3 clear the operator's
    bar -- ex-COVID out-of-sample best precision at recall >= 0.80 on the recession leg
    exceeds 0.40 -- and how does it compare to the current engine's softmax posterior
    (reported at 0.311 precision / recall 0.80 on the probability basis, 0.230 / 0.800 on
    the categorical label; docs/review_2026-09-22/reports/MRI_S1_APPROVAL.md answer 8 item 4)?

Model, exactly as specified in section 2.3:
  * K = 5 states (goldilocks, reflation, tightening, stagflation, recession), Gaussian
    emissions with diagonal covariance over the five regime dimensions (growth_momentum,
    inflation_pressure, policy_stance, credit_liquidity, yield_curve), standardised.
  * Prototype-anchored means (the operator's judgement table below). Two variants are
    measured: "pinned_means" (means never move) and "em_moved_means" (EM updates them from
    that initialisation).
  * Sticky Dirichlet prior on the transition diagonal (kappa = 10, section 2.3/S3.1 default).
  * A missing dimension is dropped from that month's emission likelihood; diagonal covariance
    makes this exact (independent factors -> the joint density factorises, so integrating out
    a missing factor is just omitting its term from the log-likelihood sum).
  * Yearly parameter vintages: refit once a year on an expanding window starting 1990-01 with
    a minimum of 180 months. Month m's probability uses the vintage fit strictly before m's
    year and the FORWARD filter (never smoothed) over observations through m.

No-look-ahead guarantee, stated exactly (see tests/test_probe_hmm_recession.py for the proof):
  1. Standardisation (mean/sd per dimension) for a given vintage is computed only from months
     strictly before that vintage's year; that fixed standardisation is then applied to every
     month scored under that vintage, past and future alike -- so no future value can shift
     where "0" sits for an earlier month.
  2. EM (forward-backward) fits each vintage's means/variances/transition matrix using only
     that same strictly-before-year training window. The backward pass never leaves that
     window, so fitting itself cannot see a future month.
  3. The published number for month m is the FORWARD filter's alpha_m: a strictly causal
     recursion (alpha_t is a function of alpha_{t-1}, the fixed vintage parameters, and the
     emission at t only -- never of alpha_{t+1} or anything later). Re-running the identical
     recursion with the observation series truncated at any t' >= m reproduces alpha_m to
     floating-point equality, because nothing after m ever enters the computation of alpha_m.
     test_forward_filter_no_lookahead proves this for the code below directly.

Usage:
    <python> scripts/probes/hmm_recession.py
    <python> scripts/probes/hmm_recession.py --db-path ... --out-dir outputs/probes
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent

DEFAULT_DB_PATH = r"C:\Users\riper\stocks\macro-regime-indicator\data\macro_engine.duckdb"
DEFAULT_OUT_DIR = "outputs/probes"
DEFAULT_NBER_CONFIG = "config/nber_recessions.yaml"

_ROUND_NDIGITS = 6

# ── the model, section 2.3 ───────────────────────────────────────────────────

REGIME_DIMENSIONS = [
    "growth_momentum",
    "inflation_pressure",
    "policy_stance",
    "credit_liquidity",
    "yield_curve",
]

STATES = ["goldilocks", "reflation", "tightening", "stagflation", "recession"]
RECESSION_STATE = "recession"

# Prototypes table, P0_0_MRI_TARGET_ARCHITECTURE.md section 2.3. Column order follows
# REGIME_DIMENSIONS (growth, inflation, policy, credit, curve).
PROTOTYPES: dict[str, list[float]] = {
    "goldilocks": [0.7, -0.7, 0.0, 0.7, 0.0],
    "reflation": [0.7, 0.7, 0.7, 0.7, 0.7],
    "tightening": [0.3, 0.7, -0.7, 0.0, -0.7],
    "stagflation": [-0.7, 0.7, -0.7, -0.7, -0.7],
    "recession": [-0.7, -0.7, 0.7, -0.7, 0.7],
}

MIN_TRAINING_MONTHS = 180
STICKY_KAPPA = 10.0
EM_MAX_ITER = 100
EM_TOL = 1e-8
VARIANCE_FLOOR = 1e-4
TRANSITION_FLOOR = 1e-8

RECALL_FLOOR = 0.80
OPERATOR_BAR_PRECISION = 0.40
COVID_MONTHS = ["2020-02", "2020-03", "2020-04"]

VARIANTS = ("pinned_means", "em_moved_means")


def prototypes_matrix() -> np.ndarray:
    return np.array([PROTOTYPES[state] for state in STATES], dtype=float)


# ── numeric primitives (numpy only) ─────────────────────────────────────────


def _logsumexp(a: np.ndarray, axis: int | None = None) -> np.ndarray:
    amax = np.max(a, axis=axis, keepdims=True)
    amax = np.where(np.isfinite(amax), amax, 0.0)
    out = amax + np.log(np.sum(np.exp(a - amax), axis=axis, keepdims=True))
    if axis is None:
        return out.reshape(())
    return np.squeeze(out, axis=axis)


def _softmax_rows(log_values: np.ndarray) -> np.ndarray:
    m = np.max(log_values, axis=-1, keepdims=True)
    e = np.exp(log_values - m)
    return e / e.sum(axis=-1, keepdims=True)


def _init_transition(k: int, self_transition: float = 0.85) -> np.ndarray:
    off = (1.0 - self_transition) / (k - 1)
    a = np.full((k, k), off)
    np.fill_diagonal(a, self_transition)
    return a


def emission_logprob(
    z: np.ndarray, mask: np.ndarray, means: np.ndarray, variances: np.ndarray
) -> np.ndarray:
    """Diagonal-Gaussian log-likelihood per state, per month, with missing dimensions dropped
    from the sum (independent factors -> exact marginalisation for a diagonal covariance)."""
    t_len = z.shape[0]
    k = means.shape[0]
    log_b = np.zeros((t_len, k))
    log2pi = np.log(2.0 * np.pi)
    z_filled = np.where(mask, z, 0.0)
    for state in range(k):
        diff2 = (z_filled - means[state]) ** 2
        per_dim_ll = -0.5 * (log2pi + np.log(variances[state])) - 0.5 * diff2 / variances[state]
        log_b[:, state] = np.where(mask, per_dim_ll, 0.0).sum(axis=1)
    return log_b


def forward_filter(
    log_b: np.ndarray, transition: np.ndarray, initial: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Strictly causal forward recursion. log_alpha[t] depends only on log_alpha[t-1],
    `transition`/`initial` (fixed inputs) and log_b[t] -- never on log_b[t'] for t' > t.
    Returns (log_alpha, log_c) where log_c[t] is the log of the per-step normaliser
    (its cumulative sum is the training-window log-likelihood when the sequence is the
    training window; on an evaluation pass it is simply discarded)."""
    t_len, k = log_b.shape
    log_alpha = np.empty((t_len, k))
    log_c = np.empty(t_len)
    log_a = np.log(transition)
    row = np.log(initial) + log_b[0]
    log_c[0] = _logsumexp(row)
    log_alpha[0] = row - log_c[0]
    for t in range(1, t_len):
        combined = log_alpha[t - 1][:, None] + log_a
        pred = _logsumexp(combined, axis=0)
        row = pred + log_b[t]
        log_c[t] = _logsumexp(row)
        log_alpha[t] = row - log_c[t]
    return log_alpha, log_c


def backward_log(log_b: np.ndarray, transition: np.ndarray, log_c: np.ndarray) -> np.ndarray:
    t_len, k = log_b.shape
    log_beta = np.zeros((t_len, k))
    log_a = np.log(transition)
    for t in range(t_len - 2, -1, -1):
        combined = log_a + (log_b[t + 1] + log_beta[t + 1])[None, :]
        log_beta[t] = _logsumexp(combined, axis=1) - log_c[t + 1]
    return log_beta


def _softmax_flat(log_xi: np.ndarray) -> np.ndarray:
    t1, k, _ = log_xi.shape
    if t1 == 0:
        return log_xi
    flat = log_xi.reshape(t1, k * k)
    m = np.max(flat, axis=1, keepdims=True)
    e = np.exp(flat - m)
    norm = e / e.sum(axis=1, keepdims=True)
    return norm.reshape(t1, k, k)


def _m_step(
    z: np.ndarray,
    mask: np.ndarray,
    gamma: np.ndarray,
    xi: np.ndarray,
    means: np.ndarray,
    *,
    update_means: bool,
    kappa: float,
    var_floor: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    k = means.shape[0]
    pi_new = np.clip(gamma[0], TRANSITION_FLOOR, None)
    pi_new = pi_new / pi_new.sum()

    xi_sum = xi.sum(axis=0) if len(xi) else np.zeros((k, k))
    prior = np.eye(k) * kappa
    numer = xi_sum + prior
    a_new = numer / numer.sum(axis=1, keepdims=True)
    a_new = np.clip(a_new, TRANSITION_FLOOR, None)
    a_new = a_new / a_new.sum(axis=1, keepdims=True)

    z_filled = np.where(mask, z, 0.0)
    means_new = means.copy()
    variances_new = np.empty_like(means)
    for state in range(k):
        weight = gamma[:, state]
        for dim in range(means.shape[1]):
            wd = weight * mask[:, dim]
            wsum = wd.sum()
            if wsum < 1e-10:
                variances_new[state, dim] = 1.0
                continue
            if update_means:
                means_new[state, dim] = (wd * z_filled[:, dim]).sum() / wsum
            diff2 = (z_filled[:, dim] - means_new[state, dim]) ** 2
            variances_new[state, dim] = max((wd * diff2).sum() / wsum, var_floor)
    return pi_new, a_new, means_new, variances_new


def fit_hmm(
    z: np.ndarray,
    mask: np.ndarray,
    prototypes: np.ndarray,
    *,
    update_means: bool,
    kappa: float = STICKY_KAPPA,
    max_iter: int = EM_MAX_ITER,
    tol: float = EM_TOL,
    var_floor: float = VARIANCE_FLOOR,
) -> dict[str, Any]:
    """EM (Baum-Welch) fit on `z`/`mask` alone. Every step here -- E and M -- only ever reads
    rows of `z`/`mask` that the caller passed in; it has no access to anything else, which is
    what guarantees a vintage's parameters cannot depend on data the caller excluded."""
    k, _ = prototypes.shape
    means = prototypes.copy()
    variances = np.ones_like(prototypes)
    transition = _init_transition(k)
    initial = np.full(k, 1.0 / k)
    prev_ll = -np.inf
    loglik = prev_ll
    for _ in range(max_iter):
        log_b = emission_logprob(z, mask, means, variances)
        log_alpha, log_c = forward_filter(log_b, transition, initial)
        loglik = float(log_c.sum())
        log_beta = backward_log(log_b, transition, log_c)
        gamma = _softmax_rows(log_alpha + log_beta)

        t_len = z.shape[0]
        log_a = np.log(transition)
        log_xi = np.empty((max(t_len - 1, 0), k, k))
        for t in range(t_len - 1):
            log_xi[t] = (
                log_alpha[t][:, None]
                + log_a
                + (log_b[t + 1] + log_beta[t + 1])[None, :]
                - log_c[t + 1]
            )
        xi = _softmax_flat(log_xi)

        initial, transition, means, variances = _m_step(
            z, mask, gamma, xi, means, update_means=update_means, kappa=kappa, var_floor=var_floor
        )
        if abs(loglik - prev_ll) < tol:
            prev_ll = loglik
            break
        prev_ll = loglik
    return {
        "means": means,
        "variances": variances,
        "transition": transition,
        "initial": initial,
        "loglik": prev_ll if math.isfinite(prev_ll) else loglik,
    }


def standardize(
    scores: np.ndarray, mask: np.ndarray, train_end_exclusive: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mean/sd per dimension computed ONLY from rows [0, train_end_exclusive) -- i.e. only
    months strictly before the vintage's year. Applied to every row afterwards, so the
    standardisation itself cannot see a future month."""
    train_scores = scores[:train_end_exclusive]
    train_mask = mask[:train_end_exclusive]
    n_dims = scores.shape[1]
    means = np.zeros(n_dims)
    stds = np.ones(n_dims)
    for dim in range(n_dims):
        valid = train_scores[train_mask[:, dim], dim]
        if len(valid) == 0:
            continue
        means[dim] = valid.mean()
        sd = valid.std(ddof=0)
        stds[dim] = sd if sd > 1e-8 else 1.0
    z = (scores - means) / stds
    return z, means, stds


# ── yearly-vintage forward-filtered pipeline ────────────────────────────────


def warmup_year_for(years: np.ndarray, min_training_months: int = MIN_TRAINING_MONTHS) -> int:
    for year in sorted(set(years.tolist())):
        if int((years < year).sum()) >= min_training_months:
            return int(year)
    raise RuntimeError("insufficient history for any out-of-sample vintage")


def run_vintages(
    periods: pd.PeriodIndex,
    scores: np.ndarray,
    mask: np.ndarray,
    *,
    update_means: bool,
) -> tuple[int, np.ndarray, dict[int, dict[str, Any]]]:
    years = periods.year.to_numpy()
    warmup_year = warmup_year_for(years)
    t_len = len(periods)
    k = len(STATES)
    prototypes = prototypes_matrix()

    filtered = np.full((t_len, k), np.nan)
    vintages: dict[int, dict[str, Any]] = {}

    for year in sorted(set(years.tolist())):
        if year < warmup_year:
            continue
        train_end = int((years < year).sum())
        if train_end < MIN_TRAINING_MONTHS:
            continue
        eval_end = int((years <= year).sum())

        z, _train_means, _train_stds = standardize(scores, mask, train_end)
        fit = fit_hmm(
            z[:train_end], mask[:train_end], prototypes, update_means=update_means
        )

        log_b_eval = emission_logprob(
            z[:eval_end], mask[:eval_end], fit["means"], fit["variances"]
        )
        log_alpha_eval, _log_c_eval = forward_filter(log_b_eval, fit["transition"], fit["initial"])
        alpha_eval = np.exp(log_alpha_eval)
        filtered[train_end:eval_end] = alpha_eval[train_end:eval_end]

        vintages[int(year)] = {
            "training_months": train_end,
            "training_start": str(periods[0]),
            "training_end": str(periods[train_end - 1]),
            "evaluated_months": eval_end - train_end,
            "training_loglik": _r(fit["loglik"]),
        }

    return warmup_year, filtered, vintages


# ── DB access (read-only) ───────────────────────────────────────────────────


def _read_dimension_matrix(db_path: str) -> tuple[pd.PeriodIndex, np.ndarray, np.ndarray]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        placeholders = ",".join("?" for _ in REGIME_DIMENSIONS)
        frame = con.execute(
            f"SELECT date, dimension_id, score, valid FROM dimension_scores "
            f"WHERE dimension_id IN ({placeholders})",
            REGIME_DIMENSIONS,
        ).fetchdf()
    finally:
        con.close()
    frame["date"] = pd.to_datetime(frame["date"])
    score_pivot = frame.pivot(index="date", columns="dimension_id", values="score")
    valid_pivot = frame.pivot(index="date", columns="dimension_id", values="valid")
    score_pivot = score_pivot[REGIME_DIMENSIONS].sort_index()
    valid_pivot = valid_pivot[REGIME_DIMENSIONS].sort_index().fillna(False).astype(bool)
    periods = pd.PeriodIndex(score_pivot.index, freq="M")
    mask = valid_pivot.to_numpy(dtype=bool)
    scores = np.where(mask, score_pivot.to_numpy(dtype=float), 0.0)
    return periods, scores, mask


def _read_current_engine(db_path: str) -> dict[str, Any]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        recession_prob = con.execute(
            "SELECT date, probability, valid FROM regime_scores WHERE regime_id = ? ORDER BY date",
            [RECESSION_STATE],
        ).fetchdf()
        timeline = con.execute(
            "SELECT date, raw_dominant_regime FROM historical_regime_timeline ORDER BY date"
        ).fetchdf()
    finally:
        con.close()
    recession_prob["date"] = pd.to_datetime(recession_prob["date"])
    timeline["date"] = pd.to_datetime(timeline["date"])
    return {
        "recession_probability": recession_prob,
        "timeline": timeline,
    }


def _load_nber_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _nber_recessions(config: dict[str, Any]) -> list[tuple[str, str]]:
    return [(item["start"], item["end"]) for item in config.get("nber_recessions", [])]


def _nber_month_set(recessions: list[tuple[str, str]]) -> set[pd.Period]:
    months: set[pd.Period] = set()
    for start, end in recessions:
        months.update(pd.period_range(start, end, freq="M").tolist())
    return months


# ── evaluation metrics ──────────────────────────────────────────────────────


def precision_recall_curve(probs: np.ndarray, labels: np.ndarray) -> list[dict[str, Any]]:
    thresholds = np.unique(probs)
    points = []
    for threshold in thresholds:
        pred = probs >= threshold
        tp = int(np.sum(pred & labels))
        fp = int(np.sum(pred & ~labels))
        fn = int(np.sum(~pred & labels))
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        points.append(
            {
                "threshold": _r(float(threshold)),
                "precision": _r(precision),
                "recall": _r(recall),
                "tp": tp,
                "fp": fp,
                "fn": fn,
            }
        )
    points.sort(key=lambda p: p["threshold"])
    return points


def best_precision_at_recall(
    points: list[dict[str, Any]], min_recall: float
) -> dict[str, Any] | None:
    candidates = [
        p
        for p in points
        if p["recall"] is not None and p["recall"] >= min_recall and p["precision"] is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p["precision"], p["recall"]))


def auroc(probs: np.ndarray, labels: np.ndarray) -> float | None:
    n_pos = int(labels.sum())
    n_neg = int(len(labels) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return None
    order = np.argsort(probs, kind="mergesort")
    sorted_probs = probs[order]
    n = len(sorted_probs)
    ranks = np.empty(n, dtype=float)
    i = 0
    rank_cursor = 1
    while i < n:
        j = i
        while j + 1 < n and sorted_probs[j + 1] == sorted_probs[i]:
            j += 1
        avg_rank = (rank_cursor + (rank_cursor + (j - i))) / 2.0
        ranks[order[i : j + 1]] = avg_rank
        rank_cursor += j - i + 1
        i = j + 1
    sum_ranks_pos = ranks[labels].sum()
    return float((sum_ranks_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def brier_score(probs: np.ndarray, labels: np.ndarray) -> float:
    return float(np.mean((probs - labels.astype(float)) ** 2))


def detection_lead_lag(
    periods: pd.PeriodIndex,
    probs: np.ndarray,
    recessions: list[tuple[str, str]],
    threshold: float,
    window_months: int,
) -> list[dict[str, Any]]:
    results = []
    for start, end in recessions:
        peak = pd.Period(start, freq="M")
        trough = pd.Period(end, freq="M")
        window_start = peak - window_months
        in_window = (periods >= window_start) & (periods <= trough)
        idx = np.where(in_window)[0]
        detected_period = None
        for i in idx:
            if probs[i] >= threshold:
                detected_period = periods[i]
                break
        if detected_period is None:
            results.append(
                {"recession_start": start, "recession_end": end, "detected": False, "lead_months": None}
            )
        else:
            lead = (peak - detected_period).n
            results.append(
                {
                    "recession_start": start,
                    "recession_end": end,
                    "detected": True,
                    "detected_month": str(detected_period),
                    "lead_months": int(lead),
                }
            )
    return results


def argmax_spell_stats(state_labels: np.ndarray) -> dict[str, Any]:
    if len(state_labels) == 0:
        return {"switch_count": 0, "n_spells": 0, "median_spell_months": None, "mean_spell_months": None}
    prev = np.roll(state_labels, 1)
    switch = state_labels != prev
    switch[0] = False
    switch_count = int(switch.sum())
    spell_group = np.cumsum(switch)
    _, counts = np.unique(spell_group, return_counts=True)
    spells = counts.astype(float)
    return {
        "switch_count": switch_count,
        "n_spells": int(len(spells)),
        "median_spell_months": _r(float(np.median(spells))),
        "mean_spell_months": _r(float(np.mean(spells))),
    }


def exclude_covid(periods: pd.PeriodIndex, *arrays: np.ndarray) -> tuple[np.ndarray, ...]:
    exclude = {pd.Period(m, freq="M") for m in COVID_MONTHS}
    keep = np.array([p not in exclude for p in periods])
    return tuple(a[keep] for a in (periods.to_numpy(), *arrays))


def evaluate_basis(
    periods: np.ndarray,
    probs: np.ndarray,
    nber_months: set[pd.Period],
    recessions: list[tuple[str, str]],
    *,
    detection_threshold: float,
    lead_lag_window_months: int,
) -> dict[str, Any]:
    labels = np.array([pd.Period(p, freq="M") in nber_months for p in periods])
    period_index = pd.PeriodIndex(periods, freq="M")
    pr_curve = precision_recall_curve(probs, labels)
    best = best_precision_at_recall(pr_curve, RECALL_FLOOR)
    auc = auroc(probs, labels)
    brier = brier_score(probs, labels)
    lead_lag = detection_lead_lag(
        period_index, probs, recessions, detection_threshold, lead_lag_window_months
    )
    return {
        "n_months": int(len(periods)),
        "n_positive_months": int(labels.sum()),
        "best_precision_at_recall_0_80": (
            {
                "precision": best["precision"],
                "recall": best["recall"],
                "threshold": best["threshold"],
                "tp": best["tp"],
                "fp": best["fp"],
                "fn": best["fn"],
            }
            if best is not None
            else None
        ),
        "auroc": _r(auc) if auc is not None else None,
        "brier_score": _r(brier),
        "precision_recall_curve": pr_curve,
        "detection_lead_lag": lead_lag,
    }


# ── shared numeric helpers ──────────────────────────────────────────────────


def _r(value: Any, ndigits: int = _ROUND_NDIGITS) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return round(float(value), ndigits)


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(key): _clean(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(item) for item in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        value = float(obj)
        return None if math.isnan(value) else value
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


# ── payload assembly ─────────────────────────────────────────────────────────


def build_probe_pack(
    *, db_path: str = DEFAULT_DB_PATH, nber_config_path: str | Path = DEFAULT_NBER_CONFIG
) -> dict[str, Any]:
    periods, scores, mask = _read_dimension_matrix(db_path)
    nber_config = _load_nber_config(nber_config_path)
    recessions_all = _nber_recessions(nber_config)
    recessions_ex_covid = [r for r in recessions_all if r != ("2020-02", "2020-04")]
    nber_months_all = _nber_month_set(recessions_all)
    detection_threshold = float(nber_config["benchmark"]["detection_threshold"])
    lead_lag_window_months = int(nber_config["benchmark"]["lead_lag_window_months"])
    recession_idx = STATES.index(RECESSION_STATE)

    current = _read_current_engine(db_path)

    variant_payloads: dict[str, Any] = {}
    warmup_year = None
    for variant in VARIANTS:
        update_means = variant == "em_moved_means"
        this_warmup_year, filtered, vintages = run_vintages(
            periods, scores, mask, update_means=update_means
        )
        warmup_year = this_warmup_year
        eval_mask = periods.year.to_numpy() >= warmup_year
        eval_periods = periods[eval_mask].to_numpy()
        eval_filtered = filtered[eval_mask]
        recession_probs = eval_filtered[:, recession_idx]
        argmax_states = np.array(STATES)[np.argmax(eval_filtered, axis=1)]

        ex_covid_periods, ex_covid_probs, ex_covid_argmax = exclude_covid(
            pd.PeriodIndex(eval_periods, freq="M"), recession_probs, argmax_states
        )

        variant_payloads[variant] = {
            "warmup_year": warmup_year,
            "vintages": {str(year): meta for year, meta in sorted(vintages.items())},
            "argmax_spell_length": argmax_spell_stats(argmax_states),
            "bases": {
                "all_four_recessions": evaluate_basis(
                    eval_periods,
                    recession_probs,
                    nber_months_all,
                    recessions_all,
                    detection_threshold=detection_threshold,
                    lead_lag_window_months=lead_lag_window_months,
                ),
                "ex_covid": {
                    **evaluate_basis(
                        ex_covid_periods,
                        ex_covid_probs,
                        nber_months_all,
                        recessions_ex_covid,
                        detection_threshold=detection_threshold,
                        lead_lag_window_months=lead_lag_window_months,
                    ),
                    "argmax_spell_length": argmax_spell_stats(ex_covid_argmax),
                },
            },
        }

    # current engine, same evaluation window, both bases
    rp = current["recession_probability"]
    rp = rp[pd.PeriodIndex(rp["date"], freq="M").year >= warmup_year]
    rp_valid = rp[rp["valid"] & rp["probability"].notna()]
    n_excluded = int(len(rp) - len(rp_valid))
    ce_periods = pd.PeriodIndex(rp_valid["date"], freq="M").to_numpy()
    ce_probs = rp_valid["probability"].to_numpy(dtype=float)

    tl = current["timeline"]
    tl = tl[pd.PeriodIndex(tl["date"], freq="M").year >= warmup_year]
    ce_argmax_full = tl["raw_dominant_regime"].fillna("invalid").to_numpy()

    ce_ex_covid_periods, ce_ex_covid_probs = exclude_covid(
        pd.PeriodIndex(ce_periods, freq="M"), ce_probs
    )
    tl_periods = pd.PeriodIndex(tl["date"], freq="M")
    ce_ex_covid_mask_periods, ce_ex_covid_argmax = exclude_covid(tl_periods, ce_argmax_full)

    current_payload = {
        "basis_note": (
            "regime_scores.probability WHERE regime_id='recession' AND valid, same evaluation "
            "window as the HMM (from the HMM warmup year onward); argmax_spell_length uses "
            "historical_regime_timeline.raw_dominant_regime (pre-transition-filter), for "
            "comparability with the HMM's unfiltered argmax -- NOT reported_regime, which "
            "carries the hysteresis filter the HMM has no equivalent of"
        ),
        "n_months_excluded_invalid_or_null_probability": n_excluded,
        "argmax_spell_length": argmax_spell_stats(ce_argmax_full),
        "bases": {
            "all_four_recessions": evaluate_basis(
                ce_periods,
                ce_probs,
                nber_months_all,
                recessions_all,
                detection_threshold=detection_threshold,
                lead_lag_window_months=lead_lag_window_months,
            ),
            "ex_covid": {
                **evaluate_basis(
                    ce_ex_covid_periods,
                    ce_ex_covid_probs,
                    nber_months_all,
                    recessions_ex_covid,
                    detection_threshold=detection_threshold,
                    lead_lag_window_months=lead_lag_window_months,
                ),
                "argmax_spell_length": argmax_spell_stats(ce_ex_covid_argmax),
            },
        },
    }

    verdict = _build_verdict(variant_payloads)

    payload: dict[str, Any] = {
        "comparison_instructions": (
            "hmm_recession.json must be byte-identical across consecutive runs against an "
            "unchanged database; diff it directly. No timestamp is written anywhere in this "
            "payload."
        ),
        "question": (
            "Does the section-2.3 prototype-anchored sticky Gaussian HMM, forward-filtered "
            "and fit on yearly out-of-sample parameter vintages, clear the operator's bar -- "
            "ex-COVID out-of-sample best precision at recall >= 0.80 on the recession leg "
            "exceeds 0.40 -- against the current engine's softmax posterior (0.311 at recall "
            "0.80 probability basis; 0.230 at recall 0.800 categorical label)?"
        ),
        "operator_bar": {
            "metric": "ex-COVID out-of-sample best precision at recall >= 0.80 on the recession leg",
            "bar": OPERATOR_BAR_PRECISION,
            "source": "MRI_S1_APPROVAL.md answer 8 item 4",
        },
        "no_look_ahead_guarantee": [
            "Standardisation (mean/sd per dimension) for a given vintage is computed only from "
            "months strictly before that vintage's year, and that fixed standardisation is then "
            "applied to every month scored under that vintage, past and future alike.",
            "EM (forward-backward) fits each vintage's means/variances/transition matrix using "
            "only that same strictly-before-year training window; the backward pass never "
            "leaves that window, so fitting itself cannot see a future month.",
            "The published number for month m is the FORWARD filter's alpha_m: a strictly "
            "causal recursion (alpha_t is a function of alpha_{t-1}, the fixed vintage "
            "parameters and the emission at t only). Re-running the identical recursion with "
            "the series truncated at any t' >= m reproduces alpha_m exactly -- proved directly "
            "for this code by tests/test_probe_hmm_recession.py::test_forward_filter_no_lookahead.",
        ],
        "model": {
            "states": STATES,
            "recession_state": RECESSION_STATE,
            "regime_dimensions": REGIME_DIMENSIONS,
            "prototypes": PROTOTYPES,
            "sticky_kappa": STICKY_KAPPA,
            "variance_floor": VARIANCE_FLOOR,
            "min_training_months": MIN_TRAINING_MONTHS,
            "vintage_scheme": (
                "yearly expanding window from 1990-01, refit annually; month m's probability "
                "uses the vintage fit strictly before m's year and the forward (never smoothed) "
                "filter over observations through m"
            ),
            "variants_measured": list(VARIANTS),
        },
        "warmup_year": warmup_year,
        "warmup_rationale": (
            f"first calendar year with >= {MIN_TRAINING_MONTHS} months of data strictly before "
            "it (an expanding window starting 1990-01); the vintage fit on that window is the "
            "first one with a legitimate out-of-sample claim, so evaluation starts at its first "
            "month"
        ),
        "nber_recessions_all": [{"start": s, "end": e} for s, e in recessions_all],
        "nber_recessions_ex_covid": [{"start": s, "end": e} for s, e in recessions_ex_covid],
        "detection_threshold": detection_threshold,
        "detection_threshold_source": (
            "config/nber_recessions.yaml: benchmark.detection_threshold (read, not modified)"
        ),
        "detection_lead_lag_window_months": lead_lag_window_months,
        "recall_floor": RECALL_FLOOR,
        "variants": variant_payloads,
        "current_engine": current_payload,
        "verdict": verdict,
    }
    return _clean(payload)


def _build_verdict(variant_payloads: dict[str, Any]) -> dict[str, Any]:
    per_variant: dict[str, Any] = {}
    for variant, payload in variant_payloads.items():
        best = payload["bases"]["ex_covid"]["best_precision_at_recall_0_80"]
        precision = best["precision"] if best is not None else None
        if precision is None:
            verdict_line = "DOES NOT CLEAR THE BAR: no threshold reaches recall >= 0.80 ex-COVID"
            shortfall = None
        elif precision > OPERATOR_BAR_PRECISION:
            verdict_line = "PASSES"
            shortfall = None
        else:
            verdict_line = "DOES NOT CLEAR THE BAR"
            shortfall = _r(OPERATOR_BAR_PRECISION - precision)
        per_variant[variant] = {
            "ex_covid_best_precision_at_recall_0_80": precision,
            "verdict": verdict_line,
            "shortfall_below_bar": shortfall,
        }

    scored = [(v, d["ex_covid_best_precision_at_recall_0_80"]) for v, d in per_variant.items()]
    scored = [(v, p) for v, p in scored if p is not None]
    if scored:
        best_variant, best_precision = max(scored, key=lambda item: item[1])
        overall_verdict = "PASSES" if best_precision > OPERATOR_BAR_PRECISION else "DOES NOT CLEAR THE BAR"
        overall_shortfall = (
            None if best_precision > OPERATOR_BAR_PRECISION else _r(OPERATOR_BAR_PRECISION - best_precision)
        )
    else:
        best_variant, best_precision, overall_verdict, overall_shortfall = None, None, "DOES NOT CLEAR THE BAR", None

    return {
        "rule": (
            "if ex-COVID out-of-sample best precision at recall >= 0.80 exceeds 0.40, PASSES; "
            "otherwise DOES NOT CLEAR THE BAR, stating the shortfall"
        ),
        "per_variant": per_variant,
        "overall": {
            "best_variant": best_variant,
            "ex_covid_best_precision_at_recall_0_80": best_precision,
            "verdict": overall_verdict,
            "shortfall_below_bar": overall_shortfall,
        },
    }


# ── output ───────────────────────────────────────────────────────────────────


def _write_pack(payload: dict[str, Any], out_dir: str | Path) -> None:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    (target / "hmm_recession.json").write_text(json_text, encoding="utf-8")
    (target / "hmm_recession.md").write_text(_build_markdown(payload), encoding="utf-8")


def _fmt(value: Any) -> str:
    return "n/a" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)


def _build_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# S3.3 offline probe -- HMM recession detection")
    lines.append("")
    lines.append(payload["question"])
    lines.append("")
    lines.append(f"Operator bar: {payload['operator_bar']['metric']} > {payload['operator_bar']['bar']}")
    lines.append("")
    lines.append(f"Warm-up year (first legitimate out-of-sample vintage): **{payload['warmup_year']}**")
    lines.append(f"({payload['warmup_rationale']})")
    lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append("| variant | ex-COVID best precision @ recall>=0.80 | verdict |")
    lines.append("| --- | --- | --- |")
    for variant, v in payload["verdict"]["per_variant"].items():
        lines.append(
            f"| {variant} | {_fmt(v['ex_covid_best_precision_at_recall_0_80'])} | {v['verdict']} |"
        )
    overall = payload["verdict"]["overall"]
    lines.append("")
    lines.append(
        f"**Overall: {overall['verdict']}** (best variant: {overall['best_variant']}, "
        f"precision {_fmt(overall['ex_covid_best_precision_at_recall_0_80'])})"
    )
    lines.append("")

    lines.append("## HMM vs current engine, both bases")
    lines.append("")
    lines.append("| series | basis | best precision @ recall>=0.80 | recall | AUROC | Brier |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for variant in payload["model"]["variants_measured"]:
        vp = payload["variants"][variant]
        for basis_key, basis_label in (("all_four_recessions", "all 4"), ("ex_covid", "ex-COVID")):
            basis = vp["bases"][basis_key]
            best = basis["best_precision_at_recall_0_80"]
            lines.append(
                f"| hmm:{variant} | {basis_label} | {_fmt(best['precision'] if best else None)} | "
                f"{_fmt(best['recall'] if best else None)} | {_fmt(basis['auroc'])} | "
                f"{_fmt(basis['brier_score'])} |"
            )
    ce = payload["current_engine"]
    for basis_key, basis_label in (("all_four_recessions", "all 4"), ("ex_covid", "ex-COVID")):
        basis = ce["bases"][basis_key]
        best = basis["best_precision_at_recall_0_80"]
        lines.append(
            f"| current_engine | {basis_label} | {_fmt(best['precision'] if best else None)} | "
            f"{_fmt(best['recall'] if best else None)} | {_fmt(basis['auroc'])} | "
            f"{_fmt(basis['brier_score'])} |"
        )
    lines.append("")

    lines.append("## Argmax spell length (evaluation window)")
    lines.append("")
    lines.append("| series | median months | mean months | switches |")
    lines.append("| --- | --- | --- | --- |")
    for variant in payload["model"]["variants_measured"]:
        s = payload["variants"][variant]["argmax_spell_length"]
        lines.append(
            f"| hmm:{variant} | {_fmt(s['median_spell_months'])} | {_fmt(s['mean_spell_months'])} | "
            f"{s['switch_count']} |"
        )
    s = ce["argmax_spell_length"]
    lines.append(
        f"| current_engine (raw_dominant_regime) | {_fmt(s['median_spell_months'])} | "
        f"{_fmt(s['mean_spell_months'])} | {s['switch_count']} |"
    )
    lines.append("")

    lines.append("## Detection lead/lag (ex-COVID basis)")
    lines.append("")
    for variant in payload["model"]["variants_measured"]:
        lines.append(f"**hmm:{variant}**")
        for row in payload["variants"][variant]["bases"]["ex_covid"]["detection_lead_lag"]:
            lines.append(f"- {row['recession_start']}..{row['recession_end']}: {row}")
        lines.append("")
    lines.append("**current_engine**")
    for row in ce["bases"]["ex_covid"]["detection_lead_lag"]:
        lines.append(f"- {row['recession_start']}..{row['recession_end']}: {row}")
    lines.append("")

    lines.append("See `hmm_recession.json` for the full payload: complete precision/recall")
    lines.append("curves, per-vintage training windows and log-likelihoods, and both bases in full.")
    lines.append("")
    return "\n".join(lines)


# ── entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--nber-config", default=DEFAULT_NBER_CONFIG)
    args = parser.parse_args()
    payload = build_probe_pack(db_path=args.db_path, nber_config_path=args.nber_config)
    _write_pack(payload, args.out_dir)
    overall = payload["verdict"]["overall"]
    print(f"verdict: {overall['verdict']} (precision={overall['ex_covid_best_precision_at_recall_0_80']})")


if __name__ == "__main__":
    main()
