from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.anchors.config import load_anchor_config
from macro_engine.evaluation.config import load_evaluation_config
from macro_engine.news.config import load_news_ai_config
from macro_engine.news.ingest import validate_news_input_config
from macro_engine.operations_config import load_daily_pipeline_config
from macro_engine.storage.duckdb_store import DuckDBStore

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


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
    _check_output_dates(checks, db_path, config.outputs.archive_root)
    _check_anchor_basis(checks, db_path, config)
    status = "ok"
    if any(check["status"] == "blocked" for check in checks):
        status = "blocked"
    elif any(check["status"] == "warning" for check in checks):
        status = "warning"
    return {"valid": status != "blocked", "status": status, "checks": checks}


def _check_output_dates(
    checks: list[dict[str, Any]],
    db_path: str | Path,
    output_dir: str | Path = "outputs",
) -> None:
    """Flag published artifacts that claim dates the database cannot support.

    `outputs/` is gitignored local state, so a file written during a synthetic or sample run
    persists across every later run that does not happen to rewrite it. Three artifacts were
    found dated 2031-08-01 — a `current_regime.json`, a `historical_diagnostic.json` whose
    END DATE was 2031, and an `automation_run_summary.json` — while the database's newest row
    was 2026-05-01. Nothing was wrong with the producers: the files were stale, and a stale
    future-dated artifact reads exactly like a current one.

    This is a WARNING, not a block: a stale artifact is recoverable by regenerating, whereas
    failing the daily run on local file state would be worse than the problem.
    """
    newest = _newest_stored_date(db_path)
    if newest is None:
        checks.append(
            {
                "name": "output_dates",
                "status": "warning",
                "path": str(output_dir),
                "message": "no stored observation date to compare against",
            }
        )
        return
    stale: list[str] = []
    for path in sorted(Path(output_dir).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for value in _date_strings(payload):
            if value > newest:
                stale.append(f"{path.name}:{value}")
                break
    checks.append(
        {
            "name": "output_dates",
            "status": "warning" if stale else "ok",
            "path": str(output_dir),
            "message": (
                f"artifacts dated after the newest stored observation ({newest}): {sorted(stale)}"
                if stale
                else f"no artifact dated after the newest stored observation ({newest})"
            ),
            "stale_artifacts": sorted(stale),
        }
    )


def _check_anchor_basis(
    checks: list[dict[str, Any]],
    db_path: str | Path,
    config: Any,
) -> None:
    """Report whether the discount-rate anchor is current, and on what basis.

    Two things were unmonitored and both are silent by construction:

    * **Anchor age.** RS2 consumes the published anchors and tolerates them for up to
      `anchor_max_age_days: 45`. Nothing on the MRI side said how old they were, so a stale
      discount rate could sit inside that window looking authoritative.
    * **Vintage coverage.** Under the point-in-time basis an anchor resolves against the newest
      stored ALFRED vintage, so a pipeline that stops refreshing vintages produces a number that
      is old while still labelled "as published". The anchor payload carries the staleness, but
      only a health check can tell an operator BEFORE the run.

    Warning, not blocked: a stale anchor is recoverable by rebuilding, and failing the daily run
    over it would be worse than the problem this reports.
    """
    anchor_config_path = getattr(config.anchors, "config_path", "config/anchors.yaml")
    macro_config_path = getattr(config.anchors, "macro_config_path", config.macro.config_path)
    detail: dict[str, Any] = {}
    issues: list[str] = []

    try:
        anchor_config = load_anchor_config(anchor_config_path)
        cost_of_capital = Path(anchor_config.output_dir) / "cost_of_capital_anchor.json"
        payload = json.loads(cost_of_capital.read_text(encoding="utf-8"))
        asof = str(payload.get("asof", ""))[:10]
        detail["anchor_asof"] = asof
        detail["anchor_degraded"] = bool(payload.get("degraded"))
        detail["anchor_scoring_mode"] = payload.get("provenance", {}).get("scoring_mode")
        if asof:
            age = (pd.Timestamp(_today()).normalize() - pd.Timestamp(asof).normalize()).days
            detail["anchor_age_days"] = int(age)
            limit = _anchor_age_limit()
            if age > limit:
                issues.append(f"cost_of_capital anchor is {age} days old (limit {limit})")
        else:
            issues.append("cost_of_capital anchor carries no as-of date")
        if detail["anchor_degraded"]:
            issues.append(
                "cost_of_capital anchor is DEGRADED: "
                f"{payload.get('provenance', {}).get('degradation_reasons')}"
            )
    except FileNotFoundError:
        issues.append("cost_of_capital anchor has never been built")
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f"cost_of_capital anchor unreadable: {exc}")
    except Exception as exc:  # pragma: no cover - defensive health reporting
        issues.append(f"anchor config unreadable: {exc}")

    try:
        evaluation = load_evaluation_config(macro_config_path)
        store = DuckDBStore(db_path)
        store.initialize()
        absence = store.read_vintage_absence()
        keys = store.read_vintage_keys()
        detail["scoring_mode"] = evaluation.scoring_mode
        detail["point_in_time_start"] = evaluation.point_in_time_start
        detail["vintage_pairs"] = int(len(keys))
        detail["known_absent_pairs"] = int(len(absence))
        if keys.empty:
            if evaluation.scoring_mode == "point_in_time":
                issues.append("point-in-time basis configured but the vintage store is empty")
        else:
            newest = pd.to_datetime(keys["realtime_start"], errors="coerce").max()
            lag = int((pd.Timestamp(_today()).normalize() - pd.Timestamp(newest).normalize()).days)
            detail["newest_vintage"] = pd.Timestamp(newest).date().isoformat()
            detail["vintage_lag_days"] = lag
            if evaluation.scoring_mode == "point_in_time":
                limit = evaluation.point_in_time_max_lag_days
                if lag > limit:
                    issues.append(
                        f"newest ALFRED vintage is {lag} days old (limit {limit}); the "
                        "point-in-time basis is stale -- the vintages pipeline step is not "
                        "refreshing"
                    )
    except Exception as exc:  # pragma: no cover - defensive health reporting
        issues.append(f"vintage coverage unavailable: {exc}")

    checks.append(
        {
            "name": "anchor_basis",
            "status": "warning" if issues else "ok",
            "path": str(anchor_config_path),
            "message": "; ".join(issues) if issues else "anchors current and basis evidenced",
            "issues": issues,
            "detail": detail,
        }
    )


