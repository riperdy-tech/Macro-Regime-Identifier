from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
import shutil
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from macro_engine.guardrails import audit_markdown_reports
from macro_engine.news.combined import build_stored_combined_sector_diagnostics
from macro_engine.news.combined_report import write_combined_sector_report
from macro_engine.news.monitoring import (
    refresh_news_monitoring_from_stored_outputs,
    write_news_monitoring_report,
)
from macro_engine.news.report import write_news_report
from macro_engine.news.score_report import write_news_score_report
from macro_engine.news.scoring import build_stored_news_scores
from macro_engine.news.service import classify_stored_news, ingest_stored_news
from macro_engine.operations_config import DailyPipelineConfig, load_daily_pipeline_config
from macro_engine.pipeline_runner import run_pipeline
from macro_engine.sectors.report import write_current_sector_report
from macro_engine.sectors.service import build_stored_sector_scores
from macro_engine.storage.duckdb_store import DuckDBStore


DAILY_SUMMARY_DISCLAIMER = (
    "This daily package is a diagnostic research artifact. It is not investment advice, "
    "market action guidance, execution guidance, or instructions for changing holdings."
)


@dataclass(frozen=True)
class DailyDiagnosticResult:
    run_id: str
    run_date: date
    status: str
    archive_path: str | None
    summary_json_path: Path
    summary_markdown_path: Path
    warnings: list[str]
    errors: list[str]


