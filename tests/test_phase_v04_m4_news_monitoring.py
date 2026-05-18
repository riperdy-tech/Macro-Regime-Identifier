from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.news.config import load_news_monitoring_config
from macro_engine.news.monitoring import (
    build_classification_quality_run,
    build_input_quality_run,
    build_news_monitoring_report,
    build_overlay_monitoring_run,
    write_news_monitoring_report,
)
from macro_engine.storage.duckdb_store import DuckDBStore


runner = CliRunner()


def test_news_monitoring_config_and_cli():
    config = load_news_monitoring_config("config/news_monitoring.yaml")
    assert config.source_profile == "synthetic_sample"
    assert {group.group_id for group in config.source_groups} >= {
        "macro_general",
        "inflation_rates",
        "technology_ai",
    }

    result = runner.invoke(
        app,
        ["validate-news-monitoring", "--config", "config/news_monitoring.yaml"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["valid"] is True
    assert payload["source_group_count"] >= 12


def test_input_quality_status_and_concentration_warning(tmp_path: Path):
    config = load_news_monitoring_config(_monitoring_config(tmp_path))
    summary = {
        "raw_item_count": 4,
        "unique_item_count": 4,
        "duplicate_count": 0,
        "date_start": "2026-05-01T00:00:00+00:00",
        "date_end": "2026-05-01T00:00:00+00:00",
        "item_count_by_source": {"single_source": 4},
        "item_count_by_day": {"2026-05-01": 4},
        "sources": [
            {
                "short_body_count": 1,
                "very_old_count": 0,
                "future_published_at_count": 0,
            }
        ],
        "warnings": ["single_source: 1 items have very short body text"],
    }

    frame = build_input_quality_run(
        config=config,
        profile="unit",
        input_summary=summary,
        run_id="run_1",
    )
    row = frame.iloc[-1]
    details = json.loads(row["details_json"])

    assert row["quality_status"] == "warning"
    assert row["short_body_count"] == 1
    assert any("one source exceeds" in warning for warning in details["warnings"])
    assert any("date coverage" in warning for warning in details["warnings"])


def test_classification_quality_retry_repair_and_failures(tmp_path: Path):
    config = load_news_monitoring_config(_monitoring_config(tmp_path))
    classifications = pd.DataFrame(
        [
            {
                "classification_status": "success",
                "ai_provider": "deepseek",
                "ai_model": "deepseek-v4-flash",
                "raw_ai_response_json": json.dumps(
                    {"retry_count": 1, "was_repaired": True, "validation_errors": []}
                ),
                "error_message": None,
            },
            {
                "classification_status": "error",
                "ai_provider": "deepseek",
                "ai_model": "deepseek-v4-flash",
                "raw_ai_response_json": json.dumps(
                    {"retry_count": 0, "was_repaired": False, "validation_errors": ["bad enum"]}
                ),
                "error_message": "schema validation failed",
            },
        ]
    )

    frame = build_classification_quality_run(
        config=config,
        classifications=classifications,
        run_id="run_1",
    )
    row = frame.iloc[-1]
    modes = json.loads(row["top_failure_modes_json"])

    assert row["success_count"] == 1
    assert row["failure_count"] == 1
    assert row["retry_count"] == 1
    assert row["repaired_count"] == 1
    assert row["quality_status"] == "warning"
    assert modes


def test_overlay_monitoring_rank_change_and_report(tmp_path: Path):
    config_path = _monitoring_config(tmp_path)
    config = load_news_monitoring_config(config_path)
    config.quality_thresholds.max_rank_change = 0
    overlay = build_overlay_monitoring_run(
        config=config,
        daily_theme_scores=_daily_theme_scores(),
        daily_sector_scores=_daily_sector_scores(),
        combined_diagnostics=_combined_diagnostics(),
        sector_scores=_sector_scores(),
        run_id="run_1",
    )
    row = overlay.iloc[-1]
    changes = json.loads(row["sectors_changed_by_news_json"])

    assert row["max_rank_change"] == 1
    assert row["overlay_status"] == "warning"
    assert changes[0]["sector_id"] in {"energy", "real_estate"}

    input_quality = build_input_quality_run(
        config=config,
        profile="unit",
        input_summary={
            "raw_item_count": 2,
            "unique_item_count": 2,
            "duplicate_count": 0,
            "date_start": "2026-05-01T00:00:00+00:00",
            "date_end": "2026-05-02T00:00:00+00:00",
            "item_count_by_source": {"source_a": 1, "source_b": 1},
            "item_count_by_day": {"2026-05-01": 1, "2026-05-02": 1},
            "sources": [{"short_body_count": 0, "very_old_count": 0, "future_published_at_count": 0}],
            "warnings": [],
        },
        run_id="run_1",
    )
    classification = build_classification_quality_run(
        config=config,
        classifications=pd.DataFrame(
            [
                {
                    "classification_status": "success",
                    "ai_provider": "mock",
                    "ai_model": "mock",
                    "raw_ai_response_json": "{}",
                    "error_message": None,
                }
            ]
        ),
        run_id="run_1",
    )
    payload = build_news_monitoring_report(
        config=config,
        input_quality_runs=input_quality,
        classification_quality_runs=classification,
        overlay_monitoring=overlay,
    )
    assert payload["valid"] is True
    assert payload["overlay_monitoring"]["max_rank_change"] == 1

    store = DuckDBStore(tmp_path / "macro.duckdb")
    store.initialize()
    store.upsert_news_monitoring_outputs(input_quality, classification, overlay)
    json_path, markdown_path = write_news_monitoring_report(
        config_path=config_path,
        db_path=tmp_path / "macro.duckdb",
    )
    assert json.loads(json_path.read_text(encoding="utf-8"))["valid"] is True
    markdown = markdown_path.read_text(encoding="utf-8").lower()
    assert "news monitoring report" in markdown
    assert not any(
        term in markdown
        for term in [
            "buy",
            "sell",
            "overweight",
            "underweight",
            "avoid",
            "recommendation",
            "recommend",
            "trade",
            "position sizing",
            "portfolio allocation",
        ]
    )


def _monitoring_config(tmp_path: Path) -> Path:
    path = tmp_path / "news_monitoring.yaml"
    path.write_text(
        f"""
news_monitoring:
  output_dir: {(tmp_path / "outputs").as_posix()}
  news_sources_config: config/news_sources.yaml
  news_ai_config: config/news_ai.yaml
  news_themes_config: config/news_themes.yaml
  news_scoring_config: config/news_scoring.yaml
  sector_news_integration_config: config/sector_news_integration.yaml
  source_profile: synthetic_sample
  source_groups:
    - group_id: macro_general
      target_item_count: 2
    - group_id: inflation_rates
      target_item_count: 2
  quality_thresholds:
    min_body_length: 25
    max_failed_classification_rate: 0.10
    max_retry_rate: 0.20
    max_repair_rate: 0.20
    min_source_count: 2
    min_date_coverage_days: 2
    max_source_share: 0.60
    max_theme_share: 0.60
    max_sector_share: 0.60
    max_date_share: 0.60
    max_old_item_share: 0.20
    max_rank_change: 3
    max_avg_abs_rank_change: 1.50
""",
        encoding="utf-8",
    )
    return path


def _daily_theme_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "score_date": "2026-05-02",
                "theme_id": "inflation_pressure",
                "raw_score": 0.30,
                "adjusted_score": 0.30,
                "item_count": 2,
                "avg_confidence": 0.8,
                "avg_severity": 0.7,
                "top_news_ids_json": json.dumps(["news_1"]),
                "created_at": "2026-05-02T01:00:00Z",
            }
        ]
    )


