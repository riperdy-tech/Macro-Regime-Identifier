from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from macro_engine.news.combined import build_stored_combined_sector_diagnostics
from macro_engine.news.config import NewsMonitoringConfig, load_news_monitoring_config
from macro_engine.news.ingest import validate_news_input_config
from macro_engine.news.report import FORBIDDEN_REPORT_TERMS
from macro_engine.news.scoring import build_stored_news_scores
from macro_engine.news.service import classify_stored_news, ingest_stored_news
from macro_engine.storage.duckdb_store import DuckDBStore


MONITORING_DISCLAIMER = (
    "This is a diagnostic operating-quality report for news inputs, AI classifications, "
    "and the experimental combined overlay. It is not investment advice, market action "
    "guidance, execution guidance, or instructions for changing holdings."
)


@dataclass(frozen=True)
class NewsMonitoringResult:
    input_quality_runs: pd.DataFrame
    classification_quality_runs: pd.DataFrame
    overlay_monitoring: pd.DataFrame


def validate_news_monitoring_config(
    path: str | Path = "config/news_monitoring.yaml",
) -> NewsMonitoringConfig:
    return load_news_monitoring_config(path)


def run_news_monitoring(
    *,
    config_path: str | Path = "config/news_monitoring.yaml",
    source_profile: str | None = None,
    db_path: str | Path = "data/macro_engine.duckdb",
) -> NewsMonitoringResult:
    config = load_news_monitoring_config(config_path)
    profile = source_profile or config.source_profile
    store = DuckDBStore(db_path)
    store.initialize()
    run_id = _run_id()

    try:
        input_summary = validate_news_input_config(config.news_sources_config, profile=profile)
    except (FileNotFoundError, ValueError) as exc:
        input_runs = _blocked_input_run(run_id, profile, str(exc))
        classification_runs = _empty_frame(_classification_columns())
        overlay_runs = _empty_frame(_overlay_columns())
        store.upsert_news_monitoring_outputs(input_runs, classification_runs, overlay_runs)
        return NewsMonitoringResult(input_runs, classification_runs, overlay_runs)

    ingest_stored_news(config_path=config.news_sources_config, db_path=db_path, profile=profile)
    classify_stored_news(
        ai_config_path=config.news_ai_config,
        themes_config_path=config.news_themes_config,
        db_path=db_path,
    )
    build_stored_news_scores(config_path=config.news_scoring_config, db_path=db_path)
    build_stored_combined_sector_diagnostics(
        config_path=config.sector_news_integration_config,
        db_path=db_path,
    )

    input_runs = build_input_quality_run(
        config=config,
        profile=profile,
        input_summary=input_summary,
        run_id=run_id,
    )
    classification_runs = build_classification_quality_run(
        config=config,
        classifications=store.read_table("news_classifications"),
        run_id=run_id,
    )
    overlay_runs = build_overlay_monitoring_run(
        config=config,
        daily_theme_scores=store.read_table("news_daily_theme_scores"),
        daily_sector_scores=store.read_table("news_daily_sector_scores"),
        combined_diagnostics=store.read_table("combined_sector_diagnostics"),
        sector_scores=store.read_table("sector_scores"),
        run_id=run_id,
    )
    store.upsert_news_monitoring_outputs(input_runs, classification_runs, overlay_runs)
    return NewsMonitoringResult(input_runs, classification_runs, overlay_runs)


