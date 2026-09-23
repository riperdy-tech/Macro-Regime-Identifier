from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import json
import sys
import time
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from macro_engine.news.classify import (
    MockNewsClassifier,
    classify_news_item,
    should_use_mock_classifier,
)
from macro_engine.news.config import (
    load_news_ai_config,
    load_news_selection_config,
    load_news_themes_config,
)
from macro_engine.news.config import load_news_sources_config
from macro_engine.news.fulltext import enrich_items_with_fulltext
from macro_engine.news.ingest import SourceResult, load_news_items_with_report
from macro_engine.news.selection import rank_and_select
from macro_engine.news.providers.openai_classifier import DeepSeekNewsClassifier
from macro_engine.storage.duckdb_store import DuckDBStore

# Don't enforce the failure-rate guard until enough items are attempted; a
# couple of early transient errors should not abort the whole run.
_MIN_ATTEMPTS_BEFORE_RATE_GUARD = 10


def ingest_stored_news(
    *,
    config_path: str | Path = "config/news_sources.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    profile: str | None = None,
    run_id: str | None = None,
) -> pd.DataFrame:
    store = DuckDBStore(db_path)
    store.initialize()
    stored_items = store.read_news_items()
    stored_ids = (
        set(stored_items["news_id"].dropna().astype(str))
        if not stored_items.empty
        else set()
    )
    items, source_results = load_news_items_with_report(config_path, profile=profile)
    sources_config = load_news_sources_config(config_path)
    new_items = [item for item in items if str(item.news_id) not in stored_ids]
    enriched_new_items = enrich_items_with_fulltext(
        new_items, sources_config.fulltext_enrichment
    )
    enriched_by_id = {item.news_id: item for item in enriched_new_items}
    final_items = [enriched_by_id.get(item.news_id, item) for item in items]
    frame = pd.DataFrame([item.model_dump() for item in final_items])
    store.merge_news_items(frame)

    # N1.5: one news_source_runs row per source this run, so a cold cache
    # never loses what happened -- health (N1.6) and accumulation read this,
    # not the ephemeral SourceResult list.
    run_at = datetime.now(UTC)
    resolved_run_id = run_id or f"{run_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    _record_source_runs(
        store,
        final_items,
        source_results,
        stored_ids,
        run_id=resolved_run_id,
        run_at=run_at,
    )
    return frame


def _record_source_runs(
    store: DuckDBStore,
    final_items: list[Any],
    source_results: list[SourceResult],
    stored_ids: set[str],
    *,
    run_id: str,
    run_at: datetime,
) -> None:
    items_new_by_source: dict[str, int] = {}
    for item in final_items:
        if str(item.news_id) in stored_ids:
            continue
        source_id = item.raw_metadata.get("source_id") or item.source
        items_new_by_source[source_id] = items_new_by_source.get(source_id, 0) + 1

    if not source_results:
        return
    rows = [
        {
            "run_id": run_id,
            "run_at": run_at,
            "source_id": result.source_id,
            "provider": result.provider,
            "source_group": result.source_group,
            "status": result.status,
            "items_fetched": result.items_fetched,
            "items_new": items_new_by_source.get(result.source_id, 0),
            "newest_published_at": result.newest_published_at,
            "undated_count": result.undated_count,
            "error": result.error,
            "elapsed_seconds": result.elapsed_seconds,
        }
        for result in source_results
    ]
    store.insert_news_source_runs(pd.DataFrame(rows))


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
    selection_config_path: str | Path | None = None,
    sources_config_path: str | Path = "config/news_sources.yaml",
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    ai_config = load_news_ai_config(ai_config_path)
    themes = load_news_themes_config(themes_config_path)
    use_mock = should_use_mock_classifier(ai_config)
    classifier = MockNewsClassifier() if use_mock else DeepSeekNewsClassifier(ai_config)
    store = DuckDBStore(db_path)
    store.initialize()
    news_items = store.read_news_items()
    existing = store.read_table("news_classifications")
    if not existing.empty:
        all_ids = set(existing["news_id"].dropna().astype(str))
        real_ids = set(
            existing.loc[existing["ai_provider"] != "mock", "news_id"].dropna().astype(str)
        )
        if use_mock:
            news_items = news_items[~news_items["news_id"].astype(str).isin(all_ids)].copy()
        elif only_unclassified:
            news_items = news_items[~news_items["news_id"].astype(str).isin(all_ids)].copy()
        else:
            news_items = news_items[~news_items["news_id"].astype(str).isin(real_ids)].copy()
    if limit is not None:
        if selection_config_path is not None and not news_items.empty:
            # Importance-ranked selection within the budget (pure, no LLM).
            # N1.7: live_ai_safety.max_items_per_run (this `limit`) is the
            # single spend cap -- news_selection.daily_cap was removed because
            # it never bound anything tighter.
            sel_cfg = load_news_selection_config(selection_config_path)
            news_items = rank_and_select(
                news_items, config=sel_cfg, cap=limit, sources_config_path=sources_config_path
            )
        else:
            news_items = news_items.sort_values("published_at", na_position="last").tail(limit)
    records = []
    rows = news_items.to_dict(orient="records")
    total = len(rows)
    failure_count = 0
    inserted_count = 0
    upgraded_count = 0
    skipped_protected_count = 0
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
    deadline_hit = False
    for index, row in enumerate(rows, start=1):
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            deadline_hit = True
            done = len(records)
            print(f"classify-news: deadline reached after {done}/{total}", flush=True)
            break
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
        write_res = store.write_news_classifications(
            pd.DataFrame([record.model_dump()]),
            _theme_scores_from_classifications([record]),
            _sector_impacts_from_classifications([record]),
            origin="mock" if use_mock else "live",
        )
        inserted_count += write_res.get("inserted", 0)
        upgraded_count += write_res.get("upgraded", 0)
        skipped_protected_count += write_res.get("skipped_protected", 0)
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
            and attempted >= _MIN_ATTEMPTS_BEFORE_RATE_GUARD
            and failure_count / attempted > stop_on_failure_rate_above
        ):
            raise ValueError(
                "classification failure rate exceeded threshold: "
                f"{failure_count}/{attempted}"
            )
    classifications = store.read_table("news_classifications")
    theme_scores = store.read_table("news_theme_scores")
    sector_impacts = store.read_table("news_sector_impacts")
    return {
        "classifications": classifications,
        "theme_scores": theme_scores,
        "sector_impacts": sector_impacts,
        "selected_count": total,
        "completed_count": len(records),
        "failed_count": failure_count,
        "deadline_hit": deadline_hit,
        "inserted": inserted_count,
        "upgraded": upgraded_count,
        "skipped_protected": skipped_protected_count,
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
