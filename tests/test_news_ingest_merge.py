from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
import duckdb
import pandas as pd
import pytest

from macro_engine.news.config import FulltextEnrichmentConfig
from macro_engine.news.schema import NewsItem
from macro_engine.news.service import ingest_stored_news
from macro_engine.storage.duckdb_store import DuckDBStore


def _sample_news_item_dict(
    news_id: str = "item_1",
    body: str = "Short body text for news item.",
    ingested_at: datetime | None = None,
    first_seen_at: datetime | None = None,
) -> dict:
    ing = ingested_at or datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    return {
        "news_id": news_id,
        "source": "reuters",
        "source_url": f"https://example.com/{news_id}",
        "title": f"Title for {news_id}",
        "body": body,
        "published_at": datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
        "ingested_at": ing,
        "provider": "rss",
        "raw_metadata": {"feed": "business"},
        "content_hash": f"hash_{news_id}_{len(body)}",
        "first_seen_at": first_seen_at,
    }


def test_upsert_demo_case_thin_copy_leaves_enriched_body_and_first_seen_unchanged(tmp_path: Path):
    """The upsert_demo.py case: re-ingesting a thin copy leaves the body at 900 chars (not 13)
    and leaves ingested_at and first_seen_at unchanged."""
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    initial_time = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    enriched_body = "A" * 900
    item = _sample_news_item_dict(
        news_id="news_demo_1",
        body=enriched_body,
        ingested_at=initial_time,
    )

    # First ingest: new item inserted with first_seen_at = ingested_at
    res1 = store.merge_news_items(pd.DataFrame([item]))
    assert res1["inserted"] == 1
    assert res1["body_upgraded"] == 0
    assert res1["unchanged"] == 0

    stored1 = store.read_news_items().iloc[0]
    assert len(stored1["body"]) == 900
    assert pd.notna(stored1["ingested_at"])
    assert stored1["first_seen_at"] == stored1["ingested_at"]

    # Second ingest: thin copy (13 chars) fetched later (later ingested_at)
    later_time = datetime(2026, 6, 2, 10, 0, 0, tzinfo=UTC)
    thin_body = "B" * 13
    thin_item = _sample_news_item_dict(
        news_id="news_demo_1",
        body=thin_body,
        ingested_at=later_time,
    )

    res2 = store.merge_news_items(pd.DataFrame([thin_item]))
    assert res2["inserted"] == 0
    assert res2["body_upgraded"] == 0
    assert res2["unchanged"] == 1

    stored2 = store.read_news_items().iloc[0]
    assert len(stored2["body"]) == 900
    assert stored2["body"] == enriched_body
    assert stored2["ingested_at"] == stored1["ingested_at"]
    assert stored2["first_seen_at"] == stored1["first_seen_at"]


def test_body_upgrade_allowed_without_real_classification_refused_with_one(tmp_path: Path):
    """A body upgrade is allowed with no real classification and refused with one."""
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    # Seed an item with 50-char body
    t0 = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    store.merge_news_items(pd.DataFrame([_sample_news_item_dict("news_up_1", body="X" * 50, ingested_at=t0)]))

    # Upgrade to 200 chars (no classification exists): allowed!
    item_long = _sample_news_item_dict("news_up_1", body="Y" * 200, ingested_at=datetime(2026, 6, 1, 11, 0, 0, tzinfo=UTC))
    res = store.merge_news_items(pd.DataFrame([item_long]))
    assert res["body_upgraded"] == 1
    assert res["inserted"] == 0
    assert res["unchanged"] == 0
    row = store.read_news_items().iloc[0]
    assert len(row["body"]) == 200
    assert row["body"] == "Y" * 200
    assert row["first_seen_at"] == row["ingested_at"]
    assert pd.notna(row["first_seen_at"])

    # Now add a REAL classification for this item
    store.write_news_classifications(
        pd.DataFrame([{
            "classification_id": "c_real_up",
            "news_id": "news_up_1",
            "classified_at": datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
            "ai_provider": "deepseek",
            "ai_model": "deepseek-chat",
            "macro_themes": [],
            "sector_impacts": [],
            "entities": [],
            "secular_theme": None,
            "time_horizon": "short_term",
            "severity": 0.5,
            "confidence": 0.8,
            "summary": "Real call",
            "raw_ai_response": {},
            "classification_status": "success",
            "error_message": None,
        }]),
        pd.DataFrame(),
        pd.DataFrame(),
    )

    # Attempt to upgrade body to 500 chars: refused because real classification exists!
    item_longer = _sample_news_item_dict("news_up_1", body="Z" * 500, ingested_at=datetime(2026, 6, 1, 13, 0, 0, tzinfo=UTC))
    res2 = store.merge_news_items(pd.DataFrame([item_longer]))
    assert res2["body_upgraded"] == 0
    assert res2["unchanged"] == 1
    row2 = store.read_news_items().iloc[0]
    assert len(row2["body"]) == 200
    assert row2["body"] == "Y" * 200


