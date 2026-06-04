"""Historical news backfill via the GDELT DOC 2.0 API (free, no key).

Purpose: bootstrap the confidence-calibration ledger with point-in-time-dated
articles so calibration buckets fill faster than waiting for live accumulation.

IMPORTANT VALIDITY CAVEAT
-------------------------
GDELT supplies the article date + headline, which fixes data point-in-time. It
does NOT freeze the *classifier's* knowledge: classifying an old article with
today's LLM can leak hindsight (the model may already "know" the outcome). Any
calibration fit on backfilled data is therefore PROVISIONAL and biased
optimistic - useful as a pipeline dry-run, not a trustworthy live transform.
Calibration must be re-validated on live, forward-only calls before promotion.

GDELT also returns only title + metadata (no body); the headline is used as the
body. This makes backfill classification weaker than live (which has bodies) -
another reason the result is provisional.

Network is injected (`fetch`) so the parsing/conversion logic is unit-tested
offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
import yaml
from pydantic import BaseModel, Field

from macro_engine.news.config import REQUIRED_NEWS_SOURCE_GROUPS
from macro_engine.news.ingest import content_hash_for_news
from macro_engine.news.schema import NewsItem

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# A fetch takes a fully-formed URL and returns the raw response text.
FetchFn = Callable[[str], str]


class GdeltQuery(BaseModel):
    source_group: str
    query: str
    source: str = "gdelt"

    def model_post_init(self, _ctx) -> None:  # pydantic v2 hook
        if self.source_group not in REQUIRED_NEWS_SOURCE_GROUPS:
            raise ValueError(f"unknown source_group {self.source_group}")


class GdeltBackfillConfig(BaseModel):
    start_date: str
    end_date: str
    window_days: int = Field(default=7, ge=1)
    max_records_per_window: int = Field(default=250, ge=1, le=250)
    # GDELT throttles aggressively; pause between requests + back off on 429.
    request_delay_seconds: float = Field(default=2.0, ge=0.0)
    max_retries_429: int = Field(default=4, ge=0)
    queries: list[GdeltQuery]


@dataclass
class GdeltBackfillResult:
    requested_windows: int
    fetched_articles: int
    news_items: list[NewsItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def load_gdelt_backfill_config(
    path: str | Path = "config/gdelt_backfill.yaml",
) -> GdeltBackfillConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    payload = data.get("gdelt_backfill", data)
    return GdeltBackfillConfig.model_validate(payload)


# ---- pure parsing / conversion --------------------------------------------


def build_doc_url(query: str, start: datetime, end: datetime, *, max_records: int) -> str:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": max_records,
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        "sort": "DateAsc",
    }
    return f"{GDELT_DOC_URL}?{urlencode(params)}"


def parse_gdelt_articles(raw_text: str) -> list[dict[str, Any]]:
    """Parse a GDELT ArtList JSON response into article dicts. GDELT sometimes
    returns empty/non-JSON bodies on no-match; those yield an empty list."""
    text = (raw_text or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return []
    articles = payload.get("articles") if isinstance(payload, dict) else None
    return articles if isinstance(articles, list) else []


def _parse_seendate(value: Any) -> datetime | None:
    # GDELT seendate like "20240115T143000Z"
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def gdelt_articles_to_news_items(
    articles: list[dict[str, Any]],
    *,
    source_group: str,
    source: str = "gdelt",
) -> list[NewsItem]:
    """Convert GDELT articles to NewsItem rows. No body is available, so the
    headline is used as the body (flagged in raw_metadata)."""
    items: list[NewsItem] = []
    for art in articles:
        title = str(art.get("title") or "").strip()
        if not title:
            continue
        body = title  # GDELT has no article body; headline stands in.
        published_at = _parse_seendate(art.get("seendate"))
        content_hash = content_hash_for_news(
            title=title,
            body=body,
            source=source,
            published_at=None if published_at is None else published_at.isoformat(),
        )
        items.append(
            NewsItem(
                news_id=f"news_{content_hash[:16]}",
                source=source,
                source_url=art.get("url"),
                title=title,
                body=body,
                published_at=published_at,
                ingested_at=datetime.now(UTC),
                provider="gdelt",
                raw_metadata={
                    "source_group": source_group,
                    "domain": art.get("domain"),
                    "language": art.get("language"),
                    "sourcecountry": art.get("sourcecountry"),
                    "body_is_headline": True,
                    "backfill": True,
                },
                content_hash=content_hash,
            )
        )
    return items


def _date_windows(start: datetime, end: datetime, window_days: int) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    step = timedelta(days=window_days)
    while cursor < end:
        windows.append((cursor, min(cursor + step, end)))
        cursor += step
    return windows


# ---- network-backed fetch (injectable) ------------------------------------


def make_default_fetch(max_retries_429: int = 4) -> FetchFn:
    """Network fetch with exponential backoff on HTTP 429 (GDELT throttling)."""

    def _fetch(url: str) -> str:
        request = Request(
            url,
            headers={"User-Agent": "macro-engine-gdelt-backfill/1.0 (+local diagnostics)"},
        )
        attempt = 0
        while True:
            try:
                with urlopen(request, timeout=30) as response:
                    return response.read().decode("utf-8", errors="replace")
            except HTTPError as exc:
                if exc.code == 429 and attempt < max_retries_429:
                    time.sleep(2.0 * (2**attempt))  # 2,4,8,16s
                    attempt += 1
                    continue
                raise

    return _fetch


def _default_fetch(url: str) -> str:
    return make_default_fetch()(url)


def fetch_gdelt_news_items(
    config: GdeltBackfillConfig,
    *,
    fetch: FetchFn | None = None,
    progress: Callable[[str], None] | None = None,
    on_batch: Callable[[list[NewsItem]], None] | None = None,
) -> GdeltBackfillResult:
    """Iterate (date window x topic query), fetch GDELT, convert to NewsItems,
    dedupe by content_hash. Network injected for testing. A polite delay runs
    between live requests; injected fetches in tests set delay 0 to stay fast.

    on_batch (optional) receives each query's newly-unique items immediately so
    a caller can persist incrementally - so a long run killed mid-way keeps the
    data already fetched."""
    fetch = fetch or make_default_fetch(config.max_retries_429)
    start = pd.Timestamp(config.start_date, tz="UTC").to_pydatetime()
    end = pd.Timestamp(config.end_date, tz="UTC").to_pydatetime()
    windows = _date_windows(start, end, config.window_days)
    total = len(windows) * len(config.queries)

    seen: set[str] = set()
    items: list[NewsItem] = []
    errors: list[str] = []
    fetched = 0
    done = 0
    for win_start, win_end in windows:
        for q in config.queries:
            done += 1
            url = build_doc_url(
                q.query, win_start, win_end, max_records=config.max_records_per_window
            )
            batch: list[NewsItem] = []
            try:
                raw = fetch(url)
            except Exception as exc:  # noqa: BLE001 - one bad window must not abort backfill
                errors.append(f"{q.source_group} {win_start.date()}..{win_end.date()}: {exc}")
            else:
                articles = parse_gdelt_articles(raw)
                fetched += len(articles)
                for item in gdelt_articles_to_news_items(
                    articles, source_group=q.source_group, source=q.source
                ):
                    if item.content_hash in seen:
                        continue
                    seen.add(item.content_hash)
                    items.append(item)
                    batch.append(item)
                if batch and on_batch is not None:
                    on_batch(batch)
            if progress is not None:
                progress(
                    f"[{done}/{total}] {q.source_group} "
                    f"{win_start.date()}..{win_end.date()} "
                    f"fetched={fetched} unique={len(items)} errors={len(errors)}"
                )
            if config.request_delay_seconds > 0:
                time.sleep(config.request_delay_seconds)
    return GdeltBackfillResult(
        requested_windows=total,
        fetched_articles=fetched,
        news_items=items,
        errors=errors,
    )


def news_items_to_frame(items: list[NewsItem]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "news_id": it.news_id,
                "source": it.source,
                "source_url": it.source_url,
                "title": it.title,
                "body": it.body,
                "published_at": it.published_at,
                "ingested_at": it.ingested_at,
                "provider": it.provider,
                "raw_metadata": it.raw_metadata,
                "content_hash": it.content_hash,
            }
            for it in items
        ]
    )


# ---- service ---------------------------------------------------------------


def backfill_gdelt_news(
    *,
    config_path: str | Path = "config/gdelt_backfill.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    fetch: FetchFn | None = None,
) -> dict[str, Any]:
    """Fetch historical GDELT articles and upsert them as news_items. Does NOT
    classify - run classify-news next, then run-news-confidence-calibration
    with --date-basis published_at (provisional)."""
    from macro_engine.storage.duckdb_store import DuckDBStore

    import sys

    config = load_gdelt_backfill_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()

    # Persist each query batch as it arrives so a killed run keeps its data.
    inserted = 0

    def _persist(batch: list[NewsItem]) -> None:
        nonlocal inserted
        store.upsert_news_items(news_items_to_frame(batch))
        inserted += len(batch)

    result = fetch_gdelt_news_items(
        config,
        fetch=fetch,
        progress=lambda msg: print(msg, file=sys.stderr, flush=True),
        on_batch=_persist,
    )
    return {
        "requested_windows": result.requested_windows,
        "fetched_articles": result.fetched_articles,
        "upserted_news_items": inserted,
        "errors": result.errors,
        "note": (
            "Backfill complete. Classification is NOT point-in-time for the LLM "
            "(hindsight leakage possible); resulting calibration is PROVISIONAL."
        ),
    }