def refresh_news_monitoring_from_stored_outputs(
    *,
    config_path: str | Path = "config/news_monitoring.yaml",
    source_profile: str | None = None,
    db_path: str | Path = "data/macro_engine.duckdb",
) -> NewsMonitoringResult:
    config = load_news_monitoring_config(config_path)
    profile = source_profile or config.source_profile
    store = DuckDBStore(db_path)
    store.initialize()
    run_id = _run_id()
    try:
        input_summary = validate_news_input_config(config.news_sources_config, profile=profile)
        input_runs = build_input_quality_run(
            config=config,
            profile=profile,
            input_summary=input_summary,
            run_id=run_id,
        )
    except (FileNotFoundError, ValueError) as exc:
        input_runs = _blocked_input_run(run_id, profile, str(exc))
    classification_runs = build_classification_quality_run(
        config=config,
        classifications=store.read_table("news_classifications"),
        run_id=run_id,
    )
    overlay_runs = build_overlay_monitoring_run(
        config=config,
        daily_theme_scores=store.read_table("news_daily_theme_scores"),
        daily_sector_scores=store.read_table("news_daily_sector_scores"),
        combined_diagnostics=store.read_table("combined_sector_diagnostics"),
        sector_scores=store.read_table("sector_scores"),
        run_id=run_id,
    )
    store.upsert_news_monitoring_outputs(input_runs, classification_runs, overlay_runs)
    return NewsMonitoringResult(input_runs, classification_runs, overlay_runs)


def write_news_monitoring_report(
    *,
    config_path: str | Path = "config/news_monitoring.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    config = load_news_monitoring_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    if _monitoring_tables_empty(store):
        refresh_news_monitoring_from_stored_outputs(config_path=config_path, db_path=db_path)
    payload = build_news_monitoring_report(
        config=config,
        input_quality_runs=store.read_table("news_input_quality_runs"),
        classification_quality_runs=store.read_table("news_classification_quality_runs"),
        overlay_monitoring=store.read_table("news_overlay_monitoring"),
    )
    markdown = news_monitoring_report_markdown(payload)
    _assert_no_forbidden_language(markdown)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "news_monitoring_report.json"
    markdown_path = output_dir / "news_monitoring_report.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_input_quality_run(
    *,
    config: NewsMonitoringConfig,
    profile: str,
    input_summary: dict[str, Any],
    run_id: str,
) -> pd.DataFrame:
    sources = input_summary.get("sources", [])
    short_body_count = sum(int(source.get("short_body_count", 0)) for source in sources)
    old_item_count = sum(int(source.get("very_old_count", 0)) for source in sources)
    future_item_count = sum(int(source.get("future_published_at_count", 0)) for source in sources)
    warning_count = len(input_summary.get("warnings", []))
    source_count = len(input_summary.get("item_count_by_source", {}))
    unique_count = int(input_summary.get("unique_item_count", 0))
    duplicate_count = int(input_summary.get("duplicate_count", 0))
    details = _input_details(config, input_summary)
    status = "ok"
    if future_item_count and config.freshness_rules.reject_future_dates:
        status = "blocked"
    elif details["warnings"]:
        status = "warning"
    return pd.DataFrame(
        [
            {
                "run_id": run_id,
                "run_at": datetime.now(UTC),
                "profile": profile,
                "raw_item_count": int(input_summary.get("raw_item_count", 0)),
                "unique_item_count": unique_count,
                "duplicate_count": duplicate_count,
                "source_count": source_count,
                "date_min": input_summary.get("date_start"),
                "date_max": input_summary.get("date_end"),
                "short_body_count": short_body_count,
                "old_item_count": old_item_count,
                "future_item_count": future_item_count,
                "warning_count": warning_count + len(details["warnings"]),
                "quality_status": status,
                "details_json": json.dumps(details, sort_keys=True),
            }
        ],
        columns=_input_columns(),
    )