def test_fake_fulltext_fetcher_never_called_for_stored_id(tmp_path: Path, monkeypatch):
    """The fake fulltext fetcher is never called for a stored id."""
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    # Pre-store item_1
    store.merge_news_items(pd.DataFrame([_sample_news_item_dict("item_1", body="Stored short body")]))

    # Mock load_news_items_from_config to return item_1 (stored) and item_2 (new)
    item_1_obj = NewsItem(
        news_id="item_1",
        source="reuters",
        title="Title 1",
        body="Short 1",
        published_at=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
        ingested_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
        provider="rss",
        raw_metadata={},
        content_hash="h1",
    )
    item_2_obj = NewsItem(
        news_id="item_2",
        source="reuters",
        title="Title 2",
        body="Short 2",
        published_at=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
        ingested_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
        provider="rss",
        raw_metadata={},
        content_hash="h2",
    )

    monkeypatch.setattr(
        "macro_engine.news.service.load_news_items_from_config",
        lambda *a, **kw: [item_1_obj, item_2_obj],
    )

    fake_fetcher = MagicMock(return_value="<html><body>Full article text for item</body></html>")
    monkeypatch.setattr(
        "macro_engine.news.fulltext.default_fetch",
        lambda *a, **kw: fake_fetcher,
    )

    # Ingest stored news
    ingest_stored_news(db_path=db_path)

    # fake_fetcher should ONLY be called for item_2, never for item_1!
    for call in fake_fetcher.call_args_list:
        url = call[0][0]
        assert "item_1" not in url


def test_legacy_rows_keep_first_seen_at_null(tmp_path: Path):
    """Legacy rows without first_seen_at keep first_seen_at NULL."""
    db_path = tmp_path / "macro.duckdb"
    # Create legacy table manually without first_seen_at column
    con = duckdb.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE news_items (
            news_id TEXT PRIMARY KEY,
            source TEXT,
            source_url TEXT,
            title TEXT,
            body TEXT,
            published_at TIMESTAMP,
            ingested_at TIMESTAMP,
            provider TEXT,
            raw_metadata_json TEXT,
            content_hash TEXT
        )
        """
    )
    con.execute(
        """
        INSERT INTO news_items VALUES (
            'legacy_1', 'reuters', 'url', 'Legacy title', 'Legacy body',
            TIMESTAMP '2026-05-01 10:00:00', TIMESTAMP '2026-05-01 12:00:00',
            'rss', '{}', 'hash_legacy'
        )
        """
    )
    con.close()

    # Now open with DuckDBStore and initialize (migrates table)
    store = DuckDBStore(db_path)
    store.initialize()

    row = store.read_news_items().iloc[0]
    assert row["news_id"] == "legacy_1"
    assert "first_seen_at" in row
    assert pd.isna(row["first_seen_at"]) or row["first_seen_at"] is None


def test_ingesting_same_fixture_twice_gives_zero_inserted_and_zero_column_changes(tmp_path: Path):
    """Numbers: on a store copy, ingesting the same fixture twice gives inserted = 0
    and unchanged = n on the second pass, with zero column changes."""
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    fixture = [
        _sample_news_item_dict(f"fix_{i}", body=f"Fixture body {i}")
        for i in range(1, 6)
    ]
    df_fixture = pd.DataFrame(fixture)

    res1 = store.merge_news_items(df_fixture)
    assert res1["inserted"] == 5
    assert res1["unchanged"] == 0

    first_pass_items = store.read_news_items().sort_values("news_id").reset_index(drop=True)

    res2 = store.merge_news_items(df_fixture)
    assert res2["inserted"] == 0
    assert res2["unchanged"] == 5
    assert res2["body_upgraded"] == 0

    second_pass_items = store.read_news_items().sort_values("news_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(first_pass_items, second_pass_items)
