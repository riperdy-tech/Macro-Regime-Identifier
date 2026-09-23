"""S5.0: tests for scripts/measure_s5_pack.py (MRI_S5_SPEC.md section 1 and 3).

Synthetic frames only -- never the live store. Loads scripts/measure_s5_pack.py the same way
tests/test_probe_hmm_recession.py loads scripts/probes/hmm_recession.py, since scripts/ is not
a package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from macro_engine.regimes.config import RegimeScoringConfig
from macro_engine.regimes.scoring import build_regimes_from_dimensions
from macro_engine.sectors.fit import (
    CORE_DIMENSION_IDS,
    GICS_11_SECTOR_IDS,
    evaluate_promotion,
    prepare_cross_section_matrices,
)

_SPEC = importlib.util.spec_from_file_location(
    "measure_s5_pack",
    Path(__file__).resolve().parents[1] / "scripts" / "measure_s5_pack.py",
)
pack = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pack)  # type: ignore[union-attr]


# ── 1. constants pinned exactly as the spec's block ─────────────────────────────────────────


def test_constants_match_the_spec():
    assert pack.EX_COVID == ("2020-02", "2021-06")
    assert pack.SENSITIVITY_BASES == {
        "A": ("2020-02", "2020-04"),
        "B": ("2020-02", "2020-12"),
        "none": None,
    }
    assert pack.RECALL_FLOOR == 0.80
    assert pack.BAR_PRECISION == 0.40
    assert pack.EVIDENCE_MARGIN == 0.030
    assert pack.AUROC_TOL == 0.005
    assert pack.BRIER_TOL == 0.005
    assert pack.ARM_WEIGHT == 0.15
    assert pack.EXISTING_SCALE == 0.85
    assert pack.MIN_EX_COVID_RECESSIONS == 3
    assert pack.RESOLUTION_MIN == 0.95
    assert pack.SECTOR_T_MIN == 2.0
    assert pack.MULTIPLICITY_T == 2.39
    assert pack.SECTOR_FIRST_RETURN_DATE == "1999-01-01"
    assert pack.DESIGN_BASELINE == {
        "precision": 0.319149,
        "auroc": 0.9108,
        "brier": 0.0870,
        "c0_ic_3m": 0.028901,
        "c0_t_3m": 0.776364,
        "c0_n_3m": 227,
    }
    assert pack.CANDIDATES == {
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


def test_ex_covid_excludes_exactly_17_months():
    excluded = pack._window_months(pack.EX_COVID)
    assert len(excluded) == 17


# ── 2. refusals ──────────────────────────────────────────────────────────────────────────────


def test_refusal_live_store_path():
    live = pack._LIVE_STORE_PATH
    assert pack.check_refusals(live, Path("ref.duckdb"), Path("out")) is not None
    assert pack.check_refusals(Path("cand.duckdb"), live, Path("out")) is not None


def test_refusal_out_dir_inside_repo_outputs():
    out_in_outputs = pack._REPO_OUTPUTS_DIR / "s5_1"
    refusal = pack.check_refusals(Path("cand.duckdb"), Path("ref.duckdb"), out_in_outputs)
    assert refusal is not None


def test_refusal_identical_db_paths():
    same = Path("same_store.duckdb")
    assert pack.check_refusals(same, same, Path("somewhere_else")) is not None


def test_no_refusal_for_ordinary_paths():
    assert pack.check_refusals(Path("cand.duckdb"), Path("ref.duckdb"), Path("packout")) is None


# ── 3. the arm: weights sum to 1.0; neutral where the candidate is invalid ─────────────────


def _recession_regime(weights: list[float]):
    return pack.RegimeDefinition.model_validate(
        {
            "regime_id": "recession",
            "min_valid_dimensions": 3,
            "min_coverage_ratio": 0.60,
            "dimensions": [
                {"dimension_id": "growth_momentum", "weight": weights[0], "polarity": "negative"},
                {
                    "dimension_id": "inflation_pressure",
                    "weight": weights[1],
                    "polarity": "positive",
                    "intercept": 0.429,
                },
                {"dimension_id": "credit_liquidity", "weight": weights[2], "polarity": "negative"},
                {"dimension_id": "policy_stance", "weight": weights[3], "polarity": "positive"},
                {"dimension_id": "yield_curve", "weight": weights[4], "polarity": "negative"},
            ],
        }
    )


def _other_regime():
    return pack.RegimeDefinition.model_validate(
        {
            "regime_id": "goldilocks",
            "min_valid_dimensions": 1,
            "min_coverage_ratio": 0.60,
            "dimensions": [{"dimension_id": "growth_momentum", "weight": 1.0, "polarity": "positive"}],
        }
    )


def test_arm_weights_sum_to_one():
    base_regimes = [_recession_regime([0.35, 0.10, 0.25, 0.15, 0.15]), _other_regime()]
    arm_regimes = pack.build_recession_arm_regimes(base_regimes, "commodity")
    arm_recession = next(r for r in arm_regimes if r.regime_id == "recession")
    total_weight = sum(dim.weight for dim in arm_recession.dimensions)
    assert total_weight == pytest.approx(1.0, abs=1e-12)
    assert len(arm_recession.dimensions) == 6


def test_arm_equals_baseline_when_candidate_always_invalid():
    base_regimes = [_recession_regime([0.35, 0.10, 0.25, 0.15, 0.15]), _other_regime()]
    arm_regimes = pack.build_recession_arm_regimes(base_regimes, "commodity")

    dates = pd.date_range("2020-01-01", periods=6, freq="MS")
    rows = []
    for i, date in enumerate(dates):
        for dim_id, score in [
            ("growth_momentum", 0.5 + i * 0.01),
            ("inflation_pressure", -0.2 + i * 0.02),
            ("credit_liquidity", 0.1 - i * 0.03),
            ("policy_stance", 0.3),
            ("yield_curve", -0.4 + i * 0.01),
        ]:
            rows.append(
                {"dimension_id": dim_id, "date": date, "score": score, "valid": True, "reason": "ok"}
            )
        rows.append(
            {
                "dimension_id": "commodity",
                "date": date,
                "score": None,
                "valid": False,
                "reason": "disabled_source",
            }
        )
    dimension_scores = pd.DataFrame(rows)
    scoring = RegimeScoringConfig(softmax_temperature=0.6)

    baseline = build_regimes_from_dimensions(dimension_scores, base_regimes, scoring)
    arm = build_regimes_from_dimensions(dimension_scores, arm_regimes, scoring)

    base_rec = (
        baseline.regime_scores[baseline.regime_scores["regime_id"] == "recession"]
        .set_index("date")["probability"]
    )
    arm_rec = (
        arm.regime_scores[arm.regime_scores["regime_id"] == "recession"]
        .set_index("date")["probability"]
    )
    diff = (base_rec - arm_rec).abs().max()
    assert diff <= 1e-12


# ── 4/5: sector leg fixtures ─────────────────────────────────────────────────────────────────


def _synthetic_dates(n_months: int, start: str = "2000-01-01") -> list[pd.Timestamp]:
    return [pd.Timestamp(start) + pd.DateOffset(months=i) for i in range(n_months)]


def _six_col_frames(
    n_months: int = 220,
    seed: int = 123,
    candidate: str = "commodity",
    invalid_first: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The five core dimensions drive returns; the candidate column is independent noise,
    valid everywhere except (optionally) its first `invalid_first` months."""
    rng = np.random.default_rng(seed)
    n_features = len(CORE_DIMENSION_IDS)
    dates = _synthetic_dates(n_months)
    sectors = list(GICS_11_SECTOR_IDS)

    X = np.zeros((n_months, n_features))
    for t in range(1, n_months):
        X[t] = 0.7 * X[t - 1] + rng.normal(scale=0.7, size=n_features)
    candidate_scores = rng.normal(size=n_months)

    B = rng.uniform(-1.0, 1.0, size=(len(sectors), n_features))
    alphas = rng.uniform(-0.02, 0.02, size=len(sectors))
    returns_mat = np.zeros((n_months, len(sectors)))
    for s in range(len(sectors)):
        returns_mat[:, s] = alphas[s] + X @ B[s] + rng.normal(scale=0.05, size=n_months)
    baseline_mat = rng.normal(scale=0.5, size=(n_months, len(sectors)))

    dimension_rows = []
    for t, date in enumerate(dates):
        for j, dim_id in enumerate(CORE_DIMENSION_IDS):
            dimension_rows.append(
                {"dimension_id": dim_id, "date": date, "score": X[t, j], "valid": True}
            )
        valid_candidate = t >= invalid_first
        dimension_rows.append(
            {
                "dimension_id": candidate,
                "date": date,
                "score": candidate_scores[t] if valid_candidate else np.nan,
                "valid": valid_candidate,
            }
        )
    dimension_scores = pd.DataFrame(dimension_rows)

    validation_rows = []
    for t, date in enumerate(dates):
        for s, sector_id in enumerate(sectors):
            validation_rows.append(
                {
                    "sector_id": sector_id,
                    "score_date": date,
                    "confidence_adjusted_score": baseline_mat[t, s],
                    "relative_forward_1m_return": returns_mat[t, s] * 0.4,
                    "relative_forward_3m_return": returns_mat[t, s],
                    "valid": True,
                }
            )
    validation_returns = pd.DataFrame(validation_rows)
    return dimension_scores, validation_returns


