from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from typing import Any


DASHBOARD_OUTPUT_FILES = [
    "daily_diagnostic_summary.json",
    "current_sector_ranking.json",
    "news_score_report.json",
    "combined_sector_diagnostic.json",
    "news_monitoring_report.json",
    "news_accumulation_report.json",
    "news_source_coverage_report.json",
    "secular_theme_scores.json",
    "automation_run_summary.json",
]
HISTORY_INDEX_FILE = "history_index.json"


def export_dashboard_data(
    *,
    outputs_dir: str | Path = "outputs",
    dashboard_data_dir: str | Path = "dashboard/public/data",
) -> dict[str, Any]:
    source_dir = Path(outputs_dir)
    target_dir = Path(dashboard_data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    available_files: list[str] = []
    missing_files: list[str] = []
    for filename in DASHBOARD_OUTPUT_FILES:
        source = source_dir / filename
        if source.exists():
            shutil.copy2(source, target_dir / filename)
            available_files.append(filename)
        else:
            missing_files.append(filename)

    history_index = _build_history_index(source_dir)
    (target_dir / HISTORY_INDEX_FILE).write_text(
        json.dumps(history_index, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    snapshots = {name: _read_json(target_dir / name) for name in available_files}
    manifest_available_files = [*available_files, HISTORY_INDEX_FILE]
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "available_files": manifest_available_files,
        "missing_files": missing_files,
        "latest_run_date": _nested_get(snapshots, "daily_diagnostic_summary.json", "run_date"),
        "latest_macro_date": (
            _nested_get(snapshots, "daily_diagnostic_summary.json", "macro", "date")
            or _nested_get(snapshots, "current_sector_ranking.json", "date")
        ),
        "latest_news_score_date": _nested_get(
            snapshots,
            "news_score_report.json",
            "latest_news_scoring_date",
        ),
        "data_status": _data_status(available_files, missing_files),
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def _build_history_index(outputs_dir: Path) -> dict[str, Any]:
    archive_root = outputs_dir / "archive"
    rows: list[dict[str, Any]] = []
    if archive_root.exists():
        for summary_path in archive_root.glob("*/*/daily_diagnostic_summary.json"):
            summary = _read_json(summary_path)
            if summary:
                rows.append(_history_row(summary, summary_path))
    rows.sort(key=lambda row: (str(row.get("run_date") or ""), str(row.get("run_id") or "")), reverse=True)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "history_status": "available" if rows else "empty",
        "total_runs": len(rows),
        "runs": rows,
    }


def _history_row(summary: dict[str, Any], summary_path: Path) -> dict[str, Any]:
    macro = _object(summary.get("macro"))
    monitoring = _object(summary.get("monitoring"))
    step_statuses = _object(summary.get("step_statuses"))
    replay = _object(summary.get("replay"))
    return {
        "run_id": summary.get("run_id"),
        "run_date": summary.get("run_date"),
        "run_mode": summary.get("run_mode") or ("replay" if replay else "daily"),
        "replay_date": replay.get("replay_date"),
        "status": summary.get("status"),
        "archive_path": summary.get("archive_path") or str(summary_path.parent),
        "macro_regime": macro.get("reported_regime"),
        "macro_confidence": macro.get("confidence"),
        "top_combined_sectors": _sector_ids(summary.get("combined_top")),
        "readiness_label": _nested_value(summary, "accumulation", "readiness_label") or "insufficient_history",
        "guardrail_status": step_statuses.get("guardrail_status"),
        "classification_success_rate": monitoring.get("success_rate"),
        "max_overlay_rank_change": monitoring.get("max_rank_change"),
        "warning_count": len(_array(summary.get("warnings"))) or summary.get("warning_count") or 0,
        "error_count": len(_array(summary.get("errors"))) or summary.get("error_count") or 0,
    }


def _sector_ids(value: Any) -> list[str]:
    sectors: list[str] = []
    for item in _array(value):
        if isinstance(item, dict) and item.get("sector_id"):
            sectors.append(str(item["sector_id"]))
    return sectors


def _nested_value(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _nested_get(payloads: dict[str, dict[str, Any]], filename: str, *keys: str) -> Any:
    value: Any = payloads.get(filename, {})
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _data_status(available_files: list[str], missing_files: list[str]) -> str:
    if not available_files:
        return "missing"
    if missing_files:
        return "partial"
    return "complete"