def build_classification_quality_run(
    *,
    config: NewsMonitoringConfig,
    classifications: pd.DataFrame,
    run_id: str,
) -> pd.DataFrame:
    if classifications.empty:
        return pd.DataFrame(
            [
                {
                    "run_id": run_id,
                    "run_at": datetime.now(UTC),
                    "total_items": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "success_rate": 0.0,
                    "retry_count": 0,
                    "retry_rate": 0.0,
                    "repaired_count": 0,
                    "repair_rate": 0.0,
                    "provider": None,
                    "model": None,
                    "top_failure_modes_json": "[]",
                    "quality_status": "blocked",
                    "details_json": json.dumps({"warnings": ["no stored classifications"]}),
                }
            ],
            columns=_classification_columns(),
        )
    frame = classifications.copy()
    total = len(frame)
    success_count = int((frame["classification_status"] == "success").sum())
    failure_count = total - success_count
    success_rate = success_count / total if total else 0.0
    metadata = [_raw_metadata(value) for value in frame.get("raw_ai_response_json", [])]
    retry_count = sum(int(item.get("retry_count", 0) or 0) for item in metadata)
    repaired_count = sum(1 for item in metadata if bool(item.get("was_repaired", False)))
    retry_rate = retry_count / total if total else 0.0
    repair_rate = repaired_count / total if total else 0.0
    failure_modes = _failure_modes(frame, metadata)
    warnings = []
    thresholds = config.quality_thresholds
    if (failure_count / total if total else 0.0) > thresholds.max_failed_classification_rate:
        warnings.append("classification failure rate exceeds configured threshold")
    if retry_rate > thresholds.max_retry_rate:
        warnings.append("retry rate exceeds configured threshold")
    if repair_rate > thresholds.max_repair_rate:
        warnings.append("repair rate exceeds configured threshold")
    status = "ok" if not warnings else "warning"
    return pd.DataFrame(
        [
            {
                "run_id": run_id,
                "run_at": datetime.now(UTC),
                "total_items": total,
                "success_count": success_count,
                "failure_count": failure_count,
                "success_rate": success_rate,
                "retry_count": retry_count,
                "retry_rate": retry_rate,
                "repaired_count": repaired_count,
                "repair_rate": repair_rate,
                "provider": _last_non_null(frame, "ai_provider"),
                "model": _last_non_null(frame, "ai_model"),
                "top_failure_modes_json": json.dumps(failure_modes, sort_keys=True),
                "quality_status": status,
                "details_json": json.dumps({"warnings": warnings}, sort_keys=True),
            }
        ],
        columns=_classification_columns(),
    )


def build_overlay_monitoring_run(
    *,
    config: NewsMonitoringConfig,
    daily_theme_scores: pd.DataFrame,
    daily_sector_scores: pd.DataFrame,
    combined_diagnostics: pd.DataFrame,
    sector_scores: pd.DataFrame,
    run_id: str,
) -> pd.DataFrame:
    if combined_diagnostics.empty:
        return pd.DataFrame(
            [
                {
                    "run_id": run_id,
                    "run_at": datetime.now(UTC),
                    "diagnostic_date": None,
                    "top_news_themes_json": "[]",
                    "top_sector_tailwinds_json": "[]",
                    "top_sector_headwinds_json": "[]",
                    "combined_top_sectors_json": "[]",
                    "macro_only_top_sectors_json": "[]",
                    "sectors_changed_by_news_json": "[]",
                    "max_rank_change": 0,
                    "avg_abs_rank_change": 0.0,
                    "news_item_count": 0,
                    "thin_news_warning": True,
                    "overlay_status": "blocked",
                }
            ],
            columns=_overlay_columns(),
        )
    combined = combined_diagnostics.copy()
    combined["diagnostic_date"] = pd.to_datetime(combined["diagnostic_date"], errors="coerce")
    latest_date = combined["diagnostic_date"].max()
    latest_combined = combined[combined["diagnostic_date"] == latest_date].sort_values("rank")
    macro_latest = _latest_macro_ranking(sector_scores)
    rank_changes = _rank_changes(latest_combined, macro_latest)
    max_rank_change = max((abs(row["rank_change"]) for row in rank_changes), default=0)
    avg_rank_change = (
        sum(abs(row["rank_change"]) for row in rank_changes) / len(rank_changes)
        if rank_changes
        else 0.0
    )
    news_item_count = int(latest_combined["news_item_count"].fillna(0).sum())
    thin_news_warning = news_item_count == 0 or latest_combined["news_item_count"].fillna(0).min() < 1
    warnings = _overlay_warnings(
        config=config,
        daily_theme_scores=daily_theme_scores,
        daily_sector_scores=daily_sector_scores,
        latest_combined=latest_combined,
        max_rank_change=max_rank_change,
        avg_rank_change=avg_rank_change,
    )
    status = "ok" if not warnings else "warning"
    return pd.DataFrame(
        [
            {
                "run_id": run_id,
                "run_at": datetime.now(UTC),
                "diagnostic_date": latest_date.date() if pd.notna(latest_date) else None,
                "top_news_themes_json": json.dumps(
                    _top_news_themes(daily_theme_scores), sort_keys=True
                ),
                "top_sector_tailwinds_json": json.dumps(
                    _top_sector_scores(daily_sector_scores, positive=True), sort_keys=True
                ),
                "top_sector_headwinds_json": json.dumps(
                    _top_sector_scores(daily_sector_scores, positive=False), sort_keys=True
                ),
                "combined_top_sectors_json": json.dumps(
                    _combined_top_sectors(latest_combined), sort_keys=True
                ),
                "macro_only_top_sectors_json": json.dumps(macro_latest[:5], sort_keys=True),
                "sectors_changed_by_news_json": json.dumps(rank_changes, sort_keys=True),
                "max_rank_change": int(max_rank_change),
                "avg_abs_rank_change": float(avg_rank_change),
                "news_item_count": news_item_count,
                "thin_news_warning": bool(thin_news_warning),
                "overlay_status": status,
            }
        ],
        columns=_overlay_columns(),
    )


