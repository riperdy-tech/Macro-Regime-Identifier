from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

from macro_engine.news.config import load_news_scoring_config
from macro_engine.news.report import FORBIDDEN_REPORT_TERMS
from macro_engine.storage.duckdb_store import DuckDBStore

NEWS_SCORE_DISCLAIMER = (
    "This is a diagnostic news score overlay. It is not investment advice, "
    "market action guidance, execution guidance, or instructions for changing holdings."
)


def write_news_score_report(
    *,
    config_path: str | Path = "config/news_scoring.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    config = load_news_scoring_config(config_path)
    store = DuckDBStore(db_path)
    payload = build_news_score_report(
        daily_theme_scores=store.read_table("news_daily_theme_scores"),
        daily_sector_scores=store.read_table("news_daily_sector_scores"),
        weekly_theme_scores=store.read_table("news_weekly_theme_scores"),
        weekly_sector_scores=store.read_table("news_weekly_sector_scores"),
        components=store.read_table("news_score_components"),
        classifications=store.read_table("news_classifications"),
        news_items=store.read_table("news_items"),
    )
    markdown = news_score_report_markdown(payload)
    _assert_no_forbidden_language(markdown)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "news_score_report.json"
    markdown_path = output_dir / "news_score_report.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_news_score_report(
    *,
    daily_theme_scores: pd.DataFrame,
    daily_sector_scores: pd.DataFrame,
    weekly_theme_scores: pd.DataFrame,
    weekly_sector_scores: pd.DataFrame,
    components: pd.DataFrame,
    classifications: pd.DataFrame,
    news_items: pd.DataFrame,
) -> dict[str, Any]:
    if daily_theme_scores.empty and daily_sector_scores.empty:
        return {
            "valid": False,
            "reason": "no_news_scores",
            "disclaimer": NEWS_SCORE_DISCLAIMER,
        }
    theme = _with_date(daily_theme_scores, "score_date")
    sector = _with_date(daily_sector_scores, "score_date")
    latest_date = max(
        [
            frame["score_date"].max()
            for frame in [theme, sector]
            if not frame.empty
        ]
    )
    latest_theme = theme[theme["score_date"] == latest_date] if not theme.empty else theme
    latest_sector = sector[sector["score_date"] == latest_date] if not sector.empty else sector
    latest_components = _with_date(components, "score_date")
    if not latest_components.empty:
        latest_components = latest_components[latest_components["score_date"] == latest_date]
    return _json_safe(
        {
            "valid": True,
            "latest_news_scoring_date": latest_date.date().isoformat(),
            "top_positive_macro_themes": _top_rows(
                _positive_scores(latest_theme, "adjusted_score"),
                id_column="theme_id",
                score_column="adjusted_score",
                ascending=False,
            ),
            "top_negative_macro_themes": _top_rows(
                _negative_scores(latest_theme, "adjusted_score"),
                id_column="theme_id",
                score_column="adjusted_score",
                ascending=True,
            ),
            "top_sector_news_tailwinds": _top_rows(
                _positive_scores(latest_sector, "adjusted_news_score"),
                id_column="sector_id",
                score_column="adjusted_news_score",
                ascending=False,
            ),
            "top_sector_news_headwinds": _top_rows(
                _negative_scores(latest_sector, "adjusted_news_score"),
                id_column="sector_id",
                score_column="adjusted_news_score",
                ascending=True,
            ),
            "high_severity_items": _high_severity_items(classifications, news_items),
            "low_confidence_items": _low_confidence_items(classifications, news_items),
            "freshness_decay_note": (
                "Scores use configurable freshness decay so older classified items have "
                "less influence than newer classified items."
            ),
            "component_count": int(len(latest_components)),
            "weekly_theme_rows": int(len(weekly_theme_scores)),
            "weekly_sector_rows": int(len(weekly_sector_scores)),
            "disclaimer": NEWS_SCORE_DISCLAIMER,
        }
    )


