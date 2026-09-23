"""MRI Layer 2 (MRI-12) — pure transform functions for the shock taxonomy.

Moved out of `scripts/measure_shocks.py` (S4.3) so the S4.4 daily register builder can
reuse the exact same transform code the thresholds in `config/shocks.yaml` were measured
with, instead of a second implementation that could silently drift from it.
`scripts/measure_shocks.py` imports these rather than defining them locally.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def diff_63d_bp(values: pd.Series) -> pd.Series:
    """63-trading-day change in basis points: (x[t] - x[t-63]) * 100."""
    return (values - values.shift(63)) * 100.0


def log_change_63d_pct(values: pd.Series) -> pd.Series:
    """63-trading-day log change in percent: ln(x[t] / x[t-63]) * 100."""
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = values / values.shift(63)
        # Non-positive values cannot be logged
        log_ratio = np.where(ratio > 0, np.log(ratio), np.nan)
    return pd.Series(log_ratio * 100.0, index=values.index)


def claims_spike_pct(weekly_values: pd.Series) -> pd.Series:
    """Initial claims spike: (4-week mean / trailing 52-week min - 1) * 100."""
    mean_4w = weekly_values.rolling(4).mean()
    min_52w = weekly_values.rolling(52).min()
    return (mean_4w / min_52w - 1.0) * 100.0


def stitch_dollar(
    df_newer: pd.DataFrame,
    df_older: pd.DataFrame,
    overlap_start: str = "2006-01-01",
    overlap_end: str = "2019-12-31",
) -> tuple[pd.DataFrame, float, float]:
    """Stitch daily broad dollar indices per §3.3.

    Newer: DTWEXBGS (goods and services, 2006-01 onward).
    Older: DTWEXB (goods only, 1995-01-04 to 2019-12-31).

    Computes mean log ratio over overlapping trading days, shifts older index,
    and returns (stitched_df, mean_shift, residual_sd).
    """
    newer_clean = df_newer[df_newer["value"].notna()].copy()
    older_clean = df_older[df_older["value"].notna()].copy()

    newer_clean["date"] = pd.to_datetime(newer_clean["date"])
    older_clean["date"] = pd.to_datetime(older_clean["date"])

    overlap = pd.merge(newer_clean, older_clean, on="date", suffixes=("_newer", "_older"))
    overlap = overlap[
        (overlap["date"] >= overlap_start) & (overlap["date"] <= overlap_end)
    ]

    log_ratio = np.log(overlap["value_newer"] / overlap["value_older"])
    mean_shift = float(log_ratio.mean())
    residual_sd = float(log_ratio.std(ddof=1)) if len(log_ratio) > 1 else 0.0

    older_pre = older_clean[older_clean["date"] < overlap_start].copy()
    older_pre["value"] = np.exp(np.log(older_pre["value"]) + mean_shift)

    newer_post = newer_clean[newer_clean["date"] >= overlap_start].copy()

    stitched = (
        pd.concat([older_pre[["date", "value"]], newer_post[["date", "value"]]])
        .sort_values("date")
        .reset_index(drop=True)
    )
    return stitched, mean_shift, residual_sd
