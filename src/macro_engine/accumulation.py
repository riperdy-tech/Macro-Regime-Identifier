from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from macro_engine.operations_config import (
    NewsAccumulationConfig,
    load_news_accumulation_config,
)
from macro_engine.storage.duckdb_store import DuckDBStore


ACCUMULATION_DISCLAIMER = (
    "This is a diagnostic history report for news classification coverage and combined "
    "overlay behavior. It is not investment advice, market action guidance, execution "
    "guidance, or instructions for changing holdings."
)


@dataclass(frozen=True)
class NewsAccumulationResult:
    runs: pd.DataFrame
    news_history: pd.DataFrame
    combined_history: pd.DataFrame
    readiness_label: str


def run_news_accumulation(
    *,
    config_path: str | Path = "config/news_accumulation.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    run_date: str | date | None = None,
) -> NewsAccumulationResult:
    config = load_news_accumulation_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    result = build_news_accumulation_outputs(
        config=config,
        news_items=store.read_table("news_items"),
        classifications=store.read_table("news_classifications"),
        daily_theme_scores=store.read_table("news_daily_theme_scores"),
        daily_sector_scores=store.read_table("news_daily_sector_scores"),
        combined_diagnostics=store.read_table("combined_sector_diagnostics"),
        sector_scores=store.read_table("sector_scores"),
        run_date=_coerce_run_date(run_date),
    )
    store.upsert_news_accumulation_outputs(
        result.runs,
        result.news_history,
        result.combined_history,
    )
    return result


def build_news_accumulation_outputs(
    *,
    config: NewsAccumulationConfig,
    news_items: pd.DataFrame,
    classifications: pd.DataFrame,
    daily_theme_scores: pd.DataFrame,
    daily_sector_scores: pd.DataFrame,
    combined_diagnostics: pd.DataFrame,
    sector_scores: pd.DataFrame,
    run_date: date,
) -> NewsAccumulationResult:
    created_at = datetime.now(UTC)
    runs = _accumulation_run_frame(
        config=config,
        news_items=news_items,
        classifications=classifications,
        run_date=run_date,
        created_at=created_at,
    )
    news_history = _news_score_history_frame(
        daily_theme_scores,
        daily_sector_scores,
        classifications,
        created_at,
    )
    combined_history = _combined_history_frame(combined_diagnostics, sector_scores, created_at)
    readiness = readiness_label(
        run_dates=_run_date_count(classifications),
        classified_items=_success_count(classifications),
        source_count=_source_count(news_items),
    )
    return NewsAccumulationResult(runs, news_history, combined_history, readiness)


def write_news_accumulation_report(
    *,
    config_path: str | Path = "config/news_accumulation.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    config = load_news_accumulation_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    payload = build_news_accumulation_report(
        config=config,
        runs=store.read_table("news_accumulation_runs"),
        news_history=store.read_table("news_score_history_summary"),
        combined_history=store.read_table("combined_diagnostic_history_summary"),
        news_items=store.read_table("news_items"),
        classifications=store.read_table("news_classifications"),
    )
    markdown = news_accumulation_report_markdown(payload)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "news_accumulation_report.json"
    markdown_path = output_dir / "news_accumulation_report.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_news_accumulation_report(
    *,
    config: NewsAccumulationConfig,
    runs: pd.DataFrame,
    news_history: pd.DataFrame,
    combined_history: pd.DataFrame,
    news_items: pd.DataFrame,
    classifications: pd.DataFrame,
) -> dict[str, Any]:
    classified_items = _success_count(classifications)
    label = readiness_label(
        run_dates=_run_date_count(classifications),
        classified_items=classified_items,
        source_count=_source_count(news_items),
    )
    dates = _date_range(news_items, "published_at")
    latest_run = {} if runs.empty else _json_safe(runs.sort_values("created_at").tail(1).iloc[-1].to_dict())
    return _json_safe(
        {
            "valid": True,
            "readiness_label": label,
            "total_accumulated_news_items": int(len(news_items)),
            "total_classified_items": classified_items,
            "classification_success_rate": _classification_success_rate(classifications),
            "date_start": dates[0],
            "date_end": dates[1],
            "source_count": _source_count(news_items),
            "source_group_count": _source_group_count(news_items),
            "daily_item_counts": _daily_item_counts(news_items),
            "top_recurring_macro_themes": _top_history(news_history, "top_themes_json"),
            "top_recurring_sector_tailwinds": _top_history(news_history, "top_sector_tailwinds_json"),
            "top_recurring_sector_headwinds": _top_history(news_history, "top_sector_headwinds_json"),
            "combined_rank_change_history": _combined_rank_history(combined_history),
            "latest_run": latest_run,
            "history_thresholds": {
                "insufficient_history": "fewer than 5 run dates or fewer than 100 classified items",
                "early_history": "5 to 20 run dates",
                "monitor_ready": "20+ run dates with reasonable source coverage",
                "validation_candidate": "60+ run dates with stable source coverage",
            },
            "validation_note": (
                "Do not treat the news overlay as empirically validated until enough "
                "balanced real-news history exists."
            ),
            "disclaimer": ACCUMULATION_DISCLAIMER,
        }
    )