def _daily_sector_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "score_date": "2026-05-02",
                "sector_id": "energy",
                "raw_news_score": 0.4,
                "adjusted_news_score": 0.4,
                "positive_item_count": 2,
                "negative_item_count": 0,
                "neutral_item_count": 0,
                "avg_confidence": 0.8,
                "avg_severity": 0.7,
                "top_news_ids_json": json.dumps(["news_1"]),
                "created_at": "2026-05-02T01:00:00Z",
            },
            {
                "score_date": "2026-05-02",
                "sector_id": "real_estate",
                "raw_news_score": -0.2,
                "adjusted_news_score": -0.2,
                "positive_item_count": 0,
                "negative_item_count": 1,
                "neutral_item_count": 0,
                "avg_confidence": 0.8,
                "avg_severity": 0.7,
                "top_news_ids_json": json.dumps(["news_2"]),
                "created_at": "2026-05-02T01:00:00Z",
            },
        ]
    )


def _combined_diagnostics() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "diagnostic_date": "2026-05-02",
                "sector_id": "energy",
                "sector_macro_score": 0.2,
                "sector_news_score": 0.4,
                "combined_score": 0.1,
                "macro_component_weight": 0.75,
                "news_component_weight": 0.25,
                "news_item_count": 2,
                "news_confidence": 0.8,
                "diagnostic_confidence": 0.5,
                "rank": 2,
                "created_at": "2026-05-02T01:00:00Z",
            },
            {
                "diagnostic_date": "2026-05-02",
                "sector_id": "real_estate",
                "sector_macro_score": -0.2,
                "sector_news_score": -0.2,
                "combined_score": 0.2,
                "macro_component_weight": 0.75,
                "news_component_weight": 0.25,
                "news_item_count": 1,
                "news_confidence": 0.8,
                "diagnostic_confidence": 0.5,
                "rank": 1,
                "created_at": "2026-05-02T01:00:00Z",
            },
        ]
    )


def _sector_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sector_id": "energy",
                "date": "2026-05-01",
                "raw_sector_score": 0.5,
                "confidence_adjusted_score": 0.3,
                "rank": 1,
                "macro_reported_regime": "reflation",
                "macro_raw_dominant_regime": "reflation",
                "macro_confidence": 0.2,
                "valid": True,
                "reason": "ok",
            },
            {
                "sector_id": "real_estate",
                "date": "2026-05-01",
                "raw_sector_score": -0.4,
                "confidence_adjusted_score": -0.2,
                "rank": 2,
                "macro_reported_regime": "reflation",
                "macro_raw_dominant_regime": "reflation",
                "macro_confidence": 0.2,
                "valid": True,
                "reason": "ok",
            },
        ]
    )
