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
]


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

    snapshots = {
        name: _read_json(target_dir / name)
        for name in available_files
    }
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "available_files": available_files,
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
