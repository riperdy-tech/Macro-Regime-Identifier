from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.news.classify import (
    classify_news_item,
    repair_classification_payload,
    retry_user_prompt,
    truncate_for_prompt,
    validate_classification_payload,
)
from macro_engine.news.config import (
    load_news_ai_config,
    load_news_sources_config,
    load_news_themes_config,
)
from macro_engine.news.ingest import content_hash_for_news, load_news_items_from_config
from macro_engine.news.ingest import validate_news_input_config
from macro_engine.news.providers.openai_classifier import (
    DeepSeekNewsClassifier,
    _request_payload,
    _user_prompt,
)
from macro_engine.news.report import build_news_report, news_report_markdown
from macro_engine.news.service import classify_stored_news, ingest_stored_news

runner = CliRunner()


def test_news_configs_load():
    sources = load_news_sources_config("config/news_sources.yaml")
    themes = load_news_themes_config("config/news_themes.yaml")
    ai = load_news_ai_config("config/news_ai.yaml")

    assert sources.news_sources[0].provider == "local_csv"
    assert "inflation_pressure" in themes.active_theme_ids
    assert "energy" in themes.sector_ids
    assert ai.provider == "deepseek"
    assert ai.model == "deepseek-v4-flash"
    assert ai.mock_mode is True


def test_local_csv_ingestion_and_deduplication(tmp_path: Path):
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "title,body,source,source_url,published_at\n"
        "Oil event,Oil prices rose sharply,unit,https://example.invalid,2026-01-01T00:00:00Z\n"
        "Oil event,Oil prices rose sharply,unit,https://example.invalid,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    config_path = _news_source_config(tmp_path, csv_path)

    items = load_news_items_from_config(config_path)

    assert len(items) == 1
    assert items[0].news_id.startswith("news_")
    assert items[0].content_hash == content_hash_for_news(
        title="Oil event",
        body="Oil prices rose sharply",
        source="unit",
        published_at=items[0].published_at.isoformat(),
    )


