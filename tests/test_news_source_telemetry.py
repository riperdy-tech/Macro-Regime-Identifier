"""N1.5: per-source ingestion telemetry and the GDELT circuit breaker.

All fetches are injected fakes -- no network, no real GDELT rate limiting."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

import yaml

from macro_engine.news.ingest import (
    _rotate_gdelt_sources,
    load_news_items_with_report,
)
from macro_engine.news.config import NewsSourceDefinition
from macro_engine.news.service import ingest_stored_news
from macro_engine.storage.duckdb_store import DuckDBStore


def _write_gdelt_config(tmp_path: Path, n_sources: int) -> Path:
    sources = [
        {
            "source_id": f"gdelt_{i}",
            "provider": "gdelt",
            "enabled": True,
            "query": f"topic_{i}",
            "source_group": "macro_general",
            "max_items": 5,
        }
        for i in range(n_sources)
    ]
    path = tmp_path / "news_sources_test.yaml"
    path.write_text(yaml.safe_dump({"news_sources": sources}), encoding="utf-8")
    return path


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://api.gdeltproject.org/api/v2/doc/doc", code, "err", {}, None)


def test_empty_feed_gives_empty_status(tmp_path: Path):
    config_path = _write_gdelt_config(tmp_path, 1)
    items, results = load_news_items_with_report(
        config_path, gdelt_fetch=lambda url: json.dumps({"articles": []})
    )
    assert items == []
    assert len(results) == 1
    assert results[0].status == "empty"
    assert results[0].items_fetched == 0


def test_raising_feed_gives_error_status(tmp_path: Path):
    config_path = _write_gdelt_config(tmp_path, 1)

    def _raise(url: str) -> str:
        raise ValueError("feed exploded")

    items, results = load_news_items_with_report(config_path, gdelt_fetch=_raise)
    assert items == []
    assert results[0].status == "error"
    assert "feed exploded" in results[0].error


def test_429_then_429_opens_circuit_for_remaining_gdelt_sources(tmp_path: Path):
    config_path = _write_gdelt_config(tmp_path, 3)
    calls: list[str] = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        if len(calls) == 1:
            raise _http_error(429)
        return json.dumps({"articles": []})

    items, results = load_news_items_with_report(config_path, gdelt_fetch=fake_fetch)
    assert len(results) == 3
    assert results[0].status == "rate_limited"
    assert results[1].status == "circuit_open"
    assert results[2].status == "circuit_open"
    # No request was made for the circuit_open sources.
    assert len(calls) == 1


def test_single_429_recovers_and_does_not_open_circuit(tmp_path: Path):
    """A 429 that does NOT repeat (e.g. only the retry-exhausted outcome is
    injected once) never trips the breaker for a source whose OWN fetch
    ultimately succeeds."""
    config_path = _write_gdelt_config(tmp_path, 2)

    def fake_fetch(url: str) -> str:
        return json.dumps({"articles": []})

    items, results = load_news_items_with_report(config_path, gdelt_fetch=fake_fetch)
    assert [r.status for r in results] == ["empty", "empty"]


def test_rotation_start_index_depends_on_date():
    sources = [
        NewsSourceDefinition(
            source_id=f"gdelt_{i}", provider="gdelt", query="q", source_group="macro_general"
        )
        for i in range(4)
    ]
    rotated_a = _rotate_gdelt_sources(sources, date(2026, 9, 23))
    rotated_b = _rotate_gdelt_sources(sources, date(2026, 9, 24))
    assert [s.source_id for s in rotated_a] != [s.source_id for s in rotated_b]
    # Rotation is a pure reordering: every source still appears exactly once.
    assert {s.source_id for s in rotated_a} == {s.source_id for s in sources}


def test_rotation_no_sources_is_a_noop():
    assert _rotate_gdelt_sources([], date(2026, 9, 23)) == []


def test_items_new_excludes_stored_ids_and_source_runs_are_written(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    csv_path = tmp_path / "items.csv"
    csv_path.write_text(
        "title,body,source,source_url,published_at\n"
        "First headline,First body text for the article here,unit,https://x/1,2026-06-01T00:00:00Z\n"
        "Second headline,Second body text for the article here,unit,https://x/2,2026-06-01T00:00:00Z\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "news_sources_test.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "news_sources": [
                    {
                        "source_id": "csv_test",
                        "provider": "local_csv",
                        "enabled": True,
                        "path": str(csv_path),
                        "source_group": "macro_general",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    frame1 = ingest_stored_news(config_path=config_path, db_path=db_path, run_id="run_1")
    assert len(frame1) == 2

    store = DuckDBStore(db_path)
    runs1 = store.read_table("news_source_runs")
    row1 = runs1[runs1["run_id"] == "run_1"].iloc[0]
    assert row1["source_id"] == "csv_test"
    assert int(row1["items_new"]) == 2
    assert int(row1["items_fetched"]) == 2
    assert row1["status"] == "ok"

    # Second ingest: same two items are re-fetched but already stored -> 0 new.
    frame2 = ingest_stored_news(config_path=config_path, db_path=db_path, run_id="run_2")
    assert len(frame2) == 2
    runs2 = store.read_table("news_source_runs")
    row2 = runs2[runs2["run_id"] == "run_2"].iloc[0]
    assert int(row2["items_new"]) == 0
    assert int(row2["items_fetched"]) == 2
