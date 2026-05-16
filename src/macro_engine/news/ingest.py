from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.news.config import NewsSourceDefinition, load_news_sources_config
from macro_engine.news.schema import NewsItem


REQUIRED_NEWS_COLUMNS = {"title", "body", "source", "source_url", "published_at"}


def load_news_items_from_config(
    config_path: str | Path = "config/news_sources.yaml",
) -> list[NewsItem]:
    config = load_news_sources_config(config_path)
    items: list[NewsItem] = []
    for source in config.news_sources:
        if not source.enabled:
            continue
        if source.provider == "local_csv":
            items.extend(load_local_csv_source(source))
        elif source.provider == "local_json":
            items.extend(load_local_json_source(source))
        elif source.provider == "manual_text":
            items.extend(load_manual_text_source(source))
        else:
            raise ValueError(f"unsupported news provider {source.provider}")
    return dedupe_news_items(items)


def load_local_csv_source(source: NewsSourceDefinition) -> list[NewsItem]:
    if source.path is None:
        raise ValueError(f"local_csv source {source.source_id} requires path")
    path = Path(source.path)
    if not path.exists():
        raise FileNotFoundError(f"news CSV not found: {path}")
    frame = pd.read_csv(path)
    missing = REQUIRED_NEWS_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"news CSV missing required columns: {sorted(missing)}")
    return [
        _news_item_from_mapping(row, provider="local_csv", fallback_source_id=source.source_id)
        for row in frame.to_dict(orient="records")
    ]


def load_local_json_source(source: NewsSourceDefinition) -> list[NewsItem]:
    if source.path is None:
        raise ValueError(f"local_json source {source.source_id} requires path")
    path = Path(source.path)
    if not path.exists():
        raise FileNotFoundError(f"news JSON not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("items", payload) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("local_json news payload must be a list or an object with items")
    return [
        _news_item_from_mapping(record, provider="local_json", fallback_source_id=source.source_id)
        for record in records
    ]


def load_manual_text_source(source: NewsSourceDefinition) -> list[NewsItem]:
    return [
        _news_item_from_mapping(item, provider="manual_text", fallback_source_id=source.source_id)
        for item in source.items
    ]


def dedupe_news_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        if item.content_hash in seen:
            continue
        seen.add(item.content_hash)
        deduped.append(item)
    return deduped


def content_hash_for_news(
    *,
    title: str,
    body: str,
    source: str,
    published_at: str | None,
) -> str:
    normalized = "\n".join(
        [
            source.strip().lower(),
            str(published_at or "").strip(),
            title.strip(),
            body.strip(),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _news_item_from_mapping(
    record: dict[str, Any],
    *,
    provider: str,
    fallback_source_id: str,
) -> NewsItem:
    title = str(record.get("title", "")).strip()
    body = str(record.get("body", "")).strip()
    if not title or not body:
        raise ValueError("news item requires title and body")
    source = str(record.get("source") or fallback_source_id).strip()
    published_at_raw = record.get("published_at")
    published_at = _parse_datetime(published_at_raw)
    content_hash = content_hash_for_news(
        title=title,
        body=body,
        source=source,
        published_at=None if published_at is None else published_at.isoformat(),
    )
    return NewsItem(
        news_id=f"news_{content_hash[:16]}",
        source=source,
        source_url=None if pd.isna(record.get("source_url")) else record.get("source_url"),
        title=title,
        body=body,
        published_at=published_at,
        ingested_at=datetime.now(UTC),
        provider=provider,
        raw_metadata={
            str(key): _json_safe(value)
            for key, value in record.items()
            if key not in {"title", "body", "source", "source_url", "published_at"}
        },
        content_hash=content_hash,
    )


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _json_safe(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
