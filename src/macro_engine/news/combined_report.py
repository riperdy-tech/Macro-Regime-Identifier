from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.news.config import load_sector_news_integration_config
from macro_engine.news.report import FORBIDDEN_REPORT_TERMS
from macro_engine.storage.duckdb_store import DuckDBStore

COMBINED_DISCLAIMER = (
    "This is an experimental diagnostic overlay, not investment advice, "
    "market action guidance, execution guidance, or instructions for changing holdings."
)


def write_combined_sector_report(
    *,
    config_path: str | Path = "config/sector_news_integration.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    config = load_sector_news_integration_config(config_path)
    store = DuckDBStore(db_path)
    payload = build_combined_sector_report(
        diagnostics=store.read_table("combined_sector_diagnostics"),
        components=store.read_table("combined_sector_diagnostic_components"),
        sector_scores=store.read_table("sector_scores"),
        news_sector_scores=store.read_table("news_daily_sector_scores")
        if config.news_score_frequency == "daily"
        else store.read_table("news_weekly_sector_scores"),
        timeline=store.read_table("historical_regime_timeline"),
    )
    markdown = combined_sector_report_markdown(payload)
    _assert_no_forbidden_language(markdown)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "combined_sector_diagnostic.json"
    markdown_path = output_dir / "combined_sector_diagnostic.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_combined_sector_report(
    *,
    diagnostics: pd.DataFrame,
    components: pd.DataFrame,
    sector_scores: pd.DataFrame,
    news_sector_scores: pd.DataFrame,
    timeline: pd.DataFrame,
) -> dict[str, Any]:
    if diagnostics.empty:
        return {
            "valid": False,
            "reason": "no_combined_sector_diagnostics",
            "disclaimer": COMBINED_DISCLAIMER,
        }
    frame = diagnostics.copy()
    frame["diagnostic_date"] = pd.to_datetime(frame["diagnostic_date"], errors="coerce")
    latest_date = frame["diagnostic_date"].max()
    latest = frame[frame["diagnostic_date"] == latest_date].sort_values("rank")
    component_frame = components.copy()
    if not component_frame.empty:
        component_frame["diagnostic_date"] = pd.to_datetime(
            component_frame["diagnostic_date"],
            errors="coerce",
        )
        component_frame = component_frame[component_frame["diagnostic_date"] == latest_date]
    warnings = []
    if (latest["news_item_count"].fillna(0) == 0).all():
        warnings.append("News coverage is thin; combined diagnostics are macro-only for this date.")
    elif latest["news_item_count"].fillna(0).min() < 1:
        warnings.append("Some sectors have thin recent news coverage.")
    context = _macro_context(timeline, sector_scores)
    return _json_safe(
        {
            "valid": True,
            "diagnostic_date": latest_date.date().isoformat(),
            "macro_context": context,
            "sector_macro_ranking": _sector_macro_ranking(sector_scores),
            "sector_news_ranking": _sector_news_ranking(news_sector_scores),
            "combined_experimental_ranking": [
                _diagnostic_record(row, component_frame) for row in latest.to_dict(orient="records")
            ],
            "warnings": warnings,
            "validation_note": (
                "Combined historical validation is blocked unless there is enough real "
                "classified news history aligned with sector proxy returns."
            ),
            "disclaimer": COMBINED_DISCLAIMER,
        }
    )


def combined_sector_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# Combined Sector Diagnostic\n\nNo valid combined diagnostics.\n\n{payload['disclaimer']}\n"
    combined = "\n".join(
        "- {rank}. {sector_id}: combined {combined_score:.3f}, macro {sector_macro_score:.3f}, "
        "news {sector_news_score:.3f}, news items {news_item_count}".format(**item)
        for item in payload["combined_experimental_ranking"]
    )
    macro = "\n".join(
        f"- {item['rank']}. {item['sector_id']}: {item['confidence_adjusted_score']:.3f}"
        for item in payload["sector_macro_ranking"][:5]
    ) or "- None"
    news = "\n".join(
        f"- {item['sector_id']}: {item['adjusted_news_score']:.3f}"
        for item in payload["sector_news_ranking"][:5]
    ) or "- None"
    warnings = "\n".join(f"- {warning}" for warning in payload["warnings"]) or "- None"
    explanations = "\n\n".join(
        _component_markdown(item) for item in payload["combined_experimental_ranking"][:3]
    )
    context = payload["macro_context"]
    return f"""# Combined Sector Diagnostic

Mode: experimental macro plus news diagnostic overlay.

Diagnostic date: {payload["diagnostic_date"]}
Reported macro regime: {context.get("reported_regime")}
Raw macro leader: {context.get("raw_dominant_regime")}
Macro confidence: {context.get("macro_confidence")}

## Combined Experimental Ranking

{combined}

## Macro-Only Ranking Snapshot

{macro}

## News-Only Ranking Snapshot

{news}

## Component Notes

{explanations}

## Warnings

{warnings}

{payload["validation_note"]}

{payload["disclaimer"]}
"""


