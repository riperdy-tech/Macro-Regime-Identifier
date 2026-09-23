"""Tests for N1.3: Provenance columns and the contamination fence."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from macro_engine.news.classify import MockNewsClassifier, classify_news_item, compute_prompt_version
from macro_engine.news.config import NewsThemesConfig, load_news_themes_config
from macro_engine.news.confidence_calibration import (
    build_confidence_ledger,
    repair_confidence_ledger,
)
from macro_engine.news.provenance import filter_forward_evidence, is_forward_evidence
from macro_engine.news.schema import NewsItem
from macro_engine.storage.duckdb_store import DuckDBStore


def _themes() -> NewsThemesConfig:
    return load_news_themes_config("config/news_themes.yaml")


def test_effective_reader_mixed_store_excludes_mock_and_mock_theme_sector_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "test_store.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)

    # Insert items
    items = pd.DataFrame(
        [
            {"news_id": "n_mock_1", "source": "s1", "title": "Mock 1", "body": "Mock body 1",
             "published_at": now, "ingested_at": now, "provider": "p1", "content_hash": "h1"},
            {"news_id": "n_mock_2", "source": "s1", "title": "Mock 2", "body": "Mock body 2",
             "published_at": now, "ingested_at": now, "provider": "p1", "content_hash": "h2"},
            {"news_id": "n_real_1", "source": "s1", "title": "Real 1", "body": "Real body 1",
             "published_at": now, "ingested_at": now, "provider": "p1", "content_hash": "h3"},
            {"news_id": "n_legacy_real", "source": "s1", "title": "Legacy Real", "body": "Legacy Real body",
             "published_at": now, "ingested_at": now, "provider": "p1", "content_hash": "h4"},
        ]
    )
    store.merge_news_items(items)

    # Insert classifications
    classes = pd.DataFrame(
        [
            {"classification_id": "c_mock_1", "news_id": "n_mock_1", "classified_at": now,
             "ai_provider": "mock", "ai_model": "mock-news-classifier", "classification_status": "success",
             "origin": "mock", "prompt_version": "v1"},
            {"classification_id": "c_mock_2", "news_id": "n_mock_2", "classified_at": now,
             "ai_provider": "mock", "ai_model": "mock-news-classifier", "classification_status": "success",
             "origin": "mock", "prompt_version": "v1"},
            {"classification_id": "c_real_1", "news_id": "n_real_1", "classified_at": now,
             "ai_provider": "deepseek", "ai_model": "deepseek-chat", "classification_status": "success",
             "origin": "live", "prompt_version": "v1"},
            {"classification_id": "c_legacy_real", "news_id": "n_legacy_real", "classified_at": now,
             "ai_provider": "deepseek", "ai_model": "deepseek-chat", "classification_status": "success",
             "origin": None, "prompt_version": None},
        ]
    )
    themes = pd.DataFrame(
        [
            {"news_id": "n_mock_1", "theme_id": "commodity_pressure", "direction": "positive", "severity": 0.5, "confidence": 0.7, "time_horizon": "short_term"},
            {"news_id": "n_real_1", "theme_id": "monetary_tightening", "direction": "positive", "severity": 0.6, "confidence": 0.8, "time_horizon": "short_term"},
            {"news_id": "n_legacy_real", "theme_id": "growth_slowdown", "direction": "negative", "severity": 0.4, "confidence": 0.6, "time_horizon": "short_term"},
        ]
    )
    sectors = pd.DataFrame(
        [
            {"news_id": "n_mock_1", "sector_id": "energy", "impact_direction": "tailwind", "impact_score": 0.5, "confidence": 0.7, "rationale": "r1"},
            {"news_id": "n_real_1", "sector_id": "real_estate", "impact_direction": "headwind", "impact_score": -0.4, "confidence": 0.8, "rationale": "r2"},
            {"news_id": "n_legacy_real", "sector_id": "energy", "impact_direction": "headwind", "impact_score": -0.3, "confidence": 0.6, "rationale": "r3"},
        ]
    )
    store.write_news_classifications(classes, themes, sectors)

    eff_classes, eff_themes, eff_sectors = store.read_effective_news_classifications()

    # Check classifications
    assert len(eff_classes) == 2
    assert set(eff_classes["classification_id"]) == {"c_real_1", "c_legacy_real"}
    assert set(eff_classes["news_id"]) == {"n_real_1", "n_legacy_real"}
    # effective_origin must be 'live' for both
    assert set(eff_classes["effective_origin"]) == {"live"}

    # Check theme scores and sector impacts only contain real rows
    assert set(eff_themes["news_id"]) == {"n_real_1", "n_legacy_real"}
    assert "n_mock_1" not in set(eff_themes["news_id"])

    assert set(eff_sectors["news_id"]) == {"n_real_1", "n_legacy_real"}
    assert "n_mock_1" not in set(eff_sectors["news_id"])


def test_effective_reader_mock_only_store_returns_all_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "mock_store.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)

    classes = pd.DataFrame(
        [
            {"classification_id": "c_mock_1", "news_id": "n_mock_1", "classified_at": now,
             "ai_provider": "mock", "ai_model": "mock-news-classifier", "classification_status": "success",
             "origin": "mock", "prompt_version": "v1"},
        ]
    )
    themes = pd.DataFrame(
        [
            {"news_id": "n_mock_1", "theme_id": "commodity_pressure", "direction": "positive", "severity": 0.5, "confidence": 0.7, "time_horizon": "short_term"},
        ]
    )
    sectors = pd.DataFrame(
        [
            {"news_id": "n_mock_1", "sector_id": "energy", "impact_direction": "tailwind", "impact_score": 0.5, "confidence": 0.7, "rationale": "r1"},
        ]
    )
    store.write_news_classifications(classes, themes, sectors)

    eff_classes, eff_themes, eff_sectors = store.read_effective_news_classifications()
    assert len(eff_classes) == 1
    assert eff_classes.iloc[0]["classification_id"] == "c_mock_1"
    assert eff_classes.iloc[0]["effective_origin"] == "mock"
    assert len(eff_themes) == 1
    assert len(eff_sectors) == 1


def test_ledger_repair_keeps_real_and_unknown_drops_mock(tmp_path: Path) -> None:
    db_path = tmp_path / "store_for_repair.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)

    # Store has real row and mock rows
    classes = pd.DataFrame(
        [
            {"classification_id": "c_real_0727", "news_id": "n_real_1", "classified_at": now,
             "ai_provider": "deepseek", "ai_model": "deepseek-chat", "classification_status": "success",
             "origin": "live"},
            {"classification_id": "c_mock_0727_a", "news_id": "n_mock_1", "classified_at": now,
             "ai_provider": "mock", "ai_model": "mock", "classification_status": "success",
             "origin": "mock"},
            {"classification_id": "c_mock_0727_b", "news_id": "n_mock_2", "classified_at": now,
             "ai_provider": "mock", "ai_model": "mock", "classification_status": "success",
             "origin": "mock"},
        ]
    )
    store.write_news_classifications(classes, pd.DataFrame(), pd.DataFrame())

    mock_ids = store.get_mock_classification_ids()
    assert mock_ids == {"c_mock_0727_a", "c_mock_0727_b"}

    # An accumulated ledger has real, mock, pre-wipe unknown, and another date row
    accumulated = pd.DataFrame(
        [
            {"classification_id": "c_real_0727", "news_id": "n_real_1", "prediction_date": date(2026, 7, 27),
             "sector_id": "energy", "impact_direction": "tailwind", "confidence": 0.8, "expected_sign": 1.0,
             "ai_provider": "deepseek"},
            {"classification_id": "c_mock_0727_a", "news_id": "n_mock_1", "prediction_date": date(2026, 7, 27),
             "sector_id": "energy", "impact_direction": "tailwind", "confidence": 0.8, "expected_sign": 1.0,
             "ai_provider": "mock"},
            {"classification_id": "c_mock_0727_b", "news_id": "n_mock_2", "prediction_date": date(2026, 7, 27),
             "sector_id": "energy", "impact_direction": "headwind", "confidence": 0.7, "expected_sign": -1.0,
             "ai_provider": "mock"},
            {"classification_id": "c_pre_wipe_unknown", "news_id": "n_pre_1", "prediction_date": date(2026, 7, 25),
             "sector_id": "real_estate", "impact_direction": "tailwind", "confidence": 0.6, "expected_sign": 1.0,
             "ai_provider": "deepseek"},
            {"classification_id": "c_other_date", "news_id": "n_other_1", "prediction_date": date(2026, 8, 1),
             "sector_id": "energy", "impact_direction": "headwind", "confidence": 0.75, "expected_sign": -1.0,
             "ai_provider": "deepseek"},
        ]
    )

    repaired = repair_confidence_ledger(accumulated, mock_ids)

    # Both mock rows dropped
    assert "c_mock_0727_a" not in set(repaired["classification_id"])
    assert "c_mock_0727_b" not in set(repaired["classification_id"])

    # Real row kept
    assert "c_real_0727" in set(repaired["classification_id"])

    # Pre-wipe row unknown to store kept
    assert "c_pre_wipe_unknown" in set(repaired["classification_id"])

    # Row on other date kept
    assert "c_other_date" in set(repaired["classification_id"])

    assert len(repaired) == 3


def test_forward_evidence_predicate() -> None:
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)
    pub_recent = now - timedelta(days=2)
    pub_20_days_old = now - timedelta(days=20)

    # 1. Live + success + 2 days old -> True
    assert is_forward_evidence(
        effective_origin="live",
        classification_status="success",
        classified_at=now,
        published_at=pub_recent,
    ) is True

    # 2. Replay origin -> False
    assert is_forward_evidence(
        effective_origin="replay",
        classification_status="success",
        classified_at=now,
        published_at=pub_recent,
    ) is False

    # 3. 20-day-late row (> 14 days) -> False
    assert is_forward_evidence(
        effective_origin="live",
        classification_status="success",
        classified_at=now,
        published_at=pub_20_days_old,
    ) is False

    # 4. Mock origin -> False
    assert is_forward_evidence(
        effective_origin="mock",
        classification_status="success",
        classified_at=now,
        published_at=pub_recent,
    ) is False

    # 5. Error status -> False
    assert is_forward_evidence(
        effective_origin="live",
        classification_status="error",
        classified_at=now,
        published_at=pub_recent,
    ) is False


def test_forward_evidence_dataframe_filter() -> None:
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)
    classes = pd.DataFrame(
        [
            {"classification_id": "c1", "news_id": "n1", "effective_origin": "live", "classification_status": "success", "classified_at": now},
            {"classification_id": "c2", "news_id": "n2", "effective_origin": "replay", "classification_status": "success", "classified_at": now},
            {"classification_id": "c3", "news_id": "n3", "effective_origin": "live", "classification_status": "success", "classified_at": now},
            {"classification_id": "c4", "news_id": "n4", "effective_origin": "live", "classification_status": "error", "classified_at": now},
        ]
    )
    items = pd.DataFrame(
        [
            {"news_id": "n1", "published_at": now - timedelta(days=3)},
            {"news_id": "n2", "published_at": now - timedelta(days=3)},
            {"news_id": "n3", "published_at": now - timedelta(days=20)},  # 20 days late
            {"news_id": "n4", "published_at": now - timedelta(days=3)},
        ]
    )
    filtered = filter_forward_evidence(classes, items)
    assert len(filtered) == 1
    assert filtered.iloc[0]["classification_id"] == "c1"


def test_prompt_version_and_origin_computed_and_stored(tmp_path: Path) -> None:
    expected_version = compute_prompt_version()
    assert len(expected_version) == 16

    classifier = MockNewsClassifier()
    assert classifier.origin == "mock"
    assert classifier.prompt_version == expected_version

    item = NewsItem(
        news_id="n_test_prompt",
        source="test",
        title="Energy and oil price increase",
        body="Crude oil rose 5 percent today.",
        published_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        provider="rss",
        content_hash="hash_prompt",
    )
    record = classify_news_item(item, classifier=classifier, themes=_themes())
    assert record.origin == "mock"
    assert record.prompt_version == expected_version

    # Store writes them and schema holds them
    db_path = tmp_path / "prompt_ver_store.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    store.write_news_classifications(
        pd.DataFrame([record.model_dump()]),
        pd.DataFrame(),
        pd.DataFrame(),
    )

    with store._connect() as con:
        row = con.execute(
            "SELECT origin, prompt_version FROM news_classifications WHERE classification_id = ?",
            [record.classification_id],
        ).fetchone()
        assert row[0] == "mock"
        assert row[1] == expected_version


def test_build_confidence_ledger_includes_ai_provider() -> None:
    classes = pd.DataFrame(
        [
            {"classification_id": "c1", "news_id": "n1", "classified_at": "2026-01-02T00:00:00Z", "ai_provider": "deepseek"},
        ]
    )
    impacts = pd.DataFrame(
        [
            {"news_id": "n1", "sector_id": "energy", "impact_direction": "tailwind",
             "impact_score": 0.6, "confidence": 0.90, "rationale": ""},
        ]
    )
    ledger = build_confidence_ledger(impacts, classes)
    assert "ai_provider" in ledger.columns
    assert ledger.iloc[0]["ai_provider"] == "deepseek"
