"""Tests for scripts/measure_shocks.py (MRI S4.3).

Synthetic series only -- never reads the live store.
Tests:
- 63-day change (diff_63d_bp)
- log change (log_change_63d_pct)
- claims spike (claims_spike_pct)
- dollar stitch (constant log shift preserves 63-day log changes exactly)
- episode grouping (gap <= 2 months)
- decay counting
- percentile computation
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

_SPEC = importlib.util.spec_from_file_location(
    "measure_shocks",
    Path(__file__).resolve().parents[1] / "scripts" / "measure_shocks.py",
)
measure = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(measure)


def test_diff_63d_bp():
    # 100 days of linear yield in percent: 2.00, 2.01, ..., 2.99
    values = pd.Series([2.0 + 0.01 * i for i in range(100)])
    diff = measure.diff_63d_bp(values)

    assert len(diff) == 100
    assert pd.isna(diff.iloc[0])
    assert pd.isna(diff.iloc[62])
    # At index 63: change is (2.63 - 2.00) = 0.63% = 63 bp
    assert np.isclose(diff.iloc[63], 63.0)
    assert np.isclose(diff.iloc[99], 63.0)


def test_log_change_63d_pct():
    # Index with 100 observations
    values = pd.Series(100.0, index=range(100))
    # At index 63, price jumps to 150.0
    values.iloc[63] = 150.0
    # At index 64, price drops to 50.0
    values.iloc[64] = 50.0

    chg = measure.log_change_63d_pct(values)

    assert len(chg) == 100
    assert pd.isna(chg.iloc[0])
    assert pd.isna(chg.iloc[62])
    # Up side: ln(150 / 100) * 100
    expected_up = np.log(1.5) * 100.0
    assert np.isclose(chg.iloc[63], expected_up)
    # Down side: ln(50 / 100) * 100
    expected_down = np.log(0.5) * 100.0
    assert np.isclose(chg.iloc[64], expected_down)


def test_claims_spike_pct():
    # 60 weekly observations: first 52 are 200.0, week 53 jumps to 300.0
    vals = [200.0] * 52 + [300.0] + [200.0] * 7
    series = pd.Series(vals)

    spike = measure.claims_spike_pct(series)

    assert len(spike) == 60
    assert pd.isna(spike.iloc[50])  # Needs at least 52 observations for min_52w
    # At week 52 (the 53rd week, index 52):
    # trailing 52-week min = 200.0
    # 4-week mean of index 49, 50, 51, 52 = (200 + 200 + 200 + 300) / 4 = 225.0
    # spike = (225 / 200 - 1) * 100 = 12.5%
    assert np.isclose(spike.iloc[52], 12.5)


def test_stitch_preserves_63d_log_change_exactly():
    # Synthetic daily series
    dates = pd.date_range("1995-01-01", "2010-12-31", freq="B")
    np.random.seed(42)
    # Base random walk for older index
    log_prices_older = np.cumsum(np.random.normal(0, 0.01, len(dates))) + 4.5
    prices_older = np.exp(log_prices_older)
    df_older = pd.DataFrame({"date": dates, "value": prices_older})

    # Newer index exists from 2006-01-01 onward, with an exact shift of -0.0890
    shift_true = -0.0890
    dates_newer = dates[dates >= "2006-01-01"]
    prices_newer = np.exp(np.log(df_older[df_older["date"] >= "2006-01-01"]["value"]) + shift_true)
    df_newer = pd.DataFrame({"date": dates_newer, "value": prices_newer})

    # Run stitch
    df_stitched, shift_est, sd_est = measure.stitch_dollar(
        df_newer=df_newer,
        df_older=df_older,
        overlap_start="2006-01-01",
        overlap_end="2010-12-31",
    )

    assert np.isclose(shift_est, shift_true, atol=1e-10)
    assert np.isclose(sd_est, 0.0, atol=1e-10)

    # Key property: before 2006-01-01, 63-day log change of stitched series
    # MUST EQUAL 63-day log change of older series EXACTLY
    chg_older = measure.log_change_63d_pct(df_older["value"])
    chg_stitched = measure.log_change_63d_pct(df_stitched["value"])

    pre_2006_mask = (df_stitched["date"] < "2006-01-01") & chg_older.notna()
    diff = chg_stitched[pre_2006_mask] - chg_older[pre_2006_mask]
    assert np.max(np.abs(diff)) < 1e-11, "Stitch must preserve 63-day log change exactly"


def test_episode_grouping():
    # Gap <= 2 months merges episodes; gap >= 3 starts new episode
    periods = [
        pd.Period("2020-01", "M"),
        pd.Period("2020-02", "M"),  # gap 0 -> same
        pd.Period("2020-04", "M"),  # gap 1 (Mar missing) -> same
        pd.Period("2020-07", "M"),  # gap 2 (May, Jun missing) -> same
        pd.Period("2020-11", "M"),  # gap 3 (Aug, Sep, Oct missing) -> NEW episode
        pd.Period("2020-12", "M"),  # gap 0 -> same
    ]

    episodes = measure.group_episodes(periods, max_gap_months=2)

    assert len(episodes) == 2
    assert episodes[0] == [
        pd.Period("2020-01", "M"),
        pd.Period("2020-02", "M"),
        pd.Period("2020-04", "M"),
        pd.Period("2020-07", "M"),
    ]
    assert episodes[1] == [
        pd.Period("2020-11", "M"),
        pd.Period("2020-12", "M"),
    ]


def test_decay_counting():
    # Episode starting at 2020-01 (first severity-1 crossing)
    months = pd.period_range("2020-01", "2020-08", freq="M")
    # Values start above 30, then decay
    # Month 0 (Jan): 35
    # Month 1 (Feb): 32
    # Month 2 (Mar): 28
    # Month 3 (Apr): 22
    # Month 4 (May): 19 (< 20)
    # Month 5 (Jun): 18
    # Month 6 (Jul): 14 (< 15)
    # Month 7 (Aug): 9  (< 10)
    values = [35.0, 32.0, 28.0, 22.0, 19.0, 18.0, 14.0, 9.0]
    df = pd.DataFrame({"month": months, "value": values}).set_index("month")

    episode = [pd.Period("2020-01", "M"), pd.Period("2020-02", "M")]

    # Candidate retire levels: 20, 15, 10
    decay_20 = measure.measure_episode_decay(df["value"], episode, retire_level=20.0, side="up")
    decay_15 = measure.measure_episode_decay(df["value"], episode, retire_level=15.0, side="up")
    decay_10 = measure.measure_episode_decay(df["value"], episode, retire_level=10.0, side="up")

    # From 2020-01 to 2020-05 is 4 months
    assert decay_20["months_to_retire"] == 4
    assert decay_20["retire_month"] == "2020-05"

    # From 2020-01 to 2020-07 is 6 months
    assert decay_15["months_to_retire"] == 6
    assert decay_15["retire_month"] == "2020-07"

    # From 2020-01 to 2020-08 is 7 months
    assert decay_10["months_to_retire"] == 7
    assert decay_10["retire_month"] == "2020-08"


def test_percentile_computation():
    # 100 values: 1 to 100
    series = pd.Series(range(1, 101), dtype=float)

    # Up side: candidate threshold 95
    # Values < 95 are 1..94 (94 values) -> percentile 94%
    p_up_95 = measure.compute_percentile_for_threshold(series, threshold=95.0, side="up")
    assert np.isclose(p_up_95, 94.0)

    # Exact p95 threshold
    p95_val = measure.compute_exact_percentile_threshold(series, percentile=95.0, side="up")
    assert np.isclose(p95_val, 95.05)

    # Down side: candidate threshold 5
    # Values <= 5 are 1..5 (5 values) -> percentile 5%
    p_down_5 = measure.compute_percentile_for_threshold(series, threshold=5.0, side="down")
    assert np.isclose(p_down_5, 5.0)

    # Exact p95 for down side is 5th percentile
    p5_val = measure.compute_exact_percentile_threshold(series, percentile=95.0, side="down")
    assert np.isclose(p5_val, 5.95)