def run_daily_diagnostic(
    *,
    config_path: str | Path = "config/daily_pipeline.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    run_date: str | date | None = None,
    source_profile: str | None = None,
    live_ai: bool | None = None,
    mock_ai: bool | None = None,
    max_live_items: int | None = None,
    archive: bool | None = None,
    continue_on_warning: bool = False,
    services: dict[str, Callable] | None = None,
) -> DailyDiagnosticResult:
    config = load_daily_pipeline_config(config_path)
    services = services or {}
    store = DuckDBStore(db_path)
    store.initialize()
    started_at = datetime.now(UTC)
    run_id = _run_id(started_at)
    run_day = _coerce_run_date(run_date)
    outputs: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    statuses = {
        "macro_status": "skipped",
        "sector_status": "skipped",
        "news_ingestion_status": "skipped",
        "news_classification_status": "skipped",
        "news_scoring_status": "skipped",
        "combined_status": "skipped",
        "monitoring_status": "skipped",
        "guardrail_status": "skipped",
    }

    print(f"daily: run_id={run_id} date={run_day.isoformat()} starting", flush=True)

    try:
        if config.macro.enabled:
            _run_step(
                "macro",
                statuses,
                errors,
                lambda: _run_macro(config, db_path, services),
                fail=True,
            )
        if config.sector.enabled:
            _run_step(
                "sector",
                statuses,
                errors,
                lambda: _run_sector(config, db_path, outputs, services),
                fail=True,
            )
        if config.news.enabled:
            profile = source_profile or config.news.source_profile
            _check_live_ai_safety(config, live_ai=live_ai, mock_ai=mock_ai)
            _run_step(
                "news_ingestion",
                statuses,
                errors,
                lambda: services.get("ingest_news", ingest_stored_news)(
                    config_path=config.news.news_sources_config,
                    db_path=db_path,
                    profile=profile,
                ),
                fail=True,
            )
            _run_step(
                "news_classification",
                statuses,
                errors,
                lambda: services.get("classify_news", classify_stored_news)(
                    ai_config_path=config.news.news_ai_config,
                    themes_config_path=config.news.news_themes_config,
                    db_path=db_path,
                    limit=_classification_limit(
                        config,
                        live_ai=live_ai,
                        mock_ai=mock_ai,
                        max_live_items=max_live_items,
                    ),
                    only_unclassified=_classification_only_unclassified(
                        config,
                        live_ai=live_ai,
                        mock_ai=mock_ai,
                    ),
                    progress=True,
                    continue_on_individual_failure=(
                        config.live_ai_safety.continue_on_individual_failure
                    ),
                    stop_on_failure_rate_above=(
                        config.live_ai_safety.stop_on_failure_rate_above
                    ),
                ),
                fail=True,
            )
            _run_step(
                "news_report",
                statuses,
                errors,
                lambda: _append_paths(outputs, services.get("write_news_report", write_news_report)(
                    ai_config_path=config.news.news_ai_config,
                    db_path=db_path,
                )),
                fail=True,
            )
            _run_step(
                "news_scoring",
                statuses,
                errors,
                lambda: services.get("build_news_scores", build_stored_news_scores)(
                    config_path=config.news.news_scoring_config,
                    db_path=db_path,
                ),
                fail=True,
            )
            _run_step(
                "news_score_report",
                statuses,
                errors,
                lambda: _append_paths(outputs, services.get("write_news_score_report", write_news_score_report)(
                    config_path=config.news.news_scoring_config,
                    db_path=db_path,
                )),
                fail=True,
            )
        if config.combined.enabled:
            _run_step(
                "combined",
                statuses,
                errors,
                lambda: _run_combined(config, db_path, outputs, services),
                fail=True,
            )
        if config.monitoring.enabled:
            _run_step(
                "monitoring",
                statuses,
                errors,
                lambda: _run_monitoring(config, db_path, outputs, source_profile, services),
                fail=True,
            )
        guardrail = audit_markdown_reports([path for path in outputs if str(path).endswith(".md")])
        statuses["guardrail_status"] = guardrail.status
        if not guardrail.passed:
            errors.extend([f"{item['path']}:{item['term']}" for item in guardrail.violations])
            if config.safety.fail_on_guardrail_violation:
                raise ValueError("daily report guardrail audit failed")
    except Exception as exc:
        errors.append(str(exc))
        status = "failed"
    else:
        status = _status_from_steps(statuses, warnings, config, continue_on_warning)

    summary_payload = build_daily_summary_payload(
        store=store,
        run_id=run_id,
        run_day=run_day,
        status=status,
        statuses=statuses,
        warnings=warnings,
        errors=errors,
        generated_paths=outputs,
        archive_path=None,
    )
    summary_json, summary_md = write_daily_summary(summary_payload)
    outputs.extend([str(summary_json), str(summary_md)])
    timeline_json = write_regime_timeline(store, Path(summary_json).parent)
    outputs.append(str(timeline_json))
    archive_path = None
    archive_enabled = config.outputs.archive_enabled if archive is None else archive
    if archive_enabled:
        archive_path = archive_outputs(
            run_id=run_id,
            run_day=run_day,
            output_paths=outputs,
            archive_root=config.outputs.archive_root,
        )
        summary_payload["archive_path"] = archive_path
        summary_json, summary_md = write_daily_summary(summary_payload)
        shutil.copy2(summary_json, Path(archive_path) / summary_json.name)
        shutil.copy2(summary_md, Path(archive_path) / summary_md.name)

    completed_at = datetime.now(UTC)
    store.upsert_daily_diagnostic_run(
        {
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "status": status,
            "run_date": run_day,
            "macro_status": statuses["macro_status"],
            "sector_status": statuses["sector_status"],
            "news_ingestion_status": statuses["news_ingestion_status"],
            "news_classification_status": statuses["news_classification_status"],
            "news_scoring_status": statuses["news_scoring_status"],
            "combined_status": statuses["combined_status"],
            "monitoring_status": statuses["monitoring_status"],
            "guardrail_status": statuses["guardrail_status"],
            "archive_path": archive_path,
            "warnings_json": json.dumps(warnings, sort_keys=True),
            "errors_json": json.dumps(errors, sort_keys=True),
            "created_at": completed_at,
        }
    )
    print(f"daily: run_id={run_id} status={status} complete", flush=True)
    return DailyDiagnosticResult(
        run_id=run_id,
        run_date=run_day,
        status=status,
        archive_path=archive_path,
        summary_json_path=summary_json,
        summary_markdown_path=summary_md,
        warnings=warnings,
        errors=errors,
    )


def build_daily_summary_payload(
    *,
    store: DuckDBStore,
    run_id: str,
    run_day: date,
    status: str,
    statuses: dict[str, str],
    warnings: list[str],
    errors: list[str],
    generated_paths: list[str],
    archive_path: str | None,
) -> dict[str, Any]:
    return _json_safe(
        {
            "run_id": run_id,
            "run_date": run_day.isoformat(),
            "status": status,
            "step_statuses": statuses,
            "macro": _latest_macro(store),
            "sector_macro_top": _latest_sector_top(store),
            "news": _latest_news_summary(store),
            "combined_top": _latest_combined_top(store),
            "monitoring": _latest_monitoring(store),
            "warnings": warnings,
            "errors": errors,
            "generated_artifacts": generated_paths,
            "archive_path": archive_path,
            "disclaimer": DAILY_SUMMARY_DISCLAIMER,
        }
    )


