"""WS-C: dynamic secular theme discovery and promotion.

Three additive stages, none of which touch the existing 9 secular themes:

1. select_discovery_candidates  - pick recent news that the fixed taxonomy
   could not place (secular_theme IS NULL) or placed with low confidence.
2. parse_candidate_response     - parse a DeepSeek proposal batch into
   structured candidates, then summarise their support from the SOURCE news
   (counts come from our data, never from the model).
3. evaluate_promotion           - pure-Python anti-noise gate.
   promote_candidates           - register survivors into news_themes.yaml
                                   (additive; reviewable in git diff).

The DeepSeek call itself lives in scripts/discover_themes.py and is dry-run by
default + hard --max-calls capped. This module has no network dependency so it
is fully unit-testable offline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import yaml

# Promotion thresholds are HAND-SET anti-noise priors, not measured values.
# A candidate theme only registers if it clears all of them.
DEFAULT_MIN_DISTINCT_ARTICLES = 8
DEFAULT_MIN_DAYS_OBSERVED = 5
DEFAULT_MIN_SOURCE_DIVERSITY = 3

_THEME_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")


@dataclass(frozen=True)
class PromotionThresholds:
    """Anti-noise gate thresholds (priors, not measured)."""

    min_distinct_articles: int = DEFAULT_MIN_DISTINCT_ARTICLES
    min_days_observed: int = DEFAULT_MIN_DAYS_OBSERVED
    min_source_diversity: int = DEFAULT_MIN_SOURCE_DIVERSITY


@dataclass
class ThemeCandidate:
    theme_id: str
    label: str
    description: str
    # Support metrics computed from OUR news data, not the model's claims.
    distinct_articles: int = 0
    days_observed: int = 0
    source_diversity: int = 0
    supporting_news_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme_id": self.theme_id,
            "label": self.label,
            "description": self.description,
            "distinct_articles": self.distinct_articles,
            "days_observed": self.days_observed,
            "source_diversity": self.source_diversity,
            "supporting_news_ids": list(self.supporting_news_ids),
        }


def select_discovery_candidates(
    news_items: pd.DataFrame,
    classifications: pd.DataFrame,
    *,
    max_confidence: float = 0.35,
    max_items: int = 200,
) -> pd.DataFrame:
    """Return recent news the fixed taxonomy failed to place.

    A row qualifies if its latest classification has secular_theme IS NULL OR
    overall confidence <= max_confidence. Returns news_items columns plus the
    classification confidence, newest first, capped at max_items.
    """
    if news_items.empty:
        return news_items.copy()

    items = news_items.copy()
    if classifications is None or classifications.empty:
        items["classification_confidence"] = pd.NA
        items["secular_theme"] = pd.NA
        return _cap_recent(items, max_items)

    cls = classifications.copy()
    if "classified_at" in cls.columns:
        cls = cls.sort_values("classified_at").groupby("news_id", as_index=False).tail(1)
    keep_cols = [c for c in ["news_id", "secular_theme", "confidence"] if c in cls.columns]
    cls = cls[keep_cols].rename(columns={"confidence": "classification_confidence"})

    merged = items.merge(cls, on="news_id", how="left")
    secular = merged.get("secular_theme")
    conf = merged.get("classification_confidence")
    unplaced = secular.isna() if secular is not None else pd.Series(True, index=merged.index)
    low_conf = (
        conf.fillna(0.0) <= max_confidence
        if conf is not None
        else pd.Series(True, index=merged.index)
    )
    qualifying = merged[unplaced | low_conf]
    return _cap_recent(qualifying, max_items)


def _cap_recent(frame: pd.DataFrame, max_items: int) -> pd.DataFrame:
    if "published_at" in frame.columns:
        frame = frame.sort_values("published_at", ascending=False)
    return frame.head(max_items).reset_index(drop=True)


def build_discovery_prompt(
    items: pd.DataFrame,
    *,
    existing_theme_ids: set[str],
    max_body_chars: int = 280,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for a single DeepSeek proposal call.

    The model is asked to PROPOSE candidate secular themes and reference the
    article indices that support each one. It must not reuse existing theme_ids.
    """
    system = (
        "You are a research analyst identifying EMERGING secular investment "
        "themes from a batch of news headlines that an existing taxonomy could "
        "not classify.\n"
        "Return valid JSON only. No markdown. No investment advice; do not use "
        "buy, sell, overweight, underweight, allocation, or position language.\n"
        "Propose only durable multi-year structural themes, not single events or "
        "short-term macro moves. Do not duplicate these existing theme_ids: "
        f"{', '.join(sorted(existing_theme_ids))}.\n"
        "theme_id must be lower_snake_case, 3-40 chars. Reference the 0-based "
        "article indices that support each theme.\n"
        'Shape: {"candidates": [{"theme_id": "...", "label": "...", '
        '"description": "...", "article_indices": [0, 2]}]}\n'
        "If no durable new theme is present, return {\"candidates\": []}."
    )
    lines = []
    for idx, row in items.reset_index(drop=True).iterrows():
        title = str(row.get("title", "")).strip()
        body = str(row.get("body", "") or "")[:max_body_chars].strip()
        lines.append(f"[{idx}] {title} :: {body}")
    user = "Articles:\n" + "\n".join(lines) + "\n\nReturn JSON only."
    return system, user