def build_news_monitoring_report(
    *,
    config: NewsMonitoringConfig,
    input_quality_runs: pd.DataFrame,
    classification_quality_runs: pd.DataFrame,
    overlay_monitoring: pd.DataFrame,
) -> dict[str, Any]:
    input_row = _latest_record(input_quality_runs, "run_at")
    classification_row = _latest_record(classification_quality_runs, "run_at")
    overlay_row = _latest_record(overlay_monitoring, "run_at")
    return _json_safe(
        {
            "valid": input_row is not None or classification_row is not None or overlay_row is not None,
            "input_quality": _record_with_json(input_row, ["details_json"]),
            "classification_quality": _record_with_json(
                classification_row,
                ["top_failure_modes_json", "details_json"],
            ),
            "overlay_monitoring": _record_with_json(
                overlay_row,
                [
                    "top_news_themes_json",
                    "top_sector_tailwinds_json",
                    "top_sector_headwinds_json",
                    "combined_top_sectors_json",
                    "macro_only_top_sectors_json",
                    "sectors_changed_by_news_json",
                ],
            ),
            "source_groups": [group.model_dump() for group in config.source_groups],
            "calibration_note": (
                "News scoring calibration remains deferred until balanced, time-consistent "
                "real-news history exists."
            ),
            "disclaimer": MONITORING_DISCLAIMER,
        }
    )


def news_monitoring_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# News Monitoring Report\n\nNo monitoring rows are available.\n\n{payload['disclaimer']}\n"
    input_quality = payload.get("input_quality") or {}
    classification = payload.get("classification_quality") or {}
    overlay = payload.get("overlay_monitoring") or {}
    top_themes = _markdown_list(overlay.get("top_news_themes_json", []), "id", "score")
    tailwinds = _markdown_list(overlay.get("top_sector_tailwinds_json", []), "id", "score")
    headwinds = _markdown_list(overlay.get("top_sector_headwinds_json", []), "id", "score")
    combined = _markdown_list(overlay.get("combined_top_sectors_json", []), "sector_id", "combined_score")
    changes = _markdown_rank_changes(overlay.get("sectors_changed_by_news_json", []))
    return f"""# News Monitoring Report

Mode: real-news input, classification, and overlay stability monitoring.

## Input Quality

- Profile: {input_quality.get("profile")}
- Raw items: {input_quality.get("raw_item_count")}
- Unique items: {input_quality.get("unique_item_count")}
- Source count: {input_quality.get("source_count")}
- Date range: {input_quality.get("date_min")} to {input_quality.get("date_max")}
- Warning count: {input_quality.get("warning_count")}
- Quality status: {input_quality.get("quality_status")}

## Classification Quality

- Total items: {classification.get("total_items")}
- Success rate: {_format_percent(classification.get("success_rate"))}
- Retry rate: {_format_percent(classification.get("retry_rate"))}
- Repair rate: {_format_percent(classification.get("repair_rate"))}
- Provider/model: {classification.get("provider")} / {classification.get("model")}
- Quality status: {classification.get("quality_status")}

## News Themes And Sector Impacts

Top news themes:
{top_themes}

Sector diagnostic tailwinds:
{tailwinds}

Sector diagnostic headwinds:
{headwinds}

## Combined Overlay Stability

- Diagnostic date: {overlay.get("diagnostic_date")}
- News item count: {overlay.get("news_item_count")}
- Max rank change: {overlay.get("max_rank_change")}
- Average absolute rank change: {_format_float(overlay.get("avg_abs_rank_change"))}
- Thin news warning: {overlay.get("thin_news_warning")}
- Overlay status: {overlay.get("overlay_status")}

Combined top sectors:
{combined}

Rank changes caused by news overlay:
{changes}

## Calibration Decision

{payload["calibration_note"]}

{payload["disclaimer"]}
"""


