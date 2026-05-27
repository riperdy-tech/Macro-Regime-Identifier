from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.regime_status import build_regime_status, write_regime_status


runner = CliRunner()


def test_build_regime_status_from_outputs(tmp_path: Path):
    (tmp_path / "current_regime.json").write_text(
        json.dumps(
            {
                "valid": True,
                "reported_regime": "reflation",
                "reported_regime_probability": 0.42,
                "reported_confidence": 0.12,
                "raw_dominant_regime": "tightening",
                "raw_dominant_probability": 0.39,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "news_accumulation_report.json").write_text(
        json.dumps({"readiness_label": "monitor_ready"}),
        encoding="utf-8",
    )
    (tmp_path / "secular_theme_scores.json").write_text(
        json.dumps(
            {
                "computed_at": "2026-05-27T00:00:00+00:00",
                "themes": {"ai_compute": {"score": 0.5, "item_count": 3}},
            }
        ),
        encoding="utf-8",
    )

    status = build_regime_status(outputs_dir=tmp_path)

    assert status["dominant_regime"] == "reflation"
    assert status["regime_probability"] == 0.42
    assert status["raw_dominant_regime"] == "tightening"
    assert status["monitor_ready"] is True
    assert status["status"] == "monitor_ready"
    assert status["secular_theme_scores"]["ai_compute"]["score"] == 0.5
    assert "not investment advice" in status["disclaimer"]


def test_build_regime_status_falls_back_to_daily_summary(tmp_path: Path):
    (tmp_path / "daily_diagnostic_summary.json").write_text(
        json.dumps(
            {
                "macro": {
                    "reported_regime": "goldilocks",
                    "reported_regime_probability": 0.31,
                    "reported_confidence": 0.08,
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "news_accumulation_report.json").write_text(
        json.dumps({"readiness_label": "insufficient_history"}),
        encoding="utf-8",
    )

    status = build_regime_status(outputs_dir=tmp_path)

    assert status["dominant_regime"] == "goldilocks"
    assert status["regime_probability"] == 0.31
    assert status["monitor_ready"] is False
    assert status["status"] == "diagnostic_only"


def test_write_regime_status_cli(tmp_path: Path):
    (tmp_path / "news_accumulation_report.json").write_text(
        json.dumps({"readiness_label": "validation_candidate"}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["write-regime-status", "--outputs-dir", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    output_path = tmp_path / "regime_status.json"
    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8"))["monitor_ready"] is True


def test_write_regime_status_function(tmp_path: Path):
    path = write_regime_status(outputs_dir=tmp_path)
    assert path == tmp_path / "regime_status.json"
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "diagnostic_only"
