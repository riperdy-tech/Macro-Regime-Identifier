#!/usr/bin/env python3
"""Emit an alert line when the macro regime FLIPS or a guardrail FAILS.

Reads the archived daily run summaries (committed by the daily workflow's
run-audit persist step), compares the two most recent runs, and prints a short
message to stdout if either:
  - the reported regime changed vs the previous run, or
  - the latest run's guardrail status is not ok.

Prints nothing (and exits 0) when there is nothing to alert. The workflow turns
any printed line into a GitHub Issue.
"""

from __future__ import annotations

from datetime import UTC, datetime
import glob
import json
from pathlib import Path
import sys
from typing import Any

_OK_GUARDRAIL = {"ok", "success", "pass", "passed", "", None}


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _regime(summary: dict[str, Any]) -> str | None:
    macro = summary.get("macro") if isinstance(summary.get("macro"), dict) else {}
    return macro.get("reported_regime") or macro.get("dominant_regime")


def _guardrail(summary: dict[str, Any]) -> Any:
    steps = summary.get("step_statuses") if isinstance(summary.get("step_statuses"), dict) else {}
    return steps.get("guardrail_status") or summary.get("guardrail_status")


def alert_message(
    outputs_dir: str | Path = "outputs",
    *,
    today: str | None = None,
) -> str:
    """Return the alert text, or '' if nothing to alert."""
    archive_dir = Path(outputs_dir) / "archive"
    pattern = str(archive_dir / "*" / "*" / "daily_diagnostic_summary.json")
    summaries = sorted(glob.glob(pattern))

    today_str = today or datetime.now(UTC).date().isoformat()
    today_pattern = str(archive_dir / today_str / "*" / "daily_diagnostic_summary.json")
    today_summaries = glob.glob(today_pattern)

    alerts: list[str] = []

    # Check 1: no archived daily_diagnostic_summary.json for today's UTC date.
    # OA-1: the DuckDB cache now saves if: always(), so a missing archive means
    # the run itself did not finish, not that the cache save was skipped.
    if not today_summaries:
        alerts.append("daily run did not complete (no summary archived for today)")

    if summaries:
        current = _load(summaries[-1])
        guard = _guardrail(current)
        if guard not in _OK_GUARDRAIL:
            alerts.append(f"guardrail status = {guard}")
        if len(summaries) >= 2:
            prev_regime = _regime(_load(summaries[-2]))
            regime = _regime(current)
            if regime and prev_regime and regime != prev_regime:
                alerts.append(f"regime change: {prev_regime} -> {regime}")

        # N1.6 / OA-4: a failed run status, with the first recorded error.
        if current.get("status") == "failed":
            errors = current.get("errors") or []
            first_error = str(errors[0]) if errors else "no error recorded"
            alerts.append(f"daily status = failed: {first_error}")

        # N1.6: news_health degraded or failed, with its reasons.
        news_health = current.get("news_health") or {}
        health_status = news_health.get("status")
        if health_status in ("degraded", "failed"):
            reasons = ", ".join(news_health.get("reasons") or []) or "no reasons recorded"
            alerts.append(f"news_health status = {health_status}: {reasons}")

        # Check 2: pipeline shortfall warnings in latest summary
        warnings = current.get("warnings") or []
        shortfall_warnings = [
            str(w)
            for w in warnings
            if any(k in str(w) for k in ("vintage_partial", "deadline_reached", "news_classification_deadline"))
        ]
        if shortfall_warnings:
            alerts.append(f"pipeline warnings: {', '.join(shortfall_warnings)}")

    return "; ".join(alerts)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "outputs"
    today_arg = sys.argv[2] if len(sys.argv) > 2 else None
    message = alert_message(target, today=today_arg)
    if message:
        print(message)
