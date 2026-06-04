from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.news.combined import build_combined_sector_diagnostics
from macro_engine.news.config import (
    load_news_scoring_config,
    load_sector_news_integration_config,
)
from macro_engine.news.score_report import write_news_score_report
from macro_engine.news.scoring import build_news_scores, freshness_weight
from macro_engine.storage.duckdb_store import DuckDBStore

runner = CliRunner()


def test_news_scoring_config_and_freshness_decay():
    config = load_news_scoring_config("config/news_scoring.yaml")

    assert config.confidence_weighting is True
    assert config.severity_weighting is True
    assert freshness_weight(0, config) == 1.0
    assert round(freshness_weight(7, config), 4) == 0.5
    assert freshness_weight(22, config) == 0.0


def test_news_scoring_aggregation_components_and_caps():
    config = load_news_scoring_config("config/news_scoring.yaml")
    config.max_single_item_contribution = 0.20
    config.max_single_source_daily_contribution = 0.30
    config.output_end_date = "2026-05-02"

    result = build_news_scores(
        news_items=_news_items(),
        classifications=_classifications(),
        theme_scores=_theme_scores(),
        sector_impacts=_sector_impacts(),
        config=config,
    )

    assert not result.daily_theme_scores.empty
    assert not result.daily_sector_scores.empty
    assert not result.weekly_sector_scores.empty
    assert set(result.components["component_type"]) == {"theme", "sector"}
    assert result.components["adjusted_component"].abs().max() <= 0.20

    latest_energy = result.daily_sector_scores[
        (result.daily_sector_scores["score_date"] == pd.Timestamp("2026-05-02").date())
        & (result.daily_sector_scores["sector_id"] == "energy")
    ].iloc[0]
    assert latest_energy["adjusted_news_score"] <= 0.30
    assert latest_energy["positive_item_count"] >= 1


def test_event_cap_limits_duplicate_narrative(tmp_path: Path):
    config = load_news_scoring_config("config/news_scoring.yaml")
    config.max_single_item_contribution = 1.0
    config.max_single_source_daily_contribution = 1.0
    config.max_single_event_daily_contribution = 0.5
    config.output_end_date = "2026-05-02"
    config.output_start_date = "2026-05-02"

    dup = "Oil supply disruption from a major outage lifted crude and energy prices sharply today"
    items = pd.DataFrame(
        [
            {"news_id": "n1", "source": "src_a", "source_url": "x",
             "title": "Oil supply outage", "body": dup,
             "published_at": "2026-05-02T00:00:00Z", "ingested_at": "2026-05-02T01:00:00Z",
             "provider": "local_csv", "raw_metadata": {}, "content_hash": "h1"},
            {"news_id": "n2", "source": "src_b", "source_url": "y",
             "title": "Oil supply outage", "body": dup,
             "published_at": "2026-05-02T00:00:00Z", "ingested_at": "2026-05-02T01:00:00Z",
             "provider": "local_csv", "raw_metadata": {}, "content_hash": "h2"},
        ]
    )
    classes = pd.DataFrame(
        [
            {"classification_id": f"c{i}", "news_id": nid, "classified_at": "2026-05-02T02:00:00Z",
             "ai_provider": "mock", "ai_model": "mock", "macro_themes": [], "sector_impacts": [],
             "entities": [], "time_horizon": "short_term", "severity": 0.9, "confidence": 1.0,
             "summary": "oil", "raw_ai_response": {}, "classification_status": "success",
             "error_message": None}
            for i, nid in enumerate(["n1", "n2"])
        ]
    )
    impacts = pd.DataFrame(
        [
            {"news_id": "n1", "sector_id": "energy", "impact_direction": "tailwind",
             "impact_score": 0.5, "confidence": 1.0, "rationale": ""},
            {"news_id": "n2", "sector_id": "energy", "impact_direction": "tailwind",
             "impact_score": 0.5, "confidence": 1.0, "rationale": ""},
        ]
    )

    result = build_news_scores(
        news_items=items, classifications=classes, theme_scores=pd.DataFrame(),
        sector_impacts=impacts, config=config,
    )
    energy = result.daily_sector_scores[
        result.daily_sector_scores["sector_id"] == "energy"
    ].iloc[0]
    # Two near-duplicate articles -> one event -> capped at 0.5 (not summed to ~1.0).
    assert energy["adjusted_news_score"] <= 0.5 + 1e-9
    assert energy["adjusted_news_score"] > 0.4  # event present, not zeroed


