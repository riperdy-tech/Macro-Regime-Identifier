"""MRI Layer 2 (MRI-12) — §3.5 the narrative channel, attached, never additive.

`attach_narrative` receives the register as an immutable object (a `dict`, as
`register.build_shock_register_artifact` returns it) and returns a **copy** with
`narrative` filled per shock and `unmeasured_narratives` populated. It never rewrites
`value`, `severity`, `direction`, `intensity`, `state`, or any other numeric/state field
`register.py` already finalised -- `tests/test_phase_w_shocks.py` asserts the register's
numeric fields are byte-identical with this function called and not called.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pandas as pd

from macro_engine.news.advisory import rekey_theme_scores_by_group

NARRATIVE_LOOKBACK_DAYS = 14
SUMMARY_MAX_CHARS = 280

# A theme's "positive" classification means the condition it names is intensifying.
# Themes come in the taxonomy as an intensify/reverse pair (inflation_pressure /
# disinflation, credit_stress / credit_easing, ...) except where a shock has only the
# intensifying side (volatility, oil, dollar's financial/geopolitical/energy/commodity
# themes) -- a shock's fired `direction` ("up"/"down") is compared against a theme
# classification through this polarity, not against the raw string. Editorial judgement
# (MRI-12 owns this mapping per §3.5), not a measured quantity; it never touches a
# numeric field.
_UP_ALIGNED_THEMES = frozenset(
    {
        "inflation_pressure",
        "growth_acceleration",
        "labor_weakness",
        "monetary_tightening",
        "credit_stress",
        "yield_curve_flattening",
        "energy_supply_shock",
        "commodity_pressure",
        "fiscal_expansion",
        "geopolitical_risk",
        "financial_stability_risk",
    }
)
_DOWN_ALIGNED_THEMES = frozenset(
    {
        "disinflation",
        "growth_slowdown",
        "labor_strength",
        "monetary_easing",
        "credit_easing",
        "yield_curve_steepening",
        "fiscal_drag",
    }
)


def attach_narrative(
    register: dict[str, Any],
    *,
    news_items: pd.DataFrame,
    news_theme_scores: pd.DataFrame,
    news_classifications: pd.DataFrame,
    shock_theme_map: dict[str, list[str]],
    known_theme_ids: set[str],
    as_of: date,
    source_run_id: str | None = None,
    mock_ai: bool = False,
    lookback_days: int = NARRATIVE_LOOKBACK_DAYS,
) -> dict[str, Any]:
    out = copy.deepcopy(register)
    theme_frame = _theme_frame_in_window(news_items, news_theme_scores, as_of, lookback_days)
    by_shock = rekey_theme_scores_by_group(theme_frame, shock_theme_map)
    summary_by_news_id = {} if mock_ai else _summary_by_news_id(news_classifications)

    matched_theme_ids: set[str] = set()
    for shock_row in out["shocks"]:
        shock_id = shock_row["shock_id"]
        theme_ids = list(shock_theme_map.get(shock_id, []))
        matched_theme_ids.update(theme_ids)
        rows = by_shock.get(shock_id)
        if rows is None or rows.empty:
            continue
        fired_direction = shock_row.get("direction", "none")
        shock_row["narrative"] = {
            "theme_ids": theme_ids,
            "item_count": int(rows["news_id"].nunique()),
            "direction_agreement": _direction_agreement(rows, fired_direction),
            "classifier_confidence": _mean_confidence(rows),
            "summary": None if mock_ai else _pick_summary(rows, summary_by_news_id),
            "source_run_id": source_run_id,
        }

    out["unmeasured_narratives"] = _unmeasured_narratives(theme_frame, known_theme_ids, matched_theme_ids)
    return out


def _theme_frame_in_window(
    news_items: pd.DataFrame,
    news_theme_scores: pd.DataFrame,
    as_of: date,
    lookback_days: int,
) -> pd.DataFrame:
    if news_theme_scores.empty or news_items.empty:
        return news_theme_scores.iloc[0:0].copy()
    # Same join shape as news/scoring.py:_theme_base -- items[news_id, published_at]
    # merged onto the per-theme rows.
    frame = news_theme_scores.merge(
        news_items[["news_id", "published_at"]], on="news_id", how="left"
    )
    frame["published_at"] = pd.to_datetime(frame["published_at"], errors="coerce", utc=True)
    cutoff = pd.Timestamp(as_of, tz="UTC") - pd.Timedelta(days=lookback_days)
    upper = pd.Timestamp(as_of, tz="UTC") + pd.Timedelta(days=1)
    return frame[(frame["published_at"] >= cutoff) & (frame["published_at"] < upper)].copy()


def _direction_agreement(rows: pd.DataFrame, fired_direction: str) -> float | None:
    if fired_direction not in ("up", "down"):
        return None
    if rows.empty:
        return None

    def _matches(row: pd.Series) -> bool:
        theme_id = row["theme_id"]
        item_direction = row.get("direction")
        if item_direction not in ("positive", "negative"):
            return False
        up_aligned = theme_id in _UP_ALIGNED_THEMES
        down_aligned = theme_id in _DOWN_ALIGNED_THEMES
        if not up_aligned and not down_aligned:
            return False
        theme_says_up = (up_aligned and item_direction == "positive") or (
            down_aligned and item_direction == "negative"
        )
        theme_says_down = (down_aligned and item_direction == "positive") or (
            up_aligned and item_direction == "negative"
        )
        return (fired_direction == "up" and theme_says_up) or (
            fired_direction == "down" and theme_says_down
        )

    matches = rows.apply(_matches, axis=1)
    return float(matches.mean())


def _mean_confidence(rows: pd.DataFrame) -> float | None:
    confidence = pd.to_numeric(rows.get("confidence"), errors="coerce").dropna()
    if confidence.empty:
        return None
    return float(confidence.mean())


def _pick_summary(rows: pd.DataFrame, summary_by_news_id: dict[str, str]) -> str | None:
    for news_id in rows.sort_values("news_id")["news_id"].drop_duplicates():
        summary = summary_by_news_id.get(str(news_id))
        if summary:
            return summary[:SUMMARY_MAX_CHARS]
    return None


def _summary_by_news_id(news_classifications: pd.DataFrame) -> dict[str, str]:
    if news_classifications.empty or "summary" not in news_classifications.columns:
        return {}
    frame = news_classifications.dropna(subset=["summary"])
    return dict(zip(frame["news_id"].astype(str), frame["summary"].astype(str)))


def _unmeasured_narratives(
    theme_frame: pd.DataFrame,
    known_theme_ids: set[str],
    matched_theme_ids: set[str],
) -> list[dict[str, Any]]:
    """Themes the classifier raised in the window that map to no taxonomy entry (§3.5):
    no numeric field of any kind, promotion is an operator ruling plus a FRED series."""
    if theme_frame.empty:
        return []
    unmatched_ids = set(theme_frame["theme_id"].dropna().unique()) - matched_theme_ids
    unmatched_ids &= known_theme_ids
    out: list[dict[str, Any]] = []
    for theme_id in sorted(unmatched_ids):
        rows = theme_frame[theme_frame["theme_id"] == theme_id]
        out.append(
            {
                "theme_id": theme_id,
                "item_count": int(rows["news_id"].nunique()),
                "classifier_confidence": _mean_confidence(rows),
            }
        )
    return out