def test_news_input_validation_and_profile_loading(tmp_path: Path):
    csv_path = tmp_path / "pilot.csv"
    csv_path.write_text(
        "title,body,source,source_url,published_at\n"
        "Oil event,Oil prices rose sharply after a supply disruption affected crude markets and shipping routes,unit,https://example.invalid/1,2026-01-01T00:00:00Z\n"
        "Oil event,Oil prices rose sharply after a supply disruption affected crude markets and shipping routes,unit,https://example.invalid/1,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "news_sources.yaml"
    config_path.write_text(
        f"""
news_sources:
  - source_id: default_sample
    provider: manual_text
    enabled: true
    items:
      - title: Default item
        body: Default body with enough words for a small diagnostic event example.
        source: default
        source_url: https://example.invalid/default
        published_at: "2026-01-02T00:00:00Z"
  - source_id: pilot_local_csv
    provider: local_csv
    enabled: false
    profiles: [pilot_local_csv]
    path: {csv_path.as_posix()}
""",
        encoding="utf-8",
    )

    default_items = load_news_items_from_config(config_path)
    pilot_items = load_news_items_from_config(config_path, profile="pilot_local_csv")
    summary = validate_news_input_config(config_path, profile="pilot_local_csv")

    assert len(default_items) == 1
    assert len(pilot_items) == 1
    assert summary["raw_item_count"] == 2
    assert summary["unique_item_count"] == 1
    assert summary["duplicate_count"] == 1
    assert summary["sources"][0]["duplicate_title_count"] == 1
    assert summary["sources"][0]["quality"] == "warning"

    cli_result = runner.invoke(
        app,
        [
            "validate-news-input",
            "--config",
            str(config_path),
            "--profile",
            "pilot_local_csv",
        ],
    )
    assert cli_result.exit_code == 0, cli_result.output
    assert json.loads(cli_result.output)["duplicate_count"] == 1


def test_ai_schema_validation_rejects_unknown_ids_and_bad_bounds():
    themes = load_news_themes_config("config/news_themes.yaml")
    valid_payload = {
        "summary": "Diagnostic summary.",
        "macro_themes": [
            {
                "theme_id": "inflation_pressure",
                "direction": "positive",
                "severity": 0.5,
                "confidence": 0.8,
                "time_horizon": "short_term",
                "rationale": "Inflation pressure mentioned.",
            }
        ],
        "sector_impacts": [
            {
                "sector_id": "energy",
                "impact_direction": "tailwind",
                "impact_score": 0.4,
                "confidence": 0.7,
                "rationale": "Energy mentioned.",
            }
        ],
        "entities": [],
        "overall_severity": 0.5,
        "overall_confidence": 0.8,
        "time_horizon": "short_term",
    }

    assert validate_classification_payload(valid_payload, themes).overall_severity == 0.5

    bad_theme = valid_payload | {
        "macro_themes": [valid_payload["macro_themes"][0] | {"theme_id": "not_a_theme"}]
    }
    with pytest.raises(ValueError, match="unknown news theme"):
        validate_classification_payload(bad_theme, themes)

    bad_sector = valid_payload | {
        "sector_impacts": [valid_payload["sector_impacts"][0] | {"sector_id": "not_a_sector"}]
    }
    with pytest.raises(ValueError, match="unknown sector"):
        validate_classification_payload(bad_sector, themes)

    bad_score = valid_payload | {
        "sector_impacts": [valid_payload["sector_impacts"][0] | {"impact_score": 2.0}]
    }
    with pytest.raises(ValueError):
        validate_classification_payload(bad_score, themes)


def test_schema_repair_normalizes_aliases_and_clamps_scores():
    themes = load_news_themes_config("config/news_themes.yaml")
    payload = {
        "summary": "Diagnostic summary.",
        "macro_themes": [
            {
                "theme_id": "inflation_pressure",
                "direction": "uncertain",
                "severity": 1.02,
                "confidence": 3,
                "time_horizon": "short-term",
                "rationale": "Inflation pressure mentioned.",
            }
        ],
        "sector_impacts": [
            {
                "sector_id": "energy",
                "impact_direction": "positive_for_sector",
                "impact_score": 1.03,
                "confidence": 2,
                "rationale": "Energy mentioned.",
            }
        ],
        "entities": [
            {
                "name": "Midwest",
                "entity_type": "geography",
                "relevance": 9,
            }
        ],
        "overall_severity": 5,
        "overall_confidence": 3,
        "time_horizon": "unknown",
    }

    repaired, notes = repair_classification_payload(payload)
    parsed = validate_classification_payload(repaired, themes)

    assert parsed.macro_themes[0].direction == "unclear"
    assert parsed.sector_impacts[0].impact_direction == "tailwind"
    assert parsed.entities[0].entity_type == "region"
    assert parsed.overall_severity == 1.0
    assert notes


def test_invalid_structure_still_fails_after_repair():
    themes = load_news_themes_config("config/news_themes.yaml")
    payload = {
        "summary": "Diagnostic summary.",
        "macro_themes": [{"theme_id": "not_a_theme", "direction": "uncertain"}],
        "sector_impacts": [],
        "entities": [],
        "overall_severity": 0.1,
        "overall_confidence": 0.1,
        "time_horizon": "unclear",
    }

    repaired, _ = repair_classification_payload(payload)

    with pytest.raises(ValueError):
        validate_classification_payload(repaired, themes)


def test_retry_prompt_and_retry_flow_records_metadata():
    item = load_news_items_from_config("config/news_sources.yaml")[0]
    themes = load_news_themes_config("config/news_themes.yaml")

    class RetryClassifier:
        provider_name = "retry"
        model_name = "retry-model"

        def __init__(self):
            self.calls = 0

        def classify(self, item, themes):
            self.calls += 1
            return {"not": "valid"}

        def classify_with_feedback(self, item, themes, *, validation_error, previous_response):
            self.calls += 1
            return {
                "summary": "Corrected diagnostic summary.",
                "macro_themes": [
                    {
                        "theme_id": "inflation_pressure",
                        "direction": "positive",
                        "severity": 0.4,
                        "confidence": 0.7,
                        "time_horizon": "short_term",
                        "rationale": "Corrected.",
                    }
                ],
                "sector_impacts": [],
                "entities": [],
                "overall_severity": 0.4,
                "overall_confidence": 0.7,
                "time_horizon": "short_term",
            }

    classifier = RetryClassifier()
    prompt = retry_user_prompt(item, validation_error="bad enum", previous_response={"x": "y"})
    record = classify_news_item(item, classifier=classifier, themes=themes, max_retries=1)

    assert "bad enum" in prompt
    assert record.classification_status == "success"
    assert classifier.calls == 2
    assert record.raw_ai_response["retry_count"] == 1
    assert record.raw_ai_response["validation_errors"]


def test_live_ai_prompt_truncates_long_body():
    item = load_news_items_from_config("config/news_sources.yaml")[0].model_copy(
        update={"body": "A" * 1000}
    )

    prompt = _user_prompt(item, max_body_chars=80)

    assert "A" * 80 in prompt
    assert "A" * 81 not in prompt
    assert "[truncated_to_80_chars]" in prompt


def test_retry_prompt_truncates_body_and_previous_response():
    item = load_news_items_from_config("config/news_sources.yaml")[0].model_copy(
        update={"body": "B" * 1000}
    )
    previous_response = {"summary": "C" * 1000}

    prompt = retry_user_prompt(
        item,
        validation_error="bad",
        previous_response=previous_response,
        max_body_chars=60,
        max_previous_response_chars=70,
    )

    assert "B" * 60 in prompt
    assert "B" * 61 not in prompt
    assert "[truncated_to_60_chars]" in prompt
    assert "[truncated_to_70_chars]" in prompt


def test_request_payload_uses_configured_output_cap():
    themes = load_news_themes_config("config/news_themes.yaml")
    config = load_news_ai_config("config/news_ai_live.yaml")

    payload = _request_payload(config=config, themes=themes, user_content="{}")

    assert payload["max_tokens"] == config.max_tokens
    assert payload["max_tokens"] <= 800


def test_truncate_for_prompt_preserves_short_text():
    assert truncate_for_prompt("short", 10) == "short"


def test_live_ai_usage_metadata_preserved(monkeypatch):
    themes = load_news_themes_config("config/news_themes.yaml")
    item = load_news_items_from_config("config/news_sources.yaml")[0]
    config = load_news_ai_config("config/news_ai_live.yaml")
    monkeypatch.setenv(config.api_key_env, "test-key")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Usage metadata test.",
                                    "macro_themes": [],
                                    "sector_impacts": [],
                                    "entities": [],
                                    "overall_severity": 0.2,
                                    "overall_confidence": 0.7,
                                    "time_horizon": "short_term",
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 150,
                    "completion_tokens": 45,
                    "total_tokens": 195,
                    "prompt_cache_hit_tokens": 100,
                    "prompt_cache_miss_tokens": 50,
                },
            }

    def fake_post(*args, **kwargs):
        assert "Authorization" in kwargs["headers"]
        return FakeResponse()

    monkeypatch.setattr("macro_engine.news.providers.openai_classifier.requests.post", fake_post)

    record = classify_news_item(
        item,
        classifier=DeepSeekNewsClassifier(config),
        themes=themes,
    )

    usage = record.raw_ai_response["response"]["_provider_usage"]
    assert usage["prompt_tokens"] == 150
    assert usage["completion_tokens"] == 45
    assert usage["total_tokens"] == 195
    assert usage["prompt_cache_hit_tokens"] == 100
    assert usage["prompt_cache_miss_tokens"] == 50
    assert usage["finish_reason"] == "stop"
    assert "test-key" not in json.dumps(record.raw_ai_response)


