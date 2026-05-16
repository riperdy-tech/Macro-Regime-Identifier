from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

from macro_engine.news.config import load_news_ai_config
from macro_engine.storage.duckdb_store import DuckDBStore


NEWS_REPORT_DISCLAIMER = (
    "This is an AI-assisted diagnostic news/event classification report. "
    "It is not investment advice, market action guidance, execution guidance, "
    "or instructions for changing holdings."
)

FORBIDDEN_REPORT_TERMS = [
    "buy",
    "sell",
    "overweight",
    "underweight",
    "avoid",
    "recommendation",
    "trade",
    "position sizing",
    "portfolio allocation",
]


def write_news_report(
    *,
    ai_config_path: str | Path = "config/news_ai.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    config = load_news_ai_config(ai_config_path)
    store = DuckDBStore(db_path)
    payload = build_news_report(
        news_items=store.read_table("news_items"),
        classifications=store.read_table("news_classifications"),
        theme_scores=store.read_table("news_theme_scores"),
        sector_impacts=store.read_table("news_sector_impacts"),
    )
    markdown = news_report_markdown(payload)
    _assert_no_forbidden_language(markdown)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "news_classification_report.json"
    markdown_path = output_dir / "news_classification_report.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_news_report(
    *,
    news_items: pd.DataFrame,
    classifications: pd.DataFrame,
    theme_scores: pd.DataFrame,
    sector_impacts: pd.DataFrame,
) -> dict[str, Any]:
    if classifications.empty:
        return {
            "valid": False,
            "reason": "no_news_classifications",
            "disclaimer": NEWS_REPORT_DISCLAIMER,
        }
    successes = classifications[classifications["classification_status"] == "success"].copy()
    latest = classifications.sort_values("classified_at").tail(10)
    return _json_safe(
        {
            "valid": True,
            "news_item_count": int(len(news_items)),
            "classification_count": int(len(classifications)),
            "successful_classification_count": int(len(successes)),
            "error_classification_count": int(len(classifications) - len(successes)),
            "latest_classified_items": _latest_items(latest, news_items),
            "top_macro_themes": _top_theme_scores(theme_scores),
            "top_sector_impacts": _top_sector_impacts(sector_impacts),
            "high_severity_events": _high_severity(classifications, news_items),
            "low_confidence_events": _low_confidence(classifications, news_items),
            "disclaimer": NEWS_REPORT_DISCLAIMER,
        }
    )


def news_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# News Classification Report\n\nNo valid news classifications.\n\n{payload['disclaimer']}\n"
    latest = "\n".join(
        "- {date}: {title} ({summary})".format(
            date=item["published_at"] or "unknown date",
            title=_safe_report_text(item["title"]),
            summary=_safe_report_text(item["summary"]),
        )
        for item in payload["latest_classified_items"]
    )
    themes = "\n".join(
        f"- {item['theme_id']}: average severity {item['average_severity']:.2f}, count {item['count']}"
        for item in payload["top_macro_themes"]
    ) or "- none"
    sectors = "\n".join(
        f"- {item['sector_id']}: average impact {item['average_impact_score']:.2f}, count {item['count']}"
        for item in payload["top_sector_impacts"]
    ) or "- none"
    high = "\n".join(
        "- {title}: severity {severity}, confidence {confidence}".format(
            title=_safe_report_text(item["title"]),
            severity=_format_optional_score(item["severity"]),
            confidence=_format_optional_score(item["confidence"]),
        )
        for item in payload["high_severity_events"]
    ) or "- none"
    low = "\n".join(
        "- {title}: confidence {confidence}".format(
            title=_safe_report_text(item["title"]),
            confidence=_format_optional_score(item["confidence"]),
        )
        for item in payload["low_confidence_events"]
    ) or "- none"
    return f"""# News Classification Report

Mode: AI-assisted diagnostic classification only.

News items: {payload["news_item_count"]}
Classifications: {payload["classification_count"]}
Successful classifications: {payload["successful_classification_count"]}
Classification errors: {payload["error_classification_count"]}

## Latest Classified Items

{latest}

## Top Macro Themes

{themes}

## Top Sector Impacts

{sectors}

## High-Severity Events

{high}

## Low-Confidence Or Unclear Events

{low}

{payload["disclaimer"]}
"""


