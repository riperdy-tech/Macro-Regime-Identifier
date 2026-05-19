from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from typer.testing import CliRunner

from macro_engine.accumulation import (
    build_news_accumulation_outputs,
    readiness_label,
    write_news_accumulation_report,
)
from macro_engine.cli import app
from macro_engine.daily import run_daily_diagnostic
from macro_engine.guardrails import audit_markdown_reports
from macro_engine.operations_config import (
    load_daily_pipeline_config,
    load_news_accumulation_config,
)
from macro_engine.storage.duckdb_store import DuckDBStore


runner = CliRunner()


def test_daily_pipeline_config_and_guardrail_audit(tmp_path: Path):
    config = load_daily_pipeline_config("config/daily_pipeline.yaml")
    assert config.news.mock_mode_default is True
    assert config.outputs.archive_enabled is True

    clean = tmp_path / "clean.md"
    clean.write_text("diagnostic tailwind only", encoding="utf-8")
    assert audit_markdown_reports([clean]).passed is True

    bad = tmp_path / "bad.md"
    bad.write_text("This report says buy something.", encoding="utf-8")
    audit = audit_markdown_reports([bad])
    assert audit.passed is False
    assert audit.violations[0]["term"] == "buy"


def test_run_daily_diagnostic_with_mocked_services(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    config_path = _daily_config(tmp_path)
    services = _daily_services(tmp_path)

    result = run_daily_diagnostic(
        config_path=config_path,
        db_path=db_path,
        run_date="2026-05-18",
        archive=True,
        services=services,
    )
    store = DuckDBStore(db_path)
    store.initialize()
    runs = store.read_table("daily_diagnostic_runs")

    assert result.status == "success"
    assert result.archive_path is not None
    assert (Path(result.archive_path) / "daily_diagnostic_summary.md").exists()
    assert not runs.empty
    assert runs.iloc[-1]["guardrail_status"] == "passed"


def test_run_daily_diagnostic_records_guardrail_failure(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    config_path = _daily_config(tmp_path)
    services = _daily_services(tmp_path, forbidden_report=True)

    result = run_daily_diagnostic(
        config_path=config_path,
        db_path=db_path,
        run_date="2026-05-18",
        archive=False,
        services=services,
    )

    assert result.status == "failed"
    assert any("guardrail" in error.lower() or "buy" in error.lower() for error in result.errors)


def test_run_daily_diagnostic_live_ai_uses_bounded_classification(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    config_path = _daily_config(tmp_path, allow_live_ai=True)
    services = _daily_services(tmp_path)
    captured: dict = {}

    def classify_news(**kwargs):
        captured.update(kwargs)
        return {}

    services["classify_news"] = classify_news
    result = run_daily_diagnostic(
        config_path=config_path,
        db_path=db_path,
        run_date="2026-05-19",
        live_ai=True,
        services=services,
    )

    assert result.status == "success"
    assert captured["limit"] == 25
    assert captured["only_unclassified"] is True
    assert captured["progress"] is True


def test_news_accumulation_outputs_and_report(tmp_path: Path):
    config = load_news_accumulation_config("config/news_accumulation.yaml")
    result = build_news_accumulation_outputs(
        config=config,
        news_items=_news_items(),
        classifications=_classifications(),
        daily_theme_scores=_daily_theme_scores(),
        daily_sector_scores=_daily_sector_scores(),
        combined_diagnostics=_combined_diagnostics(),
        sector_scores=_sector_scores(),
        run_date=pd.Timestamp("2026-05-18").date(),
    )

    assert result.readiness_label == "insufficient_history"
    assert result.runs.iloc[-1]["classified_items"] == 2
    assert not result.news_history.empty
    assert not result.combined_history.empty
    assert readiness_label(run_dates=65, classified_items=500, source_count=5) == "validation_candidate"

    store = DuckDBStore(tmp_path / "macro.duckdb")
    store.initialize()
    store.upsert_news_items(_news_items())
    store.replace_news_classifications(_classifications(), pd.DataFrame(), pd.DataFrame())
    store.upsert_news_accumulation_outputs(
        result.runs,
        result.news_history,
        result.combined_history,
    )
    config_path = _accumulation_config(tmp_path)
    json_path, markdown_path = write_news_accumulation_report(
        config_path=config_path,
        db_path=tmp_path / "macro.duckdb",
    )
    assert json_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8").lower()
    assert "insufficient_history" in markdown
    assert "diagnostic history report" in markdown


def test_accumulation_cli_summary(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    config = load_news_accumulation_config("config/news_accumulation.yaml")
    result = build_news_accumulation_outputs(
        config=config,
        news_items=_news_items(),
        classifications=_classifications(),
        daily_theme_scores=_daily_theme_scores(),
        daily_sector_scores=_daily_sector_scores(),
        combined_diagnostics=_combined_diagnostics(),
        sector_scores=_sector_scores(),
        run_date=pd.Timestamp("2026-05-18").date(),
    )
    store.upsert_news_accumulation_outputs(
        result.runs,
        result.news_history,
        result.combined_history,
    )

    cli = runner.invoke(app, ["news-accumulation-summary", "--db-path", str(db_path)])
    assert cli.exit_code == 0, cli.output
    assert '"valid": true' in cli.output


def _daily_config(tmp_path: Path, *, allow_live_ai: bool = False) -> Path:
    path = tmp_path / "daily_pipeline.yaml"
    path.write_text(
        f"""
daily_pipeline:
  macro:
    enabled: true
    config_path: config/phase_b_sources.yaml
    mode: test
  sector:
    enabled: true
    config_path: config/phase_b_sources.yaml
  news:
    enabled: true
    source_profile: synthetic_sample
    news_sources_config: config/news_sources.yaml
    news_ai_config: config/news_ai.yaml
    news_themes_config: config/news_themes.yaml
    news_scoring_config: config/news_scoring.yaml
    allow_live_ai: {str(allow_live_ai).lower()}
    mock_mode_default: {str(not allow_live_ai).lower()}
  live_ai_safety:
    max_items_per_run: 25
    batch_size: 5
    classify_only_unclassified: true
    continue_on_individual_failure: true
    stop_on_failure_rate_above: 0.20
    stop_on_timeout_count_above: 3
  combined:
    enabled: true
    config_path: config/sector_news_integration.yaml
  monitoring:
    enabled: true
    config_path: config/news_monitoring.yaml
    source_profile: synthetic_sample
  outputs:
    archive_enabled: true
    archive_root: {(tmp_path / "archive").as_posix()}
    include_json: true
    include_markdown: true
    include_run_summary: true
  safety:
    fail_on_guardrail_violation: true
    fail_on_missing_api_key_if_live_ai_enabled: true
    fail_on_macro_pipeline_failure: true
    allow_success_with_warnings: true
""",
        encoding="utf-8",
    )
    return path


def _accumulation_config(tmp_path: Path) -> Path:
    path = tmp_path / "news_accumulation.yaml"
    path.write_text(
        f"""
news_accumulation:
  enabled: true
  source_profile: synthetic_sample
  min_items_per_run: 1
  target_items_per_day: 5
  max_items_per_day: 50
  min_source_count: 1
  min_source_groups: 0
  dedupe_across_runs: true
  retain_raw_items: true
  retain_classifications: true
  output_history_report: true
  output_dir: {(tmp_path / "outputs").as_posix()}
""",
        encoding="utf-8",
    )
    return path


def _daily_services(tmp_path: Path, forbidden_report: bool = False) -> dict:
    outputs = tmp_path / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)

    def write_pair(name: str):
        json_path = outputs / f"{name}.json"
        markdown_path = outputs / f"{name}.md"
        json_path.write_text("{}", encoding="utf-8")
        text = "buy signal" if forbidden_report and name == "sector" else "diagnostic report"
        markdown_path.write_text(text, encoding="utf-8")
        return json_path, markdown_path

    return {
        "run_pipeline": lambda **_: SimpleNamespace(status="success"),
        "build_sector_scores": lambda **_: None,
        "write_sector_report": lambda **_: write_pair("sector"),
        "ingest_news": lambda **_: pd.DataFrame(),
        "classify_news": lambda **_: {},
        "write_news_report": lambda **_: write_pair("news"),
        "build_news_scores": lambda **_: SimpleNamespace(),
        "write_news_score_report": lambda **_: write_pair("news_score"),
        "build_combined": lambda **_: SimpleNamespace(),
        "write_combined_report": lambda **_: write_pair("combined"),
        "refresh_monitoring": lambda **_: None,
        "write_monitoring_report": lambda **_: write_pair("monitoring"),
    }


def _news_items() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "news_id": "news_1",
                "source": "source_a",
                "source_url": "https://example.invalid/1",
                "title": "Energy event",
                "body": "Oil supply disruption lifted crude prices.",
                "published_at": "2026-05-01T00:00:00Z",
                "ingested_at": "2026-05-01T01:00:00Z",
                "provider": "local_csv",
                "raw_metadata": {},
                "raw_metadata_json": "{}",
                "content_hash": "hash_1",
            },
            {
                "news_id": "news_2",
                "source": "source_b",
                "source_url": "https://example.invalid/2",
                "title": "Rates event",
                "body": "Higher rates pressured property financing.",
                "published_at": "2026-05-02T00:00:00Z",
                "ingested_at": "2026-05-02T01:00:00Z",
                "provider": "local_csv",
                "raw_metadata": {},
                "raw_metadata_json": "{}",
                "content_hash": "hash_2",
            },
        ]
    )


