#!/usr/bin/env python3
"""S5 pre-registered measurement pack (docs/review_2026-09-22/MRI_S5_SPEC.md section 1).

MEASUREMENT ONLY. Opens two store COPIES read-only, never the live store, and never writes
config, src/ or outputs/. Reads config/phase_b_sources.yaml and config/dimension_composition.yaml
from the current working directory (the worktree), the same way every other CLI command in this
engine resolves its default relative config path.

Answers, for one candidate shadow dimension (real_rates | commodity | dollar):
  * the recession leg (a pre-registered in-memory counterfactual arm of the softmax `recession`
    regime, never written to any store);
  * the sector leg (the frozen S6.3 out-of-sample harness, `src/macro_engine/sectors/fit.py`,
    unedited, with a like-for-like masked panel and a paired ΔIC increment over C0);
  * the STOP rules of section 1.4;
  * the decision rules of section 1.3.

Usage:
    <python> scripts/measure_s5_pack.py --candidate {real_rates|commodity|dollar} \
        --db-path CAND --reference-db-path REF --out-dir DIR
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml

import macro_engine
from macro_engine.dimensions.composition import load_composition_registry
from macro_engine.regimes.config import RegimeDefinition, load_regime_config
from macro_engine.regimes.scoring import build_regimes_from_dimensions
from macro_engine.sectors.fit import (
    CORE_DIMENSION_IDS,
    build_annual_vintages,
    compute_annual_oos_acceptance,
    evaluate_promotion,
    prepare_cross_section_matrices,
    score_series_from_vintages,
    spearman_ic,
    summarize_ic_series,
)

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

# HARD RULE: the real live store this order must never open, read from directly, or measure
# against, regardless of the current working directory (risk 2, section 2).
_LIVE_STORE_PATH = Path(r"C:\Users\riper\stocks\macro-regime-indicator\data\macro_engine.duckdb")
_REPO_OUTPUTS_DIR = _REPO_ROOT / "outputs"

_DEFAULT_CONFIG_PATH = "config/phase_b_sources.yaml"
_DEFAULT_COMPOSITION_PATH = "config/dimension_composition.yaml"
_DEFAULT_NBER_PATH = "config/nber_recessions.yaml"

_ROUND_NDIGITS = 6

# ── section 1: module-level constants, pinned by tests/test_s5_pack.py ─────────────────────

EX_COVID = ("2020-02", "2021-06")
SENSITIVITY_BASES = {"A": ("2020-02", "2020-04"), "B": ("2020-02", "2020-12"), "none": None}
RECALL_FLOOR = 0.80
BAR_PRECISION = 0.40
EVIDENCE_MARGIN = 0.030
AUROC_TOL = 0.005
BRIER_TOL = 0.005
ARM_WEIGHT = 0.15
EXISTING_SCALE = 0.85
MIN_EX_COVID_RECESSIONS = 3
RESOLUTION_MIN = 0.95
SECTOR_T_MIN = 2.0
MULTIPLICITY_T = 2.39
SECTOR_FIRST_RETURN_DATE = "1999-01-01"
DESIGN_BASELINE = {
    "precision": 0.319149,
    "auroc": 0.9108,
    "brier": 0.0870,
    "c0_ic_3m": 0.028901,
    "c0_t_3m": 0.776364,
    "c0_n_3m": 227,
}
CANDIDATES = {
    "real_rates": {
        "features": ["real_rate_10y_level_z", "real_rate_10y_6m_change_z"],
        "valid_from": "2005-09-01",
        "recession_polarity": "positive",
    },
    "commodity": {
        "features": ["oil_wti_yoy_z"],
        "valid_from": "1990-01-01",
        "recession_polarity": "positive",
    },
    "dollar": {
        "features": ["usd_narrow_yoy_z"],
        "valid_from": "1990-01-01",
        "recession_polarity": "positive",
    },
}

# ── load scripts/probes/hmm_recession.py's metric functions, without re-implementing them ──

_HMM_SPEC = importlib.util.spec_from_file_location(
    "hmm_recession",
    _SCRIPT_DIR / "probes" / "hmm_recession.py",
)
_hmm_recession = importlib.util.module_from_spec(_HMM_SPEC)
_HMM_SPEC.loader.exec_module(_hmm_recession)  # type: ignore[union-attr]

precision_recall_curve = _hmm_recession.precision_recall_curve
best_precision_at_recall = _hmm_recession.best_precision_at_recall
auroc = _hmm_recession.auroc
brier_score = _hmm_recession.brier_score
argmax_spell_stats = _hmm_recession.argmax_spell_stats


# ── refusals (exit code 2) ──────────────────────────────────────────────────────────────────


def check_refusals(db_path: Path, reference_db_path: Path, out_dir: Path) -> str | None:
    db_path = Path(db_path)
    reference_db_path = Path(reference_db_path)
    out_dir = Path(out_dir)
    for label, path in (("--db-path", db_path), ("--reference-db-path", reference_db_path)):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved == _LIVE_STORE_PATH.resolve():
            return f"refused: {label} resolves to the live store ({_LIVE_STORE_PATH})"
    try:
        resolved_out = out_dir.resolve()
    except OSError:
        resolved_out = out_dir
    try:
        resolved_out.relative_to(_REPO_OUTPUTS_DIR.resolve())
        return f"refused: --out-dir is inside <repo>/outputs ({_REPO_OUTPUTS_DIR})"
    except (ValueError, OSError):
        pass
    try:
        if db_path.resolve() == reference_db_path.resolve():
            return "refused: --db-path equals --reference-db-path"
    except OSError:
        if str(db_path) == str(reference_db_path):
            return "refused: --db-path equals --reference-db-path"
    return None


# ── DB access ────────────────────────────────────────────────────────────────────────────────


def _read_table(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    return con.execute(f"select * from {table}").fetchdf()  # noqa: S608 -- table is a literal


def open_store(path: str | Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True)


# ── NBER recessions ──────────────────────────────────────────────────────────────────────────


def load_nber(path: str | Path = _DEFAULT_NBER_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def nber_recessions(config: dict[str, Any]) -> list[tuple[str, str]]:
    return [(item["start"], item["end"]) for item in config.get("nber_recessions", [])]


def nber_month_set(recessions: list[tuple[str, str]]) -> set[pd.Period]:
    months: set[pd.Period] = set()
    for start, end in recessions:
        months.update(pd.period_range(start, end, freq="M").tolist())
    return months


def ex_covid_recessions(recessions: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [r for r in recessions if r != ("2020-02", "2020-04")]


# ── decision rules (section 1.3), pure functions pinned by tests/test_s5_pack.py ───────────


def recession_verdict(
    *,
    eligible: bool,
    arm_precision: float | None,
    arm_auroc: float | None,
    arm_brier: float | None,
    base_precision: float | None,
    base_auroc: float | None,
    base_brier: float | None,
) -> str:
    if not eligible:
        return "INELIGIBLE_INFORMATION_ONLY"
    if (
        arm_precision is None
        or arm_auroc is None
        or arm_brier is None
        or base_precision is None
        or base_auroc is None
        or base_brier is None
    ):
        return "NO_EVIDENCE"
    auroc_ok = arm_auroc >= base_auroc - AUROC_TOL
    brier_ok = arm_brier <= base_brier + BRIER_TOL
    if arm_precision > BAR_PRECISION and auroc_ok and brier_ok:
        return "MEETS_BAR"
    if arm_precision >= base_precision + EVIDENCE_MARGIN and auroc_ok and brier_ok:
        return "EVIDENCE"
    return "NO_EVIDENCE"


def sector_verdict(mean_ic: float | None, t_stat: float | None, s_b_passed: bool) -> str:
    if mean_ic is None or not (mean_ic > 0):
        return "NO_IMPROVEMENT"
    s_a = t_stat is not None and t_stat >= SECTOR_T_MIN
    if s_a and s_b_passed:
        return "PASS"
    return "IMPROVES_NOT_SIGNIFICANT"


def sector_labels(verdict: str, t_stat: float | None, candidate_valid_from: str) -> list[str]:
    if verdict != "PASS":
        return []
    labels: list[str] = []
    if t_stat is None or t_stat < MULTIPLICITY_T:
        labels.append("PASS_NOT_MULTIPLICITY_ROBUST")
    if candidate_valid_from > SECTOR_FIRST_RETURN_DATE:
        labels.append("PASS_LATE_START_FORWARD_ONLY")
    return labels


def resolution_ratio(frame: pd.DataFrame, valid_from: str) -> float:
    """Fraction of calendar months from `valid_from` through the last observed evaluation
    month on which the feature resolves valid (S1, section 1.4). `frame` has columns
    `evaluation_date` and `valid`, one row per (evaluation_date, feature_id) already
    filtered to a single feature_id."""
    f = frame.copy()
    f["evaluation_date"] = pd.to_datetime(f["evaluation_date"])
    valid_from_ts = pd.Timestamp(valid_from)
    f = f[f["evaluation_date"] >= valid_from_ts]
    if f.empty:
        return 0.0
    last_month = f["evaluation_date"].max()
    total_months = len(pd.period_range(valid_from_ts, last_month, freq="M"))
    valid_months = pd.PeriodIndex(f.loc[f["valid"].fillna(False), "evaluation_date"], freq="M").unique()
    return len(valid_months) / total_months if total_months else 0.0


# ── recession leg ────────────────────────────────────────────────────────────────────────────


def _window_months(window: tuple[str, str] | None) -> set[pd.Period]:
    if window is None:
        return set()
    start, end = window
    return set(pd.period_range(start, end, freq="M").tolist())


def evaluate_recession_basis(
    periods: np.ndarray,
    probs: np.ndarray,
    nber_months_all: set[pd.Period],
    window: tuple[str, str] | None,
) -> dict[str, Any]:
    excluded = _window_months(window)
    keep = np.array([pd.Period(p, freq="M") not in excluded for p in periods])
    periods_k = periods[keep]
    probs_k = probs[keep]
    labels = np.array([pd.Period(p, freq="M") in nber_months_all for p in periods_k])
    pr_curve = precision_recall_curve(probs_k, labels)
    best = best_precision_at_recall(pr_curve, RECALL_FLOOR)
    auc = auroc(probs_k, labels)
    brier = brier_score(probs_k, labels)
    threshold_25 = 0.25
    pred_25 = probs_k >= threshold_25
    tp25 = int(np.sum(pred_25 & labels))
    fp25 = int(np.sum(pred_25 & ~labels))
    fn25 = int(np.sum(~pred_25 & labels))
    precision_25 = tp25 / (tp25 + fp25) if (tp25 + fp25) else None
    recall_25 = tp25 / (tp25 + fn25) if (tp25 + fn25) else None
    return {
        "n_months": int(len(periods_k)),
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
        "precision_at_0_25": _r(precision_25) if precision_25 is not None else None,
        "recall_at_0_25": _r(recall_25) if recall_25 is not None else None,
    }


def detection_lead_lag(
    periods: np.ndarray,
    probs: np.ndarray,
    recessions: list[tuple[str, str]],
    threshold: float,
    window_months: int,
) -> list[dict[str, Any]]:
    period_index = pd.PeriodIndex([pd.Period(p, freq="M") for p in periods], freq="M")
    results = []
    for start, end in recessions:
        peak = pd.Period(start, freq="M")
        trough = pd.Period(end, freq="M")
        window_start = peak - window_months
        in_window = (period_index >= window_start) & (period_index <= trough)
        idx = np.where(in_window)[0]
        detected_period = None
        for i in idx:
            if probs[i] >= threshold:
                detected_period = period_index[i]
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


def build_recession_arm_regimes(
    base_regimes: list[RegimeDefinition], candidate: str
) -> list[RegimeDefinition]:
    """Arm R_k (section 1.1): the `recession` regime's five existing dimension weights
    multiplied by EXISTING_SCALE, plus the candidate at ARM_WEIGHT with its pre-registered
    polarity. Every other regime is returned unchanged (same object)."""
    arm_regimes: list[RegimeDefinition] = []
    for regime in base_regimes:
        if regime.regime_id != "recession":
            arm_regimes.append(regime)
            continue
        arm_dims = [
            {
                "dimension_id": dim.dimension_id,
                "weight": dim.weight * EXISTING_SCALE,
                "polarity": dim.polarity,
                "intercept": dim.intercept,
            }
            for dim in regime.dimensions
        ]
        arm_dims.append(
            {
                "dimension_id": candidate,
                "weight": ARM_WEIGHT,
                "polarity": CANDIDATES[candidate]["recession_polarity"],
                "intercept": 0.0,
            }
        )
        arm_regimes.append(
            RegimeDefinition.model_validate(
                {
                    "regime_id": regime.regime_id,
                    "enabled": regime.enabled,
                    "min_valid_dimensions": regime.min_valid_dimensions,
                    "min_coverage_ratio": regime.min_coverage_ratio,
                    "dimensions": arm_dims,
                }
            )
        )
    return arm_regimes


def recession_leg(
    *,
    candidate: str,
    cand_con: duckdb.DuckDBPyConnection,
    nber_config: dict[str, Any],
) -> dict[str, Any]:
    stop_reasons: list[str] = []

    dimension_scores_all_rows = _read_table(cand_con, "dimension_scores")
    regime_scores_stored = _read_table(cand_con, "regime_scores")

    regime_config = load_regime_config(_DEFAULT_CONFIG_PATH)

    # self-check (S5 STOP, part 1): the unmodified regimes must reproduce the stored
    # recession probability within 1e-12 on every valid month.
    baseline_full = build_regimes_from_dimensions(
        dimension_scores_all_rows, regime_config.regimes, regime_config.scoring
    )
    baseline_recession = baseline_full.regime_scores[
        baseline_full.regime_scores["regime_id"] == "recession"
    ].copy()
    stored_recession = regime_scores_stored[regime_scores_stored["regime_id"] == "recession"].copy()
    merged_check = baseline_recession.merge(
        stored_recession, on="date", suffixes=("_recomputed", "_stored")
    )
    merged_valid = merged_check[
        merged_check["valid_stored"] & merged_check["probability_stored"].notna()
    ]
    max_abs_diff = (
        float(
            (merged_valid["probability_recomputed"] - merged_valid["probability_stored"])
            .abs()
            .max()
        )
        if not merged_valid.empty
        else 0.0
    )
    if not (max_abs_diff <= 1e-12):
        stop_reasons.append(
            f"S5: self-check failed, recomputed baseline recession probability differs from "
            f"the stored value by up to {max_abs_diff} (> 1e-12)"
        )

    # the arm
    arm_regimes = build_recession_arm_regimes(regime_config.regimes, candidate)
    arm_full = build_regimes_from_dimensions(
        dimension_scores_all_rows, arm_regimes, regime_config.scoring
    )
    arm_recession = arm_full.regime_scores[arm_full.regime_scores["regime_id"] == "recession"].copy()

    # arm-neutral-where-invalid self-check (S5 STOP, part 2)
    candidate_rows = dimension_scores_all_rows[
        dimension_scores_all_rows["dimension_id"] == candidate
    ].copy()
    candidate_rows["date"] = pd.to_datetime(candidate_rows["date"]).dt.date
    invalid_candidate_dates = set(
        candidate_rows.loc[~candidate_rows["valid"].fillna(False), "date"]
    )
    all_dates_with_scores = set(pd.to_datetime(dimension_scores_all_rows["date"]).dt.date.unique())
    invalid_or_missing_dates = invalid_candidate_dates | (
        all_dates_with_scores - set(candidate_rows["date"])
    )
    cmp = baseline_recession.merge(arm_recession, on="date", suffixes=("_base", "_arm"))
    cmp_invalid = cmp[cmp["date"].isin(invalid_or_missing_dates)]
    if not cmp_invalid.empty:
        diffs = (cmp_invalid["probability_base"] - cmp_invalid["probability_arm"]).abs()
        diffs = diffs.combine_first(pd.Series(0.0, index=diffs.index))
        max_neutral_diff = float(diffs.max()) if not diffs.empty else 0.0
        if not (max_neutral_diff <= 1e-12):
            stop_reasons.append(
                f"S5: self-check failed, arm differs from baseline by up to {max_neutral_diff} "
                "on a month where the candidate is invalid (> 1e-12)"
            )

    # evaluation window: 1990-01-01 through the last evaluation month in the store, valid
    # rows and non-null probability only.
    def _prepared(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        f = frame.copy()
        f["date"] = pd.to_datetime(f["date"])
        f = f[(f["date"] >= "1990-01-01") & f["valid"] & f["probability"].notna()]
        f = f.sort_values("date")
        periods = pd.PeriodIndex(f["date"], freq="M").to_numpy()
        probs = f["probability"].to_numpy(dtype=float)
        return periods, probs

    base_periods, base_probs = _prepared(baseline_recession)
    arm_periods, arm_probs = _prepared(arm_recession)

    recessions_all = nber_recessions(nber_config)
    recessions_ex_covid = ex_covid_recessions(recessions_all)
    nber_months_all = nber_month_set(recessions_all)
    detection_threshold = float(nber_config["benchmark"]["detection_threshold"])
    lead_lag_window = int(nber_config["benchmark"]["lead_lag_window_months"])

    valid_from = CANDIDATES[candidate]["valid_from"]
    n_eligible_recessions = sum(
        1 for start, _end in recessions_ex_covid if start >= valid_from
    )
    eligible = n_eligible_recessions >= MIN_EX_COVID_RECESSIONS

    bases: dict[str, Any] = {}
    all_bases = {"C": EX_COVID, **SENSITIVITY_BASES}
    for basis_name, window in all_bases.items():
        base_eval = evaluate_recession_basis(base_periods, base_probs, nber_months_all, window)
        arm_eval = evaluate_recession_basis(arm_periods, arm_probs, nber_months_all, window)
        bases[basis_name] = {"baseline": base_eval, "arm": arm_eval}

    governing = bases["C"]
    base_g = governing["baseline"]

    def _verdict_for(base: dict[str, Any], arm: dict[str, Any]) -> str:
        arm_best = arm["best_precision_at_recall_0_80"]
        base_best = base["best_precision_at_recall_0_80"]
        return recession_verdict(
            eligible=eligible,
            arm_precision=arm_best["precision"] if arm_best is not None else None,
            arm_auroc=arm["auroc"],
            arm_brier=arm["brier_score"],
            base_precision=base_best["precision"] if base_best is not None else None,
            base_auroc=base["auroc"],
            base_brier=base["brier_score"],
        )

    verdict_by_basis = {name: _verdict_for(b["baseline"], b["arm"]) for name, b in bases.items()}
    governing_verdict = verdict_by_basis["C"]
    verdict_flips = any(
        name != "C" and v != governing_verdict for name, v in verdict_by_basis.items()
    )

    drift_precision = None
    drift_auroc = None
    baseline_drift = False
    if base_g["best_precision_at_recall_0_80"] is not None:
        drift_precision = base_g["best_precision_at_recall_0_80"]["precision"] - DESIGN_BASELINE["precision"]
    if base_g["auroc"] is not None:
        drift_auroc = base_g["auroc"] - DESIGN_BASELINE["auroc"]
    if (drift_precision is not None and abs(drift_precision) > 0.01) or (
        drift_auroc is not None and abs(drift_auroc) > 0.005
    ):
        baseline_drift = True

    lead_lag = detection_lead_lag(
        base_periods, arm_probs if False else arm_probs, recessions_all, detection_threshold, lead_lag_window
    )

    base_argmax_states = _argmax_states(baseline_full.regime_scores)
    arm_argmax_states = _argmax_states(arm_full.regime_scores)

    return {
        "candidate": candidate,
        "eligible": eligible,
        "n_eligible_ex_covid_recessions": n_eligible_recessions,
        "valid_from": valid_from,
        "bases": bases,
        "verdict_by_basis": verdict_by_basis,
        "verdict": governing_verdict,
        "verdict_flips_with_covid_window": verdict_flips,
        "baseline_drift": baseline_drift,
        "baseline_drift_detail": {
            "delta_precision": _r(drift_precision) if drift_precision is not None else None,
            "delta_auroc": _r(drift_auroc) if drift_auroc is not None else None,
            "design_baseline": DESIGN_BASELINE,
        },
        "detection_lead_lag_governing_basis": lead_lag,
        "argmax_spell_stats": {
            "baseline": argmax_spell_stats(base_argmax_states),
            "arm": argmax_spell_stats(arm_argmax_states),
        },
        "stop_reasons": stop_reasons,
        "self_check_max_abs_diff": max_abs_diff,
    }


def _argmax_states(regime_scores: pd.DataFrame) -> np.ndarray:
    frame = regime_scores.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["valid"] & frame["probability"].notna()]
    if frame.empty:
        return np.array([])
    frame = frame.sort_values(["date", "probability"], ascending=[True, False])
    top = frame.groupby("date", as_index=False).first().sort_values("date")
    return top["regime_id"].to_numpy()


def build_masked_arms(
    dates: list[pd.Timestamp],
    sectors: list[str],
    returns_mat: np.ndarray,
    features_mat_6col: np.ndarray,
    baseline_mat: np.ndarray,
    candidate_dim_ids: list[str],
    horizon_months: int,
) -> dict[str, Any]:
    """Section 1.2's like-for-like mask: a date is kept only when every one of the six
    (5 core + candidate) columns is observed. C0 uses the first 5 masked columns, C_k uses
    all 6 -- both arms therefore train and evaluate on identical dates. Pure: no DB, no I/O."""
    mask = ~np.isnan(features_mat_6col).any(axis=1)
    dates_masked = [d for d, keep in zip(dates, mask) if keep]
    returns_masked = returns_mat[mask]
    baseline_masked = baseline_mat[mask]
    x6_masked = features_mat_6col[mask]

    arms: dict[str, Any] = {}
    arm_specs = {
        "C0": (CORE_DIMENSION_IDS, x6_masked[:, :5]),
        "Ck": (candidate_dim_ids, x6_masked),
    }
    vintage_scores: dict[str, dict[pd.Timestamp, float]] = {}
    for arm_name, (dim_ids, feat_mat) in arm_specs.items():
        acceptance = compute_annual_oos_acceptance(
            dates_masked, sectors, dim_ids, returns_masked, feat_mat, baseline_masked, horizon_months
        )
        arms[arm_name] = acceptance

        vintages = build_annual_vintages(
            dates_masked, sectors, dim_ids, returns_masked, feat_mat, horizon_months
        )
        scores, _active, _reasons = score_series_from_vintages(
            dates_masked, sectors, dim_ids, feat_mat, vintages
        )
        per_date_ic: dict[pd.Timestamp, float] = {}
        if vintages:
            lo, hi = vintages[0].vintage_date, vintages[-1].vintage_date
            for idx, d in enumerate(dates_masked):
                if not (lo <= d <= hi):
                    continue
                ic = spearman_ic(scores[idx], returns_masked[idx])
                if ic is not None:
                    per_date_ic[d] = ic
        vintage_scores[arm_name] = per_date_ic

    common_dates = sorted(set(vintage_scores["C0"]) & set(vintage_scores["Ck"]))
    delta = [vintage_scores["Ck"][d] - vintage_scores["C0"][d] for d in common_dates]
    paired = summarize_ic_series(delta, horizon_months)

    return {
        "dates_masked": dates_masked,
        "n_masked_dates": len(dates_masked),
        "C0_masked": arms["C0"],
        "Ck_masked": arms["Ck"],
        "paired_delta_ic": paired,
        "vintage_scores": vintage_scores,
    }


# ── sector leg ───────────────────────────────────────────────────────────────────────────────


def sector_leg(
    *,
    candidate: str,
    cand_con: duckdb.DuckDBPyConnection,
    ref_con: duckdb.DuckDBPyConnection,
) -> dict[str, Any]:
    stop_reasons: list[str] = []

    def _inputs(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame]:
        dims = _read_table(con, "dimension_scores")
        dims = dims[dims["valid"]]
        vr = _read_table(con, "sector_validation_returns")
        vr = vr[vr["valid"]]
        return dims, vr

    cand_dims, cand_vr = _inputs(cand_con)
    ref_dims, ref_vr = _inputs(ref_con)

    candidate_dim_ids = CORE_DIMENSION_IDS + [candidate]

    def _c0_full(dims: pd.DataFrame, vr: pd.DataFrame, horizon: int) -> dict[str, Any]:
        dates, sectors, returns_mat, features_mat, baseline_mat = prepare_cross_section_matrices(
            vr, dims, dimension_ids=CORE_DIMENSION_IDS, horizon_months=horizon
        )
        return compute_annual_oos_acceptance(
            dates, sectors, CORE_DIMENSION_IDS, returns_mat, features_mat, baseline_mat, horizon
        )

    horizons = {}
    for h in (1, 3):
        dates, sectors, returns_mat, features_mat, baseline_mat = prepare_cross_section_matrices(
            cand_vr, cand_dims, dimension_ids=candidate_dim_ids, horizon_months=h
        )
        masked = build_masked_arms(
            dates, sectors, returns_mat, features_mat, baseline_mat, candidate_dim_ids, h
        )
        c0_full = _c0_full(cand_dims, cand_vr, h)

        horizons[f"{h}m"] = {
            "C0_masked": masked["C0_masked"],
            "Ck_masked": masked["Ck_masked"],
            "C0_full_window_reference": c0_full,
            "paired_delta_ic": masked["paired_delta_ic"],
            "n_masked_dates": masked["n_masked_dates"],
        }

    # sector self-check (S5 STOP): C0 on the full panel must be identical between REF and CAND.
    for h in (1, 3):
        cand_c0 = _c0_full(cand_dims, cand_vr, h)
        ref_c0 = _c0_full(ref_dims, ref_vr, h)
        cand_ic = cand_c0["oos"]["mean_ic"] if cand_c0.get("oos") else None
        ref_ic = ref_c0["oos"]["mean_ic"] if ref_c0.get("oos") else None
        if (cand_ic is None) != (ref_ic is None):
            stop_reasons.append(f"S5: C0 full panel ({h}m) present in one store and absent in the other")
        elif cand_ic is not None and abs(cand_ic - ref_ic) > 1e-9:
            stop_reasons.append(
                f"S5: C0 full panel ({h}m) differs between REF ({ref_ic}) and CAND ({cand_ic})"
            )

    gate = horizons["3m"]
    paired_3m = gate["paired_delta_ic"]
    mean_ic = paired_3m["mean_ic"]
    t_stat = paired_3m["t_stat"]
    s_a = mean_ic is not None and mean_ic > 0 and t_stat is not None and t_stat >= SECTOR_T_MIN
    s_b = bool(evaluate_promotion(gate["Ck_masked"]).get("passed"))

    verdict = sector_verdict(mean_ic, t_stat, s_b)
    labels = sector_labels(verdict, t_stat, CANDIDATES[candidate]["valid_from"])

    return {
        "candidate": candidate,
        "horizons": horizons,
        "s_a_paired_significant": s_a,
        "s_b_evaluate_promotion_passed": s_b,
        "verdict": verdict,
        "labels": labels,
        "stop_reasons": stop_reasons,
    }


# ── STOP rules S1-S4 ────────────────────────────────────────────────────────────────────────


def stop_s1_resolution(cand_con: duckdb.DuckDBPyConnection, candidate: str) -> list[str]:
    reasons: list[str] = []
    frame = _read_table(cand_con, "asof_feature_values")
    valid_from = CANDIDATES[candidate]["valid_from"]
    for feature_id in CANDIDATES[candidate]["features"]:
        f = frame[frame["feature_id"] == feature_id]
        if f.empty:
            reasons.append(f"S1: feature {feature_id} has no asof_feature_values rows from its valid_from")
            continue
        resolution = resolution_ratio(f, valid_from)
        if resolution < RESOLUTION_MIN:
            reasons.append(
                f"S1: {feature_id} resolves on {resolution:.4f} of evaluation months "
                f"(< {RESOLUTION_MIN})"
            )
    return reasons


def stop_s2_first_valid_month(cand_con: duckdb.DuckDBPyConnection, candidate: str) -> list[str]:
    reasons: list[str] = []
    dims = _read_table(cand_con, "dimension_scores")
    dims = dims[(dims["dimension_id"] == candidate) & dims["valid"]]
    if dims.empty:
        reasons.append(f"S2: dimension {candidate} has no valid rows in dimension_scores")
        return reasons
    first_valid = pd.to_datetime(dims["date"]).min()
    registry = load_composition_registry(_DEFAULT_COMPOSITION_PATH)
    composition = registry.dimensions.get(candidate)
    if composition is None or not composition.segments:
        reasons.append(f"S2: dimension {candidate} is not registered in {_DEFAULT_COMPOSITION_PATH}")
        return reasons
    declared_valid_from = min(segment.valid_from for segment in composition.segments)
    if pd.Timestamp(first_valid).date() != declared_valid_from:
        reasons.append(
            f"S2: first valid evaluation month for {candidate} is {first_valid.date()}, "
            f"declared valid_from is {declared_valid_from}"
        )
    return reasons


def stop_s3_no_harm(
    cand_con: duckdb.DuckDBPyConnection, ref_con: duckdb.DuckDBPyConnection, candidate: str
) -> list[str]:
    reasons: list[str] = []
    cand_dims = _read_table(cand_con, "dimension_scores")
    ref_dims = _read_table(ref_con, "dimension_scores")
    cand_dims = cand_dims[cand_dims["dimension_id"] != candidate]
    merged = cand_dims.merge(
        ref_dims, on=["dimension_id", "date"], suffixes=("_cand", "_ref"), how="inner"
    )
    if len(merged) != len(ref_dims):
        reasons.append(
            "S3: dimension_scores row count for pre-existing dimensions does not match between "
            f"CAND ({len(merged)} matched) and REF ({len(ref_dims)})"
        )
    score_diff = (merged["score_cand"] - merged["score_ref"]).abs().max() if not merged.empty else 0.0
    if pd.notna(score_diff) and not (score_diff == 0.0 or score_diff <= 0.0):
        reasons.append(f"S3: dimension_scores.score differs by up to {score_diff}")
    if not merged.empty:
        if not (merged["valid_cand"] == merged["valid_ref"]).all():
            reasons.append("S3: dimension_scores.valid differs between CAND and REF")
        if not (merged["reason_cand"].fillna("") == merged["reason_ref"].fillna("")).all():
            reasons.append("S3: dimension_scores.reason differs between CAND and REF")
        if "composition_id" in merged.columns or (
            "composition_id_cand" in merged.columns and "composition_id_ref" in merged.columns
        ):
            left = merged.get("composition_id_cand", merged.get("composition_id"))
            right = merged.get("composition_id_ref", merged.get("composition_id"))
            if left is not None and right is not None:
                if not (left.fillna("") == right.fillna("")).all():
                    reasons.append("S3: dimension_scores.composition_id differs between CAND and REF")

    cand_regimes = _read_table(cand_con, "regime_scores")
    ref_regimes = _read_table(ref_con, "regime_scores")
    merged_r = cand_regimes.merge(
        ref_regimes, on=["regime_id", "date"], suffixes=("_cand", "_ref"), how="inner"
    )
    if len(merged_r) != len(ref_regimes):
        reasons.append(
            "S3: regime_scores row count does not match between CAND "
            f"({len(merged_r)} matched) and REF ({len(ref_regimes)})"
        )
    if not merged_r.empty:
        for col in ("raw_score", "probability"):
            diff = (merged_r[f"{col}_cand"] - merged_r[f"{col}_ref"]).abs().max()
            if pd.notna(diff) and diff > 0.0:
                reasons.append(f"S3: regime_scores.{col} differs by up to {diff}")
        if not (merged_r["valid_cand"] == merged_r["valid_ref"]).all():
            reasons.append("S3: regime_scores.valid differs between CAND and REF")
    return reasons


def stop_s4_undeclared_composition(cand_con: duckdb.DuckDBPyConnection) -> list[str]:
    dims = _read_table(cand_con, "dimension_scores")
    hits = dims[dims["reason"].fillna("").str.startswith("undeclared_composition:")]
    if hits.empty:
        return []
    examples = hits[["dimension_id", "date", "reason"]].head(5).to_dict(orient="records")
    return [f"S4: dimension_scores.reason starts with undeclared_composition: (examples: {examples})"]


# ── provenance / correlation ─────────────────────────────────────────────────────────────────


def git_head(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # pragma: no cover - defensive
        return None


def build_provenance(candidate: str) -> dict[str, Any]:
    return {
        "git_head_worktree": git_head(_REPO_ROOT),
        "worktree_root": str(_REPO_ROOT),
        "candidate": candidate,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "macro_engine_file": macro_engine.__file__,
        "disclosure": (
            "values are revised FRED data; point-in-time resolution governs publication "
            "timing only (AGENTS.md)"
        ),
    }


def correlation_with_other_dimensions(
    cand_con: duckdb.DuckDBPyConnection, candidate: str
) -> dict[str, float | None]:
    dims = _read_table(cand_con, "dimension_scores")
    dims = dims[dims["valid"]]
    pivot = dims.pivot_table(index="date", columns="dimension_id", values="score", aggfunc="first")
    if candidate not in pivot.columns:
        return {}
    result: dict[str, float | None] = {}
    for other in pivot.columns:
        if other == candidate:
            continue
        pair = pivot[[candidate, other]].dropna()
        if len(pair) < 2:
            result[other] = None
            continue
        corr = pair[candidate].corr(pair[other])
        result[other] = _r(float(corr)) if pd.notna(corr) else None
    return result


# ── step outcome ────────────────────────────────────────────────────────────────────────────


def step_outcome(stop_reasons: list[str], recession_verdict: str, sector_verdict: str) -> str:
    if stop_reasons:
        return "STOP"
    if recession_verdict in ("MEETS_BAR", "EVIDENCE") or sector_verdict.startswith("PASS"):
        return "CANDIDATE_FOR_S5.k-b"
    return "SHADOW_INFORMATION_ONLY"


# ── shared helpers ──────────────────────────────────────────────────────────────────────────


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
        val = float(obj)
        return None if math.isnan(val) else val
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj.date())
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


# ── payload assembly ────────────────────────────────────────────────────────────────────────


def build_pack(candidate: str, db_path: str, reference_db_path: str) -> dict[str, Any]:
    cand_con = open_store(db_path)
    ref_con = open_store(reference_db_path)
    try:
        nber_config = load_nber()

        recession = recession_leg(candidate=candidate, cand_con=cand_con, nber_config=nber_config)
        sector = sector_leg(candidate=candidate, cand_con=cand_con, ref_con=ref_con)

        stop_reasons: list[str] = []
        stop_reasons.extend(stop_s1_resolution(cand_con, candidate))
        stop_reasons.extend(stop_s2_first_valid_month(cand_con, candidate))
        stop_reasons.extend(stop_s3_no_harm(cand_con, ref_con, candidate))
        stop_reasons.extend(stop_s4_undeclared_composition(cand_con))
        stop_reasons.extend(recession["stop_reasons"])
        stop_reasons.extend(sector["stop_reasons"])

        outcome = step_outcome(stop_reasons, recession["verdict"], sector["verdict"])

        no_harm = {
            "s1_resolution": [r for r in stop_reasons if r.startswith("S1:")],
            "s2_first_valid_month": [r for r in stop_reasons if r.startswith("S2:")],
            "s3_no_harm": [r for r in stop_reasons if r.startswith("S3:")],
            "s4_undeclared_composition": [r for r in stop_reasons if r.startswith("S4:")],
            "s5_self_check": [r for r in stop_reasons if r.startswith("S5:")],
        }

        payload = {
            "provenance": build_provenance(candidate),
            "resolution": no_harm["s1_resolution"],
            "no_harm": no_harm,
            "correlation": correlation_with_other_dimensions(cand_con, candidate),
            "recession": recession,
            "sector": sector,
            "stop_reasons": stop_reasons,
            "step_outcome": outcome,
        }
        return _clean(payload)
    finally:
        cand_con.close()
        ref_con.close()


def _fmt(value: Any) -> str:
    return "n/a" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)


def build_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# S5 measurement pack -- {payload['provenance']['candidate']}")
    lines.append("")
    lines.append(f"git HEAD (worktree): {payload['provenance']['git_head_worktree']}")
    lines.append(f"macro_engine.__file__: {payload['provenance']['macro_engine_file']}")
    lines.append(f"generated_at_utc: {payload['provenance']['generated_at_utc']}")
    lines.append("")
    lines.append(payload["provenance"]["disclosure"])
    lines.append("")
    lines.append(f"## Step outcome: {payload['step_outcome']}")
    lines.append("")
    if payload["stop_reasons"]:
        lines.append("### STOP reasons")
        for reason in payload["stop_reasons"]:
            lines.append(f"- {reason}")
        lines.append("")

    rec = payload["recession"]
    lines.append("## Recession leg")
    lines.append("")
    lines.append(f"eligible: {rec['eligible']} (n ex-COVID recessions with peak >= valid_from: "
                 f"{rec['n_eligible_ex_covid_recessions']})")
    lines.append(f"verdict (governing basis C): **{rec['verdict']}**")
    lines.append(f"verdict flips with covid window: {rec['verdict_flips_with_covid_window']}")
    lines.append(f"baseline drift vs design-time: {rec['baseline_drift']} {rec['baseline_drift_detail']}")
    lines.append("")
    lines.append("| basis | arm | baseline |")
    lines.append("| --- | --- | --- |")
    for basis_name, b in rec["bases"].items():
        arm_best = b["arm"]["best_precision_at_recall_0_80"]
        base_best = b["baseline"]["best_precision_at_recall_0_80"]
        lines.append(
            f"| {basis_name} | precision={_fmt(arm_best['precision'] if arm_best else None)} "
            f"auroc={_fmt(b['arm']['auroc'])} brier={_fmt(b['arm']['brier_score'])} "
            f"| precision={_fmt(base_best['precision'] if base_best else None)} "
            f"auroc={_fmt(b['baseline']['auroc'])} brier={_fmt(b['baseline']['brier_score'])} |"
        )
    lines.append("")

    sec = payload["sector"]
    lines.append("## Sector leg")
    lines.append("")
    lines.append(f"verdict: **{sec['verdict']}** {sec['labels']}")
    lines.append(f"S-a paired significant: {sec['s_a_paired_significant']}; "
                 f"S-b evaluate_promotion passed: {sec['s_b_evaluate_promotion_passed']}")
    gate = sec["horizons"]["3m"]
    lines.append(f"paired delta IC (3m): {gate['paired_delta_ic']}")
    lines.append("")

    lines.append("## Correlation with other dimensions")
    lines.append("")
    for dim_id, corr in payload["correlation"].items():
        lines.append(f"- {dim_id}: {_fmt(corr)}")
    lines.append("")
    lines.append("See `pack.json` for the full payload.")
    lines.append("")
    return "\n".join(lines)


def write_pack(payload: dict[str, Any], out_dir: str | Path) -> None:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    (target / "pack.json").write_text(json_text, encoding="utf-8")
    (target / "pack.md").write_text(build_markdown(payload), encoding="utf-8")


# ── entry point ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, choices=sorted(CANDIDATES))
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--reference-db-path", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    refusal = check_refusals(Path(args.db_path), Path(args.reference_db_path), Path(args.out_dir))
    if refusal is not None:
        print(refusal, file=sys.stderr)
        raise SystemExit(2)

    payload = build_pack(args.candidate, args.db_path, args.reference_db_path)
    write_pack(payload, args.out_dir)
    print(f"step_outcome: {payload['step_outcome']}")
    print(f"recession verdict: {payload['recession']['verdict']}")
    print(f"sector verdict: {payload['sector']['verdict']}")
    if payload["stop_reasons"]:
        print("STOP reasons:")
        for reason in payload["stop_reasons"]:
            print(f"  - {reason}")


if __name__ == "__main__":
    main()
