from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.news.config import (
    NewsSourceWatchlistConfig,
    load_news_source_watchlist_config,
)
from macro_engine.news.report import FORBIDDEN_REPORT_TERMS
from macro_engine.storage.duckdb_store import DuckDBStore


SOURCE_COVERAGE_DISCLAIMER = (
    "This source coverage report is an operating diagnostic for news collection breadth. "
    "It is not investment advice, market action guidance, execution guidance, or "
    "instructions for changing holdings."
)


def validate_news_sources_config(
    path: str | Path = "config/news_source_watchlist.yaml",
) -> NewsSourceWatchlistConfig:
    return load_news_source_watchlist_config(path)


def build_news_source_coverage_report(
    *,
    config_path: str | Path = "config/news_source_watchlist.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> dict[str, Any]:
    config = load_news_source_watchlist_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    news_items = store.read_news_items()
    enabled_sources = [source for source in config.news_source_watchlist if source.enabled]
    configured_groups = sorted({source.source_group for source in enabled_sources})
    required_groups = sorted(config.coverage.required_source_groups)
    item_groups = _item_groups(news_items, config)
    counts_by_group = _counts_by_group(item_groups)
    latest_counts_by_group = _latest_counts_by_group(item_groups)
    latest_dates_by_group = _latest_dates_by_group(item_groups)
    missing_configured_groups = sorted(set(required_groups) - set(configured_groups))
    missing_data_groups = sorted(group for group in configured_groups if counts_by_group.get(group, 0) == 0)
    stale_groups = _stale_groups(latest_dates_by_group, config.coverage.stale_after_days)
    overrepresented_groups = _overrepresented_groups(
        latest_counts_by_group,
        max_share=config.coverage.max_group_share,
    )
    warnings = []
    if missing_configured_groups:
        warnings.append("some required source groups have no enabled source")
    if missing_data_groups:
        warnings.append("some enabled source groups have no stored items")
    if stale_groups:
        warnings.append("some source groups have stale stored items")
    if overrepresented_groups:
        warnings.append("latest stored items are concentrated in a small number of source groups")
    payload = {
        "valid": True,
        "configured_source_count": len(config.news_source_watchlist),
        "enabled_source_count": len(enabled_sources),
        "required_source_groups": required_groups,
        "configured_groups": configured_groups,
        "enabled_sources_by_group": _enabled_sources_by_group(enabled_sources),
        "stored_item_count": int(len(news_items)),
        "item_count_by_group": counts_by_group,
        "latest_item_count_by_group": latest_counts_by_group,
        "latest_date_by_group": {
            group: value.isoformat() for group, value in sorted(latest_dates_by_group.items())
        },
        "missing_configured_groups": missing_configured_groups,
        "missing_data_groups": missing_data_groups,
        "overrepresented_groups": overrepresented_groups,
        "stale_groups": stale_groups,
        "suggested_groups_needing_more_coverage": sorted(
            set(missing_configured_groups) | set(missing_data_groups) | set(stale_groups)
        ),
        "warnings": warnings,
        "disclaimer": SOURCE_COVERAGE_DISCLAIMER,
    }
    return payload


def write_news_source_coverage_report(
    *,
    config_path: str | Path = "config/news_source_watchlist.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    config = load_news_source_watchlist_config(config_path)
    payload = build_news_source_coverage_report(config_path=config_path, db_path=db_path)
    markdown = news_source_coverage_report_markdown(payload)
    _assert_no_forbidden_language(markdown)
    output_dir = Path(config.coverage.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "news_source_coverage_report.json"
    markdown_path = output_dir / "news_source_coverage_report.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def news_source_coverage_report_markdown(payload: dict[str, Any]) -> str:
    groups = _markdown_counts(payload.get("latest_item_count_by_group", {}))
    missing = _markdown_items(payload.get("missing_configured_groups", []))
    thin = _markdown_items(payload.get("missing_data_groups", []))
    stale = _markdown_items(payload.get("stale_groups", []))
    concentrated = _markdown_group_shares(payload.get("overrepresented_groups", []))
    suggested = _markdown_items(payload.get("suggested_groups_needing_more_coverage", []))
    warnings = _markdown_items(payload.get("warnings", []))
    return f"""# News Source Coverage Report

Mode: source-group coverage and stored-news breadth diagnostic.

## Configured Sources

- Configured source count: {payload.get("configured_source_count")}
- Enabled source count: {payload.get("enabled_source_count")}
- Stored item count: {payload.get("stored_item_count")}

## Latest Items By Source Group

{groups}

## Coverage Gaps

Required groups without an enabled source:
{missing}

Enabled groups with no stored items:
{thin}

Stale groups:
{stale}

Overrepresented groups:
{concentrated}

Suggested groups needing more coverage:
{suggested}

## Warnings

{warnings}

{payload["disclaimer"]}
"""


def _item_groups(
    news_items: pd.DataFrame,
    config: NewsSourceWatchlistConfig,
) -> list[dict[str, Any]]:
    if news_items.empty:
        return []
    group_by_source = {
        source.source_name: source.source_group
        for source in config.news_source_watchlist
        if source.enabled
    }
    group_by_source.update(
        {
            source.source_id: source.source_group
            for source in config.news_source_watchlist
            if source.enabled
        }
    )
    rows = []
    for row in news_items.to_dict(orient="records"):
        metadata = _metadata(row.get("raw_metadata_json") or row.get("raw_metadata"))
        group = metadata.get("source_group") or group_by_source.get(str(row.get("source")))
        if not group:
            group = "unmapped"
        published_at = pd.to_datetime(row.get("published_at"), errors="coerce", utc=True)
        rows.append(
            {
                "source": row.get("source"),
                "source_group": group,
                "published_at": None if pd.isna(published_at) else published_at.to_pydatetime(),
            }
        )
    return rows


def _counts_by_group(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        group = str(row["source_group"])
        counts[group] = counts.get(group, 0) + 1
    return dict(sorted(counts.items()))


def _latest_counts_by_group(rows: list[dict[str, Any]]) -> dict[str, int]:
    dates = [row["published_at"] for row in rows if row.get("published_at") is not None]
    if not dates:
        return {}
    latest_date = max(value.date() for value in dates)
    latest_rows = [row for row in rows if row.get("published_at") and row["published_at"].date() == latest_date]
    return _counts_by_group(latest_rows)


def _latest_dates_by_group(rows: list[dict[str, Any]]) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for row in rows:
        published_at = row.get("published_at")
        if published_at is None:
            continue
        group = str(row["source_group"])
        if group not in latest or published_at > latest[group]:
            latest[group] = published_at
    return latest


def _enabled_sources_by_group(sources) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for source in sources:
        result.setdefault(source.source_group, []).append(source.source_id)
    return {group: sorted(ids) for group, ids in sorted(result.items())}


def _stale_groups(latest_dates: dict[str, datetime], stale_after_days: int) -> list[str]:
    if stale_after_days == 0:
        return []
    now = datetime.now(UTC)
    return sorted(
        group
        for group, latest in latest_dates.items()
        if (now - latest).days > stale_after_days
    )


def _overrepresented_groups(counts: dict[str, int], *, max_share: float) -> list[dict[str, Any]]:
    total = sum(counts.values())
    if total == 0:
        return []
    result = []
    for group, count in sorted(counts.items()):
        share = count / total
        if share > max_share:
            result.append({"source_group": group, "item_count": count, "share": share})
    return result


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None or pd.isna(value):
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _markdown_counts(items: dict[str, int]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {key}: {value}" for key, value in sorted(items.items()))


def _markdown_items(items: list[Any]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


def _markdown_group_shares(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- None"
    return "\n".join(
        f"- {item['source_group']}: {item['item_count']} items ({item['share']:.1%})"
        for item in items
    )


def _assert_no_forbidden_language(markdown: str) -> None:
    lower = markdown.lower()
    violations = [term for term in FORBIDDEN_REPORT_TERMS if term in lower]
    if violations:
        raise ValueError(f"news source coverage report contains forbidden language: {violations}")
