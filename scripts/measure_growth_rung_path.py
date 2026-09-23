#!/usr/bin/env python3
"""
S2.1 -- measure the terminal-growth rung path under specifications C and E.

Reads the DuckDB store (read-only) and replays `advance_rung_state` month by month
from `--start` to the latest available month, computing `raw_trend_g` exactly as
`build_long_run_growth_anchor` does (10-year log-linear GDPPOT trend + trailing
12-month mean of the monthly mean of T5YIFR, fallback T10YIE, x0.85, clamped to
[floor, ceiling]) and feeding it through the same dead-band + confirmation state
machine the live anchor uses.

specification C = confirm_months=1 (dead-band only, no wait)
specification E = confirm_months=3 (dead-band + 3-consecutive-monthly-build wait;
                  the operator's choice, P0_0_MRI_TARGET_ARCHITECTURE.md §10 Q2)

This is the S2.1 gate: "the rung path reproduces the architecture's measured history
(9 changes in 22 years under the recommended smoothing) ... or the difference is
explained". It writes nothing to the store; `outputs/growth_rung_path_S2.json` is a
product, not source, same as the rest of `outputs/`.

Usage:
    python scripts/measure_growth_rung_path.py
    python scripts/measure_growth_rung_path.py --start 2004-06-30 --db-path data/macro_engine.duckdb
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from macro_engine.anchors.config import load_anchor_config  # noqa: E402
from macro_engine.anchors.cost_of_capital import _series_slice  # noqa: E402
from macro_engine.anchors.growth import (  # noqa: E402
    advance_rung_state,
    log_linear_trend_annualized,
    trailing_12m_mean_of_monthly_mean,
)
from macro_engine.evaluation.asof import normalize_asof  # noqa: E402
from macro_engine.storage.duckdb_store import DuckDBStore  # noqa: E402


def month_ends(start: str, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(start, end, freq="ME"))


def raw_trend_g_series(
    observations: pd.DataFrame,
    config,
    dates: list[pd.Timestamp],
) -> list[dict]:
    growth = config.growth
    rows = []
    for as_of in dates:
        real_frame = _series_slice(observations, growth.real_potential.series)
        real_potential, _, _ = log_linear_trend_annualized(
            real_frame["value"] if not real_frame.empty else pd.Series(dtype="float64"),
            real_frame["date"] if not real_frame.empty else pd.Series(dtype="datetime64[ns]"),
            window_years=growth.real_potential.trend_window_years,
            min_observations=growth.real_potential.min_observations,
            as_of=as_of,
        )
        inflation = None
        series_used = None
        for ref in growth.inflation_expectation.candidates:
            frame = _series_slice(observations, ref.series)
            smoothed, _, n_months = trailing_12m_mean_of_monthly_mean(
                frame["value"] if not frame.empty else pd.Series(dtype="float64"),
                frame["date"] if not frame.empty else pd.Series(dtype="datetime64[ns]"),
                as_of=as_of,
                min_months=growth.inflation_expectation.min_observations,
            )
            if smoothed is None:
                continue
            inflation = ref.to_decimal(smoothed)
            series_used = ref.series
            break
        nominal_trend = None if real_potential is None or inflation is None else real_potential + inflation
        clamp = growth.terminal_g
        raw = None if nominal_trend is None else nominal_trend * clamp.max_share_of_nominal_trend
        clamped = None if raw is None else min(max(raw, clamp.floor), clamp.ceiling)
        rows.append(
            {
                "month": as_of.date().isoformat(),
                "real_potential": real_potential,
                "inflation_expectation_12m": inflation,
                "inflation_series": series_used,
                "nominal_trend": nominal_trend,
                "raw_trend_g": raw,
                "raw_trend_g_clamped": clamped,
            }
        )
    return rows


def walk_rung_path(rows: list[dict], *, round_to: float, confirm_months: int) -> list[dict]:
    state = None
    path = []
    for row in rows:
        clamped = row["raw_trend_g_clamped"]
        if clamped is None:
            path.append({**row, "current_rung": None, "changed": False})
            continue
        as_of = pd.Timestamp(row["month"])
        prior_rung = None if state is None else state.get("current_rung")
        new_state = advance_rung_state(
            raw_trend_g_clamped=clamped,
            as_of=as_of,
            prior_state=state,
            round_to=round_to,
            confirm_months=confirm_months,
        )
        changed = prior_rung is not None and new_state["current_rung"] != prior_rung
        state = new_state
        path.append({**row, "current_rung": state["current_rung"], "changed": changed})
    return path


def summarize(path: list[dict]) -> dict:
    changes = [row for row in path if row["changed"]]
    return {
        "n_months": len(path),
        "n_changes": len(changes),
        "change_dates_and_rungs": [(row["month"], row["current_rung"]) for row in changes],
        "final_rung": path[-1]["current_rung"] if path else None,
        "largest_single_change_bp": (
            round(
                max(
                    abs(path[i]["current_rung"] - path[i - 1]["current_rung"])
                    for i in range(1, len(path))
                    if path[i]["current_rung"] is not None and path[i - 1]["current_rung"] is not None
                )
                * 10000
            )
            if len(path) > 1
            else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="data/macro_engine.duckdb")
    parser.add_argument("--config-path", default="config/anchors.yaml")
    parser.add_argument("--start", default="2004-06-30")
    parser.add_argument("--out", default="outputs/growth_rung_path_S2.json")
    args = parser.parse_args()

    store = DuckDBStore(args.db_path)
    store.initialize()
    observations = store.read_raw_observations()
    config = load_anchor_config(args.config_path)

    # Cap at today, the same discipline anchors/service.py._resolve_as_of uses: GDPPOT
    # (CBO Real Potential GDP) carries projection rows roughly a decade past the
    # present, and a month-end past today would fit a "trend" against its own future.
    today = normalize_asof(None)
    max_date = min(pd.to_datetime(observations["date"], errors="coerce").max(), today)
    dates = month_ends(args.start, max_date)
    rows = raw_trend_g_series(observations, config, dates)

    path_c = walk_rung_path(rows, round_to=config.growth.terminal_g.round_to, confirm_months=1)
    path_e = walk_rung_path(rows, round_to=config.growth.terminal_g.round_to, confirm_months=3)

    summary = {
        "start": args.start,
        "end": dates[-1].date().isoformat() if dates else None,
        "n_months": len(dates),
        "specification_C_confirm_1": summarize(path_c),
        "specification_E_confirm_3": summarize(path_e),
        "today_raw_trend_g_unsmoothed_spot": None,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"summary": summary, "path_C": path_c, "path_E": path_e}, indent=2, default=str),
        encoding="utf-8",
    )

    print(f"months measured: {summary['n_months']} ({summary['start']} .. {summary['end']})")
    print(f"specification C (confirm_months=1): {summary['specification_C_confirm_1']['n_changes']} changes, "
          f"largest single change {summary['specification_C_confirm_1']['largest_single_change_bp']} bp, "
          f"final rung {summary['specification_C_confirm_1']['final_rung']}")
    for month, rung in summary["specification_C_confirm_1"]["change_dates_and_rungs"]:
        print(f"    C  {month}  -> {rung}")
    print(f"specification E (confirm_months=3): {summary['specification_E_confirm_3']['n_changes']} changes, "
          f"largest single change {summary['specification_E_confirm_3']['largest_single_change_bp']} bp, "
          f"final rung {summary['specification_E_confirm_3']['final_rung']}")
    for month, rung in summary["specification_E_confirm_3"]["change_dates_and_rungs"]:
        print(f"    E  {month}  -> {rung}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
