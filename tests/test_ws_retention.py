"""News retention: prune old raw_ai_response_json, keep structured fields."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from macro_engine.news.retention import prune_raw_ai_responses
from macro_engine.storage.duckdb_store import DuckDBStore

NOW = datetime(2026, 5, 31, tzinfo=UTC)


def _seed(db: Path) -> None:
    store = DuckDBStore(db)
    store.initialize()
    old = (NOW - timedelta(days=200)).isoformat()
    recent = (NOW - timedelta(days=10)).isoformat()
    rows = pd.DataFrame(
        [
            {
                "classification_id": "c_old",
                "news_id": "n_old",
                "classified_at": old,
                "ai_provider": "deepseek",
                "ai_model": "m",
                "macro_themes_json": "[]",
                "sector_impacts_json": "[]",
                "entities_json": "[]",
                "secular_theme": None,
                "time_horizon": "short_term",
                "severity": 0.1,
                "confidence": 0.2,
                "summary": "old",
                "raw_ai_response_json": '{"big": "blob"}',
                "classification_status": "success",
                "error_message": None,
            },
            {
                "classification_id": "c_new",
                "news_id": "n_new",
                "classified_at": recent,
                "ai_provider": "deepseek",
                "ai_model": "m",
                "macro_themes_json": "[]",
                "sector_impacts_json": "[]",
                "entities_json": "[]",
                "secular_theme": None,
                "time_horizon": "short_term",
                "severity": 0.1,
                "confidence": 0.2,
                "summary": "new",
                "raw_ai_response_json": '{"big": "blob"}',
                "classification_status": "success",
                "error_message": None,
            },
        ]
    )
    with store._connect() as con:  # noqa: SLF001
        con.execute("DELETE FROM news_classifications")
        con.register("rows", rows)
        con.execute("INSERT INTO news_classifications SELECT * FROM rows")


def test_prune_nulls_old_keeps_recent(tmp_path: Path):
    db = tmp_path / "t.duckdb"
    _seed(db)
    result = prune_raw_ai_responses(db, max_age_days=150, now=NOW)
    assert result["pruned"] == 1

    store = DuckDBStore(db)
    df = store.read_table("news_classifications").set_index("classification_id")
    # Old: raw blob nulled, but structured fields intact.
    assert pd.isna(df.loc["c_old", "raw_ai_response_json"])
    assert df.loc["c_old", "summary"] == "old"
    assert df.loc["c_old", "classification_status"] == "success"
    # Recent: untouched.
    assert df.loc["c_new", "raw_ai_response_json"] == '{"big": "blob"}'


def test_prune_idempotent(tmp_path: Path):
    db = tmp_path / "t.duckdb"
    _seed(db)
    prune_raw_ai_responses(db, max_age_days=150, now=NOW)
    second = prune_raw_ai_responses(db, max_age_days=150, now=NOW)
    assert second["pruned"] == 0  # nothing left to prune