def _paired_signal_frames(
    n_months: int = 280, seed: int = 7, candidate: str = "commodity"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The candidate column carries a genuine factor the 5 core dimensions omit, so it should
    add real out-of-sample skill over C0 (mirrors test_phase_s6_3_sector_fit.py's planted
    signal fixture, extended with one more incremental factor)."""
    rng = np.random.default_rng(seed)
    n_features = len(CORE_DIMENSION_IDS)
    dates = _synthetic_dates(n_months)
    sectors = list(GICS_11_SECTOR_IDS)

    X = np.zeros((n_months, n_features))
    for t in range(1, n_months):
        X[t] = 0.7 * X[t - 1] + rng.normal(scale=0.7, size=n_features)
    candidate_scores = np.zeros(n_months)
    for t in range(1, n_months):
        candidate_scores[t] = 0.7 * candidate_scores[t - 1] + rng.normal(scale=0.7)

    B = rng.uniform(-1.0, 1.0, size=(len(sectors), n_features))
    gamma = rng.uniform(0.8, 1.5, size=len(sectors)) * np.sign(rng.normal(size=len(sectors)))
    alphas = rng.uniform(-0.02, 0.02, size=len(sectors))

    returns_mat = np.zeros((n_months, len(sectors)))
    for s in range(len(sectors)):
        returns_mat[:, s] = (
            alphas[s] + X @ B[s] + gamma[s] * candidate_scores + rng.normal(scale=0.05, size=n_months)
        )
    baseline_mat = rng.normal(scale=0.5, size=(n_months, len(sectors)))

    dimension_rows = []
    for t, date in enumerate(dates):
        for j, dim_id in enumerate(CORE_DIMENSION_IDS):
            dimension_rows.append(
                {"dimension_id": dim_id, "date": date, "score": X[t, j], "valid": True}
            )
        dimension_rows.append(
            {"dimension_id": candidate, "date": date, "score": candidate_scores[t], "valid": True}
        )
    dimension_scores = pd.DataFrame(dimension_rows)

    validation_rows = []
    for t, date in enumerate(dates):
        for s, sector_id in enumerate(sectors):
            validation_rows.append(
                {
                    "sector_id": sector_id,
                    "score_date": date,
                    "confidence_adjusted_score": baseline_mat[t, s],
                    "relative_forward_1m_return": returns_mat[t, s] * 0.4,
                    "relative_forward_3m_return": returns_mat[t, s],
                    "valid": True,
                }
            )
    validation_returns = pd.DataFrame(validation_rows)
    return dimension_scores, validation_returns


# ── 4. like-for-like masking ─────────────────────────────────────────────────────────────────


def test_like_for_like_masking_same_n_dates_and_differs_from_full():
    dimension_scores, validation_returns = _six_col_frames(n_months=220, invalid_first=30)
    candidate_dim_ids = CORE_DIMENSION_IDS + ["commodity"]

    dates, sectors, returns_mat, features_mat, baseline_mat = prepare_cross_section_matrices(
        validation_returns, dimension_scores, dimension_ids=candidate_dim_ids, horizon_months=3
    )
    masked = pack.build_masked_arms(
        dates, sectors, returns_mat, features_mat, baseline_mat, candidate_dim_ids, 3
    )
    c0_masked_n = masked["C0_masked"]["oos"]["n_dates"]
    ck_masked_n = masked["Ck_masked"]["oos"]["n_dates"]
    assert c0_masked_n == ck_masked_n

    # C0 on the unmasked, full 5-column panel
    full_dates, full_sectors, full_returns, full_features, full_baseline = (
        prepare_cross_section_matrices(
            validation_returns, dimension_scores, dimension_ids=CORE_DIMENSION_IDS, horizon_months=3
        )
    )
    from macro_engine.sectors.fit import compute_annual_oos_acceptance

    c0_full = compute_annual_oos_acceptance(
        full_dates, full_sectors, CORE_DIMENSION_IDS, full_returns, full_features, full_baseline, 3
    )
    assert c0_full["oos"]["n_dates"] != c0_masked_n


# ── 5. paired delta: planted signal recovered, pure noise does not pass ────────────────────


def test_planted_signal_gives_significant_paired_delta_t():
    dimension_scores, validation_returns = _paired_signal_frames(n_months=280, seed=7)
    candidate_dim_ids = CORE_DIMENSION_IDS + ["commodity"]
    dates, sectors, returns_mat, features_mat, baseline_mat = prepare_cross_section_matrices(
        validation_returns, dimension_scores, dimension_ids=candidate_dim_ids, horizon_months=3
    )
    masked = pack.build_masked_arms(
        dates, sectors, returns_mat, features_mat, baseline_mat, candidate_dim_ids, 3
    )
    paired = masked["paired_delta_ic"]
    assert paired["mean_ic"] is not None and paired["mean_ic"] > 0
    assert paired["t_stat"] is not None and paired["t_stat"] > 2.0


def test_pure_noise_candidate_does_not_pass():
    dimension_scores, validation_returns = _six_col_frames(n_months=280, invalid_first=0, seed=11)
    candidate_dim_ids = CORE_DIMENSION_IDS + ["commodity"]
    dates, sectors, returns_mat, features_mat, baseline_mat = prepare_cross_section_matrices(
        validation_returns, dimension_scores, dimension_ids=candidate_dim_ids, horizon_months=3
    )
    masked = pack.build_masked_arms(
        dates, sectors, returns_mat, features_mat, baseline_mat, candidate_dim_ids, 3
    )
    paired = masked["paired_delta_ic"]
    s_b = bool(evaluate_promotion(masked["Ck_masked"]).get("passed"))
    verdict = pack.sector_verdict(paired["mean_ic"], paired["t_stat"], s_b)
    assert verdict != "PASS"


# ── 6. truth tables ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "eligible,arm_precision,arm_auroc,arm_brier,base_precision,base_auroc,base_brier,expected",
    [
        (False, 0.90, 0.99, 0.01, 0.10, 0.99, 0.01, "INELIGIBLE_INFORMATION_ONLY"),
        (True, 0.45, 0.94, 0.06, 0.30, 0.94, 0.06, "MEETS_BAR"),
        (True, 0.35, 0.94, 0.06, 0.30, 0.94, 0.06, "EVIDENCE"),
        (True, 0.31, 0.94, 0.06, 0.30, 0.94, 0.06, "NO_EVIDENCE"),
        (True, 0.45, 0.90, 0.06, 0.30, 0.94, 0.06, "NO_EVIDENCE"),
        (True, 0.45, 0.94, 0.08, 0.30, 0.94, 0.06, "NO_EVIDENCE"),
        (True, None, None, None, 0.30, 0.94, 0.06, "NO_EVIDENCE"),
    ],
)
def test_recession_verdict_truth_table(
    eligible, arm_precision, arm_auroc, arm_brier, base_precision, base_auroc, base_brier, expected
):
    assert (
        pack.recession_verdict(
            eligible=eligible,
            arm_precision=arm_precision,
            arm_auroc=arm_auroc,
            arm_brier=arm_brier,
            base_precision=base_precision,
            base_auroc=base_auroc,
            base_brier=base_brier,
        )
        == expected
    )


@pytest.mark.parametrize(
    "mean_ic,t_stat,s_b,expected",
    [
        (0.05, 2.5, True, "PASS"),
        (0.05, 1.5, True, "IMPROVES_NOT_SIGNIFICANT"),
        (0.05, 2.5, False, "IMPROVES_NOT_SIGNIFICANT"),
        (-0.01, 2.5, True, "NO_IMPROVEMENT"),
        (0.0, 2.5, True, "NO_IMPROVEMENT"),
        (None, None, False, "NO_IMPROVEMENT"),
    ],
)
def test_sector_verdict_truth_table(mean_ic, t_stat, s_b, expected):
    assert pack.sector_verdict(mean_ic, t_stat, s_b) == expected


def test_sector_labels_multiplicity_and_late_start():
    assert pack.sector_labels("PASS", 2.1, "2005-09-01") == [
        "PASS_NOT_MULTIPLICITY_ROBUST",
        "PASS_LATE_START_FORWARD_ONLY",
    ]
    assert pack.sector_labels("PASS", 2.5, "1990-01-01") == []
    assert pack.sector_labels("PASS", 2.39, "1990-01-01") == []
    assert pack.sector_labels("IMPROVES_NOT_SIGNIFICANT", 1.0, "2005-09-01") == []


@pytest.mark.parametrize(
    "stop_reasons,rec_verdict,sec_verdict,expected",
    [
        (["S1: resolution too low"], "NO_EVIDENCE", "NO_IMPROVEMENT", "STOP"),
        ([], "MEETS_BAR", "NO_IMPROVEMENT", "CANDIDATE_FOR_S5.k-b"),
        ([], "EVIDENCE", "NO_IMPROVEMENT", "CANDIDATE_FOR_S5.k-b"),
        ([], "NO_EVIDENCE", "PASS", "CANDIDATE_FOR_S5.k-b"),
        ([], "INELIGIBLE_INFORMATION_ONLY", "NO_IMPROVEMENT", "SHADOW_INFORMATION_ONLY"),
        ([], "NO_EVIDENCE", "IMPROVES_NOT_SIGNIFICANT", "SHADOW_INFORMATION_ONLY"),
    ],
)
def test_step_outcome_truth_table(stop_reasons, rec_verdict, sec_verdict, expected):
    assert pack.step_outcome(stop_reasons, rec_verdict, sec_verdict) == expected


# ── 7. resolution STOP boundary ──────────────────────────────────────────────────────────────


def _resolution_frame(total_months: int, valid_count: int, valid_from: str = "2000-01-01") -> pd.DataFrame:
    dates = pd.date_range(valid_from, periods=total_months, freq="MS")
    valid_flags = [True] * valid_count + [False] * (total_months - valid_count)
    return pd.DataFrame({"evaluation_date": dates, "valid": valid_flags})


def test_resolution_stop_fires_at_0_949_not_at_0_95():
    frame_949 = _resolution_frame(1000, 949)
    frame_950 = _resolution_frame(1000, 950)
    ratio_949 = pack.resolution_ratio(frame_949, "2000-01-01")
    ratio_950 = pack.resolution_ratio(frame_950, "2000-01-01")
    assert ratio_949 == pytest.approx(0.949)
    assert ratio_950 == pytest.approx(0.95)
    assert ratio_949 < pack.RESOLUTION_MIN
    assert not (ratio_950 < pack.RESOLUTION_MIN)
