from __future__ import annotations

from datetime import UTC, datetime
import json

import pandas as pd

from macro_engine.news.config import SecularNewsScoringConfig, load_secular_news_scoring_config
from macro_engine.news.secular_scoring import (
    build_secular_theme_tracker,
    secular_theme_tracker_markdown,
)
from macro_engine.storage.duckdb_store import DuckDBStore


THEMES = {
    "ai_compute": {"label": "AI Compute Infrastructure"},
    "cybersecurity": {"label": "Cybersecurity"},
}


def test_secular_scoring_config_defaults_from_yaml():
    config = load_secular_news_scoring_config("config/news_scoring.yaml")

    assert config.aggregation_frequency == ["monthly", "quarterly"]
    assert config.freshness_decay.half_life_days == 30
    assert config.freshness_decay.max_age_days == 180


def test_secular_tracker_scores_only_known_successful_secular_themes():
    payload = build_secular_theme_tracker(
        news_items=_news_items(),
        classifications=_classifications(),
        scoring_config=SecularNewsScoringConfig(),
        secular_themes=THEMES,
        computed_at=datetime(2026, 5, 31, tzinfo=UTC),
    )

    ai_compute = payload["themes"]["ai_compute"]
    assert payload["valid"] is True
    assert ai_compute["item_count"] == 2
    assert ai_compute["score"] > 0
    assert ai_compute["trend_30d"] > 0
    assert ai_compute["mock_contribution_ratio"] > 0
    assert ai_compute["monthly_scores"]
    assert ai_compute["quarterly_scores"]
    assert payload["themes"]["cybersecurity"]["item_count"] == 0


def test_secular_tracker_handles_missing_optional_secular_theme_column():
    classifications = _classifications().drop(columns=["secular_theme"])
    payload = build_secular_theme_tracker(
        news_items=_news_items(),
        classifications=classifications,
        scoring_config=SecularNewsScoringConfig(),
        secular_themes=THEMES,
        computed_at=datetime(2026, 5, 31, tzinfo=UTC),
    )

    assert payload["scored_theme_count"] == 0
    assert payload["themes"]["ai_compute"]["item_count"] == 0


def test_secular_tracker_output_is_deterministic_with_fixed_time():
    kwargs = {
        "news_items": _news_items(),
        "classifications": _classifications(),
        "scoring_config": SecularNewsScoringConfig(),
        "secular_themes": THEMES,
        "computed_at": datetime(2026, 5, 31, tzinfo=UTC),
    }

    first = build_secular_theme_tracker(**kwargs)
    second = build_secular_theme_tracker(**kwargs)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_secular_tracker_markdown_sanitizes_forbidden_language():
    payload = build_secular_theme_tracker(
        news_items=_news_items(title="Analyst says buy chips now"),
        classifications=_classifications(),
        scoring_config=SecularNewsScoringConfig(),
        secular_themes=THEMES,
        computed_at=datetime(2026, 5, 31, tzinfo=UTC),
    )
    markdown = secular_theme_tracker_markdown(payload)

    assert "buy" not in markdown.lower()
    assert "market-action term" in json.dumps(payload).lower()


def test_duckdb_store_persists_optional_secular_theme(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_news_items(_news_items())
    store.replace_news_classifications(_classifications(), pd.DataFrame(), pd.DataFrame())

    stored = store.read_table("news_classifications")

    assert "secular_theme" in stored.columns
    assert set(stored["secular_theme"].dropna()) == {"ai_compute", "not_configured"}


def _news_items(title: str = "AI chip demand expands") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "news_id": "news_1",
                "source": "nvidia_blog",
                "source_url": "https://example.com/1",
                "title": title,
                "body": "Data center operators expanded accelerator clusters for AI workloads.",
                "published_at": "2026-05-25T12:00:00Z",
                "ingested_at": "2026-05-25T12:01:00Z",
                "provider": "local_csv",
                "raw_metadata_json": "{}",
                "content_hash": "hash_1",
            },
            {
                "news_id": "news_2",
                "source": "cloud_blog",
                "source_url": "https://example.com/2",
                "title": "Cloud AI infrastructure grows",
                "body": "Cloud providers added compute capacity for AI inference.",
                "published_at": "2026-05-20T12:00:00Z",
                "ingested_at": "2026-05-20T12:01:00Z",
                "provider": "local_csv",
                "raw_metadata_json": "{}",
                "content_hash": "hash_2",
            },
            {
                "news_id": "news_3",
                "source": "other",
                "source_url": "https://example.com/3",
                "title": "Unknown secular theme ignored",
                "body": "This item uses a theme not configured in the local taxonomy.",
                "published_at": "2026-05-22T12:00:00Z",
                "ingested_at": "2026-05-22T12:01:00Z",
                "provider": "local_csv",
                "raw_metadata_json": "{}",
                "content_hash": "hash_3",
            },
        ]
    )


def _classifications() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "classification_id": "class_1",
                "news_id": "news_1",
                "classified_at": "2026-05-25T12:05:00Z",
                "ai_provider": "mock",
                "ai_model": "mock-news-classifier",
                "macro_themes": [],
                "sector_impacts": [],
                "entities": [],
                "secular_theme": "ai_compute",
                "time_horizon": "long_term",
                "severity": 0.8,
                "confidence": 0.7,
                "summary": "AI compute infrastructure item.",
                "raw_ai_response": {},
                "classification_status": "success",
                "error_message": None,
            },
            {
                "classification_id": "class_2",
                "news_id": "news_2",
                "classified_at": "2026-05-20T12:05:00Z",
                "ai_provider": "deepseek",
                "ai_model": "deepseek-v4-flash",
                "macro_themes": [],
                "sector_impacts": [],
                "entities": [],
                "secular_theme": "ai_compute",
                "time_horizon": "long_term",
                "severity": 0.6,
                "confidence": 0.6,
                "summary": "Cloud AI infrastructure item.",
                "raw_ai_response": {},
                "classification_status": "success",
                "error_message": None,
            },
            {
                "classification_id": "class_3",
                "news_id": "news_3",
                "classified_at": "2026-05-22T12:05:00Z",
                "ai_provider": "deepseek",
                "ai_model": "deepseek-v4-flash",
                "macro_themes": [],
                "sector_impacts": [],
                "entities": [],
                "secular_theme": "not_configured",
                "time_horizon": "long_term",
                "severity": 0.9,
                "confidence": 0.9,
                "summary": "Unknown theme item.",
                "raw_ai_response": {},
                "classification_status": "success",
                "error_message": None,
            },
        ]
    )
