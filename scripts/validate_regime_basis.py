#!/usr/bin/env python3
"""
MRI v0.2 — what does the point-in-time hybrid change in the REGIME layer?

`scripts/validate_pit_vs_calendar.py` measures the anchors. This measures the other half: the
as-of feature layer and the regime timeline the historical diagnostic is built from. The anchor
question is "what discount rate would we have published"; this one is "what would we have said
the regime was".

Method — two arms over the SAME stored data, differing only in as-of basis:

  1. copy the live database to a scratch file (the arms write to separate stores, so neither can
     contaminate the other or the live outputs);
  2. run build-asof-features -> build-dimensions -> build-regimes on the copy with
     `scoring_mode: calendar_asof`;
  3. run the same chain on the live database with the shipped configuration (the hybrid);
  4. compare the two timelines date by date.

Nothing here writes to `outputs/` or to the live database.

Usage:
    python scripts/validate_regime_basis.py
    python scripts/validate_regime_basis.py --db-path data/macro_engine.duckdb --json data/logs/regime_basis.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from macro_engine.evaluation.config import load_evaluation_config  # noqa: E402
from macro_engine.storage.duckdb_store import DuckDBStore  # noqa: E402

DEFAULT_CONFIG = "config/phase_b_sources.yaml"
DEFAULT_DB = "data/macro_engine.duckdb"
CHAIN = ("build-asof-features", "build-dimensions", "build-regimes")


def _calendar_config(config_path: Path, target: Path) -> Path:
    """A copy of the config forced to the calendar basis, for the reference arm."""
    text = config_path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^scoring_mode:.*$", "scoring_mode: calendar_asof", text)
    if "scoring_mode: calendar_asof" not in text:
        raise SystemExit(f"could not force calendar_asof in {config_path}")
    target.write_text(text, encoding="utf-8")
    return target


def _run_chain(config: str, db_path: Path, label: str) -> None:
    for command in CHAIN:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "macro_engine.cli",
                command,
                "--config",
                config,
                "--db-path",
                str(db_path),
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"{label}: '{command}' failed ({result.returncode})\n{result.stderr[-2000:]}"
            )
        print(f"  {label}: {command} ok", flush=True)


def _timeline(db_path: Path) -> pd.DataFrame:
    store = DuckDBStore(db_path)
    store.initialize()
    frame = store.read_table("historical_regime_timeline")
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    return frame.sort_values("date").reset_index(drop=True)


def _asof_features(db_path: Path) -> pd.DataFrame:
    store = DuckDBStore(db_path)
    store.initialize()
    frame = store.read_table("asof_feature_values")
    if frame.empty:
        return frame
    frame["evaluation_date"] = pd.to_datetime(frame["evaluation_date"], errors="coerce")
    return frame


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--db-path", default=DEFAULT_DB)
    parser.add_argument("--json", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = _REPO_ROOT / args.config
    live_db = _REPO_ROOT / args.db_path
    evaluation = load_evaluation_config(config_path)
    print(
        f"shipped basis: {evaluation.scoring_mode} "
        f"(point_in_time_start={evaluation.point_in_time_start})"
    )

    with tempfile.TemporaryDirectory(prefix="mri-regime-basis-") as tmp:
        tmp_path = Path(tmp)
        calendar_db = tmp_path / "calendar_arm.duckdb"
        print(f"copying {live_db.name} -> {calendar_db.name} ...", flush=True)
        shutil.copy2(live_db, calendar_db)
        calendar_config = _calendar_config(config_path, tmp_path / "calendar_sources.yaml")

        print("running the CALENDAR arm on the copy:")
        _run_chain(str(calendar_config), calendar_db, "calendar")
        print("running the SHIPPED (hybrid) arm on the live store:")
        _run_chain(str(config_path), live_db, "hybrid")

        calendar_timeline = _timeline(calendar_db)
        hybrid_timeline = _timeline(live_db)
        calendar_asof = _asof_features(calendar_db)
        hybrid_asof = _asof_features(live_db)

    if calendar_timeline.empty or hybrid_timeline.empty:
        print("one arm produced no timeline; nothing to compare")
        return 1

    merged = calendar_timeline.merge(
        hybrid_timeline, on="date", suffixes=("_calendar", "_hybrid"), how="outer"
    )
    label_columns = [
        column
        for column in calendar_timeline.columns
        if column != "date" and column in hybrid_timeline.columns
    ]
    report: dict[str, Any] = {
        "configured_mode": evaluation.scoring_mode,
        "point_in_time_start": evaluation.point_in_time_start,
        "dates_calendar": int(len(calendar_timeline)),
        "dates_hybrid": int(len(hybrid_timeline)),
        "overlap_dates": int(merged["date"].notna().sum()),
        "legs": {},
    }

    for column in label_columns:
        left, right = f"{column}_calendar", f"{column}_hybrid"
        if left not in merged.columns or right not in merged.columns:
            continue
        differs = merged[left].astype(str) != merged[right].astype(str)
        report["legs"][column] = {
            "differing_dates": int(differs.sum()),
            "first_difference": (
                merged.loc[differs, "date"].min().date().isoformat() if differs.any() else None
            ),
            "last_difference": (
                merged.loc[differs, "date"].max().date().isoformat() if differs.any() else None
            ),
        }

    feature_report: dict[str, Any] = {}
    if not calendar_asof.empty and not hybrid_asof.empty:
        merged_features = calendar_asof.merge(
            hybrid_asof,
            on=["evaluation_date", "feature_id"],
            suffixes=("_calendar", "_hybrid"),
            how="outer",
        )
        valid_calendar = merged_features["valid_calendar"].fillna(False).astype(bool)
        valid_hybrid = merged_features["valid_hybrid"].fillna(False).astype(bool)
        value_differs = (
            merged_features["normalized_value_calendar"].round(10)
            != merged_features["normalized_value_hybrid"].round(10)
        )
        feature_report = {
            "cells": int(len(merged_features)),
            "validity_flips": int((valid_calendar != valid_hybrid).sum()),
            "valid_calendar_only": int((valid_calendar & ~valid_hybrid).sum()),
            "valid_hybrid_only": int((~valid_calendar & valid_hybrid).sum()),
            "values_differing": int((value_differs & valid_calendar & valid_hybrid).sum()),
            "max_abs_value_delta": (
                float(
                    (
                        merged_features["normalized_value_hybrid"]
                        - merged_features["normalized_value_calendar"]
                    )
                    .abs()
                    .max()
                )
                if len(merged_features)
                else None
            ),
            "first_validity_flip": (
                merged_features.loc[
                    valid_calendar != valid_hybrid, "evaluation_date"
                ].min().date().isoformat()
                if (valid_calendar != valid_hybrid).any()
                else None
            ),
        }
        report["asof_features"] = feature_report

    print("\n=== regime timeline ===")
    for column, detail in report["legs"].items():
        print(
            f"  {column:24s} differing dates: {detail['differing_dates']:4d}"
            f"  first={detail['first_difference']}  last={detail['last_difference']}"
        )
    if feature_report:
        print("\n=== as-of feature cells ===")
        for key, value in feature_report.items():
            print(f"  {key:26s} {value}")

    if args.json:
        out = _REPO_ROOT / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