def news_accumulation_report_markdown(payload: dict[str, Any]) -> str:
    return f"""# News Accumulation Report

Readiness label: {payload["readiness_label"]}

## Coverage

- Total news items: {payload["total_accumulated_news_items"]}
- Total classified items: {payload["total_classified_items"]}
- Classification success rate: {_fmt(payload["classification_success_rate"])}
- Date range: {payload["date_start"]} to {payload["date_end"]}
- Source count: {payload["source_count"]}
- Source group count: {payload["source_group_count"]}

## Recurring Diagnostics

Top recurring macro themes:
{_history_lines(payload["top_recurring_macro_themes"])}

Top recurring sector diagnostic tailwinds:
{_history_lines(payload["top_recurring_sector_tailwinds"])}

Top recurring sector diagnostic headwinds:
{_history_lines(payload["top_recurring_sector_headwinds"])}

## Combined Overlay History

{_rank_history_lines(payload["combined_rank_change_history"])}

## Validation Readiness

{payload["validation_note"]}

{payload["disclaimer"]}
"""


def readiness_label(*, run_dates: int, classified_items: int, source_count: int = 0) -> str:
    if run_dates < 5 or classified_items < 100:
        return "insufficient_history"
    if run_dates < 20:
        return "early_history"
    if run_dates < 60 or source_count < 3:
        return "monitor_ready"
    return "validation_candidate"


def _accumulation_run_frame(
    *,
    config: NewsAccumulationConfig,
    news_items: pd.DataFrame,
    classifications: pd.DataFrame,
    run_date: date,
    created_at: datetime,
) -> pd.DataFrame:
    dates = _date_range(news_items, "published_at")
    raw_count = len(news_items)
    unique_count = news_items["content_hash"].nunique() if "content_hash" in news_items else raw_count
    classified = _success_count(classifications)
    failed = _failure_count(classifications)
    success_rate = _classification_success_rate(classifications)
    warnings = []
    if raw_count < config.min_items_per_run:
        warnings.append("raw item count below configured minimum")
    if _source_count(news_items) < config.min_source_count:
        warnings.append("source count below configured minimum")
    if success_rate < config.quality_status_thresholds.min_success_rate:
        warnings.append("classification success rate below configured minimum")
    status = "ok" if not warnings else "warning"
    return pd.DataFrame(
        [
            {
                "run_id": _run_id(created_at),
                "run_date": run_date,
                "raw_item_count": raw_count,
                "new_unique_items": int(unique_count),
                "duplicate_items": int(raw_count - unique_count),
                "classified_items": classified,
                "failed_items": failed,
                "success_rate": success_rate,
                "source_count": _source_count(news_items),
                "source_group_count": _source_group_count(news_items),
                "date_min": dates[0],
                "date_max": dates[1],
                "quality_status": status,
                "warning_json": json.dumps(warnings, sort_keys=True),
                "created_at": created_at,
            }
        ]
    )


