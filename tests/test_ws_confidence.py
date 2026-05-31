"""Regime confidence = normalized peakedness (1 - entropy/ln N) x coverage.

Replaces the old raw top-2 probability gap, which pinned near zero over a 5-way
softmax (a clear plurality leader read ~5%, looking broken).
"""

from __future__ import annotations

import math

import pandas as pd

from macro_engine.regimes.scoring import _build_regime_health, _entropy, _normalized_peakedness


def test_peakedness_uniform_is_zero():
    n = 5
    uniform_entropy = math.log(n)  # max entropy
    assert _normalized_peakedness(uniform_entropy, n) == 0.0


def test_peakedness_one_hot_is_near_one():
    # Almost all mass on one regime -> entropy ~0 -> peakedness near 1.
    probs = [0.998, 0.0005, 0.0005, 0.0005, 0.0005]
    assert _normalized_peakedness(_entropy(probs), len(probs)) > 0.9


def test_peakedness_single_regime_is_one():
    assert _normalized_peakedness(0.0, 1) == 1.0


def test_peakedness_clamped_and_monotonic():
    n = 5
    decisive = _normalized_peakedness(_entropy([0.7, 0.1, 0.1, 0.05, 0.05]), n)
    mixed = _normalized_peakedness(_entropy([0.30, 0.28, 0.22, 0.12, 0.08]), n)
    assert 0.0 <= mixed < decisive <= 1.0


def _scores(date, probs, coverage=1.0):
    regimes = ["goldilocks", "reflation", "stagflation", "recession", "tightening"]
    return pd.DataFrame(
        {
            "regime_id": regimes,
            "date": [pd.Timestamp(date)] * len(regimes),
            "probability": probs,
            "coverage_ratio": [coverage] * len(regimes),
            "valid": [True] * len(regimes),
        }
    )


def test_regime_health_decisive_beats_mixed():
    decisive = _build_regime_health(_scores("2020-04-01", [0.02, 0.02, 0.02, 0.90, 0.04]))
    mixed = _build_regime_health(_scores("2026-05-01", [0.04, 0.41, 0.17, 0.03, 0.35]))
    c_decisive = float(decisive.iloc[0]["confidence"])
    c_mixed = float(mixed.iloc[0]["confidence"])
    assert c_decisive > 0.7  # near-certain regime reads high
    assert c_mixed < 0.4     # two-way tie reads modest
    assert c_decisive > c_mixed


def test_regime_health_coverage_scales_confidence():
    full = _build_regime_health(_scores("2020-04-01", [0.02, 0.02, 0.02, 0.90, 0.04], coverage=1.0))
    half = _build_regime_health(_scores("2020-04-01", [0.02, 0.02, 0.02, 0.90, 0.04], coverage=0.5))
    assert float(half.iloc[0]["confidence"]) < float(full.iloc[0]["confidence"])
