"""Pure (no LLM, no network) prioritization of news items for the DeepSeek
classification budget.

The classifier has a per-run cap. Picking the NEWEST N items is arbitrary - a
burst of trivial late-posted blurbs can evict the day's important macro news.
This module ranks candidates so the budget goes to the most important, broadest
set instead:

    priority = source_authority * (1 + macro_keyword_hits) * freshness

then per-group quotas guarantee breadth and a min_priority threshold drops weak
items (so quiet days cost less). All scoring is deterministic and offline.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from macro_engine.news.config import NewsSelectionConfig, load_news_sources_config

# Mirrors ingest._likely_non_news markers (kept local to avoid building NewsItem).
_NON_NEWS_MARKERS = ["stock price", "quote", "historical data", "login", "subscribe to continue"]
_DEFAULT_GROUP_WEIGHT = 0.5
_UNMAPPED = "unmapped"


def load_macro_keyword_lexicon(
    sources_config_path: str | Path = "config/news_sources.yaml",
) -> set[str]:
    """Collect macro keywords from the existing source_group_rules (single source
    of truth) for salience scoring."""
    config = load_news_sources_config(sources_config_path)
    lexicon: set[str] = set()
    for rule in config.source_group_rules:
        for kw in (*rule.title_keywords, *rule.body_keywords):
            cleaned = kw.strip().lower()
            if cleaned:
                lexicon.add(cleaned)
    return lexicon


def _source_group(raw_metadata_json: object) -> str:
    if not isinstance(raw_metadata_json, str) or not raw_metadata_json:
        return _UNMAPPED
    try:
        meta = json.loads(raw_metadata_json)
    except (ValueError, TypeError):
        return _UNMAPPED
    group = meta.get("source_group") if isinstance(meta, dict) else None
    return str(group) if group else _UNMAPPED


def _freshness(age_days: float, *, half_life_days: float, max_age_days: int) -> float:
    if age_days < 0:
        return 0.0
    if age_days > max_age_days:
        return 0.0
    return float(0.5 ** (age_days / half_life_days))


def _keyword_hits(text: str, lexicon: set[str]) -> int:
    return sum(1 for kw in lexicon if kw in text)


def _is_short_body(body: str, min_words: int) -> bool:
    return len(str(body).split()) < min_words


def _is_likely_non_news(text: str) -> bool:
    return any(marker in text for marker in _NON_NEWS_MARKERS)


def score_items(
    items: pd.DataFrame,
    *,
    config: NewsSelectionConfig,
    lexicon: set[str],
    now: datetime | None = None,
) -> pd.DataFrame:
    """Return items with added columns: source_group, selection_score, and a
    boolean `eligible` (passed the quality gate + min_priority threshold)."""
    if items.empty:
        result = items.copy()
        for col in ("source_group", "selection_score", "eligible"):
            result[col] = pd.Series(dtype="object")
        return result

    now = now or datetime.now(UTC)
    df = items.copy().reset_index(drop=True)
    published = pd.to_datetime(df.get("published_at"), errors="coerce", utc=True)

    scores: list[float] = []
    groups: list[str] = []
    eligible: list[bool] = []
    for i in range(len(df)):
        row = df.iloc[i]
        title = str(row.get("title") or "")
        body = str(row.get("body") or "")
        text = f"{title} {body}".lower()
        group = _source_group(row.get("raw_metadata_json"))
        groups.append(group)

        # Quality gate
        bad = False
        if config.drop_likely_non_news and _is_likely_non_news(text):
            bad = True
        if _is_short_body(body, config.min_body_words):
            bad = True

        ts = published.iloc[i]
        if pd.isna(ts):
            age_days = float(config.max_age_days)  # unknown date: treat as old, not future
        else:
            age_days = max(0.0, (now - ts.to_pydatetime()).total_seconds() / 86400.0)
        fresh = _freshness(
            age_days, half_life_days=config.half_life_days, max_age_days=config.max_age_days
        )
        authority = config.source_authority.get(str(row.get("source") or ""), 1.0)
        salience = 1 + _keyword_hits(text, lexicon)
        priority = 0.0 if bad else authority * salience * fresh
        scores.append(priority)
        eligible.append((not bad) and priority >= config.min_priority)

    df["source_group"] = groups
    df["selection_score"] = scores
    df["eligible"] = eligible
    return df


def select_within_budget(scored: pd.DataFrame, *, config: NewsSelectionConfig) -> pd.DataFrame:
    """Apply per-group quotas + global fill, capped at daily_cap. Input must have
    `source_group`, `selection_score`, `eligible` columns (from score_items)."""
    pool = scored[scored["eligible"]].copy()
    if pool.empty:
        return pool
    pool = pool.sort_values(
        ["selection_score", "published_at"], ascending=[False, False], na_position="last"
    )
    cap = config.daily_cap
    present = list(pool["source_group"].unique())
    weights = {g: config.group_quota_weights.get(g, _DEFAULT_GROUP_WEIGHT) for g in present}
    total_w = sum(weights.values()) or 1.0

    selected_ids: list[str] = []
    for group, weight in weights.items():
        quota = int(math.floor(cap * weight / total_w))
        if quota <= 0:
            continue
        group_rows = pool[pool["source_group"] == group].head(quota)
        selected_ids.extend(group_rows["news_id"].tolist())

    # Fill any leftover budget by global priority (quota flooring leaves slack).
    if len(selected_ids) < cap:
        remaining = pool[~pool["news_id"].isin(selected_ids)].head(cap - len(selected_ids))
        selected_ids.extend(remaining["news_id"].tolist())

    selected = pool[pool["news_id"].isin(selected_ids)].head(cap)
    return selected.sort_values(
        ["selection_score", "published_at"], ascending=[False, False], na_position="last"
    ).reset_index(drop=True)


def rank_and_select(
    items: pd.DataFrame,
    *,
    config: NewsSelectionConfig,
    sources_config_path: str | Path = "config/news_sources.yaml",
    now: datetime | None = None,
) -> pd.DataFrame:
    """End-to-end: score then select within budget. Returns the chosen rows
    (original columns plus source_group/selection_score), capped at daily_cap."""
    lexicon = load_macro_keyword_lexicon(sources_config_path)
    scored = score_items(items, config=config, lexicon=lexicon, now=now)
    return select_within_budget(scored, config=config)