def _news_score_history_frame(
    themes: pd.DataFrame,
    sectors: pd.DataFrame,
    classifications: pd.DataFrame,
    created_at: datetime,
) -> pd.DataFrame:
    if themes.empty and sectors.empty:
        return pd.DataFrame(columns=_news_history_columns())
    theme = _with_date(themes, "score_date")
    sector = _with_date(sectors, "score_date")
    dates = sorted(set(theme.get("score_date", pd.Series(dtype="datetime64[ns]")).dropna()) | set(sector.get("score_date", pd.Series(dtype="datetime64[ns]")).dropna()))
    rows = []
    for score_date in dates:
        date_theme = theme[theme["score_date"] == score_date] if not theme.empty else theme
        date_sector = sector[sector["score_date"] == score_date] if not sector.empty else sector
        rows.append(
            {
                "score_date": score_date.date(),
                "theme_count": int(len(date_theme)),
                "sector_count": int(len(date_sector)),
                "top_themes_json": json.dumps(_top_rows(date_theme, "theme_id", "adjusted_score", False)),
                "top_sector_tailwinds_json": json.dumps(
                    _top_rows(date_sector[date_sector["adjusted_news_score"] > 0], "sector_id", "adjusted_news_score", False)
                    if not date_sector.empty
                    else []
                ),
                "top_sector_headwinds_json": json.dumps(
                    _top_rows(date_sector[date_sector["adjusted_news_score"] < 0], "sector_id", "adjusted_news_score", True)
                    if not date_sector.empty
                    else []
                ),
                "classification_count": int(len(classifications)),
                "avg_confidence": _avg(classifications, "confidence"),
                "avg_severity": _avg(classifications, "severity"),
                "created_at": created_at,
            }
        )
    return pd.DataFrame(rows, columns=_news_history_columns())


def _combined_history_frame(
    combined: pd.DataFrame,
    sector_scores: pd.DataFrame,
    created_at: datetime,
) -> pd.DataFrame:
    if combined.empty:
        return pd.DataFrame(columns=_combined_history_columns())
    frame = _with_date(combined, "diagnostic_date")
    macro = _latest_macro_ranks(sector_scores)
    rows = []
    for diagnostic_date in sorted(frame["diagnostic_date"].dropna().unique()):
        latest = frame[frame["diagnostic_date"] == diagnostic_date].sort_values("rank")
        changes = _rank_changes(latest, macro)
        max_change = max((abs(row["rank_change"]) for row in changes), default=0)
        avg_change = sum(abs(row["rank_change"]) for row in changes) / len(changes) if changes else 0.0
        rows.append(
            {
                "diagnostic_date": diagnostic_date.date(),
                "top_combined_sectors_json": json.dumps(_combined_top(latest, "combined_score")),
                "top_macro_only_sectors_json": json.dumps(macro[:5]),
                "top_news_only_sectors_json": json.dumps(_combined_top(latest, "sector_news_score")),
                "max_rank_change": int(max_change),
                "avg_abs_rank_change": float(avg_change),
                "news_item_count": int(latest["news_item_count"].fillna(0).sum()),
                "thin_news_warning": bool(latest["news_item_count"].fillna(0).min() < 1),
                "created_at": created_at,
            }
        )
    return pd.DataFrame(rows, columns=_combined_history_columns())


def _top_rows(frame: pd.DataFrame, id_column: str, score_column: str, ascending: bool) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    ranked = frame.sort_values([score_column, id_column], ascending=[ascending, True]).head(5)
    return [{"id": row[id_column], "score": float(row[score_column])} for row in ranked.to_dict(orient="records")]


def _combined_top(frame: pd.DataFrame, score_column: str) -> list[dict[str, Any]]:
    ranked = frame.sort_values([score_column, "sector_id"], ascending=[False, True]).head(5)
    return [{"sector_id": row["sector_id"], "score": float(row[score_column])} for row in ranked.to_dict(orient="records")]


def _latest_macro_ranks(sector_scores: pd.DataFrame) -> list[dict[str, Any]]:
    if sector_scores.empty:
        return []
    frame = _with_date(sector_scores, "date")
    if "valid" in frame:
        frame = frame[frame["valid"]].copy()
    if frame.empty:
        return []
    latest = frame[frame["date"] == frame["date"].max()].sort_values("rank")
    return [{"sector_id": row["sector_id"], "rank": int(row["rank"])} for row in latest.to_dict(orient="records")]


def _rank_changes(combined: pd.DataFrame, macro: list[dict[str, Any]]) -> list[dict[str, Any]]:
    macro_ranks = {row["sector_id"]: row["rank"] for row in macro}
    changes = []
    for row in combined.to_dict(orient="records"):
        macro_rank = macro_ranks.get(row["sector_id"])
        if macro_rank is None:
            continue
        change = int(macro_rank) - int(row["rank"])
        if change:
            changes.append({"sector_id": row["sector_id"], "rank_change": change})
    return changes


