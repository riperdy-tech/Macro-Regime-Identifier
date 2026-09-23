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


def _write(
    outputs: Path,
    day: str,
    regime,
    guardrail="ok",
    warnings=None,
    status=None,
    errors=None,
    news_health=None,
):
    d = outputs / "archive" / day / f"{day}T000000Z-x"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "macro": {"reported_regime": regime},
        "step_statuses": {"guardrail_status": guardrail},
        "warnings": warnings or [],
        "status": status,
        "errors": errors or [],
        "news_health": news_health or {},
    }
    (d / "daily_diagnostic_summary.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_no_alert_single_run(tmp_path: Path):
    _write(tmp_path, "2026-05-01", "reflation")
    assert check_alert.alert_message(tmp_path, today="2026-05-01") == ""


def test_no_alert_same_regime(tmp_path: Path):
    _write(tmp_path, "2026-05-01", "reflation")
    _write(tmp_path, "2026-05-02", "reflation")
    assert check_alert.alert_message(tmp_path, today="2026-05-02") == ""


def test_alert_on_regime_change(tmp_path: Path):
    _write(tmp_path, "2026-05-01", "reflation")
    _write(tmp_path, "2026-05-02", "recession")
    msg = check_alert.alert_message(tmp_path, today="2026-05-02")
    assert "regime change: reflation -> recession" in msg


def test_alert_on_guardrail_failure(tmp_path: Path):
    _write(tmp_path, "2026-05-01", "reflation", guardrail="ok")
    _write(tmp_path, "2026-05-02", "reflation", guardrail="failed")
    msg = check_alert.alert_message(tmp_path, today="2026-05-02")
    assert "guardrail status = failed" in msg


def test_alert_on_missing_today_archive(tmp_path: Path):
    # No archive for today
    msg = check_alert.alert_message(tmp_path, today="2026-05-01")
    assert "daily run did not complete (no summary archived for today)" in msg


def test_alert_on_failed_status_includes_first_error(tmp_path: Path):
    _write(
        tmp_path,
        "2026-05-01",
        "reflation",
        status="failed",
        errors=["news_history_hydrate: boom", "second error"],
    )
    msg = check_alert.alert_message(tmp_path, today="2026-05-01")
    assert "daily status = failed: news_history_hydrate: boom" in msg


def test_no_alert_on_success_status(tmp_path: Path):
    _write(tmp_path, "2026-05-01", "reflation", status="success")
    msg = check_alert.alert_message(tmp_path, today="2026-05-01")
    assert msg == ""


def test_alert_on_news_health_failed(tmp_path: Path):
    _write(
        tmp_path,
        "2026-05-01",
        "reflation",
        status="failed",
        news_health={"status": "failed", "reasons": ["news_blackout"]},
    )
    msg = check_alert.alert_message(tmp_path, today="2026-05-01")
    assert "news_health status = failed: news_blackout" in msg


def test_alert_on_news_health_degraded(tmp_path: Path):
    _write(
        tmp_path,
        "2026-05-01",
        "reflation",
        status="success_with_warnings",
        news_health={"status": "degraded", "reasons": ["source_dead:marketwatch_pulse_rss"]},
    )
    msg = check_alert.alert_message(tmp_path, today="2026-05-01")
    assert "news_health status = degraded: source_dead:marketwatch_pulse_rss" in msg


def test_no_alert_on_news_health_ok(tmp_path: Path):
    _write(
        tmp_path,
        "2026-05-01",
        "reflation",
        status="success",
        news_health={"status": "ok", "reasons": []},
    )
    msg = check_alert.alert_message(tmp_path, today="2026-05-01")
    assert msg == ""


def test_alert_on_vintage_partial_and_deadline_warnings(tmp_path: Path):
    _write(
        tmp_path,
        "2026-05-01",
        "reflation",
        warnings=["vintage_partial:deferred=5:frontier=2020-01-01"],
    )
    msg = check_alert.alert_message(tmp_path, today="2026-05-01")
    assert "vintage_partial" in msg
