from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import pandas as pd

from macro_engine.news.config import (
    REQUIRED_NEWS_SOURCE_GROUPS,
    NewsSourceDefinition,
    NewsSourceGroupRule,
    load_news_sources_config,
)
from macro_engine.news.schema import NewsItem


REQUIRED_NEWS_COLUMNS = {"title", "body", "source", "source_url", "published_at"}


def load_news_items_from_config(
    config_path: str | Path = "config/news_sources.yaml",
    profile: str | None = None,
) -> list[NewsItem]:
    config = load_news_sources_config(config_path)
    items: list[NewsItem] = []
    for source in config.news_sources:
        if not _source_selected(source, profile):
            continue
        if source.provider == "local_csv":
            items.extend(load_local_csv_source(source, rules=config.source_group_rules))
        elif source.provider == "local_json":
            items.extend(load_local_json_source(source, rules=config.source_group_rules))
        elif source.provider == "manual_text":
            items.extend(load_manual_text_source(source, rules=config.source_group_rules))
        elif source.provider == "rss":
            items.extend(load_rss_source(source, rules=config.source_group_rules))
        else:
            raise ValueError(f"unsupported news provider {source.provider}")
    return dedupe_news_items(items)


def validate_news_input_config(
    config_path: str | Path = "config/news_sources.yaml",
    profile: str | None = None,
) -> dict[str, Any]:
    config = load_news_sources_config(config_path)
    selected_sources = [source for source in config.news_sources if _source_selected(source, profile)]
    if not selected_sources:
        raise ValueError(f"no news sources selected for profile {profile or 'default'}")
    source_summaries: list[dict[str, Any]] = []
    all_items: list[NewsItem] = []
    warnings: list[str] = []
    for source in selected_sources:
        source_items = _load_source_for_validation(source, rules=config.source_group_rules)
        all_items.extend(source_items)
        dates = [item.published_at for item in source_items if item.published_at is not None]
        short_body_count = sum(1 for item in source_items if len(item.body.split()) < 25)
        missing_source_url_count = sum(1 for item in source_items if not item.source_url)
        future_count = sum(
            1
            for item in source_items
            if item.published_at is not None and item.published_at > datetime.now(UTC)
        )
        very_old_count = sum(
            1
            for item in source_items
            if item.published_at is not None and (datetime.now(UTC) - item.published_at).days > 365
        )
        duplicate_title_count = _duplicate_count([item.title.strip().lower() for item in source_items])
        likely_non_news_count = sum(1 for item in source_items if _likely_non_news(item))
        if short_body_count:
            warnings.append(f"{source.source_id}: {short_body_count} items have very short body text")
        if missing_source_url_count:
            warnings.append(f"{source.source_id}: {missing_source_url_count} items missing source_url")
        if future_count:
            warnings.append(f"{source.source_id}: {future_count} items have future published_at values")
        if very_old_count:
            warnings.append(f"{source.source_id}: {very_old_count} items are older than one year")
        if duplicate_title_count:
            warnings.append(f"{source.source_id}: {duplicate_title_count} duplicate titles detected")
        if likely_non_news_count:
            warnings.append(f"{source.source_id}: {likely_non_news_count} likely non-news pages detected")
        quality = "ok"
        if future_count or likely_non_news_count or missing_source_url_count > max(2, len(source_items) // 2):
            quality = "poor_input"
        elif short_body_count or missing_source_url_count or very_old_count or duplicate_title_count:
            quality = "warning"
        source_summaries.append(
            {
                "source_id": source.source_id,
                "provider": source.provider,
                "path": source.path,
                "item_count": len(source_items),
                "date_start": None if not dates else min(dates).isoformat(),
                "date_end": None if not dates else max(dates).isoformat(),
                "short_body_count": short_body_count,
                "missing_source_url_count": missing_source_url_count,
                "future_published_at_count": future_count,
                "very_old_count": very_old_count,
                "duplicate_title_count": duplicate_title_count,
                "likely_non_news_count": likely_non_news_count,
                "quality": quality,
            }
        )
    hashes = [item.content_hash for item in all_items]
    duplicate_count = len(hashes) - len(set(hashes))
    deduped = dedupe_news_items(all_items)
    if duplicate_count:
        warnings.append(f"{duplicate_count} duplicate items detected by content_hash")
    dates = [item.published_at for item in all_items if item.published_at is not None]
    by_source: dict[str, int] = {}
    by_day: dict[str, int] = {}
    by_group: dict[str, int] = {}
    for item in all_items:
        by_source[item.source] = by_source.get(item.source, 0) + 1
        group = _item_source_group(item)
        by_group[group] = by_group.get(group, 0) + 1
        if item.published_at is not None:
            day = item.published_at.date().isoformat()
            by_day[day] = by_day.get(day, 0) + 1
    unmapped_count = by_group.get("unmapped", 0)
    if unmapped_count:
        warnings.append(f"{unmapped_count} items missing source_group mapping")
    return {
        "valid": True,
        "profile": profile or "default",
        "selected_source_count": len(selected_sources),
        "raw_item_count": len(all_items),
        "unique_item_count": len(deduped),
        "duplicate_count": duplicate_count,
        "date_start": None if not dates else min(dates).isoformat(),
        "date_end": None if not dates else max(dates).isoformat(),
        "item_count_by_source": dict(sorted(by_source.items())),
        "item_count_by_source_group": dict(sorted(by_group.items())),
        "source_group_count": len([group for group in by_group if group != "unmapped"]),
        "unmapped_item_count": unmapped_count,
        "unmapped_pct": 0.0 if not all_items else unmapped_count / len(all_items),
        "item_count_by_day": dict(sorted(by_day.items())),
        "sources": source_summaries,
        "warnings": warnings,
    }


def load_local_csv_source(
    source: NewsSourceDefinition,
    *,
    rules: list[NewsSourceGroupRule] | None = None,
) -> list[NewsItem]:
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
        _news_item_from_mapping(
            _with_source_group_mapping(_with_source_defaults(row, source), source, rules or []),
            provider="local_csv",
            fallback_source_id=source.source_id,
        )
        for row in frame.to_dict(orient="records")
    ]


