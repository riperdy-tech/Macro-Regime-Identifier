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

import glob
import json
import sys
from pathlib import Path
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


def alert_message(outputs_dir: str | Path = "outputs") -> str:
    """Return the alert text, or '' if nothing to alert."""
    pattern = str(Path(outputs_dir) / "archive" / "*" / "*" / "daily_diagnostic_summary.json")
    summaries = sorted(glob.glob(pattern))
    if not summaries:
        return ""
    current = _load(summaries[-1])
    alerts: list[str] = []
    guard = _guardrail(current)
    if guard not in _OK_GUARDRAIL:
        alerts.append(f"guardrail status = {guard}")
    if len(summaries) >= 2:
        prev_regime = _regime(_load(summaries[-2]))
        regime = _regime(current)
        if regime and prev_regime and regime != prev_regime:
            alerts.append(f"regime change: {prev_regime} -> {regime}")
    return "; ".join(alerts)


if __name__ == "__main__":
    message = alert_message(sys.argv[1] if len(sys.argv) > 1 else "outputs")
    if message:
        print(message)
