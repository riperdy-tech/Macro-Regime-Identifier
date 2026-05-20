from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.dashboard_export import export_dashboard_data


runner = CliRunner()


def test_export_dashboard_data_copies_available_files_and_manifest(tmp_path: Path):
    outputs = tmp_path / "outputs"
    data_dir = tmp_path / "dashboard" / "public" / "data"
    outputs.mkdir()
    (outputs / "daily_diagnostic_summary.json").write_text(
        json.dumps(
            {
                "run_date": "2026-05-20",
                "macro": {"date": "2026-05-01"},
            }
        ),
        encoding="utf-8",
    )
    (outputs / "news_score_report.json").write_text(
        json.dumps({"latest_news_scoring_date": "2026-05-16"}),
        encoding="utf-8",
    )

    manifest = export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    assert manifest["data_status"] == "partial"
    assert manifest["latest_run_date"] == "2026-05-20"
    assert manifest["latest_macro_date"] == "2026-05-01"
    assert manifest["latest_news_score_date"] == "2026-05-16"
    assert (data_dir / "daily_diagnostic_summary.json").exists()
    assert (data_dir / "manifest.json").exists()
    assert "current_sector_ranking.json" in manifest["missing_files"]


def test_export_dashboard_data_marks_missing_when_no_outputs(tmp_path: Path):
    manifest = export_dashboard_data(
        outputs_dir=tmp_path / "missing_outputs",
        dashboard_data_dir=tmp_path / "dashboard" / "public" / "data",
    )

    assert manifest["data_status"] == "missing"
    assert manifest["available_files"] == []
    assert "daily_diagnostic_summary.json" in manifest["missing_files"]


def test_export_dashboard_data_cli(tmp_path: Path):
    outputs = tmp_path / "outputs"
    data_dir = tmp_path / "dashboard" / "public" / "data"
    outputs.mkdir()
    (outputs / "daily_diagnostic_summary.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "export-dashboard-data",
            "--outputs-dir",
            str(outputs),
            "--dashboard-data-dir",
            str(data_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"data_status": "partial"' in result.output
    assert (data_dir / "manifest.json").exists()
