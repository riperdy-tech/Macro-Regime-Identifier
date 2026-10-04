"""WS-C theme retirement: the mirror of promotion, so the secular-theme list cannot only grow.

Promotion (theme_discovery.evaluate_promotion) admits a theme only when at least 8 articles over at least 5 days carry
it. Retirement applies the same evidence bar to how a theme has held up: a secular theme with fewer than
`min_articles` successful classifications in the last `window_days` is DORMANT. Nothing is retired automatically -
`retire_themes` removes the dormant themes from news_themes.yaml in the working tree, and the weekly workflow pushes
that diff to the review branch (auto/theme-proposals) with any promotions; a human merges it or not.

Evidence comes from the durable classification history (outputs/news_history/classifications/YYYY/MM/*.parquet,
persisted on the run-history branch by the daily job) - the weekly job's own store holds only that week's news. A
history shorter than `min_history_days` is too short to call a theme dormant: nothing is evaluated (stated, not
guessed). Counts are distinct news items, so one article classified twice counts once.

Other repositories may keep their own list under the same ids (stock-screener's scripts/paradigm_config.json defines
membership rules for its themes); retiring a theme here stops MRI's news scoring for it and changes nothing there.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


@dataclass(frozen=True)
class RetirementThresholds:
    window_days: int = 180
    min_articles: int = 8            # promotion's evidence bar (PromotionThresholds.min_articles)
    min_history_days: int = 90


def load_classification_history(history_dir: str | Path) -> pd.DataFrame:
    """Every classification row in the history's daily parquet files; an empty frame when there are none."""
    root = Path(history_dir) / "classifications"
    files = sorted(root.rglob("*.parquet")) if root.exists() else []
    if not files:
        return pd.DataFrame(columns=["news_id", "classified_at", "secular_theme", "classification_status"])
    return pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)


def evaluate_retirement(
    classifications: pd.DataFrame,
    theme_ids: set[str],
    *,
    as_of: datetime | None = None,
    thresholds: RetirementThresholds = RetirementThresholds(),
) -> dict[str, Any]:
    """{as_of, history_start, history_days, evaluated, reason, thresholds, themes: {id: {articles, dormant}},
    dormant: [ids]}. `evaluated` is False (and nothing is dormant) when the history spans fewer than
    `min_history_days` days."""
    as_of = (as_of or datetime.now(UTC)).astimezone(UTC)
    frame = classifications.copy()
    frame["classified_at"] = pd.to_datetime(frame.get("classified_at"), errors="coerce", utc=True)
    frame = frame[frame["classified_at"].notna()]
    start = frame["classified_at"].min() if not frame.empty else None
    history_days = (pd.Timestamp(as_of) - start).days if start is not None else 0
    out: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "history_start": start.isoformat() if start is not None else None,
        "history_days": int(history_days),
        "thresholds": asdict(thresholds),
        "themes": {},
        "dormant": [],
    }
    if history_days < thresholds.min_history_days:
        out.update(evaluated=False,
                   reason=f"history spans {history_days} days < {thresholds.min_history_days}; no theme is judged")
        return out
    window_start = pd.Timestamp(as_of - timedelta(days=thresholds.window_days))
    ok = frame[(frame["classification_status"] == "success") & (frame["classified_at"] >= window_start)]
    for theme_id in sorted(theme_ids):
        articles = int(ok.loc[ok["secular_theme"] == theme_id, "news_id"].nunique())
        dormant = articles < thresholds.min_articles
        out["themes"][theme_id] = {"articles": articles, "dormant": dormant}
        if dormant:
            out["dormant"].append(theme_id)
    out.update(evaluated=True, reason=None)
    return out


def retire_themes(themes_path: str | Path, theme_ids: list[str]) -> list[str]:
    """Remove `theme_ids` from the secular_themes of news_themes.yaml (working tree only, never committed here).
    Returns the ids actually removed."""
    with open(themes_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    secular = data.get("secular_themes") or {}
    removed = [theme_id for theme_id in theme_ids if theme_id in secular]
    if removed:
        for theme_id in removed:
            del secular[theme_id]
        data["secular_themes"] = secular
        with open(themes_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
    return removed