def parse_candidate_response(
    text: str,
    items: pd.DataFrame,
    *,
    existing_theme_ids: set[str],
) -> list[ThemeCandidate]:
    """Parse a model JSON proposal and attach support metrics from OUR data.

    Counts (distinct articles, days observed, source diversity) are recomputed
    from the referenced source rows - the model's own numbers are ignored.
    Malformed entries and duplicates of existing themes are dropped.
    """
    payload = json.loads(text) if isinstance(text, str) else text
    raw_candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
    indexed = items.reset_index(drop=True)
    seen: set[str] = set()
    out: list[ThemeCandidate] = []
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        theme_id = str(raw.get("theme_id", "")).strip().lower()
        label = str(raw.get("label", "")).strip()
        description = str(raw.get("description", "")).strip()
        if not _THEME_ID_RE.match(theme_id) or not label or not description:
            continue
        if theme_id in existing_theme_ids or theme_id in seen:
            continue
        seen.add(theme_id)
        idxs = [i for i in raw.get("article_indices", []) if isinstance(i, int)]
        support = indexed.iloc[[i for i in idxs if 0 <= i < len(indexed)]]
        out.append(
            ThemeCandidate(
                theme_id=theme_id,
                label=label,
                description=description,
                **_support_metrics(support),
            )
        )
    return out


def _support_metrics(support: pd.DataFrame) -> dict[str, Any]:
    if support.empty:
        return {
            "distinct_articles": 0,
            "days_observed": 0,
            "source_diversity": 0,
            "supporting_news_ids": [],
        }
    news_ids = (
        [str(x) for x in support["news_id"].dropna().unique()]
        if "news_id" in support.columns
        else []
    )
    sources = (
        support["source"].dropna().nunique() if "source" in support.columns else 0
    )
    days = 0
    if "published_at" in support.columns:
        dates = pd.to_datetime(support["published_at"], errors="coerce").dropna()
        days = dates.dt.normalize().nunique()
    return {
        "distinct_articles": len(news_ids),
        "days_observed": int(days),
        "source_diversity": int(sources),
        "supporting_news_ids": news_ids,
    }


def evaluate_promotion(
    candidate: ThemeCandidate,
    *,
    existing_theme_ids: set[str],
    thresholds: PromotionThresholds = PromotionThresholds(),
) -> tuple[bool, str]:
    """Pure anti-noise gate. Returns (promote, reason)."""
    if candidate.theme_id in existing_theme_ids:
        return False, "already_registered"
    if candidate.distinct_articles < thresholds.min_distinct_articles:
        return False, (
            f"insufficient_articles {candidate.distinct_articles} "
            f"< {thresholds.min_distinct_articles}"
        )
    if candidate.days_observed < thresholds.min_days_observed:
        return False, (
            f"insufficient_days {candidate.days_observed} < {thresholds.min_days_observed}"
        )
    if candidate.source_diversity < thresholds.min_source_diversity:
        return False, (
            f"insufficient_source_diversity {candidate.source_diversity} "
            f"< {thresholds.min_source_diversity}"
        )
    return True, "ok"


def promote_candidates(
    themes_path: str,
    candidates: list[ThemeCandidate],
    *,
    thresholds: PromotionThresholds = PromotionThresholds(),
) -> dict[str, Any]:
    """Register gate-passing candidates into news_themes.yaml (additive).

    Existing themes are never edited or removed. Returns a summary dict.
    Writing leaves the change in the working tree for human git-diff review;
    it never commits.
    """
    with open(themes_path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    secular = data.get("secular_themes") or {}
    existing = set(secular.keys())

    promoted: list[str] = []
    rejected: list[dict[str, str]] = []
    for cand in candidates:
        ok, reason = evaluate_promotion(
            cand, existing_theme_ids=existing | set(promoted), thresholds=thresholds
        )
        if not ok:
            rejected.append({"theme_id": cand.theme_id, "reason": reason})
            continue
        secular[cand.theme_id] = {"label": cand.label, "description": cand.description}
        promoted.append(cand.theme_id)

    if promoted:
        data["secular_themes"] = secular
        with open(themes_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "promoted": promoted,
        "rejected": rejected,
        "existing_count": len(existing),
        "new_count": len(promoted),
    }
