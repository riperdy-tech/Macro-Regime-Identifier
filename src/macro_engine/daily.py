from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
import shutil
import time
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from macro_engine.anchors.config import load_anchor_config
from macro_engine.anchors.service import build_anchors
from macro_engine.guardrails import audit_markdown_reports
from macro_engine.news.advisory import write_news_advisory_block
from macro_engine.news.combined import build_stored_combined_sector_diagnostics
from macro_engine.news.combined_report import write_combined_sector_report
from macro_engine.news.history import export_news_history, hydrate_news_history
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
from macro_engine.regime_status import compute_feature_freshness
from macro_engine.sectors.fit import write_sector_fit_report
from macro_engine.sectors.report import write_current_sector_report
from macro_engine.sectors.service import build_stored_sector_scores
from macro_engine.sectors.validation import run_stored_sector_validation
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
    output_dir: str | Path = "outputs",
) -> DailyDiagnosticResult:
    config = load_daily_pipeline_config(config_path)
    services = services or {}
    store = DuckDBStore(db_path)
    store.initialize()
    started_at = datetime.now(UTC)
    run_id = _run_id(started_at)
    run_day = _coerce_run_date(run_date)
    run_deadline = time.monotonic() + config.safety.overall_run_timeout_minutes * 60.0
    outputs: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    profile = source_profile or config.news.source_profile
    statuses = {
        "macro_status": "skipped",
        "sector_status": "skipped",
        "news_history_hydrate_status": "skipped",
        "news_ingestion_status": "skipped",
        "news_classification_status": "skipped",
        "news_scoring_status": "skipped",
        "news_history_export_status": "skipped",
        "news_health_status": "skipped",
        "combined_status": "skipped",
        "monitoring_status": "skipped",
        "guardrail_status": "skipped",
    }
    news_history_result: dict[str, Any] = {}
    news_health_result: dict[str, Any] = {}
    classification_result_holder: dict[str, Any] = {}

    print(f"daily: run_id={run_id} date={run_day.isoformat()} starting", flush=True)

    try:
        if config.macro.enabled:
            _run_step(
                "macro",
                statuses,
                errors,
                lambda: _run_macro(
                    config,
                    db_path,
                    services,
                    run_deadline=run_deadline,
                    warnings=warnings,
                ),
                fail=True,
                deadline=run_deadline,
                daily_warnings=warnings,
            )
        if config.sector.enabled:
            _run_step(
                "sector",
                statuses,
                errors,
                lambda: _run_sector(config, db_path, outputs, services),
                fail=True,
                deadline=run_deadline,
                daily_warnings=warnings,
            )
        if config.news.enabled:
            profile = source_profile or config.news.source_profile
            _check_live_ai_safety(config, live_ai=live_ai, mock_ai=mock_ai)
            store = DuckDBStore(db_path)
            store.initialize()
            if config.news.history_dir:
                # N1.4: hydrate BEFORE ingestion, so an item present in both the
                # snapshot and today's fetch keeps the snapshot's first_seen_at,
                # and a cold store never re-pays for an already-classified id.
                # Deadline-exempt and optional: a bad snapshot must never take
                # the macro/sector product down.
                _run_step(
                    "news_history_hydrate",
                    statuses,
                    errors,
                    lambda: _run_news_history_hydrate(config, db_path, services, news_history_result),
                    fail=False,
                    optional=True,
                    deadline=None,
                    daily_warnings=warnings,
                )
            if (not _daily_uses_live_ai(config, live_ai=live_ai, mock_ai=mock_ai)) and store.has_real_classifications():
                statuses["news_ingestion_status"] = "skipped_live_store"
                statuses["news_classification_status"] = "skipped_live_store"
                warnings.append("news_nonlive_skipped_on_live_store")
            else:
                _run_step(
                    "news_ingestion",
                    statuses,
                    errors,
                    lambda: services.get("ingest_news", ingest_stored_news)(
                        config_path=config.news.news_sources_config,
                        db_path=db_path,
                        profile=profile,
                        run_id=run_id,
                    ),
                    fail=True,
                    deadline=run_deadline,
                    daily_warnings=warnings,
                )
                # N1.6 pre-check: F1 (news_blackout) and F4 (cold_hydrate_failed)
                # are both known right after ingestion, before a single DeepSeek
                # call is spent. A failed pre-check skips classification instead
                # of paying for it; the final news_health (after export) is what
                # the summary and the workflow's red-run gate (OA-4) read.
                precheck = _compute_news_health(
                    config,
                    db_path,
                    run_id=run_id,
                    run_at=started_at,
                    profile=profile,
                    news_history_result=news_history_result,
                    classification_result=None,
                )
                if precheck["status"] == "failed":
                    statuses["news_classification_status"] = "skipped_news_failed"
                else:
                    classification_deadline = (
                        run_deadline - config.safety.post_classification_reserve_minutes * 60.0
                    )
                    _run_step(
                        "news_classification",
                        statuses,
                        errors,
                        lambda: _run_news_classification(
                            config,
                            db_path,
                            services,
                            live_ai=live_ai,
                            mock_ai=mock_ai,
                            max_live_items=max_live_items,
                            deadline_monotonic=classification_deadline,
                            warnings=warnings,
                            result_holder=classification_result_holder,
                        ),
                        fail=True,
                        deadline=run_deadline,
                        daily_warnings=warnings,
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
                deadline=run_deadline,
                daily_warnings=warnings,
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
                deadline=run_deadline,
                daily_warnings=warnings,
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
                deadline=run_deadline,
                daily_warnings=warnings,
            )
            if config.news.history_dir:
                # N1.4: export runs after the last news write (classification,
                # scoring) and before the summary is built, deadline-exempt so a
                # slow run still gets its durable copy out.
                _run_step(
                    "news_history_export",
                    statuses,
                    errors,
                    lambda: _run_news_history_export(config, db_path, services, news_history_result),
                    fail=False,
                    optional=True,
                    deadline=None,
                    daily_warnings=warnings,
                )
            # N1.6: the authoritative news_health, computed after export,
            # deadline-exempt, before the summary. Never depends on monitoring
            # (the deadline can skip that step). failed -> daily status becomes
            # failed (the CLI still exits 0; OA-4's workflow step is what turns
            # the Action red). degraded -> success_with_warnings via the warning
            # appended below.
            news_health_result.update(
                _compute_news_health(
                    config,
                    db_path,
                    run_id=run_id,
                    run_at=started_at,
                    profile=profile,
                    news_history_result=news_history_result,
                    classification_result=classification_result_holder.get("classification"),
                )
            )
            if news_health_result:
                statuses["news_health_status"] = news_health_result["status"]
                reason_text = ",".join(news_health_result.get("reasons", []))
                if news_health_result["status"] == "failed":
                    warnings.append(f"news_health_failed:{reason_text}")
                elif news_health_result["status"] == "degraded":
                    warnings.append(f"news_health_degraded:{reason_text}")
        if config.combined.enabled:
            _run_step(
                "combined",
                statuses,
                errors,
                lambda: _run_combined(config, db_path, outputs, services),
                fail=True,
                deadline=run_deadline,
                daily_warnings=warnings,
            )
        if config.anchors.enabled:
            # The macro step above already ran run_pipeline's required vintages step (F3),
            # which refreshes the same as-of set (vintage_asof_dates) this used to refresh a
            # second time here. Running it again was redundant, not protective -- a failed
            # refresh now fails the macro step loudly instead of being swallowed by this
            # step's fail=False, optional=True.
            # `fail=config.anchors.required` (False by default): the anchors are an
            # additive artifact layer. A missing anchor input degrades loudly INSIDE the
            # anchor payload; it must never take the daily diagnostic down with it.
            _run_step(
                "anchors",
                statuses,
                errors,
                lambda: _run_anchors(config, db_path, outputs, services),
                fail=config.anchors.required,
                optional=not config.anchors.required,
                deadline=run_deadline,
                daily_warnings=warnings,
            )
        if config.monitoring.enabled:
            _run_step(
                "monitoring",
                statuses,
                errors,
                lambda: _run_monitoring(config, db_path, outputs, source_profile, services),
                fail=True,
                deadline=run_deadline,
                daily_warnings=warnings,
            )
        if time.monotonic() < run_deadline:
            guardrail = audit_markdown_reports([path for path in outputs if str(path).endswith(".md")])
            statuses["guardrail_status"] = guardrail.status
            if not guardrail.passed:
                errors.extend([f"{item['path']}:{item['term']}" for item in guardrail.violations])
                if config.safety.fail_on_guardrail_violation:
                    raise ValueError("daily report guardrail audit failed")
        else:
            statuses["guardrail_status"] = "skipped_deadline"
            warnings.append("deadline_reached:skipped=guardrail")
        feature_freshness = compute_feature_freshness(store)
        if feature_freshness.get("stale"):
            warnings.append(
                f"feature_freshness_stale:gap_days={feature_freshness.get('gap_days')}"
            )
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
        news_history=news_history_result,
        news_health=news_health_result,
    )
    summary_json, summary_md = write_daily_summary(summary_payload, output_dir)
    outputs.extend([str(summary_json), str(summary_md)])
    timeline_json = write_regime_timeline(store, Path(summary_json).parent)
    outputs.append(str(timeline_json))
    features_json = write_macro_features_timeline(store, Path(summary_json).parent)
    outputs.append(str(features_json))
    sources_json = write_news_sources_used(
        config.news.news_sources_config, profile, Path(summary_json).parent
    )
    outputs.append(str(sources_json))
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
        summary_json, summary_md = write_daily_summary(summary_payload, output_dir)
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
    news_history: dict[str, Any] | None = None,
    news_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from macro_engine.ingest.service import compute_vintage_backlog

    vintage_backlog = compute_vintage_backlog(store)
    if vintage_backlog.get("pending_pairs", 0) > 0:
        print(
            f"daily: vintage backlog pending_pairs={vintage_backlog['pending_pairs']} "
            f"frontier={vintage_backlog.get('frontier_asof')} "
            f"start={vintage_backlog.get('point_in_time_start')}",
            flush=True,
        )

    return _json_safe(
        {
            "run_id": run_id,
            "run_date": run_day.isoformat(),
            "status": status,
            "step_statuses": statuses,
            "macro": _latest_macro(store),
            "feature_freshness": compute_feature_freshness(store),
            "vintage_backlog": vintage_backlog,
            "sector_macro_top": _latest_sector_top(store),
            "news": _latest_news_summary(store),
            "combined_top": _latest_combined_top(store),
            "monitoring": _latest_monitoring(store),
            "news_history": news_history or {},
            "news_health": news_health or {},
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

    # Per-month probability for every regime, so the dashboard can chart the full
    # regime "mix" over time (a stacked area), not just the winning label.
    prob_by_date: dict[str, dict[str, float]] = {}
    try:
        regime_scores = store.read_table("regime_scores")
    except Exception:
        regime_scores = pd.DataFrame()
    if not regime_scores.empty and "probability" in regime_scores.columns:
        rs = regime_scores.copy()
        rs["date"] = pd.to_datetime(rs["date"], errors="coerce")
        rs = rs.dropna(subset=["date"])
        for row in rs.to_dict(orient="records"):
            prob = row.get("probability")
            if prob is None or pd.isna(prob):
                continue
            key = row["date"].date().isoformat()
            prob_by_date.setdefault(key, {})[str(row.get("regime_id"))] = round(float(prob), 4)

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
            iso = row["date"].date().isoformat()
            points.append(
                {
                    "date": iso,
                    "reported_regime": row.get("reported_regime") or row.get("dominant_regime"),
                    "raw_dominant_regime": row.get("raw_dominant_regime")
                    or row.get("dominant_regime"),
                    "confidence": confidence,
                    "valid": None if valid is None or pd.isna(valid) else bool(valid),
                    "probabilities": prob_by_date.get(iso, {}),
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


def write_macro_features_timeline(
    store: DuckDBStore,
    output_dir: str | Path = "outputs",
) -> Path:
    """Export each macro indicator's normalized (z-score) history, grouped by the
    dimension it feeds, so the dashboard can chart what drives every regime axis.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "macro_features_timeline.json"

    try:
        af = store.read_table("asof_feature_values")
    except Exception:
        af = pd.DataFrame()
    try:
        contrib = store.read_table("dimension_feature_contributions")
    except Exception:
        contrib = pd.DataFrame()
    try:
        health = store.read_table("feature_health")
    except Exception:
        health = pd.DataFrame()

    # feature -> dimension and feature -> series_id maps.
    feat_dim: dict[str, str] = {}
    if not contrib.empty:
        for row in contrib[["feature_id", "dimension_id"]].drop_duplicates().to_dict("records"):
            feat_dim[str(row["feature_id"])] = str(row["dimension_id"])
    feat_series: dict[str, str] = {}
    if not health.empty and "series_id" in health.columns:
        for row in health[["feature_id", "series_id"]].drop_duplicates().to_dict("records"):
            feat_series[str(row["feature_id"])] = str(row.get("series_id") or "")

    dimensions: list[dict[str, Any]] = []
    start_date = end_date = None
    if not af.empty:
        frame = af.copy()
        frame["evaluation_date"] = pd.to_datetime(frame["evaluation_date"], errors="coerce")
        frame = frame.dropna(subset=["evaluation_date"]).sort_values("evaluation_date")
        start_date = frame["evaluation_date"].min().date().isoformat()
        end_date = frame["evaluation_date"].max().date().isoformat()

        by_dim: dict[str, list[dict[str, Any]]] = {}
        for feature_id, fgroup in frame.groupby("feature_id"):
            dim = feat_dim.get(str(feature_id), "unmapped")
            points = [
                {"date": row["evaluation_date"].date().isoformat(), "value": round(float(v), 3)}
                for row in fgroup.to_dict("records")
                if (v := row.get("normalized_value")) is not None and not pd.isna(v)
            ]
            if not points:
                continue
            by_dim.setdefault(dim, []).append(
                {
                    "feature_id": str(feature_id),
                    "series_id": feat_series.get(str(feature_id), ""),
                    "points": points,
                }
            )
        for dim_id in sorted(by_dim):
            dimensions.append(
                {"dimension_id": dim_id, "features": sorted(by_dim[dim_id], key=lambda f: f["feature_id"])}
            )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "dimensions": dimensions,
        "disclaimer": DAILY_SUMMARY_DISCLAIMER,
    }
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_news_sources_used(
    sources_config_path: str | Path,
    profile: str | None,
    output_dir: str | Path = "outputs",
) -> Path:
    """Export the news feeds actually used (the selected profile's RSS sources),
    grouped by category, so the dashboard can show where the news comes from."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "news_sources.json"

    groups: dict[str, list[dict[str, str]]] = {}
    try:
        from macro_engine.news.config import load_news_sources_config

        config = load_news_sources_config(sources_config_path)
        for source in config.news_sources:
            if profile and profile not in source.profiles:
                continue
            if source.provider != "rss" or not source.feed_url:
                continue
            group = source.source_group or "unmapped"
            groups.setdefault(group, []).append(
                {
                    "name": source.source or source.source_id,
                    "url": source.feed_url,
                }
            )
    except Exception:
        groups = {}

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": profile,
        "source_count": sum(len(v) for v in groups.values()),
        "groups": [
            {"group": g, "sources": sorted(groups[g], key=lambda s: s["name"])}
            for g in sorted(groups)
        ],
        "disclaimer": "News headlines are used as diagnostic inputs only, not republished.",
    }
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True), encoding="utf-8")
    return path


def daily_summary_markdown(payload: dict[str, Any]) -> str:
    macro = payload.get("macro") or {}
    news = payload.get("news") or {}
    monitoring = payload.get("monitoring") or {}
    news_health = payload.get("news_health") or {}
    feature_freshness = payload.get("feature_freshness") or {}
    monitoring_status = (payload.get("step_statuses") or {}).get("monitoring_status")
    monitoring_note = (
        ""
        if monitoring_status == "success"
        else f"\n_Monitoring not run this time; values as_of run_id={monitoring.get('as_of_run_id')}._\n"
    )
    return f"""# Daily Diagnostic Summary

Run date: {payload["run_date"]}
Run status: {payload["status"]}

## Macro

- Reported regime: {macro.get("reported_regime")}
- Raw leader: {macro.get("raw_dominant_regime")}
- Confidence: {_fmt(macro.get("confidence"))}
- Feature freshness: gap {feature_freshness.get("gap_days")}d (stale: {feature_freshness.get("stale")})

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
{monitoring_note}
- Classification success rate: {_fmt(monitoring.get("success_rate"))}
- Retry rate: {_fmt(monitoring.get("retry_rate"))}
- Repair rate: {_fmt(monitoring.get("repair_rate"))}
- Max overlay rank change: {monitoring.get("max_rank_change")}
- Monitoring warnings: {monitoring.get("warning_count")}

## News health

- Status: {news_health.get("status", "not_computed")}
- Reasons: {", ".join(news_health.get("reasons", [])) or "none"}
- Notes: {", ".join(news_health.get("notes", [])) or "none"}
{_dead_source_lines(news_health.get("sources", []))}

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


def _run_macro(
    config: DailyPipelineConfig,
    db_path: str | Path,
    services: dict[str, Callable],
    *,
    run_deadline: float | None = None,
    warnings: list[str] | None = None,
) -> None:
    runner = services.get("run_pipeline", run_pipeline)
    print("daily: macro pipeline (ingest -> features -> dimensions -> regimes -> reports)", flush=True)

    vintage_time_budget_seconds: float | None = None
    if run_deadline is not None:
        seconds_left = max(0.0, run_deadline - time.monotonic())
        reserve_seconds = config.safety.post_classification_reserve_minutes * 60.0
        vintage_time_budget_seconds = min(
            config.macro.vintage_budget_minutes * 60.0,
            max(0.0, seconds_left - reserve_seconds),
        )
    else:
        vintage_time_budget_seconds = config.macro.vintage_budget_minutes * 60.0

    kwargs: dict[str, Any] = {
        "config_path": config.macro.config_path,
        "db_path": db_path,
        "mode": config.macro.mode,
    }
    import inspect
    sig = inspect.signature(runner)
    if "vintage_time_budget_seconds" in sig.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    ):
        kwargs["vintage_time_budget_seconds"] = vintage_time_budget_seconds

    summary = runner(**kwargs)
    print(f"daily: macro pipeline status={summary.status}", flush=True)

    if warnings is not None:
        for w in getattr(summary, "warnings", ()):
            if str(w).startswith("vintage_"):
                warnings.append(str(w))

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
    # C1 (MRI_S1_APPROVAL.md S6): validation must run BEFORE the ranking artifact is
    # written, against the sector_scores just built above -- otherwise `validation` in
    # current_sector_ranking.json always describes the previous run (the review's key
    # finding: this used to happen the other way around, write_sector_report at daily.py's
    # old :635 and run-sector-validation only later, in run_daily_diagnostic.ps1:76).
    services.get("run_sector_validation", run_stored_sector_validation)(
        config_path=config.sector.validation_config_path,
        db_path=db_path,
        macro_config_path=config.sector.config_path,
        sector_config_path=config.sector.sector_config_path,
        exposure_config_path=config.sector.exposure_config_path,
        prior_config_path=config.sector.prior_config_path,
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
    # S6.3: the shadow fitted-exposures artifact. Cheap (the fit itself is annual; scoring
    # today against the frozen vintage is one dot product per sector) and strictly additive --
    # it reads dimension_scores/sector_validation_returns and writes a NEW file,
    # outputs/sector_exposures_fitted.json, never current_sector_ranking.json (mode: shadow,
    # affects_quotas: false).
    _append_paths(
        outputs,
        services.get("write_sector_fit_report", write_sector_fit_report)(
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


def _run_anchors(
    config: DailyPipelineConfig,
    db_path: str | Path,
    outputs: list[str],
    services: dict[str, Callable],
) -> None:
    anchors = services.get("build_anchors", build_anchors)(
        config_path=config.anchors.config_path,
        macro_config_path=config.anchors.macro_config_path,
        sector_config_path=config.anchors.sector_config_path,
        db_path=db_path,
    )
    output_dir = Path(load_anchor_config(config.anchors.config_path).output_dir)
    if anchors.degraded:
        print(
            "daily: anchors built DEGRADED "
            f"({len(anchors.degradation_reasons)} reason(s); see anchor payloads)",
            flush=True,
        )
    for filename in (
        "cost_of_capital_anchor.json",
        "long_run_growth_anchor.json",
        "sector_multiple_bands.json",
        "rs2_repair_package.json",
    ):
        outputs.append(str(output_dir / filename))
    if config.anchors.advisory_block.enabled:
        _append_paths(
            outputs,
            services.get("write_news_advisory_block", write_news_advisory_block)(
                config_path=config.anchors.advisory_block.config_path,
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
    optional: bool = False,
    deadline: float | None = None,
    daily_warnings: list[str] | None = None,
) -> None:
    status_key = f"{step}_status"
    if deadline is not None and time.monotonic() >= deadline:
        statuses[status_key] = "skipped_deadline"
        if daily_warnings is not None:
            daily_warnings.append(f"deadline_reached:skipped={step}")
        print(f"daily: {step} skipped (run deadline reached)", flush=True)
        return
    print(f"daily: {step} start", flush=True)
    try:
        func()
    except Exception as exc:
        # `optional` steps record a NON-FATAL failure status. `_status_from_steps`
        # treats only "failed" as fatal, so an additive artifact layer that cannot be
        # built is reported loudly and downgrades the run to success_with_warnings
        # without taking the diagnostic down.
        statuses[status_key] = "failed_optional" if optional else "failed"
        errors.append(f"{step}: {exc}")
        print(f"daily: {step} failed - {exc}", flush=True)
        if fail:
            raise
    else:
        statuses[status_key] = "success"
        print(f"daily: {step} done", flush=True)


def _run_news_classification(
    config: DailyPipelineConfig,
    db_path: str | Path,
    services: dict[str, Callable],
    *,
    live_ai: bool | None,
    mock_ai: bool | None,
    max_live_items: int | None,
    deadline_monotonic: float | None,
    warnings: list[str],
    result_holder: dict[str, Any] | None = None,
) -> None:
    fn = services.get("classify_news", classify_stored_news)
    kwargs: dict[str, Any] = {
        "ai_config_path": config.news.news_ai_config,
        "themes_config_path": config.news.news_themes_config,
        "db_path": db_path,
        "limit": _classification_limit(
            config,
            live_ai=live_ai,
            mock_ai=mock_ai,
            max_live_items=max_live_items,
        ),
        "only_unclassified": _classification_only_unclassified(
            config,
            live_ai=live_ai,
            mock_ai=mock_ai,
        ),
        "progress": True,
        "continue_on_individual_failure": (
            config.live_ai_safety.continue_on_individual_failure
        ),
        "stop_on_failure_rate_above": (
            config.live_ai_safety.stop_on_failure_rate_above
        ),
        "selection_config_path": config.news.news_selection_config,
        "sources_config_path": config.news.news_sources_config,
    }
    import inspect
    sig = inspect.signature(fn)
    if "deadline_monotonic" in sig.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    ):
        kwargs["deadline_monotonic"] = deadline_monotonic

    result = fn(**kwargs)
    if result_holder is not None and isinstance(result, dict):
        result_holder["classification"] = result
    if isinstance(result, dict) and result.get("deadline_hit"):
        done = result.get("completed_count", 0)
        selected = result.get("selected_count", 0)
        warnings.append(f"news_classification_deadline:{done}/{selected}")


def _run_news_history_hydrate(
    config: DailyPipelineConfig,
    db_path: str | Path,
    services: dict[str, Callable],
    result_holder: dict[str, Any],
) -> None:
    fn = services.get("hydrate_news_history", hydrate_news_history)
    outcome = fn(db_path=db_path, history_dir=config.news.history_dir)
    result_holder["hydrate"] = outcome


def _run_news_history_export(
    config: DailyPipelineConfig,
    db_path: str | Path,
    services: dict[str, Callable],
    result_holder: dict[str, Any],
) -> None:
    fn = services.get("export_news_history", export_news_history)
    outcome = fn(db_path=db_path, history_dir=config.news.history_dir)
    result_holder["export"] = outcome


def _compute_news_health(
    config: DailyPipelineConfig,
    db_path: str | Path,
    *,
    run_id: str,
    run_at: datetime,
    profile: str | None,
    news_history_result: dict[str, Any],
    classification_result: dict[str, Any] | None,
) -> dict[str, Any]:
    from macro_engine.news.health import compute_news_health

    store = DuckDBStore(db_path)
    try:
        history = store.read_table("news_source_runs")
    except Exception:
        history = pd.DataFrame()
    stale_map, profile_groups = _news_source_health_config(config.news.news_sources_config, profile)
    hydrate_result = news_history_result.get("hydrate") or {}
    export_result = news_history_result.get("export") or {}
    return compute_news_health(
        run_id=run_id,
        run_at=run_at,
        source_runs_history=history,
        stale_after_hours=stale_map,
        profile_groups=profile_groups,
        classification_result=classification_result,
        hydrate_result=hydrate_result,
        export_result=export_result,
        store_was_cold=bool(hydrate_result.get("store_was_cold", False)),
    )


def _news_source_health_config(
    sources_config_path: str | Path, profile: str | None
) -> tuple[dict[str, int], dict[str, list[str]]]:
    from macro_engine.news.config import load_news_sources_config

    sources_config = load_news_sources_config(sources_config_path)
    stale_map: dict[str, int] = {}
    groups: dict[str, list[str]] = {}
    for source in sources_config.news_sources:
        stale_map[source.source_id] = source.stale_after_hours
        selected = (
            source.enabled
            if profile is None
            else (source.source_id == profile or profile in source.profiles)
        )
        if selected and source.source_group:
            groups.setdefault(source.source_group, []).append(source.source_id)
    return stale_map, groups


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
    optional_failure = any(value == "failed_optional" for value in statuses.values())
    skipped_deadline = any(value == "skipped_deadline" for value in statuses.values())
    if (warnings or optional_failure or skipped_deadline) and not (
        config.safety.allow_success_with_warnings or continue_on_warning
    ):
        return "failed"
    return (
        "success_with_warnings"
        if (warnings or optional_failure or skipped_deadline)
        else "success"
    )


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
        "as_of_run_id": row.get("run_id"),
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


def _dead_source_lines(sources: list[dict[str, Any]]) -> str:
    dead = [s for s in sources if s.get("dead")]
    if not dead:
        return "- No dead sources"
    return "\n".join(
        f"- {s['source_id']}: {s['status']}, last new item "
        f"{s.get('last_new_at') or 'never'}, {s.get('consecutive_bad_runs', 0)} bad runs"
        for s in dead
    )


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
