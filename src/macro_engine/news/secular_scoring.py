from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

from macro_engine.news.config import (
    SecularNewsScoringConfig,
    load_news_themes_config,
    load_secular_news_scoring_config,
)
from macro_engine.news.report import FORBIDDEN_REPORT_TERMS
from macro_engine.news.scoring import freshness_weight
from macro_engine.storage.duckdb_store import DuckDBStore


SECULAR_TRACKER_DISCLAIMER = (
    "This is a diagnostic secular-theme tracker built from stored news classifications. "
    "It is not investment advice, market action guidance, execution guidance, or "
    "instructions for changing holdings."
)


def write_secular_theme_tracker(
    *,
    scoring_config_path: str | Path = "config/news_scoring.yaml",
    themes_config_path: str | Path = "config/news_themes.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    as_of: str | None = None,
) -> tuple[Path, Path]:
    scoring_config = load_secular_news_scoring_config(scoring_config_path)
    themes_config = load_news_themes_config(themes_config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    computed_at = _computed_at(as_of)
    payload = build_secular_theme_tracker(
        news_items=store.read_table("news_items"),
        classifications=store.read_table("news_classifications"),
        scoring_config=scoring_config,
        secular_themes=themes_config.secular_themes or {},
        computed_at=computed_at,
    )
    markdown = secular_theme_tracker_markdown(payload)
    _assert_no_forbidden_language(markdown)
    output_dir = Path(scoring_config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "secular_theme_scores.json"
    markdown_path = output_dir / f"secular_theme_tracker_{computed_at.date():%Y%m%d}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_secular_theme_tracker(
    *,
    news_items: pd.DataFrame,
    classifications: pd.DataFrame,
    scoring_config: SecularNewsScoringConfig,
    secular_themes: dict[str, Any],
    computed_at: datetime | None = None,
) -> dict[str, Any]:
    computed_at = computed_at or datetime.now(UTC)
    theme_ids = list(secular_themes.keys())
    components = _secular_components(
        news_items=news_items,
        classifications=classifications,
        scoring_config=scoring_config,
        theme_ids=set(theme_ids),
        computed_at=computed_at,
    )
    theme_payload = {
        theme_id: _theme_payload(
            theme_id=theme_id,
            definition=secular_themes.get(theme_id) or {},
            components=components[components["secular_theme"] == theme_id]
            if not components.empty
            else components,
            scoring_config=scoring_config,
            computed_at=computed_at,
        )
        for theme_id in theme_ids
    }
    return _json_safe(
        {
            "valid": bool(theme_ids),
            "computed_at": computed_at.isoformat(),
            "readiness_note": "diagnostic_overlay_only",
            "classification_scope": "stored_successful_classifications_with_secular_theme",
            "frequencies": scoring_config.aggregation_frequency,
            "trend_window_days": scoring_config.trend_window_days,
            "theme_count": len(theme_ids),
            "scored_theme_count": sum(1 for item in theme_payload.values() if item["item_count"] > 0),
            "themes": theme_payload,
            "disclaimer": SECULAR_TRACKER_DISCLAIMER,
        }
    )


def secular_theme_tracker_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# Secular Theme Tracker\n\nNo secular themes configured.\n\n{payload['disclaimer']}\n"
    rows = []
    for theme in payload["themes"].values():
        rows.append(
            "- {theme_id}: score {score:.3f}, trend_30d {trend:.3f}, items {items}, "
            "confidence {confidence}".format(
                theme_id=theme["theme_id"],
                score=float(theme["score"]),
                trend=float(theme["trend_30d"]),
                items=int(theme["item_count"]),
                confidence=_format_optional_score(theme["avg_confidence"]),
            )
        )
    return """# Secular Theme Tracker

Mode: deterministic aggregation of stored secular-theme classifications.

Computed at: {computed_at}
Readiness note: {readiness_note}

## Theme Scores

{theme_rows}

{disclaimer}
""".format(
        computed_at=payload["computed_at"],
        readiness_note=payload["readiness_note"],
        theme_rows="\n".join(rows) or "- None",
        disclaimer=payload["disclaimer"],
    )


def _secular_components(
    *,
    news_items: pd.DataFrame,
    classifications: pd.DataFrame,
    scoring_config: SecularNewsScoringConfig,
    theme_ids: set[str],
    computed_at: datetime,
) -> pd.DataFrame:
    if news_items.empty or classifications.empty or not theme_ids:
        return _component_frame()
    if "secular_theme" not in classifications.columns:
        return _component_frame()
    classes = classifications[classifications["classification_status"] == "success"].copy()
    classes["secular_theme"] = classes["secular_theme"].where(
        classes["secular_theme"].notna(), None
    )
    classes = classes[classes["secular_theme"].isin(theme_ids)].copy()
    if classes.empty:
        return _component_frame()
    items = news_items.copy()
    items["published_at"] = pd.to_datetime(items["published_at"], errors="coerce", utc=True)
    classes["classified_at"] = pd.to_datetime(classes["classified_at"], errors="coerce", utc=True)
    classes["severity"] = pd.to_numeric(classes["severity"], errors="coerce").fillna(0.0)
    classes["confidence"] = pd.to_numeric(classes["confidence"], errors="coerce").fillna(0.0)
    merged = classes.merge(
        items[["news_id", "source", "source_url", "title", "published_at"]],
        on="news_id",
        how="left",
    )
    merged["event_at"] = merged["published_at"].fillna(merged["classified_at"])
    rows = []
    score_date = pd.Timestamp(computed_at).tz_convert("UTC").normalize()
    for row in merged.to_dict(orient="records"):
        if not _row_is_eligible(row, score_date, scoring_config):
            continue
        severity = float(row["severity"])
        confidence = float(row["confidence"])
        raw = severity if scoring_config.severity_weighting else 1.0
        adjusted = raw
        if scoring_config.confidence_weighting:
            adjusted *= confidence
        adjusted *= _source_weight(row.get("source"), scoring_config)
        adjusted *= freshness_weight(_age_days(row["event_at"], score_date), scoring_config)
        adjusted = _clip(adjusted, scoring_config.max_single_item_contribution)
        rows.append(
            {
                "news_id": row["news_id"],
                "secular_theme": row["secular_theme"],
                "event_at": row["event_at"],
                "source": row.get("source"),
                "source_url": row.get("source_url"),
                "title": row.get("title"),
                "ai_provider": row.get("ai_provider"),
                "ai_model": row.get("ai_model"),
                "severity": severity,
                "confidence": confidence,
                "raw_component": raw,
                "adjusted_component": adjusted,
            }
        )
    if not rows:
        return _component_frame()
    return pd.DataFrame(rows, columns=_component_columns())


def _theme_payload(
    *,
    theme_id: str,
    definition: dict[str, Any],
    components: pd.DataFrame,
    scoring_config: SecularNewsScoringConfig,
    computed_at: datetime,
) -> dict[str, Any]:
    if components.empty:
        return {
            "theme_id": theme_id,
            "label": definition.get("label", theme_id),
            "score": 0.0,
            "raw_score": 0.0,
            "trend_30d": 0.0,
            "item_count": 0,
            "avg_confidence": None,
            "avg_severity": None,
            "top_news_ids": [],
            "top_news_items": [],
            "source_mix": {},
            "mock_contribution_ratio": 0.0,
            "monthly_scores": [],
            "quarterly_scores": [],
        }
    frame = components.copy()
    capped = _source_period_capped(frame, scoring_config)
    current_score = float(capped["capped_component"].sum())
    raw_score = float(frame["raw_component"].sum())
    return {
        "theme_id": theme_id,
        "label": definition.get("label", theme_id),
        "score": current_score,
        "raw_score": raw_score,
        "trend_30d": _trend(frame, scoring_config, computed_at),
        "item_count": int(frame["news_id"].nunique()),
        "avg_confidence": float(frame["confidence"].mean()),
        "avg_severity": float(frame["severity"].mean()),
        "top_news_ids": _top_news_ids(frame),
        "top_news_items": _top_news_items(frame),
        "source_mix": _source_mix(frame),
        "mock_contribution_ratio": _mock_ratio(frame),
        "monthly_scores": _period_scores(frame, "M", scoring_config),
        "quarterly_scores": _period_scores(frame, "Q", scoring_config),
    }


def _row_is_eligible(
    row: dict[str, Any],
    score_date: pd.Timestamp,
    scoring_config: SecularNewsScoringConfig,
) -> bool:
    event_at = pd.Timestamp(row.get("event_at"))
    if pd.isna(event_at):
        return False
    age = _age_days(event_at, score_date)
    if age < 0 or freshness_weight(age, scoring_config) <= 0.0:
        return False
    return float(row.get("confidence", 0.0)) >= scoring_config.min_confidence and float(
        row.get("severity", 0.0)
    ) >= scoring_config.min_severity


def _source_period_capped(
    frame: pd.DataFrame,
    scoring_config: SecularNewsScoringConfig,
) -> pd.DataFrame:
    work = frame.copy()
    event_at = pd.to_datetime(work["event_at"], utc=True).dt.tz_localize(None)
    work["period"] = event_at.dt.to_period("M").dt.start_time
    rows = []
    for (period, source), group in work.groupby(["period", "source"], dropna=False):
        rows.append(
            {
                "period": period,
                "source": source,
                "capped_component": _clip(
                    float(group["adjusted_component"].sum()),
                    scoring_config.max_single_source_period_contribution,
                ),
            }
        )
    return pd.DataFrame(rows)


def _period_scores(
    frame: pd.DataFrame,
    frequency: str,
    scoring_config: SecularNewsScoringConfig,
) -> list[dict[str, Any]]:
    label = "monthly" if frequency == "M" else "quarterly"
    if label not in scoring_config.aggregation_frequency:
        return []
    work = frame.copy()
    event_at = pd.to_datetime(work["event_at"], utc=True).dt.tz_localize(None)
    period = event_at.dt.to_period(frequency)
    work["period_start"] = period.dt.start_time.dt.date
    rows = []
    for period_start, group in work.groupby("period_start"):
        capped = _source_period_capped(group, scoring_config)
        rows.append(
            {
                "period_start": period_start.isoformat(),
                "score": float(capped["capped_component"].sum()) if not capped.empty else 0.0,
                "item_count": int(group["news_id"].nunique()),
                "avg_confidence": float(group["confidence"].mean()),
            }
        )
    return sorted(rows, key=lambda row: row["period_start"])


def _trend(
    frame: pd.DataFrame,
    scoring_config: SecularNewsScoringConfig,
    computed_at: datetime,
) -> float:
    end = pd.Timestamp(computed_at).tz_convert("UTC")
    event_at = pd.to_datetime(frame["event_at"], utc=True)
    recent_start = end - pd.Timedelta(days=scoring_config.trend_window_days)
    prior_start = recent_start - pd.Timedelta(days=scoring_config.trend_window_days)
    recent = frame[(event_at > recent_start) & (event_at <= end)]
    prior = frame[(event_at > prior_start) & (event_at <= recent_start)]
    return float(recent["adjusted_component"].sum() - prior["adjusted_component"].sum())


def _top_news_ids(frame: pd.DataFrame) -> list[str]:
    ranked = frame.assign(abs_component=frame["adjusted_component"].abs()).sort_values(
        ["abs_component", "news_id"],
        ascending=[False, True],
    )
    return ranked["news_id"].drop_duplicates().head(5).tolist()


def _top_news_items(frame: pd.DataFrame) -> list[dict[str, Any]]:
    ranked = frame.assign(abs_component=frame["adjusted_component"].abs()).sort_values(
        ["abs_component", "news_id"],
        ascending=[False, True],
    )
    rows = []
    for row in ranked.head(5).to_dict(orient="records"):
        rows.append(
            {
                "news_id": row["news_id"],
                "title": _safe_report_text(row.get("title")),
                "source": row.get("source"),
                "source_url": row.get("source_url"),
                "score_component": float(row["adjusted_component"]),
                "confidence": float(row["confidence"]),
            }
        )
    return rows


def _source_mix(frame: pd.DataFrame) -> dict[str, int]:
    counts = frame["source"].fillna("unknown").astype(str).value_counts().sort_index()
    return {str(key): int(value) for key, value in counts.items()}


def _mock_ratio(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    providers = frame["ai_provider"].fillna("").astype(str).str.lower()
    mock_rows = frame[providers.str.contains("mock", regex=False)]
    total = float(frame["adjusted_component"].abs().sum())
    if total == 0.0:
        return 0.0
    return float(mock_rows["adjusted_component"].abs().sum() / total)


def _age_days(event_at: Any, score_date: pd.Timestamp) -> int:
    event = pd.Timestamp(event_at)
    if event.tzinfo is None:
        event = event.tz_localize("UTC")
    if score_date.tzinfo is None:
        score_date = score_date.tz_localize("UTC")
    return int((score_date.normalize() - event.normalize()).days)


def _source_weight(source: str | None, scoring_config: SecularNewsScoringConfig) -> float:
    if source is None:
        return scoring_config.source_weights.default
    return scoring_config.source_weights.sources.get(str(source), scoring_config.source_weights.default)


def _clip(value: float, cap: float) -> float:
    return max(-cap, min(cap, value))


def _computed_at(as_of: str | None) -> datetime:
    if as_of is None:
        return datetime.now(UTC)
    parsed = pd.Timestamp(as_of)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    return parsed.to_pydatetime()


def _component_columns() -> list[str]:
    return [
        "news_id",
        "secular_theme",
        "event_at",
        "source",
        "source_url",
        "title",
        "ai_provider",
        "ai_model",
        "severity",
        "confidence",
        "raw_component",
        "adjusted_component",
    ]


def _component_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_component_columns())


def _assert_no_forbidden_language(markdown: str) -> None:
    lower = markdown.lower()
    violations = [term for term in FORBIDDEN_REPORT_TERMS if term in lower]
    if violations:
        raise ValueError(f"secular theme tracker contains forbidden language: {violations}")


def _safe_report_text(value: Any) -> str:
    text = "" if value is None or pd.isna(value) else str(value)
    for term in sorted(FORBIDDEN_REPORT_TERMS, key=len, reverse=True):
        text = re.sub(re.escape(term), "market-action term", text, flags=re.IGNORECASE)
    return text


def _format_optional_score(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.2f}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value
