from __future__ import annotations

import ast
from datetime import UTC, datetime
import json
from pathlib import Path
from unittest.mock import MagicMock
import duckdb
import pandas as pd
import pytest
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.daily import run_daily_diagnostic
from macro_engine.news.config import NewsThemesConfig
from macro_engine.news.schema import NewsItem
from macro_engine.news.service import classify_stored_news
from macro_engine.replay import _persist_replay_day_to_central_db
from macro_engine.storage.duckdb_store import DuckDBStore


def _sample_real_classification(news_id: str = "real_1", cid: str = "c_real_1") -> dict:
    return {
        "classification_id": cid,
        "news_id": news_id,
        "classified_at": datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
        "ai_provider": "deepseek",
        "ai_model": "deepseek-chat",
        "macro_themes": [{"theme_id": "inflation_pressure", "direction": "positive", "severity": 0.8, "confidence": 0.9, "time_horizon": "short_term"}],
        "sector_impacts": [{"sector_id": "energy", "impact_direction": "tailwind", "impact_score": 0.5, "confidence": 0.8, "rationale": "oil up"}],
        "entities": [{"name": "Fed", "entity_type": "central_bank", "relevance": 0.9}],
        "secular_theme": "ai_compute",
        "time_horizon": "short_term",
        "severity": 0.8,
        "confidence": 0.9,
        "summary": "Real deepseek classification",
        "raw_ai_response": {"model": "deepseek-chat", "text": "real"},
        "classification_status": "success",
        "error_message": None,
        "origin": "live",
        "prompt_version": "v1",
    }


def _sample_mock_classification(news_id: str = "mock_1", cid: str = "c_mock_1") -> dict:
    return {
        "classification_id": cid,
        "news_id": news_id,
        "classified_at": datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
        "ai_provider": "mock",
        "ai_model": "mock-news-classifier",
        "macro_themes": [{"theme_id": "growth_expansion", "direction": "positive", "severity": 0.5, "confidence": 0.5, "time_horizon": "short_term"}],
        "sector_impacts": [{"sector_id": "technology", "impact_direction": "tailwind", "impact_score": 0.4, "confidence": 0.7, "rationale": "tech"}],
        "entities": [],
        "secular_theme": None,
        "time_horizon": "short_term",
        "severity": 0.5,
        "confidence": 0.5,
        "summary": "Mock classification",
        "raw_ai_response": {"mock": True},
        "classification_status": "success",
        "error_message": None,
        "origin": "mock",
        "prompt_version": "v1",
    }


