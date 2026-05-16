from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from macro_engine.news.classify import (
    MockNewsClassifier,
    classify_news_item,
    should_use_mock_classifier,
)
from macro_engine.news.config import (
    load_news_ai_config,
    load_news_themes_config,
)
from macro_engine.news.ingest import load_news_items_from_config
from macro_engine.news.providers.openai_classifier import DeepSeekNewsClassifier
from macro_engine.storage.duckdb_store import DuckDBStore


def ingest_stored_news(
    *,
    config_path: str | Path = "config/news_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> pd.DataFrame:
    items = load_news_items_from_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    frame = pd.DataFrame([item.model_dump() for item in items])
    store.upsert_news_items(frame)
    return frame


def classify_stored_news(
    *,
    ai_config_path: str | Path = "config/news_ai.yaml",
    themes_config_path: str | Path = "config/news_themes.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    limit: int | None = None,
) -> dict[str, pd.DataFrame]:
    ai_config = load_news_ai_config(ai_config_path)
    themes = load_news_themes_config(themes_config_path)
    classifier = MockNewsClassifier() if should_use_mock_classifier(ai_config) else DeepSeekNewsClassifier(ai_config)
    store = DuckDBStore(db_path)
    store.initialize()
    news_items = store.read_news_items()
    if limit is not None:
        news_items = news_items.sort_values("published_at", na_position="last").tail(limit)
    records = []
    for row in news_items.to_dict(orient="records"):
        item = _news_item_from_stored_row(row)
        records.append(classify_news_item(item, classifier=classifier, themes=themes))
    classifications = pd.DataFrame([record.model_dump() for record in records])
    theme_scores = _theme_scores_from_classifications(records)
    sector_impacts = _sector_impacts_from_classifications(records)
    store.replace_news_classifications(classifications, theme_scores, sector_impacts)
    return {
        "classifications": classifications,
        "theme_scores": theme_scores,
        "sector_impacts": sector_impacts,
    }


def _news_item_from_stored_row(row: dict):
    from macro_engine.news.schema import NewsItem

    return NewsItem.model_validate(
        {
            **row,
            "raw_metadata": _metadata_from_row(row),
        }
    )


def _metadata_from_row(row: dict):
    value = row.get("raw_metadata_json") or row.get("raw_metadata") or {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _theme_scores_from_classifications(records) -> pd.DataFrame:
    rows = []
    for record in records:
        for theme in record.macro_themes:
            rows.append(
                {
                    "news_id": record.news_id,
                    "theme_id": theme["theme_id"],
                    "direction": theme["direction"],
                    "severity": theme["severity"],
                    "confidence": theme["confidence"],
                    "time_horizon": theme["time_horizon"],
                }
            )
    return pd.DataFrame(rows, columns=["news_id", "theme_id", "direction", "severity", "confidence", "time_horizon"])


def _sector_impacts_from_classifications(records) -> pd.DataFrame:
    rows = []
    for record in records:
        for impact in record.sector_impacts:
            rows.append(
                {
                    "news_id": record.news_id,
                    "sector_id": impact["sector_id"],
                    "impact_direction": impact["impact_direction"],
                    "impact_score": impact["impact_score"],
                    "confidence": impact["confidence"],
                    "rationale": impact.get("rationale", ""),
                }
            )
    return pd.DataFrame(rows, columns=["news_id", "sector_id", "impact_direction", "impact_score", "confidence", "rationale"])