def _top_history(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    if frame.empty or column not in frame:
        return []
    for value in frame[column].dropna().astype(str):
        try:
            items = json.loads(value)
        except json.JSONDecodeError:
            continue
        for item in items:
            item_id = item.get("id") or item.get("sector_id")
            if item_id:
                counts[item_id] = counts.get(item_id, 0) + 1
    return [
        {"id": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]


def _combined_rank_history(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    result = _with_date(frame, "diagnostic_date").sort_values("diagnostic_date").tail(10)
    return [
        {
            "diagnostic_date": row["diagnostic_date"].date().isoformat(),
            "max_rank_change": int(row["max_rank_change"]),
            "news_item_count": int(row["news_item_count"]),
            "thin_news_warning": bool(row["thin_news_warning"]),
        }
        for row in result.to_dict(orient="records")
    ]


def _history_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item['id']}: {item['count']} dates" for item in items)


def _rank_history_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- None"
    return "\n".join(
        "- {diagnostic_date}: max rank change {max_rank_change}, news items {news_item_count}".format(**item)
        for item in items
    )


def _with_date(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    result[column] = pd.to_datetime(result[column], errors="coerce")
    return result


def _coerce_run_date(value: str | date | None) -> date:
    if value is None:
        return datetime.now(UTC).date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _date_range(frame: pd.DataFrame, column: str) -> tuple[str | None, str | None]:
    if frame.empty or column not in frame:
        return None, None
    dates = pd.to_datetime(frame[column], errors="coerce").dropna()
    if dates.empty:
        return None, None
    return dates.min().isoformat(), dates.max().isoformat()


def _daily_item_counts(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty or "published_at" not in frame:
        return {}
    result = _with_date(frame, "published_at")
    return {
        key.isoformat(): int(value)
        for key, value in result["published_at"].dt.date.value_counts().sort_index().items()
    }


def _success_count(classifications: pd.DataFrame) -> int:
    if classifications.empty or "classification_status" not in classifications:
        return 0
    return int((classifications["classification_status"] == "success").sum())


def _failure_count(classifications: pd.DataFrame) -> int:
    if classifications.empty or "classification_status" not in classifications:
        return 0
    return int((classifications["classification_status"] != "success").sum())


def _classification_success_rate(classifications: pd.DataFrame) -> float:
    total = len(classifications)
    return 0.0 if total == 0 else _success_count(classifications) / total


def _source_count(news_items: pd.DataFrame) -> int:
    if news_items.empty or "source" not in news_items:
        return 0
    return int(news_items["source"].nunique())


def _source_group_count(news_items: pd.DataFrame) -> int:
    if news_items.empty:
        return 0
    metadata_values = news_items.get("raw_metadata_json")
    if metadata_values is None:
        return 0
    groups = set()
    for value in metadata_values.dropna().astype(str):
        try:
            metadata = json.loads(value)
        except json.JSONDecodeError:
            continue
        group = metadata.get("source_group") or metadata.get("query_group")
        if group:
            groups.add(str(group))
    return len(groups)


def _run_date_count(classifications: pd.DataFrame) -> int:
    if classifications.empty or "classified_at" not in classifications:
        return 0
    dates = pd.to_datetime(classifications["classified_at"], errors="coerce").dropna()
    return int(dates.dt.date.nunique())


def _avg(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return None if values.empty else float(values.mean())


def _fmt(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.3f}"


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


def _run_id(created_at: datetime) -> str:
    return f"{created_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def _news_history_columns() -> list[str]:
    return [
        "score_date",
        "theme_count",
        "sector_count",
        "top_themes_json",
        "top_sector_tailwinds_json",
        "top_sector_headwinds_json",
        "classification_count",
        "avg_confidence",
        "avg_severity",
        "created_at",
    ]


def _combined_history_columns() -> list[str]:
    return [
        "diagnostic_date",
        "top_combined_sectors_json",
        "top_macro_only_sectors_json",
        "top_news_only_sectors_json",
        "max_rank_change",
        "avg_abs_rank_change",
        "news_item_count",
        "thin_news_warning",
        "created_at",
    ]
