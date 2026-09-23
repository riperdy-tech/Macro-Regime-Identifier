#!/usr/bin/env python3
"""
S2.1 -- measure the terminal-growth rung path under specifications A, B, C, and E.

Reads the DuckDB store in read_only mode (genuinely read-only, no initialize() call)
and replays advance_rung_state month by month from --start to the latest available month,
computing raw_trend_g on a month-start grid (as-of = first day of the build month,
with the inflation leg evaluated over the 12 complete calendar months before the
evaluation month, excluding the partial month).

Specification A = spot legs, 25 bp rounding
Specification B = smoothed leg (trailing 12m mean), 25 bp rounding
Specification C = confirm_months=1 (dead-band only, no wait)
Specification E = confirm_months=3, candidate rule (dead-band + 3-month candidate confirmation;
                  operator ruling P0_0_MRI_TARGET_ARCHITECTURE.md §10 Q2)

Usage:
    python scripts/measure_growth_rung_path.py
    python scripts/measure_growth_rung_path.py --start 2004-06-01 --db-path data/macro_engine.duckdb
    python scripts/measure_growth_rung_path.py --assert-architecture-path
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
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

ARCHITECTURE_E_PATH = [
    ("2007-02-01", 0.0475),
    ("2007-12-01", 0.0450),
    ("2009-06-01", 0.0375),
    ("2015-04-01", 0.0350),
    ("2015-11-01", 0.0325),
    ("2016-07-01", 0.0300),
    ("2018-09-01", 0.0325),
    ("2022-02-01", 0.0350),
    ("2024-06-01", 0.0375),
]


def month_starts(start: str, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(start, end, freq="MS"))


def raw_trend_g_series(
    observations: pd.DataFrame,
    config,
    dates: list[pd.Timestamp],
) -> list[dict]:
    growth = config.growth
    rows = []
    real_frame = _series_slice(observations, growth.real_potential.series)
    for as_of in dates:
        real_potential, _, _ = log_linear_trend_annualized(
            real_frame["value"] if not real_frame.empty else pd.Series(dtype="float64"),
            real_frame["date"] if not real_frame.empty else pd.Series(dtype="datetime64[ns]"),
            window_years=growth.real_potential.trend_window_years,
            min_observations=growth.real_potential.min_observations,
            as_of=as_of,
        )
        inflation = None
        inflation_spot = None
        series_used = None
        # Complete months only: the 12 complete calendar months before month as_of
        last_complete_month_end = as_of - pd.Timedelta(days=1)
        for ref in growth.inflation_expectation.candidates:
            frame = _series_slice(observations, ref.series)
            smoothed, _, _ = trailing_12m_mean_of_monthly_mean(
                frame["value"] if not frame.empty else pd.Series(dtype="float64"),
                frame["date"] if not frame.empty else pd.Series(dtype="datetime64[ns]"),
                as_of=last_complete_month_end,
                min_months=growth.inflation_expectation.min_observations,
            )
            if smoothed is None:
                continue
            inflation = ref.to_decimal(smoothed)
            series_used = ref.series
            spot_slice = frame[frame["date"] <= as_of]
            if not spot_slice.empty:
                inflation_spot = ref.to_decimal(float(spot_slice.iloc[-1]["value"]))
            break

        clamp = growth.terminal_g
        nominal_trend = None if real_potential is None or inflation is None else real_potential + inflation
        raw = None if nominal_trend is None else nominal_trend * clamp.max_share_of_nominal_trend
        clamped = None if raw is None else min(max(raw, clamp.floor), clamp.ceiling)

        spot_nominal = None if real_potential is None or inflation_spot is None else real_potential + inflation_spot
        spot_raw = None if spot_nominal is None else spot_nominal * clamp.max_share_of_nominal_trend
        spot_clamped = None if spot_raw is None else min(max(spot_raw, clamp.floor), clamp.ceiling)

        rows.append(
            {
                "month": as_of.date().isoformat(),
                "real_potential": real_potential,
                "inflation_expectation_12m": inflation,
                "inflation_expectation_spot": inflation_spot,
                "inflation_series": series_used,
                "nominal_trend": nominal_trend,
                "raw_trend_g": raw,
                "raw_trend_g_clamped": clamped,
                "spot_clamped": spot_clamped,
            }
        )
    return rows


def walk_rung_path(
    rows: list[dict],
    *,
    round_to: float,
    confirm_months: int,
    rule: str = "candidate",
) -> list[dict]:
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
            rule=rule,
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


def summarize_unbanded(rows: list[dict], key: str, round_to: float) -> dict:
    valid_rows = [r for r in rows if r[key] is not None]
    if not valid_rows:
        return {"n_changes": 0, "final_rung": None, "largest_single_change_bp": None}
    rungs = [round(r[key] / round_to) * round_to for r in valid_rows]
    changes = [
        (valid_rows[i]["month"], rungs[i])
        for i in range(1, len(rungs))
        if rungs[i] != rungs[i - 1]
    ]
    max_jump = (
        round(max(abs(rungs[i] - rungs[i - 1]) for i in range(1, len(rungs))) * 10000)
        if len(rungs) > 1
        else 0
    )
    return {
        "n_months": len(valid_rows),
        "n_changes": len(changes),
        "change_dates_and_rungs": changes,
        "final_rung": rungs[-1],
        "largest_single_change_bp": max_jump,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="data/macro_engine.duckdb")
    parser.add_argument("--config-path", default="config/anchors.yaml")
    parser.add_argument("--start", default="2004-06-01")
    parser.add_argument("--out", default="outputs/growth_rung_path_S2.json")
    parser.add_argument(
        "--assert-architecture-path",
        action="store_true",
        help="Exit 1 unless Specification E reproduces the 9-date architecture path exactly.",
    )
    args = parser.parse_args()

    # Genuinely read-only: connect with read_only=True, no initialize()
    conn = duckdb.connect(args.db_path, read_only=True)
    observations = conn.execute("SELECT * FROM raw_observations").fetchdf()
    conn.close()

    config = load_anchor_config(args.config_path)

    today = normalize_asof(None)
    max_date = min(pd.to_datetime(observations["date"], errors="coerce").max(), today)
    dates = month_starts(args.start, max_date)
    rows = raw_trend_g_series(observations, config, dates)

    round_to = config.growth.terminal_g.round_to

    summary_a = summarize_unbanded(rows, "spot_clamped", round_to)
    summary_b = summarize_unbanded(rows, "raw_trend_g_clamped", round_to)

    path_c = walk_rung_path(rows, round_to=round_to, confirm_months=1, rule="candidate")
    path_e = walk_rung_path(rows, round_to=round_to, confirm_months=3, rule="candidate")

    summary_c = summarize(path_c)
    summary_e = summarize(path_e)

    summary = {
        "start": args.start,
        "end": dates[-1].date().isoformat() if dates else None,
        "n_months": len(dates),
        "specification_A_spot": summary_a,
        "specification_B_smoothed_round_nearest": summary_b,
        "specification_C_confirm_1": summary_c,
        "specification_E_confirm_3": summary_e,
    }

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"summary": summary, "path_C": path_c, "path_E": path_e}, indent=2, default=str),
            encoding="utf-8",
        )

    print(f"months measured: {summary['n_months']} ({summary['start']} .. {summary['end']})")
    print(f"specification A (spot, unbanded): {summary_a['n_changes']} changes, "
          f"largest single change {summary_a['largest_single_change_bp']} bp, "
          f"final rung {summary_a['final_rung']}")
    print(f"specification B (smoothed, unbanded): {summary_b['n_changes']} changes, "
          f"largest single change {summary_b['largest_single_change_bp']} bp, "
          f"final rung {summary_b['final_rung']}")
    print(f"specification C (confirm_months=1): {summary_c['n_changes']} changes, "
          f"largest single change {summary_c['largest_single_change_bp']} bp, "
          f"final rung {summary_c['final_rung']}")
    for month, rung in summary_c["change_dates_and_rungs"]:
        print(f"    C  {month}  -> {rung}")
    print(f"specification E (confirm_months=3, candidate): {summary_e['n_changes']} changes, "
          f"largest single change {summary_e['largest_single_change_bp']} bp, "
          f"final rung {summary_e['final_rung']}")
    for month, rung in summary_e["change_dates_and_rungs"]:
        print(f"    E  {month}  -> {rung}")

    if args.assert_architecture_path:
        actual_e = summary_e["change_dates_and_rungs"]
        if actual_e != ARCHITECTURE_E_PATH or summary_e["final_rung"] != 0.0375:
            print(f"ASSERTION FAILED: expected {ARCHITECTURE_E_PATH}, got {actual_e}", file=sys.stderr)
            sys.exit(1)
        print("ASSERTION PASSED: Specification E matches architecture's 9-change path exactly.")


if __name__ == "__main__":
    main()
