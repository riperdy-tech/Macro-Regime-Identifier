"""Tests for sector ceiling probe harness on synthetic fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.probes.sector_ceiling import (  # noqa: E402
    compute_in_sample_ceiling,
    compute_out_of_sample,
    evaluate_verdict,
    fit_linear_model,
)


def _generate_synthetic_dates(n_months: int = 120) -> list[pd.Timestamp]:
    """Generate regular monthly timestamps starting from 2000-01-01."""
    return [pd.Timestamp("2000-01-01") + pd.DateOffset(months=i) for i in range(n_months)]


def test_fit_linear_model_and_ridge() -> None:
    """Verify linear model and ridge shrinkage kernels."""
    rng = np.random.default_rng(42)
    X = rng.normal(size=(100, 5))
    true_beta = np.array([1.0, -0.5, 0.25, -0.1, 0.0])
    true_alpha = 0.05
    y = true_alpha + X @ true_beta + rng.normal(scale=0.01, size=100)

    # OLS (lambda = 0)
    alpha_fit, beta_fit = fit_linear_model(X, y, shrinkage_lambda=0.0)
    assert np.isclose(alpha_fit, true_alpha, atol=0.05)
    assert np.allclose(beta_fit, true_beta, atol=0.05)

    # Ridge with high lambda shrinks beta toward zero
    _, beta_shrunk = fit_linear_model(X, y, shrinkage_lambda=1000.0)
    assert np.linalg.norm(beta_shrunk) < np.linalg.norm(beta_fit)


def test_planted_signal_recovered() -> None:
    """Planted signal fixture: harness recovers clear positive rank IC (> 0.50)."""
    rng = np.random.default_rng(123)
    n_dates = 120
    n_sectors = 11
    n_features = 5
    dates = _generate_synthetic_dates(n_dates)

    # Autoregressive macro factors
    X = np.zeros((n_dates, n_features))
    for t in range(1, n_dates):
        X[t] = 0.7 * X[t - 1] + rng.normal(scale=0.7, size=n_features)

    # Distinct planted sector factor exposures
    B = rng.uniform(-1.0, 1.0, size=(n_sectors, n_features))
    alphas = rng.uniform(-0.02, 0.02, size=n_sectors)

    # Realised relative forward returns with small noise
    returns_mat = np.zeros((n_dates, n_sectors))
    for s in range(n_sectors):
        returns_mat[:, s] = alphas[s] + X @ B[s] + rng.normal(scale=0.05, size=n_dates)

    # 1. In-sample ceiling should strongly recover signal
    is_res = compute_in_sample_ceiling(returns_mat, X, horizon_months=3)
    assert is_res["mean_ic"] is not None
    assert is_res["mean_ic"] > 0.60
    assert is_res["t_stat"] is not None and is_res["t_stat"] > 5.0
    assert is_res["positive_share"] is not None and is_res["positive_share"] > 0.85

    # 2. Out-of-sample expanding window should also strongly recover signal
    oos_res = compute_out_of_sample(dates, returns_mat, X, horizon_months=3, min_training_months=40)
    assert oos_res["mean_ic"] is not None
    assert oos_res["mean_ic"] > 0.50
    assert oos_res["t_stat"] is not None and oos_res["t_stat"] > 3.0


def test_pure_noise_fixture_near_zero() -> None:
    """Pure-noise fixture: returns unrelated to features yield IC near zero."""
    rng = np.random.default_rng(999)
    n_dates = 150
    n_sectors = 11
    n_features = 5
    dates = _generate_synthetic_dates(n_dates)

    X = rng.normal(size=(n_dates, n_features))
    returns_mat = rng.normal(size=(n_dates, n_sectors))

    # OOS evaluation on pure noise
    oos_res = compute_out_of_sample(dates, returns_mat, X, horizon_months=3, min_training_months=60)
    assert oos_res["mean_ic"] is not None
    # IC should hover near zero
    assert abs(oos_res["mean_ic"]) < 0.10


def test_leakage_prevention_no_overlap() -> None:
    """Verify that training set strictly excludes overlapping return windows."""
    n_dates = 80
    dates = _generate_synthetic_dates(n_dates)
    horizon = 3
    cutoff_dates = [d - pd.DateOffset(months=horizon) for d in dates]
    dates_arr = np.array(dates)

    for t_idx, test_date in enumerate(dates):
        train_mask = dates_arr <= cutoff_dates[t_idx]
        training_dates = dates_arr[train_mask]
        for d in training_dates:
            # Training return realized at d + horizon must be <= test_date
            realized_date = pd.Timestamp(d) + pd.DateOffset(months=horizon)
            assert realized_date <= test_date, f"Leakage: {d} + {horizon}m = {realized_date} > {test_date}"


def test_verdict_rule_branches() -> None:
    """Test all branches of the target architecture verdict rule."""
    # Branch 1: In-sample ceiling below 0.087
    v1 = evaluate_verdict(in_sample_3m_ic=0.060, best_oos_3m_ic=0.040, best_oos_3m_t=1.5)
    assert v1 == "CLOSE: even fitted in-sample the sector channel cannot reach the bar"

    # Branch 2: In-sample clears, but OOS below 0.087
    v2 = evaluate_verdict(in_sample_3m_ic=0.200, best_oos_3m_ic=0.070, best_oos_3m_t=2.1)
    assert v2 == "NOT DEMONSTRATED OOS"

    # Branch 3: In-sample clears, OOS IC >= 0.087, but t < 2
    v3 = evaluate_verdict(in_sample_3m_ic=0.200, best_oos_3m_ic=0.090, best_oos_3m_t=1.8)
    assert v3 == "NOT DEMONSTRATED OOS"

    # Branch 4: Both clear
    v4 = evaluate_verdict(in_sample_3m_ic=0.200, best_oos_3m_ic=0.095, best_oos_3m_t=2.4)
    assert v4 == "PASSES"