def _classifications() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "classification_id": "class_1",
                "news_id": "news_1",
                "classified_at": "2026-05-01T02:00:00Z",
                "ai_provider": "mock",
                "ai_model": "mock",
                "macro_themes": [],
                "sector_impacts": [],
                "entities": [],
                "time_horizon": "short_term",
                "severity": 0.8,
                "confidence": 0.9,
                "summary": "Energy diagnostic event.",
                "raw_ai_response": {},
                "classification_status": "success",
                "error_message": None,
            },
            {
                "classification_id": "class_2",
                "news_id": "news_2",
                "classified_at": "2026-05-02T02:00:00Z",
                "ai_provider": "mock",
                "ai_model": "mock",
                "macro_themes": [],
                "sector_impacts": [],
                "entities": [],
                "time_horizon": "short_term",
                "severity": 0.7,
                "confidence": 0.8,
                "summary": "Rates diagnostic event.",
                "raw_ai_response": {},
                "classification_status": "success",
                "error_message": None,
            },
        ]
    )


def _daily_theme_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "score_date": "2026-05-01",
                "theme_id": "commodity_pressure",
                "raw_score": 0.3,
                "adjusted_score": 0.3,
                "item_count": 1,
                "avg_confidence": 0.9,
                "avg_severity": 0.8,
                "top_news_ids_json": '["news_1"]',
                "created_at": "2026-05-01T03:00:00Z",
            }
        ]
    )


def _daily_sector_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "score_date": "2026-05-01",
                "sector_id": "energy",
                "raw_news_score": 0.3,
                "adjusted_news_score": 0.3,
                "positive_item_count": 1,
                "negative_item_count": 0,
                "neutral_item_count": 0,
                "avg_confidence": 0.9,
                "avg_severity": 0.8,
                "top_news_ids_json": '["news_1"]',
                "created_at": "2026-05-01T03:00:00Z",
            }
        ]
    )


def _combined_diagnostics() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "diagnostic_date": "2026-05-01",
                "sector_id": "energy",
                "sector_macro_score": 0.2,
                "sector_news_score": 0.3,
                "combined_score": 0.25,
                "macro_component_weight": 0.75,
                "news_component_weight": 0.25,
                "news_item_count": 1,
                "news_confidence": 0.9,
                "diagnostic_confidence": 0.5,
                "rank": 1,
                "created_at": "2026-05-01T03:00:00Z",
            }
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
            }
        ]
    )
