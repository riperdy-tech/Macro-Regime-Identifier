"""S3.3 offline probe: forward filter correctness and the no-look-ahead guarantee.

Loads scripts/probes/hmm_recession.py the same way tests/test_measure_baseline.py loads
scripts/measure_baseline.py, since scripts/ is not a package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "hmm_recession",
    Path(__file__).resolve().parents[1] / "scripts" / "probes" / "hmm_recession.py",
)
hmm_recession = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hmm_recession)  # type: ignore[union-attr]


# ── synthetic two-regime recovery ───────────────────────────────────────────


def _synthetic_two_state_series(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Three known segments over a 2-state, 3-dim system: state 0 for 60 months, state 1 for
    50, state 0 again for 70. Well-separated means (4 standard deviations apart) so the
    forward filter's argmax should recover the true path almost everywhere."""
    segment_lengths = [60, 50, 70]
    segment_states = [0, 1, 0]
    means = np.array([[1.5, 1.5, 1.5], [-1.5, -1.5, -1.5]])
    true_states = np.concatenate(
        [np.full(length, state) for length, state in zip(segment_lengths, segment_states)]
    )
    observations = np.array([means[state] + rng.normal(scale=0.5, size=3) for state in true_states])
    return observations, true_states


def test_forward_filter_recovers_known_regimes():
    rng = np.random.default_rng(0)
    observations, true_states = _synthetic_two_state_series(rng)
    mask = np.ones_like(observations, dtype=bool)

    prototypes = np.array([[1.5, 1.5, 1.5], [-1.5, -1.5, -1.5]])
    fit = hmm_recession.fit_hmm(observations, mask, prototypes, update_means=False)

    log_b = hmm_recession.emission_logprob(observations, mask, fit["means"], fit["variances"])
    log_alpha, _log_c = hmm_recession.forward_filter(log_b, fit["transition"], fit["initial"])
    filtered = np.exp(log_alpha)
    predicted_states = np.argmax(filtered, axis=1)

    accuracy = float(np.mean(predicted_states == true_states))
    assert accuracy > 0.90, f"forward filter only recovered {accuracy:.2%} of the known states"


def test_em_fit_separates_the_prototype_means_variant_too():
    """The em_moves_means variant should also recover the segments -- letting EM move the
    means from the prototype initialisation must not break recovery on well-separated data."""
    rng = np.random.default_rng(1)
    observations, true_states = _synthetic_two_state_series(rng)
    mask = np.ones_like(observations, dtype=bool)

    prototypes = np.array([[1.0, 1.0, 1.0], [-1.0, -1.0, -1.0]])  # deliberately off from the truth
    fit = hmm_recession.fit_hmm(observations, mask, prototypes, update_means=True)

    log_b = hmm_recession.emission_logprob(observations, mask, fit["means"], fit["variances"])
    log_alpha, _log_c = hmm_recession.forward_filter(log_b, fit["transition"], fit["initial"])
    predicted_states = np.argmax(np.exp(log_alpha), axis=1)

    # states are exchangeable (no ordering constraint on which fitted index is "0"); align by
    # majority vote in the first segment before comparing.
    if predicted_states[:10].mean() > 0.5:
        predicted_states = 1 - predicted_states
    accuracy = float(np.mean(predicted_states == true_states))
    assert accuracy > 0.85, f"em_moves_means forward filter only recovered {accuracy:.2%}"


# ── no-look-ahead guarantee ──────────────────────────────────────────────────


def test_forward_filter_no_lookahead():
    """A later observation must never change an earlier month's filtered probability: running
    the forward recursion on a truncated series must reproduce every earlier alpha exactly."""
    rng = np.random.default_rng(2)
    observations, _true_states = _synthetic_two_state_series(rng)
    mask = np.ones_like(observations, dtype=bool)

    prototypes = np.array([[1.5, 1.5, 1.5], [-1.5, -1.5, -1.5]])
    fit = hmm_recession.fit_hmm(observations, mask, prototypes, update_means=False)

    log_b_full = hmm_recession.emission_logprob(observations, mask, fit["means"], fit["variances"])
    log_alpha_full, _ = hmm_recession.forward_filter(log_b_full, fit["transition"], fit["initial"])
    alpha_full = np.exp(log_alpha_full)

    cutoff = 70  # an arbitrary earlier month
    for truncate_at in (cutoff + 1, cutoff + 10, len(observations)):
        log_b_truncated = hmm_recession.emission_logprob(
            observations[:truncate_at], mask[:truncate_at], fit["means"], fit["variances"]
        )
        log_alpha_truncated, _ = hmm_recession.forward_filter(
            log_b_truncated, fit["transition"], fit["initial"]
        )
        alpha_truncated = np.exp(log_alpha_truncated)
        np.testing.assert_allclose(
            alpha_truncated[:cutoff],
            alpha_full[:cutoff],
            atol=1e-12,
            err_msg=f"alpha[:{cutoff}] changed when the series was truncated at {truncate_at}",
        )