def write_daily_summary(payload: dict[str, Any], output_dir: str | Path = "outputs") -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "daily_diagnostic_summary.json"
    markdown_path = output / "daily_diagnostic_summary.md"
    markdown = daily_summary_markdown(payload)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def write_regime_timeline(
    store: DuckDBStore,
    output_dir: str | Path = "outputs",
) -> Path:
    """Export the full historical regime timeline as a chartable series.

    The daily diagnostic recomputes ``historical_regime_timeline`` back to the
    configured start_date on every run, but previously only the latest point was
    surfaced to the dashboard. This writes the entire series so the site can
    render regime-over-time instead of only post-automation daily snapshots.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "regime_timeline.json"

    timeline = store.read_table("historical_regime_timeline")
    points: list[dict[str, Any]] = []
    if not timeline.empty:
        frame = timeline.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")
        for row in frame.to_dict(orient="records"):
            confidence = row.get("reported_confidence")
            if confidence is None or pd.isna(confidence):
                confidence = row.get("confidence")
            valid = row.get("valid")
            points.append(
                {
                    "date": row["date"].date().isoformat(),
                    "reported_regime": row.get("reported_regime") or row.get("dominant_regime"),
                    "raw_dominant_regime": row.get("raw_dominant_regime")
                    or row.get("dominant_regime"),
                    "confidence": confidence,
                    "valid": None if valid is None or pd.isna(valid) else bool(valid),
                }
            )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "start_date": points[0]["date"] if points else None,
        "end_date": points[-1]["date"] if points else None,
        "point_count": len(points),
        "points": points,
        "disclaimer": DAILY_SUMMARY_DISCLAIMER,
    }
    path.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def daily_summary_markdown(payload: dict[str, Any]) -> str:
    macro = payload.get("macro") or {}
    news = payload.get("news") or {}
    monitoring = payload.get("monitoring") or {}
    return f"""# Daily Diagnostic Summary

Run date: {payload["run_date"]}
Run status: {payload["status"]}

## Macro

- Reported regime: {macro.get("reported_regime")}
- Raw leader: {macro.get("raw_dominant_regime")}
- Confidence: {_fmt(macro.get("confidence"))}

## Sector Macro Diagnostics

{_rank_lines(payload.get("sector_macro_top", []), "confidence_adjusted_score")}

## News Diagnostics

Top macro themes:
{_score_lines(news.get("top_themes", []))}

Sector diagnostic tailwinds:
{_score_lines(news.get("top_sector_tailwinds", []))}

Sector diagnostic headwinds:
{_score_lines(news.get("top_sector_headwinds", []))}

## Combined Experimental Overlay

{_rank_lines(payload.get("combined_top", []), "combined_score")}

## Monitoring

- Classification success rate: {_fmt(monitoring.get("success_rate"))}
- Retry rate: {_fmt(monitoring.get("retry_rate"))}
- Repair rate: {_fmt(monitoring.get("repair_rate"))}
- Max overlay rank change: {monitoring.get("max_rank_change")}
- Monitoring warnings: {monitoring.get("warning_count")}

## Artifacts

{_artifact_lines(payload.get("generated_artifacts", []))}

Archive path: {payload.get("archive_path")}