def _input_details(config: NewsMonitoringConfig, input_summary: dict[str, Any]) -> dict[str, Any]:
    warnings = list(input_summary.get("warnings", []))
    by_source = input_summary.get("item_count_by_source", {})
    by_group = input_summary.get("item_count_by_source_group", {})
    by_day = input_summary.get("item_count_by_day", {})
    unique_count = int(input_summary.get("unique_item_count", 0))
    source_count = len(by_source)
    source_group_count = int(input_summary.get("source_group_count", 0))
    unmapped_pct = float(input_summary.get("unmapped_pct", 0.0) or 0.0)
    if source_count < config.quality_thresholds.min_source_count:
        warnings.append("source count is below configured minimum")
    if source_group_count < config.quality_thresholds.min_source_groups and unique_count:
        warnings.append("source group count is below configured minimum")
    if by_source and _max_share(by_source) > config.quality_thresholds.max_source_share:
        warnings.append("one source exceeds configured concentration threshold")
    if by_group and _max_share(by_group) > config.quality_thresholds.max_single_group_pct:
        warnings.append("one source group exceeds configured concentration threshold")
    if unmapped_pct > config.quality_thresholds.max_unmapped_pct:
        warnings.append("unmapped source-group share exceeds configured threshold")
    if by_day and _max_share(by_day) > config.quality_thresholds.max_date_share:
        warnings.append("one date exceeds configured concentration threshold")
    if unique_count:
        old_count = sum(int(source.get("very_old_count", 0)) for source in input_summary.get("sources", []))
        if old_count / unique_count > config.quality_thresholds.max_old_item_share:
            warnings.append("old item share exceeds configured threshold")
    date_min = pd.to_datetime(input_summary.get("date_start"), errors="coerce", utc=True)
    date_max = pd.to_datetime(input_summary.get("date_end"), errors="coerce", utc=True)
    if pd.notna(date_min) and pd.notna(date_max):
        coverage_days = max(1, int((date_max - date_min).days) + 1)
        if coverage_days < config.quality_thresholds.min_date_coverage_days:
            warnings.append("date coverage is below configured minimum")
    return {
        "warnings": warnings,
        "item_count_by_source": by_source,
        "item_count_by_source_group": by_group,
        "source_group_count": source_group_count,
        "unmapped_item_count": input_summary.get("unmapped_item_count", 0),
        "unmapped_pct": unmapped_pct,
        "item_count_by_day": by_day,
    }


def _overlay_warnings(
    *,
    config: NewsMonitoringConfig,
    daily_theme_scores: pd.DataFrame,
    daily_sector_scores: pd.DataFrame,
    latest_combined: pd.DataFrame,
    max_rank_change: int,
    avg_rank_change: float,
) -> list[str]:
    warnings = []
    if max_rank_change > config.quality_thresholds.max_rank_change:
        warnings.append("news overlay max rank change exceeds configured threshold")
    if avg_rank_change > config.quality_thresholds.max_avg_abs_rank_change:
        warnings.append("news overlay average rank change exceeds configured threshold")
    if _score_share(daily_theme_scores, "adjusted_score") > config.quality_thresholds.max_theme_share:
        warnings.append("one theme dominates latest news scores")
    if _score_share(daily_sector_scores, "adjusted_news_score") > config.quality_thresholds.max_sector_share:
        warnings.append("one sector dominates latest news scores")
    if not latest_combined.empty and latest_combined["news_item_count"].fillna(0).sum() == 0:
        warnings.append("no recent news contributes to latest combined diagnostic")
    return warnings


