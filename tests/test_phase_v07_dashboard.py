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
    assert (data_dir / "history_index.json").exists()
    assert "history_index.json" in manifest["available_files"]
    assert "current_sector_ranking.json" in manifest["missing_files"]


def test_export_dashboard_data_marks_missing_when_no_outputs(tmp_path: Path):
    manifest = export_dashboard_data(
        outputs_dir=tmp_path / "missing_outputs",
        dashboard_data_dir=tmp_path / "dashboard" / "public" / "data",
    )

    assert manifest["data_status"] == "missing"
    assert manifest["available_files"] == ["history_index.json"]
    assert "daily_diagnostic_summary.json" in manifest["missing_files"]
    history = json.loads(
        (tmp_path / "dashboard" / "public" / "data" / "history_index.json").read_text(encoding="utf-8")
    )
    assert history["history_status"] == "empty"
    assert history["runs"] == []


def test_export_dashboard_data_writes_history_index_from_archives(tmp_path: Path):
    outputs = tmp_path / "outputs"
    archive_run = outputs / "archive" / "2026-05-20" / "sample-run"
    data_dir = tmp_path / "dashboard" / "public" / "data"
    archive_run.mkdir(parents=True)
    (archive_run / "daily_diagnostic_summary.json").write_text(
        json.dumps(
            {
                "run_id": "sample-run",
                "run_date": "2026-05-20",
                "status": "success",
                "archive_path": "outputs/archive/2026-05-20/sample-run",
                "macro": {"reported_regime": "reflation", "confidence": 0.25},
                "combined_top": [
                    {"sector_id": "energy", "rank": 1},
                    {"sector_id": "materials", "rank": 2},
                ],
                "run_mode": "replay",
                "replay": {"replay_date": "2026-05-20"},
                "monitoring": {"success_rate": 1.0, "max_rank_change": 1},
                "step_statuses": {"guardrail_status": "passed"},
                "warnings": ["source coverage thin"],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    history = json.loads((data_dir / "history_index.json").read_text(encoding="utf-8"))
    assert history["history_status"] == "available"
    assert history["total_runs"] == 1
    assert history["runs"][0]["run_id"] == "sample-run"
    assert history["runs"][0]["run_mode"] == "replay"
    assert history["runs"][0]["replay_date"] == "2026-05-20"
    assert history["runs"][0]["macro_regime"] == "reflation"
    assert history["runs"][0]["top_combined_sectors"] == ["energy", "materials"]
    assert history["runs"][0]["warning_count"] == 1


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