def _seed_store_with_items_and_classifications(
    store: DuckDBStore,
    real_count: int = 2,
    mock_count: int = 1,
    unclassified_count: int = 2,
) -> tuple[list[dict], list[dict]]:
    store.initialize()
    items = []
    classifications = []
    theme_scores = []
    sector_impacts = []

    # Real items
    for i in range(1, real_count + 1):
        nid = f"news_real_{i}"
        items.append({
            "news_id": nid,
            "source": "reuters",
            "source_url": f"https://reuters.com/{nid}",
            "title": f"Real title {i}",
            "body": f"Real body {i}",
            "published_at": datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
            "ingested_at": datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
            "provider": "rss",
            "raw_metadata": {},
            "content_hash": f"hash_real_{i}",
        })
        c = _sample_real_classification(news_id=nid, cid=f"c_real_{i}")
        classifications.append(c)
        theme_scores.append({
            "news_id": nid,
            "theme_id": "inflation_pressure",
            "direction": "positive",
            "severity": 0.8,
            "confidence": 0.9,
            "time_horizon": "short_term",
        })
        sector_impacts.append({
            "news_id": nid,
            "sector_id": "energy",
            "impact_direction": "tailwind",
            "impact_score": 0.5,
            "confidence": 0.8,
            "rationale": "oil up",
        })

    # Mock items
    for i in range(1, mock_count + 1):
        nid = f"news_mock_{i}"
        items.append({
            "news_id": nid,
            "source": "reuters",
            "source_url": f"https://reuters.com/{nid}",
            "title": f"Mock title {i}",
            "body": f"Mock body {i}",
            "published_at": datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
            "ingested_at": datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
            "provider": "rss",
            "raw_metadata": {},
            "content_hash": f"hash_mock_{i}",
        })
        c = _sample_mock_classification(news_id=nid, cid=f"c_mock_{i}")
        classifications.append(c)
        theme_scores.append({
            "news_id": nid,
            "theme_id": "growth_expansion",
            "direction": "positive",
            "severity": 0.5,
            "confidence": 0.5,
            "time_horizon": "short_term",
        })
        sector_impacts.append({
            "news_id": nid,
            "sector_id": "technology",
            "impact_direction": "tailwind",
            "impact_score": 0.4,
            "confidence": 0.7,
            "rationale": "tech",
        })

    # Unclassified items
    for i in range(1, unclassified_count + 1):
        nid = f"news_unclass_{i}"
        items.append({
            "news_id": nid,
            "source": "reuters",
            "source_url": f"https://reuters.com/{nid}",
            "title": f"Unclassified title {i}",
            "body": f"Unclassified body {i}",
            "published_at": datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
            "ingested_at": datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
            "provider": "rss",
            "raw_metadata": {},
            "content_hash": f"hash_unclass_{i}",
        })

    store.upsert_news_items(pd.DataFrame(items))
    if classifications:
        store.write_news_classifications(
            pd.DataFrame(classifications),
            pd.DataFrame(theme_scores),
            pd.DataFrame(sector_impacts),
        )
    return classifications, items