def load_local_json_source(
    source: NewsSourceDefinition,
    *,
    rules: list[NewsSourceGroupRule] | None = None,
) -> list[NewsItem]:
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
        _news_item_from_mapping(
            _with_source_group_mapping(_with_source_defaults(record, source), source, rules or []),
            provider="local_json",
            fallback_source_id=source.source_id,
        )
        for record in records
    ]


def load_manual_text_source(
    source: NewsSourceDefinition,
    *,
    rules: list[NewsSourceGroupRule] | None = None,
) -> list[NewsItem]:
    return [
        _news_item_from_mapping(
            _with_source_group_mapping(_with_source_defaults(item, source), source, rules or []),
            provider="manual_text",
            fallback_source_id=source.source_id,
        )
        for item in source.items
    ]


def load_rss_source(
    source: NewsSourceDefinition,
    *,
    rules: list[NewsSourceGroupRule] | None = None,
) -> list[NewsItem]:
    if source.feed_url is None:
        raise ValueError(f"rss source {source.source_id} requires feed_url")
    request = Request(
        source.feed_url,
        headers={
            "User-Agent": "macro-engine-news-pilot/0.6 (+local diagnostics)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read()
    except URLError as exc:
        raise ValueError(f"rss source {source.source_id} failed: {exc}") from exc
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"rss source {source.source_id} returned non-XML content") from exc
    records = _rss_records(root, source)
    items = [
        _news_item_from_mapping(
            _with_source_group_mapping(record, source, rules or []),
            provider="rss",
            fallback_source_id=source.source_id,
        )
        for record in records
    ]
    cutoff = None
    if source.lookback_days:
        cutoff = datetime.now(UTC) - pd.Timedelta(days=source.lookback_days).to_pytimedelta()
    filtered = [
        item
        for item in items
        if cutoff is None or item.published_at is None or item.published_at >= cutoff
    ]
    return filtered[: source.max_items]


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
    source_group = _clean_optional_text(record.get("source_group"))
    if source_group and source_group not in REQUIRED_NEWS_SOURCE_GROUPS:
        raise ValueError(f"unknown source_group {source_group}")
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


def _source_selected(source: NewsSourceDefinition, profile: str | None) -> bool:
    if profile is None:
        return source.enabled
    return source.source_id == profile or profile in source.profiles


def _load_source_for_validation(
    source: NewsSourceDefinition,
    *,
    rules: list[NewsSourceGroupRule] | None = None,
) -> list[NewsItem]:
    if source.provider == "local_csv":
        return load_local_csv_source(source, rules=rules)
    if source.provider == "local_json":
        return load_local_json_source(source, rules=rules)
    if source.provider == "manual_text":
        return load_manual_text_source(source, rules=rules)
    raise ValueError(f"unsupported news provider {source.provider}")


def _with_source_defaults(record: dict[str, Any], source: NewsSourceDefinition) -> dict[str, Any]:
    result = dict(record)
    if source.source_group and not result.get("source_group"):
        result["source_group"] = source.source_group
    if source.source and not result.get("source"):
        result["source"] = source.source
    return result


def _with_source_group_mapping(
    record: dict[str, Any],
    source: NewsSourceDefinition,
    rules: list[NewsSourceGroupRule],
) -> dict[str, Any]:
    result = dict(record)
    if _clean_optional_text(result.get("source_group")):
        result["source_group_mapping_method"] = result.get(
            "source_group_mapping_method",
            "explicit_source_group",
        )
        return result
    query_group = _clean_optional_text(result.get("query_group"))
    if query_group in REQUIRED_NEWS_SOURCE_GROUPS:
        result["source_group"] = query_group
        result["source_group_mapping_method"] = "query_group"
        return result
    for rule in rules:
        if not rule.enabled:
            continue
        if not _rule_matches(rule, source=source, record=result):
            continue
        result["source_group"] = rule.source_group
        result["source_group_mapping_rule"] = rule.rule_id
        result["source_group_mapping_method"] = "configured_rule"
        return result
    return result


def _rule_matches(
    rule: NewsSourceGroupRule,
    *,
    source: NewsSourceDefinition,
    record: dict[str, Any],
) -> bool:
    if rule.source_ids and source.source_id not in rule.source_ids:
        return False
    source_value = str(record.get("source") or source.source or source.source_id).lower()
    title_value = str(record.get("title") or "").lower()
    body_value = str(record.get("body") or "").lower()
    matchers = [
        (rule.source_keywords, source_value),
        (rule.title_keywords, title_value),
        (rule.body_keywords, body_value),
    ]
    active_matchers = [(keywords, text) for keywords, text in matchers if keywords]
    if not active_matchers:
        return True
    return any(any(keyword.lower() in text for keyword in keywords) for keywords, text in active_matchers)


def _item_source_group(item: NewsItem) -> str:
    group = item.raw_metadata.get("source_group") or item.raw_metadata.get("query_group")
    if group in REQUIRED_NEWS_SOURCE_GROUPS:
        return str(group)
    return "unmapped"


def _clean_optional_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _rss_records(root: ET.Element, source: NewsSourceDefinition) -> list[dict[str, Any]]:
    channel_items = root.findall(".//item")
    atom_items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    entries = channel_items or atom_items
    records = []
    for entry in entries:
        title = _entry_text(entry, "title")
        body = (
            _entry_text(entry, "description")
            or _entry_text(entry, "{http://www.w3.org/2005/Atom}summary")
            or _entry_text(entry, "{http://www.w3.org/2005/Atom}content")
        )
        link = _entry_text(entry, "link") or _atom_link(entry)
        published_at = (
            _entry_text(entry, "pubDate")
            or _entry_text(entry, "published")
            or _entry_text(entry, "{http://www.w3.org/2005/Atom}published")
            or _entry_text(entry, "updated")
            or _entry_text(entry, "{http://www.w3.org/2005/Atom}updated")
        )
        if not title or not body:
            continue
        records.append(
            {
                "title": title,
                "body": _strip_html(body),
                "source": source.source or source.source_id,
                "source_url": link,
                "published_at": published_at,
                "source_group": source.source_group,
                "feed_url": source.feed_url,
            }
        )
    return records


def _entry_text(entry: ET.Element, tag: str) -> str | None:
    child = entry.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _atom_link(entry: ET.Element) -> str | None:
    for child in entry.findall("{http://www.w3.org/2005/Atom}link"):
        href = child.attrib.get("href")
        if href:
            return href
    return None


def _strip_html(value: str) -> str:
    if "<" not in value or ">" not in value:
        return value
    try:
        text = ET.fromstring(f"<root>{value}</root>").itertext()
        return " ".join(part.strip() for part in text if part.strip())
    except ET.ParseError:
        return value


def _duplicate_count(values: list[str]) -> int:
    return len(values) - len(set(values))


def _likely_non_news(item: NewsItem) -> bool:
    text = f"{item.title} {item.body}".lower()
    markers = ["stock price", "quote", "historical data", "login", "subscribe to continue"]
    return any(marker in text for marker in markers)
