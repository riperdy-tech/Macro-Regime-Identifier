"""N1.4: durable news history -- export, hydrate, import.

Every test runs against a tmp_path DuckDB copy; nothing here ever opens the
real store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from macro_engine.news.history import (
    export_news_history,
    hydrate_news_history,
    import_news_history,
)
from macro_engine.storage.duckdb_store import DuckDBStore


def _item(
    news_id: str,
    *,
    provider: str = "rss",
    source: str = "reuters",
    body: str = "A reasonably long article body for testing export filtering here.",
    title: str = "A test headline",
    published_at: datetime | None = None,
    ingested_at: datetime | None = None,
    first_seen_at: datetime | None = None,
    raw_metadata: dict | None = None,
) -> dict:
    pub = published_at or datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC)
    ing = ingested_at or datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    return {
        "news_id": news_id,
        "source": source,
        "source_url": f"https://example.com/{news_id}",
        "title": title,
        "body": body,
        "published_at": pub,
        "ingested_at": ing,
        "provider": provider,
        "raw_metadata": raw_metadata or {"source_group": "macro_general"},
        "content_hash": f"hash_{news_id}",
        "first_seen_at": first_seen_at,
    }


def _classification(
    classification_id: str,
    news_id: str,
    *,
    ai_provider: str = "deepseek",
    origin: str = "live",
    classified_at: datetime | None = None,
    status: str = "success",
) -> dict:
    return {
        "classification_id": classification_id,
        "news_id": news_id,
        "classified_at": classified_at or datetime(2026, 6, 1, 11, 0, 0, tzinfo=UTC),
        "ai_provider": ai_provider,
        "ai_model": "deepseek-v4-flash",
        "macro_themes": [
            {
                "theme_id": "growth",
                "direction": "positive",
                "severity": 0.4,
                "confidence": 0.6,
                "time_horizon": "short_term",
            }
        ],
        "sector_impacts": [
            {
                "sector_id": "technology",
                "impact_direction": "tailwind",
                "impact_score": 0.3,
                "confidence": 0.5,
                "rationale": "test rationale",
            }
        ],
        "entities": [],
        "secular_theme": None,
        "time_horizon": "short_term",
        "severity": 0.4,
        "confidence": 0.6,
        "summary": "A short AI summary of the article.",
        "raw_ai_response": {"full": "raw model output should never be exported"},
        "classification_status": status,
        "error_message": None,
        "origin": origin,
    }


def _seed_store(db_path: Path, *, n: int = 3) -> DuckDBStore:
    store = DuckDBStore(db_path)
    store.initialize()
    items = [_item(f"news_{i}") for i in range(1, n + 1)]
    store.merge_news_items(pd.DataFrame(items))
    classifications = [_classification(f"cls_{i}", f"news_{i}") for i in range(1, n + 1)]
    store.write_news_classifications(
        pd.DataFrame(classifications), pd.DataFrame(), pd.DataFrame(), origin="live"
    )
    return store


def test_round_trip_export_then_hydrate_into_empty_store(tmp_path: Path):
    src_db = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    _seed_store(src_db, n=3)

    export_result = export_news_history(db_path=src_db, history_dir=history_dir)
    assert export_result["errors"] == []
    assert export_result["items_written"] == 3
    assert export_result["classifications_written"] == 3

    dst_db = tmp_path / "dest.duckdb"
    hydrate_result = hydrate_news_history(db_path=dst_db, history_dir=history_dir)
    assert hydrate_result["errors"] == []
    assert hydrate_result["store_was_cold"] is True
    assert hydrate_result["items_inserted"] == 3
    assert hydrate_result["classifications_inserted"] == 3

    dst_store = DuckDBStore(dst_db)
    items = dst_store.read_news_items()
    assert len(items) == 3
    # Bodies become the title (no body text is durable).
    for row in items.to_dict(orient="records"):
        assert row["body"] == row["title"]
    classifications = dst_store.read_table("news_classifications")
    assert len(classifications) == 3
    assert set(classifications["origin"]) == {"live"}


def test_idempotent_second_export_writes_no_files(tmp_path: Path):
    db_path = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    _seed_store(db_path, n=2)

    export_news_history(db_path=db_path, history_dir=history_dir)
    before_mtimes = {
        p: p.stat().st_mtime for p in history_dir.rglob("*") if p.is_file()
    }

    second = export_news_history(db_path=db_path, history_dir=history_dir)
    assert second["partitions_written"] == 0
    after_mtimes = {
        p: p.stat().st_mtime for p in history_dir.rglob("*") if p.is_file()
    }
    assert before_mtimes == after_mtimes


def test_never_shrink_refuses_smaller_write(tmp_path: Path):
    db_path = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    _seed_store(db_path, n=2)
    export_news_history(db_path=db_path, history_dir=history_dir)

    manifest_path = history_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    items_partition = next(p for p in manifest["partitions"] if p.startswith("items/"))
    abs_partition = history_dir / items_partition

    # Corrupt the manifest to claim more rows than the file (and the store) actually
    # has, simulating a stale/inflated manifest entry.
    manifest["partitions"][items_partition]["rows"] = 999
    manifest_path.write_text(json.dumps(manifest))
    before_bytes = abs_partition.read_bytes()

    result = export_news_history(db_path=db_path, history_dir=history_dir)
    assert any(code.startswith("export_refused_shrink:") for code in result["errors"])
    assert abs_partition.read_bytes() == before_bytes


def test_filtering_excludes_mock_replay_synthetic_and_backfill(tmp_path: Path):
    db_path = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    store = DuckDBStore(db_path)
    store.initialize()

    items = [
        _item("real_rss", provider="rss"),
        _item("synthetic_1", provider="local_csv"),
        _item("backfilled_1", provider="gdelt", raw_metadata={"backfill": True}),
    ]
    store.merge_news_items(pd.DataFrame(items))
    classifications = [
        _classification("c_live", "real_rss", ai_provider="deepseek", origin="live"),
        _classification("c_mock", "real_rss", ai_provider="mock", origin="mock"),
        _classification("c_replay", "synthetic_1", ai_provider="deepseek", origin="replay"),
    ]
    # Each classification targets a distinct news_id so none of them conflicts
    # with N1.1's precedence writer.
    classifications[1]["news_id"] = "synthetic_1"
    classifications[2]["news_id"] = "backfilled_1"
    store.write_news_classifications(
        pd.DataFrame(classifications), pd.DataFrame(), pd.DataFrame(), origin=None
    )

    result = export_news_history(db_path=db_path, history_dir=history_dir)
    assert result["items_written"] == 1
    assert result["classifications_written"] == 1

    manifest = json.loads((history_dir / "manifest.json").read_text())
    items_partition = next(p for p in manifest["partitions"] if p.startswith("items/"))
    exported_items = pd.read_parquet(history_dir / items_partition)
    assert set(exported_items["news_id"]) == {"real_rss"}

    cls_partition = next(p for p in manifest["partitions"] if p.startswith("classifications/"))
    exported_cls = pd.read_parquet(history_dir / cls_partition)
    assert set(exported_cls["news_id"]) == {"real_rss"}


def test_privacy_no_body_or_raw_response_columns_and_allow_listed_meta(tmp_path: Path):
    db_path = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    store = DuckDBStore(db_path)
    store.initialize()
    store.merge_news_items(
        pd.DataFrame(
            [
                _item(
                    "leak_test",
                    provider="rss",
                    raw_metadata={
                        "source_group": "macro_general",
                        "feed_url": "https://example.com/feed",
                        "not_allow_listed_field": "should never appear",
                    },
                )
            ]
        )
    )
    store.write_news_classifications(
        pd.DataFrame([_classification("c_leak", "leak_test")]),
        pd.DataFrame(),
        pd.DataFrame(),
        origin="live",
    )

    export_news_history(db_path=db_path, history_dir=history_dir)
    manifest = json.loads((history_dir / "manifest.json").read_text())

    items_partition = next(p for p in manifest["partitions"] if p.startswith("items/"))
    items_df = pd.read_parquet(history_dir / items_partition)
    assert "body" not in items_df.columns
    assert "raw_metadata_json" not in items_df.columns
    meta = json.loads(items_df.iloc[0]["meta_json"])
    assert set(meta.keys()) <= {
        "source_group", "feed_url", "domain", "language", "sourcecountry",
        "gdelt_query", "finnhub_category", "source_group_mapping_method",
        "original_body_chars",
    }
    assert "not_allow_listed_field" not in meta

    cls_partition = next(p for p in manifest["partitions"] if p.startswith("classifications/"))
    cls_df = pd.read_parquet(history_dir / cls_partition)
    assert "raw_ai_response_json" not in cls_df.columns
    for col in cls_df.columns:
        for value in cls_df[col].dropna().astype(str):
            assert len(value) <= 600


def test_cold_start_drill_hydrated_id_never_reclassified_and_keeps_first_seen(tmp_path: Path):
    src_db = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    _seed_store(src_db, n=2)
    export_news_history(db_path=src_db, history_dir=history_dir)

    dst_db = tmp_path / "dest.duckdb"
    result = hydrate_news_history(db_path=dst_db, history_dir=history_dir)
    assert result["store_was_cold"] is True

    dst_store = DuckDBStore(dst_db)
    items_before = dst_store.read_news_items().set_index("news_id")
    first_seen_before = items_before["first_seen_at"].to_dict()

    # Simulate the classifier being called: it must never touch an id already
    # carrying a real classification.
    classified_ids = set(
        dst_store.read_table("news_classifications")["news_id"].astype(str)
    )
    assert classified_ids == {"news_1", "news_2"}

    def fake_classify(news_id: str) -> None:
        if news_id in classified_ids:
            raise AssertionError(f"classifier called for already-classified id {news_id}")

    # A live classifier only sees ids with no row or a mock row (N1.1's
    # eligibility rule) -- every hydrated id here already carries a real row,
    # so the eligible set is empty and the classifier is never invoked.
    eligible_ids = [nid for nid in items_before.index if nid not in classified_ids]
    assert eligible_ids == []
    for news_id in eligible_ids:
        fake_classify(news_id)  # never reached

    items_after = dst_store.read_news_items().set_index("news_id")
    assert items_after["first_seen_at"].to_dict() == first_seen_before


def test_ordering_hydrate_before_ingestion_keeps_snapshot_first_seen(tmp_path: Path):
    src_db = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    early = datetime(2026, 5, 1, 8, 0, 0, tzinfo=UTC)
    store = DuckDBStore(src_db)
    store.initialize()
    store.merge_news_items(
        pd.DataFrame([_item("news_early", ingested_at=early, first_seen_at=early)])
    )
    store.write_news_classifications(
        pd.DataFrame([_classification("c_early", "news_early")]),
        pd.DataFrame(),
        pd.DataFrame(),
        origin="live",
    )
    expected_first_seen = store.read_news_items().iloc[0]["first_seen_at"]
    export_news_history(db_path=src_db, history_dir=history_dir)

    dst_db = tmp_path / "dest.duckdb"
    hydrate_news_history(db_path=dst_db, history_dir=history_dir)

    # A later "ingestion" of the same item re-fetches a fresh (later) body.
    dst_store = DuckDBStore(dst_db)
    later = datetime(2026, 9, 23, 8, 0, 0, tzinfo=UTC)
    dst_store.merge_news_items(
        pd.DataFrame(
            [
                _item(
                    "news_early",
                    body="A freshly re-fetched, much longer article body than the hydrated headline stand-in.",
                    ingested_at=later,
                )
            ]
        )
    )
    row = dst_store.read_news_items().iloc[0]
    assert pd.Timestamp(row["first_seen_at"]) == pd.Timestamp(expected_first_seen)


def test_corrupt_partition_is_skipped_and_reported(tmp_path: Path):
    src_db = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    _seed_store(src_db, n=2)
    export_news_history(db_path=src_db, history_dir=history_dir)

    manifest_path = history_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    items_partition = next(p for p in manifest["partitions"] if p.startswith("items/"))
    abs_path = history_dir / items_partition
    abs_path.write_bytes(b"not a real parquet file")

    dst_db = tmp_path / "dest.duckdb"
    result = hydrate_news_history(db_path=dst_db, history_dir=history_dir)
    assert any(code.startswith("snapshot_partition_corrupt:") for code in result["errors"])
    # The run continues: the classifications partition still hydrates.
    dst_store = DuckDBStore(dst_db)
    assert len(dst_store.read_table("news_classifications")) == 2


def test_import_news_history_is_the_same_as_hydrate(tmp_path: Path):
    src_db = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    _seed_store(src_db, n=1)
    export_news_history(db_path=src_db, history_dir=history_dir)

    dst_db = tmp_path / "dest.duckdb"
    result = import_news_history(db_path=dst_db, history_dir=history_dir)
    assert result["items_inserted"] == 1
    assert result["classifications_inserted"] == 1


def test_daily_wiring_runs_hydrate_and_export_when_history_dir_set(tmp_path: Path):
    """N1.4 daily wiring: with news.history_dir set, both steps run and the
    summary carries their results."""
    from types import SimpleNamespace

    from macro_engine.daily import run_daily_diagnostic

    output_dir = tmp_path / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "macro.duckdb"

    cfg_text = Path("config/daily_pipeline.yaml").read_text(encoding="utf-8")
    cfg_text = cfg_text.replace(
        "mock_mode_default: true",
        f"mock_mode_default: true\n    history_dir: {(tmp_path / 'history').as_posix()}",
    )
    cfg_text = cfg_text.replace(
        "archive_root: outputs/archive", f"archive_root: {(tmp_path / 'archive').as_posix()}"
    )
    cfg_path = tmp_path / "daily_pipeline_test.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")

    def write_pair(name: str):
        j_path = output_dir / f"{name}.json"
        m_path = output_dir / f"{name}.md"
        j_path.write_text("{}", encoding="utf-8")
        m_path.write_text("diagnostic report", encoding="utf-8")
        return j_path, m_path

    hydrate_calls = []
    export_calls = []
    services = {
        "run_pipeline": lambda **_: SimpleNamespace(status="success"),
        "build_sector_scores": lambda **_: None,
        "run_sector_validation": lambda **_: None,
        "write_sector_report": lambda **_: write_pair("sector"),
        "write_sector_fit_report": lambda **_: write_pair("sector_exposures_fitted"),
        "hydrate_news_history": lambda **kw: hydrate_calls.append(kw) or {
            "store_was_cold": True, "items_inserted": 0, "classifications_inserted": 0,
            "classifications_upgraded": 0, "classifications_skipped_protected": 0,
            "source_runs_inserted": 0, "errors": [], "manifest_totals": {},
        },
        "ingest_news": lambda **_: pd.DataFrame(),
        "classify_news": lambda **_: {
            "classifications": pd.DataFrame(), "theme_scores": pd.DataFrame(),
            "sector_impacts": pd.DataFrame(), "selected_count": 0, "completed_count": 0,
            "deadline_hit": False,
        },
        "write_news_report": lambda **_: write_pair("news"),
        "build_news_scores": lambda **_: None,
        "write_news_score_report": lambda **_: write_pair("news_score"),
        "export_news_history": lambda **kw: export_calls.append(kw) or {
            "items_written": 0, "classifications_written": 0, "source_runs_written": 0,
            "partitions_written": 0, "conflicts": 0, "errors": [], "manifest_totals": {},
        },
        "build_combined": lambda **_: SimpleNamespace(),
        "write_combined_report": lambda **_: write_pair("combined"),
        "build_anchors": lambda **_: SimpleNamespace(degraded=False, degradation_reasons=[]),
        "write_news_advisory_block": lambda **_: write_pair("advisory"),
        "refresh_monitoring": lambda **_: None,
        "write_monitoring_report": lambda **_: write_pair("monitoring"),
    }

    result = run_daily_diagnostic(
        config_path=str(cfg_path),
        db_path=db_path,
        mock_ai=True,
        archive=False,
        services=services,
        output_dir=output_dir,
    )
    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["step_statuses"]["news_history_hydrate_status"] == "success"
    assert summary["step_statuses"]["news_history_export_status"] == "success"
    assert summary["news_history"]["hydrate"]["store_was_cold"] is True
    assert summary["news_history"]["export"]["items_written"] == 0
    assert len(hydrate_calls) == 1
    assert len(export_calls) == 1


def test_daily_wiring_skips_history_steps_when_history_dir_unset(tmp_path: Path):
    """With no history_dir configured (the default), neither step runs."""
    from types import SimpleNamespace

    from macro_engine.daily import run_daily_diagnostic

    output_dir = tmp_path / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "macro.duckdb"

    cfg_text = Path("config/daily_pipeline.yaml").read_text(encoding="utf-8")
    cfg_text = cfg_text.replace(
        "archive_root: outputs/archive", f"archive_root: {(tmp_path / 'archive').as_posix()}"
    )
    cfg_path = tmp_path / "daily_pipeline_test.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")

    def write_pair(name: str):
        j_path = output_dir / f"{name}.json"
        m_path = output_dir / f"{name}.md"
        j_path.write_text("{}", encoding="utf-8")
        m_path.write_text("diagnostic report", encoding="utf-8")
        return j_path, m_path

    def _fail(**_):
        raise AssertionError("history step must not run when history_dir is unset")

    services = {
        "run_pipeline": lambda **_: SimpleNamespace(status="success"),
        "build_sector_scores": lambda **_: None,
        "run_sector_validation": lambda **_: None,
        "write_sector_report": lambda **_: write_pair("sector"),
        "write_sector_fit_report": lambda **_: write_pair("sector_exposures_fitted"),
        "hydrate_news_history": _fail,
        "ingest_news": lambda **_: pd.DataFrame(),
        "classify_news": lambda **_: {
            "classifications": pd.DataFrame(), "theme_scores": pd.DataFrame(),
            "sector_impacts": pd.DataFrame(), "selected_count": 0, "completed_count": 0,
            "deadline_hit": False,
        },
        "write_news_report": lambda **_: write_pair("news"),
        "build_news_scores": lambda **_: None,
        "write_news_score_report": lambda **_: write_pair("news_score"),
        "export_news_history": _fail,
        "build_combined": lambda **_: SimpleNamespace(),
        "write_combined_report": lambda **_: write_pair("combined"),
        "build_anchors": lambda **_: SimpleNamespace(degraded=False, degradation_reasons=[]),
        "write_news_advisory_block": lambda **_: write_pair("advisory"),
        "refresh_monitoring": lambda **_: None,
        "write_monitoring_report": lambda **_: write_pair("monitoring"),
    }

    result = run_daily_diagnostic(
        config_path=str(cfg_path),
        db_path=db_path,
        mock_ai=True,
        archive=False,
        services=services,
        output_dir=output_dir,
    )
    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["step_statuses"]["news_history_hydrate_status"] == "skipped"
    assert summary["step_statuses"]["news_history_export_status"] == "skipped"
    assert summary["news_history"] == {}


def test_daily_wiring_skips_classification_on_news_blackout_precheck(tmp_path: Path):
    """N1.6 wiring: F1 (news_blackout) is known right after ingestion, before a
    single classification call is spent. classify_news must never be called,
    news_classification_status is skipped_news_failed, and the daily status
    becomes failed."""
    from types import SimpleNamespace

    from macro_engine.daily import run_daily_diagnostic
    from macro_engine.storage.duckdb_store import DuckDBStore

    output_dir = tmp_path / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "macro.duckdb"

    cfg_text = Path("config/daily_pipeline.yaml").read_text(encoding="utf-8")
    cfg_text = cfg_text.replace(
        "archive_root: outputs/archive", f"archive_root: {(tmp_path / 'archive').as_posix()}"
    )
    cfg_path = tmp_path / "daily_pipeline_test.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")

    def write_pair(name: str):
        j_path = output_dir / f"{name}.json"
        m_path = output_dir / f"{name}.md"
        j_path.write_text("{}", encoding="utf-8")
        m_path.write_text("diagnostic report", encoding="utf-8")
        return j_path, m_path

    def fake_ingest(**kwargs):
        # Simulate a run where every source fetched zero NEW items.
        store = DuckDBStore(kwargs["db_path"])
        store.initialize()
        store.insert_news_source_runs(
            pd.DataFrame(
                [
                    {
                        "run_id": kwargs["run_id"],
                        "run_at": datetime.now(UTC),
                        "source_id": "feed_a",
                        "provider": "rss",
                        "source_group": "macro_general",
                        "status": "ok",
                        "items_fetched": 5,
                        "items_new": 0,
                        "newest_published_at": None,
                        "undated_count": 0,
                        "error": None,
                        "elapsed_seconds": 1.0,
                    }
                ]
            )
        )
        return pd.DataFrame()

    services = {
        "run_pipeline": lambda **_: SimpleNamespace(status="success"),
        "build_sector_scores": lambda **_: None,
        "run_sector_validation": lambda **_: None,
        "write_sector_report": lambda **_: write_pair("sector"),
        "write_sector_fit_report": lambda **_: write_pair("sector_exposures_fitted"),
        "ingest_news": fake_ingest,
        "classify_news": MagicMock(side_effect=AssertionError("classify_news must not be called")),
        "write_news_report": lambda **_: write_pair("news"),
        "build_news_scores": lambda **_: None,
        "write_news_score_report": lambda **_: write_pair("news_score"),
        "build_combined": lambda **_: SimpleNamespace(),
        "write_combined_report": lambda **_: write_pair("combined"),
        "build_anchors": lambda **_: SimpleNamespace(degraded=False, degradation_reasons=[]),
        "write_news_advisory_block": lambda **_: write_pair("advisory"),
        "refresh_monitoring": lambda **_: None,
        "write_monitoring_report": lambda **_: write_pair("monitoring"),
    }

    result = run_daily_diagnostic(
        config_path=str(cfg_path),
        db_path=db_path,
        mock_ai=True,
        archive=False,
        services=services,
        output_dir=output_dir,
    )
    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["step_statuses"]["news_classification_status"] == "skipped_news_failed"
    assert summary["step_statuses"]["news_health_status"] == "failed"
    assert "news_blackout" in summary["news_health"]["reasons"]
    assert summary["status"] == "failed"
    # Macro/sector still ran (non-negotiable 3: news never gates the macro product).
    assert summary["step_statuses"]["macro_status"] == "success"
    assert summary["step_statuses"]["sector_status"] == "success"


def test_local_import_never_displaces_existing_real_rows(tmp_path: Path):
    """Numbers: local import on a store copy leaves existing real rows
    unchanged; imported real rows = manifest total minus already-real ids."""
    src_db = tmp_path / "source.duckdb"
    history_dir = tmp_path / "history"
    _seed_store(src_db, n=3)
    export_news_history(db_path=src_db, history_dir=history_dir)

    # The local copy already has ITS OWN real classification for news_1, with a
    # different classification_id -- it must survive the import untouched.
    dst_db = tmp_path / "dest.duckdb"
    dst_store = DuckDBStore(dst_db)
    dst_store.initialize()
    dst_store.merge_news_items(pd.DataFrame([_item("news_1")]))
    dst_store.write_news_classifications(
        pd.DataFrame([_classification("local_real_1", "news_1", classified_at=datetime(2026, 6, 5, tzinfo=UTC))]),
        pd.DataFrame(),
        pd.DataFrame(),
        origin="live",
    )

    result = import_news_history(db_path=dst_db, history_dir=history_dir)
    assert result["classifications_skipped_protected"] >= 1

    row = dst_store.read_table("news_classifications")
    row = row[row["news_id"] == "news_1"].iloc[0]
    assert row["classification_id"] == "local_real_1"
