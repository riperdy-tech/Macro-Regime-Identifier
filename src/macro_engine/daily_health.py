from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from macro_engine.news.config import load_news_ai_config
from macro_engine.news.ingest import validate_news_input_config
from macro_engine.operations_config import load_daily_pipeline_config
from macro_engine.storage.duckdb_store import DuckDBStore


def daily_health_check(
    *,
    config_path: str | Path = "config/daily_pipeline.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> dict[str, Any]:
    config = load_daily_pipeline_config(config_path)
    checks: list[dict[str, Any]] = []
    _check_path(checks, "daily_pipeline_config", config_path)
    _check_path(checks, "macro_config", config.macro.config_path)
    _check_path(checks, "sector_config", config.sector.config_path)
    _check_path(checks, "news_sources_config", config.news.news_sources_config)
    _check_path(checks, "news_ai_config", config.news.news_ai_config)
    _check_path(checks, "news_scoring_config", config.news.news_scoring_config)
    _check_path(checks, "combined_config", config.combined.config_path)
    _check_path(checks, "monitoring_config", config.monitoring.config_path)
    _check_database(checks, db_path)
    _check_archive_root(checks, config.outputs.archive_root)
    _check_news_source_profile(
        checks,
        config.news.news_sources_config,
        config.news.source_profile,
    )
    _check_ai_key(checks, config.news.news_ai_config, live_enabled=config.news.allow_live_ai)
    _check_gitignore(checks)
    status = "ok"
    if any(check["status"] == "blocked" for check in checks):
        status = "blocked"
    elif any(check["status"] == "warning" for check in checks):
        status = "warning"
    return {"valid": status != "blocked", "status": status, "checks": checks}


def _check_path(checks: list[dict[str, Any]], name: str, path: str | Path) -> None:
    target = Path(path)
    checks.append(
        {
            "name": name,
            "status": "ok" if target.exists() else "blocked",
            "path": str(target),
            "message": "exists" if target.exists() else "missing",
        }
    )


def _check_database(checks: list[dict[str, Any]], db_path: str | Path) -> None:
    try:
        store = DuckDBStore(db_path)
        store.initialize()
    except Exception as exc:  # pragma: no cover - defensive health reporting
        checks.append(
            {
                "name": "database",
                "status": "blocked",
                "path": str(db_path),
                "message": str(exc),
            }
        )
    else:
        checks.append(
            {
                "name": "database",
                "status": "ok",
                "path": str(db_path),
                "message": "reachable",
            }
        )


def _check_archive_root(checks: list[dict[str, Any]], archive_root: str | Path) -> None:
    path = Path(archive_root)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        checks.append(
            {
                "name": "archive_root",
                "status": "blocked",
                "path": str(path),
                "message": str(exc),
            }
        )
    else:
        checks.append(
            {
                "name": "archive_root",
                "status": "ok",
                "path": str(path),
                "message": "writable",
            }
        )


def _check_news_source_profile(
    checks: list[dict[str, Any]],
    config_path: str | Path,
    profile: str,
) -> None:
    try:
        summary = validate_news_input_config(config_path=config_path, profile=profile)
    except (FileNotFoundError, ValueError) as exc:
        checks.append(
            {
                "name": "news_source_profile",
                "status": "blocked",
                "path": str(config_path),
                "message": str(exc),
            }
        )
    else:
        status = "warning" if summary.get("warnings") else "ok"
        checks.append(
            {
                "name": "news_source_profile",
                "status": status,
                "path": str(config_path),
                "message": f"profile {profile}: {summary.get('unique_item_count', 0)} unique items",
                "warnings": summary.get("warnings", []),
            }
        )


def _check_ai_key(checks: list[dict[str, Any]], config_path: str | Path, *, live_enabled: bool) -> None:
    config = load_news_ai_config(config_path)
    key_present = bool(os.environ.get(config.api_key_env))
    if live_enabled and not key_present:
        status = "blocked"
        message = f"{config.api_key_env} is required when live AI is enabled"
    elif not live_enabled and not key_present:
        status = "ok"
        message = f"{config.api_key_env} not required for mock-safe mode"
    else:
        status = "ok"
        message = f"{config.api_key_env} is present"
    checks.append(
        {
            "name": "ai_key",
            "status": status,
            "path": str(config_path),
            "message": message,
        }
    )


def _check_gitignore(checks: list[dict[str, Any]]) -> None:
    path = Path(".gitignore")
    if not path.exists():
        checks.append(
            {
                "name": "gitignore_generated_outputs",
                "status": "warning",
                "path": ".gitignore",
                "message": "missing .gitignore",
            }
        )
        return
    text = path.read_text(encoding="utf-8")
    required_markers = ["outputs/", "data/news_pilot/", "logs/"]
    missing = [marker for marker in required_markers if marker not in text]
    checks.append(
        {
            "name": "gitignore_generated_outputs",
            "status": "ok" if not missing else "warning",
            "path": ".gitignore",
            "message": "generated output markers present" if not missing else f"missing {missing}",
        }
    )