def _today() -> str:
    return pd.Timestamp.now(tz="UTC").date().isoformat()


def _anchor_age_limit() -> int:
    """RS2's tolerance for a stale anchor, read from its config when reachable.

    MRI must not invent its own number here: the consumer already decides how old is too old
    (`anchor_max_age_days`), and two limits would eventually disagree.
    """
    rs2_config = Path(os.getenv("RS2_CONFIG_PATH", r"C:\Users\riper\Downloads\RS2 Local\config.json"))
    try:
        payload = json.loads(rs2_config.read_text(encoding="utf-8"))
        value = payload.get("anchor_max_age_days")
        if isinstance(value, int) and value > 0:
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return 45


def _newest_stored_date(db_path: str | Path) -> str | None:
    try:
        store = DuckDBStore(db_path)
        store.initialize()
        frame = store.read_table("historical_regime_timeline")
    except Exception:  # pragma: no cover - defensive health reporting
        return None
    if frame.empty or "date" not in frame.columns:
        return None
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().date().isoformat()


def _date_strings(value: Any, *, depth: int = 0) -> list[str]:
    """ISO dates anywhere in a payload, bounded so a pathological document cannot hang the check."""
    if depth > 6:
        return []
    if isinstance(value, str):
        return [value[:10]] if _ISO_DATE.match(value) else []
    if isinstance(value, dict):
        return [item for v in value.values() for item in _date_strings(v, depth=depth + 1)]
    if isinstance(value, list):
        return [item for v in value[:200] for item in _date_strings(v, depth=depth + 1)]
    return []


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