def test_a_0727_regression_mock_daily_run_skips_news_on_live_store(tmp_path: Path):
    """Test a: The 07-27 regression. Store with real rows under mock daily run."""
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    real_classifications, _ = _seed_store_with_items_and_classifications(
        store, real_count=3, mock_count=2, unclassified_count=2
    )

    before_real = store.read_table("news_classifications").query("ai_provider != 'mock'").sort_values("news_id").reset_index(drop=True)
    before_all_count = len(store.read_table("news_classifications"))
    before_theme_count = len(store.read_table("news_theme_scores"))
    before_sector_count = len(store.read_table("news_sector_impacts"))

    from types import SimpleNamespace

    outputs = tmp_path / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)

    def write_pair(name: str):
        j_path = outputs / f"{name}.json"
        m_path = outputs / f"{name}.md"
        j_path.write_text("{}", encoding="utf-8")
        m_path.write_text("diagnostic report", encoding="utf-8")
        return j_path, m_path

    anchors_mock = SimpleNamespace(degraded=False, degradation_reasons=[])

    services = {
        "run_pipeline": lambda **_: SimpleNamespace(status="success"),
        "build_sector_scores": lambda **_: None,
        "run_sector_validation": lambda **_: None,
        "write_sector_report": lambda **_: write_pair("sector"),
        "write_sector_fit_report": lambda **_: write_pair("sector_exposures_fitted"),
        "ingest_news": MagicMock(side_effect=AssertionError("ingest_news must not be called")),
        "classify_news": MagicMock(side_effect=AssertionError("classify_news must not be called")),
        "build_news_scores": lambda **_: SimpleNamespace(
            daily_theme_scores=pd.DataFrame(),
            daily_sector_scores=pd.DataFrame(),
            weekly_theme_scores=pd.DataFrame(),
            weekly_sector_scores=pd.DataFrame(),
            components=pd.DataFrame(),
            runs=pd.DataFrame(),
        ),
        "write_news_report": lambda **_: write_pair("news"),
        "write_news_score_report": lambda **_: write_pair("news_score"),
        "build_combined": lambda **_: SimpleNamespace(),
        "write_combined_report": lambda **_: write_pair("combined"),
        "build_anchors": lambda **_: anchors_mock,
        "write_news_advisory_block": lambda **_: write_pair("advisory"),
        "refresh_monitoring": lambda **_: None,
        "write_monitoring_report": lambda **_: write_pair("monitoring"),
    }

    result = run_daily_diagnostic(
        config_path="config/daily_pipeline.yaml",
        db_path=db_path,
        mock_ai=True,
        archive=False,
        services=services,
        output_dir=outputs,
    )

    summary_json = json.loads(result.summary_json_path.read_text(encoding="utf-8"))

    # News steps skipped
    assert summary_json["step_statuses"]["news_ingestion_status"] == "skipped_live_store"
    assert summary_json["step_statuses"]["news_classification_status"] == "skipped_live_store"
    assert "news_nonlive_skipped_on_live_store" in result.warnings
    assert summary_json["step_statuses"]["anchors_status"] == "success"

    # Real rows identical
    after_real = store.read_table("news_classifications").query("ai_provider != 'mock'").sort_values("news_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(before_real, after_real)

    # Counts unchanged
    assert len(store.read_table("news_classifications")) == before_all_count
    assert len(store.read_table("news_theme_scores")) == before_theme_count
    assert len(store.read_table("news_sector_impacts")) == before_sector_count


def test_b_classify_stored_news_mock_with_only_unclassified_false(tmp_path: Path):
    """Test b: Mock classifier with only_unclassified=False over store with real rows.
    Real rows unchanged, mock rows inserted only for ids with no row."""
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    real_classifications, _ = _seed_store_with_items_and_classifications(
        store, real_count=2, mock_count=1, unclassified_count=2
    )

    before_real = store.read_table("news_classifications").query("ai_provider != 'mock'").sort_values("news_id").reset_index(drop=True)

    result = classify_stored_news(
        db_path=db_path,
        only_unclassified=False,
    )

    after_real = store.read_table("news_classifications").query("ai_provider != 'mock'").sort_values("news_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(before_real, after_real)

    # Unclassified items (2 of them) received mock rows; mock item already had row, so only 2 inserted
    assert result["inserted"] == 2
    assert result["upgraded"] == 0
    all_class = store.read_table("news_classifications")
    assert len(all_class) == 2 + 1 + 2  # 2 real + 1 existing mock + 2 newly inserted mock


def test_c_live_fake_classifier_upgrades_mock_skips_real(tmp_path: Path, monkeypatch):
    """Test c: Live fake classifier over mock row upgrades it, over real row gives skipped_protected == 1."""
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    _seed_store_with_items_and_classifications(
        store, real_count=1, mock_count=1, unclassified_count=0
    )

    class FakeLiveClassifier:
        provider_name = "deepseek"
        model_name = "deepseek-chat"

        def classify(self, item: NewsItem, themes: NewsThemesConfig) -> dict:
            return {
                "summary": f"Live classified {item.news_id}",
                "macro_themes": [{"theme_id": "inflation_pressure", "direction": "positive", "severity": 0.9, "confidence": 0.9, "time_horizon": "short_term"}],
                "sector_impacts": [{"sector_id": "energy", "impact_direction": "tailwind", "impact_score": 0.8, "confidence": 0.85, "rationale": "upgraded"}],
                "entities": [],
                "secular_theme": None,
                "overall_severity": 0.9,
                "overall_confidence": 0.9,
                "time_horizon": "short_term",
            }

    monkeypatch.setattr("macro_engine.news.service.should_use_mock_classifier", lambda cfg: False)
    monkeypatch.setattr("macro_engine.news.service.DeepSeekNewsClassifier", lambda cfg: FakeLiveClassifier())

    # With only_unclassified=False, live classifier sees mock row (upgrade candidate)
    res = classify_stored_news(db_path=db_path, only_unclassified=False)
    assert res["upgraded"] == 1
    assert res["inserted"] == 0

    # Over news_mock_1: provider upgraded to deepseek
    upgraded_row = store.read_table("news_classifications").query("news_id == 'news_mock_1'").iloc[0]
    assert upgraded_row["ai_provider"] == "deepseek"
    assert upgraded_row["summary"] == "Live classified news_mock_1"

    # Now run again: both rows are now real (deepseek), so live classifier sees 0 items, but if we call write_news_classifications directly on a real row:
    real_row = _sample_real_classification(news_id="news_real_1")
    write_res = store.write_news_classifications(
        pd.DataFrame([real_row]),
        pd.DataFrame([{"news_id": "news_real_1", "theme_id": "inflation_pressure", "direction": "positive", "severity": 0.1, "confidence": 0.1, "time_horizon": "short_term"}]),
        pd.DataFrame(),
    )
    assert write_res["skipped_protected"] == 1
    assert write_res["upgraded"] == 0
    assert write_res["inserted"] == 0


def test_d_atomicity_rollback_on_write_failure(tmp_path: Path, monkeypatch):
    """Test d: Atomicity. Monkeypatch connection so INSERT raises. Table unchanged."""
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    _seed_store_with_items_and_classifications(store, real_count=2, mock_count=1, unclassified_count=0)

    before_class = store.read_table("news_classifications")

    orig_connect = store._connect

    class FailingConnection:
        def __init__(self, con):
            self._con = con

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return self._con.__exit__(exc_type, exc_val, exc_tb)

        def execute(self, sql, *args, **kwargs):
            if "INSERT INTO news_classifications" in sql:
                raise RuntimeError("simulated atomic write failure")
            return self._con.execute(sql, *args, **kwargs)

        def register(self, *args, **kwargs):
            return self._con.register(*args, **kwargs)

    monkeypatch.setattr(store, "_connect", lambda: FailingConnection(orig_connect()))

    new_c = _sample_mock_classification(news_id="new_atomic_news", cid="c_atomic_1")
    with pytest.raises(RuntimeError, match="simulated atomic write failure"):
        store.write_news_classifications(
            pd.DataFrame([new_c]),
            pd.DataFrame(),
            pd.DataFrame(),
        )

    # Verify table unchanged
    after_class = store.read_table("news_classifications")
    pd.testing.assert_frame_equal(before_class, after_class)


def test_e_reachability_ast_scan():
    """Test e: Reachability. AST scan of src/ finds destroy_all_news_classifications
    referenced only by maintenance CLI, and no DELETE FROM news_classifications
    outside precedence writer and maintenance method."""
    src_dir = Path("src/macro_engine")
    destroy_refs = []
    delete_from_classifications = []

    for py_file in src_dir.rglob("*.py"):
        code = py_file.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(py_file))
        rel_path = str(py_file.relative_to(src_dir.parent))

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "destroy_all_news_classifications":
                destroy_refs.append(rel_path)
            elif isinstance(node, ast.Name) and node.id == "destroy_all_news_classifications":
                # Definition in duckdb_store.py is allowed
                if "duckdb_store.py" not in rel_path:
                    destroy_refs.append(rel_path)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "DELETE FROM news_classifications" in node.value:
                    delete_from_classifications.append((rel_path, node.value.strip()))

    # destroy_all_news_classifications only referenced in cli.py (and defined in duckdb_store.py)
    for ref in destroy_refs:
        assert "cli.py" in ref or "duckdb_store.py" in ref, f"Unexpected reference to destroy_all_news_classifications in {ref}"

    # DELETE FROM news_classifications only in duckdb_store.py
    for path, sql in delete_from_classifications:
        assert "duckdb_store.py" in path, f"Found DELETE FROM news_classifications in {path}: {sql}"


def test_f_maintenance_cli_refuses_wrong_confirm_count_and_backs_up(tmp_path: Path):
    """Test f: Maintenance CLI refuses wrong --confirm-real-rows. With right value,
    backup files exist with pre-delete counts before table is empty."""
    db_path = tmp_path / "macro.duckdb"
    backup_dir = tmp_path / "backup"
    store = DuckDBStore(db_path)
    _seed_store_with_items_and_classifications(store, real_count=3, mock_count=2, unclassified_count=0)

    runner = CliRunner()

    # Wrong confirmation count (e.g. 99 instead of 3)
    res_wrong = runner.invoke(app, [
        "maintenance-destroy-news-classifications",
        "--db-path", str(db_path),
        "--backup-dir", str(backup_dir),
        "--confirm-real-rows", "99",
    ])
    assert res_wrong.exit_code != 0
    assert "confirm_real_rows mismatch" in str(res_wrong.exception) or "confirm_real_rows mismatch" in res_wrong.output

    # Table still has rows
    assert len(store.read_table("news_classifications")) == 5

    # Right confirmation count (3)
    res_right = runner.invoke(app, [
        "maintenance-destroy-news-classifications",
        "--db-path", str(db_path),
        "--backup-dir", str(backup_dir),
        "--confirm-real-rows", "3",
    ])
    assert res_right.exit_code == 0

    # Backup files exist with correct counts
    con = duckdb.connect(str(db_path))
    class_backup = con.execute("SELECT count(*) FROM read_parquet(?)", [str(backup_dir / "news_classifications.parquet")]).fetchone()[0]
    theme_backup = con.execute("SELECT count(*) FROM read_parquet(?)", [str(backup_dir / "news_theme_scores.parquet")]).fetchone()[0]
    sector_backup = con.execute("SELECT count(*) FROM read_parquet(?)", [str(backup_dir / "news_sector_impacts.parquet")]).fetchone()[0]
    con.close()

    assert class_backup == 5
    assert theme_backup == 5
    assert sector_backup == 5

    # Store tables are now empty
    assert len(store.read_table("news_classifications")) == 0
    assert len(store.read_table("news_theme_scores")) == 0
    assert len(store.read_table("news_sector_impacts")) == 0


def test_g_replay_persist_leaves_real_rows_unchanged(tmp_path: Path):
    """Test g: Replay persist into a central store with real rows leaves real rows unchanged."""
    central_db = tmp_path / "central.duckdb"
    daily_db = tmp_path / "daily.duckdb"
    central_store = DuckDBStore(central_db)
    daily_store = DuckDBStore(daily_db)

    _seed_store_with_items_and_classifications(central_store, real_count=2, mock_count=0, unclassified_count=0)
    _seed_store_with_items_and_classifications(daily_store, real_count=0, mock_count=1, unclassified_count=0)

    # In daily store, mock item has same news_id as one of the real ones
    daily_item = daily_store.read_table("news_items").iloc[0].to_dict()
    daily_item["news_id"] = "news_real_1"
    daily_store.upsert_news_items(pd.DataFrame([daily_item]))
    daily_class = daily_store.read_table("news_classifications").iloc[0].to_dict()
    daily_class["classification_id"] = "c_replay_mock_1"
    daily_class["news_id"] = "news_real_1"
    daily_class["summary"] = "Replay overwrite attempt"
    daily_store.write_news_classifications(pd.DataFrame([daily_class]), pd.DataFrame(), pd.DataFrame())

    before_real = central_store.read_table("news_classifications").query("news_id == 'news_real_1'").iloc[0].to_dict()

    _persist_replay_day_to_central_db(
        daily_db_path=daily_db,
        central_db_path=central_db,
        replay_day=datetime(2026, 6, 1, tzinfo=UTC).date(),
    )

    after_real = central_store.read_table("news_classifications").query("news_id == 'news_real_1'").iloc[0].to_dict()

    assert before_real["summary"] == after_real["summary"]
    assert after_real["summary"] != "Replay overwrite attempt"
    assert after_real["ai_provider"] == "deepseek"