def news_score_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# News Score Report\n\nNo valid news scores.\n\n{payload['disclaimer']}\n"
    positive_themes = _markdown_score_lines(payload["top_positive_macro_themes"])
    negative_themes = _markdown_score_lines(payload["top_negative_macro_themes"])
    sector_tailwinds = _markdown_score_lines(payload["top_sector_news_tailwinds"])
    sector_headwinds = _markdown_score_lines(payload["top_sector_news_headwinds"])
    high = _markdown_item_lines(payload["high_severity_items"], "severity")
    low = _markdown_item_lines(payload["low_confidence_items"], "confidence")
    return f"""# News Score Report

Mode: deterministic aggregation of stored AI classifications.

Latest news scoring date: {payload["latest_news_scoring_date"]}
Latest component rows: {payload["component_count"]}

## Top Positive Macro Themes

{positive_themes}

## Top Negative Macro Themes

{negative_themes}

## Sector News Tailwinds

{sector_tailwinds}

## Sector News Headwinds

{sector_headwinds}

## High-Severity Items

{high}

## Low-Confidence Or Unclear Items

{low}

## Freshness And Uncertainty

{payload["freshness_decay_note"]}

{payload["disclaimer"]}
"""


def _top_rows(
    frame: pd.DataFrame,
    *,
    id_column: str,
    score_column: str,
    ascending: bool,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    ranked = frame.sort_values([score_column, id_column], ascending=[ascending, True]).head(limit)
    return [
        {
            "id": row[id_column],
            "score": float(row[score_column]),
            "item_count": _item_count(row),
            "avg_confidence": _to_float(row.get("avg_confidence")),
            "avg_severity": _to_float(row.get("avg_severity")),
            "top_news_ids": _ids_from_value(row.get("top_news_ids_json")),
        }
        for row in ranked.to_dict(orient="records")
    ]


def _positive_scores(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[frame[column] > 0].copy()


def _negative_scores(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[frame[column] < 0].copy()


def _high_severity_items(classifications: pd.DataFrame, news_items: pd.DataFrame) -> list[dict[str, Any]]:
    if classifications.empty:
        return []
    frame = classifications[classifications["severity"].fillna(0.0) >= 0.6].copy()
    return _item_records(frame, news_items)


def _low_confidence_items(classifications: pd.DataFrame, news_items: pd.DataFrame) -> list[dict[str, Any]]:
    if classifications.empty:
        return []
    frame = classifications[classifications["confidence"].fillna(0.0) <= 0.4].copy()
    return _item_records(frame, news_items)


def _item_records(classifications: pd.DataFrame, news_items: pd.DataFrame) -> list[dict[str, Any]]:
    if classifications.empty:
        return []
    merged = classifications.merge(news_items, on="news_id", how="left", suffixes=("", "_item"))
    rows = []
    for row in merged.sort_values("classified_at").tail(10).to_dict(orient="records"):
        rows.append(
            {
                "news_id": row["news_id"],
                "title": row.get("title"),
                "severity": _to_float(row.get("severity")),
                "confidence": _to_float(row.get("confidence")),
                "summary": row.get("summary"),
            }
        )
    return rows


def _markdown_score_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- None"
    return "\n".join(
        f"- {item['id']}: score {item['score']:.3f}, items {item['item_count']}, "
        f"confidence {item['avg_confidence']:.2f}"
        for item in items
    )


def _markdown_item_lines(items: list[dict[str, Any]], metric: str) -> str:
    if not items:
        return "- None"
    return "\n".join(
        "- {title}: {metric} {value}".format(
            title=_safe_report_text(item["title"]),
            metric=metric,
            value=_format_optional_score(item[metric]),
        )
        for item in items
    )


def _with_date(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    result[column] = pd.to_datetime(result[column], errors="coerce")
    return result


def _ids_from_value(value: Any) -> list[str]:
    if value is None or pd.isna(value):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _safe_report_text(value: Any) -> str:
    text = "" if value is None or pd.isna(value) else str(value)
    for term in sorted(FORBIDDEN_REPORT_TERMS, key=len, reverse=True):
        text = re.sub(re.escape(term), "market-action term", text, flags=re.IGNORECASE)
    return text


def _format_optional_score(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.2f}"


def _item_count(row: dict[str, Any]) -> int:
    if "item_count" in row and not pd.isna(row.get("item_count")):
        return int(row["item_count"])
    return int(
        (row.get("positive_item_count") or 0)
        + (row.get("negative_item_count") or 0)
        + (row.get("neutral_item_count") or 0)
    )


def _assert_no_forbidden_language(markdown: str) -> None:
    lower = markdown.lower()
    violations = [term for term in FORBIDDEN_REPORT_TERMS if term in lower]
    if violations:
        raise ValueError(f"news score report contains forbidden language: {violations}")


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


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