def _latest_items(classifications: pd.DataFrame, news_items: pd.DataFrame) -> list[dict[str, Any]]:
    if classifications.empty:
        return []
    merged = classifications.merge(news_items, on="news_id", how="left", suffixes=("", "_item"))
    rows = []
    for row in merged.sort_values("classified_at").tail(10).to_dict(orient="records"):
        rows.append(
            {
                "news_id": row["news_id"],
                "title": row.get("title"),
                "published_at": None if pd.isna(row.get("published_at")) else str(row.get("published_at")),
                "summary": row.get("summary"),
                "classification_status": row.get("classification_status"),
            }
        )
    return rows


def _top_theme_scores(theme_scores: pd.DataFrame) -> list[dict[str, Any]]:
    if theme_scores.empty:
        return []
    frame = theme_scores.copy()
    grouped = frame.groupby("theme_id").agg(
        count=("news_id", "count"),
        average_severity=("severity", "mean"),
        average_confidence=("confidence", "mean"),
    )
    return grouped.reset_index().sort_values(
        ["average_severity", "count"], ascending=False
    ).head(10).to_dict(orient="records")


def _top_sector_impacts(sector_impacts: pd.DataFrame) -> list[dict[str, Any]]:
    if sector_impacts.empty:
        return []
    frame = sector_impacts.copy()
    frame["absolute_impact"] = frame["impact_score"].abs()
    grouped = frame.groupby("sector_id").agg(
        count=("news_id", "count"),
        average_impact_score=("impact_score", "mean"),
        average_absolute_impact=("absolute_impact", "mean"),
        average_confidence=("confidence", "mean"),
    )
    return grouped.reset_index().sort_values(
        ["average_absolute_impact", "count"], ascending=False
    ).head(10).to_dict(orient="records")


def _high_severity(classifications: pd.DataFrame, news_items: pd.DataFrame) -> list[dict[str, Any]]:
    frame = classifications[classifications["severity"].fillna(0.0) >= 0.6]
    return _event_rows(frame, news_items)


def _low_confidence(classifications: pd.DataFrame, news_items: pd.DataFrame) -> list[dict[str, Any]]:
    frame = classifications[classifications["confidence"].fillna(0.0) <= 0.4]
    return _event_rows(frame, news_items)


def _event_rows(classifications: pd.DataFrame, news_items: pd.DataFrame) -> list[dict[str, Any]]:
    if classifications.empty:
        return []
    merged = classifications.merge(news_items, on="news_id", how="left", suffixes=("", "_item"))
    rows = []
    for row in merged.sort_values("classified_at").tail(10).to_dict(orient="records"):
        rows.append(
            {
                "news_id": row["news_id"],
                "title": row.get("title"),
                "severity": None if pd.isna(row.get("severity")) else float(row.get("severity")),
                "confidence": None if pd.isna(row.get("confidence")) else float(row.get("confidence")),
                "summary": row.get("summary"),
            }
        )
    return rows


def _assert_no_forbidden_language(markdown: str) -> None:
    lower = markdown.lower()
    violations = [term for term in FORBIDDEN_REPORT_TERMS if term in lower]
    if violations:
        raise ValueError(f"news report contains forbidden language: {violations}")


def _format_optional_score(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.2f}"


def _safe_report_text(value: Any) -> str:
    text = "" if value is None or pd.isna(value) else str(value)
    for term in sorted(FORBIDDEN_REPORT_TERMS, key=len, reverse=True):
        text = re.sub(re.escape(term), "market-action term", text, flags=re.IGNORECASE)
    return text


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
