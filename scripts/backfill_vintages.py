#!/usr/bin/env python3
"""
MRI v0.2 — ALFRED vintage backfill (point-in-time integrity)

Fetches the ALFRED vintage of every anchor/regime series for exactly the as-of dates
the diagnostic calendar actually asks about, so an evaluation date can be scored on
what was PUBLISHED then rather than on what has since been revised.

Why only the calendar dates: request volume multiplies by the number of vintages, so
pulling a series' whole vintage history would be thousands of requests per series for
dates nothing consumes. The evaluation calendar is the authoritative list of dates the
diagnostics use, and it is already materialised in DuckDB.

Usage:
    # Dry-run (default): show the plan, fetch nothing, write nothing
    python scripts/backfill_vintages.py

    # Restrict to specific series
    python scripts/backfill_vintages.py --series CPIAUCSL --series PAYEMS

    # Actually fetch and store
    python scripts/backfill_vintages.py --apply

    # Bound the window (defaults to the whole stored evaluation calendar)
    python scripts/backfill_vintages.py --apply --start 2015-01-01

Idempotent: writes go to `raw_observation_vintages`, keyed on the full vintage key
(series_id, date, realtime_start, realtime_end), so re-running a backfill replaces the
same rows instead of accumulating duplicates.

Exit code: 0 on success, 1 on any failure (suitable for CI detection).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import yaml

# Ensure the repo root is on sys.path so we can import macro_engine
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from macro_engine.anchors.pit_calendar import vintage_asof_dates  # noqa: E402
from macro_engine.ingest.fred import FredError  # noqa: E402
from macro_engine.ingest.service import run_fred_vintage_ingestion  # noqa: E402

DEFAULT_CONFIG = "config/phase_b_sources.yaml"
DEFAULT_DB_PATH = "data/macro_engine.duckdb"
DEFAULT_PARQUET_DIR = "data/raw/alfred"

# The anchor inputs. Fetching ONLY these is not enough to run `scoring_mode: point_in_time`:
# that mode gates the FEATURE series (INDPRO, PAYEMS, UNRATE, CPIAUCSL, ...), which is a
# different set. So the default is every enabled source in the config, and `--anchor-only`
# narrows it to this list when only the anchors need point-in-time evidence.
ANCHOR_SERIES = [
    "DGS10",
    "DFII10",
    "T10YIE",
    "T5YIFR",
    "DTB3",
    "GDPPOT",
    "THREEFYTP10",
]

calendar_asof_dates = vintage_asof_dates  # single definition lives in the package


def enabled_series(config_path: str | Path) -> list[str]:
    """Every enabled series in the ingestion config — the set PIT mode must gate."""
    with Path(config_path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return [
        str(item["series_id"])
        for item in data.get("sources", [])
        if item.get("enabled", True) and item.get("series_id")
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="fetch and store vintages (default: plan only)")
    parser.add_argument("--series", action="append", default=None, help="series id (repeatable)")
    parser.add_argument(
        "--anchor-only",
        action="store_true",
        help="fetch only the anchor inputs instead of every enabled source",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="re-fetch every (series, as-of) pair instead of skipping stored ones",
    )
    parser.add_argument("--start", default=None, help="earliest as-of date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="latest as-of date (YYYY-MM-DD)")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--parquet-dir", default=DEFAULT_PARQUET_DIR)
    parser.add_argument(
        "--observation-start",
        default=None,
        help="bound the observation window carried in each vintage (default: FRED decides)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()

    as_of_dates = calendar_asof_dates(db_path=args.db_path, start=args.start, end=args.end)
    series = args.series or (ANCHOR_SERIES if args.anchor_only else enabled_series(args.config))

    print("ALFRED vintage backfill")
    print(f"  series      : {', '.join(series)}")
    print(f"  as-of dates : {len(as_of_dates)}")
    if as_of_dates:
        print(f"                {as_of_dates[0]} .. {as_of_dates[-1]}")
    print(f"  requests    : {len(series) * len(as_of_dates)} (one vintage fetch per series x date)")

    if not as_of_dates:
        print("\nNo as-of dates found. Materialise the evaluation calendar first, or pass --start/--end.")
        return 1

    if not args.apply:
        print("\nDry-run. Re-run with --apply to fetch and store.")
        return 0

    if not os.getenv("FRED_API_KEY"):
        print("\nFRED_API_KEY is not set; cannot fetch vintages.", file=sys.stderr)
        return 1

    # Bound each vintage's OBSERVATION window. Without this, a vintage fetch returns the whole
    # history as it stood on that date — ~16,000 rows per request for a daily series — and the
    # backfill is dominated by transferring decades of data it will not read. One year of lead
    # in keeps year-over-year transforms computable at the earliest as-of date.
    observation_start = args.observation_start or (
        (pd.Timestamp(as_of_dates[0]) - pd.DateOffset(years=1)).date().isoformat()
    )
    print(f"  observation window : {observation_start} .. (each vintage's own date)")

    try:
        summary = run_fred_vintage_ingestion(
            as_of_dates=as_of_dates,
            config_path=args.config,
            requested_series=series,
            observation_start=observation_start,
            db_path=args.db_path,
            parquet_dir=args.parquet_dir,
            resume=not args.no_resume,
            progress=True,
        )
    except FredError as exc:
        print(f"\nVintage backfill failed: {exc}", file=sys.stderr)
        return 1

    print("\nStored (counts aggregated; per-vintage logging intentionally suppressed):")
    print(f"  vintage rows          : {summary.vintage_rows}")
    print(f"  series stored this run: {summary.vintage_series} {summary.series_stored}")
    print(f"  pairs skipped (already stored)    : {summary.skipped_pairs}")
    print(f"  series x date with no vintage yet : {summary.empty_vintage_count}")
    print(f"  failed fetches        : {summary.failed_count}")
    print(f"  parquet               : {summary.storage_path}")
    if summary.skipped_pairs and not summary.vintage_rows:
        print("  (nothing new to fetch; re-run without --no-resume is a no-op)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
