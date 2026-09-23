"""News as an independent, separately attributable advisory block.

The news layer answers a different question from the macro layer, and the requirement
is that it is readable ON ITS OWN TERMS rather than folded into the regime signal. This
module publishes that standalone block:

    per sector -> direction, magnitude, the evidence behind it, and its own confidence

Nothing here is added to, subtracted from, or blended with a macro score. The block is
additive output; the existing overlay behaviour stays available and stays the default
(see SectorNewsIntegrationConfig.advisory_block).

A direction is asserted only when the evidence clears configured floors. Below them the
entry is reported as `insufficient_evidence` — a thin news tape is a fact about coverage,
and dressing it up as a weak view is how a news layer starts quietly steering a book.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.news.config import (
    SectorNewsIntegrationConfig,
    load_sector_news_integration_config,
)
from macro_engine.storage.duckdb_store import DuckDBStore

ADVISORY_DISCLAIMER = (
    "This is a diagnostic news-attribution block. It is a separate consultative layer "
    "and is not blended into the macro regime signal. It is not investment advice, "
    "market action guidance, execution guidance, or instructions for changing holdings."
)


def build_news_advisory_block(
    *,
    sector_scores: pd.DataFrame,
    daily_news_scores: pd.DataFrame,
    weekly_news_scores: pd.DataFrame,
    config: SectorNewsIntegrationConfig,
    computed_at: datetime | None = None,
) -> dict[str, Any]:
    computed_at = computed_at or datetime.now(UTC)
    news = _news_frame(daily_news_scores, weekly_news_scores, config)
    sectors = _sector_ids(sector_scores)
    advisory = config.advisory_block

    if news.empty:
        return _payload(
            computed_at=computed_at,
            config=config,
            latest_date=None,
            entries=[
                _entry(sector_id, None, advisory, reason="no_recent_news")
                for sector_id in sectors
            ],
        )

    latest_date = news["score_date"].max()
    cutoff = latest_date - pd.Timedelta(days=config.news_score_decay_days)
    recent = news[news["score_date"] >= cutoff]
    entries: list[dict[str, Any]] = []
    for sector_id in sectors:
        rows = recent[recent["sector_id"] == sector_id]
        if rows.empty:
            entries.append(_entry(sector_id, None, advisory, reason="no_recent_news"))
            continue
        row = rows.sort_values("score_date").iloc[-1]
        entries.append(_entry(sector_id, row, advisory, reason="ok"))

    return _payload(
        computed_at=computed_at,
        config=config,
        latest_date=latest_date,
        entries=entries,
    )


def build_stored_news_advisory_block(
    *,
    config_path: str | Path = "config/sector_news_integration.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> dict[str, Any]:
    config = load_sector_news_integration_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    return build_news_advisory_block(
        sector_scores=store.read_table("sector_scores"),
        daily_news_scores=store.read_table("news_daily_sector_scores"),
        weekly_news_scores=store.read_table("news_weekly_sector_scores"),
        config=config,
    )


def write_news_advisory_block(
    *,
    config_path: str | Path = "config/sector_news_integration.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    config = load_sector_news_integration_config(config_path)
    payload = build_stored_news_advisory_block(config_path=config_path, db_path=db_path)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / config.advisory_block.output_file
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    markdown_path.write_text(advisory_markdown(payload), encoding="utf-8")
    return json_path, markdown_path


def advisory_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# News Advisory Block",
        "",
        f"- computed at: {payload['computed_at']}",
        f"- latest news score date: {payload.get('latest_news_score_date') or 'n/a'}",
        f"- integration mode: {payload['integration_mode']}",
        "",
        "This block is published beside the macro diagnostic, never blended into it.",
        "",
        "| sector | direction | score | items | confidence | evidence |",
        "|---|---|---|---|---|---|",
    ]
    for entry in payload["entries"]:
        lines.append(
            "| {sector} | {direction} | {score} | {items} | {confidence} | {reason} |".format(
                sector=entry["sector_id"],
                direction=entry["direction"],
                score="n/a" if entry["news_score"] is None else f"{entry['news_score']:.3f}",
                items=entry["item_count"],
                confidence=(
                    "n/a"
                    if entry["news_confidence"] is None
                    else f"{entry['news_confidence']:.2f}"
                ),
                reason=entry["reason"],
            )
        )
    lines += ["", payload["disclaimer"], ""]
    return "\n".join(lines)


def _entry(
    sector_id: str,
    row: pd.Series | None,
    advisory: Any,
    *,
    reason: str,
) -> dict[str, Any]:
    if row is None:
        return {
            "sector_id": sector_id,
            "direction": "insufficient_evidence",
            "news_score": None,
            "item_count": 0,
            "news_confidence": None,
            "max_single_item_score": None,
            "score_date": None,
            "reason": reason,
            "attribution": "news_layer_only; no macro component",
        }
    score = float(row.get("adjusted_news_score") or 0.0)
    item_count = int(
        (row.get("positive_item_count") or 0)
        + (row.get("negative_item_count") or 0)
        + (row.get("neutral_item_count") or 0)
    )
    confidence = row.get("avg_confidence")
    confidence = None if confidence is None or pd.isna(confidence) else float(confidence)
    if item_count < advisory.min_items_for_direction:
        direction = "insufficient_evidence"
    elif abs(score) < advisory.min_direction_score:
        direction = "neutral"
    else:
        direction = "positive" if score > 0 else "negative"
    return {
        "sector_id": sector_id,
        "direction": direction,
        "news_score": round(score, 6),
        "item_count": item_count,
        "news_confidence": confidence,
        "max_single_item_score": _optional(row.get("max_single_item_score")),
        "score_date": _date(row.get("score_date")),
        "reason": "ok",
        "attribution": "news_layer_only; no macro component",
    }


def _payload(
    *,
    computed_at: datetime,
    config: SectorNewsIntegrationConfig,
    latest_date: Any,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    directed = [entry for entry in entries if entry["direction"] in ("positive", "negative")]
    return {
        "valid": bool(entries),
        "computed_at": computed_at.isoformat(),
        "latest_news_score_date": _date(latest_date),
        "integration_mode": config.advisory_block.mode,
        "block_role": "independent_consultative_layer",
        "macro_blended": False,
        "news_score_frequency": config.news_score_frequency,
        "news_score_decay_days": config.news_score_decay_days,
        "entry_count": len(entries),
        "directed_entry_count": len(directed),
        "insufficient_evidence_count": sum(
            1 for entry in entries if entry["direction"] == "insufficient_evidence"
        ),
        "entries": entries,
        "disclaimer": ADVISORY_DISCLAIMER,
    }


def rekey_theme_scores_by_group(
    theme_scores: pd.DataFrame,
    group_theme_ids: dict[str, list[str]],
) -> dict[str, pd.DataFrame]:
    """Re-key a `news_theme_scores`-shaped frame (`news_id, theme_id, direction, ...`,
    one row per item per theme) from per-theme rows to per-group rows, for a caller whose
    own taxonomy groups several themes under one id -- e.g. Layer 2's `shock_id ->
    theme_ids` map in `config/shocks.yaml` (§3.5). A group's frame is empty (not missing)
    when it maps to no theme_ids or none fired recently, so callers can iterate every
    group uniformly.

    Reused by `macro_engine.shocks.narrative` rather than reimplemented there -- the
    sector advisory block above re-keys the same shape by `sector_id`, and duplicating
    that join per caller is how the two would quietly drift apart."""
    empty = theme_scores.iloc[0:0].copy()
    return {
        group_id: (
            theme_scores[theme_scores["theme_id"].isin(theme_ids)].copy()
            if theme_ids and not theme_scores.empty
            else empty
        )
        for group_id, theme_ids in group_theme_ids.items()
    }


def _news_frame(
    daily_news_scores: pd.DataFrame,
    weekly_news_scores: pd.DataFrame,
    config: SectorNewsIntegrationConfig,
) -> pd.DataFrame:
    if config.news_score_frequency == "weekly":
        frame = weekly_news_scores.copy()
        if frame.empty:
            return pd.DataFrame(columns=["score_date", "sector_id", "adjusted_news_score"])
        frame["score_date"] = pd.to_datetime(frame["week_start_date"], errors="coerce")
    else:
        frame = daily_news_scores.copy()
        if frame.empty:
            return pd.DataFrame(columns=["score_date", "sector_id", "adjusted_news_score"])
        frame["score_date"] = pd.to_datetime(frame["score_date"], errors="coerce")
    return frame.dropna(subset=["score_date"])


def _sector_ids(sector_scores: pd.DataFrame) -> list[str]:
    if sector_scores.empty or "sector_id" not in sector_scores.columns:
        return []
    return sorted({str(value) for value in sector_scores["sector_id"].dropna().unique()})


def _optional(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _date(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")
