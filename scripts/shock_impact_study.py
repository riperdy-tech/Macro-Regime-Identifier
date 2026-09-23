#!/usr/bin/env python3
"""MRI S4 — Shock Impact Study (Pre-registered).

Executes the three pre-registered impact tests per MRI_S4_PLAN.md:
  - T1: Recession leg (counterfactual regime scoring on governing basis ex-COVID 2020-02..2021-06
        and sensitivity bases; individual shock arms, combined growth shocks arm, and growth shocks + oil arm;
        stability guard on switches and median spell length).
  - T2: Sector leg (S6.3 OOS harness with signed shock severity features, paired ΔIC t at 3m horizon,
        half-sample positivity, and like-for-like masking).
  - T3: Forward-risk leg (predictive OLS on 5 regime probabilities + shock severity>=1 flag for
        forward 1m return, 3m return, 3m realized vol, and 3m max drawdown; Newey-West t, Holm correction
        across 7x4=28 tests, half-sample sign check, and economic size bars).

Outputs written to --out-dir (typically $SCR/s4_impact):
  - t1.json and t1.md
  - t2.json and t2.md
  - t3.json and t3.md
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

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
from macro_engine.shocks.config import compute_taxonomy_version, load_shocks_config

# Import statistical functions from HMM recession probe
import importlib.util

_PROBE_PATH = Path(__file__).resolve().parent / "probes" / "hmm_recession.py"
_spec = importlib.util.spec_from_file_location("hmm_recession", _PROBE_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load probe from {_PROBE_PATH}")
_hmm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hmm)

precision_recall_curve = _hmm.precision_recall_curve
best_precision_at_recall = _hmm.best_precision_at_recall
auroc = _hmm.auroc
brier_score = _hmm.brier_score
argmax_spell_stats = _hmm.argmax_spell_stats

# Pre-registered constants (MRI_S4_PLAN.md & MRI_S5_SPEC.md §1)
EX_COVID = ("2020-02", "2021-06")  # 17 months excluded on governing basis C
SENSITIVITY_BASES = {
    "A": ("2020-02", "2020-04"),  # NBER months only
    "B": ("2020-02", "2020-12"),  # Pandemic year
    "none": None,  # All months kept
}
RECALL_FLOOR = 0.80
SHIP_BAR_PRECISION = 0.40
EVIDENCE_MARGIN = 0.030
AUROC_TOL = 0.005
BRIER_TOL = 0.005
STABILITY_TOLERANCE = 0.10  # Switch count and median spell within 10%

SHOCK_IDS = [
    "volatility_shock",
    "credit_shock",
    "rates_shock",
    "oil_shock",
    "dollar_shock",
    "labour_shock",
    "inflation_shock",
]

# Growth-side shock signs for T1 (+ indicates raises recession risk):
# volatility (+), credit (+), labour (+), oil-down (+), rates-down (+), dollar-up (+), inflation-down (+)
GROWTH_SIDE_DIRECTIONS = {
    "volatility_shock": "up",
    "credit_shock": "up",
    "rates_shock": "down",
    "oil_shock": "down",
    "dollar_shock": "up",
    "labour_shock": "up",
    "inflation_shock": "down",
}


def _round(val: float | None, n: int = 6) -> float | None:
    return None if val is None or math.isnan(val) else round(float(val), n)


# -----------------------------------------------------------------------------
# Statistical & Econometric Kernels
# -----------------------------------------------------------------------------


def compute_newey_west_ols(
    X: np.ndarray, y: np.ndarray, lag: int = 0
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """OLS regression with Newey-West (Bartlett kernel) covariance matrix.

    Returns:
        beta: OLS coefficients (K,)
        se: Newey-West standard errors (K,)
        t_stat: t-statistics (K,)
        p_val: two-sided p-values (K,)
    """
    n, k = X.shape
    if n < k or n == 0:
        return (
            np.full(k, np.nan),
            np.full(k, np.nan),
            np.full(k, np.nan),
            np.full(k, np.nan),
        )

    xtx = X.T @ X
    try:
        xtx_inv = np.linalg.inv(xtx)
    except np.linalg.LinAlgError:
        xtx_inv = np.linalg.pinv(xtx)

    beta = xtx_inv @ (X.T @ y)
    e = y - X @ beta

    # S_0
    S = X * e[:, None]  # shape (n, k)
    gamma0 = S.T @ S
    omega = gamma0.copy()

    # Bartlett kernel weighting
    max_lag = min(lag, n - 1)
    for j in range(1, max_lag + 1):
        weight = 1.0 - (j / (max_lag + 1.0))
        gamma_j = S[j:].T @ S[:-j]
        omega += weight * (gamma_j + gamma_j.T)

    cov = xtx_inv @ omega @ xtx_inv
    var = np.diag(cov)
    # Numerical guard against small negative floating point values
    var = np.where(var > 0, var, np.nan)
    se = np.sqrt(var)

    with np.errstate(divide="ignore", invalid="ignore"):
        t_stat = np.where(se > 0, beta / se, np.nan)

    p_val = np.array(
        [
            math.erfc(abs(t) / math.sqrt(2.0)) if not np.isnan(t) else np.nan
            for t in t_stat
        ]
    )
    return beta, se, t_stat, p_val


def holm_bonferroni(p_values: list[float] | np.ndarray) -> np.ndarray:
    """Holm-Bonferroni step-down family-wise error rate correction.

    Guarantees monotonicity: adjusted p-value is non-decreasing in raw p-value.
    """
    arr = np.asarray(p_values, dtype=float)
    m = len(arr)
    if m == 0:
        return np.array([])

    order = np.argsort(arr)
    adj = np.zeros(m, dtype=float)

    running_max = 0.0
    for rank, orig_idx in enumerate(order):
        raw_p = arr[orig_idx]
        if np.isnan(raw_p):
            adj[orig_idx] = np.nan
            continue
        factor = m - rank
        val = min(1.0, factor * raw_p)
        running_max = max(running_max, val)
        adj[orig_idx] = running_max

    return adj


def _window_months(window: tuple[str, str] | None) -> set[pd.Period]:
    if window is None:
        return set()
    start, end = window
    return set(pd.period_range(start, end, freq="M").tolist())


def evaluate_recession_performance(
    periods: np.ndarray,
    probs: np.ndarray,
    nber_months_all: set[pd.Period],
    window: tuple[str, str] | None,
) -> dict[str, Any]:
    """Evaluate precision, recall, AUROC, and Brier score over a window."""
    excluded = _window_months(window)
    keep = np.array([pd.Period(p, freq="M") not in excluded for p in periods])
    periods_k = periods[keep]
    probs_k = probs[keep]
    labels = np.array([pd.Period(p, freq="M") in nber_months_all for p in periods_k])

    pr_curve = precision_recall_curve(probs_k, labels)
    best = best_precision_at_recall(pr_curve, RECALL_FLOOR)
    auc = auroc(probs_k, labels)
    brier = brier_score(probs_k, labels)

    pred_25 = probs_k >= 0.25
    tp25 = int(np.sum(pred_25 & labels))
    fp25 = int(np.sum(pred_25 & ~labels))
    fn25 = int(np.sum(~pred_25 & labels))
    prec_25 = tp25 / (tp25 + fp25) if (tp25 + fp25) else None
    rec_25 = tp25 / (tp25 + fn25) if (tp25 + fn25) else None

    return {
        "n_months": int(len(periods_k)),
        "n_positive_months": int(labels.sum()),
        "best_precision_at_recall_0_80": (
            {
                "precision": _round(best["precision"]),
                "recall": _round(best["recall"]),
                "threshold": _round(best["threshold"]),
                "tp": best["tp"],
                "fp": best["fp"],
                "fn": best["fn"],
            }
            if best is not None
            else None
        ),
        "auroc": _round(auc),
        "brier_score": _round(brier),
        "precision_at_0_25": _round(prec_25),
        "recall_at_0_25": _round(rec_25),
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


# -----------------------------------------------------------------------------
# T1 — Recession Leg
# -----------------------------------------------------------------------------


def run_t1_recession_leg(
    con: duckdb.DuckDBPyConnection,
    panel_df: pd.DataFrame,
    shocks_cfg: dict[str, Any],
    nber_config: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Execute T1 recession leg across all arms and sensitivity bases."""
    dims_all = con.execute("SELECT * FROM dimension_scores").fetchdf()
    reg_config = load_regime_config("config/phase_b_sources.yaml")
    recessions = [(r["start"], r["end"]) for r in nber_config.get("nber_recessions", [])]
    nber_months = set()
    for s, e in recessions:
        nber_months.update(pd.period_range(s, e, freq="M").tolist())

    # Baseline evaluation
    base_res = build_regimes_from_dimensions(dims_all, reg_config.regimes, reg_config.scoring)
    base_rec = base_res.regime_scores[base_res.regime_scores["regime_id"] == "recession"].copy()
    base_rec["date"] = pd.to_datetime(base_rec["date"])
    base_rec = base_rec[base_rec["valid"] & base_rec["probability"].notna()].sort_values("date")

    base_periods = base_rec["date"].dt.to_period("M").to_numpy()
    base_probs = base_rec["probability"].to_numpy()

    base_eval_c = evaluate_recession_performance(base_periods, base_probs, nber_months, EX_COVID)
    base_spell_stats = argmax_spell_stats(_argmax_states(base_res.regime_scores))

    # Build shock pseudo-dimensions in dimension_scores format
    # Columns: dimension_id, date, score, valid
    shock_dim_rows: list[dict[str, Any]] = []
    for _, row in panel_df.iterrows():
        sid = row["shock_id"]
        ym = row["year_month"]
        sev = row["severity"]
        direction = row["direction"]
        dt = pd.Timestamp(f"{ym}-01")

        if pd.isna(sev) or sev is None:
            score = None
            valid = False
        else:
            valid = True
            growth_dir = GROWTH_SIDE_DIRECTIONS.get(sid, "up")
            if sid in ("volatility_shock", "credit_shock", "labour_shock"):
                score = 1.0 if sev >= 1 else 0.0
            else:
                score = 1.0 if (sev >= 1 and direction == growth_dir) else 0.0

        shock_dim_rows.append(
            {
                "dimension_id": sid,
                "date": dt,
                "score": score,
                "valid": valid,
                "valid_feature_count": 1 if valid else 0,
                "configured_feature_count": 1,
                "total_configured_weight": 1.0,
                "used_weight": 1.0 if valid else 0.0,
                "coverage_ratio": 1.0 if valid else 0.0,
                "reason": "ok" if valid else "sample_not_started",
                "composition_id": f"{sid}:v1",
            }
        )

    shock_dims_df = pd.DataFrame(shock_dim_rows)
    dims_with_shocks = pd.concat([dims_all, shock_dims_df], ignore_index=True)

    # Arm definitions
    # Arm 1-7: Individual shock arms (weight 0.15, core weights * 0.85)
    # Arm 8: Growth shocks (labour 0.05, credit 0.05, volatility 0.05, core weights * 0.85)
    # Arm 9: Growth shocks + oil shadow dimension (labour 0.05, credit 0.05, volatility 0.05, commodity 0.15, core weights * 0.70)
    arm_specs: list[dict[str, Any]] = []
    for sid in SHOCK_IDS:
        arm_specs.append(
            {
                "arm_id": sid,
                "name": f"Individual arm: {sid}",
                "shock_weights": {sid: 0.15},
                "commodity_weight": 0.0,
                "core_scale": 0.85,
            }
        )

    arm_specs.append(
        {
            "arm_id": "growth_shocks",
            "name": "Combined growth shocks (labour, credit, volatility at 0.05 each)",
            "shock_weights": {
                "labour_shock": 0.05,
                "credit_shock": 0.05,
                "volatility_shock": 0.05,
            },
            "commodity_weight": 0.0,
            "core_scale": 0.85,
        }
    )

    arm_specs.append(
        {
            "arm_id": "growth_shocks_plus_oil",
            "name": "Growth shocks + oil shadow dimension (commodity 0.15, shocks 0.15, core 0.70)",
            "shock_weights": {
                "labour_shock": 0.05,
                "credit_shock": 0.05,
                "volatility_shock": 0.05,
            },
            "commodity_weight": 0.15,
            "core_scale": 0.70,
        }
    )

    arms_result: list[dict[str, Any]] = []

    for spec in arm_specs:
        arm_id = spec["arm_id"]
        shock_weights = spec["shock_weights"]
        comm_weight = spec["commodity_weight"]
        core_scale = spec["core_scale"]

        # Modify recession regime
        arm_regimes: list[RegimeDefinition] = []
        for reg in reg_config.regimes:
            if reg.regime_id != "recession":
                arm_regimes.append(reg)
                continue

            new_dims = [
                {
                    "dimension_id": d.dimension_id,
                    "weight": d.weight * core_scale,
                    "polarity": d.polarity,
                    "intercept": d.intercept,
                }
                for d in reg.dimensions
            ]

            if comm_weight > 0.0:
                new_dims.append(
                    {
                        "dimension_id": "commodity",
                        "weight": comm_weight,
                        "polarity": "positive",
                        "intercept": 0.0,
                    }
                )

            for s_name, s_wt in shock_weights.items():
                new_dims.append(
                    {
                        "dimension_id": s_name,
                        "weight": s_wt,
                        "polarity": "positive",
                        "intercept": 0.0,
                    }
                )

            arm_regimes.append(
                RegimeDefinition.model_validate(
                    {
                        "regime_id": reg.regime_id,
                        "enabled": reg.enabled,
                        "min_valid_dimensions": reg.min_valid_dimensions,
                        "min_coverage_ratio": reg.min_coverage_ratio,
                        "dimensions": new_dims,
                    }
                )
            )

        arm_res = build_regimes_from_dimensions(
            dims_with_shocks, arm_regimes, reg_config.scoring
        )
        arm_rec = arm_res.regime_scores[arm_res.regime_scores["regime_id"] == "recession"].copy()
        arm_rec["date"] = pd.to_datetime(arm_rec["date"])
        arm_rec = arm_rec[arm_rec["valid"] & arm_rec["probability"].notna()].sort_values("date")

        arm_periods = arm_rec["date"].dt.to_period("M").to_numpy()
        arm_probs = arm_rec["probability"].to_numpy()

        # Evaluate on governing basis C
        eval_c = evaluate_recession_performance(arm_periods, arm_probs, nber_months, EX_COVID)

        # Evaluate on sensitivity bases
        sensitivities: dict[str, Any] = {}
        for s_name, s_win in SENSITIVITY_BASES.items():
            sensitivities[s_name] = evaluate_recession_performance(
                arm_periods, arm_probs, nber_months, s_win
            )

        # Spell and switch stats
        arm_spells = argmax_spell_stats(_argmax_states(arm_res.regime_scores))

        # Stability guard check (switch count and median spell within 10%)
        base_switches = base_spell_stats["switch_count"]
        arm_switches = arm_spells["switch_count"]
        base_med_spell = base_spell_stats["median_spell_months"]
        arm_med_spell = arm_spells["median_spell_months"]

        switches_ok = arm_switches <= (base_switches * (1.0 + STABILITY_TOLERANCE))
        spell_ok = (
            (arm_med_spell >= (base_med_spell * (1.0 - STABILITY_TOLERANCE)))
            if (base_med_spell and arm_med_spell)
            else True
        )
        stability_passed = switches_ok and spell_ok

        # Determine eligibility (late starting shocks need >= 3 ex-COVID recessions)
        # 1990 start covers 1990-07, 2001-03, 2007-12 (3 recessions).
        # inflation_shock starts 2003-01 -> only 1 recession (2007-12) -> ineligible
        eligible = True
        if arm_id == "inflation_shock":
            eligible = False

        # Verdict calculation
        arm_p = eval_c["best_precision_at_recall_0_80"]["precision"] if eval_c["best_precision_at_recall_0_80"] else None
        base_p = base_eval_c["best_precision_at_recall_0_80"]["precision"]
        arm_auc = eval_c["auroc"]
        base_auc = base_eval_c["auroc"]
        arm_brier = eval_c["brier_score"]
        base_brier = base_eval_c["brier_score"]

        auroc_ok = arm_auc is not None and arm_auc >= (base_auc - AUROC_TOL)
        brier_ok = arm_brier is not None and arm_brier <= (base_brier + BRIER_TOL)

        if not eligible:
            verdict = "INELIGIBLE_INFORMATION_ONLY"
        elif arm_p is not None and arm_p > SHIP_BAR_PRECISION and auroc_ok and brier_ok and stability_passed:
            verdict = "MEETS_BAR"
        elif arm_p is not None and arm_p >= (base_p + EVIDENCE_MARGIN) and auroc_ok and brier_ok and stability_passed:
            verdict = "EVIDENCE"
        else:
            verdict = "NO_EVIDENCE"

        delta_p = (arm_p - base_p) if arm_p is not None else None
        delta_auc = (arm_auc - base_auc) if arm_auc is not None else None

        arms_result.append(
            {
                "arm_id": arm_id,
                "name": spec["name"],
                "eligible": eligible,
                "governing_eval": eval_c,
                "delta_precision": _round(delta_p),
                "delta_auroc": _round(delta_auc),
                "sensitivities": sensitivities,
                "spell_stats": arm_spells,
                "stability_guard": {
                    "passed": stability_passed,
                    "switch_count": arm_switches,
                    "base_switch_count": base_switches,
                    "switches_ok": switches_ok,
                    "median_spell": arm_med_spell,
                    "base_median_spell": base_med_spell,
                    "spell_ok": spell_ok,
                },
                "verdict": verdict,
            }
        )

    t1_dict = {
        "test": "T1_recession_leg",
        "governing_basis": "ex-COVID 2020-02..2021-06 (Basis C, 17 months excluded)",
        "baseline": {
            "governing_eval": base_eval_c,
            "spell_stats": base_spell_stats,
        },
        "arms": arms_result,
    }

    # Markdown generation
    md_lines = [
        "# MRI S4 — Impact Study: T1 Recession Leg",
        "",
        "**Governing Basis:** ex-COVID 2020-02..2021-06 (17 months excluded, pre-registered in S5).",
        f"**Baseline Governing:** Precision@0.80 recall = **{base_eval_c['best_precision_at_recall_0_80']['precision']:.4f}** | "
        f"AUROC = **{base_eval_c['auroc']:.4f}** | Brier = **{base_eval_c['brier_score']:.4f}** | "
        f"Switches = {base_spell_stats['switch_count']} | Median Spell = {base_spell_stats['median_spell_months']}m",
        "",
        "| arm | eligible | prec@rec>=0.80 | Δprec | recall | threshold | AUROC | Brier | switches | med spell | stability | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for arm in arms_result:
        g = arm["governing_eval"]
        bp = g["best_precision_at_recall_0_80"]
        p_str = f"{bp['precision']:.4f}" if bp else "n/a"
        dp_str = f"{arm['delta_precision']:+.4f}" if arm["delta_precision"] is not None else "n/a"
        rec_str = f"{bp['recall']:.4f}" if bp else "n/a"
        th_str = f"{bp['threshold']:.4f}" if bp else "n/a"
        auc_str = f"{g['auroc']:.4f}" if g["auroc"] is not None else "n/a"
        br_str = f"{g['brier_score']:.4f}" if g["brier_score"] is not None else "n/a"
        stab = "PASS" if arm["stability_guard"]["passed"] else "FAIL"
        sw = arm["stability_guard"]["switch_count"]
        sp = arm["stability_guard"]["median_spell"]
        el_str = "yes" if arm["eligible"] else "no"

        md_lines.append(
            f"| `{arm['arm_id']}` | {el_str} | **{p_str}** | {dp_str} | {rec_str} | {th_str} | {auc_str} | {br_str} | {sw} | {sp}m | {stab} | **{arm['verdict']}** |"
        )

    md_lines.extend(
        [
            "",
            "### Sensitivity Bases Summary (Precision @ Recall ≥ 0.80)",
            "",
            "| arm | Basis C (governing) | Basis A (NBER only) | Basis B (2020-02..12) | none (all 4 recessions) |",
            "|---|---|---|---|---|",
        ]
    )

    for arm in arms_result:
        g_p = arm["governing_eval"]["best_precision_at_recall_0_80"]["precision"]
        a_p = arm["sensitivities"]["A"]["best_precision_at_recall_0_80"]["precision"]
        b_p = arm["sensitivities"]["B"]["best_precision_at_recall_0_80"]["precision"]
        n_p = arm["sensitivities"]["none"]["best_precision_at_recall_0_80"]["precision"]
        md_lines.append(
            f"| `{arm['arm_id']}` | {g_p:.4f} | {a_p:.4f} | {b_p:.4f} | {n_p:.4f} |"
        )

    md_content = "\n".join(md_lines) + "\n"
    return t1_dict, md_content


# -----------------------------------------------------------------------------
# T2 — Sector Leg
# -----------------------------------------------------------------------------


def run_t2_sector_leg(
    con: duckdb.DuckDBPyConnection,
    panel_df: pd.DataFrame,
) -> tuple[dict[str, Any], str]:
    """Execute T2 sector leg with signed shock severity features on S6.3 harness."""
    dims = con.execute("SELECT * FROM dimension_scores WHERE valid").fetchdf()
    vr = con.execute("SELECT * FROM sector_validation_returns WHERE valid").fetchdf()

    # Create signed severity features for each shock
    shock_feature_rows: list[dict[str, Any]] = []
    for _, row in panel_df.iterrows():
        sid = row["shock_id"]
        ym = row["year_month"]
        sev = row["severity"]
        direction = row["direction"]
        dt = pd.Timestamp(f"{ym}-01")

        if pd.isna(sev) or sev is None:
            score = np.nan
            valid = False
        else:
            valid = True
            sev_float = float(sev)
            if direction == "down":
                score = -sev_float
            else:
                score = sev_float

        shock_feature_rows.append(
            {
                "dimension_id": sid,
                "date": dt,
                "score": score,
                "valid": valid,
            }
        )

    shock_feat_df = pd.DataFrame(shock_feature_rows)
    cand_dims = pd.concat([dims, shock_feat_df], ignore_index=True)

    # Reference C0 on full panel (horizon 3m)
    dates_full, sectors, R_full, X_full, B_full = prepare_cross_section_matrices(
        vr, dims, dimension_ids=CORE_DIMENSION_IDS, horizon_months=3
    )
    acc_c0_full = compute_annual_oos_acceptance(
        dates_full, sectors, CORE_DIMENSION_IDS, R_full, X_full, B_full, 3
    )

    arms_to_run = [[sid] for sid in SHOCK_IDS] + [SHOCK_IDS]

    arms_result: list[dict[str, Any]] = []

    for shock_group in arms_to_run:
        is_all_seven = len(shock_group) > 1
        arm_name = "all_seven_shocks" if is_all_seven else shock_group[0]
        dim_ids = CORE_DIMENSION_IDS + shock_group

        # Prepare cross section with candidate features
        dates_c, sectors_c, R_c, X_c, B_c = prepare_cross_section_matrices(
            vr, cand_dims, dimension_ids=dim_ids, horizon_months=3
        )

        # Like-for-like masking: keep dates where ALL features (core + candidate) are observed
        mask = ~np.isnan(X_c).any(axis=1)
        dates_m = [d for d, keep in zip(dates_c, mask) if keep]
        R_m = R_c[mask]
        B_m = B_c[mask]
        X_m = X_c[mask]
        n_masked = len(dates_m)

        # C0 on masked panel
        v0 = build_annual_vintages(dates_m, sectors_c, CORE_DIMENSION_IDS, R_m, X_m[:, :5], 3)
        sc0, _, _ = score_series_from_vintages(
            dates_m, sectors_c, CORE_DIMENSION_IDS, X_m[:, :5], v0
        )
        acc_c0_m = compute_annual_oos_acceptance(
            dates_m, sectors_c, CORE_DIMENSION_IDS, R_m, X_m[:, :5], B_m, 3
        )

        # Ck on masked panel
        vk = build_annual_vintages(dates_m, sectors_c, dim_ids, R_m, X_m, 3)
        sck, _, _ = score_series_from_vintages(dates_m, sectors_c, dim_ids, X_m, vk)
        acc_ck_m = compute_annual_oos_acceptance(
            dates_m, sectors_c, dim_ids, R_m, X_m, B_m, 3
        )

        # Paired delta IC series
        ic0: dict[pd.Timestamp, float] = {}
        ick: dict[pd.Timestamp, float] = {}
        if v0 and vk:
            lo = max(v0[0].vintage_date, vk[0].vintage_date)
            hi = min(v0[-1].vintage_date, vk[-1].vintage_date)
            for idx, d in enumerate(dates_m):
                if lo <= d <= hi:
                    c0_v = spearman_ic(sc0[idx], R_m[idx])
                    ck_v = spearman_ic(sck[idx], R_m[idx])
                    if c0_v is not None and ck_v is not None:
                        ic0[d] = c0_v
                        ick[d] = ck_v

        common_dates = sorted(set(ic0) & set(ick))
        delta = [ick[d] - ic0[d] for d in common_dates]
        paired_summary = summarize_ic_series(delta, 3)

        # Half sample checks
        half_n = len(common_dates) // 2
        d_half1 = delta[:half_n] if half_n > 0 else []
        d_half2 = delta[half_n:] if half_n > 0 else []

        h1_mean = float(np.mean(d_half1)) if d_half1 else None
        h2_mean = float(np.mean(d_half2)) if d_half2 else None
        halves_positive = bool(h1_mean is not None and h2_mean is not None and h1_mean > 0 and h2_mean > 0)

        # S-b acceptance check
        s_b_passed = False
        if acc_ck_m.get("oos"):
            promo = evaluate_promotion(acc_ck_m["oos"])
            s_b_passed = bool(promo.get("passed", False))

        # Pass condition: paired ΔIC t >= 2.0 and both halves ΔIC > 0 and S-b passed
        mean_d = paired_summary["mean_ic"]
        t_d = paired_summary["t_stat"]

        if mean_d is not None and mean_d > 0 and t_d is not None and t_d >= 2.0 and halves_positive and s_b_passed:
            verdict = "PASS"
        elif mean_d is not None and mean_d > 0:
            verdict = "IMPROVES_NOT_SIGNIFICANT"
        else:
            verdict = "NO_IMPROVEMENT"

        arms_result.append(
            {
                "arm_id": arm_name,
                "n_masked_dates": n_masked,
                "n_paired_dates": len(common_dates),
                "paired_delta_ic": paired_summary,
                "half1_mean_delta": _round(h1_mean),
                "half2_mean_delta": _round(h2_mean),
                "halves_both_positive": halves_positive,
                "s_b_passed": s_b_passed,
                "ck_oos": acc_ck_m.get("oos"),
                "c0_oos": acc_c0_m.get("oos"),
                "verdict": verdict,
            }
        )

    t2_dict = {
        "test": "T2_sector_leg",
        "specification": "S6.3 OOS annual January refit, GICS 11, horizon 3m, NW(2)",
        "reference_c0_full": acc_c0_full.get("oos"),
        "arms": arms_result,
    }

    # Markdown generation
    c0_full_oos = acc_c0_full.get("oos", {})
    md_lines = [
        "# MRI S4 — Impact Study: T2 Sector Leg",
        "",
        "**Harness:** S6.3 Out-Of-Sample Annual January Refit (3-month horizon, overlap-corrected NW(2) standard errors).",
        f"**C0 Full Reference:** Mean IC = **{c0_full_oos.get('mean_ic')}** | NW t = **{c0_full_oos.get('t_stat')}** | n = {c0_full_oos.get('n_dates')}",
        "",
        "| arm | n masked | paired ΔIC mean | NW t (3m) | half 1 ΔIC | half 2 ΔIC | both halves > 0 | S-b promo | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for arm in arms_result:
        p = arm["paired_delta_ic"]
        m_ic = f"{p['mean_ic']:+.4f}" if p["mean_ic"] is not None else "n/a"
        t_stat = f"{p['t_stat']:+.2f}" if p["t_stat"] is not None else "n/a"
        h1 = f"{arm['half1_mean_delta']:+.4f}" if arm["half1_mean_delta"] is not None else "n/a"
        h2 = f"{arm['half2_mean_delta']:+.4f}" if arm["half2_mean_delta"] is not None else "n/a"
        bh = "yes" if arm["halves_both_positive"] else "no"
        sb = "yes" if arm["s_b_passed"] else "no"

        md_lines.append(
            f"| `{arm['arm_id']}` | {arm['n_masked_dates']} | **{m_ic}** | **{t_stat}** | {h1} | {h2} | {bh} | {sb} | **{arm['verdict']}** |"
        )

    md_content = "\n".join(md_lines) + "\n"
    return t2_dict, md_content


# -----------------------------------------------------------------------------
# T3 — Forward-Risk Leg
# -----------------------------------------------------------------------------


def compute_market_outcomes(
    ff_monthly: pd.DataFrame, ff_daily: pd.DataFrame
) -> pd.DataFrame:
    """Compute forward market outcomes per month-end:

      1. fwd_1m_mkt_return (decimal)
      2. fwd_3m_mkt_return (decimal)
      3. fwd_3m_realized_vol (annualized daily std * sqrt(252))
      4. fwd_3m_max_drawdown (decimal <= 0)
    """
    m_df = ff_monthly.copy()
    m_df["dt"] = pd.to_datetime(m_df["date"])
    m_df["ym"] = m_df["dt"].dt.to_period("M")
    m_df = m_df.sort_values("dt").reset_index(drop=True)

    d_df = ff_daily.copy()
    d_df["dt"] = pd.to_datetime(d_df["date"])
    d_df = d_df.sort_values("dt").reset_index(drop=True)

    records: list[dict[str, Any]] = []

    for i in range(len(m_df)):
        cur_row = m_df.iloc[i]
        cur_dt = cur_row["dt"]
        cur_ym = cur_row["ym"]

        # Forward 1m return (month i+1)
        if i + 1 < len(m_df):
            fwd_1m = float(m_df.iloc[i + 1]["mkt_return"])
        else:
            fwd_1m = None

        # Forward 3m return (months i+1, i+2, i+3)
        if i + 3 < len(m_df):
            sub_m = m_df.iloc[i + 1 : i + 4]
            fwd_3m = float((1.0 + sub_m["mkt_return"]).prod() - 1.0)
            end_3m_dt = sub_m.iloc[-1]["dt"]

            # Forward daily slice: cur_dt < d <= end_3m_dt
            sub_d = d_df[(d_df["dt"] > cur_dt) & (d_df["dt"] <= end_3m_dt)]
            if len(sub_d) >= 20:
                d_ret = sub_d["mkt_return"].to_numpy()
                vol_3m = float(np.std(d_ret, ddof=1) * math.sqrt(252.0))
                wealth = np.cumprod(1.0 + d_ret)
                peak = np.maximum.accumulate(wealth)
                dd = (wealth - peak) / peak
                max_dd_3m = float(np.min(dd))
            else:
                vol_3m = None
                max_dd_3m = None
        else:
            fwd_3m = None
            vol_3m = None
            max_dd_3m = None

        records.append(
            {
                "date": cur_dt,
                "year_month": str(cur_ym),
                "fwd_1m_mkt_return": fwd_1m,
                "fwd_3m_mkt_return": fwd_3m,
                "fwd_3m_realized_vol": vol_3m,
                "fwd_3m_max_drawdown": max_dd_3m,
            }
        )

    return pd.DataFrame(records)


def run_t3_forward_risk_leg(
    con: duckdb.DuckDBPyConnection,
    panel_df: pd.DataFrame,
    ff_monthly: pd.DataFrame,
    ff_daily: pd.DataFrame,
) -> tuple[dict[str, Any], str]:
    """Execute T3 forward-risk leg across 7 shocks x 4 outcomes."""
    # Build forward market outcomes
    outcomes_df = compute_market_outcomes(ff_monthly, ff_daily)

    # Read regime probabilities from DuckDB
    reg_df = con.execute("SELECT * FROM regime_scores WHERE valid").fetchdf()
    reg_df["date"] = pd.to_datetime(reg_df["date"])
    reg_df["year_month"] = reg_df["date"].dt.to_period("M").astype(str)

    reg_pivot = reg_df.pivot(index="year_month", columns="regime_id", values="probability")
    regime_cols = ["goldilocks", "recession", "stagflation", "reflation", "tightening"]
    reg_pivot = reg_pivot[regime_cols].dropna()

    outcomes = [
        ("fwd_1m_mkt_return", 0, "Forward 1M Market Return"),
        ("fwd_3m_mkt_return", 2, "Forward 3M Market Return"),
        ("fwd_3m_realized_vol", 2, "Forward 3M Realized Volatility"),
        ("fwd_3m_max_drawdown", 2, "Forward 3M Maximum Drawdown"),
    ]

    # Pre-registered COVID exclusion window
    covid_excluded = _window_months(EX_COVID)

    regression_records: list[dict[str, Any]] = []
    raw_p_values: list[float] = []

    for sid in SHOCK_IDS:
        shock_panel = panel_df[panel_df["shock_id"] == sid].copy()
        shock_map = dict(zip(shock_panel["year_month"], shock_panel["severity"]))

        for out_col, nw_lag, out_label in outcomes:
            merged_rows: list[dict[str, Any]] = []

            for _, row in outcomes_df.iterrows():
                ym = row["year_month"]
                p_period = pd.Period(ym, freq="M")

                # Sample restrictions: governing window 1990-01 onward, ex-COVID
                if p_period < pd.Period("1990-01", "M"):
                    continue
                if p_period in covid_excluded:
                    continue
                if ym not in reg_pivot.index:
                    continue

                out_val = row[out_col]
                if out_val is None or pd.isna(out_val):
                    continue

                sev = shock_map.get(ym)
                # If shock not observed on this month, drop like-for-like
                if sev is None or pd.isna(sev):
                    continue

                shock_flag = 1.0 if float(sev) >= 1.0 else 0.0

                item = {
                    "year_month": ym,
                    "y": float(out_val),
                    "shock_flag": shock_flag,
                }
                for rcol in regime_cols:
                    item[rcol] = float(reg_pivot.loc[ym, rcol])
                merged_rows.append(item)

            df_reg = pd.DataFrame(merged_rows)
            if len(df_reg) < 30:
                continue

            # Regressors: [5 regime probabilities, shock_flag]
            # Since sum(probabilities) == 1.0, they span the intercept.
            X = df_reg[regime_cols + ["shock_flag"]].to_numpy()
            y = df_reg["y"].to_numpy()

            beta, se, t_stat, p_val = compute_newey_west_ols(X, y, lag=nw_lag)
            # The shock flag is the last regressor (index 5)
            b_shock = float(beta[-1])
            se_shock = float(se[-1])
            t_shock = float(t_stat[-1])
            p_shock = float(p_val[-1])

            # Half-sample check (same sign in both halves)
            n_obs = len(df_reg)
            half_idx = n_obs // 2
            X1, y1 = X[:half_idx], y[:half_idx]
            X2, y2 = X[half_idx:], y[half_idx:]
            b1, _, _, _ = compute_newey_west_ols(X1, y1, lag=nw_lag)
            b2, _, _, _ = compute_newey_west_ols(X2, y2, lag=nw_lag)
            b1_shock = float(b1[-1])
            b2_shock = float(b2[-1])

            same_sign = bool(
                (b1_shock > 0 and b2_shock > 0 and b_shock > 0)
                or (b1_shock < 0 and b2_shock < 0 and b_shock < 0)
            )

            # Economic size bars:
            # - at least 2.0 pp of 3-month return (|beta| >= 0.02)
            # - at least +25% of 3-month volatility (beta >= 0.25 * mean_vol)
            # - at least 2.0 points deeper drawdown (beta <= -0.02)
            mean_y = float(np.mean(y))
            econ_large = False
            effect_note = ""

            if out_col == "fwd_3m_mkt_return":
                econ_large = abs(b_shock) >= 0.02
                effect_note = f"{b_shock * 100:+.2f} pp return (bar: >= 2.0 pp)"
            elif out_col == "fwd_1m_mkt_return":
                econ_large = abs(b_shock) >= (0.02 / 3.0)
                effect_note = f"{b_shock * 100:+.2f} pp return (bar: >= 0.67 pp)"
            elif out_col == "fwd_3m_realized_vol":
                bar_vol = 0.25 * mean_y
                econ_large = b_shock >= bar_vol
                effect_note = f"{b_shock * 100:+.2f}% vol vs bar +{bar_vol * 100:.2f}% (+25% of mean {mean_y * 100:.1f}%)"
            elif out_col == "fwd_3m_max_drawdown":
                econ_large = b_shock <= -0.02
                effect_note = f"{b_shock * 100:+.2f} pp drawdown (bar: <= -2.0 pp deeper)"

            raw_p_values.append(p_shock)

            regression_records.append(
                {
                    "shock_id": sid,
                    "outcome": out_col,
                    "outcome_label": out_label,
                    "n_obs": n_obs,
                    "nw_lag": nw_lag,
                    "beta": b_shock,
                    "se": se_shock,
                    "t_stat": t_shock,
                    "raw_p": p_shock,
                    "half1_beta": b1_shock,
                    "half2_beta": b2_shock,
                    "same_sign": same_sign,
                    "mean_y": mean_y,
                    "econ_large": econ_large,
                    "effect_note": effect_note,
                }
            )

    # Multiple testing correction: Holm step-down across the 28 tests
    holm_p_vals = holm_bonferroni(raw_p_values)

    # Attach adjusted p and determine pass/fail per rule
    passed_count = 0
    for idx, rec in enumerate(regression_records):
        h_p = float(holm_p_vals[idx])
        rec["holm_p"] = h_p
        # |t| >= 2 after Holm correction (p_holm <= 0.05)
        t_holm_ok = bool(h_p <= 0.05)
        rec["t_holm_passed"] = t_holm_ok

        # Pass requires: t_holm_ok AND same_sign AND econ_large
        passed = bool(t_holm_ok and rec["same_sign"] and rec["econ_large"])
        rec["passed"] = passed
        if passed:
            passed_count += 1

    t3_dict = {
        "test": "T3_forward_risk_leg",
        "governing_window": "1990-01..latest complete month, ex-COVID 2020-02..2021-06",
        "total_regressions": len(regression_records),
        "total_passed": passed_count,
        "results": regression_records,
    }

    # Markdown generation
    md_lines = [
        "# MRI S4 — Impact Study: T3 Forward-Risk Leg",
        "",
        "**Model:** OLS of forward market outcome on 5 regime probabilities + shock severity ≥ 1 flag.",
        "**Inference:** Newey-West standard errors (lag 0 for 1m, lag 2 for 3m). Family-wise Holm correction across 7 shocks × 4 outcomes (28 tests).",
        "**Governing Window:** 1990-01 onward, ex-COVID 2020-02..2021-06.",
        "",
        "| shock | outcome | n | beta | NW t | raw p | Holm p | half 1 / 2 beta | same sign | economic size | pass rule |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in regression_records:
        b_str = f"{r['beta']:+.4f}"
        t_str = f"{r['t_stat']:+.2f}"
        p_str = f"{r['raw_p']:.4f}"
        hp_str = f"{r['holm_p']:.4f}"
        h_str = f"{r['half1_beta']:+.3f} / {r['half2_beta']:+.3f}"
        sign_str = "yes" if r["same_sign"] else "no"
        econ_str = "yes" if r["econ_large"] else "no"
        res_str = "**PASS**" if r["passed"] else "FAIL"

        md_lines.append(
            f"| `{r['shock_id']}` | `{r['outcome']}` | {r['n_obs']} | {b_str} | **{t_str}** | {p_str} | {hp_str} | {h_str} | {sign_str} | {econ_str} | {res_str} |"
        )

    md_lines.extend(
        [
            "",
            "### Detailed Effects and Pass Rule Evaluation",
            "",
        ]
    )

    for r in regression_records:
        md_lines.append(
            f"- **`{r['shock_id']}` × `{r['outcome']}`**: beta = {r['beta']:+.4f}, t = {r['t_stat']:+.2f}, "
            f"Holm p = {r['holm_p']:.4f}, effect: {r['effect_note']}, same sign: {r['same_sign']} -> **{r['passed']}**"
        )

    md_content = "\n".join(md_lines) + "\n"
    return t3_dict, md_content


# -----------------------------------------------------------------------------
# Main Driver
# -----------------------------------------------------------------------------


def run_impact_study(
    db_path: str | Path,
    panel_parquet_path: str | Path,
    ff_monthly_path: str | Path,
    ff_daily_path: str | Path,
    out_dir: str | Path,
) -> dict[str, Any]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to DuckDB store copy at {db_path}...")
    con = duckdb.connect(str(db_path), read_only=True)

    print(f"Loading monthly panel from {panel_parquet_path}...")
    panel_df = pd.read_parquet(panel_parquet_path)

    shocks_cfg = load_shocks_config("config/shocks.yaml")
    tax_ver = compute_taxonomy_version("config/shocks.yaml")

    with open("config/nber_recessions.yaml", "r", encoding="utf-8") as f:
        import yaml

        nber_config = yaml.safe_load(f)

    # 1. T1 Recession leg
    print("Running T1 — Recession leg (counterfactual arms & stability guard)...")
    t1_dict, t1_md = run_t1_recession_leg(con, panel_df, shocks_cfg, nber_config)
    t1_dict["provenance"] = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy_version": tax_ver,
        "db_path": str(db_path),
    }
    with open(out_path / "t1.json", "w", encoding="utf-8") as f:
        json.dump(t1_dict, f, indent=2)
    with open(out_path / "t1.md", "w", encoding="utf-8") as f:
        f.write(t1_md)

    # 2. T2 Sector leg
    print("Running T2 — Sector leg (S6.3 OOS harness with signed severity)...")
    t2_dict, t2_md = run_t2_sector_leg(con, panel_df)
    t2_dict["provenance"] = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy_version": tax_ver,
        "db_path": str(db_path),
    }
    with open(out_path / "t2.json", "w", encoding="utf-8") as f:
        json.dump(t2_dict, f, indent=2)
    with open(out_path / "t2.md", "w", encoding="utf-8") as f:
        f.write(t2_md)

    # 3. T3 Forward-risk leg
    print("Running T3 — Forward-risk leg (predictive OLS & Holm correction)...")
    ff_m = pd.read_parquet(ff_monthly_path)
    ff_d = pd.read_parquet(ff_daily_path)
    t3_dict, t3_md = run_t3_forward_risk_leg(con, panel_df, ff_m, ff_d)
    t3_dict["provenance"] = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy_version": tax_ver,
        "db_path": str(db_path),
    }
    with open(out_path / "t3.json", "w", encoding="utf-8") as f:
        json.dump(t3_dict, f, indent=2)
    with open(out_path / "t3.md", "w", encoding="utf-8") as f:
        f.write(t3_md)

    print("\nImpact study execution complete.")
    print(f"  Artifacts written to: {out_path}")
    print(f"  T1: {out_path / 't1.md'}")
    print(f"  T2: {out_path / 't2.md'}")
    print(f"  T3: {out_path / 't3.md'}")

    return {"t1": t1_dict, "t2": t2_dict, "t3": t3_dict}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default="data/macro_engine.duckdb",
        help="path to DuckDB store copy",
    )
    parser.add_argument(
        "--panel-parquet",
        default=None,
        help="path to s4_impact/panel.parquet",
    )
    parser.add_argument(
        "--ff-monthly",
        default="data/external/ff_factors_monthly.parquet",
        help="path to ff_factors_monthly.parquet",
    )
    parser.add_argument(
        "--ff-daily",
        default="data/external/ff_factors_daily.parquet",
        help="path to ff_factors_daily.parquet",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="output directory for {t1,t2,t3}.{json,md}",
    )
    args = parser.parse_args(argv)

    panel_path = args.panel_parquet
    if panel_path is None:
        panel_path = Path(args.out_dir) / "panel.parquet"

    run_impact_study(
        db_path=args.db_path,
        panel_parquet_path=panel_path,
        ff_monthly_path=args.ff_monthly,
        ff_daily_path=args.ff_daily,
        out_dir=args.out_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
