"""Aware UTC timestamps are stored as naive UTC whatever the machine's time zone:
on a KST machine they were stored 9 hours ahead (DuckDB converts an aware value into
the session time zone, which defaults to the OS zone)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from macro_engine.storage.duckdb_store import DuckDBStore


def test_aware_utc_timestamp_is_stored_as_utc(tmp_path: Path):
    store = DuckDBStore(tmp_path / "macro.duckdb")
    store.initialize()
    with store._connect() as con:
        assert con.execute("SELECT current_setting('TimeZone')").fetchone()[0] == "UTC"

    classified_at = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)
    store.write_news_classifications(
        pd.DataFrame([{
            "classification_id": "c1", "news_id": "n1", "classified_at": classified_at,
            "ai_provider": "deepseek", "ai_model": "m", "classification_status": "error",
        }]),
        pd.DataFrame(),
        pd.DataFrame(),
    )
    stored = store.read_table("news_classifications")["classified_at"].iloc[0]
    assert pd.Timestamp(stored) == pd.Timestamp("2026-10-30 01:00:00")