def _top_news_themes(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    latest = _latest_rows(frame, "score_date")
    latest = latest.sort_values(["adjusted_score", "theme_id"], ascending=[False, True]).head(5)
    return [
        {
            "id": row["theme_id"],
            "score": _to_float(row["adjusted_score"]),
            "item_count": int(row["item_count"]),
        }
        for row in latest.to_dict(orient="records")
    ]


def _top_sector_scores(frame: pd.DataFrame, *, positive: bool) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    latest = _latest_rows(frame, "score_date")
    if positive:
        latest = latest[latest["adjusted_news_score"] > 0]
        latest = latest.sort_values(["adjusted_news_score", "sector_id"], ascending=[False, True])
    else:
        latest = latest[latest["adjusted_news_score"] < 0]
        latest = latest.sort_values(["adjusted_news_score", "sector_id"], ascending=[True, True])
    return [
        {
            "id": row["sector_id"],
            "score": _to_float(row["adjusted_news_score"]),
            "item_count": int(
                row.get("positive_item_count", 0)
                + row.get("negative_item_count", 0)
                + row.get("neutral_item_count", 0)
            ),
        }
        for row in latest.head(5).to_dict(orient="records")
    ]


def _combined_top_sectors(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "rank": int(row["rank"]),
            "sector_id": row["sector_id"],
            "combined_score": _to_float(row["combined_score"]),
            "news_item_count": int(row["news_item_count"]),
        }
        for row in frame.sort_values("rank").head(5).to_dict(orient="records")
    ]


def _latest_macro_ranking(sector_scores: pd.DataFrame) -> list[dict[str, Any]]:
    if sector_scores.empty:
        return []
    frame = sector_scores.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if "valid" in frame:
        frame = frame[frame["valid"]].copy()
    if frame.empty:
        return []
    latest = frame[frame["date"] == frame["date"].max()].sort_values("rank")
    return [
        {
            "rank": int(row["rank"]),
            "sector_id": row["sector_id"],
            "confidence_adjusted_score": _to_float(row["confidence_adjusted_score"]),
        }
        for row in latest.to_dict(orient="records")
    ]


def _rank_changes(combined: pd.DataFrame, macro: list[dict[str, Any]]) -> list[dict[str, Any]]:
    macro_ranks = {row["sector_id"]: int(row["rank"]) for row in macro}
    changes = []
    for row in combined.to_dict(orient="records"):
        sector_id = row["sector_id"]
        macro_rank = macro_ranks.get(sector_id)
        combined_rank = int(row["rank"])
        if macro_rank is None:
            continue
        change = macro_rank - combined_rank
        if change != 0:
            changes.append(
                {
                    "sector_id": sector_id,
                    "macro_rank": macro_rank,
                    "combined_rank": combined_rank,
                    "rank_change": change,
                }
            )
    return sorted(changes, key=lambda row: (-abs(row["rank_change"]), row["sector_id"]))


def _failure_modes(frame: pd.DataFrame, metadata: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for error in frame["error_message"].dropna().astype(str):
        key = error.split("\n", 1)[0][:120]
        counts[key] = counts.get(key, 0) + 1
    for item in metadata:
        for error in item.get("validation_errors", []) or []:
            key = str(error).split("\n", 1)[0][:120]
            counts[key] = counts.get(key, 0) + 1
    return [
        {"failure_mode": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]


def _monitoring_tables_empty(store: DuckDBStore) -> bool:
    return (
        store.read_table("news_input_quality_runs").empty
        and store.read_table("news_classification_quality_runs").empty
        and store.read_table("news_overlay_monitoring").empty
    )


def _blocked_input_run(run_id: str, profile: str, reason: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": run_id,
                "run_at": datetime.now(UTC),
                "profile": profile,
                "raw_item_count": 0,
                "unique_item_count": 0,
                "duplicate_count": 0,
                "source_count": 0,
                "date_min": None,
                "date_max": None,
                "short_body_count": 0,
                "old_item_count": 0,
                "future_item_count": 0,
                "warning_count": 1,
                "quality_status": "blocked",
                "details_json": json.dumps({"warnings": [reason]}, sort_keys=True),
            }
        ],
        columns=_input_columns(),
    )


