from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.dashboard_export import export_dashboard_data
from macro_engine.replay import (
    filter_replay_items,
    load_replay_news_frame,
    replay_news_history,
    replay_window,
)


runner = CliRunner()


def test_replay_date_grouping_and_no_future_leakage(tmp_path: Path):
    news_file = _news_file(tmp_path)
    frame = load_replay_news_frame(news_file)
    start_day, end_day = replay_window(frame, start_date="2026-05-01", end_date="2026-05-03")

    day_one = filter_replay_items(
        frame,
        replay_day=start_day,
        start_day=start_day,
        include_prior_items=True,
        max_items=10,
    )
    day_three = filter_replay_items(
        frame,
        replay_day=end_day,
        start_day=start_day,
        include_prior_items=True,
        max_items=10,
    )

    assert len(day_one) == 1
    assert day_one["title"].tolist() == ["Inflation pressure rises"]
    assert len(day_three) == 3
    assert pd.to_datetime(day_one["published_at"], utc=True).dt.date.max() == start_day


def test_replay_missing_news_file_writes_blocked_summary(tmp_path: Path):
    result = replay_news_history(
        config_path=_daily_config(tmp_path),
        news_file=tmp_path / "missing.csv",
        output_dir=tmp_path / "replay",
        db_path=tmp_path / "macro.duckdb",
    )

    assert result.status == "blocked"
    assert result.blocked_reason
    payload = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert "not found" in payload["blocked_reason"]


def test_replay_mock_path_archives_by_replay_date_and_updates_history(tmp_path: Path):
    news_file = _news_file(tmp_path)
    result = replay_news_history(
        config_path=_daily_config(tmp_path),
        news_file=news_file,
        start_date="2026-05-01",
        end_date="2026-05-03",
        db_path=tmp_path / "macro.duckdb",
        output_dir=tmp_path / "outputs" / "replay",
        archive=True,
        include_prior_items=False,
        max_items_per_replay_day=10,
        services=_daily_services(tmp_path),
    )

    assert result.status == "success"
    assert result.replay_days == 3
    assert len(result.replay_runs) == 3
    assert not (tmp_path / "macro.duckdb").exists()
    assert (tmp_path / "outputs" / "replay" / "tmp" / "replay_2026-05-01.duckdb").exists()
    assert any(row["selected_item_count"] == 0 for row in result.replay_runs)
    for row in result.replay_runs:
        archive_path = Path(row["archive_path"])
        assert archive_path.exists()
        assert archive_path.parent.name == row["replay_date"]
        summary = json.loads((archive_path / "daily_diagnostic_summary.json").read_text(encoding="utf-8"))
        assert summary["run_mode"] == "replay"
        assert summary["replay"]["replay_date"] == row["replay_date"]

    manifest = export_dashboard_data(
        outputs_dir=tmp_path / "outputs",
        dashboard_data_dir=tmp_path / "dashboard" / "public" / "data",
    )
    history = json.loads(
        (tmp_path / "dashboard" / "public" / "data" / "history_index.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["data_status"] == "missing"
    assert history["runs"][0]["run_mode"] == "replay"
    assert history["runs"][0]["replay_date"] == "2026-05-03"


def test_replay_cli_blocked_missing_file(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "replay-news-history",
            "--config",
            str(_daily_config(tmp_path)),
            "--news-file",
            str(tmp_path / "missing.csv"),
            "--output-dir",
            str(tmp_path / "replay"),
            "--db-path",
            str(tmp_path / "macro.duckdb"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"status": "blocked"' in result.output


def _news_file(tmp_path: Path) -> Path:
    path = tmp_path / "news.csv"
    pd.DataFrame(
        [
            {
                "title": "Inflation pressure rises",
                "body": "Inflation data showed renewed pressure across services and goods categories.",
                "source": "source_a",
                "source_url": "https://example.com/a",
                "published_at": "2026-05-01T12:00:00Z",
                "source_group": "inflation_rates",
            },
            {
                "title": "Energy supply disruption",
                "body": "Energy markets reacted to a reported supply disruption in crude shipments.",
                "source": "source_b",
                "source_url": "https://example.com/b",
                "published_at": "2026-05-03T12:00:00Z",
                "source_group": "energy_commodities",
            },
            {
                "title": "Credit conditions tighten",
                "body": "Banks reported tighter credit conditions for business borrowers.",
                "source": "source_c",
                "source_url": "https://example.com/c",
                "published_at": "2026-05-03T14:00:00Z",
                "source_group": "credit_financial_conditions",
            },
        ]
    ).to_csv(path, index=False)
    return path


def _daily_config(tmp_path: Path) -> Path:
    path = tmp_path / "daily.yaml"
    path.write_text(
        f"""
macro:
  enabled: false
sector:
  enabled: false
news:
  enabled: true
  source_profile: replay_local_csv
  news_sources_config: unused.yaml
  news_ai_config: config/news_ai.yaml
  news_themes_config: config/news_themes.yaml
  news_scoring_config: config/news_scoring.yaml
  allow_live_ai: false
  mock_mode_default: true
combined:
  enabled: false
monitoring:
  enabled: false
outputs:
  archive_enabled: true
  archive_root: {(tmp_path / "outputs" / "archive").as_posix()}
safety:
  allow_success_with_warnings: true
""",
        encoding="utf-8",
    )
    return path


def _daily_services(tmp_path: Path) -> dict:
    def write_pair(name: str):
        json_path = tmp_path / f"{name}.json"
        md_path = tmp_path / f"{name}.md"
        json_path.write_text("{}", encoding="utf-8")
        md_path.write_text(f"# {name}\n\ndiagnostic output only\n", encoding="utf-8")
        return json_path, md_path

    return {
        "write_news_report": lambda **_: write_pair("news_report"),
        "build_news_scores": lambda **_: None,
        "write_news_score_report": lambda **_: write_pair("news_score"),
    }