def test_build_news_scores_cli_and_report(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    _seed_news_outputs(db_path)
    config_path = _news_scoring_config(tmp_path)

    build = runner.invoke(
        app,
        ["build-news-scores", "--config", str(config_path), "--db-path", str(db_path)],
    )
    assert build.exit_code == 0, build.output
    assert json.loads(build.output)["daily_sector_rows"] > 0

    summary = runner.invoke(app, ["current-news-summary", "--db-path", str(db_path)])
    assert summary.exit_code == 0, summary.output
    assert json.loads(summary.output)["valid"] is True

    inspect = runner.invoke(
        app,
        ["inspect-news-score", "--sector", "energy", "--db-path", str(db_path)],
    )
    assert inspect.exit_code == 0, inspect.output

    json_path, markdown_path = write_news_score_report(config_path=config_path, db_path=db_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8").lower()
    assert payload["valid"] is True
    assert "diagnostic news score overlay" in markdown
    assert not any(
        term in markdown
        for term in [
            "buy",
            "sell",
            "overweight",
            "underweight",
            "avoid",
            "recommendation",
            "trade",
            "position sizing",
            "portfolio allocation",
        ]
    )


def test_combined_config_and_score_calculation():
    config = load_sector_news_integration_config("config/sector_news_integration.yaml")
    macro = _sector_scores()
    news = pd.DataFrame(
        [
            {
                "score_date": "2026-05-02",
                "sector_id": "energy",
                "adjusted_news_score": 0.4,
                "positive_item_count": 1,
                "negative_item_count": 0,
                "neutral_item_count": 0,
                "avg_confidence": 0.8,
            },
            {
                "score_date": "2026-05-02",
                "sector_id": "real_estate",
                "adjusted_news_score": -0.4,
                "positive_item_count": 0,
                "negative_item_count": 1,
                "neutral_item_count": 0,
                "avg_confidence": 0.7,
            },
        ]
    )

    result = build_combined_sector_diagnostics(
        sector_scores=macro,
        daily_news_scores=news,
        weekly_news_scores=pd.DataFrame(),
        config=config,
    )

    assert len(result.diagnostics) == 2
    assert len(result.components) == 6
    assert result.diagnostics.sort_values("rank").iloc[0]["sector_id"] == "energy"
    assert result.diagnostics["sector_news_score"].abs().max() <= config.max_news_adjustment


def test_combined_score_handles_empty_news_sector_scores():
    config = load_sector_news_integration_config("config/sector_news_integration.yaml")

    result = build_combined_sector_diagnostics(
        sector_scores=_sector_scores(),
        daily_news_scores=pd.DataFrame(),
        weekly_news_scores=pd.DataFrame(),
        config=config,
    )

    assert len(result.diagnostics) == 2
    assert set(result.diagnostics["sector_id"]) == {"energy", "real_estate"}
    assert result.diagnostics["sector_news_score"].tolist() == [0.0, 0.0]
    assert result.diagnostics["news_component_weight"].tolist() == [0.0, 0.0]


def test_combined_cli_report_and_no_sector_score_mutation(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_sector_outputs(_sector_scores(), pd.DataFrame(), pd.DataFrame())
    _seed_news_outputs(db_path)
    news_config = _news_scoring_config(tmp_path)
    combined_config = _combined_config(tmp_path)

    runner.invoke(
        app,
        ["build-news-scores", "--config", str(news_config), "--db-path", str(db_path)],
    )
    before = store.read_table("sector_scores").copy()
    build = runner.invoke(
        app,
        [
            "build-combined-sector-diagnostics",
            "--config",
            str(combined_config),
            "--db-path",
            str(db_path),
        ],
    )
    assert build.exit_code == 0, build.output
    after = store.read_table("sector_scores")
    pd.testing.assert_frame_equal(before, after)

    ranking = runner.invoke(app, ["current-combined-sector-ranking", "--db-path", str(db_path)])
    assert ranking.exit_code == 0, ranking.output
    assert json.loads(ranking.output)["valid"] is True

    inspect = runner.invoke(
        app,
        ["inspect-combined-sector", "energy", "--db-path", str(db_path)],
    )
    assert inspect.exit_code == 0, inspect.output

    report = runner.invoke(
        app,
        ["write-combined-sector-report", "--config", str(combined_config), "--db-path", str(db_path)],
    )
    assert report.exit_code == 0, report.output
    markdown = (tmp_path / "outputs" / "combined_sector_diagnostic.md").read_text(
        encoding="utf-8"
    ).lower()
    assert "experimental macro plus news diagnostic overlay" in markdown
    assert not any(
        term in markdown
        for term in [
            "buy",
            "sell",
            "overweight",
            "underweight",
            "avoid",
            "recommendation",
            "trade",
            "position sizing",
            "portfolio allocation",
        ]
    )


def _seed_news_outputs(db_path: Path) -> None:
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_news_items(_news_items())
    store.replace_news_classifications(_classifications(), _theme_scores(), _sector_impacts())


def _news_items() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "news_id": "news_1",
                "source": "unit",
                "source_url": "https://example.invalid/1",
                "title": "Oil supply event",
                "body": "Oil supply disruption lifted energy prices.",
                "published_at": "2026-05-01T00:00:00Z",
                "ingested_at": "2026-05-01T01:00:00Z",
                "provider": "local_csv",
                "raw_metadata": {},
                "content_hash": "hash_1",
            },
            {
                "news_id": "news_2",
                "source": "unit",
                "source_url": "https://example.invalid/2",
                "title": "Rates pressure",
                "body": "Restrictive rates weighed on property financing.",
                "published_at": "2026-05-02T00:00:00Z",
                "ingested_at": "2026-05-02T01:00:00Z",
                "provider": "local_csv",
                "raw_metadata": {},
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
                "summary": "Oil supply diagnostic event.",
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
                "summary": "Rate pressure diagnostic event.",
                "raw_ai_response": {},
                "classification_status": "success",
                "error_message": None,
            },
        ]
    )


