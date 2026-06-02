"""News retention: keep the persisted DB bounded.

When the DuckDB is persisted across runs (GitHub Actions cache), classified news
accumulates. The bulky part is each classification's ``raw_ai_response_json``
(the full provider reply, a few KB each). We keep the structured fields that
scoring/readiness use, but null out the verbose raw blob once it is older than a
retention window so the DB does not grow without bound.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from macro_engine.storage.duckdb_store import DuckDBStore

DEFAULT_RAW_RESPONSE_MAX_AGE_DAYS = 150


def prune_raw_ai_responses(
    db_path: str | Path = "data/macro_engine.duckdb",
    *,
    max_age_days: int = DEFAULT_RAW_RESPONSE_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, int]:
    """Null out raw_ai_response_json for classifications older than max_age_days.

    Returns {"pruned": n} where n is the number of rows cleared. The structured
    classification columns (themes, sectors, confidence, status) are untouched.
    """
    store = DuckDBStore(db_path)
    store.initialize()
    cutoff = (now or datetime.now(UTC)) - timedelta(days=max_age_days)
    cutoff_iso = cutoff.isoformat()
    with store._connect() as con:  # noqa: SLF001 - maintenance helper
        existing = con.execute(
            "SELECT count(*) FROM news_classifications "
            "WHERE raw_ai_response_json IS NOT NULL AND classified_at < ?",
            [cutoff_iso],
        ).fetchone()[0]
        con.execute(
            "UPDATE news_classifications SET raw_ai_response_json = NULL "
            "WHERE raw_ai_response_json IS NOT NULL AND classified_at < ?",
            [cutoff_iso],
        )
    return {"pruned": int(existing or 0), "max_age_days": max_age_days}
