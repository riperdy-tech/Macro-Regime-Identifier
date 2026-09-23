"""Tests for shock impact study statistical kernels and decision rules."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from macro_engine.sectors.validation import newey_west_t  # noqa: E402
from shock_impact_study import (  # noqa: E402
    compute_newey_west_ols,
    holm_bonferroni,
)


def test_newey_west_known_univariate_case() -> None:
    """Verify multivariate OLS Newey-West matches validation.py's univariate newey_west_t for X = 1."""
    np.random.seed(42)
    # Generate AR(1) series
    n = 150
    e = np.random.randn(n)
    y = np.zeros(n)
    for t in range(1, n):
        y[t] = 0.5 * y[t - 1] + e[t]

    X = np.ones((n, 1))
    lag = 2

    beta, se, t_stat, p_val = compute_newey_west_ols(X, y, lag=lag)
    t_univariate, sd_univariate = newey_west_t(list(y), lag=lag)

    assert pytest.approx(beta[0], rel=1e-7) == float(np.mean(y))
    assert t_univariate is not None
    assert pytest.approx(t_stat[0], rel=1e-6) == t_univariate


def test_holm_bonferroni_correctness() -> None:
    """Test step-down adjustment on hand-calculated known cases and properties."""
    # Case 1: 3 hypotheses
    raw_p = [0.01, 0.04, 0.03]
    # Sorted: 0.01 (factor 3 -> 0.03), 0.03 (factor 2 -> 0.06), 0.04 (factor 1 -> 0.04, max -> 0.06)
    adj = holm_bonferroni(raw_p)
    assert pytest.approx(adj[0], abs=1e-9) == 0.03
    assert pytest.approx(adj[1], abs=1e-9) == 0.06
    assert pytest.approx(adj[2], abs=1e-9) == 0.06

    # Case 2: Cap at 1.0
    large_p = [0.6, 0.8, 0.9]
    adj_large = holm_bonferroni(large_p)
    assert all(p <= 1.0 for p in adj_large)
    assert all(p >= raw for p, raw in zip(adj_large, large_p))

    # Case 3: Monotonicity with 28 hypotheses (matching S4 study)
    np.random.seed(123)
    p28 = np.sort(np.random.uniform(0.001, 0.5, 28))
    adj28 = holm_bonferroni(p28)
    assert all(adj28[i] <= adj28[i + 1] for i in range(len(adj28) - 1))


def test_planted_forward_risk_effect_detected() -> None:
    """Test that a planted economic effect in synthetic regime/return data is detected."""
    np.random.seed(999)
    n = 200

    # 5 regime probabilities summing to 1.0
    raw_probs = np.random.uniform(0.1, 1.0, (n, 5))
    probs = raw_probs / raw_probs.sum(axis=1, keepdims=True)

    # Shock flag active on ~15% of dates
    shock_flag = (np.random.uniform(0, 1, n) < 0.15).astype(float)
    # Ensure active in both halves
    shock_flag[20] = 1.0
    shock_flag[150] = 1.0

    # Planted effect: +5 percentage points (+0.05) return when shock fires
    regime_effects = np.array([0.02, -0.03, -0.01, 0.04, 0.01])
    noise = np.random.normal(0, 0.005, n)
    y = probs @ regime_effects + 0.05 * shock_flag + noise

    X = np.column_stack([probs, shock_flag])
    beta, se, t_stat, p_val = compute_newey_west_ols(X, y, lag=2)

    b_shock = beta[-1]
    t_shock = t_stat[-1]
    p_shock = p_val[-1]

    assert pytest.approx(b_shock, abs=0.01) == 0.05
    assert t_shock > 5.0
    assert p_shock < 1e-4

    # Half sample check
    h = n // 2
    b1, _, _, _ = compute_newey_west_ols(X[:h], y[:h], lag=2)
    b2, _, _, _ = compute_newey_west_ols(X[h:], y[h:], lag=2)
    assert b1[-1] > 0 and b2[-1] > 0


def test_noise_forward_risk_not_detected() -> None:
    """Test that pure noise with zero effect does not produce false positives."""
    np.random.seed(888)
    n = 200
    raw_probs = np.random.uniform(0.1, 1.0, (n, 5))
    probs = raw_probs / raw_probs.sum(axis=1, keepdims=True)
    shock_flag = (np.random.uniform(0, 1, n) < 0.1).astype(float)

    # Pure noise outcome
    y = np.random.normal(0, 0.05, n)
    X = np.column_stack([probs, shock_flag])
    beta, se, t_stat, p_val = compute_newey_west_ols(X, y, lag=2)

    # Economic size bar for 3m return is 0.02 (2.0 pp)
    econ_large = abs(beta[-1]) >= 0.02
    t_sig = abs(t_stat[-1]) >= 2.0
    # Over pure noise, either t fails or econ size fails
    assert not (econ_large and t_sig and p_val[-1] < 0.001)
