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

from macro_engine.evaluation.asof import normalize_asof
from macro_engine.storage.duckdb_store import DuckDBStore


def vintage_asof_dates(
    *,
    db_path: str | Path = "data/macro_engine.duckdb",
    start: str | None = None,
    end: str | None = None,
    include_present: bool = True,
) -> list[str]:
    """ISO as-of dates to fetch vintages for, oldest first.

    Falls back to the regime timeline for a store whose evaluation calendar has not
    been materialised yet, so a freshly ingested database is still usable.

    `include_present` adds the two dates a live point-in-time build actually asks about but the
    monthly evaluation calendar never contains: TODAY, and the newest stored observation date (the
    as-of an anchor build defaults to). Without them the archive stops at the calendar's last
    month start, and a present-day build silently resolves to a months-old vintage -- measured at
    −54 bp on the 10-year nominal yield, which is staleness masquerading as a revision effect
    (docs/ANCHOR_METHODOLOGY.md §6.2b). The newest-observation date is capped at today because
    several FRED series carry projections years into the future (GDPPOT reaches ~2036).
    """
    store = DuckDBStore(db_path)
    store.initialize()
    frame = store.read_table("evaluation_calendar")
    column = "evaluation_date"
    if frame.empty or column not in frame.columns:
        frame = store.read_table("historical_regime_timeline")
        column = "date"
    if frame.empty or column not in frame.columns:
        dates = pd.Series(dtype="datetime64[ns]")
    else:
        dates = pd.to_datetime(frame[column], errors="coerce").dropna()

    if include_present:
        today = normalize_asof(None)
        present = {today}
        newest = _newest_observation_on_or_before(store, today)
        if newest is not None:
            present.add(newest)
        dates = pd.Series(sorted(set(dates) | present))

    if start:
        dates = dates[dates >= pd.Timestamp(start)]
    if end:
        dates = dates[dates <= pd.Timestamp(end)]
    return [value.date().isoformat() for value in sorted(dates.unique())]


def vintage_staleness_days(vintages: pd.DataFrame | None, as_of: pd.Timestamp | str) -> int | None:
    """How far the newest vintage VISIBLE on `as_of` trails `as_of` itself, in days.

    `None` when there are no vintages at all (a different failure, reported as
    `pit_vintage_missing` by the resolver). Zero means the archive holds an as-of for that very
    day, which is what a daily refresh produces.
    """
    if vintages is None or vintages.empty or "realtime_start" not in vintages.columns:
        return None
    newest = pd.to_datetime(vintages["realtime_start"], errors="coerce").max()
    if pd.isna(newest):
        return None
    moment = pd.Timestamp(as_of).normalize()
    return int((moment - pd.Timestamp(newest).normalize()).days)


def _newest_observation_on_or_before(
    store: DuckDBStore, moment: pd.Timestamp
) -> pd.Timestamp | None:
    """Newest stored observation date that is not in the future."""
    with store._connect() as con:  # noqa: SLF001 -- read-only, and the store exposes no scalar
        row = con.execute(
            "SELECT max(date) FROM raw_observations WHERE date <= ?", [moment]
        ).fetchone()
    if not row or row[0] is None:
        return None
    return pd.Timestamp(row[0])