def test_retry_stops_after_max_attempts():
    item = load_news_items_from_config("config/news_sources.yaml")[0]
    themes = load_news_themes_config("config/news_themes.yaml")

    class AlwaysBadClassifier:
        provider_name = "bad"
        model_name = "bad-model"

        def __init__(self):
            self.calls = 0

        def classify(self, item, themes):
            self.calls += 1
            return {"not": "valid"}

        def classify_with_feedback(self, item, themes, *, validation_error, previous_response):
            self.calls += 1
            return {"still": "bad"}

    classifier = AlwaysBadClassifier()
    record = classify_news_item(item, classifier=classifier, themes=themes, max_retries=1)

    assert record.classification_status == "error"
    assert classifier.calls == 2
    assert record.raw_ai_response["retry_count"] == 1


def test_mock_classification_flow_stores_outputs(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "title,body,source,source_url,published_at\n"
        "Oil event,Oil supply disruption lifted prices,unit,https://example.invalid,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    source_config = _news_source_config(tmp_path, csv_path)

    ingested = ingest_stored_news(config_path=source_config, db_path=db_path)
    result = classify_stored_news(db_path=db_path)

    assert len(ingested) == 1
    assert len(result["classifications"]) == 1
    assert len(result["theme_scores"]) == 1
    assert result["sector_impacts"].iloc[0]["sector_id"] == "energy"


def test_classification_progress_logs_ai_cost_caps(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "title,body,source,source_url,published_at\n"
        "Oil event,Oil supply disruption lifted prices,unit,https://example.invalid,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    source_config = _news_source_config(tmp_path, csv_path)
    ingest_stored_news(config_path=source_config, db_path=db_path)
    progress_lines: list[str] = []

    classify_stored_news(db_path=db_path, progress_callback=progress_lines.append)

    config_line = next(line for line in progress_lines if line.startswith("classify-news: ai_config"))
    assert "provider=deepseek" in config_line
    assert "model=deepseek-v4-flash" in config_line
    assert "classifier_mode=mock" in config_line
    assert "selected_items=1" in config_line
    assert "limit=None" in config_line
    assert "max_tokens=1600" in config_line
    assert "max_prompt_body_chars=8000" in config_line
    assert "DEEPSEEK_API_KEY" not in config_line


def test_classification_error_record_for_invalid_ai_response():
    item = load_news_items_from_config("config/news_sources.yaml")[0]
    themes = load_news_themes_config("config/news_themes.yaml")

    class BadClassifier:
        provider_name = "bad"
        model_name = "bad-model"

        def classify(self, item, themes):
            return {
                "summary": "",
                "macro_themes": [],
                "sector_impacts": [],
                "entities": [],
                "overall_severity": 0.0,
                "overall_confidence": 0.0,
                "time_horizon": "unclear",
            }

    record = classify_news_item(item, classifier=BadClassifier(), themes=themes)

    assert record.classification_status == "error"
    assert record.error_message


def test_news_report_generation_and_language_guardrails():
    news_items = pd.DataFrame(
        [
            {
                "news_id": "news_1",
                "title": "Oil event",
                "published_at": "2026-01-01",
            }
        ]
    )
    classifications = pd.DataFrame(
        [
            {
                "classification_id": "classification_1",
                "news_id": "news_1",
                "classified_at": "2026-01-01",
                "classification_status": "success",
                "severity": 0.7,
                "confidence": 0.8,
                "summary": "Diagnostic macro classification.",
            },
            {
                "classification_id": "classification_2",
                "news_id": "news_1",
                "classified_at": "2026-01-02",
                "classification_status": "error",
                "severity": None,
                "confidence": None,
                "summary": "Classification failed.",
            }
        ]
    )
    theme_scores = pd.DataFrame(
        [
            {
                "news_id": "news_1",
                "theme_id": "commodity_pressure",
                "severity": 0.7,
                "confidence": 0.8,
            }
        ]
    )
    sector_impacts = pd.DataFrame(
        [
            {
                "news_id": "news_1",
                "sector_id": "energy",
                "impact_score": 0.5,
                "confidence": 0.8,
            }
        ]
    )

    payload = build_news_report(
        news_items=news_items,
        classifications=classifications,
        theme_scores=theme_scores,
        sector_impacts=sector_impacts,
    )
    markdown = news_report_markdown(payload)

    forbidden = [
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
    assert payload["valid"] is True
    assert "not investment advice" in markdown
    assert not any(term in markdown.lower() for term in forbidden)


def test_news_cli_commands_work(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "title,body,source,source_url,published_at\n"
        "Rate event,Fed officials discussed restrictive policy,unit,https://example.invalid,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    source_config = _news_source_config(tmp_path, csv_path)
    ai_config = _news_ai_config(tmp_path)

    ingest = runner.invoke(
        app,
        ["ingest-news", "--config", str(source_config), "--db-path", str(db_path)],
    )
    assert ingest.exit_code == 0, ingest.output

    classify = runner.invoke(
        app,
        ["classify-news", "--config", str(ai_config), "--db-path", str(db_path)],
    )
    assert classify.exit_code == 0, classify.output

    summary = runner.invoke(app, ["news-classification-summary", "--db-path", str(db_path)])
    assert summary.exit_code == 0, summary.output
    assert json.loads(summary.output)["valid"] is True

    report = runner.invoke(
        app,
        ["write-news-report", "--config", str(ai_config), "--db-path", str(db_path)],
    )
    assert report.exit_code == 0, report.output
    assert (tmp_path / "outputs" / "news_classification_report.md").exists()


def _news_source_config(tmp_path: Path, csv_path: Path) -> Path:
    path = tmp_path / "news_sources.yaml"
    path.write_text(
        f"""
news_sources:
  - source_id: test_csv
    provider: local_csv
    enabled: true
    path: {csv_path.as_posix()}
""",
        encoding="utf-8",
    )
    return path


def _news_ai_config(tmp_path: Path) -> Path:
    path = tmp_path / "news_ai.yaml"
    path.write_text(
        f"""
ai:
  provider: deepseek
  model: deepseek-v4-flash
  enable_live_ai: false
  mock_mode: true
  output_dir: {(tmp_path / "outputs").as_posix()}
""",
        encoding="utf-8",
    )
    return path
