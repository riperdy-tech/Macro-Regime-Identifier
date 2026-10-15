"""WS2-T6: Verify secular_themes taxonomy loads and schema validates."""
from __future__ import annotations

from macro_engine.news.config import load_news_themes_config, NewsThemesConfig
from macro_engine.news.classify import (
    build_system_prompt,
    compute_prompt_version,
    validate_classification_payload,
)
from macro_engine.news.schema import NewsClassificationPayload, NewsClassificationRecord


def test_secular_themes_load_from_yaml():
    """Config loads without error and secular_themes block is present."""
    themes = load_news_themes_config("config/news_themes.yaml")

    # Existing macro_themes still intact
    assert "inflation_pressure" in themes.active_theme_ids
    assert len(themes.active_theme_ids) == 18

    # secular_themes block exists with all 9 themes
    assert themes.secular_themes is not None
    assert len(themes.secular_themes) == 9

    expected_ids = {
        "ai_compute",
        "physical_ai",
        "glp1_metabolic",
        "cloud_software",
        "energy_transition",
        "cybersecurity",
        "quantum_computing",
        "space_economy",
        "nuclear_renaissance",
    }
    assert themes.secular_theme_ids == expected_ids

    # Each secular theme has label and description
    for theme_id, info in themes.secular_themes.items():
        assert isinstance(info, dict)
        assert "label" in info
        assert "description" in info
        assert isinstance(info["label"], str)
        assert isinstance(info["description"], str)


def test_secular_themes_optional_in_schema():
    """NewsThemesConfig accepts missing secular_themes (backward compat)."""
    config = NewsThemesConfig.model_validate({
        "macro_themes": [{"theme_id": "inflation_pressure", "label": "Inflation Pressure"}],
        "sector_ids": ["energy"],
    })
    assert config.secular_themes is None
    assert config.secular_theme_ids == set()


def test_system_prompt_includes_secular_theme_ids():
    """build_system_prompt includes secular theme IDs."""
    themes = load_news_themes_config("config/news_themes.yaml")
    prompt = build_system_prompt(themes)

    assert "secular_theme" in prompt
    assert "ai_compute" in prompt
    assert "physical_ai" in prompt
    assert "glp1_metabolic" in prompt
    assert "nuclear_renaissance" in prompt
    # Existing macro themes still present
    assert "inflation_pressure" in prompt


def test_system_prompt_defines_each_secular_theme_and_defaults_the_example_to_null():
    themes = load_news_themes_config("config/news_themes.yaml")
    prompt = build_system_prompt(themes)

    for theme_id, info in themes.secular_themes.items():
        assert f"- {theme_id}: {info['label']} - {info['description']}" in prompt
    assert '"secular_theme": null,' in prompt
    assert '"secular_theme": "ai_compute"' not in prompt
    assert "Assign a secular theme only when the article is about that theme's own industry" in prompt
    assert "rates, inflation, central banks, growth data, geopolitics and general markets news get null" in prompt


def test_system_prompt_secular_theme_without_description_lists_id_and_label_only():
    config = NewsThemesConfig.model_validate({
        "macro_themes": [{"theme_id": "inflation_pressure", "label": "Inflation Pressure"}],
        "secular_themes": {"bare": {"label": "Bare Theme"}, "full": {"label": "Full", "description": "d"}},
        "sector_ids": ["energy"],
    })
    prompt = build_system_prompt(config)

    assert "- bare: Bare Theme\n" in prompt
    assert "- full: Full - d\n" in prompt


def test_prompt_version_changed_with_the_secular_theme_prompt():
    # Digest of the previous template (hard-coded ai_compute example, ids only) with the same themes bytes.
    assert compute_prompt_version(themes_bytes=b"x") != "d7101f2c65bc9e12"


def test_payload_accepts_secular_theme():
    """NewsClassificationPayload accepts secular_theme field."""
    # null secular_theme (backward compat)
    payload = NewsClassificationPayload.model_validate({
        "summary": "Test summary.",
        "macro_themes": [],
        "sector_impacts": [],
        "entities": [],
        "overall_severity": 0.5,
        "overall_confidence": 0.5,
        "time_horizon": "short_term",
    })
    assert payload.secular_theme is None

    # explicit secular_theme
    payload_with = NewsClassificationPayload.model_validate({
        "summary": "Test summary.",
        "macro_themes": [],
        "sector_impacts": [],
        "entities": [],
        "secular_theme": "ai_compute",
        "overall_severity": 0.5,
        "overall_confidence": 0.5,
        "time_horizon": "short_term",
    })
    assert payload_with.secular_theme == "ai_compute"


def test_record_accepts_secular_theme():
    """NewsClassificationRecord accepts secular_theme field."""
    from datetime import datetime, UTC

    # null secular_theme
    record = NewsClassificationRecord.model_validate({
        "classification_id": "test_1",
        "news_id": "news_1",
        "classified_at": datetime.now(UTC).isoformat(),
        "ai_provider": "test",
        "ai_model": "test-model",
        "macro_themes": [],
        "sector_impacts": [],
        "entities": [],
        "time_horizon": "short_term",
        "severity": 0.5,
        "confidence": 0.5,
        "summary": "Test.",
        "raw_ai_response": {},
        "classification_status": "success",
    })
    assert record.secular_theme is None

    # explicit secular_theme
    record_with = NewsClassificationRecord.model_validate({
        "classification_id": "test_2",
        "news_id": "news_2",
        "classified_at": datetime.now(UTC).isoformat(),
        "ai_provider": "test",
        "ai_model": "test-model",
        "macro_themes": [],
        "sector_impacts": [],
        "entities": [],
        "secular_theme": "nuclear_renaissance",
        "time_horizon": "medium_term",
        "severity": 0.3,
        "confidence": 0.6,
        "summary": "Test.",
        "raw_ai_response": {},
        "classification_status": "success",
    })
    assert record_with.secular_theme == "nuclear_renaissance"


def test_validate_classification_payload_rejects_unknown_secular_theme():
    """validate_classification_payload rejects unknown secular theme IDs."""
    import pytest
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
                "rationale": "Test.",
            }
        ],
        "sector_impacts": [],
        "entities": [],
        "secular_theme": "ai_compute",
        "overall_severity": 0.5,
        "overall_confidence": 0.8,
        "time_horizon": "short_term",
    }
    assert validate_classification_payload(valid_payload, themes) is not None

    # null secular_theme should pass
    null_payload = valid_payload | {"secular_theme": None}
    assert validate_classification_payload(null_payload, themes) is not None

    # unknown secular_theme should fail
    bad_payload = valid_payload | {"secular_theme": "not_a_secular_theme"}
    with pytest.raises(ValueError, match="unknown secular_theme"):
        validate_classification_payload(bad_payload, themes)