def test_forward_filter_no_lookahead_with_missing_dimensions():
    """The same guarantee holds when later months have missing dimensions the emission
    likelihood must marginalise out -- a later month's missingness pattern must not leak
    into an earlier alpha either."""
    rng = np.random.default_rng(3)
    observations, _true_states = _synthetic_two_state_series(rng)
    mask = np.ones_like(observations, dtype=bool)
    mask[100:120, 1] = False  # drop dimension 1 for months 100..119 (after the test cutoff)

    prototypes = np.array([[1.5, 1.5, 1.5], [-1.5, -1.5, -1.5]])
    fit = hmm_recession.fit_hmm(observations, mask, prototypes, update_means=False)

    log_b_full = hmm_recession.emission_logprob(observations, mask, fit["means"], fit["variances"])
    log_alpha_full, _ = hmm_recession.forward_filter(log_b_full, fit["transition"], fit["initial"])
    alpha_full = np.exp(log_alpha_full)

    cutoff = 70
    log_b_truncated = hmm_recession.emission_logprob(
        observations[:cutoff], mask[:cutoff], fit["means"], fit["variances"]
    )
    log_alpha_truncated, _ = hmm_recession.forward_filter(
        log_b_truncated, fit["transition"], fit["initial"]
    )
    alpha_truncated = np.exp(log_alpha_truncated)
    np.testing.assert_allclose(alpha_truncated, alpha_full[:cutoff], atol=1e-12)


def test_standardize_uses_only_training_rows():
    """standardize() must compute mean/sd from rows before train_end_exclusive only; appending
    wildly different future rows must not change the standardisation of the training rows."""
    rng = np.random.default_rng(4)
    base = rng.normal(loc=0.0, scale=1.0, size=(50, 2))
    mask = np.ones_like(base, dtype=bool)

    z_short, means_short, stds_short = hmm_recession.standardize(base, mask, 50)

    future = rng.normal(loc=1000.0, scale=50.0, size=(20, 2))
    extended = np.vstack([base, future])
    extended_mask = np.ones_like(extended, dtype=bool)
    z_long, means_long, stds_long = hmm_recession.standardize(extended, extended_mask, 50)

    np.testing.assert_allclose(means_short, means_long)
    np.testing.assert_allclose(stds_short, stds_long)
    np.testing.assert_allclose(z_short, z_long[:50])


def test_warmup_year_matches_180_month_minimum():
    years = np.array([1990 + i // 12 for i in range(500)])
    warmup = hmm_recession.warmup_year_for(years, min_training_months=180)
    # 1990-01..2004-12 is exactly 180 months, so 2005 is the first year with >=180 prior months.
    assert warmup == 2005


# ── small metric helpers, sanity checks only ────────────────────────────────


def test_precision_recall_and_auroc_sanity():
    probs = np.array([0.9, 0.8, 0.6, 0.4, 0.2, 0.1])
    labels = np.array([True, True, False, True, False, False])
    curve = hmm_recession.precision_recall_curve(probs, labels)
    best = hmm_recession.best_precision_at_recall(curve, 0.60)
    assert best is not None
    assert best["recall"] >= 0.60

    auc = hmm_recession.auroc(probs, labels)
    assert auc is not None
    assert 0.0 <= auc <= 1.0


def test_argmax_spell_stats_counts_switches():
    states = np.array(["a", "a", "a", "b", "b", "a", "a", "a", "a"])
    stats = hmm_recession.argmax_spell_stats(states)
    assert stats["switch_count"] == 2
    assert stats["n_spells"] == 3
    assert stats["median_spell_months"] == pytest.approx(3.0)
