#!/usr/bin/env python3
"""
WS2-T1 — FRED 1980 Backfill Runner

Extends FRED ingestion back to 1980-01-01 for all 12 enabled series.

Usage:
    # Dry-run (default): print what would be fetched, fetch nothing, write nothing
    python scripts/backfill_fred_1980.py

    # Dry-run a single series
    python scripts/backfill_fred_1980.py --series UNRATE

    # Actually perform the backfill
    python scripts/backfill_fred_1980.py --apply

    # Backfill a single series
    python scripts/backfill_fred_1980.py --apply --series UNRATE

Idempotent: DuckDBStore.upsert_raw_observations() uses DELETE+INSERT on the
composite primary key (series_id, date, realtime_start, realtime_end).
Running twice produces the same final state.

Exit code: 0 on success, non-zero on any error (suitable for CI detection).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure the repo root is on sys.path so we can import macro_engine
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from macro_engine.ingest.fred import FredError  # noqa: E402
from macro_engine.ingest.service import run_fred_ingestion  # noqa: E402
from macro_engine.storage.duckdb_store import DuckDBStore  # noqa: E402

# ── Constants ──────────────────────────────────────────────────────────────
BACKFILL_START = "1980-01-01"
DEFAULT_CONFIG = "config/phase_b_sources.yaml"
DEFAULT_DB_PATH = "data/macro_engine.duckdb"
DEFAULT_PARQUET_DIR = "data/raw/fred"

# Series that are enabled in the config (USSLIND is disabled, skip it)
ENABLED_SERIES = [
    "INDPRO",
    "PAYEMS",
    "UNRATE",
    "CPIAUCSL",
    "PCEPI",
    "FEDFUNDS",
    "DGS10",
    "BAA10Y",
    "NFCI",
    "T10Y2Y",
    "ICSA",
    "BAMLH0A0HYM2",
]

# Estimated rows per series for the 1980-01 to 1990-01 window (informational)
ESTIMATED_ROWS: dict[str, str] = {
    "INDPRO": "~120 (monthly)",
    "PAYEMS": "~120 (monthly)",
    "UNRATE": "~120 (monthly)",
    "CPIAUCSL": "~120 (monthly)",
    "PCEPI": "~120 (monthly)",
    "FEDFUNDS": "~120 (monthly)",
    "DGS10": "~2,530 (daily)",
    "BAA10Y": "~2,530 (daily)",
    "NFCI": "~520 (weekly)",
    "T10Y2Y": "~2,530 (daily)",
    "ICSA": "~520 (weekly)",
    "BAMLH0A0HYM2": "~2,530 (daily)",
}


# ── Helpers ────────────────────────────────────────────────────────────────


def _dry_run_report(series_list: list[str]) -> None:
    """Print what the script would do without making any network calls or DB writes."""
    print("=" * 60)
    print("WS2-T1 FRED 1980 Backfill - DRY RUN")
    print("=" * 60)
    print(f"Config:          {DEFAULT_CONFIG}")
    print(f"Start date:      {BACKFILL_START}")
    print("End date:        None (up to latest available)")
    print(f"DB path:         {DEFAULT_DB_PATH}")
    print(f"Parquet dir:     {DEFAULT_PARQUET_DIR}")
    print(f"Series to fetch: {len(series_list)}")
    print()
    print(f"{'Series ID':<12} {'Est. rows (1980-1990)':<25}")
    print("-" * 37)
    total_est = 0
    for sid in series_list:
        est = ESTIMATED_ROWS.get(sid, "unknown")
        print(f"{sid:<12} {est:<25}")
        # Rough numeric estimate for total
        if "monthly" in est:
            total_est += 120
        elif "weekly" in est:
            total_est += 520
        elif "daily" in est:
            total_est += 2530
    print("-" * 37)
    print(f"{'TOTAL':<12} ~{total_est:,} rows")
    print()
    print("DRY RUN - no network calls, no DB writes.")
    print("Re-run with --apply to actually fetch and store data.")
    print("=" * 60)


def _verify_db_exists(db_path: str) -> None:
    """Check that the DuckDB file exists and has the raw_observations table."""
    path = Path(db_path)
    if not path.exists():
        print(f"ERROR: DuckDB not found at {db_path}", file=sys.stderr)
        print("Run the pipeline first to initialize the database.", file=sys.stderr)
        sys.exit(1)
    try:
        store = DuckDBStore(db_path)
        store.initialize()  # ensures tables exist
    except Exception as exc:
        print(f"ERROR: Cannot open DuckDB at {db_path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _verify_api_key() -> str:
    """Check that FRED_API_KEY is available."""
    load_dotenv()
    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        print(
            "ERROR: FRED_API_KEY not set. Set it in .env or as an environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key


def _run_backfill(
    series_list: list[str],
    db_path: str,
    parquet_dir: str,
    config_path: str,
) -> None:
    """Execute the backfill by calling run_fred_ingestion.

    Delegates to the existing run_fred_ingestion service which handles
    fetching, upserting, and health updates in one pass.
    """
    print(f"Backfilling {len(series_list)} series from {BACKFILL_START}...")
    print(f"DB: {db_path}")
    print(f"Parquet: {parquet_dir}")
    print()

    try:
        summary = run_fred_ingestion(
            config_path=config_path,
            requested_series=series_list,
            start=BACKFILL_START,
            end=None,
            db_path=db_path,
            parquet_dir=parquet_dir,
        )
    except FredError as exc:
        print(f"ERROR: FRED ingestion failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: Unexpected failure: {exc}", file=sys.stderr)
        sys.exit(1)

    print()
    print("Backfill complete.")
    print(f"  Series requested: {summary.series_requested}")
    print(f"  Series succeeded: {summary.series_succeeded}")
    print(f"  Series failed:    {summary.series_failed}")
    errs = getattr(summary, "errors", None) or getattr(summary, "error_messages", None)
    if errs:
        print(f"  Errors: {errs}")

    # Print per-series row counts from the DB
    print()
    print("Post-backfill row counts (pre-1990):")
    store = DuckDBStore(db_path)
    obs = store.read_raw_observations()
    for sid in sorted(series_list):
        sub = obs[(obs["series_id"] == sid) & (obs["date"] < "1990-01-01")]
        date_range = (
            f"{sub['date'].min()} to {sub['date'].max()}" if not sub.empty else "N/A"
        )
        print(f"  {sid}: {len(sub)} rows (date range: {date_range})")

    if summary.series_failed > 0:
        print("WARNING: Some series failed. See errors above.", file=sys.stderr)
        sys.exit(1)


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill FRED macro series from 1980-01-01.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually fetch and write data. "
            "Without this flag, runs in dry-run mode (no network, no writes)."
        ),
    )
    parser.add_argument(
        "--series",
        type=str,
        nargs="*",
        default=None,
        help="One or more series IDs to backfill (default: all 12 enabled series).",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"Path to DuckDB file (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--parquet-dir",
        type=str,
        default=DEFAULT_PARQUET_DIR,
        help=f"Parquet output directory (default: {DEFAULT_PARQUET_DIR}).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_CONFIG,
        help=f"Config path (default: {DEFAULT_CONFIG}).",
    )

    args = parser.parse_args()

    # Determine which series to process
    if args.series:
        series_list = args.series
        # Validate that requested series are in the known enabled list
        unknown = [s for s in series_list if s not in ENABLED_SERIES]
        if unknown:
            print(
                f"ERROR: Unknown or disabled series: {unknown}. "
                f"Enabled series: {ENABLED_SERIES}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        series_list = ENABLED_SERIES

    if not args.apply:
        # ── Dry-run mode ────────────────────────────────────────────────
        _dry_run_report(series_list)
        sys.exit(0)

    # ── Apply mode ──────────────────────────────────────────────────────
    # Validate preconditions before making any network calls
    _verify_db_exists(args.db_path)
    _verify_api_key()

    _run_backfill(
        series_list=series_list,
        db_path=args.db_path,
        parquet_dir=args.parquet_dir,
        config_path=args.config,
    )

    print()
    print("Done. To verify, run:")
    print(
        f'  python -c "from macro_engine.storage.duckdb_store import DuckDBStore; '
        f"store = DuckDBStore('{args.db_path}'); "
        f"obs = store.read_raw_observations(); "
        f"print('Pre-1990 rows:', len(obs[obs['date'] < '1990-01-01']))\""
    )


if __name__ == "__main__":
    main()
