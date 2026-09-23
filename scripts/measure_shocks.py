#!/usr/bin/env python3
"""MRI S4.3 — shock measurement and decay analysis.

Reads historical series from DuckDB (never writes to the live store).
Computes daily transforms, month-end distributions, exact percentiles, candidate
threshold tables, episode grouping (gap <= 2 months), decay tables, and the
monthly shock-state panel for the impact study.

Outputs:
  - thresholds.json / thresholds.md
  - decay.json / decay.md
  - panel.parquet
  - provisional_thresholds.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

# Default provisional thresholds per MRI_S4_PLAN D1 / architecture §3.2
DEFAULT_PROVISIONAL_THRESHOLDS: dict[str, Any] = {
    "volatility_shock": {
        "series_id": "VIXCLS",
        "sample_start": "1990-01",
        "signed": False,
        "severity_1": 30.0,
        "severity_2": 40.0,
        "retire_level": 20.0,
        "candidate_retires": [10.0, 15.0, 20.0, 25.0, 30.0],
    },
    "credit_shock": {
        "series_id": "BAA10Y",
        "sample_start": "1986-01",
        "signed": False,
        "severity_1": 50.0,
        "severity_2": 75.0,
        "retire_level": 0.0,
        "candidate_retires": [-25.0, -12.5, 0.0, 12.5, 25.0],
    },
    "rates_shock": {
        "series_id": "DFII10",
        "proxy_series_id": "DGS2",
        "sample_start": "1986-01",
        "dfii10_start": "2003-01",
        "signed": True,
        "dfii10": {
            "severity_1_up": 75.0,
            "severity_2_up": 100.0,
            "severity_1_down": -75.0,
            "severity_2_down": -100.0,
        },
        "dgs2": {
            "severity_1_up": 100.0,
            "severity_2_up": 150.0,
            "severity_1_down": -100.0,
            "severity_2_down": -150.0,
        },
        "retire_level": 25.0,
        "candidate_retires": [12.5, 18.75, 25.0, 31.25, 37.5],
    },
    "oil_shock": {
        "series_id": "DCOILWTICO",
        "sample_start": "1986-01",
        "signed": True,
        "severity_1_up": 30.0,
        "severity_2_up": 40.0,
        "severity_1_down": -30.0,
        "severity_2_down": -40.0,
        "retire_level": 10.0,
        "candidate_retires": [5.0, 7.5, 10.0, 12.5, 15.0],
    },
    "dollar_shock": {
        "series_id": "usd_stitched",
        "sample_start": "1995-01",
        "signed": True,
        "severity_1_up": 5.0,
        "severity_2_up": 6.0,
        "severity_1_down": -5.0,
        "severity_2_down": -6.0,
        "retire_level": 10.0,
        "candidate_retires": [1.25, 1.875, 2.5, 3.125, 3.75, 5.0, 7.5, 10.0, 12.5, 15.0],
    },
    "labour_shock": {
        "series_id": "ICSA",
        "sample_start": "1990-01",
        "signed": False,
        "severity_1": 20.0,
        "severity_2": 30.0,
        "retire_level": 10.0,
        "candidate_retires": [5.0, 7.5, 10.0, 12.5, 15.0],
    },
    "inflation_shock": {
        "series_id": "T10YIE",
        "sample_start": "2003-01",
        "signed": True,
        "severity_1_up": 42.0,
        "severity_2_up": 54.0,
        "severity_1_down": -43.0,
        "severity_2_down": -64.0,
        "retire_level": 20.0,
        "candidate_retires": [10.0, 15.0, 20.0, 25.0, 30.0],
    },
}


# -----------------------------------------------------------------------------
# Pure Transform Functions
# -----------------------------------------------------------------------------


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


def group_episodes(
    firing_month_periods: list[pd.Period] | pd.Series, max_gap_months: int = 2
) -> list[list[pd.Period]]:
    """Group consecutive firing months with gaps <= max_gap_months into episodes."""
    if isinstance(firing_month_periods, pd.Series):
        periods = sorted(firing_month_periods.unique())
    else:
        periods = sorted(set(firing_month_periods))

    if not periods:
        return []

    episodes: list[list[pd.Period]] = []
    current_ep: list[pd.Period] = [periods[0]]

    for m in periods[1:]:
        last_m = current_ep[-1]
        gap = (m.year * 12 + m.month) - (last_m.year * 12 + last_m.month) - 1
        if gap <= max_gap_months:
            current_ep.append(m)
        else:
            episodes.append(current_ep)
            current_ep = [m]
    if current_ep:
        episodes.append(current_ep)

    return episodes


def compute_percentile_for_threshold(
    month_end_values: pd.Series, threshold: float, side: str = "up"
) -> float:
    """Calculate the historical percentile corresponding to a threshold value."""
    clean = month_end_values.dropna()
    if clean.empty:
        return 0.0
    if side == "up":
        # Share of sample strictly below threshold
        return float((clean < threshold).mean() * 100.0)
    else:
        # Down side: share of sample at or below negative threshold
        return float((clean <= threshold).mean() * 100.0)


def compute_exact_percentile_threshold(
    month_end_values: pd.Series, percentile: float, side: str = "up"
) -> float:
    """Calculate the exact threshold value at a given target percentile."""
    clean = month_end_values.dropna()
    if clean.empty:
        return 0.0
    if side == "up":
        return float(np.percentile(clean, percentile))
    else:
        # Down side: target p95 is bottom 5% (100 - 95 = 5)
        return float(np.percentile(clean, 100.0 - percentile))


def measure_episode_decay(
    month_end_series: pd.Series,
    episode: list[pd.Period],
    retire_level: float,
    side: str = "up",
) -> dict[str, Any]:
    """Measure months from first crossing until transform satisfies retire condition."""
    first_month = episode[0]
    after = month_end_series[month_end_series.index >= first_month]

    if side == "up":
        retired = after[after < retire_level]
    elif side == "down":
        retired = after[after > retire_level]
    else:  # signed / inside range [-retire_level, +retire_level]
        retired = after[after.abs() <= retire_level]

    if not retired.empty:
        retire_month = retired.index[0]
        months_to_retire = (retire_month.year * 12 + retire_month.month) - (
            first_month.year * 12 + first_month.month
        )
        return {
            "first_month": str(first_month),
            "retire_month": str(retire_month),
            "months_to_retire": int(months_to_retire),
            "closed": True,
        }
    else:
        # Unclosed at end of sample
        last_month = month_end_series.index[-1]
        elapsed = (last_month.year * 12 + last_month.month) - (
            first_month.year * 12 + first_month.month
        )
        return {
            "first_month": str(first_month),
            "retire_month": None,
            "months_to_retire": int(elapsed),
            "closed": False,
        }


# -----------------------------------------------------------------------------
# Data Loader and Measure Execution
# -----------------------------------------------------------------------------


def load_raw_series(con: duckdb.DuckDBPyConnection, series_id: str) -> pd.DataFrame:
    query = """
        SELECT date, value 
        FROM raw_observations 
        WHERE series_id = ? AND value IS NOT NULL 
        ORDER BY date
    """
    df = con.execute(query, [series_id]).df()
    df["date"] = pd.to_datetime(df["date"])
    return df


def build_daily_and_monthly_transforms(
    con: duckdb.DuckDBPyConnection,
) -> dict[str, dict[str, Any]]:
    """Compute daily and month-end transform series for all 7 shocks."""
    results: dict[str, dict[str, Any]] = {}

    # 1. Volatility (VIXCLS)
    df_vix = load_raw_series(con, "VIXCLS")
    df_vix["transform"] = df_vix["value"]
    df_vix["ym"] = df_vix["date"].dt.to_period("M")
    me_vix = df_vix.groupby("ym").last()
    results["volatility_shock"] = {
        "daily": df_vix,
        "month_end": me_vix,
        "sample_start": "1990-01",
        "signed": False,
        "unit": "index",
    }

    # 2. Credit (BAA10Y)
    df_baa = load_raw_series(con, "BAA10Y")
    df_baa["transform"] = diff_63d_bp(df_baa["value"])
    df_baa["ym"] = df_baa["date"].dt.to_period("M")
    me_baa = df_baa.groupby("ym").last()
    results["credit_shock"] = {
        "daily": df_baa,
        "month_end": me_baa,
        "sample_start": "1986-01",
        "signed": False,
        "unit": "bp",
    }

    # 3. Rates (DFII10 with DGS2 proxy)
    df_dfii = load_raw_series(con, "DFII10")
    df_dfii["transform"] = diff_63d_bp(df_dfii["value"])
    df_dfii["ym"] = df_dfii["date"].dt.to_period("M")
    me_dfii = df_dfii.groupby("ym").last()

    df_dgs2 = load_raw_series(con, "DGS2")
    df_dgs2["transform"] = diff_63d_bp(df_dgs2["value"])
    df_dgs2["ym"] = df_dgs2["date"].dt.to_period("M")
    me_dgs2 = df_dgs2.groupby("ym").last()

    results["rates_shock"] = {
        "daily_dfii10": df_dfii,
        "month_end_dfii10": me_dfii,
        "daily_dgs2": df_dgs2,
        "month_end_dgs2": me_dgs2,
        "sample_start": "1986-01",
        "dfii10_start": "2003-01",
        "signed": True,
        "unit": "bp",
    }

    # 4. Oil (DCOILWTICO)
    df_oil = load_raw_series(con, "DCOILWTICO")
    df_oil["transform"] = log_change_63d_pct(df_oil["value"])
    df_oil["ym"] = df_oil["date"].dt.to_period("M")
    me_oil = df_oil.groupby("ym").last()
    results["oil_shock"] = {
        "daily": df_oil,
        "month_end": me_oil,
        "sample_start": "1986-01",
        "signed": True,
        "unit": "%",
    }

    # 5. Dollar (stitched DTWEXBGS / DTWEXB)
    df_gs = load_raw_series(con, "DTWEXBGS")
    df_b = load_raw_series(con, "DTWEXB")
    df_stitch, mean_shift, residual_sd = stitch_dollar(df_gs, df_b)
    df_stitch["transform"] = log_change_63d_pct(df_stitch["value"])
    df_stitch["ym"] = df_stitch["date"].dt.to_period("M")
    me_usd = df_stitch.groupby("ym").last()
    results["dollar_shock"] = {
        "daily": df_stitch,
        "month_end": me_usd,
        "mean_shift": mean_shift,
        "residual_sd": residual_sd,
        "sample_start": "1995-01",
        "signed": True,
        "unit": "%",
    }

    # 6. Labour (ICSA)
    df_icsa = load_raw_series(con, "ICSA")
    df_icsa["transform"] = claims_spike_pct(df_icsa["value"])
    df_icsa["ym"] = df_icsa["date"].dt.to_period("M")
    me_icsa = df_icsa.groupby("ym").last()
    results["labour_shock"] = {
        "daily": df_icsa,
        "month_end": me_icsa,
        "sample_start": "1990-01",
        "signed": False,
        "unit": "%",
    }

    # 7. Inflation (T10YIE)
    df_inf = load_raw_series(con, "T10YIE")
    df_inf["transform"] = diff_63d_bp(df_inf["value"])
    df_inf["ym"] = df_inf["date"].dt.to_period("M")
    me_inf = df_inf.groupby("ym").last()
    results["inflation_shock"] = {
        "daily": df_inf,
        "month_end": me_inf,
        "sample_start": "2003-01",
        "signed": True,
        "unit": "bp",
    }

    return results


def build_threshold_analysis(
    transforms: dict[str, dict[str, Any]], thresholds_cfg: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Generate threshold tables, percentiles, episode counts and markdown."""
    report_dict: dict[str, Any] = {}
    md_lines: list[str] = [
        "# MRI Shock Register: Threshold & Episode Analysis",
        "",
        "Measured on historical FRED store copy per `P0_0_MRI_TARGET_ARCHITECTURE.md` §3.2 and `MRI_S4_PLAN.md` D1/D2.",
        "Episodes group consecutive firing months with gaps ≤ 2 months.",
        "",
        "| shock_id | leg / side | measure | sample | severity 1 (p) | severity 2 (p) | exact p95 | exact p98 | episodes (sev 1) | episodes (sev 2) | episode list (sev 2) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for shock_id, cfg in thresholds_cfg.items():
        data = transforms[shock_id]
        report_dict[shock_id] = {}

        if shock_id == "rates_shock":
            # Two legs: DFII10 (real rates, 2003-) and DGS2 (policy rate, 1990-)
            legs = [
                ("dfii10", data["month_end_dfii10"], "2003-01", cfg["dfii10"]),
                ("dgs2", data["month_end_dgs2"], "1990-01", cfg["dgs2"]),
            ]
            for leg_name, me_df, start_m, leg_cfg in legs:
                me_sub = me_df[me_df.index >= start_m]["transform"].dropna()
                # Up side
                p95_up = compute_exact_percentile_threshold(me_sub, 95.0, "up")
                p98_up = compute_exact_percentile_threshold(me_sub, 98.0, "up")
                s1_up = leg_cfg["severity_1_up"]
                s2_up = leg_cfg["severity_2_up"]
                p_s1_up = compute_percentile_for_threshold(me_sub, s1_up, "up")
                p_s2_up = compute_percentile_for_threshold(me_sub, s2_up, "up")

                ep_s1_up = group_episodes(me_sub[me_sub >= s1_up].index)
                ep_s2_up = group_episodes(me_sub[me_sub >= s2_up].index)

                # Down side
                p95_dn = compute_exact_percentile_threshold(me_sub, 95.0, "down")
                p98_dn = compute_exact_percentile_threshold(me_sub, 98.0, "down")
                s1_dn = leg_cfg["severity_1_down"]
                s2_dn = leg_cfg["severity_2_down"]
                p_s1_dn = compute_percentile_for_threshold(me_sub, s1_dn, "down")
                p_s2_dn = compute_percentile_for_threshold(me_sub, s2_dn, "down")

                ep_s1_dn = group_episodes(me_sub[me_sub <= s1_dn].index)
                ep_s2_dn = group_episodes(me_sub[me_sub <= s2_dn].index)

                def ep_str(eps):
                    return ", ".join(
                        f"{ep[0]}→{ep[-1]}" if len(ep) > 1 else str(ep[0]) for ep in eps
                    )

                report_dict[shock_id][f"{leg_name}_up"] = {
                    "sample": f"{start_m}..{me_sub.index[-1]} (n={len(me_sub)})",
                    "severity_1": {"threshold": s1_up, "percentile": round(p_s1_up, 1), "episodes": len(ep_s1_up)},
                    "severity_2": {"threshold": s2_up, "percentile": round(p_s2_up, 1), "episodes": len(ep_s2_up), "episode_list": ep_str(ep_s2_up)},
                    "exact_p95": round(p95_up, 1),
                    "exact_p98": round(p98_up, 1),
                }
                report_dict[shock_id][f"{leg_name}_down"] = {
                    "sample": f"{start_m}..{me_sub.index[-1]} (n={len(me_sub)})",
                    "severity_1": {"threshold": s1_dn, "percentile": round(p_s1_dn, 1), "episodes": len(ep_s1_dn)},
                    "severity_2": {"threshold": s2_dn, "percentile": round(p_s2_dn, 1), "episodes": len(ep_s2_dn), "episode_list": ep_str(ep_s2_dn)},
                    "exact_p95": round(p95_dn, 1),
                    "exact_p98": round(p98_dn, 1),
                }

                md_lines.append(
                    f"| `rates_shock` | {leg_name.upper()} up | 63d diff bp | {start_m}→ | **≥ +{s1_up:.0f}** (p{p_s1_up:.0f}) | **≥ +{s2_up:.0f}** (p{p_s2_up:.1f}) | +{p95_up:.1f} | +{p98_up:.1f} | {len(ep_s1_up)} | {len(ep_s2_up)} | {ep_str(ep_s2_up)} |"
                )
                md_lines.append(
                    f"| `rates_shock` | {leg_name.upper()} down | 63d diff bp | {start_m}→ | **≤ {s1_dn:.0f}** (p{p_s1_dn:.0f}) | **≤ {s2_dn:.0f}** (p{p_s2_dn:.1f}) | {p95_dn:.1f} | {p98_dn:.1f} | {len(ep_s1_dn)} | {len(ep_s2_dn)} | {ep_str(ep_s2_dn)} |"
                )
            continue

        me_series = data["month_end"]
        start_m = cfg["sample_start"]
        me_sub = me_series[me_series.index >= start_m]["transform"].dropna()

        def ep_str(eps):
            return ", ".join(
                f"{ep[0]}→{ep[-1]}" if len(ep) > 1 else str(ep[0]) for ep in eps
            )

        if cfg["signed"]:
            # Up side
            p95_up = compute_exact_percentile_threshold(me_sub, 95.0, "up")
            p98_up = compute_exact_percentile_threshold(me_sub, 98.0, "up")
            s1_up = cfg["severity_1_up"]
            s2_up = cfg["severity_2_up"]
            p_s1_up = compute_percentile_for_threshold(me_sub, s1_up, "up")
            p_s2_up = compute_percentile_for_threshold(me_sub, s2_up, "up")
            ep_s1_up = group_episodes(me_sub[me_sub >= s1_up].index)
            ep_s2_up = group_episodes(me_sub[me_sub >= s2_up].index)

            # Down side
            p95_dn = compute_exact_percentile_threshold(me_sub, 95.0, "down")
            p98_dn = compute_exact_percentile_threshold(me_sub, 98.0, "down")
            s1_dn = cfg["severity_1_down"]
            s2_dn = cfg["severity_2_down"]
            p_s1_dn = compute_percentile_for_threshold(me_sub, s1_dn, "down")
            p_s2_dn = compute_percentile_for_threshold(me_sub, s2_dn, "down")
            ep_s1_dn = group_episodes(me_sub[me_sub <= s1_dn].index)
            ep_s2_dn = group_episodes(me_sub[me_sub <= s2_dn].index)

            report_dict[shock_id]["up"] = {
                "sample": f"{start_m}..{me_sub.index[-1]} (n={len(me_sub)})",
                "severity_1": {"threshold": s1_up, "percentile": round(p_s1_up, 1), "episodes": len(ep_s1_up)},
                "severity_2": {"threshold": s2_up, "percentile": round(p_s2_up, 1), "episodes": len(ep_s2_up), "episode_list": ep_str(ep_s2_up)},
                "exact_p95": round(p95_up, 1),
                "exact_p98": round(p98_up, 1),
            }
            report_dict[shock_id]["down"] = {
                "sample": f"{start_m}..{me_sub.index[-1]} (n={len(me_sub)})",
                "severity_1": {"threshold": s1_dn, "percentile": round(p_s1_dn, 1), "episodes": len(ep_s1_dn)},
                "severity_2": {"threshold": s2_dn, "percentile": round(p_s2_dn, 1), "episodes": len(ep_s2_dn), "episode_list": ep_str(ep_s2_dn)},
                "exact_p95": round(p95_dn, 1),
                "exact_p98": round(p98_dn, 1),
            }

            md_lines.append(
                f"| `{shock_id}` | up | {data['unit']} | {start_m}→ | **≥ +{s1_up:.0f}** (p{p_s1_up:.0f}) | **≥ +{s2_up:.0f}** (p{p_s2_up:.1f}) | +{p95_up:.1f} | +{p98_up:.1f} | {len(ep_s1_up)} | {len(ep_s2_up)} | {ep_str(ep_s2_up)} |"
            )
            md_lines.append(
                f"| `{shock_id}` | down | {data['unit']} | {start_m}→ | **≤ {s1_dn:.0f}** (p{p_s1_dn:.0f}) | **≤ {s2_dn:.0f}** (p{p_s2_dn:.1f}) | {p95_dn:.1f} | {p98_dn:.1f} | {len(ep_s1_dn)} | {len(ep_s2_dn)} | {ep_str(ep_s2_dn)} |"
            )
        else:
            # One-sided (VIXCLS, BAA10Y, ICSA)
            p95 = compute_exact_percentile_threshold(me_sub, 95.0, "up")
            p98 = compute_exact_percentile_threshold(me_sub, 98.0, "up")
            s1 = cfg["severity_1"]
            s2 = cfg["severity_2"]
            p_s1 = compute_percentile_for_threshold(me_sub, s1, "up")
            p_s2 = compute_percentile_for_threshold(me_sub, s2, "up")
            ep_s1 = group_episodes(me_sub[me_sub >= s1].index)
            ep_s2 = group_episodes(me_sub[me_sub >= s2].index)

            report_dict[shock_id]["up"] = {
                "sample": f"{start_m}..{me_sub.index[-1]} (n={len(me_sub)})",
                "severity_1": {"threshold": s1, "percentile": round(p_s1, 1), "episodes": len(ep_s1)},
                "severity_2": {"threshold": s2, "percentile": round(p_s2, 1), "episodes": len(ep_s2), "episode_list": ep_str(ep_s2)},
                "exact_p95": round(p95, 1),
                "exact_p98": round(p98, 1),
            }
            prefix = "+" if shock_id in ("credit_shock", "labour_shock") else ""
            md_lines.append(
                f"| `{shock_id}` | up | {data['unit']} | {start_m}→ | **≥ {prefix}{s1:.0f}** (p{p_s1:.0f}) | **≥ {prefix}{s2:.0f}** (p{p_s2:.1f}) | {p95:.1f} | {p98:.1f} | {len(ep_s1)} | {len(ep_s2)} | {ep_str(ep_s2)} |"
            )

    return report_dict, "\n".join(md_lines) + "\n"


def build_decay_analysis(
    transforms: dict[str, dict[str, Any]], thresholds_cfg: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Generate decay tables, medians and ranges per candidate retire level."""
    report_dict: dict[str, Any] = {}
    md_lines: list[str] = [
        "# MRI Shock Register: Decay Analysis & Episode Lifespans",
        "",
        "Months from first severity-1 crossing until the transform falls below each candidate retire level.",
        "Measured on historical episodes (gap ≤ 2 months) per architecture §3.4.",
        "",
        "| shock_id | leg / side | candidate retire level | role | median episode months | range (min–max) | unclosed episodes |",
        "|---|---|---|---|---|---|---|",
    ]

    for shock_id, cfg in thresholds_cfg.items():
        data = transforms[shock_id]
        report_dict[shock_id] = {}

        if shock_id == "rates_shock":
            legs = [
                ("dfii10", data["month_end_dfii10"], "2003-01", cfg["dfii10"]["severity_1_up"]),
                ("dgs2", data["month_end_dgs2"], "1990-01", cfg["dgs2"]["severity_1_up"]),
            ]
            for leg_name, me_df, start_m, s1_thresh in legs:
                me_sub = me_df[me_df.index >= start_m]["transform"].dropna()
                episodes = group_episodes(me_sub[me_sub.abs() >= s1_thresh].index)
                report_dict[shock_id][leg_name] = {"episodes": len(episodes), "candidates": []}

                for r_lvl in cfg["candidate_retires"]:
                    decays = [
                        measure_episode_decay(me_sub, ep, r_lvl, side="signed")
                        for ep in episodes
                    ]
                    durations = [d["months_to_retire"] for d in decays if d["closed"]]
                    unclosed = sum(1 for d in decays if not d["closed"])
                    med = float(np.median(durations)) if durations else 0.0
                    min_d = min(durations) if durations else 0
                    max_d = max(durations) if durations else 0
                    role = "base" if r_lvl == cfg["retire_level"] else f"{r_lvl:+g} bp"

                    report_dict[shock_id][leg_name]["candidates"].append(
                        {
                            "retire_level": r_lvl,
                            "median_months": med,
                            "min_months": min_d,
                            "max_months": max_d,
                            "unclosed_count": unclosed,
                            "durations": durations,
                        }
                    )
                    md_lines.append(
                        f"| `rates_shock` | {leg_name.upper()} signed | ±{r_lvl:g} bp | {role} | **{med:.1f}** | {min_d}–{max_d} | {unclosed} |"
                    )
            continue

        me_series = data["month_end"]
        start_m = cfg["sample_start"]
        me_sub = me_series[me_series.index >= start_m]["transform"].dropna()

        if cfg["signed"]:
            s1_thresh = cfg["severity_1_up"]
            episodes = group_episodes(me_sub[me_sub.abs() >= s1_thresh].index)
            side_mode = "signed"
        else:
            s1_thresh = cfg["severity_1"]
            episodes = group_episodes(me_sub[me_sub >= s1_thresh].index)
            side_mode = "up"

        report_dict[shock_id] = {"episodes": len(episodes), "candidates": []}

        for r_lvl in cfg["candidate_retires"]:
            decays = [
                measure_episode_decay(me_sub, ep, r_lvl, side=side_mode)
                for ep in episodes
            ]
            durations = [d["months_to_retire"] for d in decays if d["closed"]]
            unclosed = sum(1 for d in decays if not d["closed"])
            med = float(np.median(durations)) if durations else 0.0
            min_d = min(durations) if durations else 0
            max_d = max(durations) if durations else 0
            role = "base" if r_lvl == cfg["retire_level"] else "alternative"

            report_dict[shock_id]["candidates"].append(
                {
                    "retire_level": r_lvl,
                    "median_months": med,
                    "min_months": min_d,
                    "max_months": max_d,
                    "unclosed_count": unclosed,
                    "durations": durations,
                }
            )
            unit = data["unit"]
            prefix = "±" if side_mode == "signed" else ""
            md_lines.append(
                f"| `{shock_id}` | {side_mode} | {prefix}{r_lvl:g} {unit} | {role} | **{med:.1f}** | {min_d}–{max_d} | {unclosed} |"
            )

    return report_dict, "\n".join(md_lines) + "\n"


def build_monthly_panel(
    transforms: dict[str, dict[str, Any]],
    thresholds_cfg: dict[str, Any],
    start_date: str = "1986-01-01",
    end_date: str | None = None,
) -> pd.DataFrame:
    """Build the monthly shock-state panel for the impact study."""
    # Month ends from start_date to end_date
    if end_date is None:
        # Determine latest date across all transforms
        latest_dates = [
            t["month_end"].index[-1].to_timestamp(how="end").date()
            for k, t in transforms.items()
            if "month_end" in t
        ]
        end_date = max(latest_dates).isoformat()

    month_periods = pd.period_range(
        pd.Period(start_date, "M"), pd.Period(end_date, "M"), freq="M"
    )

    rows: list[dict[str, Any]] = []

    for m in month_periods:
        m_str = str(m)
        m_end_date = str(m.to_timestamp(how="end").date())

        for shock_id, cfg in thresholds_cfg.items():
            val: float | None = None
            severity: int | None = None
            direction: str | None = None
            reason: str | None = None
            flag: str | None = None

            # Sample start check
            sample_start = cfg["sample_start"]
            if m < pd.Period(sample_start, "M"):
                if shock_id == "volatility_shock":
                    reason = "series_starts_1990_01"
                elif shock_id == "dollar_shock":
                    reason = "no_daily_broad_index_before_1995"
                elif shock_id == "inflation_shock":
                    reason = "no_daily_breakeven_before_2003"
                elif shock_id == "labour_shock":
                    reason = "sample_starts_1990_01"
                else:
                    reason = f"sample_starts_{sample_start.replace('-', '_')}"

                rows.append(
                    {
                        "month_end": m_end_date,
                        "year_month": m_str,
                        "shock_id": shock_id,
                        "value": None,
                        "severity": None,
                        "direction": None,
                        "reason": reason,
                        "flag": None,
                    }
                )
                continue

            if shock_id == "rates_shock":
                # Special combined logic for rates shock
                is_dfii10 = m >= pd.Period("2003-01", "M")
                me_dfii = transforms["rates_shock"]["month_end_dfii10"]
                me_dgs2 = transforms["rates_shock"]["month_end_dgs2"]

                v_dfii = float(me_dfii.loc[m, "transform"]) if m in me_dfii.index else None
                v_dgs2 = float(me_dgs2.loc[m, "transform"]) if m in me_dgs2.index else None

                if not is_dfii10:
                    val = v_dgs2
                    flag = "proxy_dgs2"
                    # DGS2 thresholds
                    if val is not None:
                        if val >= cfg["dgs2"]["severity_2_up"]:
                            severity, direction = 2, "up"
                        elif val >= cfg["dgs2"]["severity_1_up"]:
                            severity, direction = 1, "up"
                        elif val <= cfg["dgs2"]["severity_2_down"]:
                            severity, direction = 2, "down"
                        elif val <= cfg["dgs2"]["severity_1_down"]:
                            severity, direction = 1, "down"
                        else:
                            severity, direction = 0, "neutral"
                else:
                    val = v_dfii
                    # 2003+: primary DFII10, but DGS2 policy leg beside it
                    # Check DFII10
                    sev_dfii = 0
                    dir_dfii = "neutral"
                    if val is not None:
                        if val >= cfg["dfii10"]["severity_2_up"]:
                            sev_dfii, dir_dfii = 2, "up"
                        elif val >= cfg["dfii10"]["severity_1_up"]:
                            sev_dfii, dir_dfii = 1, "up"
                        elif val <= cfg["dfii10"]["severity_2_down"]:
                            sev_dfii, dir_dfii = 2, "down"
                        elif val <= cfg["dfii10"]["severity_1_down"]:
                            sev_dfii, dir_dfii = 1, "down"

                    # Check DGS2 policy leg
                    sev_dgs2 = 0
                    dir_dgs2 = "neutral"
                    if v_dgs2 is not None:
                        if v_dgs2 >= cfg["dgs2"]["severity_2_up"]:
                            sev_dgs2, dir_dgs2 = 2, "up"
                        elif v_dgs2 >= cfg["dgs2"]["severity_1_up"]:
                            sev_dgs2, dir_dgs2 = 1, "up"
                        elif v_dgs2 <= cfg["dgs2"]["severity_2_down"]:
                            sev_dgs2, dir_dgs2 = 2, "down"
                        elif v_dgs2 <= cfg["dgs2"]["severity_1_down"]:
                            sev_dgs2, dir_dgs2 = 1, "down"

                    if sev_dfii >= sev_dgs2 and sev_dfii > 0:
                        severity, direction = sev_dfii, dir_dfii
                        flag = "primary_dfii10"
                    elif sev_dgs2 > 0:
                        severity, direction = sev_dgs2, dir_dgs2
                        flag = "policy_leg_dgs2"
                    else:
                        severity, direction = 0, "neutral"
                        flag = "primary_dfii10"

            else:
                me_series = transforms[shock_id]["month_end"]
                if m in me_series.index:
                    raw_v = me_series.loc[m, "transform"]
                    val = float(raw_v) if pd.notna(raw_v) else None

                if val is not None:
                    if shock_id == "labour_shock":
                        # Flag values_revised if before 2009-06
                        if m < pd.Period("2009-06", "M"):
                            flag = "values_revised"
                        else:
                            flag = "pit_vintage"

                    if cfg["signed"]:
                        if val >= cfg["severity_2_up"]:
                            severity, direction = 2, "up"
                        elif val >= cfg["severity_1_up"]:
                            severity, direction = 1, "up"
                        elif val <= cfg["severity_2_down"]:
                            severity, direction = 2, "down"
                        elif val <= cfg["severity_1_down"]:
                            severity, direction = 1, "down"
                        else:
                            severity, direction = 0, "neutral"
                    else:
                        if val >= cfg["severity_2"]:
                            severity, direction = 2, "up"
                        elif val >= cfg["severity_1"]:
                            severity, direction = 1, "up"
                        else:
                            severity, direction = 0, "neutral"

            rows.append(
                {
                    "month_end": m_end_date,
                    "year_month": m_str,
                    "shock_id": shock_id,
                    "value": round(val, 4) if val is not None else None,
                    "severity": severity,
                    "direction": direction,
                    "reason": reason,
                    "flag": flag,
                }
            )

    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# Main CLI Driver
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default="data/macro_engine.duckdb",
        help="path to DuckDB store copy",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="output directory for artifacts",
    )
    parser.add_argument(
        "--config",
        default="config/shocks.yaml",
        help="path to shocks.yaml configuration file",
    )
    parser.add_argument(
        "--thresholds-file",
        default=None,
        help="optional custom JSON file with provisional thresholds",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load thresholds from custom file, shocks.yaml, or defaults
    if args.thresholds_file and Path(args.thresholds_file).exists():
        with open(args.thresholds_file, "r", encoding="utf-8") as f:
            thresholds_cfg = json.load(f)
    elif args.config and Path(args.config).exists():
        from macro_engine.shocks.config import load_shocks_config, to_measurement_thresholds

        yaml_cfg = load_shocks_config(args.config)
        thresholds_cfg = to_measurement_thresholds(yaml_cfg)
        with open(out_dir / "shocks_config_snapshot.json", "w", encoding="utf-8") as f:
            json.dump(thresholds_cfg, f, indent=2)
    else:
        thresholds_cfg = DEFAULT_PROVISIONAL_THRESHOLDS
        # Write default provisional_thresholds.json to out_dir
        with open(out_dir / "provisional_thresholds.json", "w", encoding="utf-8") as f:
            json.dump(thresholds_cfg, f, indent=2)

    print(f"Connecting to DuckDB at {args.db_path} (read-only)...")
    con = duckdb.connect(args.db_path, read_only=True)

    print("Computing daily transforms and month-end series...")
    transforms = build_daily_and_monthly_transforms(con)

    print("Building threshold analysis...")
    thresholds_dict, thresholds_md = build_threshold_analysis(transforms, thresholds_cfg)
    with open(out_dir / "thresholds.json", "w", encoding="utf-8") as f:
        json.dump(thresholds_dict, f, indent=2)
    with open(out_dir / "thresholds.md", "w", encoding="utf-8") as f:
        f.write(thresholds_md)

    print("Building decay analysis...")
    decay_dict, decay_md = build_decay_analysis(transforms, thresholds_cfg)
    with open(out_dir / "decay.json", "w", encoding="utf-8") as f:
        json.dump(decay_dict, f, indent=2)
    with open(out_dir / "decay.md", "w", encoding="utf-8") as f:
        f.write(decay_md)

    print("Building monthly shock-state panel for the impact study...")
    panel_df = build_monthly_panel(transforms, thresholds_cfg, start_date="1986-01-01")
    panel_df.to_parquet(out_dir / "panel.parquet", index=False)

    print("\nMeasurement complete.")
    print(f"  Artifacts written to: {out_dir}")
    print(f"  Panel rows: {len(panel_df)}")
    print(f"  Panel date range: {panel_df['month_end'].min()} .. {panel_df['month_end'].max()}")
    print(f"  Thresholds markdown: {len(thresholds_md.splitlines())} lines")
    print(f"  Decay markdown: {len(decay_md.splitlines())} lines")
    return 0


if __name__ == "__main__":

    raise SystemExit(main())
