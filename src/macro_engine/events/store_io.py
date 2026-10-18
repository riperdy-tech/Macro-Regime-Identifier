"""Read the E1 pool and its classifications from the news store (read-only)."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from macro_engine.events.builder import Classification, NewsItem, naive_utc


def _json_list(raw: Any) -> tuple[tuple[dict[str, Any], ...], bool]:
    """(rows, parse_error). An empty or null column is an empty list, not an error."""
    if raw is None or raw == "":
        return (), False
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return (), True
    if not isinstance(data, list):
        return (), True
    return tuple(x for x in data if isinstance(x, dict)), False


def parse_classification_row(row: dict[str, Any]) -> Classification:
    themes, e1 = _json_list(row.get("macro_themes_json"))
    sectors, e2 = _json_list(row.get("sector_impacts_json"))
    entities, e3 = _json_list(row.get("entities_json"))
    return Classification(
        classified_at=naive_utc(row.get("classified_at")),
        status=row.get("classification_status"),
        origin=row.get("origin"),
        ai_provider=row.get("ai_provider"),
        themes=themes,
        entities=entities,
        sector_impacts=sectors,
        severity=row.get("severity"),
        confidence=row.get("confidence"),
        summary=row.get("summary"),
        parse_error=e1 or e2 or e3,
    )


def load_pool(con: Any, as_of: Any, window_days: float) -> list[NewsItem]:
    """Items first seen in (as_of - window, as_of], each with the classifications made at or before `as_of`."""
    end = naive_utc(as_of)
    assert end is not None
    start = end - timedelta(days=window_days)
    items = con.execute(
        "select news_id, title, source_url, published_at, first_seen_at from news_items "
        "where first_seen_at > ? and first_seen_at <= ? order by first_seen_at, news_id",
        [start, end],
    ).fetchall()
    ids = [r[0] for r in items]
    by_id: dict[str, list[Classification]] = {i: [] for i in ids}
    if ids:
        cols = ["news_id", "classified_at", "classification_status", "origin", "ai_provider", "macro_themes_json",
                "sector_impacts_json", "entities_json", "severity", "confidence", "summary"]
        marks = ",".join("?" for _ in ids)
        rows = con.execute(
            f"select {', '.join(cols)} from news_classifications "
            f"where news_id in ({marks}) and classified_at <= ?",
            [*ids, end],
        ).fetchall()
        for r in rows:
            rec = dict(zip(cols, r, strict=True))
            by_id[rec["news_id"]].append(parse_classification_row(rec))
    return [
        NewsItem(news_id=i, title=t, source_url=u, published_at=naive_utc(p), first_seen_at=naive_utc(f),
                 classifications=tuple(by_id[i]))
        for i, t, u, p, f in items
    ]
