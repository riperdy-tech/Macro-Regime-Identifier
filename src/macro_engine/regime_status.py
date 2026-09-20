from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from macro_engine.anchors.service import anchor_status


DISCLAIMER = (
    "This is a diagnostic macro-regime status snapshot. It is not investment advice, "
    "market action guidance, execution guidance, or instructions for changing holdings."
)
MONITOR_READY_LABELS = {"monitor_ready", "validation_candidate"}
# A monthly artifact. Matches RS2's regime_max_age_days, so a regime snapshot RS2 would reject
# is flagged here too, rather than only downstream.
CURRENT_REGIME_MAX_AGE_DAYS = 45


def _regime_date_health(current: dict[str, Any]) -> dict[str, Any]:
    """Dating health of current_regime.json, surfaced rather than assumed.

    `current_regime.json` has been observed carrying a synthetic FUTURE date (2031-08-01). RS2
    already rejects it -- `_fresh()` treats a negative age as invalid -- so the defect has not
    propagated, but this status surface and the dashboard had no such gate. A consumer cannot
    tell a future-dated snapshot from a current one unless it is told, so the payload tells it.
    """
    raw = current.get("date")
    blank = {
        "current_regime_date": None,
        "current_regime_age_days": None,
        "current_regime_stale": None,
        "current_regime_future_dated": None,
    }
    if not raw:
        return blank
    try:
        observed = datetime.fromisoformat(str(raw)[:19])
    except ValueError:
        return {**blank, "current_regime_date": str(raw)}
    age = (datetime.now(UTC).replace(tzinfo=None) - observed).days
    return {
        "current_regime_date": observed.date().isoformat(),
        "current_regime_age_days": age,
        "current_regime_stale": age > CURRENT_REGIME_MAX_AGE_DAYS,
        "current_regime_future_dated": age < 0,
    }


def build_regime_status(*, outputs_dir: str | Path = "outputs") -> dict[str, Any]:
    output_dir = Path(outputs_dir)
    current = _read_json(output_dir / "current_regime.json")
    daily = _read_json(output_dir / "daily_diagnostic_summary.json")
    accumulation = _read_json(output_dir / "news_accumulation_report.json")
    secular = _read_json(output_dir / "secular_theme_scores.json")
    anchors = anchor_status(outputs_dir=output_dir)

    macro = _macro_status(current=current, daily=daily)
    readiness_label = accumulation.get("readiness_label")
    monitor_ready = readiness_label in MONITOR_READY_LABELS

    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "dominant_regime": macro.get("dominant_regime"),
        "regime_probability": macro.get("regime_probability"),
        "confidence": macro.get("confidence"),
        "raw_dominant_regime": macro.get("raw_dominant_regime"),
        "raw_regime_probability": macro.get("raw_regime_probability"),
        # Additive dating health: a future-dated or stale regime snapshot is reported, never
        # silently presented as current.
        **_regime_date_health(current),
        "monitor_ready": monitor_ready,
        "readiness_label": readiness_label or "missing",
        "secular_theme_scores": secular.get("themes") or {},
        "secular_theme_computed_at": secular.get("computed_at"),
        # Additive: absent anchors surface as None rather than as a failure, the same
        # tolerance this function already applies to secular_theme_scores.json.
        "capital_market_anchors": {
            "available": anchors["available"],
            "degraded": anchors["degraded"],
            "level_cost_of_equity": anchors["level_cost_of_equity"],
            "level_cost_of_equity_source": anchors["level_cost_of_equity_source"],
            "terminal_g_suggestion": anchors["terminal_g_suggestion"],
            "cost_of_capital": anchors["cost_of_capital"],
            "long_run_growth": anchors["long_run_growth"],
            "sector_multiple_bands": anchors["sector_multiple_bands"],
        },
        "source_files": {
            "current_regime": "current_regime.json" if current else None,
            "daily_diagnostic_summary": "daily_diagnostic_summary.json" if daily else None,
            "news_accumulation_report": "news_accumulation_report.json" if accumulation else None,
            "secular_theme_scores": "secular_theme_scores.json" if secular else None,
            **anchors["source_files"],
        },
        "status": "monitor_ready" if monitor_ready else "diagnostic_only",
        "disclaimer": DISCLAIMER,
    }


def write_regime_status(*, outputs_dir: str | Path = "outputs") -> Path:
    output_dir = Path(outputs_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "regime_status.json"
    path.write_text(
        json.dumps(build_regime_status(outputs_dir=output_dir), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _macro_status(*, current: dict[str, Any], daily: dict[str, Any]) -> dict[str, Any]:
    if current.get("valid"):
        return {
            "dominant_regime": current.get("reported_regime") or current.get("dominant_regime"),
            "regime_probability": current.get("reported_regime_probability")
            or current.get("dominant_probability"),
            "confidence": current.get("reported_confidence") or current.get("confidence"),
            "raw_dominant_regime": current.get("raw_dominant_regime"),
            "raw_regime_probability": current.get("raw_dominant_probability"),
        }

    macro = daily.get("macro")
    if isinstance(macro, dict):
        return {
            "dominant_regime": macro.get("reported_regime") or macro.get("dominant_regime"),
            "regime_probability": macro.get("reported_regime_probability")
            or macro.get("dominant_probability"),
            "confidence": macro.get("reported_confidence") or macro.get("confidence"),
            "raw_dominant_regime": macro.get("raw_dominant_regime"),
            "raw_regime_probability": macro.get("raw_dominant_probability"),
        }
    return {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