{payload["disclaimer"]}
"""


def archive_outputs(
    *,
    run_id: str,
    run_day: date,
    output_paths: list[str],
    archive_root: str | Path,
) -> str:
    archive_dir = Path(archive_root) / run_day.isoformat() / run_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    for item in output_paths:
        path = Path(item)
        if path.exists() and path.is_file():
            shutil.copy2(path, archive_dir / path.name)
    return str(archive_dir)


def _run_macro(config: DailyPipelineConfig, db_path: str | Path, services: dict[str, Callable]) -> None:
    runner = services.get("run_pipeline", run_pipeline)
    print("daily: macro pipeline (ingest → features → dimensions → regimes → reports)", flush=True)
    summary = runner(config_path=config.macro.config_path, db_path=db_path, mode=config.macro.mode)
    print(f"daily: macro pipeline status={summary.status}", flush=True)
    if summary.status == "success_with_warnings":
        return
    if summary.status != "success":
        raise ValueError(f"macro pipeline status {summary.status}")


def _run_sector(
    config: DailyPipelineConfig,
    db_path: str | Path,
    outputs: list[str],
    services: dict[str, Callable],
) -> None:
    services.get("build_sector_scores", build_stored_sector_scores)(
        config_path=config.sector.config_path,
        sector_config_path=config.sector.sector_config_path,
        exposure_config_path=config.sector.exposure_config_path,
        prior_config_path=config.sector.prior_config_path,
        db_path=db_path,
    )
    _append_paths(
        outputs,
        services.get("write_sector_report", write_current_sector_report)(
            config_path=config.sector.config_path,
            sector_config_path=config.sector.sector_config_path,
            exposure_config_path=config.sector.exposure_config_path,
            prior_config_path=config.sector.prior_config_path,
            db_path=db_path,
        ),
    )


def _run_combined(
    config: DailyPipelineConfig,
    db_path: str | Path,
    outputs: list[str],
    services: dict[str, Callable],
) -> None:
    services.get("build_combined", build_stored_combined_sector_diagnostics)(
        config_path=config.combined.config_path,
        db_path=db_path,
    )
    _append_paths(
        outputs,
        services.get("write_combined_report", write_combined_sector_report)(
            config_path=config.combined.config_path,
            db_path=db_path,
        ),
    )


def _run_monitoring(
    config: DailyPipelineConfig,
    db_path: str | Path,
    outputs: list[str],
    source_profile: str | None,
    services: dict[str, Callable],
) -> None:
    services.get("refresh_monitoring", refresh_news_monitoring_from_stored_outputs)(
        config_path=config.monitoring.config_path,
        source_profile=source_profile or config.monitoring.source_profile,
        db_path=db_path,
    )
    _append_paths(
        outputs,
        services.get("write_monitoring_report", write_news_monitoring_report)(
            config_path=config.monitoring.config_path,
            db_path=db_path,
        ),
    )


def _run_step(
    step: str,
    statuses: dict[str, str],
    errors: list[str],
    func: Callable,
    *,
    fail: bool,
) -> None:
    status_key = f"{step}_status"
    print(f"daily: {step} start", flush=True)
    try:
        func()
    except Exception as exc:
        statuses[status_key] = "failed"
        errors.append(f"{step}: {exc}")
        print(f"daily: {step} failed - {exc}", flush=True)
        if fail:
            raise
    else:
        statuses[status_key] = "success"
        print(f"daily: {step} done", flush=True)


def _classification_limit(
    config: DailyPipelineConfig,
    *,
    live_ai: bool | None,
    mock_ai: bool | None,
    max_live_items: int | None,
) -> int | None:
    if _daily_uses_live_ai(config, live_ai=live_ai, mock_ai=mock_ai):
        configured_limit = config.live_ai_safety.max_items_per_run
        if max_live_items is None:
            return configured_limit
        return min(max_live_items, configured_limit)
    return None


def _classification_only_unclassified(
    config: DailyPipelineConfig,
    *,
    live_ai: bool | None,
    mock_ai: bool | None,
) -> bool:
    if _daily_uses_live_ai(config, live_ai=live_ai, mock_ai=mock_ai):
        return config.live_ai_safety.classify_only_unclassified
    return False


def _daily_uses_live_ai(
    config: DailyPipelineConfig,
    *,
    live_ai: bool | None,
    mock_ai: bool | None,
) -> bool:
    if mock_ai:
        return False
    if live_ai:
        return True
    return config.news.allow_live_ai and not config.news.mock_mode_default


def _status_from_steps(
    statuses: dict[str, str],
    warnings: list[str],
    config: DailyPipelineConfig,
    continue_on_warning: bool,
) -> str:
    if any(value == "failed" for value in statuses.values()):
        return "failed"
    if warnings and not (config.safety.allow_success_with_warnings or continue_on_warning):
        return "failed"
    return "success_with_warnings" if warnings else "success"


def _check_live_ai_safety(
    config: DailyPipelineConfig,
    *,
    live_ai: bool | None,
    mock_ai: bool | None,
) -> None:
    if mock_ai:
        return
    requested_live = bool(live_ai)
    if requested_live and not config.news.allow_live_ai:
        raise ValueError("live AI was requested but daily pipeline config disallows live AI")


def _append_paths(outputs: list[str], paths: tuple[Path, Path] | None) -> None:
    if paths is None:
        return
    outputs.extend([str(paths[0]), str(paths[1])])


def _coerce_run_date(value: str | date | None) -> date:
    if value is None:
        return datetime.now(UTC).date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _run_id(started_at: datetime) -> str:
    return f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def _latest_macro(store: DuckDBStore) -> dict[str, Any]:
    timeline = store.read_table("historical_regime_timeline")
    if timeline.empty:
        return {}
    frame = timeline.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if "valid" in frame:
        frame = frame[frame["valid"]].copy()
    if frame.empty:
        return {}
    row = frame.sort_values("date").tail(1).iloc[-1]
    confidence = row.get("raw_confidence", row.get("confidence"))
    return {
        "date": row["date"].date().isoformat(),
        "reported_regime": row.get("reported_regime") or row.get("dominant_regime"),
        "raw_dominant_regime": row.get("raw_dominant_regime") or row.get("dominant_regime"),
        "confidence": None if pd.isna(confidence) else float(confidence),
    }


def _latest_sector_top(store: DuckDBStore) -> list[dict[str, Any]]:
    scores = store.read_table("sector_scores")
    if scores.empty:
        return []
    frame = scores.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame[frame["valid"]].copy()
    latest = frame[frame["date"] == frame["date"].max()].sort_values("rank").head(5)
    return [
        {
            "rank": int(row["rank"]),
            "sector_id": row["sector_id"],
            "confidence_adjusted_score": float(row["confidence_adjusted_score"]),
        }
        for row in latest.to_dict(orient="records")
    ]


def _latest_news_summary(store: DuckDBStore) -> dict[str, Any]:
    themes = store.read_table("news_daily_theme_scores")
    sectors = store.read_table("news_daily_sector_scores")
    return {
        "top_themes": _latest_score_rows(themes, "score_date", "theme_id", "adjusted_score", False),
        "top_sector_tailwinds": _latest_score_rows(
            sectors[sectors["adjusted_news_score"] > 0] if not sectors.empty else sectors,
            "score_date",
            "sector_id",
            "adjusted_news_score",
            False,
        ),
        "top_sector_headwinds": _latest_score_rows(
            sectors[sectors["adjusted_news_score"] < 0] if not sectors.empty else sectors,
            "score_date",
            "sector_id",
            "adjusted_news_score",
            True,
        ),
    }


def _latest_combined_top(store: DuckDBStore) -> list[dict[str, Any]]:
    diagnostics = store.read_table("combined_sector_diagnostics")
    if diagnostics.empty:
        return []
    frame = diagnostics.copy()
    frame["diagnostic_date"] = pd.to_datetime(frame["diagnostic_date"], errors="coerce")
    latest = frame[frame["diagnostic_date"] == frame["diagnostic_date"].max()].sort_values("rank").head(5)
    return [
        {
            "rank": int(row["rank"]),
            "sector_id": row["sector_id"],
            "combined_score": float(row["combined_score"]),
            "news_item_count": int(row["news_item_count"]),
        }
        for row in latest.to_dict(orient="records")
    ]


def _latest_monitoring(store: DuckDBStore) -> dict[str, Any]:
    classifications = store.read_table("news_classification_quality_runs")
    overlay = store.read_table("news_overlay_monitoring")
    row = {} if classifications.empty else classifications.sort_values("run_at").tail(1).iloc[-1].to_dict()
    overlay_row = {} if overlay.empty else overlay.sort_values("run_at").tail(1).iloc[-1].to_dict()
    return {
        "success_rate": row.get("success_rate"),
        "retry_rate": row.get("retry_rate"),
        "repair_rate": row.get("repair_rate"),
        "max_rank_change": overlay_row.get("max_rank_change"),
        "warning_count": 1 if overlay_row.get("overlay_status") == "warning" else 0,
    }


def _latest_score_rows(
    frame: pd.DataFrame,
    date_column: str,
    id_column: str,
    score_column: str,
    ascending: bool,
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    result = frame.copy()
    result[date_column] = pd.to_datetime(result[date_column], errors="coerce")
    latest = result[result[date_column] == result[date_column].max()]
    latest = latest.sort_values([score_column, id_column], ascending=[ascending, True]).head(5)
    return [
        {"id": row[id_column], "score": float(row[score_column])}
        for row in latest.to_dict(orient="records")
    ]


def _rank_lines(items: list[dict[str, Any]], score_key: str) -> str:
    if not items:
        return "- None"
    return "\n".join(
        f"- {item.get('rank')}. {item.get('sector_id')}: {_fmt(item.get(score_key))}"
        for item in items
    )


def _score_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item.get('id')}: {_fmt(item.get('score'))}" for item in items)


def _artifact_lines(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


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