def _theme_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "news_id": "news_1",
                "theme_id": "commodity_pressure",
                "direction": "positive",
                "severity": 0.8,
                "confidence": 0.9,
                "time_horizon": "short_term",
            },
            {
                "news_id": "news_2",
                "theme_id": "monetary_tightening",
                "direction": "positive",
                "severity": 0.7,
                "confidence": 0.8,
                "time_horizon": "short_term",
            },
        ]
    )


def _sector_impacts() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "news_id": "news_1",
                "sector_id": "energy",
                "impact_direction": "tailwind",
                "impact_score": 0.55,
                "confidence": 0.9,
                "rationale": "Energy is directly tied to oil supply.",
            },
            {
                "news_id": "news_2",
                "sector_id": "real_estate",
                "impact_direction": "headwind",
                "impact_score": -0.50,
                "confidence": 0.8,
                "rationale": "Financing costs pressure property-sensitive sectors.",
            },
        ]
    )


def _sector_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sector_id": "energy",
                "date": "2026-05-01",
                "raw_sector_score": 0.50,
                "confidence_adjusted_score": 0.30,
                "rank": 1,
                "macro_reported_regime": "reflation",
                "macro_raw_dominant_regime": "reflation",
                "macro_confidence": 0.20,
                "valid": True,
                "reason": "ok",
            },
            {
                "sector_id": "real_estate",
                "date": "2026-05-01",
                "raw_sector_score": -0.40,
                "confidence_adjusted_score": -0.20,
                "rank": 2,
                "macro_reported_regime": "reflation",
                "macro_raw_dominant_regime": "reflation",
                "macro_confidence": 0.20,
                "valid": True,
                "reason": "ok",
            },
        ]
    )


def _news_scoring_config(tmp_path: Path) -> Path:
    path = tmp_path / "news_scoring.yaml"
    path.write_text(
        f"""
news_scoring:
  aggregation_frequency: [daily, weekly]
  freshness_decay:
    enabled: true
    half_life_days: 7
    max_age_days: 21
  source_weights:
    default: 1.0
    sources: {{}}
  confidence_weighting: true
  severity_weighting: true
  max_single_item_contribution: 0.35
  max_single_source_daily_contribution: 0.75
  min_confidence: 0.10
  min_severity: 0.05
  neutral_score_threshold: 0.03
  output_start_date: null
  output_end_date: null
  output_dir: {(tmp_path / "outputs").as_posix()}
""",
        encoding="utf-8",
    )
    return path


def _combined_config(tmp_path: Path) -> Path:
    path = tmp_path / "sector_news_integration.yaml"
    path.write_text(
        f"""
sector_news_integration:
  enabled: true
  macro_sector_weight: 0.75
  news_sector_weight: 0.25
  news_score_frequency: daily
  news_score_decay_days: 14
  news_confidence_penalty: 0.05
  max_news_adjustment: 0.50
  min_news_item_count: 1
  require_recent_news: false
  output_label: experimental_combined_sector_diagnostic
  output_dir: {(tmp_path / "outputs").as_posix()}
""",
        encoding="utf-8",
    )
    return path
