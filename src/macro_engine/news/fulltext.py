"""Full-article-text enrichment for thin news items.

RSS descriptions and GDELT records often carry only a headline or a short
snippet, which starves the AI classifier of context. This step fetches the
article URL for items whose body is short and replaces the body with the
main article text extracted by trafilatura.

Design constraints:
- The item's news_id / content_hash are computed from the original snippet
  BEFORE enrichment, so re-ingesting the same feed entry later still dedupes
  against the stored (enriched) copy.
- Network is injected (`fetch`) so extraction and gating logic are
  unit-tested offline.
- Failures never drop an item: on any fetch/extraction problem the original
  snippet body is kept.
"""

from __future__ import annotations

import sys
from typing import Callable
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from macro_engine.news.schema import NewsItem

# Takes a URL, returns raw HTML text.
FetchFn = Callable[[str], str]

_USER_AGENT = "macro-engine-news-pilot/1.0 (+local diagnostics)"


class FulltextEnrichmentConfig(BaseModel):
    enabled: bool = False
    # Items with a body at least this long are considered rich enough already.
    min_body_chars: int = Field(default=400, ge=0)
    # Per-run cap so a large backlog cannot stall the daily run.
    max_items_per_run: int = Field(default=40, ge=1)
    # Keep stored bodies bounded; the classifier truncates further anyway.
    max_body_chars: int = Field(default=8000, ge=500)
    request_timeout_seconds: int = Field(default=12, ge=1)


def default_fetch(timeout_seconds: int) -> FetchFn:
    def fetch(url: str) -> str:
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")

    return fetch


def extract_article_text(html: str) -> str | None:
    """Extract the main article body from raw HTML. None when extraction fails."""
    try:
        import trafilatura
    except ImportError:  # pragma: no cover - dependency is declared in pyproject
        return None
    try:
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
    except Exception:  # noqa: BLE001 - malformed pages must not break ingestion
        return None
    if not extracted:
        return None
    text = extracted.strip()
    return text or None


def enrich_items_with_fulltext(
    items: list[NewsItem],
    config: FulltextEnrichmentConfig,
    fetch: FetchFn | None = None,
) -> list[NewsItem]:
    """Return items with thin bodies upgraded to full article text where possible."""
    if not config.enabled or not items:
        return items
    fetch = fetch or default_fetch(config.request_timeout_seconds)
    enriched: list[NewsItem] = []
    attempts = 0
    upgraded = 0
    for item in items:
        if attempts >= config.max_items_per_run or not _needs_enrichment(item, config):
            enriched.append(item)
            continue
        attempts += 1
        body = _fetch_body(item, fetch)
        if body is None or len(body) <= len(item.body):
            enriched.append(item)
            continue
        upgraded += 1
        metadata = dict(item.raw_metadata)
        metadata["fulltext_enriched"] = True
        metadata["original_body_chars"] = len(item.body)
        enriched.append(
            item.model_copy(
                update={
                    "body": body[: config.max_body_chars],
                    "raw_metadata": metadata,
                }
            )
        )
    if attempts:
        print(
            f"fulltext enrichment: upgraded {upgraded}/{attempts} thin item(s)",
            file=sys.stderr,
        )
    return enriched


def _needs_enrichment(item: NewsItem, config: FulltextEnrichmentConfig) -> bool:
    if not item.source_url:
        return False
    return len(item.body.strip()) < config.min_body_chars


def _fetch_body(item: NewsItem, fetch: FetchFn) -> str | None:
    try:
        html = fetch(str(item.source_url))
    except Exception:  # noqa: BLE001 - a dead link must not break ingestion
        return None
    return extract_article_text(html)