def _latest_rows(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    result[column] = pd.to_datetime(result[column], errors="coerce")
    latest = result[column].max()
    return result[result[column] == latest].copy()


def _score_share(frame: pd.DataFrame, column: str) -> float:
    if frame.empty:
        return 0.0
    latest = _latest_rows(frame, "score_date")
    values = pd.to_numeric(latest[column], errors="coerce").abs()
    total = float(values.sum())
    if total == 0.0:
        return 0.0
    return float(values.max() / total)


def _max_share(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    return 0.0 if total == 0 else max(counts.values()) / total


def _raw_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None or pd.isna(value):
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _latest_record(frame: pd.DataFrame, date_column: str) -> dict[str, Any] | None:
    if frame.empty:
        return None
    result = frame.copy()
    result[date_column] = pd.to_datetime(result[date_column], errors="coerce")
    return result.sort_values(date_column).tail(1).iloc[-1].to_dict()


def _record_with_json(row: dict[str, Any] | None, json_columns: list[str]) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for column in json_columns:
        value = result.get(column)
        if isinstance(value, str):
            try:
                result[column] = json.loads(value)
            except json.JSONDecodeError:
                result[column] = value
    return result


def _markdown_list(items: Any, id_key: str, score_key: str) -> str:
    if not items:
        return "- None"
    return "\n".join(
        f"- {item.get(id_key)}: {_format_float(item.get(score_key))}"
        for item in items
    )


def _markdown_rank_changes(items: Any) -> str:
    if not items:
        return "- None"
    return "\n".join(
        "- {sector_id}: macro rank {macro_rank}, combined rank {combined_rank}, change {rank_change}".format(
            **item
        )
        for item in items
    )


def _last_non_null(frame: pd.DataFrame, column: str) -> Any:
    if column not in frame:
        return None
    values = frame[column].dropna()
    return None if values.empty else values.iloc[-1]


def _format_percent(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.1%}"


def _format_float(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.3f}"


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


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


def _assert_no_forbidden_language(markdown: str) -> None:
    lower = markdown.lower()
    violations = [term for term in FORBIDDEN_REPORT_TERMS if term in lower]
    if violations:
        raise ValueError(f"news monitoring report contains forbidden language: {violations}")


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def _input_columns() -> list[str]:
    return [
        "run_id",
        "run_at",
        "profile",
        "raw_item_count",
        "unique_item_count",
        "duplicate_count",
        "source_count",
        "date_min",
        "date_max",
        "short_body_count",
        "old_item_count",
        "future_item_count",
        "warning_count",
        "quality_status",
        "details_json",
    ]


def _classification_columns() -> list[str]:
    return [
        "run_id",
        "run_at",
        "total_items",
        "success_count",
        "failure_count",
        "success_rate",
        "retry_count",
        "retry_rate",
        "repaired_count",
        "repair_rate",
        "provider",
        "model",
        "top_failure_modes_json",
        "quality_status",
        "details_json",
    ]


def _overlay_columns() -> list[str]:
    return [
        "run_id",
        "run_at",
        "diagnostic_date",
        "top_news_themes_json",
        "top_sector_tailwinds_json",
        "top_sector_headwinds_json",
        "combined_top_sectors_json",
        "macro_only_top_sectors_json",
        "sectors_changed_by_news_json",
        "max_rank_change",
        "avg_abs_rank_change",
        "news_item_count",
        "thin_news_warning",
        "overlay_status",
    ]
