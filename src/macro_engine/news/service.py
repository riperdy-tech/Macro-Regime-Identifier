from __future__ import annotations

from pathlib import Path
import json
import sys
import time
from typing import Callable

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
    profile: str | None = None,
) -> pd.DataFrame:
    items = load_news_items_from_config(config_path, profile=profile)
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
    only_unclassified: bool = False,
    progress: bool = False,
    progress_callback: Callable[[str], None] | None = None,
    continue_on_individual_failure: bool = True,
    stop_on_failure_rate_above: float | None = None,
) -> dict[str, pd.DataFrame]:
    ai_config = load_news_ai_config(ai_config_path)
    themes = load_news_themes_config(themes_config_path)
    use_mock = should_use_mock_classifier(ai_config)
    classifier = MockNewsClassifier() if use_mock else DeepSeekNewsClassifier(ai_config)
    store = DuckDBStore(db_path)
    store.initialize()
    news_items = store.read_news_items()
    existing = store.read_table("news_classifications")
    if only_unclassified and not existing.empty:
        classified_ids = set(existing["news_id"].dropna().astype(str))
        news_items = news_items[~news_items["news_id"].astype(str).isin(classified_ids)].copy()
    if limit is not None:
        news_items = news_items.sort_values("published_at", na_position="last").tail(limit)
    records = []
    rows = news_items.to_dict(orient="records")
    total = len(rows)
    failure_count = 0
    _emit_progress(
        "classify-news: ai_config "
        f"provider={ai_config.provider} model={ai_config.model} "
        f"classifier_mode={'mock' if use_mock else 'live'} "
        f"selected_items={total} limit={limit} "
        f"max_tokens={ai_config.max_tokens} "
        f"max_prompt_body_chars={ai_config.max_prompt_body_chars} "
        f"only_unclassified={only_unclassified}",
        enabled=progress,
        callback=progress_callback,
    )
    _emit_progress(
        f"classify-news: selected {total} item(s)"
        + (" using only-unclassified mode" if only_unclassified else ""),
        enabled=progress,
        callback=progress_callback,
    )
    for index, row in enumerate(rows, start=1):
        started = time.monotonic()
        item = _news_item_from_stored_row(row)
        _emit_progress(
            f"classify-news: item {index}/{total} start news_id={item.news_id}",
            enabled=progress,
            callback=progress_callback,
        )
        record = classify_news_item(
            item,
            classifier=classifier,
            themes=themes,
            enable_schema_repair=ai_config.enable_schema_repair,
            max_retries=ai_config.max_retries,
            retry_backoff_seconds=ai_config.retry_backoff_seconds,
        )
        records.append(record)
        if record.classification_status != "success":
            failure_count += 1
        store.upsert_news_classification_outputs(
            pd.DataFrame([record.model_dump()]),
            _theme_scores_from_classifications([record]),
            _sector_impacts_from_classifications([record]),
        )
        elapsed = time.monotonic() - started
        _emit_progress(
            "classify-news: item "
            f"{index}/{total} {record.classification_status} "
            f"news_id={item.news_id} elapsed={elapsed:.1f}s",
            enabled=progress,
            callback=progress_callback,
        )
        attempted = len(records)
        if record.classification_status != "success" and not continue_on_individual_failure:
            raise ValueError(f"classification failed for {item.news_id}: {record.error_message}")
        if (
            stop_on_failure_rate_above is not None
            and attempted > 0
            and failure_count / attempted > stop_on_failure_rate_above
        ):
            raise ValueError(
                "classification failure rate exceeded threshold: "
                f"{failure_count}/{attempted}"
            )
    classifications = pd.DataFrame([record.model_dump() for record in records])
    theme_scores = _theme_scores_from_classifications(records)
    sector_impacts = _sector_impacts_from_classifications(records)
    if not only_unclassified:
        store.replace_news_classifications(classifications, theme_scores, sector_impacts)
    elif records:
        existing_after = store.read_table("news_classifications")
        classifications = existing_after
        theme_scores = store.read_table("news_theme_scores")
        sector_impacts = store.read_table("news_sector_impacts")
    else:
        classifications = existing
        theme_scores = store.read_table("news_theme_scores")
        sector_impacts = store.read_table("news_sector_impacts")
    return {
        "classifications": classifications,
        "theme_scores": theme_scores,
        "sector_impacts": sector_impacts,
    }


def _emit_progress(
    message: str,
    *,
    enabled: bool,
    callback: Callable[[str], None] | None,
) -> None:
    if callback is not None:
        callback(message)
    elif enabled:
        print(message, file=sys.stderr, flush=True)


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