def _diagnostic_record(row: dict, components: pd.DataFrame) -> dict[str, Any]:
    sector_components = components[components["sector_id"] == row["sector_id"]]
    return {
        "rank": int(row["rank"]),
        "sector_id": row["sector_id"],
        "sector_macro_score": _to_float(row["sector_macro_score"]),
        "sector_news_score": _to_float(row["sector_news_score"]),
        "combined_score": _to_float(row["combined_score"]),
        "macro_component_weight": _to_float(row["macro_component_weight"]),
        "news_component_weight": _to_float(row["news_component_weight"]),
        "news_item_count": int(row["news_item_count"]),
        "news_confidence": _to_float(row["news_confidence"]),
        "diagnostic_confidence": _to_float(row["diagnostic_confidence"]),
        "components": [
            {
                "component_name": item["component_name"],
                "component_value": _to_float(item["component_value"]),
                "component_weight": _to_float(item["component_weight"]),
                "rationale": item["rationale"],
            }
            for item in sector_components.to_dict(orient="records")
        ],
    }


def _macro_context(timeline: pd.DataFrame, sector_scores: pd.DataFrame) -> dict[str, Any]:
    if not timeline.empty:
        frame = timeline.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        latest = frame[frame["valid"]].sort_values("date").tail(1)
        if not latest.empty:
            row = latest.iloc[-1]
            return {
                "reported_regime": row.get("reported_regime") or row.get("dominant_regime"),
                "raw_dominant_regime": row.get("raw_dominant_regime") or row.get("dominant_regime"),
                "macro_confidence": _to_float(row.get("raw_confidence") or row.get("confidence")),
            }
    if sector_scores.empty:
        return {}
    frame = sector_scores.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    latest = frame[frame["valid"]].sort_values("date").tail(1)
    if latest.empty:
        return {}
    row = latest.iloc[-1]
    return {
        "reported_regime": row.get("macro_reported_regime"),
        "raw_dominant_regime": row.get("macro_raw_dominant_regime"),
        "macro_confidence": _to_float(row.get("macro_confidence")),
    }


def _sector_macro_ranking(sector_scores: pd.DataFrame) -> list[dict[str, Any]]:
    if sector_scores.empty:
        return []
    frame = sector_scores.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    latest_date = frame[frame["valid"]]["date"].max()
    latest = frame[(frame["date"] == latest_date) & frame["valid"]].sort_values("rank")
    return [
        {
            "rank": int(row["rank"]),
            "sector_id": row["sector_id"],
            "confidence_adjusted_score": _to_float(row["confidence_adjusted_score"]),
        }
        for row in latest.to_dict(orient="records")
    ]


def _sector_news_ranking(news_sector_scores: pd.DataFrame) -> list[dict[str, Any]]:
    if news_sector_scores.empty:
        return []
    frame = news_sector_scores.copy()
    date_column = "score_date" if "score_date" in frame.columns else "week_start_date"
    frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
    latest = frame[frame[date_column] == frame[date_column].max()].copy()
    latest = latest.sort_values(["adjusted_news_score", "sector_id"], ascending=[False, True])
    return [
        {
            "sector_id": row["sector_id"],
            "adjusted_news_score": _to_float(row["adjusted_news_score"]),
            "item_count": int(
                row.get("positive_item_count", 0)
                + row.get("negative_item_count", 0)
                + row.get("neutral_item_count", 0)
            ),
        }
        for row in latest.to_dict(orient="records")
    ]


def _component_markdown(item: dict[str, Any]) -> str:
    components = "\n".join(
        "- {component_name}: value {component_value:.3f}, weight {component_weight:.2f}".format(
            **component
        )
        for component in item["components"]
    )
    return f"""### {item["sector_id"]}

Combined score {item["combined_score"]:.3f}; diagnostic confidence {item["diagnostic_confidence"]:.3f}.

{components}
"""


def _assert_no_forbidden_language(markdown: str) -> None:
    lower = markdown.lower()
    violations = [term for term in FORBIDDEN_REPORT_TERMS if term in lower]
    if violations:
        raise ValueError(f"combined sector report contains forbidden language: {violations}")


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
