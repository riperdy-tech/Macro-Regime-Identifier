"""Alert helper: fire on regime flip or guardrail failure, else stay silent."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_alert", Path(__file__).resolve().parents[1] / "scripts" / "check_alert.py"
)
check_alert = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_alert)  # type: ignore[union-attr]


def _write(outputs: Path, day: str, regime, guardrail="ok"):
    d = outputs / "archive" / day / f"{day}T000000Z-x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "daily_diagnostic_summary.json").write_text(
        json.dumps({"macro": {"reported_regime": regime}, "step_statuses": {"guardrail_status": guardrail}}),
        encoding="utf-8",
    )


def test_no_alert_single_run(tmp_path: Path):
    _write(tmp_path, "2026-05-01", "reflation")
    assert check_alert.alert_message(tmp_path) == ""


def test_no_alert_same_regime(tmp_path: Path):
    _write(tmp_path, "2026-05-01", "reflation")
    _write(tmp_path, "2026-05-02", "reflation")
    assert check_alert.alert_message(tmp_path) == ""


def test_alert_on_regime_change(tmp_path: Path):
    _write(tmp_path, "2026-05-01", "reflation")
    _write(tmp_path, "2026-05-02", "recession")
    msg = check_alert.alert_message(tmp_path)
    assert "regime change: reflation -> recession" in msg


def test_alert_on_guardrail_failure(tmp_path: Path):
    _write(tmp_path, "2026-05-01", "reflation", guardrail="ok")
    _write(tmp_path, "2026-05-02", "reflation", guardrail="failed")
    msg = check_alert.alert_message(tmp_path)
    assert "guardrail status = failed" in msg


def test_no_archive_is_silent(tmp_path: Path):
    assert check_alert.alert_message(tmp_path) == ""
