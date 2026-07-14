"""Tests for the GDELT / Finnhub news providers and full-text enrichment."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from macro_engine.news.config import NewsSourceDefinition, load_news_sources_config
from macro_engine.news.fulltext import (
    FulltextEnrichmentConfig,
    enrich_items_with_fulltext,
    extract_article_text,
)
from macro_engine.news.ingest import (
    load_finnhub_source,
    load_gdelt_source,
)

ARTICLE_HTML = """
<html><head><title>t</title></head><body>
<nav>Home | Markets | Subscribe</nav>
<article>
<h1>Factory orders slump for third month</h1>
<p>New orders for manufactured goods fell 2.1 percent in June, the third
consecutive monthly decline, as higher borrowing costs weighed on capital
spending plans across the industrial sector.</p>
<p>Economists said the slowdown was broad-based, with machinery, primary
metals, and transportation equipment all posting weaker demand. Several
manufacturers reported delaying expansion projects until financing costs
ease.</p>
</article>
<footer>Copyright</footer>
</body></html>
"""


def _gdelt_source(**overrides) -> NewsSourceDefinition:
    values = {
        "source_id": "gdelt_test",
        "provider": "gdelt",
        "query": "manufacturing sourcelang:english",
        "source_group": "manufacturing_industrials",
        "max_items": 5,
        "timespan_hours": 36,
    }
    values.update(overrides)
    return NewsSourceDefinition.model_validate(values)


def _finnhub_source(**overrides) -> NewsSourceDefinition:
    values = {
        "source_id": "finnhub_test",
        "provider": "finnhub",
        "category": "general",
        "source_group": "macro_general",
        "max_items": 10,
        "lookback_days": 0,
    }
    values.update(overrides)
    return NewsSourceDefinition.model_validate(values)


def test_gdelt_source_maps_articles_to_news_items():
    payload = {
        "articles": [
            {
                "title": "Factory orders slump for third month",
                "url": "https://example.com/factory-orders",
                "domain": "example.com",
                "seendate": "20260714T153000Z",
                "language": "English",
            },
            {"title": "", "url": "https://example.com/empty"},
        ]
    }
    items = load_gdelt_source(_gdelt_source(), fetch=lambda url: json.dumps(payload))
    assert len(items) == 1
    item = items[0]
    assert item.provider == "gdelt"
    assert item.title == "Factory orders slump for third month"
    assert item.body == item.title
    assert str(item.source_url) == "https://example.com/factory-orders"
    assert item.published_at is not None
    assert item.published_at.year == 2026
    assert item.raw_metadata.get("source_group") == "manufacturing_industrials"


def test_gdelt_source_rejects_non_json():
    with pytest.raises(ValueError, match="non-JSON"):
        load_gdelt_source(_gdelt_source(), fetch=lambda url: "<html>rate limited</html>")


def test_gdelt_query_is_sent_encoded():
    seen: dict[str, str] = {}

    def fetch(url: str) -> str:
        seen["url"] = url
        return json.dumps({"articles": []})

    load_gdelt_source(_gdelt_source(), fetch=fetch)
    assert "mode=ArtList" in seen["url"]
    assert "timespan=36h" in seen["url"]


def test_finnhub_source_requires_api_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    with pytest.raises(ValueError, match="FINNHUB_API_KEY"):
        load_finnhub_source(_finnhub_source(), fetch=lambda url: "[]")


def test_finnhub_source_maps_entries(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    payload = [
        {
            "headline": "Stocks rally as yields ease",
            "summary": "Equities rose across sectors while Treasury yields fell.",
            "source": "SomeWire",
            "url": "https://example.com/rally",
            "datetime": int(datetime(2026, 7, 14, 12, 0, tzinfo=UTC).timestamp()),
        },
        {"headline": "", "summary": "ignored"},
    ]
    items = load_finnhub_source(_finnhub_source(), fetch=lambda url: json.dumps(payload))
    assert len(items) == 1
    item = items[0]
    assert item.provider == "finnhub"
    assert item.source == "SomeWire"
    assert item.body.startswith("Equities rose")
    assert item.raw_metadata.get("source_group") == "macro_general"


def test_finnhub_error_does_not_leak_token(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "supersecrettoken")

    def failing_fetch(url: str) -> str:
        raise RuntimeError(f"boom for {url}")

    with pytest.raises(ValueError) as excinfo:
        load_finnhub_source(_finnhub_source(), fetch=failing_fetch)
    assert "supersecrettoken" not in str(excinfo.value)


def test_extract_article_text_pulls_main_body():
    text = extract_article_text(ARTICLE_HTML)
    assert text is not None
    assert "New orders for manufactured goods" in text
    assert "Subscribe" not in text


def test_enrichment_upgrades_thin_bodies_and_keeps_ids():
    items = load_gdelt_source(
        _gdelt_source(),
        fetch=lambda url: json.dumps(
            {
                "articles": [
                    {
                        "title": "Factory orders slump for third month",
                        "url": "https://example.com/factory-orders",
                        "domain": "example.com",
                        "seendate": "20260714T153000Z",
                    }
                ]
            }
        ),
    )
    original_id = items[0].news_id
    config = FulltextEnrichmentConfig(enabled=True, min_body_chars=400, max_items_per_run=5)
    enriched = enrich_items_with_fulltext(items, config, fetch=lambda url: ARTICLE_HTML)
    assert len(enriched) == 1
    assert enriched[0].news_id == original_id
    assert "New orders for manufactured goods" in enriched[0].body
    assert enriched[0].raw_metadata.get("fulltext_enriched") is True


def test_enrichment_keeps_original_body_on_fetch_failure():
    items = load_gdelt_source(
        _gdelt_source(),
        fetch=lambda url: json.dumps(
            {
                "articles": [
                    {
                        "title": "Factory orders slump",
                        "url": "https://example.com/dead-link",
                        "domain": "example.com",
                    }
                ]
            }
        ),
    )

    def failing_fetch(url: str) -> str:
        raise RuntimeError("dead link")

    config = FulltextEnrichmentConfig(enabled=True, min_body_chars=400)
    enriched = enrich_items_with_fulltext(items, config, fetch=failing_fetch)
    assert enriched[0].body == "Factory orders slump"
    assert "fulltext_enriched" not in enriched[0].raw_metadata


def test_enrichment_skips_rich_bodies_and_respects_cap():
    calls: list[str] = []

    def counting_fetch(url: str) -> str:
        calls.append(url)
        return ARTICLE_HTML

    items = load_gdelt_source(
        _gdelt_source(max_items=5),
        fetch=lambda url: json.dumps(
            {
                "articles": [
                    {"title": f"Headline {index}", "url": f"https://example.com/{index}"}
                    for index in range(4)
                ]
            }
        ),
    )
    config = FulltextEnrichmentConfig(enabled=True, min_body_chars=400, max_items_per_run=2)
    enriched = enrich_items_with_fulltext(items, config, fetch=counting_fetch)
    assert len(calls) == 2, "per-run cap must bound network fetches"
    assert len(enriched) == 4


def test_enrichment_disabled_is_identity():
    items = load_gdelt_source(
        _gdelt_source(),
        fetch=lambda url: json.dumps(
            {"articles": [{"title": "Headline", "url": "https://example.com/x"}]}
        ),
    )
    config = FulltextEnrichmentConfig(enabled=False)
    assert enrich_items_with_fulltext(items, config, fetch=lambda url: ARTICLE_HTML) == items


def test_production_config_declares_new_sources_and_enrichment():
    config = load_news_sources_config("config/news_sources.yaml")
    providers = {source.source_id: source.provider for source in config.news_sources}
    assert providers.get("finnhub_market_news") == "finnhub"
    gdelt_groups = {
        source.source_group
        for source in config.news_sources
        if source.provider == "gdelt" and "live_rss" in source.profiles
    }
    assert {
        "healthcare",
        "technology_ai",
        "consumer",
        "defensive_sectors",
        "manufacturing_industrials",
    } <= gdelt_groups
    assert config.fulltext_enrichment.enabled is True
    assert config.fulltext_enrichment.max_items_per_run >= 1
