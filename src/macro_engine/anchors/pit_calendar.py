"""Which as-of dates need an ALFRED vintage.

Point-in-time integrity is bought by multiplying request volume by the number of
vintages, so the set of dates to fetch is a cost decision and has to come from the
diagnostics rather than from a guess. The evaluation calendar IS that list: it is the
authoritative enumeration of the dates the engine asks about, and it is already
materialised in DuckDB.

Both the CLI command and scripts/backfill_vintages.py read the set from here, so there
is one definition of "the dates that matter".
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from macro_engine.storage.duckdb_store import DuckDBStore


def vintage_asof_dates(
    *,
    db_path: str | Path = "data/macro_engine.duckdb",
    start: str | None = None,
    end: str | None = None,
) -> list[str]:
    """ISO as-of dates to fetch vintages for, oldest first.

    Falls back to the regime timeline for a store whose evaluation calendar has not
    been materialised yet, so a freshly ingested database is still usable.
    """
    store = DuckDBStore(db_path)
    store.initialize()
    frame = store.read_table("evaluation_calendar")
    column = "evaluation_date"
    if frame.empty or column not in frame.columns:
        frame = store.read_table("historical_regime_timeline")
        column = "date"
    if frame.empty or column not in frame.columns:
        return []
    dates = pd.to_datetime(frame[column], errors="coerce").dropna()
    if start:
        dates = dates[dates >= pd.Timestamp(start)]
    if end:
        dates = dates[dates <= pd.Timestamp(end)]
    return [value.date().isoformat() for value in sorted(dates.unique())]
